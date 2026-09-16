"""co0057 rebuilds the inventory ledger so a return can name the issue it gives back.

This one is not additive in the way most are: it rebuilds ``inventory_movements`` to add a
column with a foreign key and a check, and it widens the guard that decides which receipts may
carry a cost correction. So the questions worth asking are whether a populated database comes
out the other side with every row and every unrelated object exactly as it went in, whether the
table it rebuilt says what the shipped metadata says, and whether the guards it puts back are
the ones it meant to.
"""
import importlib
import sqlite3
from pathlib import Path

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable

from bookflow.company import schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS

M = importlib.import_module("bookflow.storage.company_migrations.versions.0057_return_recosting")
VERSIONS = Path(__file__).resolve().parents[1] / "src/bookflow/storage/company_migrations/versions"
PREVIOUS = M.down_revision
TABLE = "inventory_movements"


def _chain():
    out = {}
    for path in sorted(VERSIONS.glob("[0-9]*.py")):
        module = importlib.import_module("bookflow.storage.company_migrations.versions." + path.stem)
        out[module.revision] = module.down_revision
    return out


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


def test_the_number_this_migration_claims_is_the_one_the_chain_gives_it():
    """And that the head every new company is built to is this one, which is what ships it.

    A migration nobody's head points at runs for no one: the column would exist in the
    metadata and not in any database, and every write naming it would fail.
    """
    chain = _chain()
    assert M.revision == "co0057" and chain[M.revision] == PREVIOUS
    assert HEADS["company"] == M.revision


def test_the_rebuilt_table_is_what_the_shipped_metadata_declares():
    """The literal in the migration and the table in the code are one table, not two."""
    expected = str(CreateTable(schema.metadata.tables[TABLE]).compile(dialect=dialect()))
    assert M.DDL == expected.replace(
        "CREATE TABLE " + TABLE + " (", "CREATE TABLE " + M.TEMP + " (", 1)


def test_a_populated_previous_revision_upgrades_with_its_rows_and_neighbours_untouched(tmp_path):
    """Everything that was there is there: the rows, and every object this does not own.

    The rebuild copies rows into a new table and renames it over the old one, which is the one
    operation in this migration that could silently lose data, so what is measured is the rows
    themselves and an unrelated local table and trigger that must come through byte for byte.
    """
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
        movements = conn.execute("SELECT count(*) FROM " + TABLE).fetchone()[0]
        transactions = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
        assert "returns_movement_id" not in before[TABLE]

    _at(path, M.revision)

    with sqlite3.connect(path) as conn:
        after = _objects(conn)
        assert conn.execute("SELECT count(*) FROM " + TABLE).fetchone()[0] == movements
        assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == transactions
        assert conn.execute("SELECT body FROM local_note").fetchone() == ("kept verbatim",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # The table it owns is rebuilt and the guard it owns is widened. Everything else --
        # every other table, index, view and trigger in the database -- is byte for byte what
        # it was, which is the whole claim a rebuild has to earn.
        owned = {TABLE, "receipt_cost_correction_dimensions", "ix_inventory_movements_returns"}
        assert {k: v for k, v in after.items() if k in before and k not in owned} \
            == {k: v for k, v in before.items() if k not in owned}
        assert set(after) - set(before) == {"ix_inventory_movements_returns"}
        assert set(before) - set(after) == set()
        assert "returns_movement_id" in after[TABLE]
        assert after["receipt_cost_correction_dimensions"] == M.GUARD
        # The temporary table it built the new one in is gone, not left behind.
        assert M.TEMP not in after


def test_reapplying_the_head_changes_nothing(tmp_path):
    """A database already at the head is left exactly alone, so a retry is harmless."""
    path = tmp_path / "fresh.db"
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        before = _objects(conn)
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        assert _objects(conn) == before


def test_the_widened_guard_still_refuses_a_correction_that_moved_its_dimensions(tmp_path):
    """Widening it admitted returns; it must not have admitted anything else.

    The guard is what stops a cost correction being filed against a receipt it does not
    describe. Checked as text rather than by writing rows, because the rows it guards need a
    whole posted company behind them and the thing at risk in this change is the predicate.
    """
    path = tmp_path / "guard.db"
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        sql = _objects(conn)["receipt_cost_correction_dimensions"]
    # Every dimension the original compared is still compared.
    for dimension in ("m.item_id=NEW.item_id", "m.effective_date=NEW.effective_date",
                      "m.asset_account_id=NEW.asset_account_id",
                      "m.offset_account_id=NEW.offset_account_id",
                      "m.currency=NEW.currency", "m.class_id IS NEW.class_id"):
        assert dimension in sql
    # And the only thing it no longer insists on is that the receipt came from a bill.
    assert "m.returns_movement_id IS NOT NULL OR EXISTS" in sql
    assert "item_receipt_lines" in sql
