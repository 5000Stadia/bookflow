"""SQLite opens. Writable opens set WAL and checkpoint on close; read-only opens set query_only."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import sqlalchemy as sa

from bookflow.core.errors import BookflowError
from bookflow.core.fs import check_local


def _connect(path: Path, writable: bool) -> sqlite3.Connection:
    uri = f"file:{path}?mode={'rwc' if writable else 'ro'}"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
    except sqlite3.OperationalError as e:
        raise BookflowError("E_IO", details={"operation": "open", "errno": "EIO", "path": str(path), "problem": str(e)})
    conn.execute("PRAGMA busy_timeout=5000")
    if writable:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    else:
        conn.execute("PRAGMA query_only=ON")
    return conn


class Database:
    """One open database: an sqlite3 connection wrapped by a SQLAlchemy engine."""

    def __init__(self, path: Path, writable: bool):
        self.path = path
        self.writable = writable
        self.raw = _connect(path, writable)
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
def open_database(path: Path, writable: bool) -> Iterator[Database]:
    check_local(path)
    db = Database(path, writable)
    try:
        yield db
    finally:
        db.close()
