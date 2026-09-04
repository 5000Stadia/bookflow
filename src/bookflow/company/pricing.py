"""Fixed-percent and per-item price-level aggregates.

This row stores definitions and assignments only.  It deliberately does not
apply a price to a sales transaction; later sales forms consume these exact,
versioned definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import sqlalchemy as sa

from bookflow.company import list_service, schema
from bookflow.company.lists import get_list_definition, normalize_display_name
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_percentage_millionths, parse_percentage_millionths
from bookflow.core.ids import new_id
from bookflow.core.money import Money, is_currency
from bookflow.core.session import now_iso
from bookflow.storage.engine import Database


PRICE_NOUN = "price-level"
PRICE_RECORD_TYPE = "price_level"
MIN_PERCENT = -100_000_000
MAX_PERCENT = 1_000_000_000_000
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def _error(field: str, problem: str, *, code: str = "E_VALIDATION", **details: Any) -> BookflowError:
    return BookflowError(code, details={"fields": [{"field": field, "problem": problem}], **details})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class PriceLevelItemInput(StrictModel):
    id: str | None = Field(default=None, min_length=26, max_length=26)
    item_id: str = Field(min_length=26, max_length=26)
    price: str | None = None
    percent: str | None = None
    adjustment_basis: Literal["standard_price", "cost", "current_custom_price"]

    @field_validator("percent")
    @classmethod
    def _exact_percent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stored = parse_percentage_millionths(value, field="percent")
        if not MIN_PERCENT <= stored <= MAX_PERCENT:
            raise _error(
                "percent", "must be from -100 through 1000000", code="E_VALUE_RANGE"
            )
        return format_percentage_millionths(stored, field="percent")

    @model_validator(mode="after")
    def _one_adjustment(self) -> "PriceLevelItemInput":
        if (self.price is None) == (self.percent is None):
            raise ValueError("exactly one of price or percent is required")
        return self


class PriceLevelInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["fixed_percent", "per_item"]
    currency: str | None = None
    rounding_mode: Literal["nearest", "up", "down"] = "nearest"
    rounding_increment: str | None = None
    rounding_offset: str | None = None
    percent: str | None = None
    items: list[PriceLevelItemInput] | None = None

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        if value is not None and not is_currency(value):
            raise ValueError("must be an uppercase ISO 4217 currency code")
        return value

    @field_validator("percent")
    @classmethod
    def _fixed_percent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stored = parse_percentage_millionths(value, field="percent")
        if not MIN_PERCENT <= stored <= MAX_PERCENT:
            raise _error(
                "percent", "must be from -100 through 1000000", code="E_VALUE_RANGE"
            )
        return format_percentage_millionths(stored, field="percent")

    @model_validator(mode="after")
    def _discriminator(self) -> "PriceLevelInput":
        if self.kind == "fixed_percent":
            if self.percent is None:
                raise ValueError("fixed_percent requires percent")
            if "items" in self.model_fields_set:
                raise ValueError("fixed_percent forbids items")
        else:
            if "percent" in self.model_fields_set:
                raise ValueError("per_item forbids percent")
            if self.items is None:
                raise ValueError("per_item requires items")
        return self


class MoneyOutput(StrictModel):
    amount: str
    currency: str
    minor_units: int


class PriceLevelItemOutput(StrictModel):
    id: str
    position: int
    active: bool
    item_id: str
    item_name: str
    price: MoneyOutput | None
    percent: str | None
    adjustment_basis: Literal["standard_price", "cost", "current_custom_price"]


class PriceLevelOutput(StrictModel):
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
    # Put the zero-child valid discriminator first so generated output examples
    # remain model-valid without inventing a percentage.
    kind: Literal["per_item", "fixed_percent"]
    currency: str | None
    resolved_currency: str
    rounding_mode: Literal["nearest", "up", "down"]
    rounding_increment: MoneyOutput
    rounding_offset: MoneyOutput
    percent: str | None
    items: list[PriceLevelItemOutput]
    item_count: int
    fixed_percent_or_item_count: str
    rounding_summary: str

    @model_validator(mode="after")
    def _discriminator(self) -> "PriceLevelOutput":
        if self.kind == "fixed_percent":
            if self.percent is None or any(item.active for item in self.items):
                raise ValueError("fixed_percent output requires percent and forbids active items")
        elif self.percent is not None:
            raise ValueError("per_item output forbids percent")
        return self


@dataclass(frozen=True)
class PriceMutation:
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


def parse_price_level_input(payload: Mapping[str, Any]) -> PriceLevelInput:
    """Parse a complete, discriminator-strict price-level definition."""
    list_service.assert_no_floats(payload)
    try:
        return PriceLevelInput.model_validate(dict(payload))
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
        sa.select(schema.price_levels).where(schema.price_levels.c.id == record_id)
    ).mappings().first()
    if found is None:
        raise BookflowError(
            "E_RECORD_NOT_FOUND",
            details={"record_type": PRICE_RECORD_TYPE, "selector": record_id, "suggestions": []},
        )
    return dict(found)


def resolve_price_level(db: Database, selector: str) -> dict[str, Any]:
    definition = get_list_definition(PRICE_NOUN)
    assert definition is not None
    return list_service.resolve_selector(db, schema.price_levels, definition, selector)


def _children(db: Database, owner_id: str) -> tuple[dict[str, Any], ...]:
    rows = db.conn.execute(
        sa.select(schema.price_level_items)
        .where(schema.price_level_items.c.price_level_id == owner_id)
        .order_by(schema.price_level_items.c.position, schema.price_level_items.c.id)
    ).mappings().all()
    return tuple(dict(row) for row in rows)


def _home_currency(db: Database) -> str:
    return str(db.conn.execute(sa.select(schema.company_info.c.home_currency)).scalar_one())


def _resolved_currency(db: Database, currency: str | None) -> str:
    resolved = currency or _home_currency(db)
    if not is_currency(resolved):
        raise _error("currency", "must be an uppercase ISO 4217 currency code")
    return resolved


def _money(value: str | None, currency: str, *, field: str, default_minor_units: int) -> Money:
    if value is None:
        result = Money(default_minor_units, currency)
    else:
        result = Money.parse(value, default_currency=currency)
    if result.currency != currency:
        raise _error(field, f"currency must be {currency}; no implicit conversion is performed")
    if result.minor_units < INT64_MIN or result.minor_units > INT64_MAX:
        raise _error(field, "does not fit signed 64-bit storage", code="E_VALUE_RANGE")
    return result


def _require_item(
    db: Database,
    item_id: str,
    *,
    field: str,
    require_active: bool,
) -> dict[str, Any]:
    found = db.conn.execute(
        sa.select(schema.items).where(schema.items.c.id == item_id)
    ).mappings().first()
    if found is None:
        raise BookflowError(
            "E_RECORD_NOT_FOUND",
            details={"record_type": "item", "field": field, "selector": item_id, "suggestions": []},
        )
    result = dict(found)
    if require_active and not result["active"]:
        raise BookflowError(
            "E_INACTIVE_REFERENCE",
            details={"record_type": "item", "field": field, "record_id": item_id},
        )
    return result


def _prepared_item(
    db: Database,
    item: PriceLevelItemInput,
    *,
    currency: str,
    existing: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    retained = existing.get(item.id or "")
    require_active = retained is None or retained["item_id"] != item.item_id
    _require_item(db, item.item_id, field="items.item_id", require_active=require_active)
    values: dict[str, Any] = {
        "item_id": item.item_id,
        "adjustment_basis": item.adjustment_basis,
        "price_minor_units": None,
        "price_currency": None,
        "percent_millionths": None,
    }
    if item.price is not None:
        price = _money(item.price, currency, field="items.price", default_minor_units=0)
        values.update(price_minor_units=price.minor_units, price_currency=price.currency)
    else:
        values["percent_millionths"] = parse_percentage_millionths(
            item.percent, field="items.percent"
        )
    if item.id is not None:
        values["id"] = item.id
    return values


def _plan_children(
    db: Database,
    existing_rows: Sequence[Mapping[str, Any]],
    submitted: Sequence[PriceLevelItemInput],
    *,
    owner_id: str,
    currency: str,
) -> tuple[dict[str, Any], ...]:
    existing = {str(row["id"]): row for row in existing_rows}
    prepared = [
        _prepared_item(db, item, currency=currency, existing=existing) for item in submitted
    ]
    reconciled = list_service.reconcile_children(
        existing_rows, prepared, semantic_key="item_id"
    )
    active = [{**row, "price_level_id": owner_id} for row in reconciled.active]
    retired = [{**row, "price_level_id": owner_id} for row in reconciled.retired]
    inactive = [{**row, "price_level_id": owner_id} for row in reconciled.inactive]
    return (*active, *retired, *inactive)


def _money_input(minor_units: int, currency: str) -> str:
    return str(Money(int(minor_units), str(currency)))


def _child_input(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "item_id": row["item_id"],
        "price": (
            _money_input(row["price_minor_units"], row["price_currency"])
            if row["price_minor_units"] is not None
            else None
        ),
        "percent": (
            format_percentage_millionths(row["percent_millionths"])
            if row["percent_millionths"] is not None
            else None
        ),
        "adjustment_basis": row["adjustment_basis"],
    }


def _owner_input(owner: Mapping[str, Any], children: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": owner["name"],
        "kind": owner["kind"],
        "currency": owner["currency"],
        "rounding_mode": owner["rounding_mode"],
        "rounding_increment": _money_input(
            owner["rounding_increment_minor_units"], owner["rounding_increment_currency"]
        ),
        "rounding_offset": _money_input(
            owner["rounding_offset_minor_units"], owner["rounding_offset_currency"]
        ),
    }
    if owner["kind"] == "fixed_percent":
        payload["percent"] = format_percentage_millionths(owner["percent_millionths"])
    else:
        payload["items"] = [_child_input(child) for child in children if child["active"]]
    return payload


def _money_output(minor_units: int, currency: str) -> MoneyOutput:
    return MoneyOutput(**Money(int(minor_units), str(currency)).to_dict())


def _child_output(db: Database, row: Mapping[str, Any]) -> PriceLevelItemOutput:
    item = _require_item(
        db, str(row["item_id"]), field="items.item_id", require_active=False
    )
    return PriceLevelItemOutput(
        id=str(row["id"]),
        position=int(row["position"]),
        active=bool(row["active"]),
        item_id=str(row["item_id"]),
        item_name=str(item["full_name"]),
        price=(
            _money_output(row["price_minor_units"], row["price_currency"])
            if row["price_minor_units"] is not None
            else None
        ),
        percent=(
            format_percentage_millionths(row["percent_millionths"])
            if row["percent_millionths"] is not None
            else None
        ),
        adjustment_basis=str(row["adjustment_basis"]),
    )


def project_price_level(
    db: Database,
    owner: Mapping[str, Any],
    children: Sequence[Mapping[str, Any]] | None = None,
) -> PriceLevelOutput:
    children = tuple(children) if children is not None else _children(db, str(owner["id"]))
    projected = [_child_output(db, child) for child in children]
    active_count = sum(child.active for child in projected)
    percent = (
        format_percentage_millionths(owner["percent_millionths"])
        if owner["percent_millionths"] is not None
        else None
    )
    rounding_increment = _money_output(
        owner["rounding_increment_minor_units"], owner["rounding_increment_currency"]
    )
    rounding_offset = _money_output(
        owner["rounding_offset_minor_units"], owner["rounding_offset_currency"]
    )
    summary = (
        f"{owner['rounding_mode']} {rounding_increment.amount} "
        f"offset {rounding_offset.amount} {rounding_increment.currency}"
    )
    return PriceLevelOutput(
        **{field: owner[field] for field in (
            "id", "version", "created_at", "created_by", "created_via",
            "updated_at", "updated_by", "updated_via", "active", "seed_key", "name",
            "kind", "currency", "rounding_mode",
        )},
        resolved_currency=str(owner["rounding_increment_currency"]),
        rounding_increment=rounding_increment,
        rounding_offset=rounding_offset,
        percent=percent,
        items=projected,
        item_count=active_count,
        fixed_percent_or_item_count=(percent if percent is not None else str(active_count)),
        rounding_summary=summary,
    )


def aggregate_snapshot(
    owner: Mapping[str, Any], children: Sequence[Mapping[str, Any]], db: Database | None = None
) -> dict[str, Any]:
    logical_children = []
    for child in children:
        logical = {
            "id": child["id"],
            "position": child["position"],
            "active": bool(child["active"]),
            "item_id": child["item_id"],
            "price": (
                Money(int(child["price_minor_units"]), str(child["price_currency"])).to_dict()
                if child["price_minor_units"] is not None
                else None
            ),
            "percent": (
                format_percentage_millionths(child["percent_millionths"])
                if child["percent_millionths"] is not None
                else None
            ),
            "adjustment_basis": child["adjustment_basis"],
        }
        logical_children.append(logical)
    snapshot = dict(owner)
    if snapshot.get("percent_millionths") is not None:
        snapshot["percent"] = format_percentage_millionths(snapshot.pop("percent_millionths"))
    else:
        snapshot.pop("percent_millionths", None)
        snapshot["percent"] = None
    for prefix in ("rounding_increment", "rounding_offset"):
        snapshot[prefix] = Money(
            int(snapshot.pop(f"{prefix}_minor_units")),
            str(snapshot.pop(f"{prefix}_currency")),
        ).to_dict()
    return list_service.aggregate_snapshot(
        snapshot, collections={"items": logical_children}
    )


def _owner_values(db: Database, parsed: PriceLevelInput) -> dict[str, Any]:
    name, name_key = normalize_display_name(parsed.name)
    currency = _resolved_currency(db, parsed.currency)
    increment = _money(
        parsed.rounding_increment,
        currency,
        field="rounding_increment",
        default_minor_units=1,
    )
    if increment.minor_units <= 0:
        raise _error(
            "rounding_increment", "must be at least one minor unit", code="E_VALUE_RANGE"
        )
    offset = _money(
        parsed.rounding_offset,
        currency,
        field="rounding_offset",
        default_minor_units=0,
    )
    percent = (
        parse_percentage_millionths(parsed.percent, field="percent")
        if parsed.percent is not None
        else None
    )
    return {
        "name": name,
        "name_key": name_key,
        "kind": parsed.kind,
        "currency": parsed.currency,
        "rounding_mode": parsed.rounding_mode,
        "rounding_increment_minor_units": increment.minor_units,
        "rounding_increment_currency": increment.currency,
        "rounding_offset_minor_units": offset.minor_units,
        "rounding_offset_currency": offset.currency,
        "percent_millionths": percent,
    }


def plan_price_level_create(
    db: Database,
    payload: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    record_id: str | None = None,
    seed_key: str | None = None,
    at: str | None = None,
) -> PriceMutation:
    parsed = parse_price_level_input(payload)
    identifier = record_id or new_id()
    values = _owner_values(db, parsed)
    definition = get_list_definition(PRICE_NOUN)
    assert definition is not None
    list_service.assert_name_available(db, schema.price_levels, definition, values["name"])
    timestamp = at or now_iso()
    owner = {
        **list_service.create_metadata(actor_id, via, record_id=identifier, at=timestamp),
        "active": True,
        "seed_key": seed_key,
        **values,
    }
    children = (
        _plan_children(
            db,
            (),
            parsed.items or (),
            owner_id=identifier,
            currency=values["rounding_increment_currency"],
        )
        if parsed.kind == "per_item"
        else ()
    )
    changed = ("name", "percent") if parsed.kind == "fixed_percent" else ("name", "items")
    return PriceMutation("create", None, owner, (), tuple(children), changed)


def plan_price_level_update(
    db: Database,
    record_id: str,
    changes: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> PriceMutation | None:
    current = _row(db, record_id)
    if "kind" in changes and changes["kind"] != current["kind"]:
        raise BookflowError("E_TYPE_CHANGE", details={"field": "kind"})
    existing = _children(db, record_id)
    payload = _owner_input(current, existing)
    payload.update(dict(changes))
    parsed = parse_price_level_input(payload)
    values = _owner_values(db, parsed)
    definition = get_list_definition(PRICE_NOUN)
    assert definition is not None
    list_service.assert_name_available(
        db, schema.price_levels, definition, values["name"], exclude_id=record_id
    )
    children = (
        _plan_children(
            db,
            existing,
            parsed.items or (),
            owner_id=record_id,
            currency=values["rounding_increment_currency"],
        )
        if current["kind"] == "per_item" and "items" in changes
        else existing
    )
    if current["kind"] == "per_item" and "items" not in changes:
        resolved_currency = str(values["rounding_increment_currency"])
        mismatched = [
            str(child["id"])
            for child in existing
            if child["active"]
            and child["price_minor_units"] is not None
            and child["price_currency"] != resolved_currency
        ]
        if mismatched:
            raise _error(
                "items.price",
                f"currency must be {resolved_currency}; resubmit fixed item prices when changing currency",
                record_ids=mismatched,
            )
    logical_groups = {
        "name": ("name",),
        "kind": ("kind",),
        "currency": ("currency",),
        "rounding_mode": ("rounding_mode",),
        "rounding_increment": (
            "rounding_increment_minor_units", "rounding_increment_currency",
        ),
        "rounding_offset": (
            "rounding_offset_minor_units", "rounding_offset_currency",
        ),
        "percent": ("percent_millionths",),
    }
    changed_fields = {
        logical
        for logical, columns in logical_groups.items()
        if any(current[column] != values[column] for column in columns)
    }
    if "items" in changes:
        before_items = [_child_input(row) for row in existing if row["active"]]
        after_items = [_child_input(row) for row in children if row["active"]]
        if before_items != after_items:
            changed_fields.add("items")
    if not changed_fields:
        return None
    after = {
        **current,
        **values,
        **list_service.update_metadata(current, actor_id, via, at=at or now_iso()),
    }
    return PriceMutation(
        "update", current, after, existing, tuple(children), tuple(sorted(changed_fields))
    )


def _validate_current(db: Database, owner: Mapping[str, Any], children: Sequence[Mapping[str, Any]]) -> None:
    parse_price_level_input(_owner_input(owner, children))
    resolved = _resolved_currency(db, owner["currency"])
    if owner["rounding_increment_currency"] != resolved or owner["rounding_offset_currency"] != resolved:
        raise _error("currency", "stored rounding currency is inconsistent with the level currency")
    active_children = [child for child in children if child["active"]]
    if owner["kind"] == "fixed_percent" and active_children:
        raise _error("items", "a fixed_percent level cannot have active per-item adjustments")
    for child in active_children:
        has_price = child["price_minor_units"] is not None
        has_percent = child["percent_millionths"] is not None
        if has_price == has_percent:
            raise _error("items", "each active item requires exactly one of price or percent")
        if has_price and child["price_currency"] != resolved:
            raise _error("items.price", "stored price currency is inconsistent with the level currency")


def plan_price_level_active_change(
    db: Database,
    record_id: str,
    active: bool,
    *,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> PriceMutation | None:
    current = _row(db, record_id)
    children = _children(db, record_id)
    _validate_current(db, current, children)
    if bool(current["active"]) == active:
        return None
    after = {
        **current,
        "active": active,
        **list_service.update_metadata(current, actor_id, via, at=at or now_iso()),
    }
    return PriceMutation(
        "activate" if active else "deactivate",
        current,
        after,
        children,
        children,
        ("active",),
    )


def persist_price_mutation(db: Database, mutation: PriceMutation) -> None:
    """Persist a validated aggregate plan inside the caller's transaction."""
    if mutation.before is None:
        db.conn.execute(schema.price_levels.insert().values(**dict(mutation.after)))
    else:
        db.conn.execute(
            schema.price_levels.update()
            .where(schema.price_levels.c.id == mutation.after["id"])
            .values(**{field: mutation.after[field] for field in schema.price_levels.c.keys()})
        )
    if "items" not in mutation.changed_fields:
        return
    owner_id = str(mutation.after["id"])
    db.conn.execute(
        schema.price_level_items.update()
        .where(
            schema.price_level_items.c.price_level_id == owner_id,
            schema.price_level_items.c.active.is_(True),
        )
        .values(active=False)
    )
    existing_ids = {str(row["id"]) for row in mutation.before_children}
    for row in mutation.after_children:
        values = {field: row[field] for field in schema.price_level_items.c.keys()}
        if str(row["id"]) in existing_ids:
            db.conn.execute(
                schema.price_level_items.update()
                .where(schema.price_level_items.c.id == row["id"])
                .values(**values)
            )
        else:
            db.conn.execute(schema.price_level_items.insert().values(**values))


