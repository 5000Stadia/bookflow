"""Hub-scoped commands for row 1."""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bookflow.commands.common import (CompanySummary, Empty, ListInput, NameInput, OrganizationOutput, WriteOutput,
                                      company_summary, organization_output)
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.fs import check_local
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.lazy import lazy
from bookflow.core.locks import RootLock
from bookflow.core.models import ListOutput, redact_paths
from bookflow.core.money import is_currency
from bookflow.core.moves import move_dir
from bookflow.core.perms import is_private_dir, private_umask
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session, localize, now_iso
from bookflow.storage.paths import (choose_folder_name, name_key, normalize_display_name, read_company_marker,
                                    write_company_marker)

sa = lazy("sqlalchemy")
h = lazy("bookflow.hub.schema")
access = lazy("bookflow.hub.access")
co = lazy("bookflow.hub.companies")
org = lazy("bookflow.hub.organizations")
users = lazy("bookflow.hub.users")
audit = lazy("bookflow.hub.audit")
info = lazy("bookflow.company.info")
rollout = lazy("bookflow.company.rollout")
engine = lazy("bookflow.storage.engine")
migrate = lazy("bookflow.storage.migrate")

VIA = lambda ctx: ctx.interface.value  # noqa: E731


# ---------------------------------------------------------------- init

class InitInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    username: str | None = Field(None, description="Username of the first user; defaults to the OS login", max_length=64)
    display_name: str | None = Field(None, description="Display name of the first user; defaults to the username", max_length=128)


class InitOutput(WriteOutput):
    data_root: str | None
    created: bool
    user_id: str
    hub_admin: bool
    username: str
    display_name: str
    system_user_id: str


def _plan_init(inp: InitInput, ctx: Context, s: Session) -> Plan:  # never called; bootstrap has its own path
    raise NotImplementedError


init_cmd = command("init", scope="hub", description="Create the data root, the system user, and the first hub-admin user mapped from the OS login.",
                   input_model=InitInput, output_model=InitOutput, writes={"hub", "config"}, error_codes=["E_INIT_CONFLICT"], bootstrap=True)(_plan_init)


def run_init(cmd, inp: InitInput, ctx: Context, s: Session) -> dict[str, Any]:
    root = s.data_root
    username = inp.username or s.os_login
    display_name = inp.display_name or username
    if s.dry_run and not (root / "hub.db").exists():
        out = InitOutput(dry_run=True, data_root=str(root), created=True, user_id=new_id(), hub_admin=True, username=username, display_name=display_name, system_user_id=new_id())
        return out.model_dump(mode="json")
    with private_umask():
        if not s.dry_run:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            for sub in ("organizations", "backups", "trash"):
                (root / sub).mkdir(mode=0o700, exist_ok=True)
        with RootLock(root, "init"):
            from bookflow.core.config import Config
            cfg_path = root / "config.toml"
            s.config = Config.load(cfg_path) if cfg_path.exists() else Config(cfg_path)
            with engine.open_database(root / "hub.db", writable=not s.dry_run or not (root / "hub.db").exists(), create=not s.dry_run) as hub:
                s.hub = hub
                if hub.writable:
                    migrate.migrate_to_head(hub, "hub", root / "backups")
                system = users.find_user(s, kind="system")
                humans = [dict(r) for r in hub.conn.execute(sa.select(h.users).where(h.users.c.kind == "human")).mappings().all()]
                mapped = s.config.user_table(s.os_login)
                if system and humans:
                    me = next((u for u in humans if mapped and u["id"] == mapped.get("user_id")), None)
                    if me is not None:
                        if inp.username and inp.username != me["username"]:
                            raise BookflowError("E_INIT_CONFLICT", details={"username": me["username"]})
                        out = InitOutput(dry_run=s.dry_run, data_root=str(root), created=False, user_id=me["id"], hub_admin=bool(me["hub_admin"]), username=me["username"], display_name=me["display_name"], system_user_id=system["id"])
                        return redact_paths(out.model_dump(mode="json"), me["hub_admin"])
                    if (not cfg_path.exists() or (mapped and not any(u["id"] == mapped.get("user_id") for u in humans))) and len(humans) == 1:
                        me = humans[0]
                        if s.dry_run:
                            out = InitOutput(dry_run=True, data_root=str(root), created=False, user_id=me["id"], hub_admin=bool(me["hub_admin"]), username=me["username"], display_name=me["display_name"], system_user_id=system["id"])
                            return out.model_dump(mode="json")
                        s.config.set_user(s.os_login, me["id"])
                        s.config.save()
                        out = InitOutput(data_root=str(root), created=False, user_id=me["id"], hub_admin=bool(me["hub_admin"]), username=me["username"], display_name=me["display_name"], system_user_id=system["id"])
                        return redact_paths(out.model_dump(mode="json"), me["hub_admin"])
                    raise BookflowError("E_NO_ACTOR")
                if s.dry_run:
                    out = InitOutput(dry_run=True, data_root=str(root), created=True, user_id=new_id(), hub_admin=True, username=username, display_name=display_name, system_user_id=new_id())
                    return out.model_dump(mode="json")
                if users.find_user(s, username=username):
                    raise BookflowError("E_INIT_CONFLICT", details={"username": username})
                hub.raw.execute("BEGIN IMMEDIATE")
                try:
                    system = system or users.create_system_user(s, VIA(ctx))
                    me = users.create_human(s, username=username, display_name=display_name, created_by=system["id"], via=VIA(ctx), hub_admin=True)
                    touched = [Touched("user", system["id"], "create", None, 1, system), Touched("user", me["id"], "create", None, 1, me)]
                    audit.write_event(s, ctx, "init", f"initialized data root; first user {username}", touched, actor_id=me["id"], actor_kind="human")
                    hub.raw.execute("COMMIT")
                except BaseException:
                    hub.raw.execute("ROLLBACK")
                    raise
                s.config.set_user(s.os_login, me["id"])
                s.config.save()
                out = InitOutput(data_root=str(root), created=True, user_id=me["id"], hub_admin=bool(me["hub_admin"]), username=username, display_name=display_name, system_user_id=system["id"])
                return out.model_dump(mode="json")


