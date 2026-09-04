"""Snapshot and commit boundaries exercised through real storage and HTTP paths."""

from concurrent.futures import ThreadPoolExecutor
import shutil
import sqlite3
import subprocess
import sys
import threading

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from bookflow.commands import party_cmds
from bookflow.core import registry
from bookflow.core.errors import BookflowError
from bookflow.storage.engine import Database, open_database
from tests.test_row3_host import _collect, hosted, live  # noqa: F401


def test_hub_read_keeps_its_snapshot_across_a_rename(hosted, monkeypatch):
    command = registry.get("company list")
    original = command.plan
    before = hosted.ok("organization.list")["items"][0]
    entered, release = threading.Event(), threading.Event()

    def gate(inp, ctx, session):
        observed = session.hub.raw.execute("SELECT display_name FROM organizations WHERE id=?", (before["id"],)).fetchone()[0]
        assert observed == before["display_name"]
        entered.set()
        assert release.wait(10)
        return original(inp, ctx, session)

    monkeypatch.setattr(command, "plan", gate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hosted.ok, "company.list")
        try:
            assert entered.wait(10)
            hosted.ok("organization.rename", {"organization": before["id"], "name": "Snapshot renamed organization"})
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result["items"][0]["organization_name"] == before["display_name"]
    assert hosted.handle.host._readers_attached == 0


def test_reader_count_is_released_even_when_close_raises(hosted, monkeypatch):
    from bookflow.core import dispatch
    original = dispatch._close

    def fail_after_close(session):
        original(session)
        raise OSError("injected close failure")

    monkeypatch.setattr(dispatch, "_close", fail_after_close)
    response = hosted.call("company.list")
    assert response.json()["code"] == "E_IO"
    assert hosted.handle.host._readers_attached == 0


def test_company_show_uses_company_truth_after_hub_snapshot_was_opened(hosted, monkeypatch):
    from bookflow.core import dispatch
    original = dispatch.open_company
    entered, release = threading.Event(), threading.Event()

    def gate(session, ctx, writable):
        if not writable:
            entered.set()
            assert release.wait(10)
        return original(session, ctx, writable)

    monkeypatch.setattr(dispatch, "open_company", gate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hosted.info)
        try:
            assert entered.wait(10)
            hosted.ok("company.update", {"legal_name": "Snapshot company truth"}, company=hosted.company_id)
        finally:
            release.set()
        output = future.result(timeout=10)
    assert output["legal_name"] == output["info"]["legal_name"] == "Snapshot company truth"
    assert hosted.handle.host._readers_attached == 0


def test_checkpoint_failure_does_not_suppress_committed_event_notification(hosted, monkeypatch):
    host, cid = hosted.handle.host, hosted.company_id
    hosted.ok("company.update", {"phone": "before checkpoint failure"}, company=cid)
    original = host._after_write
    event = threading.Event()

    class ImmediateLoop:
        @staticmethod
        def call_soon_threadsafe(fn):
            fn()

    class FailingCheckpoint:
        def __init__(self, raw):
            self.raw = raw

        def execute(self, statement, *args):
            if "wal_checkpoint(PASSIVE)" in statement:
                raise sqlite3.OperationalError("injected checkpoint failure")
            return self.raw.execute(statement, *args)

    def checkpoint_fault():
        db = host._companies[cid]
        raw = db.raw
        try:
            db.raw = FailingCheckpoint(raw)
            original()
        finally:
            db.raw = raw

    subscription, old_seq = host.subscribe(cid, ImmediateLoop(), event)
    monkeypatch.setattr(host, "_after_write", checkpoint_fault)
    try:
        hosted.ok("company.update", {"phone": "after checkpoint failure"}, company=cid)
        assert event.wait(1)
        assert host.stream_sequence(cid) > old_seq
    finally:
        host.unsubscribe(subscription)


