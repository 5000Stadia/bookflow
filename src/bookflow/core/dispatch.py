"""Run one command: locks, actor, company, roles, databases, plan/apply, audit, config."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
from bookflow.core import performance
from bookflow.core.registry import Command, Plan, Touched
from bookflow.core.session import Actor, Session
from bookflow.hub import access, schema as h
from bookflow.hub.audit import write_event
from bookflow.storage.engine import io_error, open_database
from bookflow.storage.migrate import HEADS, backup, classify, current_revision_open, current_revision_raw, migrate_to_head
from bookflow.storage.paths import name_key, resolve_data_root


def _validation_error(e: ValidationError) -> BookflowError:
    fields = [{"field": ".".join(str(p) for p in err["loc"]) or "input", "problem": err["msg"]} for err in e.errors()]
    return BookflowError("E_VALIDATION", details={"fields": fields})


CONTEXT_LIMITS = {"reason": 140, "source_ref": 512, "idempotency_key": 128}


def validate_context(ctx: Context) -> None:
    fields = []
    for name, limit in CONTEXT_LIMITS.items():
        v = getattr(ctx, name)
        if v is not None and len(v) > limit:
            fields.append({"field": name, "problem": f"at most {limit} characters"})
    if fields:
        raise BookflowError("E_VALIDATION", details={"fields": fields})


def parse_when(value: str, zone: str | None, end: bool = False) -> str:
    """Parse an ISO date or timestamp given in the viewer's zone into the stored UTC form."""
    try:
        if len(value) == 10:
            dt = datetime.fromisoformat(value)
            dt = dt.replace(tzinfo=ZoneInfo(zone) if zone else timezone.utc)
            if end:
                from datetime import timedelta
                dt = dt + timedelta(days=1)
        else:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(zone) if zone else timezone.utc)
    except (ValueError, KeyError) as e:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "since/until", "problem": f"not a date or timestamp: {value!r}"}]})
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def redact_error(err: BookflowError, allowed: bool) -> BookflowError:
    if allowed:
        return err
    err.details = redact_paths(err.details, False)
    return err


@performance.measured("command.validate")
def validate_input(cmd: Command, raw: dict[str, Any]) -> BaseModel:
    ctx_keys = set(raw) & CONTEXT_FIELD_NAMES
    if ctx_keys:
        raise BookflowError("E_CONTEXT_IN_INPUT", details={"fields": sorted(ctx_keys)})
    try:
        return cmd.input_model.model_validate(raw)
    except ValidationError as e:
        raise _validation_error(e)


@performance.measured("command.resolve")
def _load_actor(s: Session) -> None:
    table = s.config.user_table(s.os_login)
    if table is not None and (not isinstance(table, dict) or not isinstance(table.get("user_id"), str)):
        raise BookflowError("E_CONFIG_INVALID", details={"path": str(s.config.path), "problem": f"users.{s.os_login} is malformed"})
    if not table or "user_id" not in table:
        raise BookflowError("E_NO_ACTOR")
    row = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == table["user_id"], h.users.c.active.is_(True))).mappings().first()
    if row is None:
        raise BookflowError("E_NO_ACTOR", message="This login is mapped to a user that no longer exists; a hub admin can re-map it, or `bookflow init` repairs it when the hub has one human user.", details={"mapped_user_id": table["user_id"]})
    s.actor = Actor(id=row["id"], kind=row["kind"], username=row["username"], display_name=row["display_name"], hub_admin=bool(row["hub_admin"]), timezone=row["timezone"])
    access.load_memberships(s)


@performance.measured("command.resolve")
def _load_actor_by_id(s: Session, user_id: str) -> None:
    """The host's actor resolution: a credential already named the user; memberships load the same way as the CLI's."""
    row = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == user_id, h.users.c.active.is_(True))).mappings().first()
    if row is None:
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "user"})
    s.actor = Actor(id=row["id"], kind=row["kind"], username=row["username"], display_name=row["display_name"], hub_admin=bool(row["hub_admin"]), timezone=row["timezone"])
    access.load_memberships(s)