# ---------------------------------------------------------------- upgrade

class UpgradeOutput(WriteOutput):
    hub_migrated: bool
    hub_revision: str
    companies_migrated: list[str]
    companies_skipped: list[str]
    companies_missing: list[str]
    companies_failed: list[dict[str, str]]


upgrade_cmd = command("upgrade", scope="hub", description="Migrate the hub database and every company database the acting user may write to the current schema revision.",
                      input_model=Empty, output_model=UpgradeOutput, writes={"hub", "company"}, error_codes=["E_COMPANY_MISSING"])


@upgrade_cmd
def plan_upgrade(inp: Empty, ctx: Context, s: Session) -> Plan:
    rows = co.list_visible(s)
    writable = [r for r in rows if _may_write(s, r)]
    migrated, skipped, missing, failed = [], [], [], []
    for r in writable:
        p = s.abs_path(r["path"]) / "company.db"
        if not p.exists():
            missing.append(r["id"]); continue
        try:
            state = migrate.classify("company", migrate.current_revision_raw(p))
        except BookflowError as e:
            failed.append({"company_id": r["id"], "code": e.code}); continue
        (skipped if state == "head" else migrated).append(r["id"])
    hub_rev = migrate.current_revision_raw(s.data_root / "hub.db")
    hub_would = s.hub_migrated is not None or migrate.classify("hub", hub_rev) == "behind"
    return Plan(preview=UpgradeOutput(hub_migrated=hub_would, hub_revision=migrate.HEADS["hub"], companies_migrated=migrated, companies_skipped=skipped, companies_missing=missing, companies_failed=failed), data={"rows": writable})


def _may_write(s: Session, row: dict[str, Any]) -> bool:
    acc, role = access.company_role(s, row["id"], row["organization_id"])
    return acc == "hub_admin" or (role is not None and access.ROLE_RANK[role] >= 1)


@upgrade_cmd.applier
def apply_upgrade(plan: Plan, ctx: Context, s: Session) -> Applied:
    migrated, skipped, missing, failed = [], [], [], []
    s.hub.raw.execute("COMMIT")
    for r in plan.data["rows"]:
        p = s.abs_path(r["path"]) / "company.db"
        if not p.exists():
            missing.append(r["id"]); continue
        try:
            with engine.open_database(p, writable=True) as db:
                before, after = migrate.migrate_company(s, ctx, db, s.abs_path(r["path"]), r)
            if before == after:
                skipped.append(r["id"]); continue
            s.hub.raw.execute("BEGIN IMMEDIATE")
            s.hub.conn.execute(h.companies.update().where(h.companies.c.id == r["id"]).values(schema_revision=after))
            audit.write_event(s, ctx, "upgrade", f"migrated company {r['display_name']} from {before} to {after}", [t for t in s.hub_touched if t.record_id == r["id"]])
            s.hub.raw.execute("COMMIT")
            migrated.append(r["id"])
        except BookflowError as e:
            failed.append({"company_id": r["id"], "code": e.code}); break
    out = UpgradeOutput(hub_migrated=s.hub_migrated is not None, hub_revision=migrate.HEADS["hub"], companies_migrated=migrated, companies_skipped=skipped, companies_missing=missing, companies_failed=failed)
    return Applied(out, [], "upgrade run", audited=True)


