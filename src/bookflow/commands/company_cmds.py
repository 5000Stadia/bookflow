"""Company-scoped commands for row 1."""

from __future__ import annotations

from bookflow.company.tax_policy import Policy

from pathlib import Path
from typing import Any

from pydantic import model_serializer, BaseModel, ConfigDict, Field

from bookflow.commands.common import CompanySummary, Empty, WriteOutput, common_out, company_summary
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.lazy import lazy
from bookflow.core.moves import move_dir
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session, localize, now_iso
from bookflow.storage.paths import choose_folder_name, name_key, normalize_display_name, write_company_marker

sa = lazy("sqlalchemy")
h = lazy("bookflow.hub.schema")
co = lazy("bookflow.hub.companies")
org = lazy("bookflow.hub.organizations")
audit = lazy("bookflow.hub.audit")
cinfo = lazy("bookflow.company.info")


class CompanyInfoOut(BaseModel):
    model_config = ConfigDict(extra="allow")


class CompanyShowOutput(CompanySummary):
    info: dict[str, Any]
    preference_changes: list[dict[str, Any]]
    info_version: int
    info_created_by_name: str | None
    info_updated_by_name: str | None
    editing_by: list[dict[str, Any]] = []


company_show = command("company show", scope="company", description="Show the selected company: registration, company information, and who created it.",
                       input_model=Empty, output_model=CompanyShowOutput, required_role="member", error_codes=["E_COMPANY_MISSING"])


@company_show
def plan_company_show(inp: Empty, ctx: Context, s: Session) -> Plan:
    from bookflow.company import work_preferences
    row = s.company_row
    orow = org.get(s, row["organization_id"])
    info = cinfo.read_info(s.company)
    # Registry names/paths belong to the hub snapshot. Company-owned values
    # come from the company snapshot, not a potentially lagging hub copy.
    summary = company_summary(s, {**row, "legal_name": info["legal_name"], "home_currency": info["home_currency"]}, orow["display_name"])
    info.pop("display_name", None)
    for k in ("created_at", "updated_at"):
        info[k] = localize(s, info[k])
    names = cinfo.principal_names(s.company, {info["created_by"], info["updated_by"]})
    return Plan(preview=CompanyShowOutput(**summary.model_dump(), info=info, info_version=info["version"], info_created_by_name=names.get(info["created_by"]), info_updated_by_name=names.get(info["updated_by"]),
                                          preference_changes=work_preferences.changes(s, work_preferences.FIELDS),
                                          editing_by=_editing_by(s, "company_info", row["id"])))


class RenameInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(description="New display name")
    move: bool = Field(False, description="Also rename the folder")


class RenameOutput(WriteOutput):
    company_id: str
    display_name: str
    previous_display_name: str
    path: str | None
    moved: bool


company_rename = command("company rename", scope="company", description="Rename the selected company, optionally moving its folder.",
                         input_model=RenameInput, output_model=RenameOutput, writes={"hub", "company"}, required_role="admin",
                         error_codes=["E_NAME_TAKEN", "E_RENAME_INCOMPLETE", "E_COMPANY_MISSING"])


@company_rename
def plan_company_rename(inp: RenameInput, ctx: Context, s: Session) -> Plan:
    row = s.company_row
    name = normalize_display_name(inp.name, field="name")
    if co.name_taken(s, row["organization_id"], name_key(name), exclude_id=row["id"]):
        raise BookflowError("E_NAME_TAKEN", details={"name": name})
    orow = org.get(s, row["organization_id"])
    org_folder = s.abs_path(orow["path"])
    current_folder = Path(row["path"]).name
    target = choose_folder_name(org_folder, name, exclude=current_folder) if inp.move else current_folder
    will_move = inp.move and (target != current_folder or row.get("pending_path") is not None or row["id"] in s.completed_moves)
    target_rel = s.rel_path(org_folder / target)
    return Plan(preview=RenameOutput(company_id=row["id"], display_name=name, previous_display_name=row["display_name"], path=str(org_folder / target), moved=will_move),
                data={"name": name, "target_rel": target_rel, "will_move": will_move})


