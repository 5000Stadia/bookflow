"""co0086 adds the price-rule tables, and nothing else (R110).

Its frozen DDL is what the metadata declares, a fresh database reaches the head twice without change,
and the tables refuse what the rules forbid: a blank name, a value and breaks together, a second
preference row.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable

from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.test_estimate_void_migration import _chain

M = importlib.import_module('bookflow.storage.company_migrations.versions.0086_pricing_rules')


def test_the_frozen_ddl_is_what_the_metadata_declares():
    assert M.revision == 'co0086' and M.revision in _chain()
    assert M.DDL == tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=sqlite.dialect()))
                          for name in M.NEW_OBJECTS)


def test_a_fresh_database_reaches_the_head_and_the_tables_hold_their_rules(tmp_path):
    path = tmp_path / 'fresh.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before, 'reaching the head twice must change nothing'
    common = "1, 'now', 'U', 'cli', 'now', 'U', 'cli'"
    with sqlite3.connect(path) as raw:
        def rule(identity, name, value, breaks):
            raw.execute(f"INSERT INTO price_rules VALUES (?, {common}, ?, ?, NULL, 1, 1, NULL, NULL, NULL, '{{}}',"
                        " 'percent', 'down', 'sales_price', ?, ?, 'cent')", (identity, name, name.lower(), value, breaks))

        rule('R1', 'Ten off', 10_000_000, None)
        rule('R2', 'Breaks', None, '[]')
        for identity, name, value, breaks in (('R3', '  ', 1, None), ('R4', 'Both', 1, '[]'), ('R5', 'Neither', None, None)):
            with pytest.raises(sqlite3.IntegrityError):
                rule(identity, name, value, breaks)
        raw.execute(f"INSERT INTO price_rule_preferences VALUES ('P1', {common}, 1, 'priority')")
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(f"INSERT INTO price_rule_preferences VALUES ('P2', {common}, 1, 'lowest_price')")
