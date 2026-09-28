"""co0059 rebuilds the inventory ledger so a sale may be provisional and a receipt may true it up.

Two nullable columns and one guard. The questions are the ones every rebuild has to answer:
does a populated database come out with every row and every unrelated object exactly as it
went in, is the frozen table text the one the shipped metadata declares, does a second run
change nothing, and does the new guard refuse what it exists to refuse.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable

from bookflow.company import schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions
from tests.test_bill_payment_migration import _rebuilt_since

M = importlib.import_module("bookflow.storage.company_migrations.versions.0059_negative_stock")
PREVIOUS = "co0058"
TABLE = "inventory_movements"


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


def test_the_migration_follows_the_refund_revision():
    """Derived from the chain, never a second copy of the number."""
    assert M.down_revision == PREVIOUS
    assert M.revision in known_revisions("company")
    if HEADS["company"] != M.revision:
        assert M.revision < HEADS["company"]


def test_the_frozen_table_is_what_the_shipped_metadata_declares(tmp_path):
    header = "CREATE TABLE " + M.TEMP + " ("
    if TABLE in _rebuilt_since(M.revision):
        # A later revision owns today's metadata; hold the literal to this revision's own table.
        from tests.test_bill_payment_migration import _at as stopped_at
        stopped_at(tmp_path / "own.db", M.revision)
        with sqlite3.connect(tmp_path / "own.db") as conn:
            stored = conn.execute("SELECT sql FROM sqlite_schema WHERE name=?", (TABLE,)).fetchone()[0]
        assert M.DDL.strip().replace(header, "", 1) == stored.split("(", 1)[1]
        return
    expected = str(CreateTable(schema.metadata.tables[TABLE]).compile(dialect=dialect()))
    assert M.DDL == expected.replace(
        "CREATE TABLE " + TABLE + " (", "CREATE TABLE " + M.TEMP + " (", 1)
    indexes = {index.name for index in schema.metadata.tables[TABLE].indexes}
    assert "ix_inventory_movements_filled_by" in indexes


def test_a_populated_previous_revision_upgrades_with_its_rows_and_neighbours_untouched(tmp_path):
    path = tmp_path / "populated.db"
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TABLE local_note (id TEXT PRIMARY KEY, body TEXT)")
        conn.execute("CREATE TRIGGER local_note_guard BEFORE DELETE ON local_note "
                     "BEGIN SELECT RAISE(ABORT, 'local'); END")
        conn.execute("INSERT INTO local_note VALUES ('1', 'kept verbatim')")
        conn.commit()
        before = _objects(conn)
        assert "filled_by_movement_id" not in before[TABLE]

    _at(path, M.revision)

    with sqlite3.connect(path) as conn:
        after = _objects(conn)
        assert conn.execute("SELECT body FROM local_note").fetchone() == ("kept verbatim",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        owned = {TABLE, "ix_inventory_movements_filled_by", "inventory_movements_true_up_link"}
        assert {k: v for k, v in after.items() if k in before and k not in owned} \
            == {k: v for k, v in before.items() if k not in owned}
        assert set(after) - set(before) == {"ix_inventory_movements_filled_by",
                                            "inventory_movements_true_up_link"}
        assert set(before) - set(after) == set()
        assert "filled_by_movement_id" in after[TABLE]
        assert "fallback_unit_cost_minor_units" in after[TABLE]
        assert after["inventory_movements_true_up_link"] == M.GUARDS[0]
        assert M.TEMP not in after


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
        conn.execute("CREATE TABLE ix_inventory_movements_filled_by (id INTEGER)")
        conn.commit()
    with pytest.raises(Exception) as caught:
        _at(path, M.revision)
    assert "co0059" in str(caught.value) or "co0059" in str(getattr(caught.value, "__cause__", ""))


def test_the_true_up_guard_holds_the_receipt_date_and_the_issue(tmp_path):
    """Checked as text: the rows it guards need a posted company behind them, and the
    end-to-end tests in test_negative_stock write real true-ups through it."""
    path = tmp_path / "guard.db"
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        sql = _objects(conn)["inventory_movements_true_up_link"]
    for clause in ("r.kind='receipt'", "r.effective_date=NEW.effective_date",
                   "r.item_id=NEW.item_id", "i.kind='issue'", "i.item_id=NEW.item_id",
                   "i.asset_account_id=NEW.asset_account_id",
                   "i.offset_account_id=NEW.offset_account_id", "i.class_id IS NEW.class_id"):
        assert clause in sql