@company_rename.applier
def apply_company_rename(plan: Plan, ctx: Context, s: Session) -> Applied:
    row, name, target_rel, will_move = s.company_row, plan.data["name"], plan.data["target_rel"], plan.data["will_move"]
    via = ctx.interface.value
    changes: dict[str, Any] = {}
    if name != row["display_name"]:
        changes.update(display_name=name, name_key=name_key(name))
    if will_move and target_rel != row["path"] and not row.get("pending_path"):
        changes["pending_path"] = target_rel
    if not changes and not row.get("pending_path"):
        s.company.raw.execute("ROLLBACK")
        return Applied(RenameOutput(company_id=row["id"], display_name=row["display_name"], previous_display_name=row["display_name"], path=str(s.abs_path(row["path"])), moved=row["id"] in s.completed_moves), [], "no change", audited=True)
    new = row
    if changes:
        new, t = co.update(s, row, via, **changes)
        audit.write_event(s, ctx, "company rename", f"renamed company {row['display_name']} to {name}" if name != row["display_name"] else f"move requested for company {name}", [t])
    s.hub.raw.execute("COMMIT")
    try:
        cinfo.write_display_name_copy(s.company, name)
        s.company.raw.execute("COMMIT")
        write_company_marker(s.abs_path(new["path"]), company_id=row["id"], state="ready", display_name=name, schema_revision=new["schema_revision"])
    except (BookflowError, OSError) as e:  # informational copies; the next writable open rewrites them (blueprint 3.1)
        if s.company.raw.in_transaction:
            s.company.raw.execute("ROLLBACK")
        s.warnings.append(f"renamed, but the folder's display-name copy was not updated ({getattr(e, 'code', 'E_IO')}); it will be on the next write")
    moved = row["id"] in s.completed_moves
    if will_move and new.get("pending_path"):
        from bookflow.hub.moves import complete_company_move
        s.close_company()
        new = complete_company_move(s, ctx, dict(new), via)
        moved = True
    return Applied(RenameOutput(company_id=row["id"], display_name=new["display_name"], previous_display_name=row["display_name"], path=str(s.abs_path(new["path"])), moved=moved), [], "", audited=True)


# ---------------------------------------------------------------- company update, directives, presence (row 2)

from typing import Literal

from pydantic import field_validator

from bookflow.commands.hub_cmds import Address, _TAX_SHAPES, _empty_to_none
from bookflow.core.models import ListOutput, WriteOutput as _WriteOutput
from bookflow.core.versioning import check_update, current_writer, history_from_entries

versioning_audit = lazy("bookflow.core.audit")
directives = lazy("bookflow.company.directives")
presence = lazy("bookflow.company.presence")
cschema = lazy("bookflow.company.schema")
users = lazy("bookflow.hub.users")

ADDRESS_FIELDS = ("line1", "line2", "city", "state", "postal_code", "country")


class UpdateOutput(_WriteOutput):
    company_id: str
    version: int
    changed_fields: list[str]
    merged_over_versions: list[int]
    previous_version: int | None = None
    previous_updated_by: str | None = None
    previous_updated_by_name: str | None = None
    previous_on_behalf_of: str | None = None
    previous_on_behalf_of_name: str | None = None
    previous_updated_via: str | None = None
    seconds_since_previous_update: float | None = None
    recent_concurrent_activity: bool = False


