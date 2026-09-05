"""Actual attachment commands across host admission, queued authority and cleanup."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
from pathlib import Path
import threading

import pytest

from bookflow import BookflowError
from bookflow.adapters.http.auth import resolve_token
from bookflow.core.context import Context, Interface
from bookflow.core.dispatch import execute
from bookflow.core.locks import RootLock
from bookflow.core.registry import get
from bookflow.core.transfers import HostedTransfer
from tests.conftest import _seeded_template, root, client, make_actor  # noqa: F401
from tests.test_row3_host import hosted  # noqa: F401

BODY = b"%PDF-1.4\nindependent hosted regression\x00\xff\n%%EOF\n"


def _begin(hosted, *, user_id=None, authorize_session=None):
    company = hosted.info()
    if user_id is None:
        user_id = hosted.handle.host.submit(lambda: resolve_token(hosted.handle.host._hub, hosted.secret)["user_id"])
    return HostedTransfer(hosted.handle.host, get("attachment add"),
        {"record_type": "company_info", "record_id": company["company_id"], "original_filename": "witness.pdf", "media_type": "application/pdf"},
        Context.new(Interface.http, "integration-witness"), user_id, hosted.login,
        selector=hosted.company_id, authorize_session=authorize_session)


def _files(store):
    return {str(p.relative_to(store)): p.read_bytes() for p in store.rglob("*") if p.is_file()}


@pytest.mark.parametrize("role,code", [(None, "E_COMPANY_NOT_FOUND"), ("readonly", "E_PERMISSION")])
def test_hosted_denial_precedes_input_read(hosted, root, role, code):
    host = hosted.handle.host
    uid = host.submit(lambda: make_actor(root, "denied-transfer", company_role=(hosted.company_id, role) if role else None))
    store = Path(hosted.info()["path"]) / "attachments"
    before = _files(store)
    class Unreadable:
        def read(self, _size):
            pytest.fail("unauthorized hosted command consumed its input")
    transfer = None
    try:
        with pytest.raises(BookflowError) as error:
            transfer = _begin(hosted, user_id=uid)
            transfer.receive(Unreadable())
        assert error.value.code == code
        assert not host._transfers and host._readers_attached == 0
        assert _files(store) == before
    finally:
        if transfer:
            transfer.close()


def test_waiting_input_allows_real_write_but_blocks_compact(hosted):
    host = hosted.handle.host
    host.filesystem_wait_seconds = 0.02
    transfer = _begin(hosted)
    entered, release = threading.Event(), threading.Event()
    class Waiting(io.BytesIO):
        def read(self, size):
            entered.set()
            assert release.wait(5), "test did not release input"
            return super().read(size)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(transfer.receive, Waiting(BODY))
        try:
            assert entered.wait(5)
            assert host._readers_attached == 0 and transfer.resource.lease in host._transfers
            customer = hosted.ok("customer.create", {"name": "Written during upload"}, company=hosted.company_id)
            assert hosted.ok("customer.show", {"customer": customer["id"]}, company=hosted.company_id)["name"] == "Written during upload"
            blocked = hosted.call("company.compact", company=hosted.company_id)
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["code"] == "E_DB_BUSY"
            assert not host._filesystem_exclusive
        finally:
            release.set()
            future.result(timeout=5)
            transfer.close()
    assert not host._transfers
    assert hosted.call("company.compact", company=hosted.company_id).status_code == 200


def test_queued_upload_rechecks_current_token_before_publication(hosted, monkeypatch):
    host = hosted.handle.host
    checks = []
    def authorize(s):
        checks.append(threading.get_ident())
        resolve_token(s.hub, hosted.secret)
    store = Path(hosted.info()["path"]) / "attachments"
    before = _files(store)
    transfer = _begin(hosted, authorize_session=authorize)
    transfer.receive(io.BytesIO(BODY))
    occupied, release, queued = threading.Event(), threading.Event(), threading.Event()
    original_handoff = transfer.resource.lease.handoff
    def handoff():
        original_handoff()
        queued.set()
    monkeypatch.setattr(transfer.resource.lease, "handoff", handoff)
    # A preceding writer revokes the token after the upload has entered the queue.
    def revoke(s):
        occupied.set()
        assert release.wait(5)
        return execute(get("token revoke"), {"token": hosted.token},
                       Context.new(Interface.http, "integration-revoker"), s)
    uid = transfer.user_id
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            blocker = pool.submit(host.run_write, uid, hosted.login, revoke)
            try:
                assert occupied.wait(5)
                upload = pool.submit(transfer.finish_input)
                assert queued.wait(5)
                transfer.close()  # Caller departure must not clean a queued writer's stage.
                assert transfer.resource.lease.state == "writer"
                assert list(store.glob(".attachment-*.tmp"))
            finally:
                release.set()
            assert blocker.result(timeout=5)["changed"]
            with pytest.raises(BookflowError) as error:
                upload.result(timeout=5)
            assert error.value.code == "E_UNAUTHENTICATED"
            assert error.value.details["reason"] == "revoked"
        assert len(checks) >= 3
        assert not host._transfers and host._readers_attached == 0
        assert _files(store) == before
        # The token is revoked, so inspect via the host's authenticated OS actor.
        def rows(s):
            return execute(get("attachment list"), {"record_type": "company_info", "record_id": hosted.company_id},
                           Context.new(Interface.python, "integration-inspector"), s,
                           company_selector=hosted.company_id)
        result = host.run_write(uid, hosted.login, rows)
        assert all(item["attachment"]["sha256"] != hashlib.sha256(BODY).hexdigest() for item in result["items"])
    finally:
        release.set()
        transfer.close()


def test_failed_stage_cleanup_retains_slot_and_root_until_retry(hosted, monkeypatch):
    host = hosted.handle.host
    host.shutdown_wait_seconds = 0.02
    transfer = _begin(hosted)
    transfer.receive(io.BytesIO(BODY))
    store = transfer.resource.store
    temporary, = store.glob(".attachment-*.tmp")
    original_unlink = Path.unlink
    fail = True
    def unlink(path, *args, **kwargs):
        if path == temporary and fail:
            raise OSError("injected stage unlink failure")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    try:
        with pytest.raises(BookflowError) as error:
            transfer.close()
        assert error.value.code == "E_IO"
        assert transfer.resource.lease.cleanup_pending and transfer.resource.lease in host._transfers
        assert temporary.exists()
        # The failed owner still consumes one of this principal's two slots.
        spare = host.acquire_transfer(transfer.user_id, hosted.company_id)
        try:
            with pytest.raises(BookflowError) as error:
                host.acquire_transfer(transfer.user_id, hosted.company_id)
            assert error.value.code == "E_DB_BUSY"
        finally:
            spare.close()
        with pytest.raises(BookflowError) as error:
            host.stop()
        assert error.value.code == "E_IO"
        with pytest.raises(BookflowError):
            with RootLock(hosted.root, "competing test owner"):
                pytest.fail("failed cleanup released the root")
    finally:
        fail = False
        host.retry_transfer_cleanup()
        transfer.close()
    assert not temporary.exists() and not host._transfers
    host.stop()
    with RootLock(hosted.root, "test after cleanup"):
        pass


def test_standalone_input_failure_keeps_root_until_stage_cleanup_retry(client, root, monkeypatch):
    from bookflow.core.transfer_run import retry_cleanup

    company = client.company.show(company="Demo Plumbing Co")
    store = Path(company["path"]) / "attachments"
    before = _files(store)
    original_unlink = Path.unlink
    fail = True
    def unlink(path, *args, **kwargs):
        if path.parent == store and path.name.startswith(".attachment-") and fail:
            raise OSError("injected standalone cleanup failure")
        return original_unlink(path, *args, **kwargs)
    class BrokenInput:
        def read(self, _size):
            raise OSError("input disconnected")
    monkeypatch.setattr(Path, "unlink", unlink)
    try:
        with pytest.raises(BookflowError) as error:
            client.attachment.add(record_type="company_info", record_id=company["company_id"],
                original_filename="interrupted.pdf", input_stream=BrokenInput(), company="Demo Plumbing Co")
        assert error.value.code == "E_IO"
        assert list(store.glob(".attachment-*.tmp"))
        with pytest.raises(BookflowError):
            with RootLock(root, "competitor during failed cleanup"):
                pytest.fail("standalone failure released its root prematurely")
    finally:
        fail = False
        retry_cleanup(root)
    assert _files(store) == before
    with RootLock(root, "competitor after retry"):
        pass
    added = client.attachment.add(record_type="company_info", record_id=company["company_id"],
        original_filename="retry.pdf", input_stream=io.BytesIO(BODY), company="Demo Plumbing Co")
    assert added["attachment"]["sha256"] == hashlib.sha256(BODY).hexdigest()
