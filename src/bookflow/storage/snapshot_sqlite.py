"""Native SQLite handles with a conservative transaction observation lifetime.

This is not a permission decision or a trace callback. Unknown SQL invalidates;
only ordinary SELECT and nested savepoint bookkeeping preserve a live token.
"""
import re
import sqlite3


_PREFIX = re.compile(r'\A(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*([A-Za-z]+)', re.S)


def _read_or_savepoint(sql):
    if type(sql) is not str:
        return False
    match = _PREFIX.match(sql[:1024])
    return match is not None and match[1].upper() in {'SELECT', 'SAVEPOINT', 'RELEASE'}


class Cursor(sqlite3.Cursor):
    def execute(self, *args, **kwargs):
        conn = self.connection
        before = conn.in_transaction
        sql = args[0] if args else kwargs.get('sql')
        if not _read_or_savepoint(sql):
            conn._snapshot_epoch += 1
        try:
            return super().execute(*args, **kwargs)
        finally:
            if conn.in_transaction != before:
                conn._snapshot_epoch += 1

    def executemany(self, *args, **kwargs):
        self.connection._snapshot_epoch += 1
        return super().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        self.connection._snapshot_epoch += 1
        return super().executescript(*args, **kwargs)


class Connection(sqlite3.Connection):
    _snapshot_epoch = 0
    _snapshot_tracking = True
    _snapshot_closed = False

    def cursor(self, *args, **kwargs):
        if args or kwargs:
            # Preserve native factory behavior, including its exceptions. Once a
            # caller owns an untracked cursor, this connection cannot cache facts.
            self._snapshot_tracking = False
            self._snapshot_epoch += 1
            return super().cursor(*args, **kwargs)
        return super().cursor(factory=Cursor)

    def execute(self, *args, **kwargs):
        return self.cursor().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self.cursor().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self.cursor().executescript(*args, **kwargs)

    def commit(self, *args, **kwargs):
        self._snapshot_epoch += 1
        return super().commit(*args, **kwargs)

    def rollback(self, *args, **kwargs):
        self._snapshot_epoch += 1
        return super().rollback(*args, **kwargs)

    def __exit__(self, *args):
        self._snapshot_epoch += 1
        return super().__exit__(*args)

    def close(self):
        self._snapshot_closed = True
        self._snapshot_epoch += 1
        return super().close()

    def snapshot_key(self):
        if self._snapshot_closed or not self._snapshot_tracking or not self.in_transaction:
            return None
        return (self, self._snapshot_epoch, self.total_changes)
