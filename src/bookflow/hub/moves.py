"""Pending folder moves for companies and organizations (blueprint 3.1).

One function finishes a pending move from any state — nothing moved yet, moved to the
case-only hop, or moved to the target — and leaves the same versioned, audited state an
uninterrupted move would: a bumped row, `path` set, `pending_path` cleared, one `move` event.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.moves import move_dir, rename_noreplace
from bookflow.core.registry import Touched
from bookflow.core.session import Session, now_iso
from bookflow.hub import schema as h
from bookflow.hub.audit import write_event


def find_folder(s: Session, rel: str, moving_id: str) -> Path | None:
    """The folder at ``rel`` or its case-only hop form, whichever exists."""
    p = s.abs_path(rel)
    if p.exists():
        return p
    hop = p.with_name(f"{p.name}.moving-{moving_id}")
    if hop.exists():
        return hop
    return None


def effective_path(s: Session, rel: str, pending: str | None, moving_id: str) -> Path | None:
    """For read-only opens: the folder that exists now, without writing anything."""
    if pending:
        found = find_folder(s, pending, moving_id)
        if found is not None:
            return found
    p = s.abs_path(rel)
    return p if p.exists() else None


def _do_move(s: Session, old_rel: str, pending: str, moving_id: str, error_code: str, id_key: str, row_id: str) -> None:
    target = s.abs_path(pending)
    if target.exists():
        return
    hop = target.with_name(f"{target.name}.moving-{moving_id}")
    if hop.exists():
        rename_noreplace(hop, target)
        return
    src = s.abs_path(old_rel)
    if not src.exists():
        raise BookflowError(error_code, details={id_key: row_id, "path": str(target)})
    try:
        move_dir(src, target, company_id=moving_id)
    except BookflowError as e:
        raise BookflowError(error_code, details={id_key: row_id, "path": str(target), "cause": e.code, "errno": e.details.get("errno")})


def complete_company_move(s: Session, ctx: Context, row: dict[str, Any], via: str) -> dict[str, Any]:
    """Finish the company's pending move; requires a writable hub and the data-root lock."""
    pending = row["pending_path"]
    _do_move(s, row["path"], pending, row["id"], "E_RENAME_INCOMPLETE", "company_id", row["id"])
    new = {**row, "path": pending, "pending_path": None, "version": row["version"] + 1, "updated_at": now_iso(),
           "updated_by": s.actor.id if s.actor else row["updated_by"], "updated_via": via}
    s.hub.raw.execute("BEGIN IMMEDIATE")
    s.hub.conn.execute(h.companies.update().where(h.companies.c.id == row["id"]).values(
        path=pending, pending_path=None, version=new["version"], updated_at=new["updated_at"], updated_by=new["updated_by"], updated_via=new["updated_via"]))
    write_event(s, ctx, "company move", f"moved the folder of company {row['display_name']}",
                [Touched("company", row["id"], "update", row["version"], new["version"], {k: v for k, v in new.items() if k in h.companies.c})])
    s.hub.raw.execute("COMMIT")
    row.update(new)
    return row


def complete_org_move(s: Session, ctx: Context, org: dict[str, Any], via: str) -> dict[str, Any]:
    """Finish the organization's pending move and rewrite every company path under it."""
    pending = org["pending_path"]
    _do_move(s, org["path"], pending, org["id"], "E_RENAME_INCOMPLETE", "organization_id", org["id"])
    old_prefix = org["path"].rstrip("/") + "/"
    new = {**org, "path": pending, "pending_path": None, "version": org["version"] + 1, "updated_at": now_iso(),
           "updated_by": s.actor.id if s.actor else org["updated_by"], "updated_via": via}
    s.hub.raw.execute("BEGIN IMMEDIATE")
    s.hub.conn.execute(h.organizations.update().where(h.organizations.c.id == org["id"]).values(
        path=pending, pending_path=None, version=new["version"], updated_at=new["updated_at"], updated_by=new["updated_by"], updated_via=new["updated_via"]))
    touched = [Touched("organization", org["id"], "update", org["version"], new["version"], {k: v for k, v in new.items() if k in h.organizations.c})]
    for crow in s.hub.conn.execute(sa.select(h.companies).where(h.companies.c.organization_id == org["id"])).mappings().all():
        crow = dict(crow)
        if crow["path"].startswith(old_prefix):
            cnew = {**crow, "path": pending.rstrip("/") + "/" + crow["path"][len(old_prefix):], "version": crow["version"] + 1,
                    "updated_at": new["updated_at"], "updated_by": new["updated_by"], "updated_via": via}
            s.hub.conn.execute(h.companies.update().where(h.companies.c.id == crow["id"]).values(path=cnew["path"], version=cnew["version"], updated_at=cnew["updated_at"], updated_by=cnew["updated_by"], updated_via=via))
            touched.append(Touched("company", crow["id"], "update", crow["version"], cnew["version"], cnew))
    write_event(s, ctx, "organization move", f"moved the folder of organization {org['display_name']}", touched)
    s.hub.raw.execute("COMMIT")
    org.update(new)
    return org
