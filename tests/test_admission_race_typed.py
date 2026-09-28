"""R77: a commit racing a request's authority snapshot is retried or typed, never E_INTERNAL.

Every conservative commit (any write, a session issue, a company open) closes the
publication admission and starts a new generation. A request whose permission
snapshot was being opened across that commit used to raise AdmissionCancelled out
of the command, which the HTTP host answered as 500 E_INTERNAL. The injections
below place the commit exactly inside the snapshot open, or hold the barrier
closed while the request starts; no timing is involved.
"""
import threading

import pytest

from bookflow.core import permission_package as pp
from bookflow.core.errors import BookflowError
from bookflow.core.permission_package import read_package, open_read_hub
from bookflow.core.publication_admission import AdmissionCancelled
from tests.test_permission_read_package import host  # noqa: F401  (fixture)
from tests.test_permission_snapshots import path  # noqa: F401  (fixture)
from tests.test_row3_host import hosted  # noqa: F401  (fixture)


def _stable(document):
    """company.show without its relative-age preference history, which moves with the clock."""
    return {key: value for key, value in document.items() if key != 'preference_changes'}


def _commit_during_first_open(monkeypatch, admission, *, phase=None):
    """The first snapshot open (of ``phase``, if named) has a whole commit land inside it."""
    real = pp.open_database
    fired = []

    def armed():
        package = pp._current.get()
        return not fired and (phase is None or (
            package is not None and package.publication == (phase == 'publication')))

    class Racing:
        def __init__(self, *args, **kwargs):
            self.inner = real(*args, **kwargs)

        def __enter__(self):
            db = self.inner.__enter__()
            if armed():
                fired.append(True)
                admission.finish_commit(admission.close_for_commit(), committed=True)
            return db

        def __exit__(self, *exc):
            return self.inner.__exit__(*exc)

    monkeypatch.setattr(pp, 'open_database', Racing)
    return fired


def test_snapshot_overtaken_by_a_commit_is_retaken(host, path, monkeypatch):
    fired = _commit_during_first_open(monkeypatch, host.publication_admission)
    with read_package(host):
        with open_read_hub(path) as db:
            db.raw.execute('SELECT 1')
    assert fired and db._closed and host.snapshots == 0 and host.pins == 0


def test_start_during_an_open_barrier_waits_for_the_commit(host, path, monkeypatch):
    admission = host.publication_admission
    barrier = admission.close_for_commit()
    waiting = threading.Event()
    real_wait = admission.wait_open_blocking

    def observed(timeout):
        waiting.set()
        return real_wait(timeout)

    monkeypatch.setattr(admission, 'wait_open_blocking', observed)
    outcome = []

    def reader():
        try:
            with read_package(host):
                with open_read_hub(path) as db:
                    outcome.append(db.raw.execute('SELECT 1').fetchone()[0])
        except BaseException as exc:  # recorded for the assertion below
            outcome.append(exc)

    worker = threading.Thread(target=reader)
    worker.start()
    try:
        assert waiting.wait(10), outcome  # the reader is parked on the barrier, not failed
        assert not outcome
    finally:
        admission.finish_commit(barrier, committed=True)
        worker.join(10)
    assert outcome == [1] and host.snapshots == 0


def test_a_commit_that_never_settles_is_a_typed_retryable_busy(host, path, monkeypatch):
    monkeypatch.setattr(pp, 'SNAPSHOT_WAIT_SECONDS', 0.2)
    barrier = host.publication_admission.close_for_commit()
    try:
        with pytest.raises(BookflowError) as failure:
            with read_package(host):
                with open_read_hub(path):
                    pass
    finally:
        host.publication_admission.finish_commit(barrier, committed=False)
    assert failure.value.code == 'E_DB_BUSY'
    assert failure.value.details == {'operation': 'authority_change'}
    assert host.snapshots == 0 and host.pins == 0


def test_publication_phase_keeps_its_own_async_wait(host, path):
    """The release loop owns the disconnect-aware wait; it still sees the cancellation."""
    barrier = host.publication_admission.close_for_commit()
    try:
        with pytest.raises(AdmissionCancelled):
            with read_package(host, fresh=True):
                with open_read_hub(path):
                    pass
    finally:
        host.publication_admission.finish_commit(barrier, committed=False)
    assert host.snapshots == 0


def test_http_read_racing_a_commit_answers_normally(hosted, monkeypatch):
    expected = hosted.info()
    fired = _commit_during_first_open(monkeypatch, hosted.handle.host.publication_admission,
                                      phase='execution')
    response = hosted.call('company.show', company=hosted.company_id)
    assert fired
    assert response.status_code == 200, response.text
    assert _stable(response.json()) == _stable(expected)


def test_http_release_check_racing_a_commit_answers_normally(hosted, monkeypatch):
    """The release-time check of a server without the owned transport (TestClient here)."""
    expected = hosted.info()
    fired = _commit_during_first_open(monkeypatch, hosted.handle.host.publication_admission,
                                      phase='publication')
    response = hosted.call('company.show', company=hosted.company_id)
    assert fired
    assert response.status_code == 200, response.text
    assert _stable(response.json()) == _stable(expected)


def test_http_write_overtaken_is_typed_and_never_repeated(hosted, monkeypatch):
    from bookflow.adapters.http import execution
    real = execution.execute
    calls = []

    def overtaken(cmd, *args, **kwargs):
        calls.append(cmd.name)
        if cmd.is_write:
            raise AdmissionCancelled('publication admission changed')
        return real(cmd, *args, **kwargs)

    before = hosted.info()
    monkeypatch.setattr(execution, 'execute', overtaken)
    response = hosted.call('company.update', {'fax': 'must not apply twice'}, company=hosted.company_id)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body['code'] == 'E_DB_BUSY'
    assert body['details'] == {'operation': 'authority_change', 'outcome': 'unknown'}
    assert calls == ['company update']  # the host did not run the write a second time
    monkeypatch.setattr(execution, 'execute', real)
    assert _stable(hosted.info()) == _stable(before)  # raised before commit: rolled back, nothing half-applied


def test_http_read_overtaken_mid_execution_is_read_again(hosted, monkeypatch):
    from bookflow.adapters.http import execution
    real = execution.execute
    calls = []

    def overtaken_once(cmd, *args, **kwargs):
        calls.append(cmd.name)
        if len(calls) == 1:
            raise AdmissionCancelled('publication admission changed')
        return real(cmd, *args, **kwargs)

    expected = hosted.info()
    monkeypatch.setattr(execution, 'execute', overtaken_once)
    response = hosted.call('company.show', company=hosted.company_id)
    assert response.status_code == 200, response.text
    assert _stable(response.json()) == _stable(expected) and calls == ['company show', 'company show']