@performance.measured("command.resolve")
def resolve_company(s: Session, selector: str | None, source: str) -> dict[str, Any]:
    """Blueprint 5.3. Returns the hub company row or raises E_COMPANY_NOT_FOUND / E_COMPANY_AMBIGUOUS."""
    if selector is None:
        raise BookflowError("E_COMPANY_NOT_FOUND", message="No company selected; give --company, set BOOKFLOW_COMPANY, or run `bookflow company use <company>`.", details={"source": "none"})
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
    visible = s.hub.conn.execute(sa.select(h.companies.c.display_name, h.organizations.c.display_name.label("org")).join(h.organizations, h.organizations.c.id == h.companies.c.organization_id).where(vis)).all()
    if len(rows) > 1:
        raise BookflowError("E_COMPANY_AMBIGUOUS", details={"selector": selector, "suggestions": [f"{r.org}/{r.display_name}" for r in visible if name_key(r.display_name) == name_key(selector)][:3]})
    names = [f"{r.org}/{r.display_name}" for r in visible] if "/" in selector else [r.display_name for r in visible]
    raise BookflowError("E_COMPANY_NOT_FOUND", details={"source": source, "suggestions": _suggest(selector, names)})


def _suggest(value: str, candidates: list[str]) -> list[str]:
    """Up to three close matches from what the caller can see (blueprint 5.4)."""
    import difflib
    needle = value.lower()
    subs = [c for c in candidates if needle in c.lower() or c.lower() in needle]
    close = difflib.get_close_matches(value, candidates, n=3, cutoff=0.5)
    out: list[str] = []
    for c in subs + close:
        if c not in out:
            out.append(c)
    return out[:3]


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
    names = [r[0] for r in s.hub.conn.execute(q.with_only_columns(h.organizations.c.display_name)).all()]
    raise BookflowError("E_ORGANIZATION_NOT_FOUND", details={"suggestions": _suggest(selector, names)})


def _open_hub(s: Session, writable: bool, ctx: Context, skip_head_check: bool = False) -> None:
    path = s.data_root / "hub.db"
    if not path.exists():
        raise BookflowError("E_NOT_INITIALIZED", details={"data_root": str(s.data_root)})
    if not writable:
        s._hub_cm = open_database(path, False)
        s.hub = s._hub_cm.__enter__()
    # Unknown writable databases must be refused before changing any pragma.
    rev = current_revision_raw(path) if writable else current_revision_open(s.hub)
    state = classify("hub", rev)
    if state == "unknown":
        raise BookflowError("E_SCHEMA_UNKNOWN", details={"revision": rev, "path": str(path)})
    if not writable and not skip_head_check and state != "head":
        raise BookflowError("E_SCHEMA_BEHIND", details={"revision": rev, "head": HEADS["hub"], "path": str(path)}, message="The hub database schema is behind this version of Bookflow; run `bookflow upgrade`, or ask a user with write access to.")
    if writable:
        s._hub_cm = open_database(path, True)  # type: ignore[attr-defined]
        s.hub = s._hub_cm.__enter__()  # type: ignore[attr-defined]


def _complete_pending_organizations(s: Session, ctx: Context) -> None:
    """A hub admin's writable open finishes every pending organization move (blueprint 3.1).

    Non-admins never touch organizations they cannot administer; the one they are writing into is
    finished by resolve_company_folder after their command is authorized.
    """
    if not s.is_hub_admin:
        return
    from bookflow.hub.moves import complete_org_move
    rows = s.hub.conn.execute(sa.select(h.organizations).where(h.organizations.c.pending_path.isnot(None))).mappings().all()
    for org in rows:
        org = dict(org)
        if org["pending_path"].startswith("trash/"):
            continue
        try:
            complete_org_move(s, ctx, org, ctx.interface.value)
            s.completed_moves.append(org["id"])
        except BookflowError as e:
            if s.hub.write_transaction:
                s.hub.raw.execute("ROLLBACK")
            s.warnings.append(f"organization {org['display_name']} has an unfinished move that could not be completed ({e.code}); rerun `organization rename --move` on it")


def _migrate_hub(s: Session, ctx: Context) -> None:
    """Migrate the hub after the actor is known (row 2 plan, 'Schema migrations')."""
    before, after = migrate_to_head(s.hub, "hub", s.data_root / "backups")
    if before != after:
        s.hub_migrated = (before, after)
        _record_migration(s, ctx, s.hub, "hub", before, after)


