"""Binary local conversations require authorization, bounded frames and completion."""
import hashlib
import io
import json
import os
import socket
import threading
from types import SimpleNamespace

import pytest

from bookflow.adapters.http.local import LocalListener
from bookflow.commands.host_cmds import make_local_handler
from bookflow.core import forward, registry
from bookflow.core.context import Context, Interface, client_version
from bookflow.core.errors import BookflowError
from bookflow.core.host import Host
from bookflow.core.transfer_protocol import recv_json, send_json, send_body


def context():
    return Context.new(Interface.python, "local-test", reason="Local binary test")


@pytest.fixture
def local_host(root, client, tmp_path, monkeypatch):
    target = client.customer.create(name="Local transfer target", company="Demo Plumbing Co")
    host = Host(root, version=client_version())
    host.start()
    host.test_target = target
    listener = LocalListener(host, tmp_path / "local.sock", make_local_handler(host, client_version()))
    listener.start()
    monkeypatch.setattr(forward, "read_descriptor", lambda _: {
        "pid": os.getpid() + 1, "version": client_version(), "socket": str(listener.path)})
    monkeypatch.setattr(forward, "_pid_alive", lambda _: True)
    try:
        yield host, listener
    finally:
        listener.stop()
        host.stop()


def invoke(root, name, raw, **streams):
    return forward.try_forward_transfer(root, registry.get(name), raw, context(), "Demo Plumbing Co", "option", False, **streams)


def test_real_host_upload_download_parity(root, local_host):
    # Import permits this test during the parent registry-loader integration.
    from bookflow.commands import attachment_cmds  # noqa: F401
    data = bytes(range(256)) * 600
    raw = {"record_type": "customer", "record_id": local_host[0].test_target["id"], "original_filename": "receipt.pdf"}
    uploaded = invoke(root, "attachment add", raw, input_stream=io.BytesIO(data))
    sink = io.BytesIO()
    downloaded = invoke(root, "attachment get", {"attachment": uploaded["attachment"]["id"]}, output_stream=sink)
    assert sink.getvalue() == data
    assert downloaded["sha256"] == hashlib.sha256(data).hexdigest()
    assert downloaded["size_bytes"] == len(data)


class Unreadable:
    def read(self, n):
        pytest.fail("denied upload consumed source")


def test_denied_before_source_read(root, local_host):
    from bookflow.commands import attachment_cmds  # noqa: F401
    with pytest.raises(BookflowError) as error:
        invoke(root, "attachment add", {"record_type": "company", "record_id": "company",
               "original_filename": "../invalid"}, input_stream=Unreadable())
    assert error.value.code == "E_VALIDATION"


@pytest.mark.parametrize("body", [
    b'{"transfer":{"version":1,"version":1,"direction":"input"}}',
    b'{"transfer":{"version":true,"direction":"input"},"command":"attachment add","input":{}}',
    b'{"transfer":{"version":1,"direction":"sideways"},"command":"attachment add","input":{}}',
    b'{"transfer":{"version":1,"direction":"input"},"command":"attachment add","input":{},"extra":1}',
    json.dumps({"transfer": {"version": 1, "direction": "input"}, "command": "attachment add",
                "input": {"caption": "x" * 8192}}).encode(),
])
def test_invalid_envelope_never_dispatches(tmp_path, body):
    seen = []
    listener = LocalListener(None, tmp_path / "unused", lambda *args: seen.append(args))
    client, server = socket.socketpair()
    thread = threading.Thread(target=listener._serve_one, args=(server,))
    thread.start()
    with client:
        client.sendall(len(body).to_bytes(4, "big") + body)
        assert recv_json(client)["error"]["code"] == "E_VALIDATION"
    thread.join(2)
    assert not thread.is_alive() and not seen


@pytest.mark.parametrize("direction, failure", [
    ("input", "lost-final"), ("output", "lost-final"), ("output", "digest"),
    ("output", "size"), ("output", "terminal"), ("output", "final-error"),
])
def test_lost_final_never_falls_back(tmp_path, monkeypatch, direction, failure):
    path = tmp_path / "fake.sock"
    sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sk.bind(str(path))
    sk.listen()
    data = b"committed bytes"
    def serve():
        conn, _ = sk.accept()
        with conn:
            recv_json(conn, limit=8192)
            if direction == "output":
                send_json(conn, {"ready": True, "size_bytes": len(data) + (failure == "size"),
                    "sha256": "0" * 64 if failure == "digest" else hashlib.sha256(data).hexdigest()})
                if failure == "terminal":
                    conn.sendall(len(data).to_bytes(4, "big") + data)
                else:
                    send_body(conn, io.BytesIO(data), len(data))
                if failure == "final-error":
                    send_json(conn, {"error": BookflowError("E_IO", "Completion failed").to_dict()})
            else:
                from bookflow.core.transfer_protocol import FramedReader
                send_json(conn, {"ready": True, "limit": 100})
                reader = FramedReader(conn, 100)
                while reader.read(100):
                    pass
    thread = threading.Thread(target=serve)
    thread.start()
    monkeypatch.setattr(forward, "read_descriptor", lambda _: {"pid": os.getpid()+1,
        "version": client_version(), "socket": str(path)})
    monkeypatch.setattr(forward, "_pid_alive", lambda _: True)
    cmd = SimpleNamespace(bootstrap=False, name="binary", transfer=SimpleNamespace(direction=direction))
    try:
        with pytest.raises(BookflowError) as error:
            forward.try_forward_transfer(tmp_path, cmd, {}, context(), None, "option", False,
                input_stream=io.BytesIO(data), output_stream=io.BytesIO())
        assert error.value.code == "E_IO"
        assert error.value.details["may_have_committed"] == (direction == "input")
    finally:
        thread.join(2)
        sk.close()
    assert not thread.is_alive()


