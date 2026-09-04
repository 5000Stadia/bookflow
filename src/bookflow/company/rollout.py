"""Staged company creation (plan section Rollout)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from bookflow.company import schema as c
from bookflow.company.info import upsert_principal
from bookflow.core.errors import BookflowError
from bookflow.core.session import Session
from bookflow.hub.users import common
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from bookflow.storage.paths import reserve_folder, write_company_marker


def create_company_folder(s: Session, org_folder: Path, company_id: str, display_name: str, info: dict[str, Any], via: str, ctx=None) -> Path:
    """Stages 2-4. Returns the folder. Removes it on failure in 2 or 3."""
    folder = reserve_folder(org_folder, display_name)
    try:
        write_company_marker(folder, company_id=company_id, state="creating", display_name=display_name)
        for sub in ("attachments", "backups", "exports"):
            (folder / sub).mkdir(mode=0o700)
        with open_database(folder / "company.db", writable=True, create=True) as db:
            migrate_to_head(db, "company", None)
            row = {"id": company_id, **common(s.actor.id, via), **info, "display_name": display_name}
            db.conn.execute(c.company_info.insert().values(**row))
            upsert_principal(db, user_id=s.actor.id, username=s.actor.username, display_name=s.actor.display_name, kind=s.actor.kind)
            from bookflow.core.audit import write_event_to
            from bookflow.core.registry import Touched
            db.raw.execute("BEGIN IMMEDIATE")
            from bookflow.company.info import logical_info_values
            snapshot = logical_info_values({k: v for k, v in row.items() if k != "display_name"})
            write_event_to(db, ctx, "company new", f"created company {display_name}", [Touched("company_info", company_id, "create", None, 1, snapshot, db="company")], actor_id=s.actor.id, actor_kind=s.actor.kind)
            db.raw.execute("COMMIT")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    try:
        write_company_marker(folder, company_id=company_id, state="ready", display_name=display_name, schema_revision=HEADS["company"])
    except OSError as e:
        raise BookflowError("E_ROLLOUT_INCOMPLETE", details={"state": "incomplete", "path": str(folder), "problem": str(e)})
    return folder
