"""Migration runner for the hub and company chains (blueprint 3.2, 3.3)."""

from __future__ import annotations
from dataclasses import dataclass
from bookflow.core.commit_hooks import CommitHooks

from typing import Any

import sqlite3
import os
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from bookflow.core.errors import BookflowError
from bookflow.core.durability import sync_directory, sync_file
from bookflow.storage.engine import Database, io_error, sqlite_uri

# Head revisions as constants: checked before Alembic is imported on the read path.
HEADS = {"hub": "hub0012", "company": "co0037"}
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


def current_revision_open(db: Database) -> str | None:
    """Read the revision in the same SQLite snapshot as the command's data."""
    try:
        row = db.raw.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    return row[0] if row else None


def classify(chain: str, revision: str | None) -> str:
    """'head', 'behind', 'fresh', or 'unknown'. Head is a constant; Alembic loads only for older revisions."""
    if revision is None:
        return "fresh"
    if revision == HEADS[chain]:
        return "head"
    if revision in known_revisions(chain):
        return "behind"
    return "unknown"


def backup(path: Path, backups_dir: Path, prefix: str = "", from_revision: str | None = None) -> Path:
    """Copy the database with the backup API. One backup per source revision: a failed retry reuses it."""
    backups_dir.mkdir(mode=0o700, exist_ok=True)
    if from_revision:
        existing = sorted(backups_dir.glob(f"{prefix}*-from-{from_revision}.db"))
        if existing:
            # A previous attempt can have replaced the file but failed to
            # persist its directory entry. Retry that boundary before reuse.
            try:
                sync_file(existing[-1])
                sync_directory(backups_dir)
                sync_directory(backups_dir.parent)
            except OSError as e:
                raise io_error("backup", e, existing[-1])
            return existing[-1]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    suffix = f"-from-{from_revision}" if from_revision else ""
    target = backups_dir / f"{prefix}{stamp}{suffix}.db"
    n = 1
    while target.exists():
        n += 1
        target = backups_dir / f"{prefix}{stamp}-{n}{suffix}.db"
    tmp = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".partial", dir=backups_dir)
        os.close(fd)
        tmp = Path(name)
        with closing(sqlite3.connect(sqlite_uri(path, "ro"), uri=True)) as src, closing(sqlite3.connect(sqlite_uri(tmp, "rwc"), uri=True)) as dst:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            rev = None
            try:
                rev = dst.execute("SELECT version_num FROM alembic_version").fetchone()
            except sqlite3.OperationalError:
                pass
        if not ok or (from_revision and (rev is None or rev[0] != from_revision)):
            raise sqlite3.DatabaseError("backup verification failed")
        sync_file(tmp)
        tmp.replace(target)
        sync_directory(backups_dir)
        sync_directory(backups_dir.parent)
    except (sqlite3.Error, OSError) as e:
        try:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise io_error("backup", e, target)
    return target


