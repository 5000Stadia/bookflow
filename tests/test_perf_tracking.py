"""R180 increment 1: per-command timings, the store's limits, the report, the off switch and the bench.

The timings store is telemetry beside the hub (design/performance.md): these tests check that a
command's timing lands there, that nothing of its arguments does, that a broken store never
fails a command, that retention holds its caps, that the report's percentiles are right on a
hand-made set, that compare flags a planted regression, and that the switch turns it off.
"""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from bookflow.core import timings
from bookflow.perf import bench, store
from tests.test_row3_host import PASSWORD, WB, hosted  # noqa: F401

COMPANY = "Demo Plumbing Co"
NAME = "Zebulon Quartermaine 7731"
AMOUNT = "98765.43"


def _rows(root, command=None):
    timings.flush()
    p = store.path(root)
    if not p.exists():
        return []
    db = sqlite3.connect(p)
    db.row_factory = sqlite3.Row
    try:
        q = "SELECT * FROM calls" + (" WHERE command=?" if command else "")
        return [dict(r) for r in db.execute(q, (command,) if command else ())]
    finally:
        db.close()


def _store_bytes(root) -> bytes:
    timings.flush()
    return b"".join(f.read_bytes() for f in store.path(root).parent.glob("timings.db*"))


def test_a_command_is_timed_and_nothing_of_its_input_is_kept(client, root):
    made = client.customer.create(name=NAME, notes=f"Owes {AMOUNT} for the Quartermaine job", company=COMPANY)
    client.customer.show(customer=made["id"], company=COMPANY)
    [row] = _rows(root, "customer create")
    assert row["surface"] == "python" and row["principal"] == "human" and row["outcome"] == "ok"
    assert row["wall_ms"] > 0 and row["sql_count"] > 0 and row["sql_ms"] >= 0
    assert row["size_class"] in ("<1MB", "<10MB", "<100MB") and len(row["company"]) == 16
    shown = _rows(root, "customer show")
    assert shown and shown[0]["company"] == row["company"]
    raw = _store_bytes(root)
    company_id = client.company.show(company=COMPANY)["company_id"]
    for secret in (NAME, "Quartermaine", AMOUNT, "98765", made["id"], company_id):
        assert secret.encode() not in raw, secret


def test_an_error_is_recorded_by_its_code(client, root):
    with pytest.raises(Exception) as caught:
        client.customer.show(customer="01J0000000000000000000NONE", company=COMPANY)
    rows = _rows(root, "customer show")
    assert rows[-1]["outcome"] == caught.value.code


def test_a_broken_store_never_fails_the_command(client, root, monkeypatch):
    import shutil
    timings.flush()
    shutil.rmtree(root / "telemetry", ignore_errors=True)  # the demo seeding's own timings
    (root / "telemetry").write_text("not a directory")  # the store cannot even be created
    before = timings.stats["write_failures"]
    out = client.customer.create(name="Still Works Ltd", company=COMPANY)
    assert out["id"]
    assert timings.flush() == 0 and timings.stats["write_failures"] == before + 1
    (root / "telemetry").unlink()

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(store, "write", broken)
    assert client.customer.create(name="Still Works Two", company=COMPANY)["id"]
    timings.flush()


def _row(at, command="invoice post", wall=10.0, outcome="ok", company=None):
    return (at, command, "cli", "human", outcome, wall, 5, 1.0, 2, None, company, 2048)


def test_retention_rolls_up_days_and_holds_the_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "RAW_CAP", 50)
    now = time.time()
    day = 86400
    old = [_row(int(now - 800 * day))]  # beyond the daily window too
    month = [_row(int(now - 40 * day), wall=7.0)]  # beyond raw retention: rolled up, then deleted
    recent = [_row(int(now - (2 + k % 3) * day) + k, wall=float(k)) for k in range(80)]
    store.write(tmp_path, old + month + recent, now=now)
    db = sqlite3.connect(store.path(tmp_path))
    try:
        raw = db.execute("SELECT count(*), min(at) FROM calls").fetchone()
        daily = db.execute("SELECT day, count FROM daily ORDER BY day").fetchall()
    finally:
        db.close()
    assert raw[0] == 50 and raw[1] >= now - 30 * day
    days = {d for d, _ in daily}
    assert store._day(int(now - 40 * day)) in days  # the month-old day survives as its rollup
    assert store._day(int(now - 800 * day)) not in days
    assert sum(c for d, c in daily if d != store._day(int(now - 40 * day))) == 80  # every recent row counted once
    info = store.info(tmp_path)
    assert info["raw_rows"] == 50 and info["raw_cap"] == 50 and info["bytes"] > 0


