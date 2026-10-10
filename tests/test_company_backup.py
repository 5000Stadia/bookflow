"""R133: back up a company to one verified archive, and restore it as a company.

The demo is backed up and restored -- on another data root under its own id, and beside
itself as a copy -- and the restored books are compared with the original: trial balance,
customer list, attachment files and every audit row. Restore refuses an archive from a newer
Bookflow, migrates one from an older Bookflow, and refuses a damaged archive before anything
is registered. A standard member cannot back up; only an installation administrator restores.
The same backup and restore run through Python, the CLI, HTTP and MCP.
"""
import hashlib
import json
import sqlite3
import zipfile
from copy import deepcopy
from pathlib import Path

import anyio
import pytest

import bookflow
from tests.conftest import as_user, make_actor
from tests.test_row3_host import hosted  # noqa: F401

COMMANDS = frozenset({"company backup", "company restore"})
SURFACES = ("python", "cli", "http", "mcp")
TB = {"date_to": "2026-12-31", "limit": 200}


def _company(client):
    return client.company.list()["items"][0]["company_id"]


def _folder(client, company):
    return Path(client.run("company show", {}, company=company)["path"])


def _audit(db):
    with sqlite3.connect(db) as conn:
        events = {row[0]: row for row in conn.execute("SELECT * FROM audit_events")}
        entries = {row[0]: row for row in conn.execute("SELECT * FROM audit_entries")}
    return events, entries


def _bodies(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (folder / "attachments").rglob("*") if p.is_file()}


def _registered(client):
    return {row["company_id"] for row in client.company.list()["items"]}


def _rebuild(archive, target, *, database=None, manifest=None):
    """Write a copy of ``archive`` whose database and manifest have been changed, with honest hashes."""
    with zipfile.ZipFile(archive) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    data = json.loads(members["manifest.json"])
    if database is not None:
        db = target.parent / "rebuild.db"
        db.write_bytes(members["company.db"])
        with sqlite3.connect(db) as conn:
            database(conn)
        conn.close()
        members["company.db"] = db.read_bytes()
        db.unlink()
        data["files"][0] = {"name": "company.db", "sha256": hashlib.sha256(members["company.db"]).hexdigest(),
                            "size_bytes": len(members["company.db"])}
    if manifest is not None:
        manifest(data)
    members["manifest.json"] = json.dumps(data).encode()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return target


