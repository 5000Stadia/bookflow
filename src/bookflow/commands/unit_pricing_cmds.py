"""Register the price-level and unit-of-measure lifecycle command families."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.list_factory import (
    LifecycleCallbacks,
    LifecycleModels,
    ModelPair,
    register_lifecycle,
)
from bookflow.company import list_service, pricing, units
from bookflow.company.units import UnitConversionInput
from bookflow.core import audit
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.models import ListOutput
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.core.session import Session, localize
from bookflow.core.versioning import check_update, current_writer, history_from_entries


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class LifecycleListInput(StrictInput):
    query: str | None = None
    include_inactive: bool = False
    filter: list[str] = Field(default_factory=list)
    sort: str | None = None
    direction: Literal["asc", "desc"] = "asc"


class PriceLevelCreateInput(pricing.PriceLevelInput):
    pass


class PriceLevelUpdateInput(StrictInput):
    price_level: str = Field(description="Price-level id or canonical name")
    expected_version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["fixed_percent", "per_item"] | None = None
    currency: str | None = None
    rounding_mode: Literal["nearest", "up", "down"] | None = None
    rounding_increment: str | None = None
    rounding_offset: str | None = None
    percent: str | None = None
    items: list[pricing.PriceLevelItemInput] | None = None


class PriceLevelSelector(StrictInput):
    price_level: str = Field(description="Price-level id or canonical name")


class PriceLevelVersionSelector(PriceLevelSelector):
    expected_version: int | None = Field(default=None, ge=1)


class UnitOfMeasureCreateInput(units.UnitOfMeasureInput):
    pass


class UnitOfMeasureUpdateInput(StrictInput):
    unit_of_measure: str = Field(description="Unit-of-measure set id or canonical name")
    expected_version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    default_purchase_unit_id: str | None = Field(default=None, min_length=26, max_length=26)
    default_sales_unit_id: str | None = Field(default=None, min_length=26, max_length=26)
    default_shipping_unit_id: str | None = Field(default=None, min_length=26, max_length=26)
    units: list[UnitConversionInput] | None = None


class UnitOfMeasureSelector(StrictInput):
    unit_of_measure: str = Field(description="Unit-of-measure set id or canonical name")


class UnitOfMeasureVersionSelector(UnitOfMeasureSelector):
    expected_version: int | None = Field(default=None, ge=1)


class PriceLevelCreateOutput(pricing.PriceLevelOutput):
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)


class PriceLevelUpdateOutput(PriceLevelCreateOutput):
    changed_fields: list[str] = Field(default_factory=list)
    merged_over_versions: list[int] = Field(default_factory=list)
    affected_descendant_ids: list[str] = Field(default_factory=list)


class PriceLevelActiveOutput(PriceLevelCreateOutput):
    changed: bool
    affected_ids: list[str] = Field(default_factory=list)


class UnitOfMeasureCreateOutput(units.UnitOfMeasureOutput):
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)


class UnitOfMeasureUpdateOutput(UnitOfMeasureCreateOutput):
    changed_fields: list[str] = Field(default_factory=list)
    merged_over_versions: list[int] = Field(default_factory=list)
    affected_descendant_ids: list[str] = Field(default_factory=list)


class UnitOfMeasureActiveOutput(UnitOfMeasureCreateOutput):
    changed: bool
    affected_ids: list[str] = Field(default_factory=list)


def _localized(model: BaseModel, s: Session) -> dict:
    values = model.model_dump(mode="python")
    values["created_at"] = localize(s, values["created_at"])
    values["updated_at"] = localize(s, values["updated_at"])
    return values


def _version_meta(
    s: Session,
    record_type: str,
    row: dict,
    changes: set[str],
    expected_version: int | None,
):
    writer = current_writer(s.company, record_type, row["id"], row)
    return check_update(
        current_version=int(row["version"]),
        current_updated_at=row["updated_at"],
        current_writer=writer,
        changes=changes,
        expected_version=expected_version,
        history_since=lambda version: history_from_entries(
            s.company,
            record_type,
            row["id"],
            version,
            audit.decode_snapshot,
        ),
        actor_id=s.actor.id,
        window_seconds=s.company_info_row.get("recent_activity_window_seconds", 60),
    )


def _warnings(meta) -> list[str]:
    warnings: list[str] = []
    warning = list_service.blind_write_warning(meta)
    if warning:
        warnings.append(warning)
    if meta.merged_over_versions:
        warnings.append(
            f"merged over versions {meta.merged_over_versions}; those changes touched other fields"
        )
    return warnings


def _price_touched(mutation: pricing.PriceMutation) -> Touched:
    return Touched(
        pricing.PRICE_RECORD_TYPE,
        str(mutation.after["id"]),
        mutation.action,
        None if mutation.before is None else int(mutation.before["version"]),
        int(mutation.after["version"]),
        mutation.after_snapshot,
        mutation.before_snapshot,
        db="company",
    )


def _unit_touched(mutation: units.UnitMutation) -> Touched:
    return Touched(
        units.UNIT_RECORD_TYPE,
        str(mutation.after["id"]),
        mutation.action,
        None if mutation.before is None else int(mutation.before["version"]),
        int(mutation.after["version"]),
        mutation.after_snapshot,
        mutation.before_snapshot,
        db="company",
    )


def _register_price_levels() -> None:
    def create_plan(inp: PriceLevelCreateInput, ctx: Context, s: Session) -> Plan:
        mutation = pricing.plan_price_level_create(
            s.company,
            inp.model_dump(mode="python", exclude_unset=True),
            actor_id=s.actor.id,
            via=ctx.interface.value,
        )
        record = pricing.project_price_level(s.company, mutation.after, mutation.after_children)
        return Plan(
            preview=PriceLevelCreateOutput.model_validate(_localized(record, s)),
            data={"mutation": mutation},
        )

    def create_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        mutation = plan.data["mutation"]
        pricing.persist_price_mutation(s.company, mutation)
        return Applied(
            plan.preview,
            [_price_touched(mutation)],
            f"created price level {mutation.after['name']}",
        )

    def update_plan(inp: PriceLevelUpdateInput, ctx: Context, s: Session) -> Plan:
        current = pricing.resolve_price_level(s.company, inp.price_level)
        changes = {
            field: getattr(inp, field)
            for field in inp.model_fields_set
            if field not in {"price_level", "expected_version"}
        }
        nonnullable = {
            "name", "kind", "rounding_mode", "rounding_increment", "rounding_offset"
        }
        invalid_nulls = sorted(field for field in changes if field in nonnullable and changes[field] is None)
        if invalid_nulls:
            raise BookflowError(
                "E_VALIDATION",
                details={"fields": [{"field": field, "problem": "cannot be cleared"} for field in invalid_nulls]},
            )
        mutation = pricing.plan_price_level_update(
            s.company,
            current["id"],
            changes,
            actor_id=s.actor.id,
            via=ctx.interface.value,
        )
        actual = set(mutation.changed_fields) if mutation is not None else set()
        meta = _version_meta(s, pricing.PRICE_RECORD_TYPE, current, actual, inp.expected_version)
        after = current if mutation is None else mutation.after
        children = None if mutation is None else mutation.after_children
        record = pricing.project_price_level(s.company, after, children)
        warnings = _warnings(meta)
        preview = PriceLevelUpdateOutput.model_validate(
            {
                **_localized(record, s),
                "warnings": warnings,
                "changed_fields": list(meta.changed_fields),
                "merged_over_versions": list(meta.merged_over_versions),
            }
        )
        return Plan(preview=preview, data={"mutation": mutation})

    def update_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        mutation = plan.data["mutation"]
        if mutation is None:
            return Applied(plan.preview, [], "no change")
        pricing.persist_price_mutation(s.company, mutation)
        return Applied(
            plan.preview,
            [_price_touched(mutation)],
            f"updated price level {mutation.after['name']}",
        )

    def show_plan(inp: PriceLevelSelector, ctx: Context, s: Session) -> Plan:
        current = pricing.resolve_price_level(s.company, inp.price_level)
        return Plan(preview=pricing.PriceLevelOutput.model_validate(
            _localized(pricing.project_price_level(s.company, current), s)
        ))

    def list_plan(inp: LifecycleListInput, ctx: Context, s: Session) -> Plan:
        records = pricing.list_price_levels(
            s.company,
            query=inp.query,
            filters=inp.filter,
            sort=inp.sort,
            direction=inp.direction,
            include_inactive=inp.include_inactive,
        )
        localized = [
            pricing.PriceLevelOutput.model_validate(_localized(record, s)) for record in records
        ]
        return Plan(preview=ListOutput[pricing.PriceLevelOutput](items=localized, count=len(localized)))

    def active_plan(active: bool):
        def planner(inp: PriceLevelVersionSelector, ctx: Context, s: Session) -> Plan:
            current = pricing.resolve_price_level(s.company, inp.price_level)
            mutation = pricing.plan_price_level_active_change(
                s.company,
                current["id"],
                active,
                actor_id=s.actor.id,
                via=ctx.interface.value,
            )
            if mutation is not None:
                _version_meta(
                    s, pricing.PRICE_RECORD_TYPE, current, {"active"}, inp.expected_version
                )
            after = current if mutation is None else mutation.after
            children = None if mutation is None else mutation.after_children
            record = pricing.project_price_level(s.company, after, children)
            preview = PriceLevelActiveOutput.model_validate(
                {
                    **_localized(record, s),
                    "changed": mutation is not None,
                    "affected_ids": [] if mutation is None else [str(after["id"])],
                }
            )
            return Plan(preview=preview, data={"mutation": mutation})

        return planner

    def active_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        mutation = plan.data["mutation"]
        if mutation is None:
            return Applied(plan.preview, [], "no change")
        pricing.persist_price_mutation(s.company, mutation)
        return Applied(
            plan.preview,
            [_price_touched(mutation)],
            f"{mutation.action}d price level {mutation.after['name']}",
        )

    models = LifecycleModels(
        create=ModelPair(PriceLevelCreateInput, PriceLevelCreateOutput),
        update=ModelPair(PriceLevelUpdateInput, PriceLevelUpdateOutput),
        show=ModelPair(PriceLevelSelector, pricing.PriceLevelOutput),
        list=ModelPair(LifecycleListInput, ListOutput[pricing.PriceLevelOutput]),
        activate=ModelPair(PriceLevelVersionSelector, PriceLevelActiveOutput),
        deactivate=ModelPair(PriceLevelVersionSelector, PriceLevelActiveOutput),
    )
    callbacks = LifecycleCallbacks(
        create_plan=create_plan,
        create_apply=create_apply,
        update_plan=update_plan,
        update_apply=update_apply,
        show_plan=show_plan,
        list_plan=list_plan,
        activate_plan=active_plan(True),
        activate_apply=active_apply,
        deactivate_plan=active_plan(False),
        deactivate_apply=active_apply,
    )
    register_lifecycle(
        "price-level",
        models=models,
        callbacks=callbacks,
        selector_field="price_level",
        error_codes={
            "create": ("E_AMOUNT_PRECISION", "E_VALUE_RANGE"),
            "update": ("E_AMOUNT_PRECISION", "E_VALUE_RANGE", "E_TYPE_CHANGE"),
        },
    )


def _register_units() -> None:
    def create_plan(inp: UnitOfMeasureCreateInput, ctx: Context, s: Session) -> Plan:
        mutation = units.plan_unit_create(
            s.company,
            inp.model_dump(mode="python", exclude_unset=True),
            actor_id=s.actor.id,
            via=ctx.interface.value,
        )
        record = units.project_unit_record(s.company, mutation.after, mutation.after_children)
        return Plan(
            preview=UnitOfMeasureCreateOutput.model_validate(_localized(record, s)),
            data={"mutation": mutation},
        )

    def create_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        mutation = plan.data["mutation"]
        units.persist_unit_mutation(s.company, mutation)
        return Applied(
            plan.preview,
            [_unit_touched(mutation)],
            f"created unit-of-measure set {mutation.after['name']}",
        )

    def update_plan(inp: UnitOfMeasureUpdateInput, ctx: Context, s: Session) -> Plan:
        current = units.resolve_unit_set(s.company, inp.unit_of_measure)
        changes = {
            field: getattr(inp, field)
            for field in inp.model_fields_set
            if field not in {"unit_of_measure", "expected_version"}
        }
        invalid_nulls = sorted(
            field for field in ("name", "units") if field in changes and changes[field] is None
        )
        if invalid_nulls:
            raise BookflowError(
                "E_VALIDATION",
                details={"fields": [{"field": field, "problem": "cannot be cleared"} for field in invalid_nulls]},
            )
        mutation = units.plan_unit_update(
            s.company,
            current["id"],
            changes,
            actor_id=s.actor.id,
            via=ctx.interface.value,
        )
        actual = set(mutation.changed_fields) if mutation is not None else set()
        meta = _version_meta(s, units.UNIT_RECORD_TYPE, current, actual, inp.expected_version)
        after = current if mutation is None else mutation.after
        children = None if mutation is None else mutation.after_children
        record = units.project_unit_record(s.company, after, children)
        warnings = _warnings(meta)
        preview = UnitOfMeasureUpdateOutput.model_validate(
            {
                **_localized(record, s),
                "warnings": warnings,
                "changed_fields": list(meta.changed_fields),
                "merged_over_versions": list(meta.merged_over_versions),
            }
        )
        return Plan(preview=preview, data={"mutation": mutation})

    def update_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        mutation = plan.data["mutation"]
        if mutation is None:
            return Applied(plan.preview, [], "no change")
        units.persist_unit_mutation(s.company, mutation)
        return Applied(
            plan.preview,
            [_unit_touched(mutation)],
            f"updated unit-of-measure set {mutation.after['name']}",
        )

    def show_plan(inp: UnitOfMeasureSelector, ctx: Context, s: Session) -> Plan:
        current = units.resolve_unit_set(s.company, inp.unit_of_measure)
        return Plan(preview=units.UnitOfMeasureOutput.model_validate(
            _localized(units.project_unit_record(s.company, current), s)
        ))

    def list_plan(inp: LifecycleListInput, ctx: Context, s: Session) -> Plan:
        records = units.list_unit_records(
            s.company,
            query=inp.query,
            filters=inp.filter,
            sort=inp.sort,
            direction=inp.direction,
            include_inactive=inp.include_inactive,
        )
        localized = [
            units.UnitOfMeasureOutput.model_validate(_localized(record, s)) for record in records
        ]
        return Plan(preview=ListOutput[units.UnitOfMeasureOutput](items=localized, count=len(localized)))

    def active_plan(active: bool):
        def planner(inp: UnitOfMeasureVersionSelector, ctx: Context, s: Session) -> Plan:
            current = units.resolve_unit_set(s.company, inp.unit_of_measure)
            mutation = units.plan_unit_active_change(
                s.company,
                current["id"],
                active,
                actor_id=s.actor.id,
                via=ctx.interface.value,
            )
            if mutation is not None:
                _version_meta(s, units.UNIT_RECORD_TYPE, current, {"active"}, inp.expected_version)
            after = current if mutation is None else mutation.after
            children = None if mutation is None else mutation.after_children
            record = units.project_unit_record(s.company, after, children)
            preview = UnitOfMeasureActiveOutput.model_validate(
                {
                    **_localized(record, s),
                    "changed": mutation is not None,
                    "affected_ids": [] if mutation is None else [str(after["id"])],
                }
            )
            return Plan(preview=preview, data={"mutation": mutation})

        return planner

    def active_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        mutation = plan.data["mutation"]
        if mutation is None:
            return Applied(plan.preview, [], "no change")
        units.persist_unit_mutation(s.company, mutation)
        return Applied(
            plan.preview,
            [_unit_touched(mutation)],
            f"{mutation.action}d unit-of-measure set {mutation.after['name']}",
        )

    models = LifecycleModels(
        create=ModelPair(UnitOfMeasureCreateInput, UnitOfMeasureCreateOutput),
        update=ModelPair(UnitOfMeasureUpdateInput, UnitOfMeasureUpdateOutput),
        show=ModelPair(UnitOfMeasureSelector, units.UnitOfMeasureOutput),
        list=ModelPair(LifecycleListInput, ListOutput[units.UnitOfMeasureOutput]),
        activate=ModelPair(UnitOfMeasureVersionSelector, UnitOfMeasureActiveOutput),
        deactivate=ModelPair(UnitOfMeasureVersionSelector, UnitOfMeasureActiveOutput),
    )
    callbacks = LifecycleCallbacks(
        create_plan=create_plan,
        create_apply=create_apply,
        update_plan=update_plan,
        update_apply=update_apply,
        show_plan=show_plan,
        list_plan=list_plan,
        activate_plan=active_plan(True),
        activate_apply=active_apply,
        deactivate_plan=active_plan(False),
        deactivate_apply=active_apply,
    )
    register_lifecycle(
        "unit-of-measure",
        models=models,
        callbacks=callbacks,
        selector_field="unit_of_measure",
        error_codes={
            "create": ("E_VALUE_RANGE",),
            "update": ("E_VALUE_RANGE",),
        },
    )


_register_price_levels()
_register_units()


__all__ = [
    "LifecycleListInput",
    "PriceLevelActiveOutput",
    "PriceLevelCreateInput",
    "PriceLevelCreateOutput",
    "PriceLevelSelector",
    "PriceLevelUpdateInput",
    "PriceLevelUpdateOutput",
    "PriceLevelVersionSelector",
    "UnitOfMeasureActiveOutput",
    "UnitOfMeasureCreateInput",
    "UnitOfMeasureCreateOutput",
    "UnitOfMeasureSelector",
    "UnitOfMeasureUpdateInput",
    "UnitOfMeasureUpdateOutput",
    "UnitOfMeasureVersionSelector",
]
