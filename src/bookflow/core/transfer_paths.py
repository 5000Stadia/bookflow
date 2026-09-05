"""Calling-machine file sinks for verified binary transfers."""

from __future__ import annotations

import errno
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


@contextmanager
def atomic_output(destination: Path) -> Iterator[BinaryIO]:
    """Publish only when the caller returns after verified body and final result.

    The private file and destination are anchored to one open parent directory.
    Publication uses a no-replace hard link; unsupported filesystems fail closed.
    """
    parent_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    temporary = ".bookflow-transfer-" + secrets.token_hex(16)
    owned = False
    published = False
    identity = None
    try:
        try:
            os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(errno.EEXIST, "Output file already exists", str(destination))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent_fd)
        owned = True
        try:
            stream = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            identity = os.fstat(stream.fileno())
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                follow_symlinks=False)
        published = True
        os.unlink(temporary, dir_fd=parent_fd)
        owned = False
        os.fsync(parent_fd)
    except BaseException:
        if published:
            try:
                current = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    os.unlink(destination.name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            except OSError:
                pass
        raise
    finally:
        try:
            if owned:
                os.unlink(temporary, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