class CompanyUpdateInput(BaseModel):
    @model_serializer(mode='wrap')
    def legacy_tax_request(self, handler):
        values = handler(self)
        if 'sales_tax_calculation' not in self.model_fields_set:
            values.pop('sales_tax_calculation', None)
        return values

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int | None = Field(None, ge=1, description="The info_version you read; omit for a blind write")
    attachment_max_bytes: int | None = Field(None, strict=True, ge=1, le=100000000, description="Maximum actual upload bytes per file; default 25,000,000.")
    legal_name: str | None = Field(None, max_length=200, description="Name on tax forms; also updates the registry copy")
    tax_id_kind: Literal["ein", "ssn"] | None = Field(None, description="Kind of tax id")
    tax_id: str | None = Field(None, description="NN-NNNNNNN for ein, NNN-NN-NNNN for ssn")
    entity_type: Literal["sole_proprietor", "partnership", "llc", "s_corp", "c_corp", "nonprofit", "other"] | None = None
    income_tax_form: Literal["1040_schedule_c", "1065", "1120", "1120s", "990", "other"] | None = None
    industry: str | None = Field(None, max_length=128, description="Line of business")
    contact_name: str | None = Field(None, max_length=128, description="Primary contact person")
    address: Address | None = Field(None, description="Company address; children given are patched, others kept")
    legal_address: Address | None = Field(None, description="Address on tax forms")
    ship_address: Address | None = Field(None, description="Where goods are received")
    phone: str | None = Field(None, max_length=64, description="Main phone")
    fax: str | None = Field(None, max_length=64, description="Fax")
    email: str | None = Field(None, max_length=254, description="Main email; exactly one @")
    website: str | None = Field(None, max_length=254, description="Website")
    fiscal_year_start_month: int | None = Field(None, ge=1, le=12, description="First month of the fiscal year")
    tax_year_start_month: int | None = Field(None, ge=1, le=12, description="First month of the tax year")
    report_basis: Literal["accrual", "cash"] | None = Field(None, description="Default basis for reports")
    timezone: str | None = Field(None, description="IANA zone")
    closing_date: str | None = Field(None, description="Books closed through this date, YYYY-MM-DD")
    recent_activity_window_seconds: int | None = Field(None, ge=0, description="Window for the recent-activity warning")
    use_account_numbers: bool | None = Field(None, description="Show account numbers in forms, tables, and pickers")
    show_lowest_subaccount_only: bool | None = Field(None, description="Use leaf account names in pickers")
    required_employee_profile_fields: list[list[str]] | None = Field(None, description="Ordered alternative registered employee-completeness paths")
    use_classes: bool | None = Field(None, description="Enable class controls on later forms")
    prompt_for_class: bool | None = Field(None, description="Require or warn for a class on later forms")
    enable_price_levels: bool | None = Field(None, description="Enable price-level controls on later sales forms")
    units_of_measure_mode: Literal["disabled", "single_unit_per_item", "multiple_related_units"] | None = None
    sales_tax_calculation: Policy = Field(None, description="Default captured tax calculation; omission preserves, null rejects")
    sales_tax_enabled: bool | None = Field(None, description="Enable sales-tax controls on later forms")
    default_sales_tax_item_id: str | None = Field(None, description="Active sales-tax item or group default")
    sales_tax_liability_basis: Literal["invoice_date", "payment_receipt"] | None = None
    sales_tax_remittance_frequency: Literal["monthly", "quarterly", "annually"] | None = None
    default_ship_method_id: str | None = Field(None, description="Active default ship method")
    free_on_board: str | None = Field(None, max_length=128, description="Default free-on-board location")
    order_printable_checks: bool | None = Field(None, description="Company default for ordering printable checks")
    estimates_enabled: bool = Field(None, strict=True, description="Enable new estimates; omission preserves the saved setting; null rejects")
    progress_billing_enabled: bool = Field(None, strict=True, description="Enable partial work billing; disabling retains remaining-line and bounded recovery billing; omission preserves, null rejects")
    close_estimates_after_billing: bool = Field(None, strict=True, description="Make estimates inactive after final positive net billing; effective only while progress billing is disabled; omission preserves, null rejects")
    automatically_apply_payments: bool = Field(None, strict=True, description="Suggest exact-match then oldest invoice allocations; omission preserves, null rejects")
    automatically_calculate_payments: bool = Field(None, strict=True, description="Calculate selected invoice amounts; omission preserves, null rejects")
    use_undeposited_funds_for_payments: bool = Field(None, strict=True, description="Default receipts to Undeposited Funds; omission preserves, null rejects")

    @field_validator("legal_name", "tax_id", "industry", "contact_name", "phone", "fax", "email", "website", "timezone", "closing_date", "free_on_board", mode="before")
    @classmethod
    def _blank(cls, v):
        return _empty_to_none(v)


SCALARS = [f for f in CompanyUpdateInput.model_fields if f not in ("expected_version", "address", "legal_address", "ship_address")]
NOT_NULLABLE = {
    "automatically_apply_payments", "automatically_calculate_payments", "use_undeposited_funds_for_payments",
    "estimates_enabled", "progress_billing_enabled", "close_estimates_after_billing",
    "legal_name", "tax_id_kind", "entity_type", "income_tax_form", "fiscal_year_start_month",
    "tax_year_start_month", "report_basis", "timezone", "recent_activity_window_seconds",
    "use_account_numbers", "show_lowest_subaccount_only", "required_employee_profile_fields",
    "use_classes", "prompt_for_class", "enable_price_levels", "units_of_measure_mode",
    "sales_tax_enabled", "sales_tax_liability_basis", "sales_tax_remittance_frequency",
    "order_printable_checks", "attachment_max_bytes",
}


