"""Bounded attachment bytes for callers holding company ownership and a lease.

Streams must already be authorized. This module does not resolve companies,
authorize callers, or manage attachment metadata. Paths stay inside this layer.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import BinaryIO, Iterator

from bookflow.core.durability import sync_directory
from bookflow.core.errors import BookflowError

CHUNK_SIZE = 65_536
MAX_SIZE = 100_000_000
_STAGING_TOKEN = object()


@dataclass(frozen=True)
class BodyInfo:
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Publication(BodyInfo):
    newly_published: bool


class StagedAttachment:
    """An invocation-owned resource, valid only inside its staging context."""

    def __init__(self, store: Path, path: Path, writer: BinaryIO, info: BodyInfo,
                 *, _token: object | None = None):
        if _token is not _STAGING_TOKEN:
            raise _io("staged_resource")
        self._store = store
        self._path = path
        self._writer = writer
        self._info = info
        self._consumed = False
        status = os.fstat(writer.fileno())
        self._identity = (status.st_dev, status.st_ino)

    @property
    def info(self) -> BodyInfo:
        return self._info


def _io(check: str) -> BookflowError:
    return BookflowError("E_IO", "Attachment storage check failed.", {"check": check})


def _directory(path: Path) -> None:
    status = path.lstat()
    if not stat.S_ISDIR(status.st_mode):
        raise _io("directory_type")
    if os.name == "posix" and (status.st_mode & 0o077 or status.st_uid != os.geteuid()):
        raise _io("directory_private")


def _scan(stream: BinaryIO, limit: int, writer: BinaryIO | None = None) -> BodyInfo:
    if type(limit) is not int or not 0 < limit <= MAX_SIZE:
        raise BookflowError("E_VALIDATION", "Attachment limit must be 1 to 100000000 bytes.")
    digest = hashlib.sha256()
    size = 0
    while True:
        requested = min(CHUNK_SIZE, limit - size + 1)
        chunk = stream.read(requested)
        if not isinstance(chunk, bytes) or len(chunk) > requested:
            raise BookflowError("E_VALIDATION", "Attachment stream returned invalid bytes.")
        if not chunk:
            return BodyInfo(digest.hexdigest(), size)
        size += len(chunk)
        if size > limit:
            raise BookflowError("E_VALUE_RANGE", "Attachment exceeds its byte limit.")
        digest.update(chunk)
        if writer is not None:
            writer.write(chunk)


def scan(stream: BinaryIO, limit: int) -> BodyInfo:
    """Hash actual input bytes without creating filesystem entries."""
    try:
        return _scan(stream, limit)
    except OSError:
        raise _io("stream_read") from None


@contextmanager
def stage(store: Path, stream: BinaryIO, limit: int) -> Iterator[StagedAttachment]:
    """Stage bytes privately; remove only this invocation's temporary on exit."""
    path = None
    writer = None
    resource = None
    try:
        store = Path(store).absolute()
        _directory(store)
        fd, name = tempfile.mkstemp(prefix=".attachment-", suffix=".tmp", dir=store)
        path = Path(name)
        try:
            writer = os.fdopen(fd, "w+b")
        except BaseException:
            os.close(fd)
            raise
        info = _scan(stream, limit, writer)
        resource = StagedAttachment(store, path, writer, info, _token=_STAGING_TOKEN)
        yield resource
    except OSError:
        raise _io("stage") from None
    finally:
        unwinding = sys.exception() is not None
        cleanup_error = None
        if resource is not None:
            resource._consumed = True
        try:
            if writer is not None:
                writer.close()
        except OSError as exc:
            cleanup_error = exc
        finally:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    cleanup_error = exc
        if cleanup_error is not None and not unwinding:
            raise _io("stage_cleanup") from None


def _verify(reader: BinaryIO, info: BodyInfo, check: str) -> None:
    status = os.fstat(reader.fileno())
    if not stat.S_ISREG(status.st_mode) or status.st_size != info.size_bytes:
        raise _io(check)
    reader.seek(0)
    digest = hashlib.sha256()
    size = 0
    while size <= info.size_bytes:
        chunk = reader.read(min(CHUNK_SIZE, info.size_bytes - size + 1))
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
    if size != info.size_bytes or digest.hexdigest() != info.sha256:
        raise _io(check)


def _existing(path: Path, info: BodyInfo) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise _io("body_type")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0))
    try:
        reader = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with reader:
        _verify(reader, info, "body_content")
        os.fsync(reader.fileno())


def publish(store: Path, staged: StagedAttachment) -> Publication:
    """Consume staged bytes and durably publish without replacing a digest body.

    A failure can leave a published body; retry with a new staging invocation.
    The caller must retain its ownership and filesystem lease throughout.
    """
    store = Path(store).absolute()
    if (not isinstance(staged, StagedAttachment) or staged._store != store
            or staged._consumed or staged._writer.closed
            or staged._path.parent != store
            or not staged._path.name.startswith(".attachment-")
            or not staged._path.name.endswith(".tmp")):
        raise _io("staged_resource")
    staged._consumed = True
    try:
        _directory(store)
        writer = staged._writer
        try:
            writer.flush()
            _verify(writer, staged.info, "staged_content")
            held = os.fstat(writer.fileno())
            named = staged._path.lstat()
            if (not stat.S_ISREG(named.st_mode)
                    or (held.st_dev, held.st_ino) != staged._identity
                    or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)):
                raise _io("staged_identity")
            os.fsync(writer.fileno())
        finally:
            writer.close()
        shard = store / staged.info.sha256[:2]
        try:
            shard.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _directory(shard)
        body = shard / staged.info.sha256
        try:
            os.link(staged._path, body, follow_symlinks=False)
            created = True
        except FileExistsError:
            _existing(body, staged.info)
            created = False
        sync_directory(shard)
        sync_directory(store)
        return Publication(staged.info.sha256, staged.info.size_bytes, created)
    except OSError:
        raise _io("publication") from None