def test_login_releases_snapshot_before_password_work_and_rechecks_credentials(hosted, monkeypatch):
    from bookflow.adapters.http import auth
    from tests.test_row3_host import PASSWORD
    original = auth.verify_password

    def check_and_reset(stored, supplied):
        assert hosted.handle.host._readers_attached == 0
        verified = original(stored, supplied)
        hosted.ok("user.set-password", {"username": hosted.login, "password": "new-correct-horse-password"})
        return verified

    monkeypatch.setattr(auth, "verify_password", check_and_reset)
    response = hosted.api.post("/login", json={"username": hosted.login, "password": PASSWORD})
    assert response.status_code == 401 and response.json()["code"] == "E_LOGIN_FAILED"
    assert not response.cookies


def test_shutdown_waits_for_snapshot_release_before_unlocking(hosted):
    from bookflow.adapters.http.app import _reader_hub
    host = hosted.handle.host
    entered, release = threading.Event(), threading.Event()

    def hold_reader():
        with _reader_hub(host) as db:
            db.raw.execute("SELECT count(*) FROM audit_events").fetchone()
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(hold_reader)
        assert entered.wait(10)
        host.begin_shutdown()
        stopping = pool.submit(hosted.handle.stop)
        try:
            with pytest.raises(BookflowError, match="E_DB_BUSY"):
                with _reader_hub(host):
                    pass
            assert not stopping.done()
            assert host._lock is not None
        finally:
            release.set()
        reader.result(timeout=10)
        stopping.result(timeout=10)
    assert host._lock is None and host._readers_attached == 0


def test_request_arriving_at_writer_after_shutdown_is_rejected(hosted, monkeypatch):
    host = hosted.handle.host
    original = host.run_write
    entered, release = threading.Event(), threading.Event()

    def gate(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        # Bound even a regressed implementation, which would otherwise wait
        # forever on a job queued after the writer sentinel.
        assert host._stopping
        return original(*args, **kwargs)

    original_submit = host.submit

    def bounded_submit(fn, timeout=None, **kwargs):
        return original_submit(fn, timeout=2 if timeout is None else timeout, **kwargs)

    monkeypatch.setattr(host, "run_write", gate)
    monkeypatch.setattr(host, "submit", bounded_submit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hosted.call, "company.update", {"phone": "too late"}, company=hosted.company_id)
        try:
            assert entered.wait(10)
            hosted.handle.stop()
        finally:
            release.set()
        response = future.result(timeout=5)
    assert response.json()["code"] == "E_DB_BUSY"
    assert not host.enqueue_token_refresh(hosted.token, "bearer")


@pytest.mark.parametrize("verb", ["show", "list"])
def test_overlapping_customer_read_is_one_snapshot(hosted, monkeypatch, verb):
    customer = hosted.ok("customer.create", {
        "name": "Snapshot Before",
        "contacts": [{"role": "primary", "display_name": "Before", "points": []}],
    }, company=hosted.company_id)
    entered, release = threading.Event(), threading.Event()
    original = party_cmds._public_values

    def gate(session, noun, row, **kwargs):
        if noun == "customer" and row["id"] == customer["id"] and not session.company.writable:
            entered.set()
            assert release.wait(10), "writer did not complete"
        return original(session, noun, row, **kwargs)

    monkeypatch.setattr(party_cmds, "_public_values", gate)

    def read():
        with TestClient(hosted.handle.app) as api:
            raw = {"customer": customer["id"]} if verb == "show" else {"query": "Snapshot Before"}
            response = hosted.call(f"customer.{verb}", raw, company=hosted.company_id, client=api)
            assert response.status_code == 200, response.text
            output = response.json()
            return output if verb == "show" else output["items"][0]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read)
        try:
            assert entered.wait(10), "reader did not reach the aggregate projection"
            changed = hosted.ok("customer.update", {
                "customer": customer["id"], "expected_version": customer["version"],
                "name": "Snapshot After",
                "contacts": [{"role": "primary", "display_name": "After", "points": []}],
            }, company=hosted.company_id)
        finally:
            release.set()
        observed = future.result(timeout=10)

    assert (observed["version"], observed["name"], observed["contacts"][0]["display_name"]) == (
        customer["version"], "Snapshot Before", "Before",
    )
    current = hosted.ok("customer.show", {"customer": customer["id"]}, company=hosted.company_id)
    assert (current["version"], current["name"], current["contacts"][0]["display_name"]) == (
        changed["version"], "Snapshot After", "After",
    )
    assert hosted.handle.host._readers_attached == 0


