"""Register the complete Row 5 item catalogue lifecycle."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.list_factory import (
    LifecycleCallbacks,
    LifecycleModels,
    ModelPair,
    register_lifecycle,
)
from bookflow.company import items, list_service
from bookflow.core import audit
from bookflow.core.context import Context
from bookflow.core.models import ListOutput
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.core.session import Session, localize
from bookflow.core.versioning import check_update, current_writer, history_from_entries


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ItemCreateInput(items.ItemInput):
    pass


class ItemUpdateInput(StrictInput):
    item: str = Field(description="Item id or canonical full name")
    expected_version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: items.ItemType | None = None
    parent_id: str | None = Field(default=None, min_length=26, max_length=26)
    category_id: str | None = Field(default=None, min_length=26, max_length=26)
    description: str | None = None
    purchase_description: str | None = None
    sales_enabled: bool | None = None
    purchase_enabled: bool | None = None
    price: Any | None = None
    cost: Any | None = None
    income_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    expense_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    cogs_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    asset_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    deposit_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    liability_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    default_class_id: str | None = Field(default=None, min_length=26, max_length=26)
    sales_tax_code_id: str | None = Field(default=None, min_length=26, max_length=26)
    manufacturer_part_number: str | None = Field(default=None, max_length=128)
    barcode: str | None = Field(default=None, max_length=128)
    unit_of_measure_set_id: str | None = Field(default=None, min_length=26, max_length=26)
    reorder_point_min: str | None = None
    reorder_point_max: str | None = None
    preferred_vendor_id: str | None = Field(default=None, min_length=26, max_length=26)
    notes: str | None = None
    charge_percent: str | None = None
    print_members: bool | None = None
    discount_amount: Any | None = None
    discount_percent: str | None = None
    payment_method_id: str | None = Field(default=None, min_length=26, max_length=26)
    use_undeposited_funds: bool | None = None
    tax_percent: str | None = None
    tax_agency_vendor_id: str | None = Field(default=None, min_length=26, max_length=26)
    assembly_build_point: str | None = None
    asset_number: str | None = Field(default=None, max_length=128)
    purchase_date: str | None = None
    original_cost: Any | None = None
    vendor_id: str | None = Field(default=None, min_length=26, max_length=26)
    location: str | None = Field(default=None, max_length=200)
    serial_number: str | None = Field(default=None, max_length=128)
    warranty_expiration: str | None = None
    disposal_status: items.DisposalStatus | None = None
    disposal_date: str | None = None
    disposal_proceeds: Any | None = None
    disposal_costs: Any | None = None
    accumulated_depreciation_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    depreciation_expense_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    gain_loss_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    depreciation_method: items.DepreciationMethod | None = None
    useful_life_months: int | None = Field(default=None, ge=1)
    book_basis: Any | None = None
    tax_basis: Any | None = None
    members: list[items.ItemMemberInput] | None = None
    vendor_profiles: list[items.ItemVendorProfileInput] | None = None
    custom_fields: dict[str, Any | None] | None = None


class ItemSelector(StrictInput):
    item: str = Field(description="Item id or canonical full name")


class ItemVersionSelector(ItemSelector):
    expected_version: int | None = Field(default=None, ge=1)


class ItemDeactivateInput(ItemVersionSelector):
    cascade: bool = False


class ItemListInput(StrictInput):
    query: str | None = None
    include_inactive: bool = False
    filter: list[str] = Field(default_factory=list)
    sort: str | None = None
    direction: Literal["asc", "desc"] = "asc"


class ItemCreateOutput(items.ItemOutput):
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)


class ItemUpdateOutput(ItemCreateOutput):
    changed_fields: list[str] = Field(default_factory=list)
    merged_over_versions: list[int] = Field(default_factory=list)
    affected_descendant_ids: list[str] = Field(default_factory=list)


class ItemActiveOutput(ItemCreateOutput):
    changed: bool
    affected_ids: list[str] = Field(default_factory=list)


def _localized(record: items.ItemOutput, session: Session) -> dict[str, Any]:
    values = record.model_dump(mode="python")
    values["created_at"] = localize(session, values["created_at"])
    values["updated_at"] = localize(session, values["updated_at"])
    return values


def _version_meta(
    session: Session,
    row: dict[str, Any],
    changes: set[str],
    expected_version: int | None,
):
    writer = current_writer(session.company, items.ITEM_RECORD_TYPE, row["id"], row)
    return check_update(
        current_version=int(row["version"]),
        current_updated_at=row["updated_at"],
        current_writer=writer,
        changes=changes,
        expected_version=expected_version,
        history_since=lambda version: history_from_entries(
            session.company,
            items.ITEM_RECORD_TYPE,
            row["id"],
            version,
            audit.decode_snapshot,
        ),
        actor_id=session.actor.id,
        window_seconds=session.company_info_row.get("recent_activity_window_seconds", 60),
    )


def _warnings(meta) -> list[str]:
    result: list[str] = []
    warning = list_service.blind_write_warning(meta)
    if warning:
        result.append(warning)
    if meta.merged_over_versions:
        result.append(f"merged over versions {meta.merged_over_versions}; those changes touched other fields")
    return result


def _touched(mutation: items.ItemMutation) -> Touched:
    return Touched(
        items.ITEM_RECORD_TYPE,
        str(mutation.after["id"]),
        mutation.action,
        None if mutation.before is None else int(mutation.before["version"]),
        int(mutation.after["version"]),
        mutation.after_snapshot,
        mutation.before_snapshot,
        db="company",
    )


def plan_item_create(inp: ItemCreateInput, ctx: Context, session: Session) -> Plan:
    mutation = items.plan_item_create(
        session.company,
        inp.model_dump(mode="python", exclude_unset=True),
        actor_id=session.actor.id,
        via=ctx.interface.value,
    )
    projected = items.project_item(
        session.company,
        mutation.after,
        members=mutation.after_members,
        vendor_profiles=mutation.after_vendors,
        custom_values=mutation.after_custom,
    )
    return Plan(
        preview=ItemCreateOutput.model_validate(_localized(projected, session)),
        data={"mutation": mutation},
    )


def apply_item_create(plan: Plan, ctx: Context, session: Session) -> Applied:
    mutation = plan.data["mutation"]
    items.persist_item_mutation(session.company, mutation)
    return Applied(plan.preview, [_touched(mutation)], f"created item {mutation.after['full_name']}")


def plan_item_update(inp: ItemUpdateInput, ctx: Context, session: Session) -> Plan:
    current = items.resolve_item(session.company, inp.item)
    changes = {
        field: getattr(inp, field)
        for field in inp.model_fields_set
        if field not in {"item", "expected_version"}
    }
    update = items.plan_item_update(
        session.company,
        current["id"],
        changes,
        actor_id=session.actor.id,
        via=ctx.interface.value,
    )
    actual = set(update.changed_fields)
    meta = _version_meta(session, current, actual, inp.expected_version)
    mutation = update.mutation
    after = current if mutation is None else mutation.after
    projected = items.project_item(
        session.company,
        after,
        members=None if mutation is None else mutation.after_members,
        vendor_profiles=None if mutation is None else mutation.after_vendors,
        custom_values=None if mutation is None else mutation.after_custom,
    )
    preview = ItemUpdateOutput.model_validate({
        **_localized(projected, session),
        "warnings": _warnings(meta),
        "changed_fields": list(meta.changed_fields),
        "merged_over_versions": list(meta.merged_over_versions),
        "affected_descendant_ids": list(update.affected_descendant_ids),
    })
    return Plan(preview=preview, data={"update": update})


def apply_item_update(plan: Plan, ctx: Context, session: Session) -> Applied:
    update = plan.data["update"]
    if update.mutation is None:
        return Applied(plan.preview, [], "no change")
    items.persist_item_update(session.company, update)
    return Applied(plan.preview, [_touched(update.mutation)], f"updated item {update.mutation.after['full_name']}")


def plan_item_show(inp: ItemSelector, ctx: Context, session: Session) -> Plan:
    current = items.resolve_item(session.company, inp.item)
    projected = items.project_item(session.company, current)
    return Plan(preview=items.ItemOutput.model_validate(_localized(projected, session)))


def plan_item_list(inp: ItemListInput, ctx: Context, session: Session) -> Plan:
    records = items.list_items(
        session.company,
        query=inp.query,
        filters=inp.filter,
        sort=inp.sort,
        direction=inp.direction,
        include_inactive=inp.include_inactive,
    )
    localized = [items.ItemOutput.model_validate(_localized(record, session)) for record in records]
    return Plan(preview=ListOutput[items.ItemOutput](items=localized, count=len(localized)))


def _active_plan(active: bool):
    def planner(inp: ItemVersionSelector, ctx: Context, session: Session) -> Plan:
        current = items.resolve_item(session.company, inp.item)
        change = items.plan_item_active_change(
            session.company,
            current["id"],
            active,
            actor_id=session.actor.id,
            via=ctx.interface.value,
            cascade=bool(getattr(inp, "cascade", False)),
        )
        meta = _version_meta(session, current, {"active"} if change.changed else set(), inp.expected_version)
        requested = current if not change.changed else change.mutations[0].after
        mutation = None if not change.changed else change.mutations[0]
        projected = items.project_item(
            session.company,
            requested,
            members=None if mutation is None else mutation.after_members,
            vendor_profiles=None if mutation is None else mutation.after_vendors,
            custom_values=None if mutation is None else mutation.after_custom,
        )
        preview = ItemActiveOutput.model_validate({
            **_localized(projected, session),
            "warnings": _warnings(meta),
            "changed": change.changed,
            "affected_ids": list(change.affected_ids),
        })
        return Plan(preview=preview, data={"change": change})

    return planner


def apply_item_active(plan: Plan, ctx: Context, session: Session) -> Applied:
    change = plan.data["change"]
    if not change.changed:
        return Applied(plan.preview, [], "no change")
    items.persist_item_active_change(session.company, change)
    first = change.mutations[0]
    return Applied(
        plan.preview,
        [_touched(mutation) for mutation in change.mutations],
        f"{first.action}d item {first.after['full_name']}",
    )


_LIST_OUTPUT = ListOutput[items.ItemOutput]

ITEM_COMMANDS = register_lifecycle(
    "item",
    models=LifecycleModels(
        create=ModelPair(ItemCreateInput, ItemCreateOutput),
        update=ModelPair(ItemUpdateInput, ItemUpdateOutput),
        show=ModelPair(ItemSelector, items.ItemOutput),
        list=ModelPair(ItemListInput, _LIST_OUTPUT),
        activate=ModelPair(ItemVersionSelector, ItemActiveOutput),
        deactivate=ModelPair(ItemDeactivateInput, ItemActiveOutput),
    ),
    callbacks=LifecycleCallbacks(
        create_plan=plan_item_create,
        create_apply=apply_item_create,
        update_plan=plan_item_update,
        update_apply=apply_item_update,
        show_plan=plan_item_show,
        list_plan=plan_item_list,
        activate_plan=_active_plan(True),
        activate_apply=apply_item_active,
        deactivate_plan=_active_plan(False),
        deactivate_apply=apply_item_active,
    ),
    selector_field="item",
    error_codes={
        "create": ("E_VALIDATION", "E_VALUE_RANGE", "E_AMOUNT_PRECISION", "E_FEATURE_DISABLED", "E_HIERARCHY_CYCLE"),
        "update": ("E_VALIDATION", "E_VALUE_RANGE", "E_AMOUNT_PRECISION", "E_FEATURE_DISABLED", "E_TYPE_CHANGE", "E_HIERARCHY_CYCLE"),
        "list": ("E_VALIDATION", "E_RECORD_NOT_FOUND"),
        "activate": ("E_VALIDATION", "E_FEATURE_DISABLED", "E_HIERARCHY_CYCLE"),
        "deactivate": ("E_VALIDATION", "E_RECORD_IN_USE"),
    },
)


__all__ = [
    "ITEM_COMMANDS", "ItemActiveOutput", "ItemCreateInput", "ItemCreateOutput",
    "ItemDeactivateInput", "ItemListInput", "ItemSelector", "ItemUpdateInput", "ItemUpdateOutput",
]