# ---------------------------------------------------------------- organizations

class OrgSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization: str = Field(description="Organization id or display name")


class OrgRenameInput(OrgSelector):
    name: str = Field(description="New display name")
    move: bool = Field(False, description="Also rename the folder")


class OrgRenameOutput(WriteOutput):
    organization_id: str
    display_name: str
    previous_display_name: str
    path: str | None
    moved: bool


org_new = command("organization new", scope="hub", description="Create an organization, the business entity that holds one or more companies.",
                  input_model=NameInput, output_model=OrganizationOutput, writes={"hub"}, required_role="hub_admin", error_codes=["E_NAME_TAKEN"])


@org_new
def plan_org_new(inp: NameInput, ctx: Context, s: Session) -> Plan:
    name = normalize_display_name(inp.name, field="name")
    if org.name_taken(s, name_key(name)):
        raise BookflowError("E_NAME_TAKEN", details={"name": name})
    folder = choose_folder_name(s.organizations_dir, name)
    at = now_iso()
    row = {"id": new_id(), "display_name": name, "name_key": name_key(name), "path": f"organizations/{folder}", "pending_path": None, "is_demo": False, **users.common(s.actor.id, VIA(ctx), at)}
    return Plan(preview=organization_output(s, row), data={"name": name})


@org_new.applier
def apply_org_new(plan: Plan, ctx: Context, s: Session) -> Applied:
    row, touched = org.create(s, plan.data["name"], VIA(ctx))
    return Applied(organization_output(s, row), [touched], f"created organization {row['display_name']}")


org_list = command("organization list", scope="hub", description="List the organizations the acting user can see.",
                   input_model=ListInput, output_model=ListOutput[OrganizationOutput])


@org_list
def plan_org_list(inp: ListInput, ctx: Context, s: Session) -> Plan:
    ids = access.visible_org_ids(s)
    q = sa.select(h.organizations).order_by(h.organizations.c.name_key)
    if ids is not None:
        q = q.where(h.organizations.c.id.in_(ids) if ids else sa.false())
    items = [organization_output(s, dict(r)) for r in s.hub.conn.execute(q).mappings().all()]
    return Plan(preview=ListOutput[OrganizationOutput](items=items, count=len(items)))


org_show = command("organization show", scope="hub", description="Show one organization.", input_model=OrgSelector,
                   output_model=OrganizationOutput, positional=["organization"])


@org_show
def plan_org_show(inp: OrgSelector, ctx: Context, s: Session) -> Plan:
    from bookflow.core.dispatch import resolve_organization
    return Plan(preview=organization_output(s, resolve_organization(s, inp.organization)))


org_rename = command("organization rename", scope="hub", description="Rename an organization, optionally moving its folder.",
                     input_model=OrgRenameInput, output_model=OrgRenameOutput, writes={"hub"}, required_role="hub_admin",
                     positional=["organization"], error_codes=["E_NAME_TAKEN", "E_RENAME_INCOMPLETE"])


@org_rename
def plan_org_rename(inp: OrgRenameInput, ctx: Context, s: Session) -> Plan:
    from bookflow.core.dispatch import resolve_organization
    row = resolve_organization(s, inp.organization)
    name = normalize_display_name(inp.name)
    if org.name_taken(s, name_key(name), exclude_id=row["id"]):
        raise BookflowError("E_NAME_TAKEN", details={"name": name})
    current_folder = Path(row["path"]).name
    target = choose_folder_name(s.organizations_dir, name, exclude=current_folder) if inp.move else current_folder
    pending = row.get("pending_path")
    will_move = inp.move and (target != current_folder or pending is not None or row["id"] in s.completed_moves)
    preview = OrgRenameOutput(organization_id=row["id"], display_name=name, previous_display_name=row["display_name"], path=str(s.abs_path(f"organizations/{target}" if inp.move else row["path"])), moved=will_move)
    return Plan(preview=preview, data={"row": row, "name": name, "target": f"organizations/{target}", "move": inp.move, "will_move": will_move})


