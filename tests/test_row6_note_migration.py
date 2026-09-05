"""Notes migrations preserve older data and add only their declared storage."""

import importlib
import inspect
import json
import sqlite3

import pytest
import sqlalchemy as sa
from alembic import command

from bookflow.company.schema import notes
from bookflow.storage.engine import open_database, sqlite_uri
from bookflow.storage.migrate import HEADS, _config, current_revision_raw, migrate_to_head
from tests.test_migration_chain import (
    CURRENT_ROLE_CAPABILITY_SEED,
    HUB0005,
    HUB0007,
    _make_revision,
    _normalized_schema,
)


def _populate_principal(conn):
    conn.execute(
        "INSERT INTO principals VALUES ('U1','keeper','Bookkeeper','human','t','t')"
    )


def _note_values(kind="comment"):
    return {
        "id": "N1", "version": 1,
        "created_at": "t", "created_by": "U1", "created_via": "python",
        "updated_at": "t", "updated_by": "U1", "updated_via": "python",
        "record_type": "principal", "record_id": "U1", "body": "Preserved é\n",
        "author_id": "U1", "interface": "python", "at": "t", "edited_at": None,
        "kind": kind,
    }


def _schema_semantics(path):
    # SQLite batch reflection can reorder constraints in co0004's CREATE TABLE.
    # Compare their meaning while retaining the order of actual table columns.
    with open_database(path, writable=False) as db:
        inspector = sa.inspect(db.conn)
        return {
            name: {
                "columns": [
                    {**column, "type": str(column["type"])}
                    for column in inspector.get_columns(name)
                ],
                "primary_key": inspector.get_pk_constraint(name),
                **{
                    label: sorted(json.dumps(value, sort_keys=True, default=str) for value in reader(name))
                    for label, reader in (
                        ("checks", inspector.get_check_constraints),
                        ("unique", inspector.get_unique_constraints),
                        ("foreign_keys", inspector.get_foreign_keys),
                        ("indexes", inspector.get_indexes),
                    )
                },
            }
            for name in inspector.get_table_names()
        }


def test_company_notes_fresh_upgrade_data_and_verified_backup(tmp_path, monkeypatch):
    monkeypatch.setitem(HEADS, "company", "co0005")
    fresh, upgraded = tmp_path / "fresh.db", tmp_path / "upgraded.db"
    _make_revision(fresh, "company", HEADS["company"], _populate_principal)
    _make_revision(upgraded, "company", "co0004", _populate_principal)
    with sqlite3.connect(upgraded) as conn:
        original_schema = _normalized_schema(conn)
        original_principals = conn.execute("SELECT * FROM principals").fetchall()

    backups = tmp_path / "backups"
    with open_database(upgraded, writable=True) as db:
        assert migrate_to_head(db, "company", backups) == ("co0004", "co0005")
        assert db.raw.execute("SELECT * FROM principals").fetchall() == original_principals
        assert [entry for entry in _normalized_schema(db.raw) if entry["table"] != "notes"] == original_schema
        assert db.raw.execute("PRAGMA foreign_key_check").fetchall() == []
        db.conn.execute(notes.insert().values(**_note_values()))
        assert migrate_to_head(db, "company", backups) == ("co0005", "co0005")

    with open_database(fresh, writable=True) as db:
        db.conn.execute(notes.insert().values(**_note_values()))
    assert _schema_semantics(fresh) == _schema_semantics(upgraded)
    with sqlite3.connect(fresh) as left, sqlite3.connect(upgraded) as right:
        assert left.execute("SELECT * FROM notes").fetchall() == right.execute("SELECT * FROM notes").fetchall()
        assert right.execute("SELECT body, version, edited_at FROM notes").fetchone() == ("Preserved é\n", 1, None)

    saved = list(backups.glob("*-from-co0004.db"))
    assert len(saved) == 1
    assert current_revision_raw(saved[0]) == "co0004"
    with sqlite3.connect(sqlite_uri(saved[0], "ro"), uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert _normalized_schema(conn) == original_schema
        assert conn.execute("SELECT * FROM principals").fetchall() == original_principals


def test_notes_storage_matches_live_schema_and_rejects_unknown_kind(tmp_path):
    path = tmp_path / "company.db"
    _make_revision(path, "company", "co0005", _populate_principal)
    with open_database(path, writable=True) as db:
        reflected = sa.Table("notes", sa.MetaData(), autoload_with=db.conn)
        assert [(c.name, str(c.type), c.nullable, c.primary_key) for c in reflected.c] == [
            (c.name, str(c.type), c.nullable, c.primary_key) for c in notes.c
        ]
        assert {(index.name, tuple(c.name for c in index.columns)) for index in reflected.indexes} == {
            ("ix_notes_target_id", ("record_type", "record_id", "id")),
        }
        checks = sa.inspect(db.conn).get_check_constraints("notes")
        assert checks == [{"sqltext": "kind IN ('comment', 'system')", "name": "ck_notes_kind"}]
        for invalid in ("", "COMMENT", "attachment", "system-extra"):
            with pytest.raises(sa.exc.IntegrityError):
                db.conn.execute(notes.insert().values(**_note_values(invalid)))
        for index, kind in enumerate(("comment", "system")):
            db.conn.execute(notes.insert().values(**{**_note_values(kind), "id": f"N{index}"}))
        assert db.raw.execute("SELECT count(*) FROM notes").fetchone() == (2,)
        plan = db.raw.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM notes WHERE record_type=? AND record_id=? AND id<? ORDER BY id DESC LIMIT 51",
            ("principal", "U1", "Z"),
        ).fetchall()
        assert any("ix_notes_target_id" in row[3] for row in plan)


