"""Transfer owners remain live across queued writes and filesystem/shutdown gates."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import io
import threading

import pytest

from bookflow.company import attachment_store
from bookflow.core.context import client_version
from bookflow.core.errors import BookflowError
from bookflow.core.host import Host
from bookflow.core.locks import RootLock


@pytest.fixture
def host(root):
    instance = Host(root, version=client_version(), filesystem_wait_seconds=0.02,
                    shutdown_wait_seconds=0.1)
    instance.start()
    try:
        yield instance
    finally:
        instance.retry_transfer_cleanup()
        for lease in tuple(instance._transfers):
            lease.close()
        instance.stop()


def test_capacity_counts_principal_across_companies_and_reuses_closed_slot(host):
    with ExitStack() as stack:
        first = stack.enter_context(host.acquire_transfer("p", "company-a"))
        stack.enter_context(host.acquire_transfer("p", "company-b"))
        with pytest.raises(BookflowError) as error:
            host.acquire_transfer("p", "company-c")
        assert error.value.code == "E_DB_BUSY"
        for number in range(6):
            stack.enter_context(host.acquire_transfer(str(number), "company-a"))
        with pytest.raises(BookflowError):
            host.acquire_transfer("another", "company-a")
        first.close()
        stack.enter_context(host.acquire_transfer("another", "company-a"))
        assert len(host._transfers) == 8 and host._readers_attached == 0
    assert not host._transfers


def test_queued_stage_survives_submit_timeout_and_caller_context_exit(host, tmp_path):
    store = tmp_path / "attachments"
    store.mkdir(mode=0o700)
    occupied, release = threading.Event(), threading.Event()
    seen, published = [], []

    def occupy():
        occupied.set()
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        blocker = pool.submit(host.submit, occupy)
        try:
            assert occupied.wait(5)
            with host.acquire_transfer("p", "company") as lease:
                stage = attachment_store.stage(store, io.BytesIO(b"queued attachment"), 100)
                body = stage.__enter__()
                lease.add_cleanup(lambda: stage.__exit__(None, None, None))
                lease.add_cleanup(lambda: seen.append("cleaned"))
                with pytest.raises(TimeoutError):
                    host.submit(lambda: published.append(attachment_store.publish(store, body)),
                                resource=lease, timeout=0.01)
            assert lease.state == "writer" and len(host._transfers) == 1
            assert len(list(store.glob('.attachment-*.tmp'))) == 1 and seen == []
        finally:
            release.set()
        blocker.result(timeout=5)
    host.submit(lambda: None)  # FIFO barrier after the upload and its cleanup.
    assert seen == ["cleaned"] and not host._transfers
    assert len(published) == 1 and published[0].newly_published
    assert not list(store.glob('.attachment-*.tmp'))
    assert (store / published[0].sha256[:2] / published[0].sha256).read_bytes() == b"queued attachment"


def test_job_error_closes_stage_after_database_cleanup(host, monkeypatch):
    order = []
    original = host._leave_clean

    def database_cleanup():
        order.append("database")
        original()

    monkeypatch.setattr(host, "_leave_clean", database_cleanup)
    lease = host.acquire_transfer("p", "company")
    lease.add_cleanup(lambda: order.append("resource"))

    def fail():
        raise ValueError("job failure")

    with pytest.raises(ValueError, match="job failure"):
        host.submit(fail, resource=lease)
    assert order == ["database", "resource"]
    assert lease.state == "closed" and not host._transfers


def test_rejected_job_consumes_and_cleans_its_resource(host):
    closed = []
    lease = host.acquire_transfer("p", "company")
    lease.add_cleanup(lambda: closed.append(True))
    host.begin_shutdown()
    with pytest.raises(BookflowError) as error:
        host.submit(lambda: pytest.fail("stopped host ran upload"), resource=lease)
    assert error.value.code == "E_DB_BUSY"
    assert closed == [True] and not host._transfers


def test_foreign_or_duplicate_submission_never_steals_resource(host, root):
    other = Host(root, version=client_version())
    lease = host.acquire_transfer("p", "company")
    with pytest.raises(BookflowError) as error:
        other.submit(lambda: None, resource=lease)
    assert error.value.code == "E_VALIDATION" and lease.state == "caller"
    lease.handoff()
    with pytest.raises(BookflowError):
        host.submit(lambda: None, resource=lease)
    assert lease.state == "writer" and lease in host._transfers
    lease.finish()


def test_folder_gate_waits_for_transfers_but_ordinary_writes_do_not(host):
    with host.acquire_transfer("p", "company"):
        assert host.submit(lambda: "ordinary write") == "ordinary write"
        with pytest.raises(BookflowError) as error:
            host.submit(lambda: host.release_company(None))
        assert error.value.code == "E_DB_BUSY"
        assert error.value.details["operation"] == "filesystem_change"
        assert not host._filesystem_exclusive
        assert host._readers_attached == 0
    host.submit(lambda: host.release_company(None))


def test_folder_gate_blocks_new_transfer_admission(host):
    with host.acquire_transfer("p", "company") as lease:
        with ThreadPoolExecutor(max_workers=1) as pool:
            host.filesystem_wait_seconds = 5
            future = pool.submit(host.submit, lambda: host.release_company(None))
            try:
                with host._readers_lock:
                    assert host._readers_lock.wait_for(lambda: host._filesystem_exclusive, timeout=5)
                with pytest.raises(BookflowError):
                    host.acquire_transfer("other", "company")
            finally:
                lease.close()
            future.result(timeout=5)
    assert not host._filesystem_exclusive


def test_cleanup_failure_retains_lease_and_can_retry(host):
    fail = True
    calls = []

    def cleanup():
        calls.append(True)
        if fail:
            raise OSError("temporary cleanup failure")

    lease = host.acquire_transfer("p", "company")
    lease.add_cleanup(cleanup)
    with pytest.raises(BookflowError) as error:
        host.submit(lambda: 1, resource=lease)
    assert error.value.code == "E_IO"
    assert lease.cleanup_pending and lease in host._transfers
    fail = False
    host.retry_transfer_cleanup()
    assert calls == [True, True] and not host._transfers


def test_shutdown_cancels_io_but_keeps_root_lock_until_owner_finishes(host, root):
    lease = host.acquire_transfer("p", "company")
    try:
        with pytest.raises(BookflowError) as error:
            host.stop()
        assert error.value.code == "E_DB_BUSY"
        assert host._lock is not None and host._writer.is_alive()
        with pytest.raises(BookflowError):
            lease.check_io()
        with pytest.raises(BookflowError):
            with RootLock(root, "competing owner"):
                pytest.fail("shutdown released ownership during active I/O")
        with pytest.raises(BookflowError):
            host.acquire_transfer("q", "company")
    finally:
        lease.close()
    host.stop()
    assert host._lock is None and not host._writer.is_alive()


def test_shutdown_timeout_does_not_abandon_an_ordinary_writer(host):
    started, release = threading.Event(), threading.Event()

    def occupied():
        started.set()
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(host.submit, occupied)
        try:
            assert started.wait(5)
            with pytest.raises(BookflowError) as error:
                host.stop()
            assert error.value.code == "E_DB_BUSY"
            assert host._lock is not None and host._writer.is_alive()
        finally:
            release.set()
        work.result(timeout=5)
    host.stop()
    assert host._lock is None and not host._writer.is_alive()


def test_cancelled_transfer_never_runs_and_releases_capacity(host):
    lease = host.acquire_transfer("p", "company")
    lease.cancel()
    with pytest.raises(BookflowError) as error:
        host.submit(lambda: pytest.fail("cancelled transfer started"), resource=lease)
    assert error.value.code == "E_IO" and not host._transfers
