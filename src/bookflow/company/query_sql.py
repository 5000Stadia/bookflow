"""Bounded SQL compilation cache for the fixed SQLite company query dialect.

Only SQLAlchemy compiled statement structure is reused, never rows, authorization,
query results or a connection. SQLAlchemy extracts each invocation's current binds.
Company connections use new Engine instances with the same pysqlite dialect; its
instance identity otherwise prevents reuse of compiled query structures.
"""
from collections import OrderedDict
from threading import RLock


class _CompiledStatements:
    def __init__(self, capacity=128):
        self.capacity = capacity
        self.entries = OrderedDict()
        self.lock = RLock()

    @staticmethod
    def key(key):
        dialect, *parts = key
        # This cache is used only by the existing company SQLite query providers.
        return (type(dialect), dialect.paramstyle, *parts)

    def get(self, key, default=None):
        key = self.key(key)
        with self.lock:
            value = self.entries.get(key, default)
            if key in self.entries:
                self.entries.move_to_end(key)
            return value

    def __setitem__(self, key, value):
        key = self.key(key)
        with self.lock:
            self.entries[key] = value
            self.entries.move_to_end(key)
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)


_COMPILED = _CompiledStatements()


def execute(connection, statement, parameters=None):
    return connection.execute(statement, parameters or {}, execution_options={'compiled_cache': _COMPILED})