def test_notes_migration_downgrade_is_additive_and_frozen(tmp_path):
    migration = importlib.import_module("bookflow.storage.company_migrations.versions.0005_notes")
    assert "bookflow.company" not in inspect.getsource(migration)
    path = tmp_path / "company.db"
    _make_revision(path, "company", "co0004", _populate_principal)
    with open_database(path, writable=True) as db:
        before = _normalized_schema(db.raw)
        command.upgrade(_config("company", db.conn), "co0005")
        db.conn.execute(notes.insert().values(**_note_values()))
        command.downgrade(_config("company", db.conn), "co0004")
        assert _normalized_schema(db.raw) == before
        assert db.raw.execute("SELECT display_name FROM principals").fetchone() == ("Bookkeeper",)


def test_hub_note_capabilities_preserve_historical_seed_and_custom_rows(tmp_path, monkeypatch):
    monkeypatch.setitem(HEADS, "hub", "hub0007")
    assert "bookflow.core.registry" not in inspect.getsource(HUB0007)
    path = tmp_path / "hub.db"
    _make_revision(path, "hub", "hub0005", lambda _conn: None)
    custom = ("owner", "custom-extension", "owner")
    with open_database(path, writable=True) as db:
        query = "SELECT role,capability,required_role FROM role_capabilities ORDER BY role,capability,required_role"
        assert tuple(db.raw.execute(query)) == HUB0005.ROLE_CAPABILITY_SEED
        command.upgrade(_config("hub", db.conn), "hub0006")
        db.raw.execute("INSERT INTO role_capabilities VALUES (?,?,?)", custom)
        before = _normalized_schema(db.raw)
        assert migrate_to_head(db, "hub", tmp_path / "backups") == ("hub0006", "hub0007")
        assert _normalized_schema(db.raw) == before
        assert tuple(db.raw.execute(query)) == tuple(sorted((*HUB0005.ROLE_CAPABILITY_SEED, *HUB0007.ROLE_CAPABILITY_SEED, custom)))
        assert {row for row in db.raw.execute(query) if row[1] == "note"} == {
            (role, "note", required)
            for role in ("readonly", "standard", "admin", "owner", "hub_admin")
            for required in ("member", "standard")
            if role != "readonly" or required == "member"
        }
        command.downgrade(_config("hub", db.conn), "hub0006")
        assert tuple(db.raw.execute(query)) == tuple(sorted((*HUB0005.ROLE_CAPABILITY_SEED, custom)))

    saved = list((tmp_path / "backups").glob("hub-*-from-hub0006.db"))
    assert len(saved) == 1
    with sqlite3.connect(sqlite_uri(saved[0], "ro"), uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert current_revision_raw(saved[0]) == "hub0006"
        assert tuple(conn.execute(query)) == tuple(sorted((*HUB0005.ROLE_CAPABILITY_SEED, custom)))