def test_readonly_handle_pins_then_releases_snapshot(tmp_path):
    path = tmp_path / "snapshot.db"
    with open_database(path, True, create=True) as writer:
        assert writer.raw.execute("PRAGMA synchronous").fetchone() == (2,)
        assert writer.raw.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert writer.raw.execute("PRAGMA foreign_keys").fetchone() == (1,)
        writer.raw.execute("CREATE TABLE witness (value INTEGER)")
        writer.raw.execute("INSERT INTO witness VALUES (1)")
        with open_database(path, False) as reader:
            assert reader.raw.in_transaction
            assert not reader.write_transaction
            assert reader.conn.execute(sa.text("SELECT value FROM witness")).scalar_one() == 1
            writer.raw.execute("BEGIN IMMEDIATE")
            assert writer.write_transaction
            writer.raw.execute("UPDATE witness SET value=2")
            writer.raw.execute("COMMIT")
            assert reader.conn.execute(sa.text("SELECT value FROM witness")).scalar_one() == 1
        assert writer.raw.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        with open_database(path, False) as reader:
            assert reader.conn.execute(sa.text("SELECT value FROM witness")).scalar_one() == 2


def test_failed_actor_resolution_closes_reader_handle(hosted, monkeypatch):
    from bookflow.core import dispatch
    handles = []
    original = dispatch._load_actor_by_id

    def fail(session, user_id):
        if not session.hub.writable:
            handles.append(session.hub)
            raise BookflowError("E_UNAUTHENTICATED")
        return original(session, user_id)

    monkeypatch.setattr(dispatch, "_load_actor_by_id", fail)
    response = hosted.call("company.list")
    assert response.status_code == 401
    assert len(handles) == 1 and handles[0].conn.closed
    assert hosted.handle.host._readers_attached == 0


def test_failed_command_closes_both_snapshots(hosted, monkeypatch):
    command = registry.get("customer show")
    handles = []

    def fail(inp, ctx, session):
        handles.extend((session.hub, session.company))
        assert all(db.raw.in_transaction for db in handles)
        raise BookflowError("E_RECORD_NOT_FOUND")

    monkeypatch.setattr(command, "plan", fail)
    response = hosted.call("customer.show", {"customer": "missing"}, company=hosted.company_id)
    assert response.status_code == 404
    assert len(handles) == 2 and all(db.conn.closed for db in handles)
    assert hosted.handle.host._readers_attached == 0


