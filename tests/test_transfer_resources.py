"""Ownership, deadlines, and cleanup witnesses for transfer leases."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.transfer_resources import TransferLease


def error(call, code, check=None):
    with pytest.raises(BookflowError) as caught:
        call()
    assert caught.value.code == code
    if check is not None:
        assert caught.value.details == {"check": check}
    return caught.value


@pytest.mark.parametrize("lifetime", [0, -1, 301, float("inf"), float("-inf"),
                                      float("nan"), True, "30", None, 10**1000])
def test_invalid_lifetime(lifetime):
    error(lambda: TransferLease("p", "c", lambda lease: None, lifetime), "E_VALIDATION")


@pytest.mark.parametrize("lifetime", [0.001, 1, 300])
def test_deadline_is_absolute_and_inclusive(lifetime):
    now = [10.0]
    lease = TransferLease("p", "c", lambda lease: None, lifetime, lambda: now[0])
    lease.check_io()
    now[0] += lifetime / 2
    lease.check_io()
    lease.handoff()
    lease.check_start()
    now[0] = 10.0 + lifetime
    error(lease.check_start, "E_IO", "deadline")
    lease.finish()


def test_default_deadline_and_caller_expiry():
    now = [0]
    lease = TransferLease("p", "c", lambda lease: None, clock=lambda: now[0])
    now[0] = 299
    lease.check_io()
    now[0] = 300
    error(lease.check_io, "E_IO", "deadline")
    lease.close()


def test_caller_context_cleanup_and_identity():
    events = []
    with pytest.raises(RuntimeError):
        with TransferLease("principal", "company", lambda lease: events.append("release")) as lease:
            assert (lease.principal_id, lease.company_id) == ("principal", "company")
            assert lease.state == "caller"
            assert not lease.cleanup_pending
            lease.add_cleanup(lambda: events.append("first"))
            lease.add_cleanup(lambda: events.append("second"))
            raise RuntimeError("body")
    assert events == ["second", "first", "release"]
    assert lease.state == "closed"
    assert not lease.cleanup_pending
    lease.close()
    lease.finish()
    assert events == ["second", "first", "release"]


def test_identity_is_read_only_and_does_not_take_state_lock():
    lease = TransferLease("principal", "company", lambda resource: None)
    for name in ("principal_id", "company_id"):
        with pytest.raises(AttributeError):
            setattr(lease, name, "other")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lease._condition:
            future = pool.submit(lambda: (lease.principal_id, lease.company_id))
            assert future.result(timeout=5) == ("principal", "company")
    lease.close()


def test_writer_survives_caller_context_exit_and_cancellation():
    events = []
    with TransferLease("p", "c", lambda lease: events.append("release")) as lease:
        lease.add_cleanup(lambda: events.append("stage"))
        lease.handoff()
        lease.cancel()
    assert lease.state == "writer"
    assert events == []
    assert not lease.cleanup_pending
    lease.check_start()
    lease.finish()
    assert events == ["stage", "release"]


def test_cancellation_before_handoff_rejects_both_io_and_start():
    lease = TransferLease("p", "c", lambda lease: None)
    lease.cancel()
    lease.cancel()
    error(lease.check_io, "E_IO", "cancelled")
    lease.handoff()
    error(lease.check_start, "E_IO", "cancelled")
    lease.finish()


def test_expired_handoff_transfers_cleanup_to_writer():
    now = [0]
    released = []
    lease = TransferLease("p", "c", released.append, 1, lambda: now[0])
    now[0] = 1
    lease.handoff()
    lease.close()
    assert released == []
    error(lease.check_start, "E_IO", "deadline")
    lease.finish()
    assert released == [lease]


def test_foreign_owner_operations_and_closed_use():
    lease = TransferLease("p", "c", lambda lease: None)
    error(lease.check_start, "E_VALIDATION")
    error(lease.finish, "E_VALIDATION")
    error(lambda: lease.add_cleanup(None), "E_VALIDATION")
    lease.check_io()
    lease.handoff()
    error(lease.handoff, "E_VALIDATION")
    error(lease.check_io, "E_VALIDATION")
    error(lease.__enter__, "E_VALIDATION")
    error(lambda: lease.add_cleanup(lambda: None), "E_VALIDATION")
    lease.finish()
    for call in [lease.check_io, lease.check_start, lease.__enter__]:
        error(call, "E_IO", "closed")
    error(lease.handoff, "E_VALIDATION")
    error(lambda: lease.add_cleanup(lambda: None), "E_VALIDATION")
    lease.cancel()
    assert lease.state == "closed"


@pytest.mark.parametrize("owner", ["caller", "writer"])
def test_failed_cleanup_retains_resources_and_retries_lifo(owner):
    events = []
    attempts = [0]
    lease = TransferLease("p", "c", lambda lease: events.append("release"))

    def flaky():
        attempts[0] += 1
        if attempts[0] == 1:
            raise OSError("sensitive /private/path")
        events.append("middle")

    lease.add_cleanup(lambda: events.append("first"))
    lease.add_cleanup(flaky)
    lease.add_cleanup(lambda: events.append("last"))
    if owner == "writer":
        lease.handoff()
    cleanup = lease.close if owner == "caller" else lease.finish
    caught = error(cleanup, "E_IO", "cleanup")
    assert "private" not in str(caught.to_dict())
    assert caught.__suppress_context__
    assert events == ["last"]
    assert lease.state == owner
    assert lease.cleanup_pending
    error(lease.check_io, "E_IO", "closed")
    error(lease.check_start, "E_IO", "closed")
    error(lease.handoff, "E_VALIDATION")
    error(lambda: lease.add_cleanup(lambda: None), "E_VALIDATION")
    if owner == "writer":
        lease.close()
        assert events == ["last"]
    cleanup()
    cleanup()
    assert events == ["last", "middle", "first", "release"]
    assert attempts == [2]
    assert lease.state == "closed"
    assert not lease.cleanup_pending


def test_release_failure_is_retryable_without_repeating_resource_cleanup():
    events = []

    def release(resource):
        assert resource is lease
        events.append("release")
        if events.count("release") == 1:
            raise RuntimeError("private")

    lease = TransferLease("p", "c", release)
    lease.add_cleanup(lambda: events.append("stage"))
    error(lease.close, "E_IO", "cleanup")
    assert lease.cleanup_pending
    assert events == ["stage", "release"]
    lease.close()
    lease.close()
    assert events == ["stage", "release", "release"]
    assert not lease.cleanup_pending


@pytest.mark.parametrize("owner", ["caller", "writer"])
def test_concurrent_cleanup_runs_callbacks_and_release_once(owner):
    entered = threading.Barrier(2)
    proceed = threading.Event()
    ready = threading.Barrier(9)
    events = []
    lease = TransferLease("p", "c", lambda lease: events.append("release"))

    def held_cleanup():
        entered.wait(timeout=5)
        assert proceed.wait(5)
        events.append("last")

    lease.add_cleanup(lambda: events.append("first"))
    lease.add_cleanup(held_cleanup)
    if owner == "writer":
        lease.handoff()
    cleanup = lease.close if owner == "caller" else lease.finish

    def competitor():
        ready.wait(timeout=5)
        cleanup()

    with ThreadPoolExecutor(max_workers=9) as pool:
        first = pool.submit(cleanup)
        entered.wait(timeout=5)
        try:
            assert lease.state == owner
            assert not lease.cleanup_pending
            error(lease.check_io, "E_IO", "closed")
            error(lease.handoff, "E_VALIDATION")
            if owner == "writer":
                lease.close()
                assert events == []
            others = [pool.submit(competitor) for _ in range(8)]
            ready.wait(timeout=5)
        finally:
            proceed.set()
        first.result(timeout=5)
        for future in others:
            future.result(timeout=5)
    assert events == ["last", "first", "release"]
    assert lease.state == "closed"


def test_handoff_and_close_are_atomic_under_barrier():
    events = []
    lease = TransferLease("p", "c", lambda lease: events.append("release"))
    start = threading.Barrier(3)

    def handoff():
        start.wait(timeout=5)
        try:
            lease.handoff()
            return True
        except BookflowError as exc:
            assert exc.code == "E_VALIDATION"
            return False

    def close():
        start.wait(timeout=5)
        lease.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        handed = pool.submit(handoff)
        closed = pool.submit(close)
        start.wait(timeout=5)
        accepted = handed.result(timeout=5)
        closed.result(timeout=5)
    if accepted:
        assert lease.state == "writer"
        assert events == []
        lease.finish()
    assert lease.state == "closed"
    assert events == ["release"]


def test_release_can_take_host_condition_while_host_reads_state():
    host_condition = threading.Condition()
    releasing = threading.Event()

    def release(resource):
        assert resource is lease
        releasing.set()
        with host_condition:
            pass

    lease = TransferLease("p", "c", release)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with host_condition:
            future = pool.submit(lease.close)
            assert releasing.wait(5)
            assert lease.state == "caller"
            assert not lease.cleanup_pending
        future.result(timeout=5)
    assert lease.state == "closed"
