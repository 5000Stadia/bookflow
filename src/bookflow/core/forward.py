"""Hand a command to a live host over its socket (row 3 plan, Local hand-off). Standard library only."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

from bookflow.core.context import Context, client_version
from bookflow.core.errors import BookflowError

DESCRIPTOR = "host.json"


def _runtime_directory_error(errno_name: str) -> BookflowError:
    return BookflowError(
        "E_IO",
        message="The local hand-off runtime directory is unavailable.",
        details={"operation": "runtime_socket", "errno": errno_name, "path": None},
    )


def _errno_name(error: OSError) -> str:
    return errno.errorcode.get(error.errno or 0, "EIO")


def _prepare_runtime_directory(path: Path) -> None:
    """Create an owner-private runtime directory, refusing pre-existing unsafe paths."""
    if not hasattr(os, "getuid"):  # pragma: no cover - Windows transport is not implemented yet
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return

    created = False
    try:
        path.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as e:
        raise _runtime_directory_error(_errno_name(e))

    try:
        info = path.lstat()
    except OSError as e:
        raise _runtime_directory_error(_errno_name(e))
    if not stat.S_ISDIR(info.st_mode):
        raise _runtime_directory_error("ENOTDIR")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise _runtime_directory_error("EACCES")
    if created or stat.S_IMODE(info.st_mode) != 0o700:
        try:
            os.chmod(path, 0o700, follow_symlinks=False)
        except (NotImplementedError, OSError) as e:
            if isinstance(e, NotImplementedError):
                raise _runtime_directory_error("EIO")
            raise _runtime_directory_error(_errno_name(e))


def socket_path(data_root: Path) -> Path:
    digest = hashlib.sha256(str(data_root.resolve()).encode("utf-8")).hexdigest()[:16]
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and Path(base).is_dir():
        d = Path(base) / "bookflow"
    else:
        d = Path(tempfile.gettempdir()) / f"bookflow-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
    _prepare_runtime_directory(d)
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
    attempted = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sk:
            sk.settimeout(timeout)
            sk.connect(sock_path)
            payload = json.dumps(envelope, default=str).encode("utf-8")
            attempted = True
            sk.sendall(len(payload).to_bytes(4, "big") + payload)
            head = b""
            while len(head) < 4:
                chunk = sk.recv(4 - len(head))
                if not chunk:
                    raise BookflowError("E_IO", details={"stage": "local response", "outcome": "unknown"})
                head += chunk
            size = int.from_bytes(head, "big")
            body = b""
            while len(body) < size:
                chunk = sk.recv(min(65536, size - len(body)))
                if not chunk:
                    raise BookflowError("E_IO", details={"stage": "local response", "outcome": "unknown"})
                body += chunk
            return json.loads(body.decode("utf-8"))
    except (OSError, ValueError):
        if attempted:
            raise BookflowError("E_IO", details={"stage": "local response", "outcome": "unknown"}) from None
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


def try_forward_transfer(root, cmd, raw, ctx, selector, source, dry_run,
                         input_stream=None, output_stream=None) -> dict | None:
    """Forward one binary conversation; never retry locally after sending begins."""
    from bookflow.company.attachment_store import BodyInfo
    from bookflow.core.transfer_protocol import FramedReader, recv_json, send_body, send_json
    from bookflow.core.transfer_resources import TransferLease
    from bookflow.core.transfers import copy_output

    if cmd.bootstrap:
        return None
    desc = read_descriptor(root)
    if not desc or desc.get("pid") == os.getpid() or not _pid_alive(int(desc.get("pid", 0))):
        return None
    if desc.get("version") != client_version():
        raise BookflowError("E_VERSION_MISMATCH", details={"host": desc.get("version"), "client": client_version()})
    transfer = cmd.transfer
    if transfer is None or (transfer.direction == "input" and input_stream is None) or (
            transfer.direction == "output" and output_stream is None):
        raise BookflowError("E_USAGE", message="A binary transfer requires its stream or sink.")
    envelope = {"command": cmd.name, "input": raw, "company_selector": selector,
                "company_source": source, "dry_run": dry_run,
                "context": ctx.model_dump(mode="json"),
                "transfer": {"version": 1, "direction": transfer.direction}}

    def result(reply):
        if "error" in reply:
            err = reply["error"]
            if not isinstance(err, dict) or not isinstance(err.get("code"), str):
                raise BookflowError("E_IO", message="Invalid transfer error response.")
            raise BookflowError(err["code"], message=err.get("message"), details=err.get("details") or {})
        if set(reply) != {"output"} or not isinstance(reply["output"], dict):
            raise BookflowError("E_IO", message="Missing successful transfer completion.")
        return reply["output"]

    with TransferLease("", "", lambda lease: None) as lease:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sk:
            sk.settimeout(30)
            try:
                sk.connect(desc["socket"])
            except OSError:
                return None
            try:
                send_json(sk, envelope, limit=8192, check=lease.check_io)
                ready = recv_json(sk, check=lease.check_io)
                if "error" in ready:
                    result(ready)
                if ready.get("ready") is not True:
                    raise BookflowError("E_IO", message="Missing transfer ready response.")
                if transfer.direction == "input":
                    limit = ready.get("limit")
                    if type(limit) is not int or not 0 < limit <= 100_000_000:
                        raise BookflowError("E_IO", message="Invalid transfer byte limit.")
                    send_body(sk, input_stream, limit, check=lease.check_io)
                else:
                    size, sha = ready.get("size_bytes"), ready.get("sha256")
                    if (type(size) is not int or not 0 <= size <= 100_000_000
                            or not isinstance(sha, str) or len(sha) != 64
                            or any(c not in "0123456789abcdef" for c in sha)):
                        raise BookflowError("E_IO", message="Invalid download metadata.")
                    reader = FramedReader(sk, 100_000_000, check=lease.check_io)
                    copy_output(reader, output_stream, BodyInfo(sha, size), lease.check_io)
                return result(recv_json(sk, check=lease.check_io))
            except BookflowError as exc:
                if exc.code != "E_IO":
                    raise
                raise BookflowError("E_IO", message="Transfer interrupted; a write may have committed.",
                                    details={**exc.details, "may_have_committed": transfer.direction == "input"}) from exc
            except (OSError, ValueError) as exc:
                raise BookflowError("E_IO", message="Transfer interrupted; a write may have committed.",
                                    details={"may_have_committed": transfer.direction == "input"}) from exc
