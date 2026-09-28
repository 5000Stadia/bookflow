"""co0060 adds the early-payment discount records and the two default discount accounts.

Two new immutable tables and two nullable company_info columns; nothing existing is rebuilt.
The questions: is the frozen DDL what the shipped metadata declares, does a populated co0059
database come out with every existing object and row exactly as it went in, does a second run
change nothing, does a reserved name stop it, and do the new guards refuse what they exist for.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import discount_schema, schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions

M = importlib.import_module("bookflow.storage.company_migrations.versions.0060_early_payment_discounts")
PREVIOUS = "co0059"
TABLES = ("payment_discounts", "bill_payment_discounts")


def _at(path, revision):
    from alembic import command
    from bookflow.storage.migrate import _config
    with open_database(path, writable=True, create=True) as db:
        db.raw.execute("PRAGMA foreign_keys=OFF")
        try:
            command.upgrade(_config("company", db.conn), revision)
            db.raw.commit()
        finally:
            db.raw.execute("PRAGMA foreign_keys=ON")


def _objects(conn):
    return {name: sql for name, sql in conn.execute(
        "SELECT name, sql FROM sqlite_schema WHERE sql IS NOT NULL")}


def test_the_migration_follows_negative_stock():
    assert M.down_revision == PREVIOUS
    assert M.revision in known_revisions("company")
    assert M.revision <= HEADS["company"]


def test_the_frozen_ddl_is_what_the_shipped_metadata_declares():
    assert M.DDL == tuple(str(CreateTable(schema.metadata.tables[name]).compile(dialect=dialect()))
                          for name in TABLES)
    assert set(M.INDEXES) == {str(CreateIndex(index).compile(dialect=dialect()))
                              for name in TABLES for index in schema.metadata.tables[name].indexes}
    assert M.GUARDS == tuple(discount_schema.guard_statements())
    shipped = {index.name for index in schema.metadata.tables['company_info'].indexes}
    assert {'ix_company_info_customer_discount_account_id', 'ix_company_info_vendor_discount_account_id'} <= shipped


def test_a_populated_previous_revision_upgrades_with_its_rows_and_neighbours_untouched(tmp_path):
    path = tmp_path / "populated.db"
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE local_note (id TEXT PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO local_note VALUES ('1', 'kept verbatim')")
        conn.commit()
        before = _objects(conn)
        tables = [name for name, in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        rows = {name: conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
                for name in tables if name != 'alembic_version'}
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        after = _objects(conn)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        new = set(TABLES) | {index.split()[2] for index in M.INDEXES} | {
            guard.split()[2] for guard in M.GUARDS} | {
            'ix_company_info_customer_discount_account_id', 'ix_company_info_vendor_discount_account_id'}
        assert set(after) - set(before) == new
        assert {k: v for k, v in after.items() if k in before and k != 'company_info'} == \
            {k: v for k, v in before.items() if k != 'company_info'}
        for name, content in rows.items():
            columns = [row[1] for row in sqlite3.connect(path).execute(f'PRAGMA table_info("{name}")')]
            if name == 'company_info':
                kept = [c for c in columns if not c.endswith('_discount_account_id')]
                assert conn.execute(f'SELECT {",".join(kept)} FROM company_info ORDER BY rowid').fetchall() == content
            else:
                assert conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() == content
        assert conn.execute("SELECT body FROM local_note").fetchone() == ("kept verbatim",)


def test_reapplying_the_head_changes_nothing(tmp_path):
    path = tmp_path / "fresh.db"
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        before = _objects(conn)
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        assert _objects(conn) == before


def test_a_reserved_name_already_in_use_stops_the_migration(tmp_path):
    path = tmp_path / "reserved.db"
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE payment_discounts (id INTEGER)")
        conn.commit()
    with pytest.raises(Exception) as caught:
        _at(path, M.revision)
    assert "co0060" in str(caught.value) or "co0060" in str(getattr(caught.value, "__cause__", ""))


def test_discount_history_is_immutable_and_bound_to_its_edge(tmp_path):
    path = tmp_path / "guards.db"
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        objects = _objects(conn)
        for table in TABLES:
            for event in ('update', 'delete'):
                assert f'{table}_immutable_{event}' in objects
        for clause in ("a.kind = 'apply'", "a.amount_minor_units > NEW.amount_minor_units"):
            assert clause in objects['payment_discounts_edge']
            assert clause in objects['bill_payment_discounts_edge']
        conn.execute("PRAGMA foreign_keys=OFF")
        with pytest.raises(sqlite3.IntegrityError, match='discount must be part of its own receipt settlement'):
            conn.execute("INSERT INTO payment_discounts VALUES ('D','P','K','A','I','X',100,'USD',0,NULL,NULL,'t','u','cli','E')")
