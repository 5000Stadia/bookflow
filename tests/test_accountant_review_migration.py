"""co0088 adds the accountant review tables and one index, and nothing else (R121).

Its frozen DDL is what the metadata declares, a fresh database reaches the head twice without change,
and the tables refuse what the rules forbid: an unknown side or status, an owner copy marked working,
a second copy out at once, the same change imported twice.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.test_estimate_void_migration import _chain

M = importlib.import_module('bookflow.storage.company_migrations.versions.0088_accountant_review')
TABLES = M.NEW_OBJECTS[:-1]


def test_the_frozen_ddl_is_what_the_metadata_declares():
    assert M.revision == 'co0088' and M.revision in _chain() and HEADS['company'] == 'co0088'
    assert M.DDL == tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=sqlite.dialect())) for name in TABLES)
    assert M.INDEXES == tuple(str(CreateIndex(i).compile(dialect=sqlite.dialect()))
                              for name in TABLES for i in sorted(c.metadata.tables[name].indexes, key=lambda i: i.name))


def test_a_fresh_database_reaches_the_head_and_the_tables_hold_their_rules(tmp_path):
    path = tmp_path / 'fresh.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before, 'reaching the head twice must change nothing'
    common = "1, 'now', 'U', 'cli', 'now', 'U', 'cli'"
    with sqlite3.connect(path) as raw:
        def copy(identity, side, status):
            raw.execute(f"INSERT INTO accountant_copies VALUES (?, {common}, ?, ?, '2026-09-30', 'C', ?, NULL, NULL, NULL,"
                        " NULL, NULL, NULL)", (identity, side, status, '0' * 64))

        copy('K1', 'owner', 'returned')
        copy('K2', 'owner', 'out')
        for identity, side, status in (('K3', 'owner', 'out'), ('K4', 'owner', 'working'), ('K5', 'accountant', 'out'),
                                       ('K6', 'nobody', 'out')):
            with pytest.raises(sqlite3.IntegrityError):
                copy(identity, side, status)
        raw.execute(f"INSERT INTO accountant_imports VALUES ('I1', {common}, 'K2', 'key-1', 'journal', NULL, NULL, 'A', 'Ann')")
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(f"INSERT INTO accountant_imports VALUES ('I2', {common}, 'K2', 'key-1', 'journal', NULL, NULL, 'A', 'Ann')")
