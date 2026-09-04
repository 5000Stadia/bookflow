"""Unit-of-measure aggregates and exact quantity conversion.

Unit children are normalized storage rows owned by one versioned set.  Public
values remain decimal strings; integer nano- and micro-units are the only
numeric values used for persistence and arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import sqlalchemy as sa

from bookflow.company import list_service, schema
from bookflow.company.lists import get_list_definition, normalize_display_name
from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    convert_quantity_micro_units,
    format_quantity_micro_units,
    format_unit_factor_nano_units,
    parse_quantity_micro_units,
    parse_unit_factor_nano_units,
)
from bookflow.core.ids import new_id
from bookflow.core.session import now_iso
from bookflow.storage.engine import Database


UNIT_NOUN = "unit-of-measure"
UNIT_RECORD_TYPE = "unit_of_measure"


def _error(field: str, problem: str, *, code: str = "E_VALIDATION", **details: Any) -> BookflowError:
    return BookflowError(code, details={"fields": [{"field": field, "problem": problem}], **details})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class UnitConversionInput(StrictModel):
    id: str | None = Field(default=None, min_length=26, max_length=26)
    name: str = Field(min_length=1, max_length=200)
    abbreviation: str = Field(min_length=1, max_length=32)
    is_base: bool = False
    base_factor: str

    @field_validator("base_factor")
    @classmethod
    def _exact_factor(cls, value: str) -> str:
        stored = parse_unit_factor_nano_units(value, field="base_factor")
        return format_unit_factor_nano_units(stored, field="base_factor")


class UnitOfMeasureInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    default_purchase_unit_id: str | None = Field(default=None, min_length=26, max_length=26)
    default_sales_unit_id: str | None = Field(default=None, min_length=26, max_length=26)
    default_shipping_unit_id: str | None = Field(default=None, min_length=26, max_length=26)
    units: list[UnitConversionInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_collection(self) -> "UnitOfMeasureInput":
        bases = [unit for unit in self.units if unit.is_base]
        if len(bases) != 1:
            raise ValueError("units require exactly one base unit")
        if bases[0].base_factor != "1":
            raise ValueError("the base unit factor must be exactly 1")
        names = [unicodedata.normalize("NFC", unit.name.casefold()) for unit in self.units]
        abbreviations = [
            unicodedata.normalize("NFC", unit.abbreviation.casefold()) for unit in self.units
        ]
        if len(names) != len(set(names)):
            raise ValueError("active unit names must be unique after normalization")
        if len(abbreviations) != len(set(abbreviations)):
            raise ValueError("active unit abbreviations must be unique after normalization")
        return self


class UnitConversionOutput(StrictModel):
    id: str
    position: int
    active: bool
    name: str
    abbreviation: str
    is_base: bool
    base_factor: str


class UnitOfMeasureOutput(StrictModel):
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str
    active: bool
    seed_key: str | None = None
    name: str
    default_purchase_unit_id: str | None
    default_sales_unit_id: str | None
    default_shipping_unit_id: str | None
    units: list[UnitConversionOutput]
    base_unit: UnitConversionOutput
    default_purchase_unit: UnitConversionOutput | None
    default_sales_unit: UnitConversionOutput | None
    default_shipping_unit: UnitConversionOutput | None
    related_unit_count: int


class QuantityConversion(StrictModel):
    unit_of_measure_id: str
    source_unit_id: str
    target_unit_id: str
    source_quantity: str
    source_base_factor: str
    target_base_factor: str
    target_quantity: str


@dataclass(frozen=True)
class UnitMutation:
    action: Literal["create", "update", "activate", "deactivate"]
    before: Mapping[str, Any] | None
    after: Mapping[str, Any]
    before_children: tuple[Mapping[str, Any], ...]
    after_children: tuple[Mapping[str, Any], ...]
    changed_fields: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.before is None or bool(self.changed_fields)

    @property
    def before_snapshot(self) -> dict[str, Any] | None:
        if self.before is None:
            return None
        return aggregate_snapshot(self.before, self.before_children)

    @property
    def after_snapshot(self) -> dict[str, Any]:
        return aggregate_snapshot(self.after, self.after_children)


def parse_unit_input(payload: Mapping[str, Any]) -> UnitOfMeasureInput:
    """Parse the complete aggregate and translate validation into one stable code."""
    list_service.assert_no_floats(payload)
    try:
        return UnitOfMeasureInput.model_validate(dict(payload))
    except ValidationError as exc:
        fields = [
            {
                "field": ".".join(str(part) for part in item["loc"]) or "input",
                "problem": item["msg"],
            }
            for item in exc.errors(include_url=False)
        ]
        raise BookflowError("E_VALIDATION", details={"fields": fields}) from None


def _row(db: Database, record_id: str) -> dict[str, Any]:
    found = db.conn.execute(
        sa.select(schema.units_of_measure).where(schema.units_of_measure.c.id == record_id)
    ).mappings().first()
    if found is None:
        raise BookflowError(
            "E_RECORD_NOT_FOUND",
            details={"record_type": UNIT_RECORD_TYPE, "selector": record_id, "suggestions": []},
        )
    return dict(found)


def resolve_unit_set(db: Database, selector: str) -> dict[str, Any]:
    definition = get_list_definition(UNIT_NOUN)
    assert definition is not None
    return list_service.resolve_selector(db, schema.units_of_measure, definition, selector)


def _children(db: Database, owner_id: str) -> tuple[dict[str, Any], ...]:
    rows = db.conn.execute(
        sa.select(schema.unit_conversions)
        .where(schema.unit_conversions.c.unit_of_measure_id == owner_id)
        .order_by(schema.unit_conversions.c.position, schema.unit_conversions.c.id)
    ).mappings().all()
    return tuple(dict(row) for row in rows)


def _mode(db: Database) -> str:
    value = db.conn.execute(sa.select(schema.company_info.c.units_of_measure_mode)).scalar_one()
    return str(value)


def _prepared_unit(unit: UnitConversionInput) -> dict[str, Any]:
    name, name_key = normalize_display_name(unit.name, field="units.name")
    abbreviation, abbreviation_key = normalize_display_name(
        unit.abbreviation, field="units.abbreviation"
    )
    if len(abbreviation) > 32 or len(abbreviation_key) > 64:
        raise _error("units.abbreviation", "exceeds the unit-abbreviation bound")
    values: dict[str, Any] = {
        "name": name,
        "name_key": name_key,
        "abbreviation": abbreviation,
        "abbreviation_key": abbreviation_key,
        "is_base": unit.is_base,
        "base_factor_nanounits": parse_unit_factor_nano_units(
            unit.base_factor, field="base_factor"
        ),
    }
    if unit.id is not None:
        values["id"] = unit.id
    return values


def _plan_children(
    existing: Sequence[Mapping[str, Any]],
    submitted: Sequence[UnitConversionInput],
    *,
    owner_id: str,
) -> tuple[dict[str, Any], ...]:
    prepared = [_prepared_unit(unit) for unit in submitted]
    reconciled = list_service.reconcile_children(existing, prepared, semantic_key="name_key")
    active = [{**row, "unit_of_measure_id": owner_id} for row in reconciled.active]
    abbreviations = [row["abbreviation_key"] for row in active]
    if len(abbreviations) != len(set(abbreviations)):
        raise _error("units", "active unit abbreviations must be unique after normalization")
    bases = [row for row in active if row["is_base"]]
    if len(bases) != 1:
        raise _error("units", "requires exactly one active base unit")
    if bases[0]["base_factor_nanounits"] != 1_000_000_000:
        raise _error("units.base_factor", "the active base unit factor must be exactly 1")
    retired = [{**row, "unit_of_measure_id": owner_id} for row in reconciled.retired]
    inactive = [{**row, "unit_of_measure_id": owner_id} for row in reconciled.inactive]
    return (*active, *retired, *inactive)


def _active_children(children: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(child for child in children if child["active"])


def _validate_defaults(owner: Mapping[str, Any], children: Sequence[Mapping[str, Any]]) -> None:
    active_ids = {str(child["id"]) for child in children if child["active"]}
    for field in (
        "default_purchase_unit_id",
        "default_sales_unit_id",
        "default_shipping_unit_id",
    ):
        value = owner.get(field)
        if value is not None and value not in active_ids:
            raise _error(field, "must reference an active unit in this set")


def _child_input(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "abbreviation": row["abbreviation"],
        "is_base": bool(row["is_base"]),
        "base_factor": format_unit_factor_nano_units(row["base_factor_nanounits"]),
    }


def _owner_input(owner: Mapping[str, Any], children: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "name": owner["name"],
        "default_purchase_unit_id": owner["default_purchase_unit_id"],
        "default_sales_unit_id": owner["default_sales_unit_id"],
        "default_shipping_unit_id": owner["default_shipping_unit_id"],
        "units": [_child_input(child) for child in children if child["active"]],
    }


def _child_output(row: Mapping[str, Any]) -> UnitConversionOutput:
    return UnitConversionOutput(
        id=str(row["id"]),
        position=int(row["position"]),
        active=bool(row["active"]),
        name=str(row["name"]),
        abbreviation=str(row["abbreviation"]),
        is_base=bool(row["is_base"]),
        base_factor=format_unit_factor_nano_units(row["base_factor_nanounits"]),
    )


def project_unit_record(
    db: Database,
    owner: Mapping[str, Any],
    children: Sequence[Mapping[str, Any]] | None = None,
) -> UnitOfMeasureOutput:
    children = tuple(children) if children is not None else _children(db, str(owner["id"]))
    projected = [_child_output(child) for child in children]
    active = {child.id: child for child in projected if child.active}
    bases = [child for child in projected if child.active and child.is_base]
    if len(bases) != 1:
        raise _error("units", "stored unit set does not have exactly one active base")
    return UnitOfMeasureOutput(
        **{field: owner[field] for field in (
            "id", "version", "created_at", "created_by", "created_via",
            "updated_at", "updated_by", "updated_via", "active", "seed_key", "name",
            "default_purchase_unit_id", "default_sales_unit_id", "default_shipping_unit_id",
        )},
        units=projected,
        base_unit=bases[0],
        default_purchase_unit=active.get(owner["default_purchase_unit_id"]),
        default_sales_unit=active.get(owner["default_sales_unit_id"]),
        default_shipping_unit=active.get(owner["default_shipping_unit_id"]),
        related_unit_count=len(active),
    )


def aggregate_snapshot(
    owner: Mapping[str, Any], children: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    logical_children = [
        _child_output(child).model_dump(mode="python") for child in children
    ]
    return list_service.aggregate_snapshot(owner, collections={"units": logical_children})


def plan_unit_create(
    db: Database,
    payload: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    record_id: str | None = None,
    seed_key: str | None = None,
    at: str | None = None,
) -> UnitMutation:
    parsed = parse_unit_input(payload)
    identifier = record_id or new_id()
    name, name_key = normalize_display_name(parsed.name)
    definition = get_list_definition(UNIT_NOUN)
    assert definition is not None
    list_service.assert_name_available(db, schema.units_of_measure, definition, name)
    timestamp = at or now_iso()
    owner = {
        **list_service.create_metadata(actor_id, via, record_id=identifier, at=timestamp),
        "active": True,
        "seed_key": seed_key,
        "name": name,
        "name_key": name_key,
        "default_purchase_unit_id": parsed.default_purchase_unit_id,
        "default_sales_unit_id": parsed.default_sales_unit_id,
        "default_shipping_unit_id": parsed.default_shipping_unit_id,
    }
    children = _plan_children((), parsed.units, owner_id=identifier)
    _validate_defaults(owner, children)
    return UnitMutation("create", None, owner, (), children, ("name", "units"))


def plan_unit_update(
    db: Database,
    record_id: str,
    changes: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> UnitMutation | None:
    current = _row(db, record_id)
    existing = _children(db, record_id)
    payload = _owner_input(current, existing)
    payload.update(dict(changes))
    parsed = parse_unit_input(payload)
    name, name_key = normalize_display_name(parsed.name)
    definition = get_list_definition(UNIT_NOUN)
    assert definition is not None
    list_service.assert_name_available(
        db, schema.units_of_measure, definition, name, exclude_id=record_id
    )
    children = (
        _plan_children(existing, parsed.units, owner_id=record_id)
        if "units" in changes
        else existing
    )
    owner_values = {
        "name": name,
        "name_key": name_key,
        "default_purchase_unit_id": parsed.default_purchase_unit_id,
        "default_sales_unit_id": parsed.default_sales_unit_id,
        "default_shipping_unit_id": parsed.default_shipping_unit_id,
    }
    prospective = {**current, **owner_values}
    _validate_defaults(prospective, children)
    changed_fields = {
        field for field, value in owner_values.items()
        if field != "name_key" and current[field] != value
    }
    if "units" in changes:
        before_units = [_child_input(row) for row in existing if row["active"]]
        after_units = [_child_input(row) for row in children if row["active"]]
        if before_units != after_units:
            changed_fields.add("units")
    if not changed_fields:
        return None
    after = {
        **prospective,
        **list_service.update_metadata(current, actor_id, via, at=at or now_iso()),
    }
    return UnitMutation(
        "update", current, after, existing, tuple(children), tuple(sorted(changed_fields))
    )


def _validate_current_invariants(db: Database, owner: Mapping[str, Any], children: Sequence[Mapping[str, Any]]) -> None:
    active = _active_children(children)
    bases = [child for child in active if child["is_base"]]
    if len(bases) != 1 or bases[0]["base_factor_nanounits"] != 1_000_000_000:
        raise _error("units", "stored unit set violates the active-base invariant")
    _validate_defaults(owner, children)


def plan_unit_active_change(
    db: Database,
    record_id: str,
    active: bool,
    *,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> UnitMutation | None:
    current = _row(db, record_id)
    children = _children(db, record_id)
    _validate_current_invariants(db, current, children)
    if bool(current["active"]) == active:
        return None
    if not active:
        count = int(db.conn.execute(
            sa.select(sa.func.count()).select_from(schema.items).where(
                schema.items.c.active.is_(True),
                schema.items.c.unit_of_measure_set_id == record_id,
            )
        ).scalar_one())
        if count:
            raise BookflowError(
                "E_RECORD_IN_USE",
                details={
                    "record_id": record_id,
                    "dependents": [{"record_type": "item", "count": count}],
                },
            )
    after = {
        **current,
        "active": active,
        **list_service.update_metadata(current, actor_id, via, at=at or now_iso()),
    }
    return UnitMutation(
        "activate" if active else "deactivate",
        current,
        after,
        children,
        children,
        ("active",),
    )


def persist_unit_mutation(db: Database, mutation: UnitMutation) -> None:
    """Persist a validated aggregate plan inside the caller's transaction."""
    if mutation.before is None:
        db.conn.execute(schema.units_of_measure.insert().values(**dict(mutation.after)))
    else:
        db.conn.execute(
            schema.units_of_measure.update()
            .where(schema.units_of_measure.c.id == mutation.after["id"])
            .values(**{field: mutation.after[field] for field in schema.units_of_measure.c.keys()})
        )
    if "units" not in mutation.changed_fields:
        return
    owner_id = str(mutation.after["id"])
    db.conn.execute(
        schema.unit_conversions.update()
        .where(
            schema.unit_conversions.c.unit_of_measure_id == owner_id,
            schema.unit_conversions.c.active.is_(True),
        )
        .values(active=False)
    )
    existing_ids = {str(row["id"]) for row in mutation.before_children}
    for row in mutation.after_children:
        values = {field: row[field] for field in schema.unit_conversions.c.keys()}
        if str(row["id"]) in existing_ids:
            db.conn.execute(
                schema.unit_conversions.update()
                .where(schema.unit_conversions.c.id == row["id"])
                .values(**values)
            )
        else:
            db.conn.execute(schema.unit_conversions.insert().values(**values))


