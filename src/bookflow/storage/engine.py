"""Durable WAL writers and read-only handles with a stable transaction snapshot."""

from __future__ import annotations

import errno as _errno
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import sqlalchemy as sa

from bookflow.core.errors import BookflowError
from bookflow.core.fs import check_local
from bookflow.core import performance
from bookflow.storage.snapshot_sqlite import Connection as SnapshotConnection


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


_CONSTRAINT_TRIGGER = getattr(sqlite3, "SQLITE_CONSTRAINT_TRIGGER", 1811)


def guard_refusal_text(exc: BaseException) -> str | None:
    """The message of one of Bookflow's own guard triggers, when that is what refused a write.

    Every trigger in the company and hub schemas is ours, and SQLite reports a trigger's
    RAISE(ABORT, '...') with the extended code SQLITE_CONSTRAINT_TRIGGER and the trigger's
    literal message, never SQL or values. A UNIQUE, CHECK, NOT NULL or foreign-key violation
    carries a different extended code and is not a guard refusal.
    """
    inner = getattr(exc, "orig", None) if isinstance(exc, sa.exc.DBAPIError) else exc
    if isinstance(inner, sqlite3.IntegrityError) and getattr(inner, "sqlite_errorcode", None) == _CONSTRAINT_TRIGGER:
        return str(inner)
    return None


def guard_tables(conn: sqlite3.Connection, message: str) -> list[str]:
    """The tables whose triggers raise this exact guard message (a message may be shared)."""
    literal = "'" + message.replace("'", "''") + "'"
    rows = conn.execute("SELECT DISTINCT tbl_name FROM sqlite_master WHERE type = 'trigger' AND instr(sql, ?) > 0 "
                        "ORDER BY tbl_name", (literal,)).fetchall()
    return [row[0] for row in rows]


def guard_refused(message: str, details: dict | None = None) -> BookflowError:
    """E_GUARD_REFUSED: a schema guard refused a write the application's own checks let through."""
    return BookflowError("E_GUARD_REFUSED",
                         message=f"{BookflowError('E_GUARD_REFUSED').message} The check said: {message}.",
                         details={"guard": message, **(details or {})})


def _connect(path: Path, writable: bool, create: bool) -> sqlite3.Connection:
    if not create and not path.exists():
        raise BookflowError("E_IO", details={"operation": "open", "errno": "ENOENT", "path": str(path)})
    uri = sqlite_uri(path, "rwc" if (writable and create) else "rw" if writable else "ro")
    try:
        if performance.enabled():
            from bookflow.storage.traced_sqlite import Connection, category
            conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None, factory=Connection)
            conn.trace_database = category(path)
        else:
            conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None, factory=SnapshotConnection)
    except sqlite3.Error as e:
        raise io_error("open", e, path)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        if writable:
            journal = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            if (journal != "wal" or conn.execute("PRAGMA synchronous").fetchone()[0] != 2
                    or conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1):
                raise sqlite3.OperationalError("required database guarantees are unavailable")
        else:
            conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT count(*) FROM sqlite_master")
    except sqlite3.Error as e:
        conn.close()
        raise io_error("open", e, path)
    return conn


# R180: one SQLAlchemy engine for the process. The compiled-statement cache belongs to an engine
# (its keys hold the engine's dialect), so an engine per Database compiled every statement afresh
# in every command: about a hundred compiles per invoice post however warm the process. The
# shared engine owns no connections (NullPool): each Database still opens and owns its own
# sqlite3 connection, handed to the engine's creator only while that Database connects.
_ENGINE: sa.Engine | None = None
_ENGINE_LOCK = threading.Lock()
_OPENING = threading.local()
STATEMENT_CACHE_SIZE = 1500


def _opening_connection():
    raw = getattr(_OPENING, "raw", None)
    if raw is None:
        raise RuntimeError("the shared engine connects only while a Database opens")
    return raw


def _engine() -> sa.Engine:
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = sa.create_engine("sqlite://", creator=_opening_connection, poolclass=sa.pool.NullPool,
                                           query_cache_size=STATEMENT_CACHE_SIZE)
    return _ENGINE


class Database:
    """One open database: an sqlite3 connection wrapped by a SQLAlchemy connection."""

    @performance.measured("db.open")
    def __init__(self, path: Path, writable: bool, create: bool = False):
        self.path = path
        self.writable = writable
        self.raw = _connect(path, writable, create)
        self._closed = False
        self.engine = None
        self.conn = None
        try:
            self.engine = _engine()
            _OPENING.raw = self.raw
            try:
                self.conn = self.engine.connect()
            finally:
                _OPENING.raw = None
            if not writable:
                # SQLAlchemy's logical autobegin does not BEGIN sqlite3 in
                # autocommit mode. Begin after its connection initialization.
                self.raw.execute("BEGIN")
        except BaseException:
            try:
                self.close()
            except Exception:
                # Pool initialization may already have invalidated the raw
                # connection. Preserve the original opening failure.
                pass
            raise

    def authority_snapshot_key(self):
        """Exact tracked live transaction, or None when reuse cannot be proven."""
        if self._closed or not isinstance(self.raw, SnapshotConnection):
            return None
        return self.raw.snapshot_key()

    @property
    def write_transaction(self) -> bool:
        """A writable transaction, distinct from a pinned read snapshot."""
        return self.writable and self.raw.in_transaction

    @performance.measured("db.close")
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.raw.rollback()
            if self.writable:
                try:
                    self.raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.OperationalError:
                    pass
        finally:
            try:
                if self.conn is not None:
                    self.conn.close()  # NullPool: resets and closes the raw connection
            finally:
                self.raw.close()


@contextmanager
def open_database(path: Path, writable: bool, create: bool = False) -> Iterator[Database]:
    """Open an existing database; ``create=True`` only where a database is being made (init, rollout)."""
    check_local(path)
    db = Database(path, writable, create)
    try:
        yield db
    finally:
        db.close()