@org_rename.applier
def apply_org_rename(plan: Plan, ctx: Context, s: Session) -> Applied:
    row, name, target = plan.data["row"], plan.data["name"], plan.data["target"]
    changes: dict[str, Any] = {}
    if name != row["display_name"]:
        changes.update(display_name=name, name_key=name_key(name))
    if plan.data["will_move"] and target != row["path"]:
        changes["pending_path"] = target
    if not changes and not row.get("pending_path"):
        return Applied(OrgRenameOutput(organization_id=row["id"], display_name=row["display_name"], previous_display_name=row["display_name"], path=str(s.abs_path(row["path"])), moved=row["id"] in s.completed_moves), [], "no change", audited=True)
    new = org.bump(row, s.actor.id, VIA(ctx), **changes) if changes else row
    if changes:
        s.hub.conn.execute(h.organizations.update().where(h.organizations.c.id == row["id"]).values(**{k: new[k] for k in changes} | {"version": new["version"], "updated_at": new["updated_at"], "updated_by": new["updated_by"], "updated_via": new["updated_via"]}))
        audit.write_event(s, ctx, "organization rename", f"renamed organization {row['display_name']} to {name}" if name != row["display_name"] else f"move requested for organization {name}", [Touched("organization", row["id"], "update", row["version"], new["version"], new)])
    s.hub.raw.execute("COMMIT")
    moved = row["id"] in s.completed_moves
    if plan.data["will_move"] and new.get("pending_path"):
        from bookflow.hub.moves import complete_org_move
        new = complete_org_move(s, ctx, dict(new), VIA(ctx))
        moved = True
    return Applied(OrgRenameOutput(organization_id=row["id"], display_name=new["display_name"], previous_display_name=row["display_name"], path=str(s.abs_path(new["path"])), moved=moved), [], "", audited=True)


# ---------------------------------------------------------------- company new

def _empty_to_none(v):
    return None if isinstance(v, str) and v.strip() == "" else v


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    line1: str | None = Field(None, max_length=200, description="Street line 1")
    line2: str | None = Field(None, max_length=200, description="Street line 2")
    city: str | None = Field(None, max_length=200, description="City")
    state: str | None = Field(None, max_length=200, description="State or province")
    postal_code: str | None = Field(None, max_length=200, description="Postal code")
    country: str | None = Field(None, max_length=200, description="Country; defaults to US")

    @field_validator("*", mode="before")
    @classmethod
    def _blank(cls, v):
        return _empty_to_none(v)


_TAX_SHAPES = {"ein": re.compile(r"^\d{2}-\d{7}$"), "ssn": re.compile(r"^\d{3}-\d{2}-\d{4}$")}


class CompanyNewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    legal_name: str = Field(description="Name on tax forms", max_length=200)
    home_currency: str = Field(description="ISO 4217 code, upper case; immutable after creation", max_length=3)
    organization: str | None = Field(None, description="Organization id or name; defaults when exactly one is visible")
    display_name: str | None = Field(None, description="Name shown in lists; defaults to legal_name", max_length=200)
    chart: Literal["none"] = Field("none", description="Chart of accounts to seed; only none in this version")
    tax_id_kind: Literal["ein", "ssn"] = Field("ein", description="Kind of tax id")
    tax_id: str | None = Field(None, description="NN-NNNNNNN for ein, NNN-NN-NNNN for ssn")
    entity_type: Literal["sole_proprietor", "partnership", "llc", "s_corp", "c_corp", "nonprofit", "other"] = "other"
    income_tax_form: Literal["1040_schedule_c", "1065", "1120", "1120s", "990", "other"] = "other"
    industry: str | None = Field(None, max_length=128, description="Line of business; selects the default chart later")
    contact_name: str | None = Field(None, max_length=128, description="Primary contact person")
    address: Address = Field(default_factory=Address, description="Company address shown on forms")
    legal_address: Address | None = Field(None, description="Address on tax forms; defaults to the company address")
    ship_address: Address | None = Field(None, description="Where goods are received; defaults to the company address")
    phone: str | None = Field(None, max_length=64, description="Main phone")
    fax: str | None = Field(None, max_length=64, description="Fax")
    email: str | None = Field(None, max_length=254, description="Main email; exactly one @")
    website: str | None = Field(None, max_length=254, description="Website")
    fiscal_year_start_month: int = Field(1, ge=1, le=12, description="First month of the fiscal year, 1 to 12")
    tax_year_start_month: int | None = Field(None, ge=1, le=12, description="First month of the tax year; defaults to fiscal_year_start_month")
    report_basis: Literal["accrual", "cash"] = Field("accrual", description="Default basis for reports")
    timezone: str | None = Field(None, description="IANA zone; defaults to the machine's zone")
    recent_activity_window_seconds: int = Field(60, ge=0, description="Window for the recent-activity warning on blind writes")

    @field_validator("display_name", "tax_id", "industry", "contact_name", "phone", "fax", "email", "website", "timezone", "organization", mode="before")
    @classmethod
    def _blank(cls, v):
        return _empty_to_none(v)

    @field_validator("home_currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        if not is_currency(v):
            raise ValueError(f"unknown currency code {v!r}; codes are upper-case ISO 4217")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is not None and v.count("@") != 1:
            raise ValueError("must contain exactly one @")
        return v

    @field_validator("tax_id")
    @classmethod
    def _tax(cls, v: str | None, info) -> str | None:
        kind = info.data.get("tax_id_kind", "ein")
        if v is not None and not _TAX_SHAPES[kind].match(v):
            raise ValueError(f"must match the {kind} shape")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"unknown timezone {v!r}")
        return v

    @field_validator("display_name")
    @classmethod
    def _dn(cls, v: str | None) -> str | None:
        if v is not None and "/" in v:
            raise ValueError("must not contain '/'")
        return v


