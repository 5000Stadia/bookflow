"""Per-connection SQLite timing with fixed labels and no statement retention."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from bookflow.core import performance


_TOKEN = re.compile(r"\A([A-Za-z_]+)")
_CHECKPOINT = re.compile(r"\A\s+(?:(?:main|temp)\s*\.\s*)?wal_checkpoint\b", re.IGNORECASE)


def category(path: Path) -> str:
    """Return only the established file category, never a name or path."""
    return {"hub.db": "hub", "company.db": "company"}.get(path.name, "other")


def _phase(sql: object) -> str:
    # Avoid invoking caller-defined string methods, and bound parsing/allocation.
    if type(sql) is not str:
        return "sql.execute"
    prefix = sql[:256]
    while True:
        prefix = prefix.lstrip()
        if prefix.startswith("--"):
            end = prefix.find("\n", 2)
            if end < 0:
                return "sql.execute"
            prefix = prefix[end + 1:]
        elif prefix.startswith("/*"):
            end = prefix.find("*/", 2)
            if end < 0:
                return "sql.execute"
            prefix = prefix[end + 2:]
        else:
            break
    match = _TOKEN.match(prefix)
    if match is None:
        return "sql.execute"
    word = match[1].upper()
    if word in {"BEGIN", "COMMIT", "END", "ROLLBACK"}:
        return {"BEGIN": "sql.begin", "COMMIT": "sql.commit", "END": "sql.commit",
                "ROLLBACK": "sql.rollback"}[word]
    if word == "PRAGMA" and _CHECKPOINT.match(prefix[match.end():]):
        return "sql.checkpoint"
    return "sql.execute"


class Cursor(sqlite3.Cursor):
    """A native cursor retaining SQLite's rows, defaults and iteration behavior."""

    def execute(self, *args, **kwargs):
        if not performance.enabled():
            return super().execute(*args, **kwargs)
        with performance.span(_phase(args[0] if args else kwargs.get("sql")),
                              database=self.connection.trace_database):
            return super().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        if not performance.enabled():
            return super().executemany(*args, **kwargs)
        with performance.span(_phase(args[0] if args else kwargs.get("sql")),
                              database=self.connection.trace_database):
            return super().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        if not performance.enabled():
            return super().executescript(*args, **kwargs)
        # A script is one API boundary; it may contain several transaction commands.
        with performance.span("sql.execute", database=self.connection.trace_database):
            return super().executescript(*args, **kwargs)

    def fetchone(self, *args, **kwargs):
        if not performance.enabled():
            return super().fetchone(*args, **kwargs)
        with performance.span("sql.fetch", database=self.connection.trace_database):
            return super().fetchone(*args, **kwargs)

    def fetchmany(self, *args, **kwargs):
        if not performance.enabled():
            return super().fetchmany(*args, **kwargs)
        with performance.span("sql.fetch", database=self.connection.trace_database):
            return super().fetchmany(*args, **kwargs)

    def fetchall(self, *args, **kwargs):
        if not performance.enabled():
            return super().fetchall(*args, **kwargs)
        with performance.span("sql.fetch", database=self.connection.trace_database):
            return super().fetchall(*args, **kwargs)

    def __next__(self):
        if not performance.enabled():
            return super().__next__()
        with performance.span("sql.fetch", database=self.connection.trace_database):
            try:
                return super().__next__()
            except StopIteration:
                # End of a result set is a successful fetch boundary.
                pass
        raise StopIteration


class Connection(sqlite3.Connection):
    """Native connection using traced cursors only when no factory was supplied."""

    trace_database = "other"

    def cursor(self, *args, **kwargs):
        if args or kwargs:
            return super().cursor(*args, **kwargs)
        return super().cursor(factory=Cursor)

    def execute(self, *args, **kwargs):
        return self.cursor().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self.cursor().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self.cursor().executescript(*args, **kwargs)

    def commit(self, *args, **kwargs):
        if not performance.enabled():
            return super().commit(*args, **kwargs)
        with performance.span("sql.commit", database=self.trace_database):
            return super().commit(*args, **kwargs)

    def rollback(self, *args, **kwargs):
        if not performance.enabled():
            return super().rollback(*args, **kwargs)
        with performance.span("sql.rollback", database=self.trace_database):
            return super().rollback(*args, **kwargs)

    def __exit__(self, *args):
        if not performance.enabled():
            return super().__exit__(*args)
        phase = "sql.commit" if args and args[0] is None else "sql.rollback"
        with performance.span(phase, database=self.trace_database):
            return super().__exit__(*args)