def resolve_company_folder(s: Session, ctx: Context, row: dict[str, Any], writable: bool) -> Path:
    """Blueprint 3.1: consult pending moves on the company and its organization.

    A writable hub open completes any pending move (same versioned, audited state as an
    uninterrupted move); a read-only open uses the folder that exists and writes nothing.
    """
    from bookflow.hub.moves import complete_company_move, complete_org_move, effective_path
    assert s.hub is not None
    via = ctx.interface.value
    org = s.hub.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == row["organization_id"])).mappings().first()
    org = dict(org) if org else None
    if org and org.get("pending_path") and not org["pending_path"].startswith("trash/"):
        if writable:
            complete_org_move(s, ctx, org, via)
            s.completed_moves.append(org["id"])
            fresh = s.hub.conn.execute(sa.select(h.companies).where(h.companies.c.id == row["id"])).mappings().first()
            row.update(dict(fresh))
        else:
            from bookflow.hub.moves import display_path
            found = display_path(s, row, org)
            if found.exists():
                return found
    pending = row.get("pending_path")
    if pending and pending.startswith("trash/"):
        if writable:
            _complete_trash(s, row, ctx)
        raise BookflowError("E_COMPANY_NOT_FOUND", details={"source": "option"})
    if pending:
        if writable:
            complete_company_move(s, ctx, row, via)
            s.completed_moves.append(row["id"])
            return s.abs_path(row["path"])
        found = effective_path(s, row["path"], pending, row["id"])
        if found is not None:
            return found
    path = s.abs_path(row["path"])
    if not path.exists():
        raise BookflowError("E_COMPANY_MISSING", details={"company_id": row["id"], "path": str(path)})
    return path


def open_company(s: Session, ctx: Context, writable: bool) -> None:
    """Open the selected company database, completing pending moves on writable opens."""
    row = s.company_row
    assert row is not None and s.hub is not None
    path = resolve_company_folder(s, ctx, row, s.hub.writable)
    db_path = path / "company.db"
    if not db_path.exists():
        raise BookflowError("E_COMPANY_MISSING", details={"company_id": row["id"], "check": "database", "path": str(db_path)})
    if s.company_opener is not None:
        s.company = s.company_opener(row, writable, db_path)
        s._co_cm = None  # type: ignore[attr-defined]
    else:
        s._co_cm = open_database(db_path, writable)  # type: ignore[attr-defined]
        s.company = s._co_cm.__enter__()  # type: ignore[attr-defined]
    if not writable:
        rev = current_revision_open(s.company)
        state = classify("company", rev)
        if state == "unknown":
            raise BookflowError("E_SCHEMA_UNKNOWN", details={"revision": rev, "path": str(db_path)})
        if state != "head":
            raise BookflowError("E_SCHEMA_BEHIND", details={"revision": rev, "head": HEADS["company"], "path": str(db_path)}, message="The company database schema is behind this version of Bookflow; run `bookflow upgrade`, or ask a user with write access to.")
    s.company_id = row["id"]
    s.company_tz = None
    from bookflow.company.info import read_info, write_display_name_copy
    from bookflow.storage.migrate import migrate_company
    if writable:
        before, after = migrate_company(s, ctx, s.company, path, row)
        if before != after:
            s.hub.raw.execute("BEGIN IMMEDIATE")
            s.hub.conn.execute(h.companies.update().where(h.companies.c.id == row["id"]).values(schema_revision=after))
            s.hub.raw.execute("COMMIT")
        info = read_info(s.company)
        if info.get("display_name") != row["display_name"]:
            write_display_name_copy(s.company, row["display_name"])
    else:
        info = read_info(s.company)
    s.company_tz = info.get("timezone")
    s.company_info_row = info


def _record_migration(s: Session, ctx: Context, db, chain: str, before: str | None, after: str, record_id: str | None = None, label: str = "hub") -> None:
    """Blueprint 7: a `migrate` entry by the system user with on_behalf_of the triggering actor."""
    from bookflow.core.registry import Touched
    from bookflow.hub.users import find_user
    system = find_user(s, kind="system") if s.hub is not None else None
    actor_id = system["id"] if system else None
    on_behalf = s.actor.id if s.actor else None
    mctx = ctx.model_copy(update={"on_behalf_of": on_behalf})
    touched = [Touched(chain if chain == "hub" else "company", record_id or "hub", "migrate", None, None, {"schema_revision": after, "from": before})]
    s.hub.raw.execute("BEGIN IMMEDIATE")
    write_event(s, mctx, "upgrade", f"migrated {label} from {before} to {after}", touched, actor_id=actor_id, actor_kind="system")
    s.hub.raw.execute("COMMIT")


