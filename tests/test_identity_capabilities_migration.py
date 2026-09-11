"""Hub0013 seeds the identity capabilities the command registry already declares.

`user add|list|set-password` and `membership grant|revoke|list` shipped without a seed, so
`role_capabilities` sat six rows behind the registry. A root in legacy mode never reads the
table, so nothing failed; the first root to leave legacy mode would have denied every role -
owners included - the commands that administer users and memberships.
"""

import importlib
import inspect
import sqlite3

import pytest
from alembic import command

from bookflow.storage.engine import open_database, sqlite_uri
from bookflow.storage.migrate import HEADS, _config, current_revision_raw, migrate_to_head
from tests.test_migration_chain import (
    CURRENT_ROLE_CAPABILITY_SEED,
    _make_revision,
    _normalized_schema,
    _registry_role_capability_projection,
    _role_capability_modules,
)

HUB0013 = importlib.import_module("bookflow.storage.hub_migrations.versions.0013_identity_capabilities")

QUERY = "SELECT role,capability,required_role FROM main.role_capabilities ORDER BY role,capability,required_role"
CUSTOM = ("owner", "custom-extension", "owner")


def _rows(conn):
    return tuple(conn.execute(QUERY))


def _previous_seed():
    """What the chain seeds without hub0013 - derived, so a later seed cannot go stale here."""
    return tuple(sorted(
        row
        for module in _role_capability_modules()
        if module is not HUB0013
        for row in module.ROLE_CAPABILITY_SEED
    ))


def test_seed_is_exactly_the_registry_gap_and_nothing_else():
    assert HEADS["hub"] == "hub0013" == HUB0013.revision and HUB0013.down_revision == "hub0012"
    assert "bookflow.core.registry" not in inspect.getsource(HUB0013)
    previous, projection = set(_previous_seed()), set(_registry_role_capability_projection())
    assert set(HUB0013.ROLE_CAPABILITY_SEED) == projection - previous
    assert previous - projection == set()
    assert len(HUB0013.ROLE_CAPABILITY_SEED) == len(set(HUB0013.ROLE_CAPABILITY_SEED)) == 6
    # The lockout this closes: every role administering memberships, hub admins administering users.
    assert set(HUB0013.ROLE_CAPABILITY_SEED) == {
        (role, "membership", "authenticated")
        for role in ("readonly", "standard", "admin", "owner", "hub_admin")
    } | {("hub_admin", "user", "hub_admin")}


def test_populated_hub0012_upgrade_seeds_identity_rows_and_preserves_everything_else(tmp_path):
    path = tmp_path / "hub.db"
    _make_revision(path, "hub", "hub0012", lambda _conn: None)
    assert current_revision_raw(path) == "hub0012"

    backups = tmp_path / "backups"
    with open_database(path, writable=True) as db:
        db.raw.execute("INSERT INTO main.role_capabilities VALUES (?,?,?)", CUSTOM)
        before_rows, before_schema = _rows(db.raw), _normalized_schema(db.raw)
        assert before_rows == tuple(sorted((*_previous_seed(), CUSTOM)))

        assert migrate_to_head(db, "hub", backups) == ("hub0012", "hub0013")
        assert _normalized_schema(db.raw) == before_schema
        # Every pre-existing row survives verbatim, including the locally added one.
        assert set(before_rows) <= set(_rows(db.raw))
        assert _rows(db.raw) == tuple(sorted((*before_rows, *HUB0013.ROLE_CAPABILITY_SEED)))
        assert tuple(row for row in _rows(db.raw) if row != CUSTOM) == CURRENT_ROLE_CAPABILITY_SEED
        assert db.raw.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.raw.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    saved = list(backups.glob("hub-*-from-hub0012.db"))
    assert len(saved) == 1
    with sqlite3.connect(sqlite_uri(saved[0], "ro"), uri=True) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "hub0012"
        assert _rows(conn) == before_rows


def test_repeated_upgrade_and_a_root_already_carrying_a_seed_row_both_succeed(tmp_path):
    """The insert's own idempotence, not alembic's: the primary key is the whole row."""
    for label, preset in (("none", ()), ("one", HUB0013.ROLE_CAPABILITY_SEED[:1]),
                          ("all", HUB0013.ROLE_CAPABILITY_SEED)):
        path = tmp_path / f"hub-{label}.db"
        _make_revision(path, "hub", "hub0012", lambda _conn: None)
        with open_database(path, writable=True) as db:
            for row in preset:
                db.raw.execute("INSERT INTO main.role_capabilities VALUES (?,?,?)", row)
            assert migrate_to_head(db, "hub", None) == ("hub0012", "hub0013")
            once = _rows(db.raw)
            assert once == CURRENT_ROLE_CAPABILITY_SEED

            # Re-run the migration itself against the database it already seeded.
            command.stamp(_config("hub", db.conn), "hub0012", purge=True)
            command.upgrade(_config("hub", db.conn), "hub0013")
            assert _rows(db.raw) == once
            assert db.raw.execute("SELECT count(*) FROM main.role_capabilities").fetchone() == (len(once),)

            # And through the ordinary runner a second time, which must find nothing to do.
            assert migrate_to_head(db, "hub", None) == ("hub0013", "hub0013")
            assert _rows(db.raw) == once


def test_a_local_table_of_the_same_name_takes_neither_the_read_nor_the_insert(tmp_path):
    """SQLite resolves an unqualified name to TEMP first; both halves are bound to main."""
    path = tmp_path / "hub.db"
    _make_revision(path, "hub", "hub0012", lambda _conn: None)
    with open_database(path, writable=True) as db:
        db.raw.execute("CREATE TEMP TABLE role_capabilities (role TEXT, capability TEXT, required_role TEXT)")
        for row in HUB0013.ROLE_CAPABILITY_SEED:
            db.raw.execute("INSERT INTO temp.role_capabilities VALUES (?,?,?)", row)
        local = tuple(db.raw.execute("SELECT role,capability,required_role FROM temp.role_capabilities"))

        assert migrate_to_head(db, "hub", None) == ("hub0012", "hub0013")
        assert _rows(db.raw) == CURRENT_ROLE_CAPABILITY_SEED
        assert tuple(db.raw.execute("SELECT role,capability,required_role FROM temp.role_capabilities")) == local


def test_fresh_chain_matches_an_upgraded_root_and_downgrade_is_refused(tmp_path):
    fresh, upgraded = tmp_path / "fresh.db", tmp_path / "upgraded.db"
    _make_revision(fresh, "hub", "hub0013", lambda _conn: None)
    _make_revision(upgraded, "hub", "hub0012", lambda _conn: None)
    with open_database(upgraded, writable=True) as db:
        assert migrate_to_head(db, "hub", None) == ("hub0012", "hub0013")
    with sqlite3.connect(fresh) as left, sqlite3.connect(upgraded) as right:
        assert _normalized_schema(left) == _normalized_schema(right)
        assert _rows(left) == _rows(right) == CURRENT_ROLE_CAPABILITY_SEED
    with pytest.raises(NotImplementedError):
        HUB0013.downgrade()
