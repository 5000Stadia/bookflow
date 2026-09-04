"""Packaged chart reads and atomic company chart application."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.common import Empty, WriteOutput
from bookflow.company import charts
from bookflow.company.info import logical_info_values
from bookflow.core.context import Context
from bookflow.core.models import ListOutput
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session


class ChartShowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    template_id: str = Field(description="Packaged chart-template id")


class ChartShowOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str
    version: int
    display_name: str
    description: str
    ordered_account_tree: tuple[charts.AccountTemplate, ...]
    system_role_assignments: dict[str, str]


class ChartApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    template_id: str = Field(description="Packaged chart-template id")


class ChartApplyOutput(WriteOutput):
    template_id: str
    version: int
    prospective_or_created_ids: list[str]
    created_account_count: int


chart_list = command(
    "chart list", scope="hub", description="List packaged chart templates.",
    input_model=Empty, output_model=ListOutput[charts.ChartSummary], capability="chart",
)


@chart_list
def plan_chart_list(inp: Empty, ctx: Context, s: Session) -> Plan:
    items = list(charts.list_manifests())
    return Plan(preview=ListOutput[charts.ChartSummary](items=items, count=len(items)))


chart_show = command(
    "chart show", scope="hub", description="Show one packaged chart template and its ordered accounts.",
    input_model=ChartShowInput, output_model=ChartShowOutput, positional=["template_id"],
    error_codes=["E_RECORD_NOT_FOUND"], capability="chart",
)


@chart_show
def plan_chart_show(inp: ChartShowInput, ctx: Context, s: Session) -> Plan:
    manifest = charts.get_manifest(inp.template_id)
    roles = {account.system_role: account.key for account in manifest.accounts if account.system_role}
    return Plan(preview=ChartShowOutput(
        template_id=manifest.template_id,
        version=manifest.version,
        display_name=manifest.display_name,
        description=manifest.description,
        ordered_account_tree=manifest.accounts,
        system_role_assignments=roles,
    ))


chart_apply = command(
    "chart apply", scope="company", description="Apply one complete packaged chart to a chartless company.",
    input_model=ChartApplyInput, output_model=ChartApplyOutput, writes={"company"},
    required_role="admin", positional=["template_id"], accepts_idempotency_key=True,
    error_codes=["E_RECORD_NOT_FOUND", "E_CHART_INVALID", "E_CHART_EXISTS"], capability="chart",
)


@chart_apply
def plan_chart_apply(inp: ChartApplyInput, ctx: Context, s: Session) -> Plan:
    planned = charts.plan_chart_application(
        s.company, inp.template_id, actor_id=s.actor.id, via=ctx.interface.value,
    )
    output = ChartApplyOutput(
        template_id=planned.manifest.template_id,
        version=planned.manifest.version,
        prospective_or_created_ids=[row["id"] for row in planned.account_rows],
        created_account_count=len(planned.account_rows),
    )
    return Plan(preview=output, data={"chart_plan": planned})


@chart_apply.applier
def apply_chart_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
    planned = plan.data["chart_plan"]
    company_after, accounts = charts.apply_chart_application(s.company, planned)
    company_before = logical_info_values(planned.company_before)
    company_snapshot = logical_info_values(company_after)
    company_before.pop("display_name", None)
    company_snapshot.pop("display_name", None)
    touched = [
        Touched(
            "company_info", planned.company_before["id"], "update",
            planned.company_before["version"], company_after["version"],
            company_snapshot, company_before, db="company",
        ),
        *(
            Touched("account", row["id"], "create", None, 1, row, None, db="company")
            for row in accounts
        ),
    ]
    output = ChartApplyOutput(
        template_id=planned.manifest.template_id,
        version=planned.manifest.version,
        prospective_or_created_ids=[row["id"] for row in accounts],
        created_account_count=len(accounts),
    )
    return Applied(output, touched, f"applied chart {planned.manifest.template_id}")
