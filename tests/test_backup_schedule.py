"""R156: scheduled backups, audit checkpoints that make tampering visible, and a rehearsed restore.

A schedule set by a person fires in the host under an injected clock, writes read-only archives
with read-only checkpoints into its destination, and keeps only the newest N. `backup verify`
passes on untouched books, and reports an altered audit row (with any append-only trigger
dropped first) and a database put back to an earlier state. `backup rehearse` restores the
newest backup into a scratch folder, finds the trial balance and the audit chain as recorded,
and removes the folder.
"""
import json
import sqlite3
import stat
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import bookflow
from tests.test_row3_host import hosted  # noqa: F401
from bookflow.company import backup_checkpoint, backup_schedule
from bookflow.core.clock import parse_iso

def _company(client):
    return client.company.list()["items"][0]["company_id"]


def _folder(client, company):
    return Path(client.run("company show", {}, company=company)["path"])


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_a_schedule_fires_on_the_injected_clock_keeps_n_read_only_and_reports_its_runs(root, tmp_path):
    from bookflow.core.context import client_version
    from bookflow.core.host import Host
    client = bookflow.connect(data_root=str(root))
    company = _company(client)
    dest = tmp_path / "offsite"
    dest.mkdir()
    out = client.run("backup schedule", {"company": company, "daily_at": "02:00", "destination": str(dest), "keep": 2})
    assert out["schedule"]["keep"] == 2 and out["schedule"]["destination"] == str(dest)
    assert "outside Bookflow" in out["note"]
    since = parse_iso(out["schedule"]["since"])
    first = parse_iso(out["schedule"]["next_at"])  # the first 02:00 (company time) after the schedule was set

    host = Host(root, version=client_version(), backup_check_seconds=3600)
    host.start()
    try:
        assert host.backups_now(now=first - timedelta(minutes=1)) == {}
        runs = []
        for day in range(3):
            at = first + timedelta(days=day, minutes=5)
            tried = host.backups_now(now=at)
            assert set(tried) == {company}, tried
            assert tried[company]["last_success"]["file_name"], tried
            runs.append(tried[company]["last_success"])
            assert host.backups_now(now=at + timedelta(minutes=30)) == {}  # done for this day
    finally:
        host.stop()

    archives = sorted(dest.glob("*.bookflow-backup"))
    checkpoints = sorted(dest.glob("*.checkpoint.json"))
    assert len(archives) == 2 and len(checkpoints) == 2
    names = {p.name for p in archives}
    assert runs[-1]["file_name"] in names and runs[0]["file_name"] not in names  # oldest pruned, newest kept
    assert runs[-1]["removed"] == [runs[0]["file_name"]]
    for p in [*archives, *checkpoints]:
        assert _mode(p) == 0o444, p
    newest = json.loads((dest / (runs[-1]["file_name"] + ".checkpoint.json")).read_text())
    assert newest["previous"]["file"] == runs[1]["file_name"] + ".checkpoint.json"
    assert newest["company_audit"]["last_seq"] > 0 and newest["hub_audit"]["last_seq"] > 0

    listed = client.run("backup list", {}, company=company)
    assert listed["schedule"]["daily_at"] == "02:00"
    assert listed["last_success"]["file_name"] == runs[-1]["file_name"]
    assert listed["last_failure"] is None
    scheduled = [i for i in listed["items"] if i["location"] == "scheduled"]
    assert [i["file_name"] for i in scheduled][0] == runs[-1]["file_name"] and len(scheduled) == 2
    assert all(i["read_only"] and i["has_checkpoint"] for i in scheduled)

    # Every scheduled backup left a company audit event by the system user.
    with sqlite3.connect(_folder(client, company) / "company.db") as conn:
        events = conn.execute("SELECT actor_kind FROM audit_events WHERE command = 'backup scheduled'").fetchall()
    conn.close()
    assert events == [("system",)] * 3

    # A destination that has gone is a recorded failure, not a crash; turning it off removes the schedule.
    for p in [*archives, *checkpoints]:
        p.chmod(0o644)
        p.unlink()
    dest.rmdir()
    host = Host(root, version=client_version(), backup_check_seconds=3600)
    host.start()
    try:
        tried = host.backups_now(now=first + timedelta(days=3, minutes=5))
    finally:
        host.stop()
    assert tried[company]["last_failure"]["code"] == "E_IO"
    assert client.run("backup list", {}, company=company)["last_failure"]["code"] == "E_IO"
    assert client.run("backup schedule", {"company": company, "off": True})["schedule"] is None
    assert client.run("backup list", {}, company=company)["schedule"] is None


