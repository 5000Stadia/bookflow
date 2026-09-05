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
MAX_FRAME_BYTES = 8 * 1024 * 1024
CONNECTION_TIMEOUT_SECONDS = 30.0


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
        self._stopping = False
        try:
            self.path.unlink()
        except OSError:
            pass
        sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sk.bind(str(self.path))
            os.chmod(self.path, 0o600)
            sk.listen(16)
            sk.settimeout(0.5)
        except BaseException:
            sk.close()
            raise
        self._sock = sk
        self._thread = threading.Thread(target=self._loop, name="bookflow-local", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        sk = self._sock
        if sk is not None:
            try:
                sk.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sk.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._sock = None
        self._thread = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def _loop(self) -> None:
        sk = self._sock
        if sk is None:
            return
        while not self._stopping:
            try:
                conn, _ = sk.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve_one, args=(conn,), daemon=True).start()

    def _serve_one(self, conn: socket.socket) -> None:
        from bookflow.core.transfer_protocol import _IO, _decode, send_json
        from bookflow.core.transfer_resources import TransferLease
        import time

        with conn, TransferLease("", "", lambda lease: None) as lease:
            binary = False
            try:
                conn.settimeout(CONNECTION_TIMEOUT_SECONDS)
                wire = _IO(300, 30, lease.check_io, time.monotonic)
                size = int.from_bytes(wire.exact(conn, 4), "big")
                if size > MAX_FRAME_BYTES:
                    raise BookflowError("E_VALIDATION", details={"fields": [{
                        "field": "frame", "problem": f"must be at most {MAX_FRAME_BYTES} bytes",
                    }]})
                body = wire.exact(conn, size)
                envelope = _decode(body)
                binary = "transfer" in envelope
                login = peer_login(conn)
                if binary:
                    validate_transfer_envelope(envelope, size)
                    transfer = getattr(self.handler, "transfer", None)
                    if transfer is None:
                        raise BookflowError("E_USAGE", message="Binary forwarding is unavailable.")
                    transfer(login, envelope, conn, check=lease.check_io)
                    return
                reply = {"output": self.handler(login, envelope)}
            except BookflowError as e:
                reply = {"error": e.to_dict()}
            except Exception as e:  # noqa: BLE001
                reply = {"error": BookflowError("E_INTERNAL", message="host failure", details={"cause": type(e).__name__}).to_dict()}
            try:
                if binary:
                    send_json(conn, reply, check=lease.check_io)
                else:
                    payload = json.dumps(reply, default=str).encode("utf-8")
                    conn.sendall(len(payload).to_bytes(4, "big") + payload)
            except (OSError, BookflowError):
                pass


def validate_transfer_envelope(envelope, size=None):
    """Validate binary transport fields before command dispatch or body reads."""
    from bookflow.core.transfer_protocol import _encode
    _encode(envelope, 8192)
    allowed = {"command", "input", "context", "company_selector", "company_source",
               "dry_run", "transfer", "version"}
    transfer = envelope.get("transfer")
    if (size is not None and size > 8192
            or set(envelope) - allowed
            or type(transfer) is not dict or set(transfer) != {"version", "direction"}
            or type(transfer.get("version")) is not int or transfer["version"] != 1
            or transfer.get("direction") not in ("input", "output")
            or not isinstance(envelope.get("command"), str)
            or type(envelope.get("input")) is not dict
            or type(envelope.get("context", {})) is not dict
            or type(envelope.get("dry_run", False)) is not bool
            or envelope.get("company_selector") is not None and not isinstance(envelope["company_selector"], str)
            or not isinstance(envelope.get("company_source", "option"), str)):
        raise BookflowError("E_VALIDATION", message="Invalid binary transfer envelope.")


def _recv_exact(conn: socket.socket, size: int) -> bytes | None:
    """Read one complete frame component, or return ``None`` on an early EOF."""
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(min(65536, size - len(data)))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def context_from_envelope(envelope: dict[str, Any]) -> Context:
    """The caller's context with every identity field discarded; the host rebuilds those from the peer."""
    raw = dict(envelope.get("context") or {})
    for k in IDENTITY_FIELDS:
        raw.pop(k, None)
    raw["interface"] = Interface.cli.value
    return Context.model_validate(raw)