class CompanyNewOutput(WriteOutput):
    company_id: str
    organization_id: str
    display_name: str
    path: str | None


def _machine_zone() -> str | None:
    try:
        from tzlocal import get_localzone_name
        return get_localzone_name()
    except Exception:
        return None


def _info_columns(inp: CompanyNewInput) -> dict[str, Any]:
    addr = inp.address
    legal = inp.legal_address or addr
    ship = inp.ship_address or addr
    cols: dict[str, Any] = {
        "legal_name": inp.legal_name, "tax_id_kind": inp.tax_id_kind, "tax_id": inp.tax_id, "entity_type": inp.entity_type,
        "income_tax_form": inp.income_tax_form, "industry": inp.industry, "contact_name": inp.contact_name,
        "phone": inp.phone, "fax": inp.fax, "email": inp.email, "website": inp.website,
        "fiscal_year_start_month": inp.fiscal_year_start_month, "tax_year_start_month": inp.tax_year_start_month or inp.fiscal_year_start_month,
        "report_basis": inp.report_basis, "home_currency": inp.home_currency, "timezone": inp.timezone,
        "closing_date": None, "recent_activity_window_seconds": inp.recent_activity_window_seconds, "default_chart": None,
    }
    for prefix, a in (("address", addr), ("legal_address", legal), ("ship_address", ship)):
        for f in ("line1", "line2", "city", "state", "postal_code", "country"):
            v = getattr(a, f)
            if f == "country" and v is None:
                v = "US"
            cols[f"{prefix}_{f}"] = v
    return cols


def _resolve_org_for_new(s: Session, selector: str | None) -> dict[str, Any]:
    from bookflow.core.dispatch import resolve_organization
    if selector is not None:
        row = resolve_organization(s, selector)
    else:
        ids = access.visible_org_ids(s)
        q = sa.select(h.organizations)
        if ids is not None:
            q = q.where(h.organizations.c.id.in_(ids) if ids else sa.false())
        rows = [dict(r) for r in s.hub.conn.execute(q).mappings().all()]
        if len(rows) != 1:
            raise BookflowError("E_ORGANIZATION_REQUIRED", details={"visible": len(rows)})
        row = rows[0]
    if not access.role_satisfies(access.org_role(s, row["id"]), "organization", "admin", s.is_hub_admin):
        raise BookflowError("E_PERMISSION")
    return row


company_new = command("company new", scope="hub", description="Create a company inside an organization: its folder, database, and company information.",
                      input_model=CompanyNewInput, output_model=CompanyNewOutput, writes={"hub", "company"},
                      error_codes=["E_ORGANIZATION_REQUIRED", "E_NAME_TAKEN", "E_ROLLOUT_INCOMPLETE", "E_ORGANIZATION_NOT_FOUND"])


@company_new
def plan_company_new(inp: CompanyNewInput, ctx: Context, s: Session) -> Plan:
    orow = _resolve_org_for_new(s, inp.organization)
    if inp.display_name is None and "/" in inp.legal_name:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "display_name", "problem": "defaults to legal_name, which contains '/'; give display_name"}]})
    display = normalize_display_name(inp.display_name or inp.legal_name, field="display_name" if inp.display_name else "legal_name")
    if co.name_taken(s, orow["id"], name_key(display)):
        raise BookflowError("E_NAME_TAKEN", details={"name": display})
    if inp.timezone is None:
        tz = _machine_zone()
        if tz is None:
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "timezone", "problem": "could not detect the machine's zone; give --timezone"}]})
        inp = inp.model_copy(update={"timezone": tz})
    org_folder = s.abs_path(orow["path"])
    folder = choose_folder_name(org_folder, display)
    cid = new_id()
    preview = CompanyNewOutput(company_id=cid, organization_id=orow["id"], display_name=display, path=str(org_folder / folder))
    return Plan(preview=preview, data={"org": orow, "display": display, "info": _info_columns(inp), "company_id": cid})


