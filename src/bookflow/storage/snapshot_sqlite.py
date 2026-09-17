"""Native SQLite handles with a conservative transaction observation lifetime.

This is not a permission decision or a trace callback. Unknown SQL invalidates;
only ordinary SELECT and nested savepoint bookkeeping preserve a live token.
"""
import sqlite3


def _read_or_savepoint(sql):
    if type(sql) is not str:
        return False
    prefix = sql[:1024]
    i, end = 0, len(prefix)
    while i < end:
        if prefix[i].isspace():
            i += 1
        elif prefix.startswith('--', i):
            newline = prefix.find('\n', i + 2)
            if newline < 0:
                return False
            i = newline + 1
        elif prefix.startswith('/*', i):
            close = prefix.find('*/', i + 2)
            if close < 0:
                return False
            i = close + 2
        else:
            break
    start = i
    while i < end and ('a' <= prefix[i] <= 'z' or 'A' <= prefix[i] <= 'Z'):
        i += 1
    if i == end and len(sql) > end:
        return False  # truncated token; conservative invalidation
    return prefix[start:i].upper() in {'SELECT', 'SAVEPOINT', 'RELEASE'}


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