def _complete_trash(s: Session, row: dict[str, Any], ctx: Context) -> None:
    from bookflow.hub.companies import delete_company_rows
    from bookflow.core.durability import sync_move_parents
    from bookflow.core.moves import move_dir
    from bookflow.storage.paths import read_company_marker
    s.release_company(row["id"])
    source, target = s.abs_path(row["path"]), s.abs_path(row["pending_path"])
    if source.exists() and target.exists():
        raise BookflowError("E_IO", details={"operation": "trash recovery", "errno": "EEXIST", "path": str(target)})
    if not target.exists() and source.exists():
        move_dir(source, target, company_id=row["id"])
    if not target.exists() or read_company_marker(target).get("company_id") != row["id"]:
        raise BookflowError("E_COMPANY_MISSING", details={"company_id": row["id"], "path": str(target)})
    sync_move_parents(source, target)
    s.hub.raw.execute("BEGIN IMMEDIATE")
    touched = delete_company_rows(s, row["id"])
    write_event(s, ctx, "trash recovery", "completed pending company trash", touched)
    if s.pending_config:
        s.config.stage_pending(s.hub, request_id=ctx.request_id)
    s.hub.raw.execute("COMMIT")
    if s.pending_config:
        try:
            s.config.flush_pending(s.hub)
        except BookflowError as error:
            if error.code == "E_PARTIAL_WRITE":
                error.details["durable"] = sorted({"config", *(entry.record_type for entry in touched)})
            raise
        s.pending_config = False


@performance.measured("command.close")
def _close(s: Session) -> None:
    try:
        s.close_company()
    finally:
        cm = getattr(s, "_hub_cm", None)
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            finally:
                s._hub_cm = None  # type: ignore[attr-defined]


def guard(fn, allowed=False):
    """The one error boundary: named errors redacted for non-admins; OS and SQLite failures become E_IO.

    ``allowed`` may be a callable evaluated when the error happens, since the actor is known only part-way through run().
    """
    def ok() -> bool:
        return bool(allowed()) if callable(allowed) else bool(allowed)
    try:
        return fn()
    except BookflowError as e:
        raise redact_error(e, ok())
    except (OSError, sqlite3.Error) as e:
        raise redact_error(io_error("command", e, getattr(e, "filename", None)), ok())
    except sa.exc.DBAPIError as e:
        raise redact_error(io_error("command", e.orig if isinstance(e.orig, sqlite3.Error) else e), ok())


def execute(cmd: Command, raw_input: dict[str, Any], ctx: Context, s: Session, *, company_selector: str | None = None,
            company_source: str = "option", dry_run: bool = False) -> dict[str, Any]:
    """Validate and run a command inside an open, locked session with a loaded actor: the entry point every adapter shares."""
    def body():
        if dry_run and not cmd.is_write:
            raise BookflowError("E_USAGE", message=f"`{cmd.name}` does not write; --dry-run does not apply.")
        if company_selector is not None and cmd.scope != "company":
            raise BookflowError("E_USAGE", message=f"`{cmd.name}` is not a company-scoped command; --company does not apply.")
        inp = validate_input(cmd, raw_input)
        validate_context(ctx)
        if s.hub is not None and s.hub.writable and not dry_run:
            s.config.flush_pending(s.hub)
            _complete_pending_organizations(s, ctx)
        return run_in_session(cmd, inp, ctx, s, company_selector=company_selector, company_source=company_source, dry_run=dry_run)
    with performance.span("command.execute", command=cmd.name):
        return guard(body, s.is_hub_admin)