@company_new.applier
def apply_company_new(plan: Plan, ctx: Context, s: Session) -> Applied:
    orow, display, cid = plan.data["org"], plan.data["display"], plan.data["company_id"]
    folder = rollout.create_company_folder(s, s.abs_path(orow["path"]), cid, display, plan.data["info"], VIA(ctx), ctx)
    try:
        row, touched = co.register(s, company_id=cid, organization_id=orow["id"], display_name=display, rel_path=s.rel_path(folder),
                                   legal_name=plan.data["info"]["legal_name"], home_currency=plan.data["info"]["home_currency"],
                                   schema_revision=migrate.HEADS["company"], via=VIA(ctx))
    except BookflowError as e:
        raise BookflowError("E_ROLLOUT_INCOMPLETE", details={"state": "unregistered", "path": str(folder), "cause": e.code})
    return Applied(CompanyNewOutput(company_id=cid, organization_id=orow["id"], display_name=display, path=str(folder)), touched, f"created company {display} in {orow['display_name']}")


# ---------------------------------------------------------------- company list / use / attach / detach

company_list = command("company list", scope="hub", description="List the companies the acting user can see.", input_model=ListInput, output_model=ListOutput[CompanySummary])


@company_list
def plan_company_list(inp: ListInput, ctx: Context, s: Session) -> Plan:
    rows = co.list_visible(s)
    names = users.user_names(s, {r["created_by"] for r in rows})
    items = [company_summary(s, r, r["organization_name"], names) for r in rows]
    return Plan(preview=ListOutput[CompanySummary](items=items, count=len(items)))


class CompanySelector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company: str = Field(description="Company id, Organization/Company, or display name")


class CompanyUseOutput(WriteOutput):
    company_id: str
    display_name: str


company_use = command("company use", scope="hub", description="Save a company as this login's default for later commands.",
                      input_model=CompanySelector, output_model=CompanyUseOutput, writes={"config"}, positional=["company"])


@company_use
def plan_company_use(inp: CompanySelector, ctx: Context, s: Session) -> Plan:
    from bookflow.core.dispatch import resolve_company
    row = resolve_company(s, inp.company, "option")
    return Plan(preview=CompanyUseOutput(company_id=row["id"], display_name=row["display_name"]), data={"row": row})


@company_use.applier
def apply_company_use(plan: Plan, ctx: Context, s: Session) -> Applied:
    row = plan.data["row"]
    s.config.set_default_company(s.os_login, row["id"])
    s.pending_config = True
    return Applied(CompanyUseOutput(company_id=row["id"], display_name=row["display_name"]), [], f"set default company to {row['display_name']}")


class AttachInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    path: str = Field(description="Company folder, already inside an organization's folder")
    name: str | None = Field(None, description="Display name to register under; defaults to the folder's copy")


class AttachOutput(WriteOutput):
    company_id: str
    organization_id: str
    display_name: str
    path: str | None


company_attach = command("company attach", scope="hub", description="Register a company folder that already sits inside an organization's folder.",
                         input_model=AttachInput, output_model=AttachOutput, writes={"hub", "company"}, required_role="hub_admin", positional=["path"],
                         error_codes=["E_NOT_IN_ORGANIZATION_DIR", "E_INCOMPLETE_COMPANY", "E_ALREADY_ATTACHED", "E_NAME_TAKEN", "E_ATTACH_INVALID", "E_SCHEMA_UNKNOWN"])


def _read_company_raw(db_path: Path) -> dict[str, Any]:
    import sqlite3
    from bookflow.storage.engine import sqlite_uri
    try:
        conn = sqlite3.connect(sqlite_uri(db_path, "ro"), uri=True)
    except sqlite3.Error:
        raise BookflowError("E_ATTACH_INVALID", details={"check": "database", "path": str(db_path)})
    try:
        try:
            rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            row = conn.execute("SELECT id, display_name, legal_name, home_currency FROM company_info").fetchone()
        except sqlite3.DatabaseError:
            raise BookflowError("E_ATTACH_INVALID", details={"check": "database", "path": str(db_path)})
    finally:
        conn.close()
    if row is None:
        raise BookflowError("E_ATTACH_INVALID", details={"check": "database", "path": str(db_path)})
    return {"revision": rev[0] if rev else None, "id": row[0], "display_name": row[1], "legal_name": row[2], "home_currency": row[3]}


