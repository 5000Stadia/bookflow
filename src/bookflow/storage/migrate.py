"""Migration runner for the hub and company chains (blueprint 3.2, 3.3)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from bookflow.core.errors import BookflowError
from bookflow.storage.engine import Database

# Head revisions as constants: checked before Alembic is imported on the read path.
HEADS = {"hub": "hub0001", "company": "co0001"}
_PKG = Path(__file__).parent


def _config(chain: str, connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_PKG / f"{chain}_migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def known_revisions(chain: str) -> set[str]:
    script = ScriptDirectory.from_config(_config(chain, None))
    return {r.revision for r in script.walk_revisions()}


def current_revision_raw(path: Path) -> str | None:
    """Read alembic_version with a plain sqlite3 read-only connection; None on a fresh file."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return None
        return row[0] if row else None
    finally:
        conn.close()


def current_revision(db: Database) -> str | None:
    return MigrationContext.configure(db.conn).get_current_revision()


def classify(chain: str, revision: str | None) -> str:
    """'head', 'behind', 'fresh', or 'unknown'."""
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
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
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
