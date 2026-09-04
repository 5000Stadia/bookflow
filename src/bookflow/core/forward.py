"""Hand a command to a live host over its socket (row 3 plan, Local hand-off). Standard library only."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

from bookflow.core.context import Context, client_version
from bookflow.core.errors import BookflowError

DESCRIPTOR = "host.json"


def socket_path(data_root: Path) -> Path:
    digest = hashlib.sha256(str(data_root.resolve()).encode("utf-8")).hexdigest()[:16]
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and Path(base).is_dir():
        d = Path(base) / "bookflow"
    else:
        d = Path(tempfile.gettempdir()) / f"bookflow-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d / f"{digest}.sock"


def read_descriptor(data_root: Path) -> dict[str, Any] | None:
    p = data_root / DESCRIPTOR
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def call_host(sock_path: str, envelope: dict[str, Any], timeout: float = 30.0) -> dict[str, Any] | None:
    """Send one envelope, receive one JSON reply; None when no host answers."""
    if sys.platform == "win32":  # pragma: no cover - named pipes arrive with the Windows port
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sk:
            sk.settimeout(timeout)
            sk.connect(sock_path)
            payload = json.dumps(envelope, default=str).encode("utf-8")
            sk.sendall(len(payload).to_bytes(4, "big") + payload)
            head = b""
            while len(head) < 4:
                chunk = sk.recv(4 - len(head))
                if not chunk:
                    return None
                head += chunk
            size = int.from_bytes(head, "big")
            body = b""
            while len(body) < size:
                chunk = sk.recv(min(65536, size - len(body)))
                if not chunk:
                    return None
                body += chunk
            return json.loads(body.decode("utf-8"))
    except (OSError, ValueError):
        return None


def try_forward(data_root: Path, cmd, raw_input: dict[str, Any], ctx: Context, company_selector: str | None,
                company_source: str, dry_run: bool) -> dict[str, Any] | None:
    """Return the host's output when a live host serves this data root; None to proceed locally.

    ``local_only`` keeps a command off the HTTP routes; it does not keep it off the local socket, where
    the peer's OS login is known, so `company use` and `user set-password` reach a running host. Only the
    bootstrap commands (`init`, `serve`), which own the process and the lock, are never forwarded.
    """
    if cmd.bootstrap:
        return None
    desc = read_descriptor(data_root)
    if not desc or desc.get("pid") == os.getpid() or not _pid_alive(int(desc.get("pid", 0))):
        return None
    if desc.get("version") != client_version():
        raise BookflowError("E_VERSION_MISMATCH", details={"host": desc.get("version"), "client": client_version()})
    envelope = {"command": cmd.name, "input": raw_input, "company_selector": company_selector, "company_source": company_source,
                "dry_run": dry_run, "context": ctx.model_dump(mode="json")}
    reply = call_host(desc["socket"], envelope)
    if reply is None:
        return None
    if "error" in reply:
        err = reply["error"]
        raise BookflowError(err["code"], message=err.get("message"), details=err.get("details") or {})
    return reply["output"]
