"""Durable filesystem boundaries for metadata and authoritative directory entries.

POSIX directory synchronization errors propagate to the caller. Windows has no
portable directory-fsync operation here; directory moves use MoveFileExW's
write-through flag in core.moves. These operations rely on the filesystem and
device honoring synchronization, and do not claim hardware-level verification.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from bookflow.core.performance import measured, span


@measured("directory.sync")
def sync_directory(path: Path) -> None:
    """Persist directory entries on POSIX; never suppress a failed synchronization."""
    if os.name != "posix":  # pragma: no cover - no portable Windows directory fsync
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_move_parents(src: Path, dst: Path) -> None:
    """Persist both sides of a rename, including an already-moved recovery attempt."""
    sync_directory(dst.parent)
    if src.parent != dst.parent:
        sync_directory(src.parent)


@measured("file.sync")
def sync_file(path: Path) -> None:
    """Synchronize an existing owned file without changing its contents."""
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@measured("file.publish")
def write_metadata(path: Path, text: str) -> None:
    """Replace a UTF-8 file using a private, unique, same-directory temporary file.

    Content is synchronized before replacement, then its directory entry is
    synchronized. A failure after replacement leaves the new file visible and is
    reported; retry operates on that actual state. Only this call's temporary
    file is ever removed, and a cleanup failure cannot mask the original error.
    """
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        try:
            stream = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            with span("file.sync"):
                os.fsync(stream.fileno())
        with span("file.replace"):
            os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