def run(cmd: Command, raw_input: dict[str, Any], ctx: Context, *, data_root: str | None = None,
        company_selector: str | None = None, company_source: str = "option", dry_run: bool = False,
        _login: str | None = None, input_stream=None, output_stream=None) -> dict[str, Any]:
    """Execute a command from a fresh process: data root, hand-off to a live host, lock, hub, actor, migration, execute."""
    if performance.enabled():
        performance.protect_selection(data_root)
    if cmd.transfer is not None:
        from bookflow.core.transfer_run import run_transfer
        return run_transfer(cmd, raw_input, ctx, data_root=data_root, selector=company_selector,
                            source=company_source, dry_run=dry_run, login=_login,
                            input_stream=input_stream, output_stream=output_stream)
    if input_stream is not None or output_stream is not None:
        raise BookflowError("E_USAGE", message="This command does not accept a binary stream.")
    with performance.span("command", command=cmd.name, mode="offline"):
        return _run(cmd, raw_input, ctx, data_root=data_root, company_selector=company_selector,
                    company_source=company_source, dry_run=dry_run, _login=_login)


def _run(cmd: Command, raw_input: dict[str, Any], ctx: Context, *, data_root: str | None = None,
         company_selector: str | None = None, company_source: str = "option", dry_run: bool = False,
         _login: str | None = None) -> dict[str, Any]:
    if dry_run and not cmd.is_write:
        raise BookflowError("E_USAGE", message=f"`{cmd.name}` does not write; --dry-run does not apply.")
    if company_selector is not None and cmd.scope != "company":
        raise BookflowError("E_USAGE", message=f"`{cmd.name}` is not a company-scoped command; --company does not apply.")
    validate_input(cmd, raw_input)
    validate_context(ctx)

    if cmd.standalone_runner is not None:
        def standalone():
            inp = validate_input(cmd, raw_input)
            result = cmd.standalone_runner(cmd, inp, ctx)
            return cmd.output_model.model_validate(result).model_dump(mode="json")
        return guard(standalone, True)

    def before_lock():
        root = resolve_data_root(data_root)
        performance.protect_root(root)
        if root.exists() and not root.is_dir():
            raise BookflowError("E_IO", details={"operation": "data_root", "errno": "ENOTDIR", "path": str(root)})
        check_local(root)
        s = Session(data_root=root, os_login=_login or os_login(), config=Config(root / "config.toml"), dry_run=dry_run)
        if cmd.bootstrap:
            return _run_bootstrap(cmd, validate_input(cmd, raw_input), ctx, s), None
        if not (root / "hub.db").exists():
            raise BookflowError("E_NOT_INITIALIZED", details={"data_root": str(root)})
        return None, s
    done, s = guard(before_lock, False)
    if done is not None:
        return done
    from bookflow.core.forward import try_forward  # try_forward decides what may travel; bootstrap commands never do
    with performance.span("command.forward", mode="forwarded"):
        forwarded = try_forward(s.data_root, cmd, raw_input, ctx, company_selector, company_source, dry_run)
    if forwarded is not None:
        return forwarded

    def under_lock():
        with private_umask(), RootLock(s.data_root, cmd.name):
            try:
                s.config = Config.load(s.data_root / "config.toml")
                needs_hub_write = (bool(cmd.writes & {"hub", "config"}) or cmd.kind == "advisory" or (cmd.scope == "company" and "company" in cmd.writes)) and not dry_run
                _open_hub(s, needs_hub_write, ctx, skip_head_check=(dry_run and cmd.name == "upgrade"))
                _load_actor(s)
                ctx2 = ctx.model_copy(update={"actor_id": s.actor.id, "actor_kind": ActorKind(s.actor.kind)})
                if s.hub.writable:
                    _migrate_hub(s, ctx2)
                return execute(cmd, raw_input, ctx2, s, company_selector=company_selector, company_source=company_source, dry_run=dry_run)
            finally:
                _close(s)
    return guard(under_lock, lambda: s.is_hub_admin)