@company_attach
def plan_company_attach(inp: AttachInput, ctx: Context, s: Session) -> Plan:
    given = Path(inp.path).expanduser()
    try:
        folder = given.resolve(strict=True)
    except OSError:
        raise BookflowError("E_NOT_IN_ORGANIZATION_DIR", details={"path": str(given)})
    orgs = {s.abs_path(r["path"]).resolve(): dict(r) for r in s.hub.conn.execute(sa.select(h.organizations)).mappings().all()}
    orow = orgs.get(folder.parent)
    if orow is None or not folder.is_dir():
        raise BookflowError("E_NOT_IN_ORGANIZATION_DIR", details={"path": str(folder)})
    check_local(folder)
    if not is_private_dir(folder):
        raise BookflowError("E_ATTACH_INVALID", details={"check": "mode", "path": str(folder)})
    marker = read_company_marker(folder)
    if marker["state"] != "ready":
        raise BookflowError("E_INCOMPLETE_COMPANY", details={"path": str(folder)})
    db_path = folder / "company.db"
    if not db_path.exists():
        raise BookflowError("E_ATTACH_INVALID", details={"check": "database", "path": str(folder)})
    raw = _read_company_raw(db_path)
    if raw["id"] != marker["company_id"]:
        raise BookflowError("E_ATTACH_INVALID", details={"check": "id", "path": str(folder)})
    state = migrate.classify("company", raw["revision"])
    if state == "unknown":
        raise BookflowError("E_SCHEMA_UNKNOWN", details={"revision": raw["revision"], "path": str(db_path)})
    if co.get(s, raw["id"]):
        raise BookflowError("E_ALREADY_ATTACHED", details={"company_id": raw["id"]})
    name = inp.name or raw["display_name"]
    if not name:
        raise BookflowError("E_ATTACH_INVALID", details={"check": "display_name", "path": str(folder)})
    name = normalize_display_name(name)
    if co.name_taken(s, orow["id"], name_key(name)):
        raise BookflowError("E_NAME_TAKEN", details={"name": name})
    preview = AttachOutput(company_id=raw["id"], organization_id=orow["id"], display_name=name, path=str(folder))
    return Plan(preview=preview, data={"folder": folder, "org": orow, "raw": raw, "name": name, "behind": state == "behind", "rename_copy": name != raw["display_name"]})


@company_attach.applier
def apply_company_attach(plan: Plan, ctx: Context, s: Session) -> Applied:
    folder, orow, raw, name = plan.data["folder"], plan.data["org"], plan.data["raw"], plan.data["name"]
    row, touched = co.register(s, company_id=raw["id"], organization_id=orow["id"], display_name=name, rel_path=s.rel_path(folder),
                               legal_name=raw["legal_name"], home_currency=raw["home_currency"], schema_revision=raw["revision"] or migrate.HEADS["company"],
                               via=VIA(ctx), owner_membership=False)
    if plan.data["behind"] or plan.data["rename_copy"]:
        try:
            with engine.open_database(folder / "company.db", writable=True) as db:
                before, after = migrate.migrate_company(s, ctx, db, folder, row)
                info.write_display_name_copy(db, name)
            if before != after:
                s.hub.conn.execute(h.companies.update().where(h.companies.c.id == raw["id"]).values(schema_revision=after))
            write_company_marker(folder, company_id=raw["id"], state="ready", display_name=name, schema_revision=after or raw["revision"])
        except BookflowError as e:
            s.warnings.append(f"registered, but the database was not migrated ({e.code}); run `bookflow upgrade`")
    return Applied(AttachOutput(company_id=raw["id"], organization_id=orow["id"], display_name=name, path=str(folder)), touched, f"attached company {name} to {orow['display_name']}")


class DetachOutput(WriteOutput):
    company_id: str
    display_name: str
    path: str | None


company_detach = command("company detach", scope="hub", description="Remove a company from the registry, leaving its folder in place.",
                         input_model=CompanySelector, output_model=DetachOutput, writes={"hub", "config"}, required_role="hub_admin", positional=["company"])


@company_detach
def plan_company_detach(inp: CompanySelector, ctx: Context, s: Session) -> Plan:
    from bookflow.core.dispatch import resolve_company
    row = resolve_company(s, inp.company, "option")
    return Plan(preview=DetachOutput(company_id=row["id"], display_name=row["display_name"], path=str(s.abs_path(row["path"]))), data={"row": row})


@company_detach.applier
def apply_company_detach(plan: Plan, ctx: Context, s: Session) -> Applied:
    row = plan.data["row"]
    touched = co.delete_company_rows(s, row["id"])
    return Applied(DetachOutput(company_id=row["id"], display_name=row["display_name"], path=str(s.abs_path(row["path"]))), touched, f"detached company {row['display_name']}")


# ---------------------------------------------------------------- demo reset