@pytest.mark.parametrize("revoke_between_batches", [False, True])
def test_stream_uses_bounded_reauthorized_snapshot_batches(hosted, live, monkeypatch, revoke_between_batches):
    cid = hosted.company_id
    start = hosted.ok("audit.tail", {"limit": 1}, company=cid)["high_water"] or 0
    for index in range(250):
        hosted.ok("company.update", {"phone": f"snapshot-{index}"}, company=cid)
    token = hosted.ok("token.issue", {"label": "snapshot stream"})
    host = hosted.handle.host
    sessions = []
    plans = []
    reader_session, reader_done = host.reader_session, host.reader_done
    tail = registry.get("audit tail")
    plan = tail.plan

    def record_reader(*args, **kwargs):
        session = reader_session(*args, **kwargs)
        sessions.append(session)
        return session

    def record_plan(inp, ctx, session):
        plans.append(session)
        assert session.hub.raw.in_transaction and session.company.raw.in_transaction
        return plan(inp, ctx, session)

    revoked = False

    def finished():
        nonlocal revoked
        reader_done()
        if revoke_between_batches and not revoked and plans:
            revoked = True
            hosted.ok("token.revoke", {"token": token["token_id"]})

    monkeypatch.setattr(host, "reader_session", record_reader)
    monkeypatch.setattr(host, "reader_done", finished)
    monkeypatch.setattr(tail, "plan", record_plan)
    seen = _collect(live, f"/companies/{cid}/events?after={start}&command=company%20update",
                    {"Authorization": f"Bearer {token['secret']}"},
                    101 if revoke_between_batches else 250, timeout=15)
    assert all(sum(candidate is session for candidate in plans) == 1 for session in sessions)
    assert all(session.hub.conn.closed for session in sessions)
    if revoke_between_batches:
        assert len(plans) == 1
        assert seen[-1][0] == "error" and seen[-1][1]["code"] == "E_UNAUTHENTICATED"
    else:
        assert len(plans) >= 3
        assert all(kind == "audit" for kind, _ in seen)
        sequences = [event["seq"] for _, event in seen]
        assert len(set(sequences)) == 250 and sequences == sorted(sequences)


def test_filtered_stream_advances_across_nonmatching_batches(hosted, live, monkeypatch):
    cid = hosted.company_id
    start = hosted.ok("audit.tail", {"limit": 1}, company=cid)["high_water"] or 0
    for index in range(205):
        hosted.ok("directive.add", {"text": f"Nonmatching stream event {index}"}, company=cid)
    hosted.ok("company.update", {"phone": "after filtered backlog"}, company=cid)
    command = registry.get("audit tail")
    original = command.plan
    scans = []

    def trace(inp, ctx, session):
        result = original(inp, ctx, session)
        scans.append((inp.after, result.preview.next_after, result.preview.scanned_count, result.preview.count))
        return result

    monkeypatch.setattr(command, "plan", trace)
    seen = _collect(live, f"/companies/{cid}/events?after={start}&command=company%20update", hosted.bearer, 1, timeout=15)
    assert len(seen) == 1 and seen[0][0] == "audit"
    assert len(scans) >= 3
    assert all(scanned <= 100 for _, _, scanned, _ in scans)
    assert scans[0][3] == scans[1][3] == 0
    assert scans[0][1] == scans[1][0] and scans[1][1] == scans[2][0]


@pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("strace"), reason="Linux syscall witness needs strace")
def test_commit_sync_precedes_ack_with_a_pinned_reader(tmp_path):
    program = r'''
from pathlib import Path
import sys
from bookflow.storage.engine import Database
w = Database(Path(sys.argv[1]), True, create=True)
w.raw.execute("CREATE TABLE witness (value INTEGER)")
w.raw.execute("INSERT INTO witness VALUES (1)")
w.raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
r = Database(Path(sys.argv[1]), False)
if not r.raw.in_transaction:
    r.raw.execute("BEGIN")
r.raw.execute("SELECT * FROM witness").fetchall()
print("COMMIT-WITNESS-BEGIN", file=sys.stderr, flush=True)
w.raw.execute("BEGIN IMMEDIATE")
w.raw.execute("UPDATE witness SET value=2")
w.raw.execute("COMMIT")
result = w.raw.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
assert result[1] > result[2], result
print("COMMIT-WITNESS-ACK", file=sys.stderr, flush=True)
r.close()
w.close()
'''
    result = subprocess.run([
        "strace", "-s", "0", "-e", "trace=pwrite64,fdatasync,fsync",
        sys.executable, "-c", program, str(tmp_path / "durability.db"),
    ], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    trace = result.stderr.split("COMMIT-WITNESS-BEGIN", 1)[1].split("COMMIT-WITNESS-ACK", 1)[0]
    assert "pwrite64(" in trace, trace
    last_sync = max(trace.rfind("fdatasync("), trace.rfind("fsync("))
    assert last_sync > trace.rfind("pwrite64("), trace
