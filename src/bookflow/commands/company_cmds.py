"""Company-scoped commands for row 1."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.common import CompanySummary, Empty, WriteOutput, common_out, company_summary
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.lazy import lazy
from bookflow.core.moves import move_dir
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session, localize
from bookflow.storage.paths import choose_folder_name, name_key, normalize_display_name, write_company_marker

h = lazy("bookflow.hub.schema")
co = lazy("bookflow.hub.companies")
org = lazy("bookflow.hub.organizations")
audit = lazy("bookflow.hub.audit")
cinfo = lazy("bookflow.company.info")


class CompanyInfoOut(BaseModel):
    model_config = ConfigDict(extra="allow")


class CompanyShowOutput(CompanySummary):
    info: dict[str, Any]
    info_created_by_name: str | None
    info_updated_by_name: str | None


company_show = command("company show", scope="company", description="Show the selected company: registration, company information, and who created it.",
                       input_model=Empty, output_model=CompanyShowOutput, required_role="member", error_codes=["E_COMPANY_MISSING"])


@company_show
def plan_company_show(inp: Empty, ctx: Context, s: Session) -> Plan:
    row = s.company_row
    orow = org.get(s, row["organization_id"])
    summary = company_summary(s, row, orow["display_name"])
    info = cinfo.read_info(s.company)
    info.pop("display_name", None)
    for k in ("created_at", "updated_at"):
        info[k] = localize(s, info[k])
    names = cinfo.principal_names(s.company, {info["created_by"], info["updated_by"]})
    return Plan(preview=CompanyShowOutput(**summary.model_dump(), info=info, info_created_by_name=names.get(info["created_by"]), info_updated_by_name=names.get(info["updated_by"])))


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
