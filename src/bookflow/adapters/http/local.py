"""The socket listener for forwarded calls: identity from peer credentials, never from the sender (row 3 plan, Local hand-off)."""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
from pathlib import Path
from typing import Any

from bookflow.core.config import Config
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError

IDENTITY_FIELDS = ("actor_id", "actor_kind", "on_behalf_of", "company_id")


def peer_login(conn: socket.socket) -> str:
    """The OS login of the connected peer, from the kernel."""
    if sys.platform.startswith("linux"):
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", creds)
    elif sys.platform == "darwin":  # pragma: no cover
        import ctypes
        libc = ctypes.CDLL(None)
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        libc.getpeereid(conn.fileno(), ctypes.byref(uid), ctypes.byref(gid))
        uid = uid.value
    else:  # pragma: no cover
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "peer credentials unavailable on this platform"})
    import pwd
    return pwd.getpwuid(uid).pw_name


class LocalListener:
    """Accepts envelopes on a Unix socket and runs them through the host with the peer's identity."""

    def __init__(self, host, path: Path, handler):
        self.host = host
        self.path = path
        self.handler = handler  # (login, envelope) -> output dict; raises BookflowError
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False

    def start(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
        sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sk.bind(str(self.path))
        os.chmod(self.path, 0o600)
        sk.listen(16)
        sk.settimeout(0.5)
        self._sock = sk
        self._thread = threading.Thread(target=self._loop, name="bookflow-local", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._sock is not None:
            self._sock.close()
        try:
            self.path.unlink()
        except OSError:
            pass

    def _loop(self) -> None:
        while not self._stopping:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve_one, args=(conn,), daemon=True).start()

    def _serve_one(self, conn: socket.socket) -> None:
        with conn:
            try:
                head = conn.recv(4)
                if len(head) < 4:
                    return
                size = int.from_bytes(head, "big")
                body = b""
                while len(body) < size:
                    chunk = conn.recv(min(65536, size - len(body)))
                    if not chunk:
                        return
                    body += chunk
                envelope = json.loads(body.decode("utf-8"))
                login = peer_login(conn)
                try:
                    reply = {"output": self.handler(login, envelope)}
                except BookflowError as e:
                    reply = {"error": e.to_dict()}
            except Exception as e:  # noqa: BLE001
                reply = {"error": BookflowError("E_INTERNAL", message="host failure", details={"cause": type(e).__name__}).to_dict()}
            payload = json.dumps(reply, default=str).encode("utf-8")
            try:
                conn.sendall(len(payload).to_bytes(4, "big") + payload)
            except OSError:
                pass


def context_from_envelope(envelope: dict[str, Any]) -> Context:
    """The caller's context with every identity field discarded; the host rebuilds those from the peer."""
    raw = dict(envelope.get("context") or {})
    for k in IDENTITY_FIELDS:
        raw.pop(k, None)
    raw.setdefault("interface", Interface.cli.value)
    return Context.model_validate(raw)
