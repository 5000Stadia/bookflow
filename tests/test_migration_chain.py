"""The shipped migration chains upgrade populated older databases (a fresh root never exercises this)."""

import ast
import importlib
import inspect
import json
import sqlite3
from pathlib import Path

import bookflow
from bookflow.hub import schema as hub_schema
from bookflow.storage.engine import open_database, sqlite_uri
from bookflow.storage.migrate import HEADS, current_revision_raw, migrate_to_head

HUB0003 = importlib.import_module("bookflow.storage.hub_migrations.versions.0003_capabilities_features")
HUB0004 = importlib.import_module("bookflow.storage.hub_migrations.versions.0004_row5_capabilities")
HUB0005 = importlib.import_module("bookflow.storage.hub_migrations.versions.0005_row5_complete_capabilities")
HUB0007 = importlib.import_module("bookflow.storage.hub_migrations.versions.0007_note_capabilities")
HUB0008 = importlib.import_module("bookflow.storage.hub_migrations.versions.0008_attachment_capabilities")
CURRENT_ROLE_CAPABILITY_SEED = tuple(sorted(HUB0005.ROLE_CAPABILITY_SEED + HUB0007.ROLE_CAPABILITY_SEED + HUB0008.ROLE_CAPABILITY_SEED))
CO0002 = importlib.import_module("bookflow.storage.company_migrations.versions.0002_contract")
FIXTURES = Path(__file__).parent / "fixtures"


def _make_revision(path: Path, chain: str, revision: str, populate) -> None:
    from alembic import command
    from bookflow.storage.migrate import _config
    with open_database(path, writable=True, create=True) as db:
        command.upgrade(_config(chain, db.conn), revision)
        populate(db.raw)


def _make_first_revision(path: Path, chain: str, populate) -> None:
    _make_revision(path, chain, f"{'hub' if chain == 'hub' else 'co'}0001", populate)


def _registry_role_capability_projection() -> tuple[tuple[str, str, str], ...]:
    from bookflow.core.registry import all_commands, load_all

    load_all()
    role_rank = {"readonly": 0, "standard": 1, "admin": 2, "owner": 3, "hub_admin": 4}
    required_rank = {"authenticated": 0, "member": 0, "standard": 1, "admin": 2, "owner": 3, "hub_admin": 4}
    requirements = {(command.capability, command.required_role or "authenticated") for command in all_commands()}
    return tuple(sorted(
        (role, capability, required_role)
        for capability, required_role in requirements
        for role, rank in role_rank.items()
        if rank >= required_rank[required_role]
    ))


def _pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in sorted(conn.execute(f"PRAGMA table_info({table})"), key=lambda row: row[5]) if row[5]]


def _normalized_schema(conn: sqlite3.Connection) -> list[dict[str, str | None]]:
    return [
        {
            "type": object_type,
            "name": name,
            "table": table_name,
            "sql": " ".join(sql.split()) if sql else None,
        }
        for object_type, name, table_name, sql in conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    ]


def _assigned_literal(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"migration source has no {name} assignment")


def _live_schema_imports(tree: ast.Module, chain: str) -> list[str]:
    module = f"bookflow.{chain}.schema"
    parent = f"bookflow.{chain}"
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name == module)
        elif isinstance(node, ast.ImportFrom):
            if node.module == module:
                found.append(module)
            elif node.module == parent and any(alias.name == "schema" for alias in node.names):
                found.append(module)
    return found


def test_company_co0002_is_frozen_revision_local_ddl(tmp_path):
    assert "bookflow.company.schema" not in inspect.getsource(CO0002)
    path = tmp_path / "co0002.db"
    _make_revision(path, "company", "co0002", lambda _conn: None)
    with sqlite3.connect(path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        audit_columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_events)")}
    assert {"audit_events", "audit_entries", "presence", "idempotency_keys", "directives", "sequences"} <= tables
    assert "undo_of_event_id" not in audit_columns


def test_company_co0002_matches_frozen_shipped_schema(tmp_path):
    expected = json.loads((FIXTURES / "company_co0002_schema.json").read_text())
    path = tmp_path / "co0002.db"
    _make_revision(path, "company", "co0002", lambda _conn: None)
    with sqlite3.connect(path) as conn:
        assert _normalized_schema(conn) == expected


def test_non_initial_migrations_do_not_import_live_schema():
    storage = Path(bookflow.__file__).parent / "storage"
    violations = []
    for chain in ("hub", "company"):
        versions = storage / f"{chain}_migrations" / "versions"
        for path in sorted(versions.glob("*.py")):
            if path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            if _assigned_literal(tree, "down_revision") is None:
                continue
            if _live_schema_imports(tree, chain):
                violations.append(str(path.relative_to(storage)))
    assert violations == []


