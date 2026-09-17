"""Finite read packages own hub snapshots, never actors or mutable sessions."""
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest

from bookflow.core.permission_package import read_package, open_read_hub, before_write
from bookflow.core.publication_admission import Admission
from bookflow.core.errors import BookflowError
from tests.test_permission_snapshots import path


@pytest.fixture
def host(path):
    state = SimpleNamespace(data_root=path.parent, publication_admission=Admission(), pins=0, snapshots=0)
    def started(): state.pins += 1
    def done(): state.pins -= 1
    state.reader_started, state.reader_done = started, done
    def snapshot_started(): state.snapshots += 1
    def snapshot_done(): state.snapshots -= 1
    state.permission_snapshot_started, state.permission_snapshot_done = snapshot_started, snapshot_done
    return state


def test_sequential_borrows_share_hub_fresh_phase_does_not(host, path):
    with read_package(host):
        with open_read_hub(path) as first:
            assert host.pins == 0
        assert not first._closed
        with open_read_hub(path) as second:
            assert first is second
        with read_package(host, fresh=True):
            with open_read_hub(path) as publication:
                assert publication is not first
                assert host.pins == 0
        assert publication._closed and host.pins == 0
    assert first._closed and host.pins == 0 and host.snapshots == 0


def test_nested_readers_keep_independent_savepoints(host, path):
    with read_package(host):
        with open_read_hub(path) as first:
            with open_read_hub(path) as nested:
                assert nested is not first
                first.raw.execute('SAVEPOINT outer_reader')
                nested.raw.execute('SAVEPOINT nested_reader')
                first.raw.execute('RELEASE outer_reader')
                nested.raw.execute('RELEASE nested_reader')
            assert nested._closed
    assert host.pins == 0


def test_copied_context_never_borrows_or_closes_another_threads_handle(host, path):
    with read_package(host):
        with open_read_hub(path) as first:
            pass
        context = copy_context()
        def worker():
            with open_read_hub(path) as other:
                assert other is not first
                other.raw.execute('SELECT 1')
            return other._closed
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(context.run, worker).result(timeout=5)
        assert not first._closed and host.pins == 0
    assert first._closed and host.pins == 0 and host.snapshots == 0


def test_before_write_releases_idle_pin_and_refuses_active_borrow(host, path):
    with read_package(host):
        with open_read_hub(path) as first:
            with pytest.raises(BookflowError) as failure:
                before_write(host)
            assert failure.value.code == 'E_DB_BUSY'
        before_write(host)
        assert first._closed and host.pins == 0 and host.snapshots == 0
        with open_read_hub(path) as later:
            assert later is not first
    assert later._closed and host.pins == 0


def test_error_closes_package(host, path):
    with pytest.raises(ValueError):
        with read_package(host):
            with open_read_hub(path) as db:
                raise ValueError('owned failure')
    assert db._closed and host.pins == 0


def test_generation_change_rebuilds_idle_snapshot_and_refuses_nested_active_use(host, path):
    with read_package(host):
        with open_read_hub(path) as first:
            barrier = host.publication_admission.close_for_commit()
            host.publication_admission.finish_commit(barrier, committed=False)
            with pytest.raises(BookflowError) as failure:
                with open_read_hub(path):
                    pass
            assert failure.value.code == 'E_DB_BUSY'
            assert not first._closed
        with open_read_hub(path) as refreshed:
            assert refreshed is not first and first._closed
    assert refreshed._closed and host.pins == 0


def test_real_host_sessions_do_not_share_identity_or_company_and_writer_discards_snapshot(hosted):
    from bookflow.core.config import Config, os_login
    from bookflow.core.dispatch import _close
    from bookflow.hub import permission_runtime as runtime
    host = hosted.handle.host
    uid = Config.load(hosted.root / 'config.toml').user_table(os_login())['user_id']
    with read_package(host):
        first = host.reader_session(uid, hosted.login)
        try:
            db = first.hub
            observation = runtime._operation_observation(db)
            first.company_row = {'id': 'must-not-leak'}
        finally:
            _close(first)
            host.reader_done()
        second = host.reader_session(hosted.outsider_id, 'outsider')
        try:
            assert second.hub is db
            assert second.actor.id == hosted.outsider_id
            assert second.company_row is None
            assert runtime._operation_observation(second.hub) is observation
        finally:
            _close(second)
            host.reader_done()
        assert host._readers_attached == 0
        # Actual queue boundary releases idle snapshots before a filesystem owner
        # can wait for command readers, even when submit is not through HTTP.
        host.submit(lambda: host.release_company(hosted.company_id))
        assert db._closed
    assert host._readers_attached == 0


from tests.test_row3_host import hosted


def test_shutdown_retains_root_until_idle_package_owner_closes(hosted):
    import threading
    from bookflow.core.config import Config, os_login
    from bookflow.core.dispatch import _close
    host = hosted.handle.host
    uid = Config.load(hosted.root / 'config.toml').user_table(os_login())['user_id']
    ready, release = threading.Event(), threading.Event()
    retained = []
    def worker():
        with read_package(host):
            session = host.reader_session(uid, hosted.login)
            retained.append(session.hub)
            _close(session)
            host.reader_done()
            ready.set()
            assert release.wait(10)
        assert retained[0]._closed
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(worker)
        try:
            assert ready.wait(5)
            assert host._readers_attached == 0 and host._permission_snapshots == 1
            host.shutdown_wait_seconds = 0.05
            with pytest.raises(BookflowError) as failure:
                host.stop()
            assert failure.value.code == 'E_DB_BUSY'
            assert 'readers or transfers' in failure.value.message
            assert host._lock is not None and host._lock._fh is not None
            assert not retained[0]._closed
            with pytest.raises(BookflowError):
                host.permission_snapshot_started()
        finally:
            release.set()
        task.result(timeout=5)
    assert retained[0]._closed and host._permission_snapshots == 0
    host.shutdown_wait_seconds = 10
    host.stop()
    assert host._lock is None
