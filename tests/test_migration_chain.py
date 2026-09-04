"""The shipped migration chains upgrade populated first-revision databases (a fresh root never exercises this)."""

import sqlite3
from pathlib import Path

import bookflow
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, current_revision_raw, migrate_to_head


def _make_first_revision(path: Path, chain: str, populate) -> None:
    from alembic import command
    from bookflow.storage.migrate import _config
    with open_database(path, writable=True, create=True) as db:
        command.upgrade(_config(chain, db.conn), f"{'hub' if chain == 'hub' else 'co'}0001")
        populate(db.raw)


def test_populated_hub_and_company_upgrade(tmp_path):
    hub = tmp_path / "hub.db"
    def pop_hub(conn):
        conn.execute("INSERT INTO users (id, version, created_at, created_by, created_via, updated_at, updated_by, updated_via, kind, username, display_name, owner_user_id, password_hash, hub_admin, timezone, active) VALUES ('U1',1,'t','U1','cli','t','U1','cli','human','k','k',NULL,NULL,1,NULL,1)")
        conn.execute("INSERT INTO audit_events (id, at, command, actor_id, actor_kind, on_behalf_of, interface, client_name, client_version, client_host, session_id, request_id, idempotency_key, reason, directive_id, source_ref, summary) VALUES ('E1','t','init','U1','human',NULL,'cli','c','0','h','S','R',NULL,NULL,NULL,NULL,'init')")
        conn.execute("INSERT INTO audit_entries (id, event_id, record_type, record_id, action, version_before, version_after, after, before) VALUES ('N1','E1','user','U1','create',NULL,1,X'00',NULL)")
    _make_first_revision(hub, "hub", pop_hub)
    assert current_revision_raw(hub) == "hub0001"
    with open_database(hub, writable=True) as db:
        assert migrate_to_head(db, "hub", tmp_path / "backups") == ("hub0001", HEADS["hub"])
        assert db.raw.execute("SELECT seq FROM audit_events WHERE id='E1'").fetchone()[0] == 1
        assert db.raw.execute("SELECT count(*) FROM audit_entries").fetchone()[0] == 1
        assert db.raw.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.raw.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert len(list((tmp_path / "backups").glob("hub-*-from-hub0001.db"))) == 1
    co = tmp_path / "company.db"
    def pop_co(conn):
        conn.execute("INSERT INTO company_info (id, version, created_at, created_by, created_via, updated_at, updated_by, updated_via, legal_name, display_name, tax_id_kind, entity_type, income_tax_form, fiscal_year_start_month, tax_year_start_month, report_basis, home_currency, timezone, recent_activity_window_seconds) VALUES ('C1',1,'t','U1','cli','t','U1','cli','L','D','ein','other','other',1,1,'accrual','USD','UTC',60)")
        conn.execute("INSERT INTO principals (user_id, username, display_name, kind, first_seen_at, last_seen_at) VALUES ('U1','k','k','human','t','t')")
    _make_first_revision(co, "company", pop_co)
    with open_database(co, writable=True) as db:
        assert migrate_to_head(db, "company", tmp_path / "cbackups") == ("co0001", HEADS["company"])
        names = {r[0] for r in db.raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"audit_events", "directives", "presence", "idempotency_keys", "sequences"} <= names


def _downgrade_copy(src: Path, chain: str, tables: dict[str, list[str]]) -> None:
    """Rebuild ``src`` at the first revision with the rows of the given tables (columns that existed then)."""
    from alembic import command
    from bookflow.storage.migrate import _config
    tmp = src.with_suffix(".old")
    with open_database(tmp, writable=True, create=True) as db:
        command.upgrade(_config(chain, db.conn), f"{'hub' if chain == 'hub' else 'co'}0001")
        db.raw.execute(f"ATTACH DATABASE '{src}' AS cur")
        for table, cols in tables.items():
            collist = ", ".join(cols)
            db.raw.execute(f"INSERT INTO {table} ({collist}) SELECT {collist} FROM cur.{table}")
        db.raw.execute("DETACH DATABASE cur")
    src.unlink()
    for side in ("-wal", "-shm"):
        p = src.with_name(src.name + side)
        if p.exists():
            p.unlink()
    tmp.rename(src)


def test_old_data_root_upgrades_end_to_end(tmp_path, monkeypatch):
    """A data root whose databases sit at the first revisions, with real rows, upgrades through ordinary commands."""
    root = tmp_path / "old"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    c = bookflow.connect(data_root=str(root)); c.init(); c.organization.new(name="Old Org")
    made = c.company.new(legal_name="Old Co", home_currency="USD", organization="Old Org", timezone="UTC")
    common = ["id", "version", "created_at", "created_by", "created_via", "updated_at", "updated_by", "updated_via"]
    _downgrade_copy(root / "hub.db", "hub", {
        "users": common + ["kind", "username", "display_name", "owner_user_id", "password_hash", "hub_admin", "timezone", "active"],
        "organizations": common + ["display_name", "name_key", "path", "pending_path", "is_demo"],
        "companies": common + ["organization_id", "display_name", "name_key", "path", "pending_path", "legal_name", "home_currency", "schema_revision", "is_demo"],
        "memberships": ["id", "user_id", "scope_type", "scope_id", "role", "granted_by", "granted_at", "revoked_at"],
        "audit_events": ["id", "at", "command", "actor_id", "actor_kind", "on_behalf_of", "interface", "client_name", "client_version", "client_host", "session_id", "request_id", "idempotency_key", "reason", "directive_id", "source_ref", "summary"],
        "audit_entries": ["id", "event_id", "record_type", "record_id", "action", "version_before", "version_after", "after", "before"],
    })
    co = Path(made["path"]) / "company.db"
    _downgrade_copy(co, "company", {
        "company_info": [c_ for c_ in sqlite3.connect(str(co)).execute("PRAGMA table_info(company_info)").fetchall() and [r[1] for r in sqlite3.connect(str(co)).execute("PRAGMA table_info(company_info)").fetchall()]],
        "principals": ["user_id", "username", "display_name", "kind", "first_seen_at", "last_seen_at"],
    })
    conn = sqlite3.connect(str(root / "hub.db")); conn.execute("UPDATE companies SET schema_revision='co0001'"); conn.commit(); conn.close()
    assert current_revision_raw(root / "hub.db") == "hub0001" and current_revision_raw(co) == "co0001"
    c = bookflow.connect(data_root=str(root))
    out = c.upgrade()
    assert out["hub_migrated"] is True and len(out["companies_migrated"]) == 1 and out["companies_failed"] == []
    assert c.company.show(company="Old Org/Old Co")["schema_revision"] == HEADS["company"]
    ev = c.audit.list(company="Old Org/Old Co")["items"]
    assert any(e["command"] == "upgrade" for e in ev)
    assert c.company.update(phone="1", expected_version=1, company="Old Org/Old Co")["version"] == 2, "the baseline entry lets versioned writes work on an upgraded company"
    assert len(list((root / "backups").glob("hub-*-from-hub0001.db"))) == 1
