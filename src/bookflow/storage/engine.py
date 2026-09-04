"""SQLite opens. Writable opens set WAL and checkpoint on close; read-only opens set query_only."""

from __future__ import annotations

import errno as _errno
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import sqlalchemy as sa

from bookflow.core.errors import BookflowError
from bookflow.core.fs import check_local


def sqlite_uri(path: Path, mode: str) -> str:
    """A file: URI with every reserved character percent-encoded, so `#`, `%`, `?` in names are safe."""
    return f"file:{quote(str(path), safe='/')}?mode={mode}"


def io_error(operation: str, exc: BaseException, path: Path | str | None = None) -> BookflowError:
    """Translate an OS or SQLite failure into E_IO without echoing SQL or values."""
    code = None
    if isinstance(exc, OSError) and exc.errno:
        code = _errno.errorcode.get(exc.errno, str(exc.errno))
    elif isinstance(exc, sqlite3.Error):
        text = str(exc).lower()
        code = ("ENOENT" if "unable to open" in text else "EACCES" if "readonly" in text or "permission" in text
                else "EBUSY" if "locked" in text or "busy" in text else "ENOTDB" if "not a database" in text else "EIO")
    return BookflowError("E_IO", details={"operation": operation, "errno": code or "EIO", "path": str(path) if path else None})


def _connect(path: Path, writable: bool, create: bool) -> sqlite3.Connection:
    if not create and not path.exists():
        raise BookflowError("E_IO", details={"operation": "open", "errno": "ENOENT", "path": str(path)})
    uri = sqlite_uri(path, "rwc" if (writable and create) else "rw" if writable else "ro")
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
    except sqlite3.Error as e:
        raise io_error("open", e, path)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        if writable:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")  # durable across process crashes in WAL mode; one fsync per checkpoint, not per commit
            conn.execute("PRAGMA foreign_keys=ON")
        else:
            conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT count(*) FROM sqlite_master")
    except sqlite3.Error as e:
        conn.close()
        raise io_error("open", e, path)
    return conn


class Database:
    """One open database: an sqlite3 connection wrapped by a SQLAlchemy engine."""

    def __init__(self, path: Path, writable: bool, create: bool = False):
        self.path = path
        self.writable = writable
        self.raw = _connect(path, writable, create)
        self.engine = sa.create_engine("sqlite://", creator=lambda: self.raw, poolclass=sa.pool.StaticPool)
        self.conn = self.engine.connect()

    def close(self) -> None:
        try:
            if self.writable:
                try:
                    self.raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.OperationalError:
                    pass
        finally:
            self.conn.close()
            self.engine.dispose()
            try:
                self.raw.close()
            except sqlite3.ProgrammingError:
                pass


@contextmanager
def open_database(path: Path, writable: bool, create: bool = False) -> Iterator[Database]:
    """Open an existing database; ``create=True`` only where a database is being made (init, rollout)."""
    check_local(path)
    db = Database(path, writable, create)
    try:
        yield db
    finally:
        db.close()