def test_fresh_init_has_current_compatibility_schema(tmp_path):
    root = tmp_path / "fresh"
    bookflow.connect(data_root=str(root)).init()

    assert current_revision_raw(root / "hub.db") == "hub0008"
    assert HEADS == {"hub": "hub0008", "company": "co0006"}
    assert str(hub_schema.memberships.c.grants.type) == "TEXT" and hub_schema.memberships.c.grants.nullable
    assert str(hub_schema.memberships.c.denies.type) == "TEXT" and hub_schema.memberships.c.denies.nullable
    assert [column.name for column in hub_schema.role_capabilities.primary_key.columns] == [
        "role", "capability", "required_role",
    ]
    assert [column.name for column in hub_schema.features.primary_key.columns] == [
        "scope_type", "scope_id", "feature",
    ]
    with sqlite3.connect(root / "hub.db") as conn:
        membership_columns = {row[1]: row for row in conn.execute("PRAGMA table_info(memberships)")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        seeded = tuple(conn.execute(
            "SELECT role, capability, required_role FROM role_capabilities ORDER BY role, capability, required_role"
        ))
        assert membership_columns["grants"][2:4] == ("TEXT", 0)
        assert membership_columns["denies"][2:4] == ("TEXT", 0)
        assert {"role_capabilities", "features"} <= tables
        assert _pk_columns(conn, "role_capabilities") == ["role", "capability", "required_role"]
        assert _pk_columns(conn, "features") == ["scope_type", "scope_id", "feature"]
        assert seeded == CURRENT_ROLE_CAPABILITY_SEED
        assert conn.execute("SELECT count(*) FROM features").fetchone()[0] == 0


def test_frozen_role_capability_seed_matches_registry():
    assert CURRENT_ROLE_CAPABILITY_SEED == _registry_role_capability_projection()


def test_populated_hub0004_upgrade_refreshes_complete_capabilities(tmp_path):
    hub = tmp_path / "hub.db"
    _make_revision(hub, "hub", "hub0004", lambda _conn: None)
    assert current_revision_raw(hub) == "hub0004"

    backups = tmp_path / "backups"
    with open_database(hub, writable=True) as db:
        assert migrate_to_head(db, "hub", backups) == ("hub0004", "hub0008")
        seeded = tuple(db.raw.execute(
            "SELECT role, capability, required_role FROM role_capabilities "
            "ORDER BY role, capability, required_role"
        ))
        assert seeded == CURRENT_ROLE_CAPABILITY_SEED
        assert db.raw.execute("PRAGMA foreign_key_check").fetchall() == []

    saved = list(backups.glob("hub-*-from-hub0004.db"))
    assert len(saved) == 1


def test_populated_co0003_upgrade_allows_job_delivery_inheritance(tmp_path):
    company = tmp_path / "company.db"

    def populate(conn):
        conn.execute(
            "INSERT INTO customers (id,version,created_at,created_by,created_via,updated_at,updated_by,updated_via,"
            "active,name,name_key,parent_id,full_name,full_name_key,depth,path,preferred_delivery_method,"
            "job_status,address_mode,contact_mode) VALUES "
            "('C1',1,'t','U1','cli','t','U1','cli',1,'Existing','existing',NULL,'Existing','existing',1,"
            "'/C1/','mail','none','own','own')"
        )

    _make_revision(company, "company", "co0003", populate)
    backups = tmp_path / "backups"
    with open_database(company, writable=True) as db:
        assert migrate_to_head(db, "company", backups) == ("co0003", "co0006")
        column = next(
            row for row in db.raw.execute("PRAGMA table_info(customers)")
            if row[1] == "preferred_delivery_method"
        )
        assert column[3] == 0
        assert db.raw.execute(
            "SELECT preferred_delivery_method FROM customers WHERE id='C1'"
        ).fetchone() == ("mail",)
        assert db.raw.execute("PRAGMA foreign_key_check").fetchall() == []


def test_populated_hub0002_upgrade_adds_compatibility_schema_and_verified_backup(tmp_path):
    hub = tmp_path / "hub.db"

    def populate(conn):
        common = "'t','U1','cli','t','U1','cli'"
        conn.execute(
            "INSERT INTO users (id, version, created_at, created_by, created_via, updated_at, updated_by, updated_via, "
            "kind, username, display_name, owner_user_id, password_hash, hub_admin, timezone, active) "
            f"VALUES ('U1', 1, {common}, 'human', 'k', 'K', NULL, NULL, 1, NULL, 1)"
        )
        conn.execute(
            "INSERT INTO organizations (id, version, created_at, created_by, created_via, updated_at, updated_by, updated_via, "
            "display_name, name_key, path, pending_path, is_demo) "
            f"VALUES ('O1', 1, {common}, 'Org', 'org', '/old/org', NULL, 0)"
        )
        conn.execute(
            "INSERT INTO memberships (id, user_id, scope_type, scope_id, role, granted_by, granted_at, revoked_at) "
            "VALUES ('M1', 'U1', 'organization', 'O1', 'owner', 'U1', 't', NULL)"
        )

    _make_revision(hub, "hub", "hub0002", populate)
    assert current_revision_raw(hub) == "hub0002"

    backups = tmp_path / "backups"
    with open_database(hub, writable=True) as db:
        assert migrate_to_head(db, "hub", backups) == ("hub0002", "hub0008")
        membership = db.raw.execute(
            "SELECT id, user_id, scope_type, scope_id, role, grants, denies FROM memberships WHERE id='M1'"
        ).fetchone()
        seeded = tuple(db.raw.execute(
            "SELECT role, capability, required_role FROM role_capabilities ORDER BY role, capability, required_role"
        ))
        assert membership == ("M1", "U1", "organization", "O1", "owner", None, None)
        assert seeded == CURRENT_ROLE_CAPABILITY_SEED
        assert db.raw.execute("SELECT count(*) FROM features").fetchone()[0] == 0
        assert db.raw.execute("PRAGMA foreign_key_check").fetchall() == []

    saved = list(backups.glob("hub-*-from-hub0002.db"))
    assert len(saved) == 1
    with sqlite3.connect(sqlite_uri(saved[0], "ro"), uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "hub0002"
        assert conn.execute("SELECT id, role FROM memberships WHERE id='M1'").fetchone() == ("M1", "owner")
        assert "grants" not in {row[1] for row in conn.execute("PRAGMA table_info(memberships)")}
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('role_capabilities', 'features')"
        ).fetchone()[0] == 0


def test_fresh_and_populated_hub0002_paths_have_identical_schema(tmp_path):
    fresh = tmp_path / "fresh-hub.db"
    upgraded = tmp_path / "upgraded-hub.db"

    _make_revision(fresh, "hub", HEADS["hub"], lambda _conn: None)

    def populate(conn):
        common = "'t','U1','cli','t','U1','cli'"
        conn.execute(
            "INSERT INTO users (id, version, created_at, created_by, created_via, updated_at, updated_by, updated_via, "
            "kind, username, display_name, owner_user_id, password_hash, hub_admin, timezone, active) "
            f"VALUES ('U1', 1, {common}, 'human', 'k', 'K', NULL, NULL, 1, NULL, 1)"
        )
        conn.execute(
            "INSERT INTO organizations (id, version, created_at, created_by, created_via, updated_at, updated_by, updated_via, "
            "display_name, name_key, path, pending_path, is_demo) "
            f"VALUES ('O1', 1, {common}, 'Org', 'org', '/old/org', NULL, 0)"
        )
        conn.execute(
            "INSERT INTO memberships (id, user_id, scope_type, scope_id, role, granted_by, granted_at, revoked_at) "
            "VALUES ('M1', 'U1', 'organization', 'O1', 'owner', 'U1', 't', NULL)"
        )

    _make_revision(upgraded, "hub", "hub0002", populate)
    with open_database(upgraded, writable=True) as db:
        migrate_to_head(db, "hub", tmp_path / "hub-schema-backups")

    with sqlite3.connect(fresh) as left, sqlite3.connect(upgraded) as right:
        assert _normalized_schema(left) == _normalized_schema(right)


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


def _downgrade_copy(src: Path, chain: str, tables: dict[str, list[str]], revision: str | None = None) -> None:
    """Rebuild ``src`` at an older revision with the rows of the given tables."""
    from alembic import command
    from bookflow.storage.migrate import _config
    tmp = src.with_suffix(".old")
    with open_database(tmp, writable=True, create=True) as db:
        command.upgrade(_config(chain, db.conn), revision or f"{'hub' if chain == 'hub' else 'co'}0001")
        db.raw.execute(f"ATTACH DATABASE '{src}' AS cur")
        for table, cols in tables.items():
            target_columns = {row[1] for row in db.raw.execute(f"PRAGMA table_info({table})")}
            collist = ", ".join(column for column in cols if column in target_columns)
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
    made = c.company.new(
        legal_name="Old Co",
        home_currency="USD",
        organization="Old Org",
        timezone="UTC",
        chart="none",
    )
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
