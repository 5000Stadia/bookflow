"""Packaged supporting-profile reads and idempotent company application."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
import sqlalchemy as sa

from bookflow.commands.common import Empty, WriteOutput
from bookflow.commands.list_factory import (
    LifecycleCallbacks,
    LifecycleModels,
    ModelPair,
    register_lifecycle,
)
from bookflow.company import list_service, profiles, schema
from bookflow.company.lists import get_list_definition
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.models import ListOutput
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session, localize
from bookflow.core.versioning import check_update, current_writer, history_from_entries
from bookflow.core import audit


class ProfileSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    profile_id: str = Field(description="Packaged profile id")


class ProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str
    version: int
    lists: list[str]
    record_count: int


class ProfileShowOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str
    version: int
    lists: list[str]
    records: dict[str, list[dict]]


class ProfileApplyInput(ProfileSelector):
    pass


class ProfileApplyOutput(WriteOutput):
    profile_id: str
    version: int
    inserted_by_list: dict[str, int]
    preserved_by_list: dict[str, int]
    prospective_or_created_ids: list[str]


def _manifest(profile_id: str) -> profiles.StandardProfileManifest:
    if profile_id != "standard":
        raise BookflowError(
            "E_RECORD_NOT_FOUND",
            details={"record_type": "profile", "selector": profile_id, "suggestions": []},
        )
    return profiles.load_standard_profile()


profile_list = command(
    "profile list",
    scope="hub",
    description="List packaged supporting-data profiles.",
    input_model=Empty,
    output_model=ListOutput[ProfileSummary],
    capability="profile",
)


@profile_list
def plan_profile_list(inp: Empty, ctx: Context, s: Session) -> Plan:
    manifest = profiles.load_standard_profile()
    item = ProfileSummary(
        profile_id=manifest.manifest_id,
        version=manifest.version,
        lists=list(manifest.lists),
        record_count=sum(len(records) for records in manifest.records.values()),
    )
    return Plan(preview=ListOutput[ProfileSummary](items=[item], count=1))


profile_show = command(
    "profile show",
    scope="hub",
    description="Show one packaged supporting-data profile.",
    input_model=ProfileSelector,
    output_model=ProfileShowOutput,
    positional=["profile_id"],
    error_codes=["E_RECORD_NOT_FOUND"],
    capability="profile",
)


@profile_show
def plan_profile_show(inp: ProfileSelector, ctx: Context, s: Session) -> Plan:
    manifest = _manifest(inp.profile_id)
    return Plan(
        preview=ProfileShowOutput(
            profile_id=manifest.manifest_id,
            version=manifest.version,
            lists=list(manifest.lists),
            records=manifest.records,
        )
    )


profile_apply = command(
    "profile apply",
    scope="company",
    description="Apply a packaged supporting-data profile without replacing edits.",
    input_model=ProfileApplyInput,
    output_model=ProfileApplyOutput,
    writes={"company"},
    required_role="admin",
    positional=["profile_id"],
    accepts_idempotency_key=True,
    error_codes=["E_RECORD_NOT_FOUND"],
    capability="profile",
)


@profile_apply
def plan_profile_apply(inp: ProfileApplyInput, ctx: Context, s: Session) -> Plan:
    _manifest(inp.profile_id)
    manifest, mutations, preserved = profiles.plan_standard_profile(
        s.company,
        actor_id=s.actor.id,
        via=ctx.interface.value,
    )
    inserted = {noun: 0 for noun in manifest.lists}
    for mutation in mutations:
        inserted[mutation.noun] += 1
    output = ProfileApplyOutput(
        profile_id=manifest.manifest_id,
        version=manifest.version,
        inserted_by_list=inserted,
        preserved_by_list=preserved,
        prospective_or_created_ids=[str(mutation.after["id"]) for mutation in mutations],
    )
    return Plan(preview=output, data={"mutations": mutations})


@profile_apply.applier
def apply_profile_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
    mutations = plan.data["mutations"]
    for mutation in mutations:
        profiles.persist_profile_mutation(s.company, mutation)
    touched = [
        Touched(
            mutation.noun.replace("-", "_"),
            str(mutation.after["id"]),
            "create",
            None,
            1,
            dict(mutation.after),
            db="company",
        )
        for mutation in mutations
    ]
    return Applied(plan.preview, touched, "applied profile standard")


# Supporting-list lifecycle commands ----------------------------------------


class ProfileListInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str | None = None
    include_inactive: bool = False
    filter: list[str] = Field(default_factory=list)
    sort: str | None = None
    direction: Literal["asc", "desc"] = "asc"


_COMMON_RECORD_FIELDS = {
    "id": (str, ...),
    "version": (int, ...),
    "created_at": (str, ...),
    "created_by": (str, ...),
    "created_via": (str, ...),
    "updated_at": (str, ...),
    "updated_by": (str, ...),
    "updated_via": (str, ...),
    "active": (bool, ...),
    "seed_key": (str | None, None),
}

_HIERARCHY_FIELDS = {
    "name": (str, ...),
    "parent_id": (str | None, None),
    "full_name": (str, ...),
    "depth": (int, ...),
}

_PUBLIC_FIELDS: dict[str, dict[str, tuple[object, object]]] = {
    "item-category": {**_HIERARCHY_FIELDS, "usage_count": (int, 0)},
    "class": {**_HIERARCHY_FIELDS, "usage_count": (int, 0)},
    "customer-type": dict(_HIERARCHY_FIELDS),
    "vendor-type": dict(_HIERARCHY_FIELDS),
    "job-type": dict(_HIERARCHY_FIELDS),
    "term": {
        "name": (str, ...),
        "kind": (Literal["standard", "date_driven"], ...),
        "due_days": (int | None, None),
        "discount_days": (int | None, None),
        "due_day_of_month": (int | None, None),
        "due_next_month_if_within_days": (int | None, None),
        "discount_day_of_month": (int | None, None),
        "discount_percent": (str | None, None),
        "due_rule_summary": (str, ...),
        "discount_rule_summary": (str, ...),
        "calculated_due_date": (date | None, None),
        "calculated_discount_date": (date | None, None),
    },
    "payment-method": {
        "name": (str, ...),
        "kind": (
            Literal["cash", "check", "credit_card", "debit_card", "gift_card", "e_check", "ach", "other"],
            ...,
        ),
    },
    "sales-tax-code": {
        "code": (str, ...),
        "description": (str | None, None),
        "taxable": (bool, ...),
    },
    "sales-rep": {
        "name": (str, ...),
        "initials": (str, ...),
        "name_type": (Literal["employee", "vendor", "other_name"], ...),
        "name_id": (str, ...),
        "source_type": (Literal["employee", "vendor", "other_name"], ...),
        "source_name": (str, ...),
    },
    "ship-method": {
        "name": (str, ...),
        "display_order": (int, ...),
    },
    "customer-message": {
        "name": (str, ...),
        "text": (str, ...),
        "display_order": (int, ...),
    },
}


def _class_name(noun: str) -> str:
    return "".join(part.title() for part in noun.split("-"))


def _selector_field(noun: str) -> str:
    return noun.replace("-", "_")


def _record_model(noun: str) -> type[BaseModel]:
    return create_model(
        f"{_class_name(noun)}Output",
        __config__=ConfigDict(extra="forbid"),
        **_COMMON_RECORD_FIELDS,
        **_PUBLIC_FIELDS[noun],
    )


def _write_model(noun: str, record_model: type[BaseModel], verb: str) -> type[BaseModel]:
    additions: dict[str, tuple[object, object]] = {
        "dry_run": (bool, False),
        "warnings": (list[str], Field(default_factory=list)),
    }
    if verb == "update":
        additions.update(
            changed_fields=(list[str], Field(default_factory=list)),
            merged_over_versions=(list[int], Field(default_factory=list)),
            affected_descendant_ids=(list[str], Field(default_factory=list)),
        )
    elif verb in ("activate", "deactivate"):
        additions.update(
            changed=(bool, ...),
            affected_ids=(list[str], Field(default_factory=list)),
        )
    return create_model(
        f"{_class_name(noun)}{verb.title()}Output",
        __base__=record_model,
        **additions,
    )


def _selector_model(noun: str, *, show: bool = False) -> type[BaseModel]:
    fields: dict[str, tuple[object, object]] = {
        _selector_field(noun): (str, Field(description=f"{noun} id or canonical name")),
    }
    if show and noun == "term":
        fields["transaction_date"] = (date | None, None)
    return create_model(
        f"{_class_name(noun)}{'Show' if show else 'Selector'}Input",
        __config__=ConfigDict(extra="forbid", str_strip_whitespace=True),
        **fields,
    )


def _update_model(noun: str, create_input: type[BaseModel]) -> type[BaseModel]:
    fields: dict[str, tuple[object, object]] = {
        _selector_field(noun): (str, Field(description=f"{noun} id or canonical name")),
        "expected_version": (int | None, Field(default=None, ge=1)),
    }
    for name, model_field in create_input.model_fields.items():
        fields[name] = (model_field.annotation | None, None)
    return create_model(
        f"{_class_name(noun)}UpdateInput",
        __config__=ConfigDict(extra="forbid", str_strip_whitespace=True),
        **fields,
    )


def _active_model(noun: str, *, cascade: bool) -> type[BaseModel]:
    fields: dict[str, tuple[object, object]] = {
        _selector_field(noun): (str, Field(description=f"{noun} id or canonical name")),
        "expected_version": (int | None, Field(default=None, ge=1)),
    }
    if cascade:
        fields["cascade"] = (bool, False)
    return create_model(
        f"{_class_name(noun)}{'Deactivate' if cascade else 'Activate'}Input",
        __config__=ConfigDict(extra="forbid", str_strip_whitespace=True),
        **fields,
    )


def _public_values(
    s: Session,
    noun: str,
    row: dict,
    *,
    transaction_date: date | None = None,
) -> dict:
    values = profiles.project_profile_record(
        s.company,
        noun,
        row,
        transaction_date=transaction_date,
    )
    values["created_at"] = localize(s, values["created_at"])
    values["updated_at"] = localize(s, values["updated_at"])
    return values


def _model_output(model: type[BaseModel], values: dict, **extra) -> BaseModel:
    available = {name: values[name] for name in model.model_fields if name in values}
    return model.model_validate({**available, **extra})


def _resolve(s: Session, noun: str, selector: str) -> dict:
    definition = get_list_definition(noun)
    assert definition is not None
    return list_service.resolve_selector(s.company, profiles.TABLES[noun], definition, selector)


def _sales_rep_expressions() -> tuple[dict[str, sa.ColumnElement], dict[str, sa.ColumnElement]]:
    table = schema.sales_reps
    source_name = sa.case(
        (
            table.c.name_type == "employee",
            sa.select(schema.employees.c.name).where(schema.employees.c.id == table.c.name_id).scalar_subquery(),
        ),
        (
            table.c.name_type == "vendor",
            sa.select(schema.vendors.c.name).where(schema.vendors.c.id == table.c.name_id).scalar_subquery(),
        ),
        else_=sa.select(schema.other_names.c.name).where(schema.other_names.c.id == table.c.name_id).scalar_subquery(),
    )
    return {"source_name": sa.func.lower(source_name)}, {"source_type": table.c.name_type}


def _version_meta(s: Session, noun: str, row: dict, changes: set[str], expected_version: int | None):
    record_type = noun.replace("-", "_")
    writer = current_writer(s.company, record_type, row["id"], row)
    return check_update(
        current_version=row["version"],
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


def _register_supporting_noun(noun: str) -> None:
    create_input = profiles.PROFILE_MODELS[noun]
    record_output = _record_model(noun)
    create_output = _write_model(noun, record_output, "create")
    update_input = _update_model(noun, create_input)
    update_output = _write_model(noun, record_output, "update")
    show_input = _selector_model(noun, show=True)
    activate_input = _active_model(noun, cascade=False)
    deactivate_input = _active_model(noun, cascade=noun in profiles.HIERARCHICAL_NOUNS)
    active_output = _write_model(noun, record_output, "activate")
    deactivate_output = _write_model(noun, record_output, "deactivate")
    list_output = ListOutput[record_output]
    selector_field = _selector_field(noun)

    def create_plan(inp: BaseModel, ctx: Context, s: Session) -> Plan:
        mutation = profiles.plan_profile_create(
            s.company,
            noun,
            inp.model_dump(mode="python"),
            actor_id=s.actor.id,
            via=ctx.interface.value,
        )
        values = _public_values(s, noun, dict(mutation.after))
        return Plan(preview=_model_output(create_output, values), data={"mutation": mutation})

    def create_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        mutation = plan.data["mutation"]
        profiles.persist_profile_mutation(s.company, mutation)
        touched = Touched(
            noun.replace("-", "_"),
            str(mutation.after["id"]),
            "create",
            None,
            1,
            dict(mutation.after),
            db="company",
        )
        return Applied(plan.preview, [touched], f"created {noun} {mutation.after.get('name', mutation.after.get('code'))}")

    def update_plan(inp: BaseModel, ctx: Context, s: Session) -> Plan:
        row = _resolve(s, noun, getattr(inp, selector_field))
        changes = {
            name: getattr(inp, name)
            for name in inp.model_fields_set
            if name not in {selector_field, "expected_version"}
        }
        update = profiles.plan_profile_update(
            s.company,
            noun,
            row["id"],
            changes,
            actor_id=s.actor.id,
            via=ctx.interface.value,
        )
        actual_changes = set(changes) if update.mutation is not None else set()
        meta = _version_meta(s, noun, row, actual_changes, inp.expected_version)
        after = dict(update.mutation.after) if update.mutation is not None else row
        warnings = []
        warning = list_service.blind_write_warning(meta)
        if warning:
            warnings.append(warning)
        values = _public_values(s, noun, after)
        preview = _model_output(
            update_output,
            values,
            warnings=warnings,
            changed_fields=meta.changed_fields,
            merged_over_versions=meta.merged_over_versions,
            affected_descendant_ids=[item.record_id for item in update.projections],
        )
        return Plan(preview=preview, data={"update": update})

    def update_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        update = plan.data["update"]
        if update.mutation is None:
            return Applied(plan.preview, [], "no change")
        profiles.persist_profile_update(s.company, update)
        mutation = update.mutation
        touched = Touched(
            noun.replace("-", "_"),
            str(mutation.after["id"]),
            "update",
            int(mutation.before["version"]),
            int(mutation.after["version"]),
            dict(mutation.after),
            dict(mutation.before),
            db="company",
        )
        return Applied(plan.preview, [touched], f"updated {noun} {mutation.after.get('name', mutation.after.get('code'))}")

    def show_plan(inp: BaseModel, ctx: Context, s: Session) -> Plan:
        row = _resolve(s, noun, getattr(inp, selector_field))
        values = _public_values(
            s,
            noun,
            row,
            transaction_date=getattr(inp, "transaction_date", None),
        )
        return Plan(preview=_model_output(record_output, values))

    def list_plan(inp: ProfileListInput, ctx: Context, s: Session) -> Plan:
        definition = get_list_definition(noun)
        assert definition is not None
        search_expressions = None
        filter_expressions = None
        if noun == "sales-rep":
            search_expressions, filter_expressions = _sales_rep_expressions()
        rows = list_service.list_rows(
            s.company,
            profiles.TABLES[noun],
            definition,
            query=inp.query,
            filters=inp.filter,
            sort=inp.sort,
            direction=inp.direction,
            include_inactive=inp.include_inactive,
            search_expressions=search_expressions,
            filter_expressions=filter_expressions,
        )
        items = [_model_output(record_output, _public_values(s, noun, row)) for row in rows]
        return Plan(preview=list_output(items=items, count=len(items)))

    def active_plan(active: bool):
        def planner(inp: BaseModel, ctx: Context, s: Session) -> Plan:
            row = _resolve(s, noun, getattr(inp, selector_field))
            change = profiles.plan_profile_active_change(
                s.company,
                noun,
                row["id"],
                active,
                actor_id=s.actor.id,
                via=ctx.interface.value,
                cascade=bool(getattr(inp, "cascade", False)),
            )
            if change.changed:
                _version_meta(s, noun, row, {"active"}, inp.expected_version)
            requested = dict(change.mutations[0].after) if change.changed else row
            values = _public_values(s, noun, requested)
            model = active_output if active else deactivate_output
            preview = _model_output(
                model,
                values,
                changed=change.changed,
                affected_ids=list(change.affected_ids),
            )
            return Plan(preview=preview, data={"change": change})

        return planner

    def active_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        change = plan.data["change"]
        if not change.changed:
            return Applied(plan.preview, [], "no change")
        profiles.persist_profile_active_change(s.company, change)
        touched = [
            Touched(
                noun.replace("-", "_"),
                str(mutation.after["id"]),
                mutation.action,
                int(mutation.before["version"]),
                int(mutation.after["version"]),
                dict(mutation.after),
                dict(mutation.before),
                db="company",
            )
            for mutation in change.mutations
        ]
        return Applied(plan.preview, touched, f"{change.mutations[0].action}d {noun}")

    models = LifecycleModels(
        create=ModelPair(create_input, create_output),
        update=ModelPair(update_input, update_output),
        show=ModelPair(show_input, record_output),
        list=ModelPair(ProfileListInput, list_output),
        activate=ModelPair(activate_input, active_output),
        deactivate=ModelPair(deactivate_input, deactivate_output),
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
        noun,
        models=models,
        callbacks=callbacks,
        selector_field=selector_field,
        error_codes={"update": ("E_TYPE_CHANGE",)},
    )


for _noun in profiles.PROFILE_MODELS:
    _register_supporting_noun(_noun)