def authorize(cmd: Command, ctx: Context, s: Session, *, company_selector: str | None = None,
              company_source: str = "option", dry_run: bool = False, read_only: bool = False) -> Context:
    """Shared company, role, directive and reason checks, with optional read-only opening."""
    if cmd.scope == "company":
        if s.company_row is None or (company_selector is not None):
            s.close_company()
            s.company_row = resolve_company(s, company_selector, company_source)
        acc, role = access.company_role(s, s.company_row["id"], s.company_row["organization_id"])
        if acc is None:
            raise BookflowError("E_COMPANY_NOT_FOUND", details={"source": company_source})
        if not access.role_satisfies(role, acc, cmd.required_role, s.is_hub_admin):
            raise BookflowError("E_PERMISSION", details={"capability": cmd.capability, "required_role": cmd.required_role, "role": role})
        ctx = ctx.model_copy(update={"company_id": s.company_row["id"]})
        if s.company is None:
            open_company(s, ctx, "company" in cmd.writes and not dry_run and not read_only)
    elif cmd.required_role == "hub_admin" and not s.is_hub_admin:
        raise BookflowError("E_PERMISSION", details={"capability": cmd.capability, "required_role": "hub_admin"})
    # directive resolution and the reason gate (blueprint 5.8)
    s.directive_code = None
    if ctx.directive_id:
        if cmd.scope != "company" or not cmd.is_write:
            raise BookflowError("E_USAGE", message="--directive applies only to company-scoped writes.")
        from bookflow.company.directives import resolve as resolve_directive
        drow = resolve_directive(s.company, ctx.directive_id, include_inactive=False)
        ctx = ctx.model_copy(update={"directive_id": drow["id"]})
        s.directive_code = drow["code"]
    if cmd.is_write and s.actor.kind in ("agent", "system") and not ctx.reason and not ctx.directive_id:
        raise BookflowError("E_REASON_REQUIRED")
    if ctx.idempotency_key and not cmd.accepts_idempotency_key:
        raise BookflowError("E_USAGE", message=f"`{cmd.name}` does not accept an idempotency key.")
    return ctx


def run_in_session(cmd: Command, inp: BaseModel, ctx: Context, s: Session, *, company_selector: str | None = None,
                   company_source: str = "option", dry_run: bool = False) -> dict[str, Any]:
    """Run a command inside an open, locked session with a loaded actor. Used by run(), demo reset, and later the host."""
    from bookflow.core import idempotency
    s.dry_run = dry_run
    s.hub_touched, s.company_touched = [], []
    ctx = authorize(cmd, ctx, s, company_selector=company_selector,
                    company_source=company_source, dry_run=dry_run)
    if cmd.transfer is not None:
        from bookflow.core.transfers import validate_resource
        validate_resource(cmd, inp, ctx, s)
    if cmd.name in ("attachment link", "attachment unlink", "company compact"):
        from bookflow.company.attachment_gc import pending, recover_pending
        if dry_run:
            if pending(s):
                raise BookflowError("E_DB_BUSY", message="Attachment collection requires recovery before preview.")
        else:
            recover_pending(s, ctx)
    # idempotency lookup (blueprint 6.5)
    key_db = None
    ihash = None
    if ctx.idempotency_key:
        if not cmd.accepts_idempotency_key:
            raise BookflowError("E_USAGE", message=f"`{cmd.name}` does not accept an idempotency key.")
        key_db = s.company if cmd.truth == "company" else s.hub
        retry_input = inp.model_dump(mode="json")
        if cmd.transfer is not None and cmd.transfer.direction == "input":
            retry_input = {"input": retry_input, "body": {
                "sha256": s.transfer.info.sha256, "size_bytes": s.transfer.info.size_bytes}}
        ihash = idempotency.input_hash(retry_input, s.company_row["id"] if s.company_row else None)
        hit = idempotency.lookup(key_db, s.actor.id, ctx.idempotency_key, cmd.name, ihash)
        if hit is not None:
            replay = _replay(cmd, hit, s)
            if replay is not None:
                replay["idempotent_replay"] = True
                if dry_run:
                    replay["dry_run"] = True
                return redact_paths(replay, s.is_hub_admin)
    with performance.span("command.plan", command=cmd.name):
        plan = cmd.plan(inp, ctx, s)
    if dry_run or not cmd.is_write:
        with performance.span("command.serialize"):
            out = plan.preview.model_dump(mode="json")
        if dry_run:
            out["dry_run"] = True
        if cmd.kind == "advisory":
            applied = _apply(cmd, plan, ctx, s)
            out = applied.output.model_dump(mode="json")
        return redact_paths(out, s.is_hub_admin)
    applied = _apply(cmd, plan, ctx, s, key=(key_db, ihash))
    with performance.span("command.serialize"):
        out = applied.output.model_dump(mode="json")
    if "warnings" in out:
        out["warnings"] = list(out.get("warnings") or []) + list(s.warnings)
    return redact_paths(out, s.is_hub_admin)


