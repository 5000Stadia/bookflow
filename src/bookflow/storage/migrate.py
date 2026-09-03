"""Migration runner for the hub and company chains (blueprint 3.2, 3.3)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bookflow.core.errors import BookflowError
from bookflow.storage.engine import Database, io_error, sqlite_uri

# Head revisions as constants: checked before Alembic is imported on the read path.
HEADS = {"hub": "hub0002", "company": "co0002"}
_PKG = Path(__file__).parent


def _config(chain: str, connection):
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", str(_PKG / f"{chain}_migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def known_revisions(chain: str) -> set[str]:
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(_config(chain, None))
    return {r.revision for r in script.walk_revisions()}


def current_revision_raw(path: Path) -> str | None:
    """Read alembic_version with a plain sqlite3 read-only connection; None on a fresh file."""
    try:
        conn = sqlite3.connect(sqlite_uri(path, "ro"), uri=True)
    except sqlite3.Error as e:
        raise io_error("open", e, path)
    try:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return None
            raise io_error("read", e, path)
        except sqlite3.DatabaseError as e:
            raise io_error("read", e, path)
        return row[0] if row else None
    finally:
        conn.close()


def current_revision(db: Database) -> str | None:
    from alembic.runtime.migration import MigrationContext
    return MigrationContext.configure(db.conn).get_current_revision()


def classify(chain: str, revision: str | None) -> str:
    """'head', 'behind', 'fresh', or 'unknown'. Head is a constant; Alembic loads only for older revisions."""
    if revision is None:
        return "fresh"
    if revision == HEADS[chain]:
        return "head"
    if revision in known_revisions(chain):
        return "behind"
    return "unknown"


def backup(path: Path, backups_dir: Path, prefix: str = "") -> Path:
    backups_dir.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    target = backups_dir / f"{prefix}{stamp}.db"
    n = 1
    while target.exists():
        n += 1
        target = backups_dir / f"{prefix}{stamp}-{n}.db"
    try:
        src = sqlite3.connect(sqlite_uri(path, "ro"), uri=True)
        dst = sqlite3.connect(sqlite_uri(target, "rwc"), uri=True)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except (sqlite3.Error, OSError) as e:
        raise io_error("backup", e, target)
    return target


def migrate_to_head(db: Database, chain: str, backups_dir: Path | None) -> tuple[str | None, str]:
    """Migrate an open writable database. Returns (revision before, revision after)."""
    before = current_revision(db)
    state = classify(chain, before)
    if state == "unknown":
        raise BookflowError("E_SCHEMA_UNKNOWN", details={"revision": before, "path": str(db.path)})
    if state == "head":
        return before, before
    if state == "behind" and backups_dir is not None:
        backup(db.path, backups_dir, prefix="hub-" if chain == "hub" else "")
    from alembic import command
    command.upgrade(_config(chain, db.conn), "head")
    return before, HEADS[chain]


def require_head_readonly(path: Path, chain: str) -> str:
    rev = current_revision_raw(path)
    state = classify(chain, rev)
    if state == "head":
        return rev
    if state == "unknown":
        raise BookflowError("E_SCHEMA_UNKNOWN", details={"revision": rev, "path": str(path)})
    raise BookflowError("E_SCHEMA_BEHIND", details={"revision": rev, "head": HEADS[chain], "path": str(path)})


def migrate_company(s, ctx, db: Database, folder: Path, row: dict | None) -> tuple[str | None, str]:
    """Migrate an open writable company database and record it (blueprint 7; row 2 plan).

    Writes the `migrate` entry into the company by the system user with on_behalf_of the actor,
    a `baseline` entry for company_info when this migration created the audit tables, rewrites the
    marker, and updates the hub projection with an entry on the caller's hub event (the caller
    records the hub side through ``s.hub_touched``). ``row`` is the hub registry row or None at rollout.
    """
    from bookflow.core.audit import write_event_to
    from bookflow.core.registry import Touched
    from bookflow.storage.paths import write_company_marker
    before, after = migrate_to_head(db, "company", folder / "backups")
    if before == after:
        return before, after
    system = None
    if s.hub is not None:
        from bookflow.hub.users import find_user
        system = find_user(s, kind="system")
    if system is not None:
        from bookflow.company.info import upsert_principal
        upsert_principal(db, user_id=system["id"], username=system["username"], display_name=system["display_name"], kind="system")
    actor_id = s.actor.id if s.actor else None
    mctx = ctx.model_copy(update={"on_behalf_of": actor_id})
    touched = [Touched("company_info", row["id"] if row else "unknown", "migrate", None, None, {"schema_revision": after, "from": before}, db="company")]
    if before is not None and before < "co0002" <= after:
        from bookflow.company.info import read_info
        info = read_info(db)
        if info:
            snap = {k: v for k, v in info.items() if k != "display_name"}
            touched.append(Touched("company_info", info["id"], "baseline", None, info["version"], snap, db="company"))
    db.raw.execute("BEGIN IMMEDIATE") if not db.raw.in_transaction else None
    write_event_to(db, mctx, "upgrade", f"migrated from {before} to {after}", touched, actor_id=system["id"] if system else None, actor_kind="system")
    db.raw.execute("COMMIT")
    if row is not None:
        write_company_marker(folder, company_id=row["id"], state="ready", display_name=row["display_name"], schema_revision=after)
        s.hub_touched.append(Touched("company", row["id"], "migrate", None, None, {"schema_revision": after, "from": before}, db="hub"))
    return before, after