class DemoResetOutput(WriteOutput):
    organization_id: str
    company_id: str
    display_name: str
    path: str | None
    trashed_path: str | None


demo_reset = command("demo reset", scope="hub", description="Create, or move to trash and recreate, the demo organization and its demo company from the package seed.",
                     input_model=Empty, output_model=DemoResetOutput, writes={"hub", "company", "config"}, required_role="hub_admin",
                     error_codes=["E_DEMO_RESET_INCOMPLETE", "E_NAME_TAKEN"])


def _load_seed() -> dict[str, Any]:
    import tomllib
    from importlib import resources
    return tomllib.loads(resources.files("bookflow.demo").joinpath("seed.toml").read_text(encoding="utf-8"))


@demo_reset
def plan_demo_reset(inp: Empty, ctx: Context, s: Session) -> Plan:
    seed = _load_seed()
    existing = s.hub.conn.execute(sa.select(h.organizations).where(h.organizations.c.is_demo.is_(True))).mappings().first()
    existing = dict(existing) if existing else None
    if existing is None and org.name_taken(s, name_key(seed["organization"]["display_name"])):
        raise BookflowError("E_NAME_TAKEN", details={"name": seed["organization"]["display_name"]})
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    trash_rel = f"trash/{Path(existing['path']).name}-{stamp}" if existing else None
    org_folder = choose_folder_name(s.organizations_dir, seed["organization"]["display_name"], exclude=Path(existing["path"]).name if existing else None)
    from bookflow.storage.paths import derive_folder_name
    company_folder = derive_folder_name(seed["company"]["display_name"]) if existing else choose_folder_name(s.organizations_dir / org_folder, seed["company"]["display_name"])
    preview = DemoResetOutput(organization_id=new_id(), company_id=new_id(), display_name=seed["company"]["display_name"], path=str(s.organizations_dir / org_folder / company_folder), trashed_path=str(s.abs_path(trash_rel)) if trash_rel else None)
    return Plan(preview=preview, data={"seed": seed, "existing": existing, "trash_rel": trash_rel})


@demo_reset.applier
def apply_demo_reset(plan: Plan, ctx: Context, s: Session) -> Applied:
    seed, existing, trash_rel = plan.data["seed"], plan.data["existing"], plan.data["trash_rel"]
    trashed = None
    if existing:
        pending = existing.get("pending_path") if (existing.get("pending_path") or "").startswith("trash/") else trash_rel
        if existing.get("pending_path") != pending:
            s.hub.conn.execute(h.organizations.update().where(h.organizations.c.id == existing["id"]).values(pending_path=pending))
            audit.write_event(s, ctx, "demo reset", "moving the previous demo organization to trash", [Touched("organization", existing["id"], "update", existing["version"], existing["version"], {**existing, "pending_path": pending})])
        s.hub.raw.execute("COMMIT")
        src, dst = s.abs_path(existing["path"]), s.abs_path(pending)
        n = 1
        while dst.exists():
            n += 1
            dst = s.abs_path(f"{pending}-{n}")
        if src.exists():
            try:
                move_dir(src, dst, company_id=existing["id"])
            except BookflowError as e:
                raise BookflowError("E_DEMO_RESET_INCOMPLETE", details={"organization_id": existing["id"], "cause": e.code, "path": str(dst)})
        trashed = str(dst)
        s.hub.raw.execute("BEGIN IMMEDIATE")
        touched = co.delete_organization_rows(s, existing["id"])
        audit.write_event(s, ctx, "demo reset", "removed previous demo organization", touched)
    orow, t_org = org.create(s, normalize_display_name(seed["organization"]["display_name"]), VIA(ctx), is_demo=True)
    inp = CompanyNewInput.model_validate({k: v for k, v in seed["company"].items()} | {"organization": orow["id"]})
    display = normalize_display_name(inp.display_name or inp.legal_name)
    if inp.timezone is None:
        inp = inp.model_copy(update={"timezone": _machine_zone() or "UTC"})
    cid = new_id()
    folder = rollout.create_company_folder(s, s.abs_path(orow["path"]), cid, display, _info_columns(inp), VIA(ctx), ctx)
    row, t_co = co.register(s, company_id=cid, organization_id=orow["id"], display_name=display, rel_path=s.rel_path(folder), legal_name=inp.legal_name,
                            home_currency=inp.home_currency, schema_revision=migrate.HEADS["company"], via=VIA(ctx), is_demo=True)
    return Applied(DemoResetOutput(organization_id=orow["id"], company_id=cid, display_name=display, path=str(folder), trashed_path=trashed), [t_org, *t_co], f"reset demo: {orow['display_name']} / {display}")


