"""The one lock per data root, held exclusively for the whole of a command."""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path
from types import TracebackType

from bookflow.core.errors import BookflowError
from bookflow.core.performance import measured

DEFAULT_TIMEOUT = 5.0


def _timeout() -> float:
    raw = os.environ.get("BOOKFLOW_LOCK_TIMEOUT")
    if raw is None:
        return DEFAULT_TIMEOUT
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_TIMEOUT


class RootLock:
    """Context manager over ``<data_root>/root.lock``.

    The holder writes hostname, pid, and command into the file after acquiring.
    The contents are ephemeral and are never data.
    """

    def __init__(self, data_root: Path, command: str):
        self.path = data_root / "root.lock"
        self.command = command
        self._fh = None

    def _try_lock(self) -> bool:
        if sys.platform == "win32":  # pragma: no cover
            import msvcrt
            try:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        import fcntl
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def holder(self) -> dict[str, str]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        out: dict[str, str] = {}
        for line in text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    @measured("lock.acquire")
    def __enter__(self) -> "RootLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        deadline = time.monotonic() + _timeout()
        while True:
            if self._try_lock():
                break
            if time.monotonic() >= deadline:
                holder = self.holder()
                self._fh.close()
                self._fh = None
                held = None
                try:
                    held = round(time.time() - float(holder.get("started", "")), 1)
                except ValueError:
                    pass
                raise BookflowError("E_DB_BUSY", details={"command": holder.get("command"), "held_seconds": held})
            time.sleep(0.05)
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"hostname={socket.gethostname()}\npid={os.getpid()}\ncommand={self.command}\nstarted={time.time()}\n")
        self._fh.flush()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.flush()
        finally:
            if sys.platform != "win32":
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