def list_unit_records(
    db: Database,
    *,
    query: str | None = None,
    filters: Sequence[str] = (),
    sort: str | None = None,
    direction: Literal["asc", "desc"] = "asc",
    include_inactive: bool = False,
) -> list[UnitOfMeasureOutput]:
    definition = get_list_definition(UNIT_NOUN)
    assert definition is not None
    child = schema.unit_conversions
    owner = schema.units_of_measure
    active_child = child.c.active.is_(True)
    base_name = sa.select(child.c.name).where(
        child.c.unit_of_measure_id == owner.c.id, active_child, child.c.is_base.is_(True)
    ).limit(1).scalar_subquery()
    child_names = sa.select(sa.func.group_concat(child.c.name, " ")).where(
        child.c.unit_of_measure_id == owner.c.id, active_child
    ).scalar_subquery()
    child_abbreviations = sa.select(sa.func.group_concat(child.c.abbreviation, " ")).where(
        child.c.unit_of_measure_id == owner.c.id, active_child
    ).scalar_subquery()
    count = sa.select(sa.func.count()).select_from(child).where(
        child.c.unit_of_measure_id == owner.c.id, active_child
    ).scalar_subquery()
    default_name = lambda column: sa.select(child.c.name).where(child.c.id == column).scalar_subquery()
    rows = list_service.list_rows(
        db,
        owner,
        definition,
        query=query,
        filters=filters,
        sort=sort,
        direction=direction,
        include_inactive=include_inactive,
        search_expressions={
            "unit_names": sa.func.lower(sa.func.coalesce(child_names, "")),
            "unit_abbreviations": sa.func.lower(sa.func.coalesce(child_abbreviations, "")),
        },
        filter_expressions={"base_unit": base_name.is_not(None)},
        sort_expressions={
            "base_unit": base_name,
            "default_purchase_unit": default_name(owner.c.default_purchase_unit_id),
            "default_sales_unit": default_name(owner.c.default_sales_unit_id),
            "default_shipping_unit": default_name(owner.c.default_shipping_unit_id),
            "related_unit_count": count,
        },
    )
    return [project_unit_record(db, row) for row in rows]