def test_the_demo_backed_up_and_restored_keeps_its_books_files_and_history(root, tmp_path):
    client = bookflow.connect(data_root=str(root))
    company = _company(client)
    folder = _folder(client, company)
    before_events, before_entries = _audit(folder / "company.db")
    trial = client.run("report trial-balance", TB, company=company)
    customers = client.run("customer list", {"include_inactive": True}, company=company)
    bodies = _bodies(folder)
    assert bodies, "the demo has attachment files to carry"

    preview = client.run("company backup", {}, company=company, dry_run=True)
    assert preview["dry_run"] and preview["sha256"] is None and not list((folder / "backups").glob("*.bookflow-backup"))
    made = client.run("company backup", {}, company=company, reason="Before year end")
    archive = Path(made["path"])
    assert archive.parent == folder / "backups" and archive.name == made["file_name"]
    assert made["file_name"].startswith("Demo Plumbing Co ") and made["file_name"].endswith(".bookflow-backup")
    assert made["size_bytes"] == archive.stat().st_size and made["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert made["attachment_count"] == len(bodies) and made["missing_attachments"] == []
    assert {f["name"] for f in made["files"]} == {"company.db", *(f"attachments/{sha[:2]}/{sha}" for sha in bodies)}
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["company_id"] == company and manifest["schema_revision"] == made["schema_revision"]
        assert manifest["created_by"]["user_id"] and manifest["backup_id"] == made["backup_id"]
    # The backup is audited in the company's own trail, and the archive holds the trail as it was.
    event = client.run("audit list", {"command": "company backup", "limit": 5}, company=company)["items"]
    assert len(event) == 1 and event[0]["reason"] == "Before year end"

    # Restoring beside the original is refused unless it is a copy; nothing is registered.
    with pytest.raises(bookflow.BookflowError) as refused:
        client.run("company restore", {"archive": str(archive)})
    assert refused.value.code == "E_ALREADY_ATTACHED" and "--as-copy" in refused.value.message
    assert _registered(client) == {company}

    checked = client.run("company restore", {"archive": str(archive), "as_copy": True}, dry_run=True)
    assert checked["dry_run"] and checked["display_name"] == "Demo Plumbing Co (restored)" and _registered(client) == {company}
    copy = client.run("company restore", {"archive": str(archive), "as_copy": True, "name": "Demo Plumbing Copy"})
    assert copy["as_copy"] and copy["company_id"] != company and copy["source_company_id"] == company
    assert copy["migrated"] is False and copy["attachment_count"] == len(bodies)

    # On another machine: a fresh installation restores it under its own id.
    other_root = tmp_path / "other-machine"
    other = bookflow.connect(data_root=str(other_root))
    other.init()
    other.run("organization new", {"name": "Plumbing Books"})
    moved = other.run("company restore", {"archive": str(archive)})
    assert moved["company_id"] == company and moved["as_copy"] is False and moved["display_name"] == "Demo Plumbing Co"
    shown = other.run("company show", {}, company=company)
    assert shown["role"] == "owner"
    hub_event = other.run("hub audit list", {"command": "company restore", "limit": 5})["items"]
    assert len(hub_event) == 1

    for reader, restored in ((client, copy["company_id"]), (other, company)):
        assert reader.run("report trial-balance", TB, company=restored)["rows"] == trial["rows"]
        assert reader.run("customer list", {"include_inactive": True}, company=restored)["items"] == customers["items"]
        restored_folder = _folder(reader, restored)
        assert _bodies(restored_folder) == bodies
        events, entries = _audit(restored_folder / "company.db")
        # Every audit row of the original is there, byte for byte; the restore adds its own event.
        assert {k: events[k] for k in before_events} == before_events
        assert {k: entries[k] for k in before_entries} == before_entries
        added = [events[k] for k in set(events) - set(before_events)]
        assert [row[3] for row in added] == ["company restore"], added  # audit_events.command
        restore_event = reader.run("audit list", {"command": "company restore", "limit": 5}, company=restored)["items"]
        assert len(restore_event) == 1


def test_restore_refuses_a_backup_from_a_newer_bookflow_and_migrates_an_older_one(root, tmp_path):
    client = bookflow.connect(data_root=str(root))
    company = _company(client)
    archive = Path(client.run("company backup", {}, company=company)["path"])
    org_folder = _folder(client, company).parent
    folders = set(org_folder.iterdir())

    def revision(value):
        return lambda conn: conn.execute("UPDATE alembic_version SET version_num = ?", (value,))

    newer = _rebuild(archive, tmp_path / "newer.bookflow-backup", database=revision("co9999"),
                     manifest=lambda m: m.update(schema_revision="co9999"))
    with pytest.raises(bookflow.BookflowError) as refused:
        client.run("company restore", {"archive": str(newer), "as_copy": True})
    assert refused.value.code == "E_SCHEMA_UNKNOWN" and "newer version of Bookflow" in refused.value.message
    assert _registered(client) == {company} and set(org_folder.iterdir()) == folders

    # co0063 only widened a CHECK, co0064 only added an index, co0065 only added triggers and co0066 only added tables; putting the old CHECK back
    # and dropping the index, triggers and tables is the exact co0062 file. co0067 widened two CHECKs and one trigger
    # and added a table; its own REPLACEMENTS and guard edit, run backwards, undo it. co0068 and co0069 only added a table. The demo holds a sales tax
    # adjustment (DEMO-STADJ-1), which no co0062 file can, so the older archive is made from a company without one.
    client.organization.new(name="Older Books Organization")
    company = client.company.new(legal_name="Older Books", home_currency="USD", timezone="UTC",
                                 organization="Older Books Organization", chart="general")["company_id"]
    client.run("journal post", {"date": "2026-06-30", "memo": "Owner investment", "lines": [
        {"account": "Checking", "side": "debit", "amount": "1500.00"},
        {"account": "Opening Balance Equity", "side": "credit", "amount": "1500.00"}]},
        company=company, reason="Something to restore")
    archive = Path(client.run("company backup", {}, company=company)["path"])
    import importlib
    co0063 = importlib.import_module("bookflow.storage.company_migrations.versions.0063_card_credits")
    co0067 = importlib.import_module("bookflow.storage.company_migrations.versions.0067_sales_tax_adjustments")

    def older(conn):
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_schema SET sql = replace(sql, ?, ?) WHERE name = 'money_out_documents'", (co0063.NEW, co0063.OLD))
        for table, pairs in co0067.REPLACEMENTS.items():
            for old_text, new_text in pairs:
                conn.execute("UPDATE sqlite_schema SET sql = replace(sql, ?, ?) WHERE name = ?", (new_text, old_text, table))
        conn.execute("UPDATE sqlite_schema SET sql = replace(sql, ?, ?) WHERE name = 'document_lines_type_insert'",
                     (co0067.GUARD_REPLACEMENT, co0067.GUARD_TARGET))
        conn.execute("PRAGMA writable_schema=OFF")
        conn.execute("DROP INDEX ix_work_billing_allocation_transaction")
        for trigger in ("audit_events_no_update", "audit_events_no_delete", "audit_entries_no_update", "audit_entries_no_delete"):
            conn.execute(f"DROP TRIGGER {trigger}")  # co0065's
        for table in ("statement_csv_mappings", "statement_lines", "statement_imports"):
            conn.execute(f"DROP TABLE {table}")  # co0066's, with their triggers
        conn.execute("DROP TABLE sales_tax_adjustment_profiles")  # co0067's, with its triggers
        conn.execute("DROP TABLE party_merges")  # co0068's, with its triggers
        conn.execute("DROP TABLE payment_bounces")  # co0069's, with its triggers
        conn.execute("UPDATE alembic_version SET version_num = 'co0062'")

    old = _rebuild(archive, tmp_path / "older.bookflow-backup", database=older, manifest=lambda m: m.update(schema_revision="co0062"))
    restored = client.run("company restore", {"archive": str(old), "as_copy": True, "name": "Demo From Older",
                                              "organization": "Older Books Organization"})
    assert restored["migrated"] and restored["backup_schema_revision"] == "co0062" and restored["schema_revision"] == "co0069"
    folder = _folder(client, restored["company_id"])
    # The migration took its verified backup of the restored database first, as every migration does.
    assert list((folder / "backups").glob("*-from-co0062.db"))
    assert client.run("report trial-balance", TB, company=restored["company_id"])["rows"] == \
        client.run("report trial-balance", TB, company=company)["rows"]
    upgrade = client.run("audit list", {"command": "upgrade", "limit": 5}, company=restored["company_id"])["items"]
    assert len(upgrade) == 1


def test_a_damaged_backup_is_refused_before_anything_is_registered(root, tmp_path):
    client = bookflow.connect(data_root=str(root))
    company = _company(client)
    archive = Path(client.run("company backup", {}, company=company)["path"])
    org_folder = _folder(client, company).parent
    folders = set(org_folder.iterdir())

    # A stretch of the database overwritten after the manifest was written: an intact zip, a wrong fingerprint.
    damaged = tmp_path / "damaged.bookflow-backup"
    with zipfile.ZipFile(archive) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    db = bytearray(members["company.db"])
    db[len(db) // 2:len(db) // 2 + 64] = b"\xff" * 64
    members["company.db"] = bytes(db)
    with zipfile.ZipFile(damaged, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    truncated = tmp_path / "truncated.bookflow-backup"
    truncated.write_bytes(archive.read_bytes()[: archive.stat().st_size // 2])
    stranger = _rebuild(archive, tmp_path / "stranger.bookflow-backup",
                        manifest=lambda m: m["files"].append({"name": "../../escape", "sha256": "0" * 64, "size_bytes": 0}))
    for bad, check in ((damaged, "member_hash"), (truncated, "archive"), (stranger, "member_name"),
                       (tmp_path / "absent.bookflow-backup", "archive_missing")):
        for dry_run in (True, False):
            with pytest.raises(bookflow.BookflowError) as refused:
                client.run("company restore", {"archive": str(bad), "as_copy": True}, dry_run=dry_run)
            assert refused.value.code == "E_BACKUP_INVALID", (bad, refused.value.to_dict())
            assert refused.value.details["check"] == check, (bad, refused.value.to_dict())
        assert _registered(client) == {company} and set(org_folder.iterdir()) == folders
    # A relative path is not guessed at on the machine that runs the command.
    with pytest.raises(bookflow.BookflowError) as relative:
        client.run("company restore", {"archive": "backup.bookflow-backup"})
    assert relative.value.code == "E_VALIDATION"


def test_only_administrators_back_up_and_only_installation_administrators_restore(root):
    client = bookflow.connect(data_root=str(root))
    company = _company(client)
    make_actor(root, "sam", company_role=(company, "standard"))
    make_actor(root, "ada", company_role=(company, "admin"))
    with pytest.raises(bookflow.BookflowError) as standard:
        as_user(root, "sam").run("company backup", {}, company=company)
    assert standard.value.code == "E_PERMISSION"
    made = as_user(root, "ada").run("company backup", {}, company=company)
    assert made["path"] is None and made["file_name"].endswith(".bookflow-backup")  # host paths are the installation's
    archive = _folder(client, company) / "backups" / made["file_name"]
    with pytest.raises(bookflow.BookflowError) as admin:
        as_user(root, "ada").run("company restore", {"archive": str(archive), "as_copy": True})
    assert admin.value.code == "E_PERMISSION"


def test_the_browser_backs_up_and_restores_from_the_company_menu(hosted):
    from tests.test_row5_workbench_forms import _browser
    browser = _browser(hosted)
    company = hosted.company_id
    menu = browser.get(f"/c/{company}/_group/company")
    assert menu.status_code == 200
    assert f"/c/{company}/company/backup" in menu.text and f"/c/{company}/company/restore" in menu.text
    page = browser.get(f"/c/{company}/company/backup")
    assert page.status_code == 200 and "Back up now" in page.text and "not downloaded" in page.text
    done = browser.post(f"/c/{company}/company/backup", headers={"X-Bookflow-Workbench": "1"})
    assert done.status_code == 200, done.text[:400]
    assert "Backup saved and verified" in done.text and "data-backup-path" in done.text
    import re
    path = Path(re.search(r"<code data-backup-path>([^<]+)</code>", done.text).group(1).replace("&#39;", "'"))
    assert path.is_file()

    form = browser.get(f"/c/{company}/company/restore")
    assert form.status_code == 200 and 'type="file"' in form.text
    upload = {"backup": (path.name, path.read_bytes(), "application/octet-stream")}
    refused = browser.post(f"/c/{company}/company/restore", files=upload, headers={"X-Bookflow-Workbench": "1"})
    assert refused.status_code == 400 and "E_ALREADY_ATTACHED" in refused.text
    restored = browser.post(f"/c/{company}/company/restore", files=upload, data={"as_copy": "yes", "name": "Browser Copy"},
                            headers={"X-Bookflow-Workbench": "1"})
    assert restored.status_code == 200, restored.text[:600]
    assert "Restored Browser Copy" in restored.text
    new_id = re.search(r'href="/c/([0-9A-Z]{26})/" data-restored-company', restored.text).group(1)
    assert browser.get(f"/c/{new_id}/").status_code == 200
    events = hosted.ok("audit.list", {"command": "company restore", "limit": 5}, company=new_id)["items"]
    assert len(events) == 1 and events[0]["reason"] is None


@pytest.mark.timeout(300)
def test_the_same_backup_and_restore_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip("mcp")
    from tests.mcp_matrix_support import Matrix

    async def witness():
        matrix = Matrix()
        seen = {}
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                call = lambda name, raw, **ctx: matrix.call(surface, name, deepcopy(raw), **ctx)
                preview = await call("company backup", {}, dry_run=True)
                assert preview["dry_run"] and preview["size_bytes"] is None, surface
                made = await call("company backup", {})
                assert made["path"] and Path(made["path"]).is_file(), surface
                refused = await call("company restore", {"archive": made["path"]}, rejected=True)
                assert refused["code"] == "E_ALREADY_ATTACHED", (surface, refused)
                checked = await call("company restore", {"archive": made["path"], "as_copy": True}, dry_run=True)
                assert checked["dry_run"], surface
                restored = await call("company restore", {"archive": made["path"], "as_copy": True, "name": "Parity Copy"})
                assert restored["as_copy"] and restored["display_name"] == "Parity Copy", surface
                seen[surface] = (made["attachment_count"], tuple(sorted(f["name"] for f in made["files"])),
                                 restored["attachment_count"], restored["source_company_id"])
        finally:
            await matrix.close()
        assert len(set(seen.values())) == 1, seen

    anyio.run(witness)
