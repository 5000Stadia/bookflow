"""Complete routed lifecycle for Row 5 chart-of-accounts records."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.list_factory import (
    LifecycleCallbacks,
    LifecycleModels,
    ModelPair,
    register_lifecycle,
)
from bookflow.company import accounts
from bookflow.core import audit
from bookflow.core.context import Context
from bookflow.core.models import ListOutput
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.core.session import Session, localize
from bookflow.core.versioning import check_update, current_writer, history_from_entries


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class MoneyOutput(_StrictModel):
    amount: str
    currency: str
    minor_units: int


class AccountRecordOutput(_StrictModel):
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str
    active: bool
    seed_key: str | None
    name: str
    number: str | None
    type: accounts.AccountType
    parent_id: str | None
    full_name: str
    depth: int
    description: str | None
    currency: str
    tax_line: str | None
    institution_name: str | None
    institution_account_last4: str | None
    routing_number_last4: str | None
    provider_profile_ref: None = None
    next_check_number: str | None
    check_reorder_number: str | None
    order_printable_checks: bool | None
    default_class_id: str | None
    track_reimbursable_expenses: bool
    reimbursable_income_account_id: str | None
    note: str | None
    system_role: str | None
    is_system: bool
    balance: MoneyOutput
    available_balance: MoneyOutput | None
    normal_balance: accounts.NormalBalance
    statement_family: accounts.StatementFamily
    has_transactions: bool
    child_count: int
    has_children: bool


class AccountCreateOutput(AccountRecordOutput):
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)


class AccountUpdateOutput(AccountCreateOutput):
    changed_fields: list[str] = Field(default_factory=list)
    merged_over_versions: list[int] = Field(default_factory=list)
    affected_descendant_ids: list[str] = Field(default_factory=list)


class AccountSelectorInput(_StrictModel):
    account: str = Field(description="Account id or canonical full name")


class AccountListInput(_StrictModel):
    query: str | None = None
    include_inactive: bool = False
    filter: list[str] = Field(default_factory=list)
    sort: str | None = None
    direction: Literal["asc", "desc"] = "asc"


class AccountActivateInput(AccountSelectorInput):
    expected_version: int | None = Field(default=None, ge=1)


class AccountDeactivateInput(AccountActivateInput):
    cascade: bool = False


class AccountActiveOutput(AccountCreateOutput):
    changed: bool
    affected_ids: list[str] = Field(default_factory=list)


def _output(
    s: Session,
    model: type[BaseModel],
    row: dict,
    **extra,
) -> BaseModel:
    values = accounts.project_account(s.company, row)
    values["created_at"] = localize(s, values["created_at"])
    values["updated_at"] = localize(s, values["updated_at"])
    return model.model_validate({**values, **extra})


def _version_meta(
    s: Session,
    row: dict,
    changes: set[str],
    expected_version: int | None,
):
    writer = current_writer(s.company, "account", row["id"], row)
    return check_update(
        current_version=int(row["version"]),
        current_updated_at=row["updated_at"],
        current_writer=writer,
        changes=changes,
        expected_version=expected_version,
        history_since=lambda version: history_from_entries(
            s.company,
            "account",
            row["id"],
            version,
            audit.decode_snapshot,
        ),
        actor_id=s.actor.id,
        window_seconds=s.company_info_row.get(
            "recent_activity_window_seconds", 60
        ),
    )


def _warning(meta) -> list[str]:
    warning = accounts.list_service.blind_write_warning(meta)
    return [warning] if warning else []


def plan_account_create(
    inp: accounts.AccountCreateInput, ctx: Context, s: Session
) -> Plan:
    mutation = accounts.plan_account_create(
        s.company,
        inp,
        actor_id=s.actor.id,
        via=ctx.interface.value,
    )
    preview = _output(s, AccountCreateOutput, dict(mutation.after))
    return Plan(preview=preview, data={"mutation": mutation})


def apply_account_create(plan: Plan, ctx: Context, s: Session) -> Applied:
    mutation = plan.data["mutation"]
    accounts.persist_account_mutation(s.company, mutation)
    touched = Touched(
        "account",
        str(mutation.after["id"]),
        "create",
        None,
        1,
        accounts.logical_account_snapshot(mutation.after),
        db="company",
    )
    return Applied(
        plan.preview,
        [touched],
        f"created account {mutation.after['full_name']}",
    )


def plan_account_update(
    inp: accounts.AccountUpdateInput, ctx: Context, s: Session
) -> Plan:
    current = accounts.resolve_account(s.company, inp.account)
    changes = {
        field: getattr(inp, field)
        for field in inp.model_fields_set
        if field not in {"account", "expected_version"}
    }
    update = accounts.plan_account_update(
        s.company,
        current["id"],
        changes,
        actor_id=s.actor.id,
        via=ctx.interface.value,
    )
    meta = _version_meta(
        s, current, set(update.changed_fields), inp.expected_version
    )
    row = dict(update.mutation.after) if update.mutation is not None else current
    warnings = _warning(meta)
    preview = _output(
        s,
        AccountUpdateOutput,
        row,
        warnings=warnings,
        changed_fields=meta.changed_fields,
        merged_over_versions=meta.merged_over_versions,
        affected_descendant_ids=list(update.affected_descendant_ids),
    )
    return Plan(preview=preview, data={"update": update})


def apply_account_update(plan: Plan, ctx: Context, s: Session) -> Applied:
    update = plan.data["update"]
    if update.mutation is None:
        return Applied(plan.preview, [], "no change")
    accounts.persist_account_update(s.company, update)
    mutation = update.mutation
    touched = Touched(
        "account",
        str(mutation.after["id"]),
        "update",
        int(mutation.before["version"]),
        int(mutation.after["version"]),
        accounts.logical_account_snapshot(mutation.after),
        accounts.logical_account_snapshot(mutation.before),
        db="company",
    )
    return Applied(
        plan.preview,
        [touched],
        f"updated account {mutation.after['full_name']}",
    )


def plan_account_show(inp: AccountSelectorInput, ctx: Context, s: Session) -> Plan:
    row = accounts.resolve_account(s.company, inp.account)
    return Plan(preview=_output(s, AccountRecordOutput, row))


def plan_account_list(inp: AccountListInput, ctx: Context, s: Session) -> Plan:
    rows = accounts.list_accounts(
        s.company,
        query=inp.query,
        filters=inp.filter,
        sort=inp.sort,
        direction=inp.direction,
        include_inactive=inp.include_inactive,
    )
    items = []
    for row in rows:
        row["created_at"] = localize(s, row["created_at"])
        row["updated_at"] = localize(s, row["updated_at"])
        items.append(AccountRecordOutput.model_validate(row))
    output_model = ListOutput[AccountRecordOutput]
    return Plan(preview=output_model(items=items, count=len(items)))


def _plan_active(active: bool):
    def planner(inp: BaseModel, ctx: Context, s: Session) -> Plan:
        current = accounts.resolve_account(s.company, inp.account)
        change = accounts.plan_account_active_change(
            s.company,
            current["id"],
            active,
            actor_id=s.actor.id,
            via=ctx.interface.value,
            cascade=bool(getattr(inp, "cascade", False)),
        )
        warnings: list[str] = []
        if change.changed:
            meta = _version_meta(s, current, {"active"}, inp.expected_version)
            warnings = _warning(meta)
            requested = dict(change.mutations[0].after)
        else:
            requested = current
        preview = _output(
            s,
            AccountActiveOutput,
            requested,
            warnings=warnings,
            changed=change.changed,
            affected_ids=list(change.affected_ids),
        )
        return Plan(preview=preview, data={"change": change})

    return planner


def apply_account_active(plan: Plan, ctx: Context, s: Session) -> Applied:
    change = plan.data["change"]
    if not change.changed:
        return Applied(plan.preview, [], "no change")
    accounts.persist_account_active_change(s.company, change)
    touched = [
        Touched(
            "account",
            str(mutation.after["id"]),
            mutation.action,
            int(mutation.before["version"]),
            int(mutation.after["version"]),
            accounts.logical_account_snapshot(mutation.after),
            accounts.logical_account_snapshot(mutation.before),
            db="company",
        )
        for mutation in change.mutations
    ]
    return Applied(
        plan.preview,
        touched,
        f"{change.mutations[0].action}d account {change.mutations[0].after['full_name']}",
    )


_LIST_OUTPUT = ListOutput[AccountRecordOutput]

ACCOUNT_COMMANDS = register_lifecycle(
    "account",
    models=LifecycleModels(
        create=ModelPair(accounts.AccountCreateInput, AccountCreateOutput),
        update=ModelPair(accounts.AccountUpdateInput, AccountUpdateOutput),
        show=ModelPair(AccountSelectorInput, AccountRecordOutput),
        list=ModelPair(AccountListInput, _LIST_OUTPUT),
        activate=ModelPair(AccountActivateInput, AccountActiveOutput),
        deactivate=ModelPair(AccountDeactivateInput, AccountActiveOutput),
    ),
    callbacks=LifecycleCallbacks(
        create_plan=plan_account_create,
        create_apply=apply_account_create,
        update_plan=plan_account_update,
        update_apply=apply_account_update,
        show_plan=plan_account_show,
        list_plan=plan_account_list,
        activate_plan=_plan_active(True),
        activate_apply=apply_account_active,
        deactivate_plan=_plan_active(False),
        deactivate_apply=apply_account_active,
    ),
    selector_field="account",
    error_codes={
        "update": ("E_SYSTEM_RECORD", "E_TYPE_CHANGE", "E_RECORD_IN_USE"),
        "activate": ("E_SYSTEM_RECORD",),
        "deactivate": ("E_RECORD_IN_USE",),
    },
)


__all__ = [
    "ACCOUNT_COMMANDS",
    "AccountActivateInput",
    "AccountActiveOutput",
    "AccountCreateOutput",
    "AccountDeactivateInput",
    "AccountListInput",
    "AccountRecordOutput",
    "AccountSelectorInput",
    "AccountUpdateOutput",
]