def _replay(cmd: Command, hit: dict[str, Any], s: Session) -> dict[str, Any] | None:
    """A done key replays its output; an in-progress rollout key inspects the folder it names."""
    import json
    if hit["state"] == "done" and hit["output"]:
        return json.loads(hit["output"])
    if cmd.name == "company new" and hit["output"]:
        info = json.loads(hit["output"])
        folder = Path(info.get("path", ""))
        from bookflow.storage.paths import read_company_marker
        if not folder.exists():
            return None
        try:
            marker = read_company_marker(folder)
        except BookflowError:
            raise BookflowError("E_ROLLOUT_INCOMPLETE", details={"state": "incomplete", "path": str(folder)})
        registered = s.hub.conn.execute(sa.select(h.companies.c.id).where(h.companies.c.id == marker["company_id"])).first()
        if registered:
            return info
        state = "incomplete" if marker.get("state") != "ready" else "unregistered"
        raise BookflowError("E_ROLLOUT_INCOMPLETE", details={"state": state, "path": str(folder)}, message="Company creation did not finish; a folder remains." + (" `company attach` adopts it." if state == "unregistered" else ""))
    return None


def _accepts_selector(cmd: Command) -> bool:
    return False


def _apply(cmd: Command, plan: Plan, ctx: Context, s: Session, key=(None, None)):
    """Apply with one transaction per database, events where the touched records live, commits in truth order."""
    from bookflow.core import idempotency
    from bookflow.core.audit import write_event_to
    assert cmd.apply is not None
    key_db, ihash = key
    hub_tx = bool(cmd.writes & {"hub", "config"}) or cmd.kind == "advisory"
    co_tx = s.company is not None and "company" in cmd.writes
    business_savepoint = co_tx and getattr(cmd, "ledger", False)
    if hub_tx:
        s.hub.raw.execute("BEGIN IMMEDIATE")
    if co_tx:
        s.company.raw.execute("BEGIN IMMEDIATE")
        if business_savepoint:
            s.company.raw.execute("SAVEPOINT bookflow_business")
    try:
        if co_tx:
            _upsert_principals(s, ctx)
        with performance.span("command.apply", command=cmd.name):
            applied = cmd.apply(plan, ctx, s)
        if applied.finalized:
            if any(db is not None and db.write_transaction for db in (s.company, s.hub)):
                raise BookflowError("E_INTERNAL", message="A finalized command left an unfinished transaction.")
            return applied
        if s.pending_config:
            s.config.stage_pending(s.hub, request_id=ctx.request_id)
        if cmd.kind == "advisory":
            if s.company.write_transaction:
                s.company.raw.execute("COMMIT")
            if s.hub.write_transaction:
                s.hub.raw.execute("COMMIT")
            return applied
        hub_entries = [t for t in applied.touched if t.db == "hub"] + list(s.hub_touched)
        co_entries = [t for t in applied.touched if t.db == "company"]
        changed = bool(applied.touched) or applied.audited or bool(s.hub_touched) or s.pending_config
        output = applied.output.model_dump(mode="json")
        if not changed:
            if business_savepoint and s.company is not None and s.company.write_transaction:
                s.company.raw.execute("ROLLBACK TO bookflow_business")
                s.company.raw.execute("RELEASE bookflow_business")
            # A no-op keeps its successful retry result without creating an audit event.
            if key_db is not None and ihash:
                idempotency.store(key_db, s.actor.id, ctx.idempotency_key, cmd.name, ihash, ctx.request_id, output)
            if s.company is not None and s.company.write_transaction:
                s.company.raw.execute("COMMIT")
            if s.hub is not None and s.hub.writable:
                repaired = _repair_projection(s, ctx)
                if repaired:
                    from bookflow.core.audit import write_event_to
                    write_event_to(s.hub, ctx, cmd.name, "repaired the registry copy of company information", repaired, actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code)
                if s.hub.write_transaction:
                    s.hub.raw.execute("COMMIT")
            return applied
        if business_savepoint and s.company is not None and s.company.write_transaction:
            s.company.raw.execute("RELEASE bookflow_business")
        if s.company is not None and cmd.truth == "company":
            if co_entries and not applied.audited and s.company.write_transaction:
                write_event_to(s.company, ctx, cmd.name, applied.summary, co_entries, actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code)
            if key_db is s.company and ihash and changed:
                idempotency.store(s.company, s.actor.id, ctx.idempotency_key, cmd.name, ihash, ctx.request_id, output)
            if s.company.write_transaction:
                s.company.raw.execute("COMMIT")
            try:
                hub_entries += _repair_projection(s, ctx)
                if hub_tx and hub_entries and not applied.audited and changed:
                    write_event_to(s.hub, ctx, cmd.name, applied.summary, hub_entries, actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code)
                if key_db is s.hub and ihash and changed:
                    idempotency.store(s.hub, s.actor.id, ctx.idempotency_key, cmd.name, ihash, ctx.request_id, output)
                if s.hub.write_transaction:
                    s.hub.raw.execute("COMMIT")
            except (BookflowError, OSError, sqlite3.Error, sa.exc.DBAPIError) as e:
                if s.hub.write_transaction:
                    s.hub.raw.execute("ROLLBACK")
                durable = sorted({t.record_type for t in co_entries})
                raise BookflowError("E_PARTIAL_WRITE", message=f"Saved {', '.join(durable)}; the registry copy was not updated and will be on the next write.", details={"durable": durable, "request_id": ctx.request_id, "cause": getattr(e, "code", "E_IO")})
        else:
            if hub_tx and not applied.audited and changed:
                write_event_to(s.hub, ctx, cmd.name, applied.summary, hub_entries, actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code)
            if key_db is s.hub and ihash and changed:
                idempotency.store(s.hub, s.actor.id, ctx.idempotency_key, cmd.name, ihash, ctx.request_id, output)
            if s.hub is not None and s.hub.write_transaction:
                s.hub.raw.execute("COMMIT")
            if s.company is not None and s.company.write_transaction:
                if co_entries and not applied.audited:
                    write_event_to(s.company, ctx, cmd.name, applied.summary, co_entries, actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code)
                s.company.raw.execute("COMMIT")
    except BaseException:
        for db in (s.company, s.hub):
            if db is not None and db.write_transaction:
                try:
                    db.raw.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
        raise
    project_config = s.pending_config
    s.pending_config = False
    # The intent is already committed. Nested demo commands must not inherit
    # the outer dirty flag, and file projection failure must not skip seeding.
    if applied.after_commit is not None:
        applied.after_commit()
    if project_config:
        try:
            s.config.flush_pending(s.hub)
        except BookflowError as error:
            if error.code == "E_PARTIAL_WRITE":
                error.details["durable"] = sorted({"config", *(entry.record_type for entry in applied.touched)})
                error.details["command"] = cmd.name
                error.message = "The command committed; its settings-file update remains pending. Read the durable effects and request identity before retrying."
            raise
    return applied


