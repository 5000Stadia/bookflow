"""Frozen hub0009 conversion commits its security audit with authority changes."""

import ast
import importlib
import inspect
import sqlite3
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command

from bookflow.core.audit import decode_snapshot
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from bookflow.hub import schema as h
from bookflow.storage.engine import open_database, sqlite_uri
from bookflow.storage.migrate import HEADS, _config, current_revision_raw, migrate_to_head
from tests.test_migration_chain import _normalized_schema
from tests.test_row6_note_migration import _schema_semantics

MIGRATION = importlib.import_module("bookflow.storage.hub_migrations.versions.0009_identity_authority")
REASON = "migration_requires_authorization"


@pytest.fixture(autouse=True)
def _revision_under_test(monkeypatch):
    monkeypatch.setitem(HEADS, "hub", "hub0009")
    monkeypatch.setitem(HEADS, "company", "co0006")


def _insert(conn, table, values):
    columns = ', '.join(f'"{name}"' for name in values)
    conn.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({", ".join("?" for _ in values)})', tuple(values.values()))


def _common(record_id):
    return dict(id=record_id, version=7, created_at="old-created", created_by="H",
                created_via="cli", updated_at="old-updated", updated_by="H", updated_via="python")


def _populate(conn, *, system=True, agents=True):
    identities = [("H", "human", True)]
    if system:
        identities.append(("S", "system", True))
    if agents:
        identities.extend((("A", "agent", True), ("B", "agent", False), ("C", "agent", True)))
    for user_id, kind, active in identities:
        _insert(conn, "users", {**_common(user_id), "kind": kind, "username": user_id,
                "display_name": "private name " + user_id, "owner_user_id": "H" if kind == "agent" else None,
                "password_hash": "private-password-hash" if kind == "human" else None,
                "hub_admin": kind == "human", "active": active})
    tokens = [("TH", "H", "bearer", None, None), ("SH", "H", "session", None, None)]
    if agents:
        tokens.extend((("TA", "A", "bearer", None, None), ("SA", "A", "session", None, None),
                       ("EX", "B", "bearer", None, "2000-01-01T00:00:00Z"),
                       ("RV", "A", "bearer", "old-revocation", None)))
    for token_id, user_id, kind, revoked, expires in tokens:
        _insert(conn, "api_tokens", {**_common(token_id), "user_id": user_id,
                "on_behalf_of": None if user_id == "H" else "H", "kind": kind,
                "token_hash": "private-token-hash-" + token_id, "label": "private-label",
                "revoked_at": revoked, "expires_at": expires, "last_used_at": "old-used"})
    _insert(conn, "memberships", dict(id="M", user_id="H", scope_type="organization",
            scope_id="O", role="owner", granted_by="H", granted_at="old", revoked_at=None))
    _insert(conn, "pending_config", dict(id=1, token="PC", request_id="PR", contents="private-config"))
    _insert(conn, "audit_events", dict(id="OLD", seq=41, at="old", command="init", actor_id="H",
            actor_kind="human", interface="cli", client_name="old-client", client_version="old-version",
            client_host="old-host", session_id="old-session", request_id="old-request", summary="old-summary"))
    _insert(conn, "audit_entries", dict(id="OLDENTRY", event_id="OLD", record_type="user", record_id="H",
            action="create", version_before=None, version_after=7,
            before=b'\x00{"old": "before"}', after=b'\x00{"old": "after"}'))


def _make(path, revision="hub0008", populate=True, **kwargs):
    with open_database(path, writable=True, create=True) as db:
        command.upgrade(_config("hub", db.conn), revision)
        if populate:
            _populate(db.raw, **kwargs)


def _rows(conn, table):
    return conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def _dump(conn):
    return {name: _rows(conn, name) for (name,) in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )}


def _security_events(conn):
    return conn.execute("SELECT * FROM audit_events WHERE reason=?", (REASON,)).fetchall()


