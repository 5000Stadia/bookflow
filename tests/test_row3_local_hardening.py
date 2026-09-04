"""Focused regression witnesses for the Row 3 local hand-off boundary."""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError


class _FragmentedConnection:
    """A socket-shaped frame source that returns exactly one byte per recv call."""

    def __init__(self, incoming: bytes):
        self.incoming = bytearray(incoming)
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def recv(self, size: int) -> bytes:
        if not self.incoming:
            return b""
        take = min(size, 1)
        chunk = bytes(self.incoming[:take])
        del self.incoming[:take]
        return chunk

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)


def test_listener_reads_fragmented_header_and_body(monkeypatch, tmp_path):
    from bookflow.adapters.http import local

    envelope = {"command": "company list", "input": {"marker": "fragmented"}}
    payload = json.dumps(envelope).encode("utf-8")
    conn = _FragmentedConnection(len(payload).to_bytes(4, "big") + payload)
    seen = []
    listener = local.LocalListener(
        None,
        tmp_path / "unused.sock",
        lambda login, received: seen.append((login, received)) or {"accepted": True},
    )
    monkeypatch.setattr(local, "peer_login", lambda _conn: "local-user")

    listener._serve_one(conn)

    assert seen == [("local-user", envelope)]
    response_size = int.from_bytes(conn.sent[:4], "big")
    response = json.loads(conn.sent[4:4 + response_size])
    assert response == {"output": {"accepted": True}}


def test_forwarded_context_forces_cli_and_discards_identity():
    from bookflow.adapters.http.local import context_from_envelope

    raw = Context.new(Interface.system, "forged-client").model_dump(mode="json")
    raw.update(
        actor_id="forged-actor",
        actor_kind="agent",
        on_behalf_of="forged-principal",
        company_id="forged-company",
    )

    context = context_from_envelope({"context": raw})

    assert context.interface is Interface.cli
    assert context.client_name == "forged-client"
    assert context.actor_id is None
    assert context.actor_kind is None
    assert context.on_behalf_of is None
    assert context.company_id is None


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="Unix runtime-directory contract")
def test_fallback_runtime_directory_must_already_be_private(monkeypatch, tmp_path):
    from bookflow.core import forward

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(forward.tempfile, "gettempdir", lambda: str(tmp_path))
    runtime = tmp_path / f"bookflow-{os.getuid()}"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o755)

    with pytest.raises(BookflowError) as caught:
        forward.socket_path(tmp_path / "data-root")

    assert caught.value.code == "E_IO"
    assert caught.value.details == {"operation": "runtime_socket", "errno": "EACCES", "path": None}
    assert str(runtime) not in json.dumps(caught.value.to_dict())
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o755


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="Unix runtime-directory contract")
def test_xdg_bookflow_directory_must_be_owned_by_the_current_uid(monkeypatch, tmp_path):
    from bookflow.core import forward

    xdg = tmp_path / "xdg"
    runtime = xdg / "bookflow"
    runtime.mkdir(mode=0o700, parents=True)
    real_uid = os.getuid()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))
    monkeypatch.setattr(forward.os, "getuid", lambda: real_uid + 1)

    with pytest.raises(BookflowError) as caught:
        forward.socket_path(tmp_path / "data-root")

    assert caught.value.code == "E_IO"
    assert caught.value.details["errno"] == "EACCES"
    assert caught.value.details["path"] is None
    assert str(runtime) not in json.dumps(caught.value.to_dict())


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="Unix runtime-directory contract")
def test_new_xdg_bookflow_directory_is_mode_0700(monkeypatch, tmp_path):
    from bookflow.core import forward

    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))

    path = forward.socket_path(tmp_path / "data-root")

    assert path.parent == xdg / "bookflow"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_client_version_mismatch_never_calls_the_socket(monkeypatch, tmp_path):
    from bookflow.core import forward

    socket_calls = []
    monkeypatch.setattr(
        forward,
        "read_descriptor",
        lambda _root: {"pid": os.getpid() + 1, "socket": "/must-not-connect", "version": "old-host"},
    )
    monkeypatch.setattr(forward, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(forward, "client_version", lambda: "current-client")
    monkeypatch.setattr(forward, "call_host", lambda *_args, **_kwargs: socket_calls.append(True))

    with pytest.raises(BookflowError) as caught:
        forward.try_forward(
            tmp_path,
            SimpleNamespace(bootstrap=False, name="company list"),
            {},
            Context.new(Interface.cli, "bookflow-cli"),
            None,
            "default",
            False,
        )

    assert caught.value.code == "E_VERSION_MISMATCH"
    assert caught.value.details == {"host": "old-host", "client": "current-client"}
    assert socket_calls == []


class _BlockingAcceptSocket:
    def __init__(self):
        self.accepting = threading.Event()
        self.released = threading.Event()

    def accept(self):
        self.accepting.set()
        self.released.wait(timeout=5)
        raise OSError("listener closed")

    def shutdown(self, _how):
        self.released.set()

    def close(self):
        self.released.set()


def test_listener_stop_wakes_accept_before_join(tmp_path):
    from bookflow.adapters.http.local import LocalListener

    listener = LocalListener(None, tmp_path / "absent.sock", lambda _login, _envelope: {})
    blocking = _BlockingAcceptSocket()
    listener._sock = blocking
    listener._thread = threading.Thread(target=listener._loop, daemon=True)
    listener._thread.start()
    assert blocking.accepting.wait(timeout=1)

    began = time.monotonic()
    listener.stop()
    elapsed = time.monotonic() - began

    assert blocking.released.is_set()
    assert elapsed < 0.25