def _merged_row(current: dict[str, Any], inp: CompanyUpdateInput) -> tuple[dict[str, Any], set[str]]:
    """Patch the stored row with the input: a key absent leaves the field, a key set to null clears it (blueprint: one JSON shape on every surface)."""
    new = dict(current)
    fields_set = set(inp.model_fields_set) - {"expected_version"}
    for f in SCALARS:
        if f in fields_set:
            new[f] = getattr(inp, f)
    for prefix in ("address", "legal_address", "ship_address"):
        if prefix in fields_set:
            sub = getattr(inp, prefix)
            if sub is None:
                for child in ADDRESS_FIELDS:
                    new[f"{prefix}_{child}"] = None
            else:
                for child in sub.model_fields_set:
                    new[f"{prefix}_{child}"] = getattr(sub, child)
    return new, fields_set


def _validate_merged(new: dict[str, Any], s: Session, changed: set[str]) -> None:
    fields = []
    if new.get("tax_id") is not None and not _TAX_SHAPES[new.get("tax_id_kind", "ein")].match(new["tax_id"]):
        fields.append({"field": "tax_id", "problem": f"must match the {new.get('tax_id_kind')} shape"})
    if new.get("email") is not None and new["email"].count("@") != 1:
        fields.append({"field": "email", "problem": "must contain exactly one @"})
    if new.get("timezone") is not None:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(new["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            fields.append({"field": "timezone", "problem": f"unknown timezone {new['timezone']!r}"})
    if new.get("closing_date") is not None:
        from datetime import date
        try:
            date.fromisoformat(new["closing_date"])
        except ValueError:
            fields.append({"field": "closing_date", "problem": "must be YYYY-MM-DD"})
    for f in ("legal_name", "timezone"):
        if new.get(f) in (None, ""):
            fields.append({"field": f, "problem": "must not be empty"})
    if fields:
        raise BookflowError("E_VALIDATION", details={"fields": fields})
    if new["prompt_for_class"] and not new["use_classes"]:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "prompt_for_class", "problem": "requires use_classes"}]})
    if not new["sales_tax_enabled"] and new.get("default_sales_tax_item_id") is not None:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "sales_tax_enabled", "problem": "clear default_sales_tax_item_id before disabling sales tax"}]})

    for field, table in (("default_sales_tax_item_id", cschema.items), ("default_ship_method_id", cschema.ship_methods)):
        if field not in changed or new.get(field) is None:
            continue
        row = s.company.conn.execute(sa.select(table).where(table.c.id == new[field])).mappings().first()
        if row is None:
            raise BookflowError("E_RECORD_NOT_FOUND", details={"record_type": table.name, "selector": new[field], "suggestions": []})
        if not row["active"]:
            raise BookflowError("E_INACTIVE_REFERENCE", details={"field": field, "record_id": new[field]})
        if field == "default_sales_tax_item_id" and row["type"] not in ("sales_tax_item", "sales_tax_group"):
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": "must name a sales-tax item or group"}]})

    if "units_of_measure_mode" in changed and new["units_of_measure_mode"] == "disabled":
        blockers = s.company.conn.execute(
            sa.select(cschema.items.c.id, cschema.items.c.full_name)
            .where(cschema.items.c.active.is_(True), cschema.items.c.unit_of_measure_set_id.is_not(None))
            .order_by(cschema.items.c.id)
        ).mappings().all()
        if blockers:
            raise BookflowError("E_ACTIVE_DEPENDENTS", details={"field": "units_of_measure_mode", "records": [dict(row) for row in blockers]})

    if "required_employee_profile_fields" in changed:
        new["required_employee_profile_fields"] = cinfo.validate_employee_profile_fields(
            new["required_employee_profile_fields"], s.company,
        )


company_update = command("company update", scope="company", description="Update the selected company's information; versioned, blind, or merged per the concurrency rules.",
                         input_model=CompanyUpdateInput, output_model=UpdateOutput, writes={"company", "hub"}, required_role="admin", truth="company", clearable=True,
                         error_codes=["E_VERSION_CONFLICT", "E_PARTIAL_WRITE", "E_DIRECTIVE_NOT_FOUND", "E_DIRECTIVE_INACTIVE"], version_source=("company show", None, "info_version"))