def convert_quantity(
    db: Database,
    unit_of_measure_id: str,
    source_unit_id: str,
    target_unit_id: str,
    quantity: str,
) -> QuantityConversion:
    """Project an exact same-set conversion with one half-even micro-unit rounding."""
    list_service.assert_no_floats(quantity, path="quantity")
    owner = _row(db, unit_of_measure_id)
    if not owner["active"]:
        raise BookflowError(
            "E_INACTIVE_REFERENCE",
            details={"record_type": UNIT_RECORD_TYPE, "record_id": unit_of_measure_id},
        )
    all_children = _children(db, unit_of_measure_id)
    _validate_current_invariants(db, owner, all_children)
    mode = _mode(db)
    if mode != "multiple_related_units":
        raise BookflowError(
            "E_FEATURE_DISABLED",
            details={
                "feature": "unit_conversion",
                "units_of_measure_mode": mode,
                "required_mode": "multiple_related_units",
            },
        )
    found = db.conn.execute(
        sa.select(schema.unit_conversions).where(
            schema.unit_conversions.c.id.in_([source_unit_id, target_unit_id]),
            schema.unit_conversions.c.unit_of_measure_id == unit_of_measure_id,
            schema.unit_conversions.c.active.is_(True),
        )
    ).mappings().all()
    by_id = {str(row["id"]): dict(row) for row in found}
    for field, identifier in (("source_unit_id", source_unit_id), ("target_unit_id", target_unit_id)):
        if identifier not in by_id:
            raise BookflowError(
                "E_RECORD_NOT_FOUND",
                details={"record_type": "unit_conversion", "field": field, "selector": identifier, "suggestions": []},
            )
    source_quantity = parse_quantity_micro_units(quantity, field="quantity")
    converted = convert_quantity_micro_units(
        source_quantity,
        by_id[source_unit_id]["base_factor_nanounits"],
        by_id[target_unit_id]["base_factor_nanounits"],
    )
    return QuantityConversion(
        unit_of_measure_id=unit_of_measure_id,
        source_unit_id=source_unit_id,
        target_unit_id=target_unit_id,
        source_quantity=format_quantity_micro_units(source_quantity),
        source_base_factor=format_unit_factor_nano_units(
            by_id[source_unit_id]["base_factor_nanounits"]
        ),
        target_base_factor=format_unit_factor_nano_units(
            by_id[target_unit_id]["base_factor_nanounits"]
        ),
        target_quantity=format_quantity_micro_units(converted),
    )


