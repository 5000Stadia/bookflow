"""Tracing real commands observes work without changing its facts or cleanup."""

from concurrent.futures import ThreadPoolExecutor
import errno
import json
import shutil
import threading

import pytest

import bookflow
from bookflow.core import performance as p
from bookflow.core.context import client_version
from bookflow.core.errors import BookflowError
from bookflow.core.host import Host


@pytest.fixture(autouse=True)
def capture_state(monkeypatch):
    p.close()
    monkeypatch.setattr(p, "_roots", set())
    monkeypatch.setattr(p, "_roots_failed", False)
    yield
    p.close()


@pytest.fixture
def destination(tmp_path):
    directory = tmp_path / "trace"
    directory.mkdir(mode=0o700)
    return directory


def test_seeded_read_write_configuration_parity_and_privacy(client, root, tmp_path, destination):
    clone = tmp_path / "untraced"
    shutil.copytree(root, clone)
    plain = bookflow.connect(data_root=str(clone))
    secret = "SENSITIVE_INPUT_712045"

    def work(c):
        before = c.customer.query(company="Demo Plumbing Co")
        item = c.customer.create(name=secret, company="Demo Plumbing Co")
        updated = c.customer.update(customer=item["id"], expected_version=item["version"],
                                    name=secret + "-updated", company="Demo Plumbing Co")
        c.company.use(company="Demo Plumbing Co")
        events = c.audit.list(command="customer update", company="Demo Plumbing Co")["items"]
        return before, updated, events

    baseline = work(plain)
    recorder = p.start(destination)
    assert recorder is not None
    traced = work(client)
    exported = recorder.close()
    assert baseline[0] == traced[0]
    assert baseline[1]["name"] == traced[1]["name"] == secret + "-updated"
    assert baseline[1]["version"] == traced[1]["version"] == 2
    assert len(baseline[2]) == len(traced[2])
    assert (root / "config.toml").read_text() == (clone / "config.toml").read_text()
    assert exported is not None
    encoded = exported.read_text()
    assert secret not in encoded and str(root) not in encoded
    assert traced[1]["id"] not in encoded
    document = json.loads(encoded)
    phases = {e["name"] for e in document["traceEvents"]}
    assert {"command", "command.plan", "command.apply", "command.audit", "command.serialize",
            "sql.execute", "sql.fetch", "sql.commit", "file.publish", "file.sync",
            "file.replace", "directory.sync", "command.close", "lock.acquire"} <= phases
    assert document["metadata"]["unfinished"] == document["metadata"]["dropped"] == 0


def test_append_and_export_failure_cannot_reverse_committed_success(client, destination, monkeypatch):
    before = len(client.audit.list(command="customer create", company="Demo Plumbing Co")["items"])
    recorder = p.start(destination)
    assert recorder is not None

    def broken(*args):
        raise MemoryError("SENSITIVE_INTERNAL_FAILURE")

    monkeypatch.setattr(recorder, "_finish", broken)
    created = client.customer.create(name="Committed despite observer", company="Demo Plumbing Co")
    destination.chmod(0o755)
    assert recorder.close() is None
    saved = client.customer.show(customer=created["id"], company="Demo Plumbing Co")
    assert saved["name"] == created["name"]
    assert len(client.audit.list(command="customer create", company="Demo Plumbing Co")["items"]) == before + 1


def test_sync_and_open_failures_preserve_original_exception(destination, tmp_path, monkeypatch):
    from bookflow.core import durability
    from bookflow.storage.engine import Database

    recorder = p.start(destination)
    assert recorder is not None
    with pytest.raises(BookflowError) as missing:
        Database(tmp_path / "missing.db", writable=False)
    assert missing.value.code == "E_IO"
    original = OSError(errno.EIO, "SENSITIVE_SYNC_FAILURE")

    def broken(*args):
        raise original

    with monkeypatch.context() as patch:
        patch.setattr(durability.os, "fsync", broken)
        with pytest.raises(OSError) as sync:
            durability.write_metadata(tmp_path / "metadata.json", "{}")
        assert sync.value is original
    assert not (tmp_path / "metadata.json").exists()
    assert "SENSITIVE_SYNC_FAILURE" not in recorder.close().read_text()


