"""Forwarded-reader cleanup and retryable public shutdown handles."""

import errno
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bookflow.commands.host_cmds import start_serving
from bookflow.core.context import client_version
from bookflow.core.errors import BookflowError


def test_forwarded_read_close_failure_releases_reader_count(cli, root, monkeypatch):
    from bookflow.core import dispatch

    handle = start_serving(root, client_version())
    real_close = dispatch._close
    closed = []

    def close(session):
        real_close(session)
        closed.append(session)
        raise OSError(errno.EIO, "injected read close failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(dispatch, "_close", close)
            # A distinct CLI process discovers this root's actual Unix listener
            # and performs the forwarded read through its peer credentials.
            error, _status = cli.error("company", "show", "--company", "Demo Plumbing Co")
            assert error["code"] == "E_IO"
        assert len(closed) == 1
        assert handle.host._readers_attached == 0
        assert closed[0]._hub_cm is None
    finally:
        handle.stop()
    assert handle.stopped and handle.host._lock is None
    assert not handle.host._writer.is_alive()


def test_serve_handle_busy_shutdown_preserves_descriptor_and_can_retry(root, monkeypatch):
    handle = start_serving(root, client_version())
    descriptor = root / "host.json"
    original = descriptor.read_bytes()
    real_stop = handle.host.stop
    attempts = []

    def stop():
        attempts.append(True)
        if len(attempts) == 1:
            handle.host.begin_shutdown()
            raise BookflowError("E_DB_BUSY", message="readers are still closing")
        real_stop()

    monkeypatch.setattr(handle.host, "stop", stop)
    try:
        with pytest.raises(BookflowError) as error:
            handle.stop()
        assert error.value.code == "E_DB_BUSY"
        assert not handle.stopped
        assert handle.host._lock is not None and handle.host._writer.is_alive()
        assert descriptor.read_bytes() == original
        handle.stop()
        assert len(attempts) == 2 and handle.stopped
        assert handle.host._lock is None and not handle.host._writer.is_alive()
        assert not descriptor.exists()
        handle.stop()
        assert len(attempts) == 2
    finally:
        if not handle.stopped:
            monkeypatch.setattr(handle.host, "stop", real_stop)
            handle.stop()


def _writer_command(host, user_id, login, name, inputs, company=None):
    from bookflow.core import registry
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import execute

    ctx = Context.new(Interface.python, "filesystem-gate-test")
    return host.run_write(user_id, login, lambda session: execute(
        registry.get(name), inputs, ctx, session, company_selector=company,
    ))


def _reader_show(host, user_id, login, company):
    from bookflow.core import dispatch, registry
    from bookflow.core.context import Context, Interface

    session = host.reader_session(user_id, login)
    try:
        return dispatch.execute(registry.get("company show"), {}, Context.new(Interface.python, "filesystem-gate-test"),
                                session, company_selector=company)
    finally:
        try:
            dispatch._close(session)
        finally:
            host.reader_done()


@pytest.mark.parametrize("scope", ["company", "organization"])
def test_admitted_reader_finishes_old_path_before_folder_move(client, root, monkeypatch, scope):
    from bookflow.core import dispatch
    from bookflow.core.config import Config, os_login

    before = client.company.show(company="Demo Plumbing Co")
    old_path = Path(before["path"])
    company_id, organization_id = before["company_id"], before["organization_id"]
    login = os_login()
    user_id = Config.load(root / "config.toml").user_table(login)["user_id"]
    handle = start_serving(root, client_version())
    host = handle.host
    real_open = dispatch.open_company
    selected, finish_read = threading.Event(), threading.Event()

    def open_company(session, ctx, writable):
        if not writable and not selected.is_set():
            # This read has selected the old path in its hub snapshot, but has
            # not yet opened company.db. A rename must wait for this window too.
            assert session.company_row["path"] == str(old_path.relative_to(root))
            selected.set()
            assert finish_read.wait(5), "test did not release admitted reader"
        return real_open(session, ctx, writable)

    monkeypatch.setattr(dispatch, "open_company", open_company)
    new_name = "Moved Company" if scope == "company" else "Moved Organization"
    new_path = old_path.with_name(new_name) if scope == "company" else old_path.parent.with_name(new_name) / old_path.name
    inputs = {"name": new_name, "move": True}
    if scope == "organization":
        inputs["organization"] = organization_id
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reader = pool.submit(_reader_show, host, user_id, login, company_id)
            try:
                assert selected.wait(5)
                writer = pool.submit(_writer_command, host, user_id, login, f"{scope} rename", inputs,
                                     company_id if scope == "company" else None)
                with host._readers_lock:
                    assert host._readers_lock.wait_for(lambda: host._filesystem_exclusive, timeout=5)
                    assert host._readers_attached == 1
                assert old_path.exists() and not new_path.exists()
                assert not writer.done()
                with pytest.raises(BookflowError) as error:
                    host.reader_session(user_id, login)
                assert error.value.code == "E_DB_BUSY"
                assert error.value.details["operation"] == "filesystem_change"
            finally:
                finish_read.set()
            read_result = reader.result(timeout=5)
            write_result = writer.result(timeout=5)
        assert read_result["path"] == str(old_path)
        assert read_result["info"]["legal_name"] == before["info"]["legal_name"]
        assert write_result["moved"] and not old_path.exists() and new_path.exists()
        assert not host._filesystem_exclusive and host._readers_attached == 0
        assert _reader_show(host, user_id, login, company_id)["path"] == str(new_path)
    finally:
        finish_read.set()
        handle.stop()


def test_folder_gate_timeout_does_not_move_and_releases_admission(client, root):
    from bookflow.core import dispatch
    from bookflow.core.config import Config, os_login

    before = client.company.show(company="Demo Plumbing Co")
    old_path, company_id = Path(before["path"]), before["company_id"]
    login = os_login()
    user_id = Config.load(root / "config.toml").user_table(login)["user_id"]
    handle = start_serving(root, client_version())
    host = handle.host
    host.filesystem_wait_seconds = 0.02
    session = host.reader_session(user_id, login)
    try:
        with pytest.raises(BookflowError) as error:
            _writer_command(host, user_id, login, "company rename", {"name": "After Reader", "move": True}, company_id)
        assert error.value.code == "E_DB_BUSY"
        assert old_path.exists() and not old_path.with_name("After Reader").exists()
        assert not host._filesystem_exclusive
        # New reads are admitted after the failed writer job, even while the
        # original reader remains open. Pending-path recovery has not moved it.
        assert _reader_show(host, user_id, login, company_id)["path"] == str(old_path)
    finally:
        dispatch._close(session)
        host.reader_done()
    try:
        result = _writer_command(host, user_id, login, "company rename", {"name": "After Reader", "move": True}, company_id)
        assert result["moved"] and Path(result["path"]).exists()
        assert not host._filesystem_exclusive
    finally:
        handle.stop()


@pytest.mark.parametrize("scope", ["empty_organization", "empty_demo"])
def test_empty_organization_moves_also_take_filesystem_gate(client, root, monkeypatch, scope):
    from bookflow.core import moves
    from bookflow.core.config import Config, os_login

    if scope == "empty_organization":
        organization_id = client.organization.new(name="Empty Organization")["organization_id"]
    else:
        company_id = client.company.list()["items"][0]["company_id"]
        client.company.detach(company=company_id)
    login = os_login()
    user_id = Config.load(root / "config.toml").user_table(login)["user_id"]
    handle = start_serving(root, client_version())
    host = handle.host
    real_rename = moves.rename_noreplace
    observed = []

    def rename(source, target):
        assert host._filesystem_exclusive and host._readers_attached == 0
        observed.append((source, target))
        return real_rename(source, target)

    monkeypatch.setattr(moves, "rename_noreplace", rename)
    try:
        if scope == "empty_organization":
            result = _writer_command(host, user_id, login, "organization rename",
                                     {"organization": organization_id, "name": "Moved Empty", "move": True})
            assert result["moved"]
        else:
            result = _writer_command(host, user_id, login, "demo reset", {})
            assert result["trashed_path"]
        assert len(observed) == 1 and not host._filesystem_exclusive
    finally:
        handle.stop()


def test_filesystem_gate_released_after_move_failure(client, root, monkeypatch):
    from bookflow.core import moves
    from bookflow.core.config import Config, os_login

    company_id = client.company.list()["items"][0]["company_id"]
    login = os_login()
    user_id = Config.load(root / "config.toml").user_table(login)["user_id"]
    handle = start_serving(root, client_version())
    host = handle.host

    def fail(source, target):
        assert host._filesystem_exclusive
        raise OSError(errno.EIO, "injected rename failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(moves, "rename_noreplace", fail)
            with pytest.raises(BookflowError) as error:
                _writer_command(host, user_id, login, "company rename", {"name": "Retry Move", "move": True}, company_id)
            assert error.value.code == "E_RENAME_INCOMPLETE"
        assert not host._filesystem_exclusive
        assert _reader_show(host, user_id, login, company_id)["company_id"] == company_id
    finally:
        handle.stop()
