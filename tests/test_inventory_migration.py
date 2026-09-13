"""co0037 adds the inventory ledger to an existing company without disturbing anything.

The migration is purely additive, so the questions worth asking are: does its frozen DDL
still say what the shipped metadata says, does a populated database at the previous revision
come out the other side with every table, index, trigger and row exactly as it went in, and
do the guards it installs actually refuse what they claim to.
"""
import importlib
import re
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema
from bookflow.core.errors import BookflowError
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head

M = importlib.import_module("bookflow.storage.company_migrations.versions.0037_inventory")
VERSIONS = Path(__file__).resolve().parents[1] / "src/bookflow/storage/company_migrations/versions"

# The revision this one is written to follow. Read from the migration, not repeated: the one
# thing that has ever gone wrong with a migration number here is two copies of it disagreeing.
PREVIOUS = M.down_revision


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
    chain = _chain()
    assert M.revision == "co0037" and chain[M.revision] == PREVIOUS
    # This migration's number is a position in the chain, not the end of it. Asserting it is
    # the head was true on the day it landed and false from the next migration onward, which is
    # a fact about the calendar rather than about this file.
    assert M.revision in chain and chain[M.revision] == PREVIOUS
    reachable, cursor = set(), HEADS["company"]
    while cursor:
        reachable.add(cursor)
        cursor = chain.get(cursor)
    assert M.revision in reachable, (
        M.revision, "the head no longer reaches this migration, so the chain has forked")
    # A revision number is a name in several places inside its own file -- the assignment,
    # the down link and every refusal message -- and a half-finished renumber shows up as one
    # of them naming a revision that is not this one.
    source = (VERSIONS / "0037_inventory.py").read_text()
    assert set(re.findall(r"co0\d{3}", source)) == {M.revision, PREVIOUS}
    assert f"revision = '{M.revision}'" in source and f"down_revision = '{PREVIOUS}'" in source


def test_the_frozen_ddl_is_what_the_shipped_metadata_declares():
    expected = []
    for name in M.NEW_TABLES:
        table = schema.metadata.tables[name]
        expected.append(str(CreateTable(table).compile(dialect=dialect())).strip())
        expected.extend(str(CreateIndex(index).compile(dialect=dialect())).strip()
                        for index in sorted(table.indexes, key=lambda index: index.name))
    assert list(M.DDL) == expected
    assert set(M.OBJECTS) == {name for name in M.NEW_TABLES} | {
        index.name for name in M.NEW_TABLES for index in schema.metadata.tables[name].indexes} | {
        statement.split()[2] for statement in M.GUARDS}


def test_a_fresh_database_reaches_the_head_and_reapplying_changes_nothing(tmp_path):
    with open_database(tmp_path / "fresh.db", writable=True, create=True) as db:
        assert migrate_to_head(db, "company", None) == (None, HEADS["company"])
        assert db.raw.execute("PRAGMA main.foreign_key_check").fetchall() == []
        assert db.raw.execute("PRAGMA main.integrity_check").fetchall() == [("ok",)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, "company", None) == (HEADS["company"], HEADS["company"])
        assert list(db.raw.iterdump()) == before


def test_a_populated_previous_revision_upgrades_with_everything_else_untouched(tmp_path):
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
        rows = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
    # Upgrade to THIS migration, not to the head. "Additive" is a claim about what this file
    # does to its predecessor; running the whole chain measures every later migration too, and
    # a later one legitimately replaced a trigger when statement charges became settleable.
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        after = _objects(conn)
        assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == rows
        assert conn.execute("SELECT body FROM local_note").fetchone() == ("kept verbatim",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # Additive means additive: every object that was there is there, byte for byte.
        assert {name: sql for name, sql in after.items() if name in before} == before
        assert set(after) - set(before) == set(M.OBJECTS)


def test_the_migration_refuses_to_run_over_a_name_it_would_own(tmp_path):
    path = tmp_path / "collision.db"
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE inventory_movements (id TEXT PRIMARY KEY)")
        conn.commit()
    with pytest.raises((RuntimeError, BookflowError)):
        with open_database(path, writable=True) as db:
            migrate_to_head(db, "company", None)


def test_the_guards_refuse_an_unmatched_or_mutated_movement(tmp_path):
    """A movement has to be the posting line it names, and it may never be rewritten."""
    with open_database(tmp_path / "guards.db", writable=True, create=True) as db:
        migrate_to_head(db, "company", None)
        conn = db.raw
        conn.execute("PRAGMA foreign_keys=OFF")
        row = dict(id="01ARZ3NDEKTSV4RRFFQ69G5FAV", created_at="2026-01-01T00:00:00Z",
                   created_by="01ARZ3NDEKTSV4RRFFQ69G5FAV", created_via="cli",
                   audit_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", item_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                   transaction_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", revision_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                   posting_batch_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", posting_line_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                   document_line_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", effective_date="2026-01-01",
                   sequence=1, kind="receipt", quantity_microunits=1, value_minor_units=1,
                   currency="USD", asset_account_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                   offset_account_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", class_id=None,
                   corrects_movement_id=None, reverses_movement_id=None)
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        with pytest.raises(sqlite3.IntegrityError, match="does not match its posting line"):
            conn.execute(f"INSERT INTO inventory_movements ({columns}) VALUES ({placeholders})",
                         tuple(row.values()))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO inventory_documents (transaction_id, type, kind, created_at, "
                         "created_by, created_via, audit_event_id) VALUES "
                         "('01ARZ3NDEKTSV4RRFFQ69G5FAV', 'invoice', 'adjustment', 'x', 'y', 'cli', 'z')")
        # With the matching guard out of the way the row lands, and the immutability guards
        # are what keep it as written: a movement is history, and history is not edited.
        conn.execute("DROP TRIGGER inventory_movements_match_posting")
        conn.execute(f"INSERT INTO inventory_movements ({columns}) VALUES ({placeholders})",
                     tuple(row.values()))
        for statement in ("UPDATE inventory_movements SET value_minor_units = 2",
                          "DELETE FROM inventory_movements"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable inventory movement"):
                conn.execute(statement)
