"""Bounded, private diagnostic capture independent of application behavior."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from bookflow.core import performance as p


@pytest.fixture(autouse=True)
def recorder_state(monkeypatch):
    p.close()
    monkeypatch.setattr(p, "_roots", set())
    monkeypatch.setattr(p, "_roots_failed", False)
    yield
    p.close()


@pytest.fixture
def destination(tmp_path):
    path = tmp_path / "capture"
    path.mkdir(mode=0o700)
    return path


def test_disabled_does_not_time_allocate_or_touch_paths(monkeypatch):
    def broken(*args, **kwargs):
        raise AssertionError("disabled timing")
    monkeypatch.setattr(p, "perf_counter_ns", broken)
    monkeypatch.setattr(p, "_Span", broken)
    @p.measured("command")
    def work(value):
        return value
    with p.span("command"):
        assert work(17) == 17
    assert p.begin_wait("writer.queue") is None
    assert p.close() is None


def test_export_labels_nesting_privacy_and_original_exception(destination, monkeypatch):
    monkeypatch.setitem(sys.modules, "bookflow.core.registry", SimpleNamespace(REGISTRY={"customer list": object()}))
    recorder = p.start(destination)
    with p.span("command", command="customer list", mode="hosted"):
        with p.span("command.plan", command="secret-input", mode="secret-mode", database="company"):
            with pytest.raises(ValueError, match="secret-exception"):
                with p.span("sql.execute", database="secret-path"):
                    raise ValueError("secret-exception")
        with p.span("secret-phase"):
            pass
    assert p.current_context() is None
    output = recorder.close()
    assert output is not None
    assert output.stat().st_mode & 0o777 == 0o600
    payload = output.read_text()
    assert "secret" not in payload
    events = json.loads(payload)["traceEvents"]
    assert len(events) == 3
    root = next(e for e in events if e["name"] == "command")
    assert root["args"]["command"] == "customer list"
    for e in events:
        assert e["ph"] == "X" and e["dur"] >= 0
        assert e["args"]["operation_id"] == root["args"]["operation_id"]
        assert root["ts"] <= e["ts"] <= e["ts"] + e["dur"] <= root["ts"] + root["dur"] + 0.001
    assert events[0]["args"]["success"] is False


@pytest.mark.parametrize("limit,kwargs", [("event_limit", {"event_limit": 2}), ("active_limit", {"active_limit": 2}), ("depth_limit", {"depth_limit": 2})])
def test_reservation_limits(destination, limit, kwargs):
    recorder = p.start(destination, **kwargs)
    with p.span("command"):
        with p.span("command.plan"):
            with p.span("sql.execute"):
                assert len(recorder._pending) == 2
    snapshot = recorder.snapshot()
    assert len(snapshot["traceEvents"]) == 2
    assert snapshot["metadata"][limit] == 1
    assert snapshot["metadata"]["dropped"] == 1


def test_deadline_and_late_completion(destination, monkeypatch):
    monkeypatch.setattr(p, "perf_counter_ns", lambda: 100)
    recorder = p.start(destination, seconds=1)
    ticket = p.begin_wait("writer.queue")
    monkeypatch.setattr(p, "perf_counter_ns", lambda: 2_000_000_100)
    assert p.begin_wait("writer.queue") is None
    path = recorder.close()
    p.finish_wait(ticket)
    assert recorder.snapshot()["traceEvents"] == []
    assert json.loads(path.read_text())["metadata"]["unfinished"] == 1
    assert recorder.snapshot()["metadata"]["deadline"] == 1


def test_cross_thread_queue_and_binding_cleanup(destination):
    recorder = p.start(destination)
    caller_tid = __import__("threading").get_native_id()
    with p.span("command"):
        token = p.current_context()
        ticket = p.begin_wait("writer.queue")
    def work():
        assert p.current_context() is None
        p.finish_wait(ticket)
        with p.bind(token):
            with p.span("writer.execute"):
                with p.bind(None):
                    assert p.current_context() is None
                assert p.current_context() is not None
        assert p.current_context() is None
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(work).result()
        pool.submit(work).result()
    events = recorder.snapshot()["traceEvents"]
    queue = next(e for e in events if e["name"] == "writer.queue")
    execution = next(e for e in events if e["name"] == "writer.execute")
    assert queue["tid"] > 1 << 30
    assert queue["args"]["submitting_thread_id"] == caller_tid != execution["tid"]
    assert queue["args"]["operation_id"] == execution["args"]["operation_id"]
    assert queue["args"]["parent_id"] == execution["args"]["parent_id"]
    assert len([e for e in events if e["name"] == "writer.queue"]) == 1


def test_cancel_and_internal_finish_failure_preserve_business(destination, monkeypatch):
    recorder = p.start(destination)
    def broken(*args):
        raise RuntimeError("private diagnostic error")
    monkeypatch.setattr(recorder, "_finish", broken)
    calls = []
    @p.measured("command")
    def work():
        calls.append(1)
        return 42
    assert work() == 42
    with pytest.raises(asyncio.CancelledError):
        with p.span("command"):
            raise asyncio.CancelledError("private cancel")
    assert calls == [1]
    assert p.current_context() is None
    assert recorder.snapshot()["metadata"]["errors"] == 2


def test_root_exclusion_before_and_after_start(destination, tmp_path):
    p.protect_root(tmp_path)
    assert p.start(destination) is None
    p._roots.clear()
    recorder = p.start(destination)
    p.protect_root(tmp_path / "other")
    p.protect_root(tmp_path)
    assert recorder.close() is None
    assert not list(destination.iterdir())


def test_root_bound_fails_closed(destination):
    for i in range(129):
        p.protect_root(Path("/synthetic-root") / str(i))
    assert len(p._roots) == 128
    assert p.start(destination) is None


def test_unsafe_directory_symlink_and_changed_modes(destination, tmp_path):
    destination.chmod(0o755)
    assert p.start(destination) is None
    destination.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(destination, target_is_directory=True)
    assert p.start(link) is None
    recorder = p.start(destination)
    destination.chmod(0o755)
    assert recorder.close() is None
    assert list(destination.iterdir()) == []


def test_export_failure_and_exclusive_creation(destination, monkeypatch, capsys):
    monkeypatch.setattr(p.secrets, "token_hex", lambda _: "fixed")
    existing = destination / "bookflow-trace-fixed.json"
    existing.write_text("untouched")
    recorder = p.start(destination)
    with p.span("command"):
        pass
    assert recorder.close() is None
    assert existing.read_text() == "untouched"
    assert capsys.readouterr().err == "Bookflow performance capture unavailable.\n"


def test_stale_recorder_cannot_close_new_capture(destination):
    first = p.start(destination)
    assert p.start(destination) is None
    assert first.close() is not None
    second = p.start(destination)
    assert first.close() is None
    assert p.enabled()
    assert second.close() is not None


def test_concurrent_reservations_are_bounded(destination):
    recorder = p.start(destination, event_limit=20, active_limit=5)
    with ThreadPoolExecutor(max_workers=12) as pool:
        tickets = list(pool.map(lambda _: p.begin_wait("writer.queue"), range(100)))
    assert sum(t is not None for t in tickets) == 5
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(p.finish_wait, tickets))
    assert len(recorder.snapshot()["traceEvents"]) == 5
    assert recorder.snapshot()["metadata"]["dropped"] == 95


def test_append_failure_counts_unfinished_without_masking_result(destination):
    class BrokenBuffer(list):
        def append(self, value):
            raise MemoryError("private")
    recorder = p.start(destination)
    recorder._events = BrokenBuffer()
    with p.span("command"):
        result = 19
    assert result == 19
    output = recorder.close()
    metadata = json.loads(output.read_text())["metadata"]
    assert metadata["unfinished"] == 1
    assert metadata["errors"] == 1


def test_replaced_destination_does_not_redirect_export(destination):
    recorder = p.start(destination)
    moved = destination.with_name("moved")
    destination.rename(moved)
    destination.mkdir(mode=0o700)
    assert recorder.close() is None
    assert list(moved.iterdir()) == list(destination.iterdir()) == []


def test_overlapping_queue_waits_have_separate_virtual_lanes(destination):
    recorder = p.start(destination)
    with p.span("command"):
        first = p.begin_wait("writer.queue")
    with p.span("command"):
        second = p.begin_wait("writer.queue")
    p.finish_wait(first)
    p.finish_wait(second)
    waits = [e for e in recorder.snapshot()["traceEvents"] if e["name"] == "writer.queue"]
    assert waits[0]["tid"] != waits[1]["tid"]
    assert all(e["tid"] > 1 << 30 and e["args"]["virtual_lane"] == 1 for e in waits)
    assert waits[0]["args"]["submitting_thread_id"] == waits[1]["args"]["submitting_thread_id"]