@pytest.mark.parametrize("system", [True, False])
def test_legacy_conversion_preserves_data_backup_and_safe_audit(tmp_path, system):
    path = tmp_path / "hub.db"
    _make(path, system=system)
    with sqlite3.connect(path) as conn:
        original = _dump(conn)
        original_schema = _normalized_schema(conn)
    backups = tmp_path / "backups"
    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, "hub", backups) == ("hub0008", "hub0009")
        after = _dump(db.raw)
        for table in original.keys() - {"api_tokens", "audit_events", "audit_entries", "alembic_version"}:
            assert after[table] == original[table]
        assert after["agent_principals"] == []
        authority = after["agent_authority"]
        assert [row[0] for row in authority] == ["A", "B", "C"]
        at = authority[0][2]
        assert at and all(row[1:] == (1, at, REASON) for row in authority)
        event = db.conn.execute(sa.select(h.audit_events).where(h.audit_events.c.reason == REASON)).mappings().one()
        assert (event["seq"], event["actor_kind"], event["actor_id"], event["interface"], event["at"]) == (
            42, "system", "S" if system else None, "system", at)
        assert event["on_behalf_of"] is None
        assert db.raw.execute("SELECT * FROM audit_events WHERE id='OLD'").fetchall() == original["audit_events"]
        assert db.raw.execute("SELECT * FROM audit_entries WHERE id='OLDENTRY'").fetchall() == original["audit_entries"]
        entries = db.conn.execute(sa.select(h.audit_entries).where(h.audit_entries.c.event_id == event["id"])).mappings().all()
        assert {(e["record_type"], e["record_id"]) for e in entries} == {
            ("agent_authority", name) for name in ("A", "B", "C")
        } | {("api_token", name) for name in ("TA", "SA", "EX")}
        assert len(entries) == 6
        for entry in entries:
            assert entry["action"] == "migrate"
            before, snapshot = decode_snapshot(entry["before"]), decode_snapshot(entry["after"])
            for blob in (entry["before"], entry["after"]):
                if blob:
                    assert b"private" not in blob and b"hash" not in blob and b"label" not in blob
            if entry["record_type"] == "agent_authority":
                assert before is None and entry["version_before"] is None and entry["version_after"] is None
                assert snapshot == dict(agent_user_id=entry["record_id"], epoch=1, suspended_at=at, suspension_reason=REASON)
            else:
                assert (entry["version_before"], entry["version_after"]) == (7, 8)
                assert before["revoked_at"] is None and before["version"] == 7
                assert snapshot == {**before, "revoked_at": at, "version": 8, "updated_at": at,
                                    "updated_by": "S" if system else "system", "updated_via": "system"}
                stored = db.conn.execute(sa.select(h.api_tokens).where(h.api_tokens.c.id == entry["record_id"])).mappings().one()
                assert snapshot == {key: stored[key] for key in snapshot}
        for old in original["api_tokens"]:
            new = next(row for row in after["api_tokens"] if row[0] == old[0])
            assert new[-1] is None  # No credential gains an issuance epoch.
            if old[0] in {"TH", "SH", "RV"}:
                assert new[:-1] == old
            else:
                # Only version, update provenance and revocation change.
                assert tuple(v for i, v in enumerate(new[:-1]) if i not in {1, 5, 6, 7, 15}) == tuple(
                    v for i, v in enumerate(old) if i not in {1, 5, 6, 7, 15})
        assert db.raw.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrate_to_head(db, "hub", backups) == ("hub0009", "hub0009")
        assert _dump(db.raw) == after
    saved = list(backups.glob("hub-*-from-hub0008.db"))
    assert len(saved) == 1 and current_revision_raw(saved[0]) == "hub0008"
    with sqlite3.connect(sqlite_uri(saved[0], "ro"), uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert _normalized_schema(conn) == original_schema
        assert _dump(conn) == original


def test_fresh_upgrade_and_legacy_identity_columns_agree(tmp_path):
    fresh, upgraded = tmp_path / "fresh.db", tmp_path / "upgraded.db"
    _make(fresh, "hub0009", populate=False)
    _make(upgraded)
    with open_database(upgraded, writable=True) as db:
        migrate_to_head(db, "hub", None)
    assert _schema_semantics(fresh) == _schema_semantics(upgraded)
    with open_database(fresh, writable=True) as db:
        assert _security_events(db.raw) == []
        for table in (h.api_tokens, h.agent_principals, h.agent_authority):
            reflected = sa.Table(table.name, sa.MetaData(), autoload_with=db.conn)
            # This fixture deliberately ends at hub0009. Later inert additive
            # columns do not belong in its frozen physical-schema oracle.
            legacy = ("agent_user_id", "epoch", "suspended_at", "suspension_reason") if table is h.agent_authority else tuple(table.c.keys())
            assert [(c.name, str(c.type), c.nullable, c.primary_key) for c in reflected.c] == [
                (table.c[name].name, str(table.c[name].type), table.c[name].nullable, table.c[name].primary_key) for name in legacy]
            assert {(fk.parent.name, fk.target_fullname) for fk in reflected.foreign_keys} == {
                (fk.parent.name, fk.target_fullname) for fk in table.foreign_keys}
        assert list(h.agent_principals.c.keys()) == ["agent_user_id", "principal_user_id", "assigned_by", "assigned_at", "revoked_at"]
        assert [column["name"] for column in sa.inspect(db.conn).get_columns("agent_authority")] == ["agent_user_id", "epoch", "suspended_at", "suspension_reason"]
        inspector = sa.inspect(db.conn)
        assert inspector.get_pk_constraint("agent_principals")["constrained_columns"] == ["agent_user_id", "principal_user_id"]
        assert inspector.get_pk_constraint("agent_authority")["constrained_columns"] == ["agent_user_id"]
        assert inspector.get_check_constraints("agent_authority") == [{"sqltext": "epoch >= 1", "name": "ck_agent_authority_epoch"}]
        assert [(i["name"], i["column_names"], i["unique"]) for i in inspector.get_indexes("agent_principals")] == [
            ("ix_agent_principals_principal", ["principal_user_id", "agent_user_id"], 0)]
        _populate(db.raw, agents=False)
        assert _security_events(db.raw) == []
        for epoch in (0, -1, None):
            with pytest.raises(sa.exc.IntegrityError):
                db.conn.execute(h.agent_authority.insert().values(agent_user_id="H", epoch=epoch))
        with pytest.raises(sa.exc.IntegrityError):
            db.conn.execute(h.agent_authority.insert().values(agent_user_id="missing", epoch=1))
        db.conn.execute(h.agent_authority.insert().values(agent_user_id="H", epoch=1))
        with pytest.raises(sa.exc.IntegrityError):
            db.conn.execute(h.agent_authority.insert().values(agent_user_id="H", epoch=2))
        assignment = dict(agent_user_id="H", principal_user_id="H", assigned_by="H", assigned_at="t")
        for field in ("agent_user_id", "principal_user_id", "assigned_by"):
            with pytest.raises(sa.exc.IntegrityError):
                db.conn.execute(h.agent_principals.insert().values(**{**assignment, field: "missing"}))
        db.conn.execute(h.agent_principals.insert().values(**assignment))
        with pytest.raises(sa.exc.IntegrityError):
            db.conn.execute(h.agent_principals.insert().values(**assignment))


def test_human_only_upgrade_has_no_conversion_event(tmp_path):
    path = tmp_path / "hub.db"
    _make(path, agents=False)
    with open_database(path, writable=True) as db:
        original = _dump(db.raw)
        migrate_to_head(db, "hub", None)
        assert _security_events(db.raw) == []
        assert _rows(db.raw, "agent_authority") == []
        assert _rows(db.raw, "api_tokens") == [(*row, None) for row in original["api_tokens"]]
        assert _rows(db.raw, "audit_events") == original["audit_events"]


@pytest.mark.parametrize("failure_table", ["audit_events", "audit_entries"])
@pytest.mark.parametrize("with_backup", [False, True])
def test_audit_failure_rolls_back_ddl_conversion_and_history(tmp_path, failure_table, with_backup):
    path = tmp_path / "hub.db"
    _make(path)
    backups = tmp_path / "backups" if with_backup else None
    with open_database(path, writable=True) as db:
        # Fail token entries after authority and token updates have occurred.
        condition = "NEW.reason = 'migration_requires_authorization'" if failure_table == "audit_events" else "NEW.record_type = 'api_token'"
        db.raw.execute(f"CREATE TRIGGER fail_security_audit BEFORE INSERT ON {failure_table} WHEN {condition} BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END")
        original, schema = _dump(db.raw), _normalized_schema(db.raw)
        with pytest.raises(BookflowError) as error:
            migrate_to_head(db, "hub", backups)
        assert error.value.code == "E_MIGRATION_FAILED"
        assert _dump(db.raw) == original
        assert _normalized_schema(db.raw) == schema
        assert not db.raw.in_transaction
        assert db.raw.execute("PRAGMA foreign_keys").fetchone() == (1,)
        db.raw.execute("DROP TRIGGER fail_security_audit")
        assert migrate_to_head(db, "hub", backups) == ("hub0008", "hub0009")
        assert len(_security_events(db.raw)) == 1
    if with_backup:
        assert len(list(backups.glob("hub-*-from-hub0008.db"))) == 1


def test_interruption_before_ordinary_report_keeps_one_committed_security_event(tmp_path, monkeypatch):
    from bookflow.core import dispatch

    path = tmp_path / "hub.db"
    _make(path)
    reports = []

    def interrupt(*args):
        reports.append(args[-2:])
        raise KeyboardInterrupt("after conversion commit, before ordinary report")

    monkeypatch.setattr(dispatch, "_record_migration", interrupt)
    ctx = Context.new(Interface.python, "row7-migration-test")
    with open_database(path, writable=True) as db:
        session = SimpleNamespace(hub=db, data_root=tmp_path, hub_migrated=None)
        with pytest.raises(KeyboardInterrupt):
            dispatch._migrate_hub(session, ctx)
        assert not db.raw.in_transaction
        committed = _dump(db.raw)
    assert current_revision_raw(path) == "hub0009"
    with open_database(path, writable=True) as db:
        assert len(_security_events(db.raw)) == 1
        assert db.raw.execute("SELECT count(*) FROM audit_entries WHERE event_id != 'OLD'").fetchone() == (6,)
        assert db.raw.execute("SELECT count(*) FROM api_tokens WHERE user_id IN ('A','B') AND revoked_at IS NULL").fetchone() == (0,)
        dispatch._migrate_hub(SimpleNamespace(hub=db, data_root=tmp_path, hub_migrated=None), ctx)
        assert _dump(db.raw) == committed
        assert reports == [("hub0008", "hub0009")]


def test_revision_is_frozen_against_live_metadata_changes(tmp_path, monkeypatch):
    tree = ast.parse(inspect.getsource(MIGRATION))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert set(imports) <= {"json", "datetime", "sqlalchemy", "alembic", "ulid"}
    normal, changed = tmp_path / "normal.db", tmp_path / "changed.db"
    _make(normal, "hub0009", populate=False)
    future = sa.MetaData()
    sa.Table("future_table", future, sa.Column("future", sa.Integer))
    monkeypatch.setattr(h, "metadata", future)
    for name in ("api_tokens", "agent_authority", "agent_principals", "audit_events", "audit_entries"):
        monkeypatch.setattr(h, name, None)
    _make(changed)
    with open_database(changed, writable=True) as db:
        migrate_to_head(db, "hub", None)
        assert len(_security_events(db.raw)) == 1
    assert _schema_semantics(normal) == _schema_semantics(changed)
    assert HEADS == {"hub": "hub0009", "company": "co0006"}