def test_prune_never_removes_the_newest(tmp_path):
    company = "01TESTCOMPANY0000000000000"
    for n in range(3):
        archive = tmp_path / f"C 2026-01-0{n + 1}-020000.bookflow-backup"
        archive.write_bytes(b"x")
        backup_checkpoint.write(archive, {"format": backup_checkpoint.FORMAT, "format_version": 1, "company_id": company,
                                          "archive": archive.name, "created_at": f"2026-01-0{n + 1}T02:00:00Z"})
    assert backup_schedule.prune(tmp_path, company, 0) == ["C 2026-01-02-020000.bookflow-backup", "C 2026-01-01-020000.bookflow-backup"]
    assert [p.name for p in tmp_path.glob("*.bookflow-backup")] == ["C 2026-01-03-020000.bookflow-backup"]


def test_only_a_person_changes_the_schedule(root, tmp_path):
    from bookflow.commands.backup_schedule_cmds import ScheduleInput, plan_backup_schedule
    from tests.conftest import as_user, make_actor
    agent = SimpleNamespace(actor=SimpleNamespace(kind="agent", id="x"))
    with pytest.raises(bookflow.BookflowError) as refused:
        plan_backup_schedule.plan(ScheduleInput(company="x", off=True), None, agent)
    assert refused.value.code == "E_PERMISSION" and refused.value.details["required_role"] == "human"
    client = bookflow.connect(data_root=str(root))
    company = _company(client)
    make_actor(root, "ada", company_role=(company, "admin"))
    with pytest.raises(bookflow.BookflowError) as admin:
        as_user(root, "ada").run("backup schedule", {"company": company, "daily_at": "02:00", "destination": str(tmp_path), "keep": 3})
    assert admin.value.code == "E_PERMISSION"
    with pytest.raises(bookflow.BookflowError) as relative:
        client.run("backup schedule", {"company": company, "daily_at": "02:00", "destination": "nowhere/at/all", "keep": 3})
    assert relative.value.code == "E_VALIDATION"


def test_verify_passes_then_reports_an_altered_row_and_a_rollback_and_the_rehearsal_passes(root, tmp_path):
    client = bookflow.connect(data_root=str(root))
    company = _company(client)
    db = _folder(client, company) / "company.db"
    earlier = tmp_path / "earlier.db"
    with sqlite3.connect(db) as src, sqlite3.connect(earlier) as dst:
        src.backup(dst)
    first = client.run("company backup", {}, company=company)
    second = client.run("company backup", {}, company=company)
    backups = Path(second["path"]).parent
    assert _mode(Path(second["path"])) == 0o444
    assert _mode(Path(second["path"] + ".checkpoint.json")) == 0o444

    ok = client.run("backup verify", {}, company=company)
    assert ok["ok"] is True, ok
    assert ok["checkpoint"] == second["file_name"] + ".checkpoint.json"
    assert ok["company_audit"]["status"] == "ok" and ok["hub_audit"]["status"] == "ok"
    assert ok["archive_intact"] is True
    assert ok["links"] == [{"checkpoint": second["file_name"] + ".checkpoint.json",
                            "previous": first["file_name"] + ".checkpoint.json", "state": "ok"}]

    rehearsal = client.run("backup rehearse", {}, company=company)
    assert rehearsal["ok"] is True, rehearsal
    assert rehearsal["archive"] == second["file_name"] and rehearsal["scratch_removed"] is True
    assert {c["name"] for c in rehearsal["checks"]} == {"archive", "trial_balance", "company_audit", "hub_audit"}
    assert not list(Path(root).rglob("bookflow-rehearsal-*"))

    # An audit row altered after the checkpoint (the append-only triggers taken away for it).
    from tests.audit_tamper import tampering
    with sqlite3.connect(db) as conn:
        with tampering(conn):
            conn.execute("UPDATE audit_events SET summary = 'nothing to see' WHERE seq = 2")
    conn.close()
    changed = client.run("backup verify", {}, company=company)
    assert changed["ok"] is False
    assert changed["company_audit"]["status"] == "changed"
    assert changed["company_audit"]["from_seq"] <= 2 <= changed["company_audit"]["to_seq"]
    assert any("changed or removed" in p for p in changed["problems"])

    # The database put back to before the newest checkpoint.
    with sqlite3.connect(earlier) as src, sqlite3.connect(db) as dst:
        src.backup(dst)
    rolled = client.run("backup verify", {}, company=company)
    assert rolled["ok"] is False and rolled["company_audit"]["status"] == "rollback", rolled
    assert rolled["company_audit"]["live_seq"] < rolled["company_audit"]["recorded_seq"]

    # An older checkpoint edited breaks the newer one's link to it.
    older = Path(first["path"] + ".checkpoint.json")
    older.chmod(0o644)
    older.write_text(older.read_text().replace('"company_id"', '"company_id" ', 1))
    linked = client.run("backup verify", {}, company=company)
    assert linked["links"][0]["state"] == "broken"
    assert backups == older.parent