def test_report_percentiles_on_a_hand_made_set(tmp_path):
    now = int(time.time())
    rows = [_row(now - 10, wall=float(w)) for w in range(1, 101)]
    rows += [_row(now - 10, command="customer list", wall=3.0), _row(now - 10, command="customer list", wall=5.0, outcome="E_VALIDATION")]
    store.write(tmp_path, rows, now=now)
    out = store.report(tmp_path, since=now - 3600, slowest=3)
    by = {r["command"]: r for r in out["items"]}
    assert by["invoice post"]["count"] == 100
    assert (by["invoice post"]["p50_ms"], by["invoice post"]["p95_ms"], by["invoice post"]["max_ms"]) == (50.0, 95.0, 100.0)
    assert by["customer list"]["errors"] == 1 and by["customer list"]["p50_ms"] == 3.0
    assert [r["wall_ms"] for r in out["slowest"]] == [100.0, 99.0, 98.0]
    assert out["items"][0]["command"] == "invoice post"  # slowest p95 first
    assert store.report(tmp_path, since=now - 3600, command="customer list")["calls"] == 2
    assert store.report(tmp_path, since=now - 3600, companies=set())["calls"] == 0


def test_compare_flags_a_planted_regression():
    def run(name, p50s):
        return {"name": name, "scenarios": {k: {"p50_ms": v, "sql_count": 10} for k, v in p50s.items()}}
    base = run("base", {"invoice post": 200.0, "home": 40.0, "deposit": 1000.0, "tiny": 2.0})
    now = run("now", {"invoice post": 210.0, "home": 45.0, "deposit": 1600.0, "tiny": 4.0})
    out = bench.compare(now, base, threshold=0.25, floor_ms=20)
    flagged = {r["scenario"] for r in out["items"] if r["regression"]}
    assert flagged == {"deposit"} and out["regressions"] == 1  # tiny doubled but by 2 ms, under the floor
    better = bench.compare(base, now, threshold=0.25, floor_ms=20)
    assert {r["scenario"] for r in better["items"] if r["improvement"]} == {"deposit"}


def test_the_off_switch(client, root, monkeypatch):
    client.customer.list(company=COMPANY)
    assert _rows(root, "customer list")
    out = client.run("perf settings", {"recording": "off"})
    assert out["recording"] is False and out["raw_rows"] >= 1
    client.customer.list(company=COMPANY)
    client.customer.list(company=COMPANY)
    assert len(_rows(root, "customer list")) == 1
    assert client.run("perf report", {})["recording"] is False
    client.run("perf settings", {"recording": "on"})
    client.customer.list(company=COMPANY)
    assert len(_rows(root, "customer list")) == 2
    monkeypatch.setenv("BOOKFLOW_PERF", "off")
    assert timings.begin() is None
    client.customer.list(company=COMPANY)
    assert len(_rows(root, "customer list")) == 2
    assert client.run("perf settings", {})["environment_off"] is True


def test_report_and_settings_on_http_and_the_workbench(hosted):  # noqa: F811
    hosted.ok("company.show", company=hosted.company_id)
    report = hosted.ok("perf.report", {"since": "24h"})
    assert report["scope"] == "installation"
    rows = {(r["command"], r["surface"]) for r in report["items"]}
    assert ("company show", "http") in rows
    assert hosted.ok("perf.settings")["recording"] is True
    # Anyone else sees only companies they can see: the outsider sees none.
    from fastapi.testclient import TestClient
    outsider = TestClient(hosted.handle.app)
    assert outsider.post("/login", json={"username": "outsider", "password": "another-long-password"}).status_code == 200
    seen = outsider.post("/commands/perf.report", json={}, headers=WB).json()
    assert seen["scope"] == "visible companies" and seen["calls"] == 0
    refused = outsider.post("/commands/perf.settings", json={}, headers=WB)
    assert refused.status_code == 403, refused.text
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    page = browser.get("/hub/performance?since=24h")
    assert page.status_code == 200, page.text
    assert "company show" in page.text and 'id="perf-lines"' in page.text and 'hx-get="/hub/performance"' in page.text
    assert "perf-error" not in browser.get("/hub/performance?surface=bogus").text


def test_generate_and_bench_a_tiny_company(tmp_path):
    from bookflow.perf import generate as gen
    template = tmp_path / gen.template_name("tiny", 7)
    manifest = gen.generate(template, "tiny", 7)
    assert manifest["counts"]["invoices"] == 6 and manifest["counts"]["undeposited_payments"] == 5
    assert manifest["disk_bytes"] > 0 and (template / "root" / "hub.db").exists()
    run = bench.run_bench(template, name="tiny", repeat=1, work_dir=tmp_path,
                          only=["home", "invoice post", "deposit post, all undeposited", "deposit void, all undeposited"])
    assert set(run["scenarios"]) == {"home", "invoice post", "deposit post, all undeposited", "deposit void, all undeposited"}
    big = run["scenarios"]["deposit post, all undeposited"]
    assert big["rows"] >= 5 and big["sql_count"] > 0 and big["p50_ms"] > 0
    saved = bench.save(run, tmp_path / "runs")
    assert bench.load_run("tiny", tmp_path / "runs") == json.loads(saved.read_text())
    assert not list(tmp_path.glob("bookflow-bench-*"))  # the working copy is gone
