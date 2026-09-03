"""Run one command: locks, actor, company, roles, databases, plan/apply, audit, config."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ValidationError

from bookflow.core.config import Config, os_login
from bookflow.core.context import CONTEXT_FIELD_NAMES, ActorKind, Context
from bookflow.core.errors import BookflowError
from bookflow.core.fs import check_local
from bookflow.core.ids import is_ulid, normalize_ulid
from bookflow.core.locks import RootLock
from bookflow.core.models import redact_paths
from bookflow.core.perms import private_umask
from bookflow.core.registry import Command, Plan
from bookflow.core.session import Actor, Session
from bookflow.hub import access, schema as h
from bookflow.hub.audit import write_event
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, backup, classify, current_revision_raw, migrate_to_head
from bookflow.storage.paths import name_key, resolve_data_root


def _validation_error(e: ValidationError) -> BookflowError:
    fields = [{"field": ".".join(str(p) for p in err["loc"]) or "input", "problem": err["msg"]} for err in e.errors()]
    return BookflowError("E_VALIDATION", details={"fields": fields})


def validate_input(cmd: Command, raw: dict[str, Any]) -> BaseModel:
    ctx_keys = set(raw) & CONTEXT_FIELD_NAMES
    if ctx_keys:
        raise BookflowError("E_CONTEXT_IN_INPUT", details={"fields": sorted(ctx_keys)})
    try:
        return cmd.input_model.model_validate(raw)
    except ValidationError as e:
        raise _validation_error(e)


def _load_actor(s: Session) -> None:
    table = s.config.user_table(s.os_login)
    if not table or "user_id" not in table:
        raise BookflowError("E_NO_ACTOR")
    row = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == table["user_id"], h.users.c.active.is_(True))).mappings().first()
    if row is None:
        raise BookflowError("E_NO_ACTOR")
    s.actor = Actor(id=row["id"], kind=row["kind"], username=row["username"], display_name=row["display_name"], hub_admin=bool(row["hub_admin"]), timezone=row["timezone"])
    access.load_memberships(s)


def resolve_company(s: Session, selector: str | None, source: str) -> dict[str, Any]:
    """Blueprint 5.3. Returns the hub company row or raises E_COMPANY_NOT_FOUND / E_COMPANY_AMBIGUOUS."""
    if selector is None:
        raise BookflowError("E_COMPANY_NOT_FOUND", details={"source": "none"})
    vis = access.visible_company_filter(s)
    q = sa.select(h.companies).where(vis)
    if is_ulid(selector):
        row = s.hub.conn.execute(q.where(h.companies.c.id == normalize_ulid(selector))).mappings().first()
        if row:
            return dict(row)
    if "/" in selector:
        org_name, co_name = selector.split("/", 1)
        row = s.hub.conn.execute(
            q.join(h.organizations, h.organizations.c.id == h.companies.c.organization_id)
             .where(h.organizations.c.name_key == name_key(org_name), h.companies.c.name_key == name_key(co_name))
        ).mappings().first()
        if row:
            return {k: v for k, v in row.items() if k in h.companies.c}
    rows = s.hub.conn.execute(q.where(h.companies.c.name_key == name_key(selector))).mappings().all()
    if len(rows) == 1:
        return dict(rows[0])
    if len(rows) > 1:
        raise BookflowError("E_COMPANY_AMBIGUOUS", details={"selector": selector})
    raise BookflowError("E_COMPANY_NOT_FOUND", details={"source": source})


def resolve_organization(s: Session, selector: str) -> dict[str, Any]:
    ids = access.visible_org_ids(s)
    q = sa.select(h.organizations)
    if ids is not None:
        q = q.where(h.organizations.c.id.in_(ids) if ids else sa.false())
    if is_ulid(selector):
        row = s.hub.conn.execute(q.where(h.organizations.c.id == normalize_ulid(selector))).mappings().first()
        if row:
            return dict(row)
    row = s.hub.conn.execute(q.where(h.organizations.c.name_key == name_key(selector))).mappings().first()
    if row:
        return dict(row)
    raise BookflowError("E_ORGANIZATION_NOT_FOUND")


def _open_hub(s: Session, writable: bool, ctx: Context) -> None:
    path = s.data_root / "hub.db"
    if not path.exists():
        raise BookflowError("E_NOT_INITIALIZED", details={"data_root": str(s.data_root)})
    if not writable:
        rev = current_revision_raw(path)
        state = classify("hub", rev)
        if state == "unknown":
            raise BookflowError("E_SCHEMA_UNKNOWN", details={"revision": rev, "path": str(path)})
        if state != "head":
            raise BookflowError("E_SCHEMA_BEHIND", details={"revision": rev, "head": HEADS["hub"], "path": str(path)}, message="The hub database schema is behind this version of Bookflow; run `bookflow upgrade`, or ask a user with write access to.")
    s._hub_cm = open_database(path, writable)  # type: ignore[attr-defined]
    s.hub = s._hub_cm.__enter__()  # type: ignore[attr-defined]
    if writable:
        before, after = migrate_to_head(s.hub, "hub", s.data_root / "backups")
        if before != after:
            s.hub.raw.execute("BEGIN IMMEDIATE")
            write_event(s, ctx, "upgrade", f"migrated hub from {before} to {after}", [], actor_id=None, actor_kind=None)
            s.hub.raw.execute("COMMIT")


def open_company(s: Session, ctx: Context, writable: bool) -> None:
    """Open the selected company database, completing a pending move first when needed."""
    row = s.company_row
    assert row is not None and s.hub is not None
    path = s.abs_path(row["path"])
    if not path.exists():
        pending = row.get("pending_path")
        if pending and s.abs_path(pending).exists():
            _complete_pending(s, row)
            path = s.abs_path(row["path"])
        else:
            raise BookflowError("E_COMPANY_MISSING", details={"company_id": row["id"], "path": str(path)})
    db_path = path / "company.db"
    if not writable:
        rev = current_revision_raw(db_path)
        state = classify("company", rev)
        if state == "unknown":
            raise BookflowError("E_SCHEMA_UNKNOWN", details={"revision": rev, "path": str(db_path)})
        if state != "head":
            raise BookflowError("E_SCHEMA_BEHIND", details={"revision": rev, "head": HEADS["company"], "path": str(db_path)}, message="The company database schema is behind this version of Bookflow; run `bookflow upgrade`, or ask a user with write access to.")
    s._co_cm = open_database(db_path, writable)  # type: ignore[attr-defined]
    s.company = s._co_cm.__enter__()  # type: ignore[attr-defined]
    s.company_id = row["id"]
    s.company_tz = None
    from bookflow.company.info import read_info, write_display_name_copy
    if writable:
        before, after = migrate_to_head(s.company, "company", path / "backups")
        info = read_info(s.company)
        if info.get("display_name") != row["display_name"]:
            write_display_name_copy(s.company, row["display_name"])
        if before != after:
            s.hub.raw.execute("BEGIN IMMEDIATE")
            s.hub.conn.execute(h.companies.update().where(h.companies.c.id == row["id"]).values(schema_revision=after))
            write_event(s, ctx, "upgrade", f"migrated company {row['display_name']} from {before} to {after}", [], actor_id=None, actor_kind=None)
            s.hub.raw.execute("COMMIT")
    else:
        info = read_info(s.company)
    s.company_tz = info.get("timezone")

def _complete_pending(s: Session, row: dict[str, Any]) -> None:
    """A pending move whose folder exists: commit the path (blueprint 3.1)."""
    pending = row["pending_path"]
    if pending.startswith("trash/"):
        from bookflow.hub.companies import delete_company_rows
        s.hub.raw.execute("BEGIN IMMEDIATE")
        delete_company_rows(s, row["id"])
        s.hub.raw.execute("COMMIT")
        raise BookflowError("E_COMPANY_NOT_FOUND", details={"source": "option"})
    s.hub.raw.execute("BEGIN IMMEDIATE")
    s.hub.conn.execute(h.companies.update().where(h.companies.c.id == row["id"]).values(path=pending, pending_path=None))
    s.hub.raw.execute("COMMIT")
    row["path"], row["pending_path"] = pending, None


def _close(s: Session) -> None:
    for attr in ("_co_cm", "_hub_cm"):
        cm = getattr(s, attr, None)
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            finally:
                setattr(s, attr, None)


def run(cmd: Command, raw_input: dict[str, Any], ctx: Context, *, data_root: str | None = None,
        company_selector: str | None = None, company_source: str = "option", dry_run: bool = False,
        login: str | None = None) -> dict[str, Any]:
    """Execute a command and return its output as a dict (redacted for the actor)."""
    if dry_run and not cmd.is_write:
        raise BookflowError("E_USAGE", message=f"`{cmd.name}` does not write; --dry-run does not apply.")
    if company_selector is not None and cmd.scope != "company" and not _accepts_selector(cmd):
        raise BookflowError("E_USAGE", message=f"`{cmd.name}` is not a company-scoped command; --company does not apply.")
    inp = validate_input(cmd, raw_input)
    root = resolve_data_root(data_root)
    check_local(root)
    s = Session(data_root=root, os_login=login or os_login(), config=Config(root / "config.toml"), dry_run=dry_run)
    if cmd.bootstrap:
        return _run_bootstrap(cmd, inp, ctx, s)
    if not (root / "hub.db").exists():
        raise BookflowError("E_NOT_INITIALIZED", details={"data_root": str(root)})
    with private_umask(), RootLock(root, cmd.name):
        try:
            s.config = Config.load(root / "config.toml")
            _open_hub(s, bool(cmd.writes & {"hub", "config"}), ctx)
            _load_actor(s)
            ctx = ctx.model_copy(update={"actor_id": s.actor.id, "actor_kind": ActorKind(s.actor.kind), "hub_admin": s.actor.hub_admin})
            if cmd.scope == "company":
                s.company_row = resolve_company(s, company_selector, company_source)
                acc, role = access.company_role(s, s.company_row["id"], s.company_row["organization_id"])
                if acc is None or not access.role_satisfies(role, acc, cmd.required_role, s.is_hub_admin):
                    if acc is None:
                        raise BookflowError("E_COMPANY_NOT_FOUND", details={"source": company_source})
                    raise BookflowError("E_PERMISSION")
                ctx = ctx.model_copy(update={"company_id": s.company_row["id"]})
                open_company(s, ctx, "company" in cmd.writes)
            elif cmd.required_role == "hub_admin" and not s.is_hub_admin:
                raise BookflowError("E_PERMISSION")
            plan = cmd.plan(inp, ctx, s)
            if dry_run or not cmd.is_write:
                out = plan.preview.model_dump(mode="json")
                if dry_run:
                    out["dry_run"] = True
                return redact_paths(out, s.is_hub_admin)
            applied = _apply(cmd, plan, ctx, s)
            out = applied.output.model_dump(mode="json")
            if s.warnings and "warnings" in out:
                out["warnings"] = list(s.warnings)
            return redact_paths(out, s.is_hub_admin)
        finally:
            _close(s)


def _accepts_selector(cmd: Command) -> bool:
    return False


def _apply(cmd: Command, plan: Plan, ctx: Context, s: Session):
    assert cmd.apply is not None
    if "hub" in cmd.writes or "config" in cmd.writes:
        s.hub.raw.execute("BEGIN IMMEDIATE")
    if s.company is not None and "company" in cmd.writes:
        s.company.raw.execute("BEGIN IMMEDIATE")
    try:
        applied = cmd.apply(plan, ctx, s)
        if s.company is not None and "company" in cmd.writes and s.company.raw.in_transaction:
            s.company.raw.execute("COMMIT")
        if "hub" in cmd.writes or "config" in cmd.writes:
            if not applied.audited:
                write_event(s, ctx, cmd.name, applied.summary, applied.touched)
            if s.hub.raw.in_transaction:
                s.hub.raw.execute("COMMIT")
    except BaseException:
        for db in (s.company, s.hub):
            if db is not None and db.raw.in_transaction:
                try:
                    db.raw.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
        raise
    if s.pending_config:
        s.config.save()
    return applied


def _run_bootstrap(cmd: Command, inp: BaseModel, ctx: Context, s: Session) -> dict[str, Any]:
    from bookflow.commands.hub_cmds import run_init
    return run_init(cmd, inp, ctx, s)