@company_update
def plan_company_update(inp: CompanyUpdateInput, ctx: Context, s: Session) -> Plan:
    current = cinfo.read_info(s.company)
    nulled = [f for f in inp.model_fields_set if f not in ("expected_version",) and getattr(inp, f) is None and f in NOT_NULLABLE]
    if nulled:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": f, "problem": "cannot be cleared; it always has a value"} for f in nulled]})
    new, fields_set = _merged_row(current, inp)
    changed = {f for f in fields_set if any(new.get(col) != current.get(col) for col in ([f] if f not in ("address", "legal_address", "ship_address") else [f"{f}_{c}" for c in ADDRESS_FIELDS]))}
    if changed:
        _validate_merged(new, s, changed)
    window = current.get("recent_activity_window_seconds", 60)
    writer = current_writer(s.company, "company_info", current["id"], current)
    names = cinfo.principal_names(s.company, {x for x in ((writer.updated_by, writer.on_behalf_of) if writer else ()) if x})
    if writer:
        writer.updated_by_name = names.get(writer.updated_by)
        writer.on_behalf_of_name = names.get(writer.on_behalf_of)
    meta = check_update(current_version=current["version"], current_updated_at=current["updated_at"], current_writer=writer, changes=changed,
                        expected_version=inp.expected_version, history_since=lambda v: history_from_entries(s.company, "company_info", current["id"], v, versioning_audit.decode_snapshot),
                        actor_id=s.actor.id, window_seconds=window)
    warnings = []
    if meta.merged_over_versions:
        warnings.append(f"merged over versions {meta.merged_over_versions}; those changes touched other fields")
    if meta.recent_concurrent_activity:
        who = meta.previous_updated_by_name or meta.previous_updated_by
        if meta.previous_on_behalf_of_name:
            who = f"{who} on behalf of {meta.previous_on_behalf_of_name}"
        warnings.append(f"{who} changed this record {meta.seconds_since_previous_update} s ago through {meta.previous_updated_via}")
    preview = UpdateOutput(company_id=current["id"], warnings=warnings, **{k: v for k, v in meta.as_dict().items()})
    return Plan(preview=preview, data={"current": current, "new": new, "changed": sorted(changed), "meta": meta, "warnings": warnings})


@company_update.applier
def apply_company_update(plan: Plan, ctx: Context, s: Session) -> Applied:
    current, new, changed, meta = plan.data["current"], plan.data["new"], plan.data["changed"], plan.data["meta"]
    if not changed:
        return Applied(UpdateOutput(company_id=current["id"], **meta.as_dict()), [], "no change")
    via = ctx.interface.value
    new = {**new, "version": meta.version, "updated_at": now_iso(), "updated_by": s.actor.id, "updated_via": via}
    logical_cols = {k: v for k, v in new.items() if k in cschema.company_info.c}
    stored_cols = dict(logical_cols)
    stored_cols["required_employee_profile_fields"] = cinfo.encode_employee_profile_fields(new["required_employee_profile_fields"])
    s.company.conn.execute(cschema.company_info.update().where(cschema.company_info.c.id == current["id"]).values(**stored_cols))
    snap = {k: v for k, v in logical_cols.items() if k != "display_name"}
    touched = [Touched("company_info", current["id"], "update", current["version"], meta.version, snap, db="company")]
    summary = "updated company info: " + ", ".join(changed)
    return Applied(UpdateOutput(company_id=current["id"], warnings=plan.data["warnings"], **meta.as_dict()), touched, summary)


# ---------------------------------------------------------------- directives

class DirectiveAddInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(description="The standing instruction, as the principal gave it", min_length=1, max_length=1000)


class DirectiveSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    directive: str = Field(description="Directive id or code, e.g. SI-3")


class DirectiveListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_inactive: bool = Field(False, description="Include deactivated directives")


class DirectiveOut(BaseModel):
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str
    code: str
    text: str
    given_by: str
    given_by_name: str | None
    recorded_by: str
    recorded_by_name: str | None
    active: bool
    deactivated_at: str | None
    deactivated_by: str | None
    deactivated_by_name: str | None
    access: str | None = None
    role: str | None = None
    editing_by: list[dict[str, Any]] = []


class DirectiveAddOutput(_WriteOutput):
    idempotent_replay: bool = False
    directive: DirectiveOut


def _names(s: Session, ids: set[str]) -> dict[str, str]:
    """Names through principals; the session's actor and principal are known even before their upsert (dry runs)."""
    names = cinfo.principal_names(s.company, ids)
    if s.actor and s.actor.id in ids and s.actor.id not in names:
        names[s.actor.id] = s.actor.display_name
    return names