def test_missing_host_is_only_pre_send_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(forward, "read_descriptor", lambda _: None)
    cmd = SimpleNamespace(bootstrap=False)
    assert forward.try_forward_transfer(tmp_path, cmd, {}, context(), None, "option", False) is None


def test_binary_version_mismatch_before_source(tmp_path, monkeypatch):
    monkeypatch.setattr(forward, "read_descriptor", lambda _: {
        "pid": os.getpid()+1, "version": "old", "socket": "unused"})
    monkeypatch.setattr(forward, "_pid_alive", lambda _: True)
    with pytest.raises(BookflowError) as error:
        forward.try_forward_transfer(tmp_path, SimpleNamespace(bootstrap=False), {}, context(),
            None, "option", False, input_stream=Unreadable())
    assert error.value.code == "E_VERSION_MISMATCH"


@pytest.mark.parametrize("truncated", [False, True])
def test_handler_kernel_identity_safe_context_and_truncation(tmp_path, monkeypatch, truncated):
    from bookflow.commands import host_cmds
    from bookflow.adapters.http import published_transfer
    from bookflow.core.publication import OSBinding
    from bookflow.core.publication_admission import Admission
    from bookflow.core.transfer_resources import TransferLease
    from bookflow.company.attachment_store import BodyInfo
    expected = b"complete download"
    seen = []
    cmd = SimpleNamespace(bootstrap=False, transfer=SimpleNamespace(direction="output"))
    monkeypatch.setattr(registry, "get", lambda _: cmd)
    monkeypatch.setattr(OSBinding, "capture", lambda host, login, principal:
        seen.append(login) or SimpleNamespace(user_id="real-user", revalidate=lambda db: None))

    class FakeTransfer:
        def __init__(self, host, cmd, raw, ctx, user_id, login, **kwargs):
            seen.append((ctx, user_id))
            self.resource = SimpleNamespace(lease=TransferLease(user_id, "real-company", lambda _: None))
            self.prepared = SimpleNamespace(info=BodyInfo(hashlib.sha256(expected).hexdigest(), len(expected)),
                metadata={"original_filename": "a.pdf", "media_type": "application/pdf"})
            self.reader = io.BytesIO(expected[:-1] if truncated else expected)
            class Document(dict):
                def check(document): seen.append("checked")
            self.output = Document(done=True)
        def suspend_publication(self):
            pytest.fail("uncontended transfer must not suspend")
        def resume_publication(self):
            pytest.fail("uncontended transfer must not resume")
        def check_output(self):
            self.resource.lease.check_io()
            seen.append("checked")
        def close(self):
            self.resource.lease.close()

    monkeypatch.setattr(published_transfer, "PublishedTransfer", FakeTransfer)
    host = SimpleNamespace(publication_admission=Admission(), _stopping=False)
    handler = make_local_handler(host, client_version())
    listener = LocalListener(host, tmp_path / "unused", handler)
    client, server = socket.socketpair()
    thread = threading.Thread(target=listener._serve_one, args=(server,))
    thread.start()
    raw_ctx = context().model_dump(mode="json")
    raw_ctx.update(actor_id="forged", actor_kind="agent", on_behalf_of="forged", company_id="forged", interface="system")
    with client:
        send_json(client, {"command": "binary", "input": {}, "context": raw_ctx,
            "transfer": {"version": 1, "direction": "output"}})
        ready = recv_json(client)
        assert ready["ready"] is True
        from bookflow.core.transfer_protocol import FramedReader
        reader = FramedReader(client, 100)
        if truncated:
            with pytest.raises(BookflowError) as error:
                while reader.read(100):
                    pass
            assert error.value.code == "E_IO"
        else:
            assert reader.read(100) == expected
            assert reader.read(100) == b""
            assert recv_json(client) == {"output": {"done": True}}
    thread.join(2)
    import pwd
    assert seen[0] == pwd.getpwuid(os.getuid()).pw_name
    ctx, user_id = seen[1]
    assert user_id == "real-user" and ctx.interface == Interface.cli
    assert ctx.actor_id is ctx.actor_kind is ctx.on_behalf_of is ctx.company_id is None
    assert "checked" in seen


def test_unmapped_peer_denied_before_source(root, local_host, monkeypatch):
    from bookflow.adapters.http import local
    monkeypatch.setattr(local, "peer_login", lambda _: "unmapped-transfer-peer")
    with pytest.raises(BookflowError) as error:
        invoke(root, "attachment add", {"record_type": "customer",
            "record_id": local_host[0].test_target["id"], "original_filename": "a.pdf"}, input_stream=Unreadable())
    assert error.value.code == "E_UNAUTHENTICATED"


def test_ordinary_binary_command_requires_transfer_flag(root, local_host):
    host, listener = local_host
    reply = forward.call_host(str(listener.path), {"command": "attachment add",
        "input": {"record_type": "customer", "record_id": host.test_target["id"], "original_filename": "a.pdf"},
        "context": context().model_dump(mode="json"), "company_selector": "Demo Plumbing Co"})
    assert reply["error"]["code"] == "E_USAGE"