def list_price_levels(
    db: Database,
    *,
    query: str | None = None,
    filters: Sequence[str] = (),
    sort: str | None = None,
    direction: Literal["asc", "desc"] = "asc",
    include_inactive: bool = False,
) -> list[PriceLevelOutput]:
    definition = get_list_definition(PRICE_NOUN)
    assert definition is not None
    owner = schema.price_levels
    child = schema.price_level_items
    item = schema.items
    active_child = child.c.active.is_(True)
    item_names = sa.select(sa.func.group_concat(item.c.full_name, " ")).select_from(
        child.join(item, item.c.id == child.c.item_id)
    ).where(child.c.price_level_id == owner.c.id, active_child).scalar_subquery()
    item_count = sa.select(sa.func.count()).select_from(child).where(
        child.c.price_level_id == owner.c.id, active_child
    ).scalar_subquery()
    resolved_currency = sa.func.coalesce(
        owner.c.currency,
        sa.select(schema.company_info.c.home_currency).limit(1).scalar_subquery(),
    )
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
            "per_item_item_names": sa.func.lower(sa.func.coalesce(item_names, "")),
        },
        filter_expressions={"currency": resolved_currency},
        sort_expressions={
            "percent": owner.c.percent_millionths,
            "item_count": item_count,
            "currency": resolved_currency,
        },
    )
    return [project_price_level(db, row) for row in rows]


__all__ = [
    "MAX_PERCENT",
    "MIN_PERCENT",
    "MoneyOutput",
    "PriceLevelInput",
    "PriceLevelItemInput",
    "PriceLevelItemOutput",
    "PriceLevelOutput",
    "PriceMutation",
    "aggregate_snapshot",
    "list_price_levels",
    "parse_price_level_input",
    "persist_price_mutation",
    "plan_price_level_active_change",
    "plan_price_level_create",
    "plan_price_level_update",
    "project_price_level",
    "resolve_price_level",
]