def _directive_out(s: Session, row: dict[str, Any], with_presence: bool = False) -> DirectiveOut:
    names = _names(s, {x for x in (row["given_by"], row["recorded_by"], row.get("deactivated_by")) if x})
    if row["given_by"] not in names:
        hub_names = users.user_names(s, {row["given_by"]})
        names.update(hub_names)
    from bookflow.hub import access
    acc, role = access.company_role(s, s.company_row["id"], s.company_row["organization_id"])
    return DirectiveOut(**{k: (localize(s, row[k]) if k in ("created_at", "updated_at", "deactivated_at") else row[k]) for k in DirectiveOut.model_fields if k in row and k != "editing_by"},
                        given_by_name=names.get(row["given_by"]), recorded_by_name=names.get(row["recorded_by"]), deactivated_by_name=names.get(row.get("deactivated_by")), access=acc, role=role,
                        editing_by=_editing_by(s, "directive", row["id"]) if with_presence else [])


directive_add = command("directive add", scope="company", description="Record a standing instruction that later writes can cite by code instead of repeating a reason.",
                        input_model=DirectiveAddInput, output_model=DirectiveAddOutput, writes={"company"}, required_role="standard", truth="company", accepts_idempotency_key=True)


@directive_add
def plan_directive_add(inp: DirectiveAddInput, ctx: Context, s: Session) -> Plan:
    if s.actor.kind == "human":
        given_by = s.actor.id
    elif ctx.on_behalf_of:
        given_by = ctx.on_behalf_of
    else:
        raise BookflowError("E_PERMISSION", message="An agent without a principal cannot record a directive.", details={"capability": "directive", "required_role": "standard", "reason": "agent without a principal"})
    n = s.company.conn.execute(sa.select(cschema.sequences.c.next_number).where(cschema.sequences.c.name == "directive")).scalar_one()
    row = {"id": new_id(), "code": f"SI-{n}", "text": inp.text, "given_by": given_by, "recorded_by": s.actor.id, "active": True, "deactivated_at": None, "deactivated_by": None,
           "version": 1, "created_at": now_iso(), "created_by": s.actor.id, "created_via": ctx.interface.value, "updated_at": now_iso(), "updated_by": s.actor.id, "updated_via": ctx.interface.value}
    return Plan(preview=DirectiveAddOutput(directive=_directive_out(s, row)), data={"given_by": given_by})


@directive_add.applier
def apply_directive_add(plan: Plan, ctx: Context, s: Session) -> Applied:
    row, t = directives.add(s.company, text=plan.preview.directive.text, given_by=plan.data["given_by"], recorded_by=s.actor.id, via=ctx.interface.value)
    t.db = "company"
    return Applied(DirectiveAddOutput(directive=_directive_out(s, row)), [t], f"recorded directive {row['code']}")


directive_list = command("directive list", scope="company", description="List this company's standing instructions.", input_model=DirectiveListInput, output_model=ListOutput[DirectiveOut], required_role="member")


@directive_list
def plan_directive_list(inp: DirectiveListInput, ctx: Context, s: Session) -> Plan:
    items = [_directive_out(s, r) for r in directives.list_all(s.company, inp.include_inactive)]
    return Plan(preview=ListOutput[DirectiveOut](items=items, count=len(items)))


directive_show = command("directive show", scope="company", description="Show one standing instruction.", input_model=DirectiveSelector, output_model=DirectiveOut, required_role="member", positional=["directive"], error_codes=["E_DIRECTIVE_NOT_FOUND"])


@directive_show
def plan_directive_show(inp: DirectiveSelector, ctx: Context, s: Session) -> Plan:
    return Plan(preview=_directive_out(s, directives.resolve(s.company, inp.directive), with_presence=True))


class DirectiveDeactivateOutput(_WriteOutput):
    directive: DirectiveOut


directive_deactivate = command("directive deactivate", scope="company", description="Deactivate a standing instruction so it can no longer be cited.",
                               input_model=DirectiveSelector, output_model=DirectiveDeactivateOutput, writes={"company"}, required_role="standard", truth="company",
                               positional=["directive"], error_codes=["E_DIRECTIVE_NOT_FOUND"])