@performance.measured("command.projection")
def _repair_projection(s: Session, ctx: Context) -> list:
    """Row 2 plan: inside a real write, converge the hub projection with company_info."""
    from bookflow.core.registry import Touched
    if s.company is None or s.company_row is None or s.dry_run:
        return []
    from bookflow.company.info import read_info
    info = read_info(s.company)
    row = s.company_row
    changes = {k: info[k] for k in ("legal_name", "home_currency") if info.get(k) != row.get(k)}
    if not changes:
        return []
    if not s.hub.write_transaction:
        s.hub.raw.execute("BEGIN IMMEDIATE")
    from bookflow.hub import companies as co
    new, t = co.update(s, row, ctx.interface.value, **changes)
    s.company_row = new
    return [t]


def _upsert_principals(s: Session, ctx: Context) -> None:
    """Blueprint 4.1a: every company write mirrors the actor and the principal into the company."""
    from bookflow.company.info import upsert_principal
    from bookflow.hub.users import find_user
    upsert_principal(s.company, user_id=s.actor.id, username=s.actor.username, display_name=s.actor.display_name, kind=s.actor.kind)
    if ctx.on_behalf_of:
        p = find_user(s, id=ctx.on_behalf_of)
        if p:
            upsert_principal(s.company, user_id=p["id"], username=p["username"], display_name=p["display_name"], kind=p["kind"])


def _run_bootstrap(cmd: Command, inp: BaseModel, ctx: Context, s: Session) -> dict[str, Any]:
    """Commands with their own path: they take the lock themselves, or hold it for their whole run."""
    if cmd.name == "serve":
        from bookflow.commands.host_cmds import run_serve
        return run_serve(cmd, inp, ctx, s)
    from bookflow.commands.hub_cmds import run_init
    return run_init(cmd, inp, ctx, s)