def restore(path: Path, backup_path: Path) -> None:
    """Put the backup's contents back into ``path`` with the backup API (used when a migration fails)."""
    src = sqlite3.connect(sqlite_uri(backup_path, "ro"), uri=True)
    dst = sqlite3.connect(sqlite_uri(path, "rw"), uri=True)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def migrate_to_head(db: Database, chain: str, backups_dir: Path | None, *, commits: CommitHooks | None = None) -> tuple[str | None, str]:
    """Migrate an open writable database. Returns (revision before, revision after)."""
    commits = commits if commits is not None else CommitHooks()
    with commits.operation("migration.head", db):
        before = current_revision(db)
        state = classify(chain, before)
        if state == "unknown":
            raise BookflowError("E_SCHEMA_UNKNOWN", details={"revision": before, "path": str(db.path)})
        if state == "head":
            return before, before
        saved = None
        if state == "behind" and backups_dir is not None:
            saved = backup(db.path, backups_dir, prefix="hub-" if chain == "hub" else "", from_revision=before)
        from alembic import command
        # SQLite batch rewrites drop and recreate tables; foreign keys must be off for the duration (the pragma is ignored inside a transaction).
        # The whole chain runs in one transaction so a failing step leaves nothing behind; the verified backup is the second line.
        db.raw.execute("PRAGMA foreign_keys=OFF")
        db.raw.execute("BEGIN IMMEDIATE")
        try:
            command.upgrade(_config(chain, db.conn), HEADS[chain])  # HEADS is the target, so the constants are the single truth
            problems = db.raw.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise BookflowError("E_MIGRATION_FAILED", details={"chain": chain, "from": before, "to": HEADS[chain], "cause": "foreign key check", "rows": len(problems)})
            commits.commit(db, "migration.head")
        except BaseException as e:
            recovery: dict[str, Any] = {}
            try:
                if db.raw.in_transaction:
                    db.raw.execute("ROLLBACK")
            except sqlite3.Error as re:
                recovery["rollback_failed"] = type(re).__name__
            if saved is not None:
                try:
                    commits.publishing("migration.head")
                    restore(db.path, saved)
                    recovery["restored_from_path"] = str(saved)
                except (sqlite3.Error, OSError) as re:
                    recovery["restore_failed"] = type(re).__name__
                    recovery["backup_path"] = str(saved)
            details = e.details if isinstance(e, BookflowError) else {"chain": chain, "from": before, "to": HEADS[chain], "cause": type(e).__name__, "path": str(db.path)}
            details = {**details, **recovery}
            message = None
            if "restore_failed" in recovery or "rollback_failed" in recovery:
                message = ("The migration failed and recovery also failed; the database may be partially migrated. Restore it by hand from the backup in details.backup_path (shown to hub admins) before running again."
                           if saved else "The migration failed and the rollback also failed; the database may be partially migrated.")
            code = e.code if isinstance(e, BookflowError) else "E_MIGRATION_FAILED"
            raise BookflowError(code, message=message, details=details) from (e if not isinstance(e, BookflowError) else None)
        finally:
            db.raw.execute("PRAGMA foreign_keys=ON")
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
    with s.commits.operation("migration.company", s.hub, s.company):
        from bookflow.core.audit import write_event_to
        from bookflow.core.registry import Touched
        from bookflow.storage.paths import write_company_marker
        before, after = migrate_to_head(db, "company", folder / "backups", commits=s.commits)
        if before == after:
            return before, after
        system = None
        if s.hub is not None:
            from bookflow.hub.users import find_user
            system = find_user(s, kind="system")
        from bookflow.company.info import upsert_principal
        if system is not None:
            with s.commits.autocommit(db, "migration.company"):
                upsert_principal(db, user_id=system["id"], username=system["username"], display_name=system["display_name"], kind="system")
        if s.actor is not None:
            with s.commits.autocommit(db, "migration.company"):
                upsert_principal(db, user_id=s.actor.id, username=s.actor.username, display_name=s.actor.display_name, kind=s.actor.kind)
        actor_id = s.actor.id if s.actor else None
        mctx = ctx.model_copy(update={"on_behalf_of": actor_id})
        migration_snapshot = {"schema_revision": after, "from": before}
        if before is not None and before < "co0015" <= after:
            from bookflow.company.info import read_info
            migration_snapshot['sales_tax_calculation'] = read_info(db)['sales_tax_calculation']
        touched = [Touched("company_info", row["id"] if row else "unknown", "migrate", None, None, migration_snapshot, db="company")]
        if before is not None and before < "co0002" <= after:
            from bookflow.company.info import read_info
            info = read_info(db)
            if info:
                snap = {k: v for k, v in info.items() if k != "display_name"}
                touched.append(Touched("company_info", info["id"], "baseline", None, info["version"], snap, db="company"))
        db.raw.execute("BEGIN IMMEDIATE") if not db.raw.in_transaction else None
        write_event_to(db, mctx, "upgrade", f"migrated from {before} to {after}", touched, actor_id=system["id"] if system else None, actor_kind="system")
        s.commits.commit(db, "migration.company")
        if row is not None:
            write_company_marker(folder, company_id=row["id"], state="ready", display_name=row["display_name"], schema_revision=after)
            s.hub_touched.append(Touched("company", row["id"], "migrate", None, None, {"schema_revision": after, "from": before}, db="hub"))
        return before, after


@dataclass(frozen=True)
class FeatureRevision:
    """Private feature activation metadata, installed with its owning migration.

    None explicitly means no persistence in this supported chain. A feature
    migration must replace None and ship its real resolver in the same change.
    """
    chain: str
    revision: str | None


def feature_admission(db, feature: FeatureRevision, *, resolver):
    """Return absent only before the owned feature; return its real resolver if active.

    Revision ancestry is Alembic graph ancestry, never lexical ordering. The
    resolver is returned, not invoked: the caller owns typed feature arguments,
    authorization and the existing database snapshot/transaction.
    """
    from alembic.script import ScriptDirectory
    if type(feature) is not FeatureRevision or feature.chain not in ('company', 'hub'):
        raise ValueError('invalid feature metadata')
    script = ScriptDirectory.from_config(_config(feature.chain, None))
    known = {r.revision: r for r in script.walk_revisions()}
    current = current_revision_open(db)
    if current not in known:
        raise BookflowError('E_SCHEMA_UNKNOWN')
    if feature.revision is None:
        if resolver is not None:
            raise ValueError('resolver without feature revision')
        return None
    if feature.revision not in known:
        raise ValueError('feature revision is not in the supported chain')
    ancestors, frontier = set(), [current]
    while frontier:
        revision = frontier.pop()
        if revision in ancestors:
            continue
        ancestors.add(revision)
        parent = known[revision].down_revision
        frontier.extend(parent if isinstance(parent, tuple) else [parent] if parent else [])
    if feature.revision not in ancestors:
        return None
    if not callable(resolver):
        raise BookflowError('E_INTERNAL', message='Active storage feature requires its owning resolver.')
    return resolver
