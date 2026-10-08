"""Table aliases built once.

`table.alias(name)` builds a new selectable, and the first read of its `.c` builds a proxy for
every column. A statement that is issued once per invoice of a receipt applied to hundreds of
them spent a quarter of its time doing that again. An alias is immutable, so the same
(table, name) pair is the same object everywhere.
"""
from functools import cache


@cache
def alias(table, name):
    return table.alias(name)