def test_writer_queue_timeout_late_commit_context_and_failure_cleanup(root, destination):
    recorder = p.start(destination)
    assert recorder is not None
    host = Host(root, version=client_version(), idle_checkpoint_seconds=3600)
    host.start()
    entered, release, committed = threading.Event(), threading.Event(), threading.Event()
    operations = {}

    def first():
        with p.span("command", mode="hosted"):
            operations["first"] = p.current_context().operation
            def occupied():
                entered.set()
                assert release.wait(5)
            host.submit(occupied)

    def second():
        with p.span("command", mode="hosted"):
            operations["second"] = p.current_context().operation
            def late_write():
                host._hub.raw.execute("CREATE TABLE trace_witness(value INTEGER)")
                host._hub.raw.execute("BEGIN IMMEDIATE")
                host._hub.raw.execute("INSERT INTO trace_witness VALUES (1)")
                host._hub.raw.commit()
                committed.set()
            with pytest.raises(TimeoutError):
                host.submit(late_write, timeout=0.01)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            occupied = pool.submit(first)
            assert entered.wait(5)
            timed_out = pool.submit(second)
            try:
                timed_out.result(timeout=5)
                assert not committed.is_set()
            finally:
                release.set()
            occupied.result(timeout=5)
        assert committed.wait(5)
        original = ValueError("SENSITIVE_JOB_FAILURE")
        with p.span("command"):
            operations["failure"] = p.current_context().operation
            def fail():
                host._hub.raw.execute("BEGIN IMMEDIATE")
                host._hub.raw.execute("INSERT INTO trace_witness VALUES (2)")
                raise original
            with pytest.raises(ValueError) as caught:
                host.submit(fail)
            assert caught.value is original
        assert p.current_context() is None
        assert host.submit(lambda: host._hub.raw.execute("SELECT value FROM trace_witness").fetchall()) == [(1,)]
        host.checkpoint_now()
        assert host._writer.is_alive() and not host._filesystem_exclusive
    finally:
        release.set()
        host.stop()
    events = recorder.snapshot()["traceEvents"]
    assert len(set(operations.values())) == 3
    for name in ("first", "second", "failure"):
        selected = [e for e in events if e["args"]["operation_id"] == operations[name]]
        queue = next(e for e in selected if e["name"] == "writer.queue")
        execution = next(e for e in selected if e["name"] == "writer.execute")
        cleanup = next(e for e in selected if e["name"] == "writer.cleanup")
        assert queue["tid"] != execution["tid"] == cleanup["tid"]
        assert queue["ts"] + queue["dur"] <= execution["ts"]
        assert execution["args"]["success"] == (name != "failure")
        if name == "second":
            assert any(e["name"] == "sql.commit" for e in selected)
            caller = next(e for e in selected if e["name"] == "command")
            assert caller["ts"] + caller["dur"] < execution["ts"]
    assert any(e["name"] == "writer.execute" and e["args"]["mode"] == "maintenance" for e in events)
    assert "SENSITIVE_JOB_FAILURE" not in recorder.close().read_text()


def test_cli_bootstrap_capture_and_output_parity(cli, destination):
    baseline = cli.run("--help")
    traced = cli.run("--help", env={"BOOKFLOW_TRACE_DIR": str(destination)})
    assert traced.stdout == baseline.stdout and traced.stderr == baseline.stderr == ""
    files = list(destination.glob("*.json"))
    assert len(files) == 1
    names = {e["name"] for e in json.loads(files[0].read_text())["traceEvents"]}
    assert {"cli.import_app", "cli.invoke", "cli.parser"} <= names
    assert "sql.execute" not in names


def test_capture_destination_inside_resolved_root_is_refused(cli, root):
    destination = root / "capture"
    destination.mkdir(mode=0o700)
    result = cli.run("company", "list", "--json", env={"BOOKFLOW_TRACE_DIR": str(destination)})
    assert json.loads(result.stdout)["items"]
    assert list(destination.iterdir()) == []
    assert result.stderr == "Bookflow performance capture unavailable.\n"


def test_rejected_command_still_excludes_explicit_root(client, root):
    destination = root / "capture"
    destination.mkdir(mode=0o700)
    recorder = p.start(destination)
    assert recorder is not None
    with pytest.raises(BookflowError) as error:
        client.customer.list(dry_run=True, company="Demo Plumbing Co")
    assert error.value.code == "E_USAGE"
    assert recorder.close() is None
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("equals", [False, True])
def test_cli_parser_failure_excludes_explicit_root(cli, destination, equals):
    option = [f"--data-root={destination}"] if equals else ["--data-root", str(destination)]
    result = cli.run(*option, "customer", "list", "--unknown-trace-option",
                     env={"BOOKFLOW_TRACE_DIR": str(destination)}, expect=None)
    assert result.returncode != 0
    assert list(destination.iterdir()) == []


def test_http_read_write_and_rejection_keep_readers_and_writer_usable(client, root, destination):
    from fastapi.testclient import TestClient
    from bookflow.commands.host_cmds import start_serving

    company = client.company.list()["items"][0]["company_id"]
    token = client.token.issue(label="SENSITIVE_TOKEN_LABEL")["secret"]
    recorder = p.start(destination)
    assert recorder is not None
    handle = start_serving(root, client_version())
    try:
        with TestClient(handle.app) as api:
            headers = {"Authorization": "Bearer " + token}
            base = f"/companies/{company}/commands/"
            read = api.post(base + "customer.query", json={}, headers=headers)
            assert read.status_code == 200
            created = api.post(base + "customer.create", json={"name": "SENSITIVE_HTTP_VALUE"}, headers=headers)
            assert created.status_code == 200
            failed = api.post(base + "customer.update", json={"customer": created.json()["id"],
                              "expected_version": 99, "name": "SENSITIVE_REJECTED"}, headers=headers)
            assert failed.status_code == 409
            assert api.post(base + "customer.query", json={}, headers=headers).status_code == 200
        assert handle.host._readers_attached == 0 and handle.host._writer.is_alive()
    finally:
        handle.stop()
    encoded = recorder.close().read_text()
    assert token not in encoded and "SENSITIVE" not in encoded and company not in encoded
    events = json.loads(encoded)["traceEvents"]
    roots = [e for e in events if e["name"] == "command" and e["args"].get("mode") == "hosted"]
    assert len(roots) == 4 and len({e["args"]["operation_id"] for e in roots}) == 4
    assert not any(e["name"].startswith("cli.") for e in events)