def validate_unit_assignment(db: Database, unit_of_measure_id: str | None) -> None:
    """Validate a new item-to-set assignment against the company's current mode.

    Clearing an assignment is always allowed.  Definition lifecycle operations
    intentionally do not call this helper, so changing modes preserves every
    existing set and its defaults.
    """
    if unit_of_measure_id is None:
        return
    mode = _mode(db)
    if mode == "disabled":
        raise BookflowError(
            "E_FEATURE_DISABLED",
            details={"feature": "units_of_measure", "setting": "units_of_measure_mode"},
        )
    owner = _row(db, unit_of_measure_id)
    if not owner["active"]:
        raise BookflowError(
            "E_INACTIVE_REFERENCE",
            details={"record_type": UNIT_RECORD_TYPE, "record_id": unit_of_measure_id},
        )
    children = _children(db, unit_of_measure_id)
    _validate_current_invariants(db, owner, children)
    if mode == "single_unit_per_item":
        count = sum(bool(child["active"]) for child in children)
        if count != 1:
            raise _error(
                "unit_of_measure_set_id",
                "single_unit_per_item mode requires a set with exactly one active unit",
            )


__all__ = [
    "QuantityConversion",
    "UnitConversionInput",
    "UnitConversionOutput",
    "UnitMutation",
    "UnitOfMeasureInput",
    "UnitOfMeasureOutput",
    "aggregate_snapshot",
    "convert_quantity",
    "list_unit_records",
    "parse_unit_input",
    "persist_unit_mutation",
    "plan_unit_active_change",
    "plan_unit_create",
    "plan_unit_update",
    "project_unit_record",
    "resolve_unit_set",
    "validate_unit_assignment",
]