def test_the_rehearsal_finds_a_trial_balance_that_differs_from_the_recorded_one(root):
    client = bookflow.connect(data_root=str(root))
    company = _company(client)
    out = client.run("company backup", {}, company=company)
    cp = Path(out["path"] + ".checkpoint.json")
    data = json.loads(cp.read_text())
    data["trial_balance"]["debit_minor_units"] += 1
    cp.chmod(0o644)
    cp.write_text(json.dumps(data))
    rehearsal = client.run("backup rehearse", {}, company=company)
    assert rehearsal["ok"] is False
    assert [c["name"] for c in rehearsal["checks"] if not c["ok"]] == ["trial_balance"]
    assert rehearsal["scratch_removed"] is True


COMMANDS = frozenset({"backup schedule", "backup list", "backup verify", "backup rehearse"})
SURFACES = ("python", "cli", "http", "mcp")


@pytest.mark.timeout(600)
def test_schedule_list_verify_and_rehearse_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from copy import deepcopy
    from tests.mcp_matrix_support import Matrix
    company = _company(bookflow.connect(data_root=str(root)))

    async def witness():
        matrix = Matrix()
        seen = {}
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                call = lambda name, raw, **ctx: matrix.call(surface, name, deepcopy(raw), **ctx)
                dest = tmp_path / f"dest-{surface}"
                dest.mkdir()
                raw = {"company": company, "daily_at": "03:30", "destination": str(dest), "keep": 4}
                preview = await call("backup schedule", raw, dry_run=True)
                assert preview["dry_run"] and preview["schedule"]["keep"] == 4, surface
                made = await call("backup schedule", raw)
                assert made["schedule"]["destination"] == str(dest), surface
                refused = await call("backup schedule", {"company": company, "off": True, "keep": 2}, rejected=True)
                assert refused["code"] == "E_VALIDATION", (surface, refused)
                await call("company backup", {})
                listed = await call("backup list", {})
                assert listed["schedule"]["daily_at"] == "03:30" and listed["count"] >= 1, surface
                verified = await call("backup verify", {})
                assert verified["ok"], (surface, verified)
                rehearsed = await call("backup rehearse", {})
                assert rehearsed["ok"] and rehearsed["scratch_removed"], (surface, rehearsed)
                seen[surface] = (verified["company_audit"]["status"], tuple(c["name"] for c in rehearsed["checks"]))
        finally:
            await matrix.close()
        assert len(set(seen.values())) == 1, seen

    anyio.run(witness)


def test_the_browser_shows_the_backups_and_checks_them_from_the_backup_page(hosted):
    from tests.test_row5_workbench_forms import _browser
    browser = _browser(hosted)
    company = hosted.company_id
    headers = {"X-Bookflow-Workbench": "1"}
    page = browser.get(f"/c/{company}/company/backup")
    assert page.status_code == 200 and "data-backup-listing" in page.text and "No backup schedule is set" in page.text
    assert "outside Bookflow" in page.text
    assert browser.post(f"/c/{company}/company/backup", headers=headers).status_code == 200
    page = browser.get(f"/c/{company}/company/backup")
    assert page.text.count("data-backup-row") == 1
    verified = browser.post(f"/c/{company}/company/backup/verify", headers=headers)
    assert verified.status_code == 200 and 'data-backup-check="verify"' in verified.text and "No tampering found." in verified.text
    rehearsed = browser.post(f"/c/{company}/company/backup/rehearse", headers=headers)
    assert rehearsed.status_code == 200 and "Restore rehearsed" in rehearsed.text, rehearsed.text[-1500:]
