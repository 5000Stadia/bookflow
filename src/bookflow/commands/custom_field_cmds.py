"""Custom-field definition lifecycle commands over the typed domain service."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
import sqlalchemy as sa

from bookflow.commands.list_factory import LifecycleCallbacks, LifecycleModels, ModelPair, register_lifecycle
from bookflow.company import custom_fields, list_service, schema
from bookflow.company.lists import get_list_definition
from bookflow.core.context import Context
from bookflow.core.models import ListOutput, WriteOutput
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.core.session import Session, localize, now_iso


class CustomFieldScopeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    record_type: str
    position: int
    active: bool


class CustomFieldChoiceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    value: str
    position: int
    active: bool


class CustomFieldOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str
    active: bool
    name: str
    kind: custom_fields.CustomFieldKind
    position: int
    required: bool
    default: Any | None
    target_types: list[str]
    scopes: list[CustomFieldScopeOutput]
    choices: list[CustomFieldChoiceOutput]


class CustomFieldCreateOutput(WriteOutput, CustomFieldOutput):
    pass


class CustomFieldUpdateOutput(WriteOutput, CustomFieldOutput):
    changed_fields: list[str] = Field(default_factory=list)
    previous_version: int | None = None


class CustomFieldActiveOutput(WriteOutput, CustomFieldOutput):
    changed: bool
    affected_ids: list[str] = Field(default_factory=list)


class CustomFieldUpdateInput(custom_fields.CustomFieldDefinitionUpdate):
    custom_field: str = Field(description="Custom-field definition id or name")
    expected_version: int | None = Field(default=None, ge=1)


class CustomFieldSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    custom_field: str = Field(description="Custom-field definition id or name")


class CustomFieldActiveInput(CustomFieldSelector):
    expected_version: int | None = Field(default=None, ge=1)


class CustomFieldListInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str | None = None
    include_inactive: bool = False
    filter: list[str] = Field(default_factory=list)
    sort: str | None = None
    direction: Literal["asc", "desc"] = "asc"


def _resolve(s: Session, selector: str) -> dict[str, Any]:
    definition = get_list_definition("custom-field")
    assert definition is not None
    return list_service.resolve_selector(
        s.company,
        schema.custom_field_defs,
        definition,
        selector,
    )


def _output(s: Session, row: dict[str, Any]) -> dict[str, Any]:
    result = {
        field: row[field]
        for field in CustomFieldOutput.model_fields
        if field in row
    }
    result["created_at"] = localize(s, result["created_at"])
    result["updated_at"] = localize(s, result["updated_at"])
    result["target_types"] = [scope["record_type"] for scope in result["scopes"]]
    return result


def _snapshot(connection: sa.Connection, definition_id: str) -> dict[str, Any]:
    row = connection.execute(
        sa.select(schema.custom_field_defs).where(schema.custom_field_defs.c.id == definition_id)
    ).mappings().one()
    scopes = connection.execute(
        sa.select(schema.custom_field_scopes)
        .where(schema.custom_field_scopes.c.definition_id == definition_id)
        .order_by(schema.custom_field_scopes.c.position, schema.custom_field_scopes.c.id)
    ).mappings().all()
    choices = connection.execute(
        sa.select(schema.custom_field_choices)
        .where(schema.custom_field_choices.c.definition_id == definition_id)
        .order_by(schema.custom_field_choices.c.position, schema.custom_field_choices.c.id)
    ).mappings().all()
    default = (
        None
        if row["default_canonical_text"] is None
        else custom_fields.typed_value_from_canonical(row["kind"], row["default_canonical_text"])
    )
    return list_service.aggregate_snapshot(
        {**dict(row), "default": default},
        collections={"scopes": scopes, "choices": choices},
    )


@contextmanager
def _planning_copy(s: Session):
    """Clone the company into memory so mutating validation remains preview-safe."""
    raw = sqlite3.connect(":memory:", isolation_level=None)
    engine = None
    connection = None
    try:
        s.company.raw.backup(raw)
        raw.execute("PRAGMA foreign_keys=ON")
        engine = sa.create_engine(
            "sqlite://",
            creator=lambda: raw,
            poolclass=sa.pool.StaticPool,
        )
        connection = engine.connect()
        yield connection
    finally:
        if connection is not None:
            connection.close()
        if engine is not None:
            engine.dispose()
        try:
            raw.close()
        except sqlite3.ProgrammingError:
            pass


def _capturing_ids() -> tuple[list[str], Callable[[], str]]:
    from bookflow.core.ids import new_id

    captured: list[str] = []

    def generate() -> str:
        identifier = new_id()
        captured.append(identifier)
        return identifier

    return captured, generate


def _replay_ids(ids: list[str]) -> Callable[[], str]:
    iterator = iter(ids)
    return lambda: next(iterator)


def plan_create(inp: custom_fields.CustomFieldDefinitionCreate, ctx: Context, s: Session) -> Plan:
    timestamp = now_iso()
    generated, id_factory = _capturing_ids()
    with _planning_copy(s) as planning:
        after = custom_fields.create_definition(
            planning,
            inp,
            actor_id=s.actor.id,
            interface=ctx.interface.value,
            at=timestamp,
            id_factory=id_factory,
        )
        snapshot = _snapshot(planning, after["id"])
    return Plan(
        preview=CustomFieldCreateOutput(**_output(s, after)),
        data={"input": inp, "at": timestamp, "ids": generated, "snapshot": snapshot},
    )


def apply_create(plan: Plan, ctx: Context, s: Session) -> Applied:
    after = custom_fields.create_definition(
        s.company,
        plan.data["input"],
        actor_id=s.actor.id,
        interface=ctx.interface.value,
        at=plan.data["at"],
        id_factory=_replay_ids(plan.data["ids"]),
    )
    snapshot = _snapshot(s.company.conn, after["id"])
    touched = Touched("custom_field", after["id"], "create", None, 1, snapshot, db="company")
    return Applied(CustomFieldCreateOutput(**_output(s, after)), [touched], f"created custom field {after['name']}")


def _plan_change(
    s: Session,
    ctx: Context,
    definition_id: str,
    patch: custom_fields.CustomFieldDefinitionUpdate,
    expected_version: int | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[str], str]:
    before = custom_fields.read_definition(s.company, definition_id)
    before_snapshot = _snapshot(s.company.conn, definition_id)
    timestamp = now_iso()
    generated, id_factory = _capturing_ids()

    with _planning_copy(s) as planning:
        after = custom_fields.update_definition(
            planning,
            definition_id,
            patch,
            actor_id=s.actor.id,
            interface=ctx.interface.value,
            expected_version=expected_version,
            at=timestamp,
            id_factory=id_factory,
        )
        after_snapshot = _snapshot(planning, definition_id)
    return before, before_snapshot, after, after_snapshot, generated, timestamp


def plan_update(inp: CustomFieldUpdateInput, ctx: Context, s: Session) -> Plan:
    selected = _resolve(s, inp.custom_field)
    patch = custom_fields.CustomFieldDefinitionUpdate.model_validate(
        inp.model_dump(exclude={"custom_field", "expected_version"}, exclude_unset=True)
    )
    before, before_snapshot, after, after_snapshot, generated, timestamp = _plan_change(
        s,
        ctx,
        selected["id"],
        patch,
        inp.expected_version,
    )
    changed_fields = sorted(
        field
        for field in patch.model_fields_set
        if before.get(field) != after.get(field)
    )
    warnings = []
    if after["version"] != before["version"] and inp.expected_version is None:
        warnings.append(
            f"Blind write: version {before['version']} and fields {', '.join(changed_fields)} were not compared."
        )
    preview = CustomFieldUpdateOutput(
        **_output(s, after),
        changed_fields=changed_fields,
        previous_version=before["version"] if after["version"] != before["version"] else None,
        warnings=warnings,
    )
    return Plan(
        preview=preview,
        data={
            "id": selected["id"],
            "patch": patch,
            "expected_version": inp.expected_version,
            "before": before_snapshot,
            "after": after_snapshot,
            "ids": generated,
            "at": timestamp,
        },
    )


def apply_update(plan: Plan, ctx: Context, s: Session) -> Applied:
    if plan.data["before"] == plan.data["after"]:
        return Applied(plan.preview, [], "no change")
    after = custom_fields.update_definition(
        s.company,
        plan.data["id"],
        plan.data["patch"],
        actor_id=s.actor.id,
        interface=ctx.interface.value,
        expected_version=plan.data["expected_version"],
        at=plan.data["at"],
        id_factory=_replay_ids(plan.data["ids"]),
    )
    snapshot = _snapshot(s.company.conn, after["id"])
    touched = Touched(
        "custom_field",
        after["id"],
        "update",
        plan.data["before"]["version"],
        after["version"],
        snapshot,
        plan.data["before"],
        db="company",
    )
    return Applied(plan.preview, [touched], f"updated custom field {after['name']}")


def plan_show(inp: CustomFieldSelector, ctx: Context, s: Session) -> Plan:
    selected = _resolve(s, inp.custom_field)
    return Plan(preview=CustomFieldOutput(**_output(s, custom_fields.read_definition(s.company, selected["id"]))))


def plan_list(inp: CustomFieldListInput, ctx: Context, s: Session) -> Plan:
    definition = get_list_definition("custom-field")
    assert definition is not None
    filters = definition.parse_filters(inp.filter)
    sort_terms = definition.resolve_sort(inp.sort, inp.direction)
    ids = s.company.conn.execute(
        sa.select(schema.custom_field_defs.c.id).order_by(schema.custom_field_defs.c.id)
    ).scalars().all()
    rows = [custom_fields.read_definition(s.company, definition_id) for definition_id in ids]
    if not inp.include_inactive and not any(item.field == "active" for item in filters):
        rows = [row for row in rows if row["active"]]
    for item in filters:
        if item.field == "target_type":
            rows = [row for row in rows if item.value in {scope["record_type"] for scope in row["scopes"]}]
        else:
            rows = [row for row in rows if row.get(item.field) == item.value]
    if inp.query and inp.query.strip():
        needle = inp.query.strip().casefold()
        rows = [
            row
            for row in rows
            if needle in row["name"].casefold()
            or any(needle in scope["record_type"].casefold() for scope in row["scopes"])
            or any(needle in choice["value"].casefold() for choice in row["choices"])
        ]

    def sort_value(row: dict[str, Any], field: str):
        if field == "target_type":
            return tuple(scope["record_type"] for scope in row["scopes"])
        return row.get(field)

    for term in reversed(sort_terms):
        rows.sort(key=lambda row, field=term.field: sort_value(row, field), reverse=term.direction == "desc")
    items = [CustomFieldOutput(**_output(s, row)) for row in rows]
    return Plan(preview=ListOutput[CustomFieldOutput](items=items, count=len(items)))


def _active_planner(active: bool):
    def planner(inp: CustomFieldActiveInput, ctx: Context, s: Session) -> Plan:
        selected = _resolve(s, inp.custom_field)
        patch = custom_fields.CustomFieldDefinitionUpdate(active=active)
        before, before_snapshot, after, after_snapshot, generated, timestamp = _plan_change(
            s,
            ctx,
            selected["id"],
            patch,
            inp.expected_version,
        )
        changed = before_snapshot != after_snapshot
        preview = CustomFieldActiveOutput(
            **_output(s, after),
            changed=changed,
            affected_ids=[selected["id"]] if changed else [],
        )
        return Plan(
            preview=preview,
            data={
                "id": selected["id"],
                "patch": patch,
                "expected_version": inp.expected_version,
                "before": before_snapshot,
                "after": after_snapshot,
                "ids": generated,
                "at": timestamp,
            },
        )

    return planner


def apply_active(plan: Plan, ctx: Context, s: Session) -> Applied:
    if plan.data["before"] == plan.data["after"]:
        return Applied(plan.preview, [], "no change")
    after = custom_fields.update_definition(
        s.company,
        plan.data["id"],
        plan.data["patch"],
        actor_id=s.actor.id,
        interface=ctx.interface.value,
        expected_version=plan.data["expected_version"],
        at=plan.data["at"],
        id_factory=_replay_ids(plan.data["ids"]),
    )
    snapshot = _snapshot(s.company.conn, after["id"])
    action = "activate" if after["active"] else "deactivate"
    touched = Touched(
        "custom_field",
        after["id"],
        action,
        plan.data["before"]["version"],
        after["version"],
        snapshot,
        plan.data["before"],
        db="company",
    )
    return Applied(plan.preview, [touched], f"{action}d custom field {after['name']}")


register_lifecycle(
    "custom-field",
    models=LifecycleModels(
        create=ModelPair(custom_fields.CustomFieldDefinitionCreate, CustomFieldCreateOutput),
        update=ModelPair(CustomFieldUpdateInput, CustomFieldUpdateOutput),
        show=ModelPair(CustomFieldSelector, CustomFieldOutput),
        list=ModelPair(CustomFieldListInput, ListOutput[CustomFieldOutput]),
        activate=ModelPair(CustomFieldActiveInput, CustomFieldActiveOutput),
        deactivate=ModelPair(CustomFieldActiveInput, CustomFieldActiveOutput),
    ),
    callbacks=LifecycleCallbacks(
        create_plan=plan_create,
        create_apply=apply_create,
        update_plan=plan_update,
        update_apply=apply_update,
        show_plan=plan_show,
        list_plan=plan_list,
        activate_plan=_active_planner(True),
        activate_apply=apply_active,
        deactivate_plan=_active_planner(False),
        deactivate_apply=apply_active,
    ),
    selector_field="custom_field",
    required_write_role="admin",
)