@directive_deactivate
def plan_directive_deactivate(inp: DirectiveSelector, ctx: Context, s: Session) -> Plan:
    row = directives.resolve(s.company, inp.directive)
    if not row["active"]:
        preview = dict(row)  # already inactive: the real run changes nothing and returns the row as it is
    else:
        at = now_iso()
        preview = dict(row, active=False, deactivated_at=at, deactivated_by=s.actor.id, version=row["version"] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    return Plan(preview=DirectiveDeactivateOutput(directive=_directive_out(s, preview)), data={"row": row})


@directive_deactivate.applier
def apply_directive_deactivate(plan: Plan, ctx: Context, s: Session) -> Applied:
    row = plan.data["row"]
    if not row["active"]:
        return Applied(DirectiveDeactivateOutput(directive=_directive_out(s, row)), [], "no change")
    new, t = directives.deactivate(s.company, row, s.actor.id, ctx.interface.value)
    t.db = "company"
    return Applied(DirectiveDeactivateOutput(directive=_directive_out(s, new)), [t], f"deactivated directive {row['code']}")


# ---------------------------------------------------------------- presence

RECORD_TYPES = ("company_info", "directive")


class PresenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    record_type: Literal["company_info", "directive"] = Field(description="Record type")
    record_id: str = Field(description="Record id")


class PresenceOutput(BaseModel):
    record_type: str
    record_id: str
    editing_by: list[dict[str, Any]]


def _record_exists(s: Session, record_type: str, record_id: str) -> str:
    """Validate the target and return its canonical id (a directive code becomes its id)."""
    if record_type == "company_info":
        if record_id.upper() != s.company_row["id"]:
            raise BookflowError("E_RECORD_NOT_FOUND", details={"record_type": record_type, "suggestions": [s.company_row["id"]]})
        return s.company_row["id"]
    try:
        return directives.resolve(s.company, record_id)["id"]
    except BookflowError as e:
        raise BookflowError("E_RECORD_NOT_FOUND", details={"record_type": record_type, "suggestions": e.details.get("suggestions", [])})


def _editing_by(s: Session, record_type: str, record_id: str) -> list[dict[str, Any]]:
    rows = presence.live_for(s.company, record_type, record_id.upper())
    names = cinfo.principal_names(s.company, {r["user_id"] for r in rows})
    return [{"user_id": r["user_id"], "name": names.get(r["user_id"]), "interface": r["interface"], "since": localize(s, r["started_at"])} for r in rows]


presence_set = command("presence set", scope="company", description="Say that you are editing a record, so other people's screens can show it; advisory only, never blocks a write.",
                       input_model=PresenceInput, output_model=PresenceOutput, writes={"company"}, kind="advisory", required_role="standard", positional=["record_type", "record_id"], error_codes=["E_RECORD_NOT_FOUND"])


@presence_set
def plan_presence_set(inp: PresenceInput, ctx: Context, s: Session) -> Plan:
    rid = _record_exists(s, inp.record_type, inp.record_id)
    return Plan(preview=PresenceOutput(record_type=inp.record_type, record_id=rid, editing_by=[]))


@presence_set.applier
def apply_presence_set(plan: Plan, ctx: Context, s: Session) -> Applied:
    p = plan.preview
    presence.set_presence(s.company, record_type=p.record_type, record_id=p.record_id, user_id=s.actor.id, interface=ctx.interface.value)
    return Applied(PresenceOutput(record_type=p.record_type, record_id=p.record_id, editing_by=_editing_by(s, p.record_type, p.record_id)), [], "")


presence_clear = command("presence clear", scope="company", description="Say that you stopped editing a record.",
                         input_model=PresenceInput, output_model=PresenceOutput, writes={"company"}, kind="advisory", required_role="standard", positional=["record_type", "record_id"], error_codes=["E_RECORD_NOT_FOUND"])


@presence_clear
def plan_presence_clear(inp: PresenceInput, ctx: Context, s: Session) -> Plan:
    rid = _record_exists(s, inp.record_type, inp.record_id)
    return Plan(preview=PresenceOutput(record_type=inp.record_type, record_id=rid, editing_by=[]))


@presence_clear.applier
def apply_presence_clear(plan: Plan, ctx: Context, s: Session) -> Applied:
    p = plan.preview
    presence.clear_presence(s.company, record_type=p.record_type, record_id=p.record_id, user_id=s.actor.id, interface=ctx.interface.value)
    return Applied(PresenceOutput(record_type=p.record_type, record_id=p.record_id, editing_by=_editing_by(s, p.record_type, p.record_id)), [], "")
