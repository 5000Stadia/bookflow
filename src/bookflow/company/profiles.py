"""Supporting Row 5 profiles, exact term rules, and seed application.

This module owns profile-specific validation and deterministic mutation plans.
The shared list command factory remains responsible for authorization,
concurrency, idempotency, and audit events.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from importlib.resources import files
import json
from typing import Any, Literal, Mapping
import unicodedata

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from bookflow.company import schema as c
from bookflow.company.lists import hierarchy_projection, normalize_display_name
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_percentage_millionths, parse_percentage_millionths
from bookflow.core.ids import new_id
from bookflow.core.session import now_iso
from bookflow.storage.engine import Database


ProfileNoun = Literal[
    "item-category",
    "class",
    "term",
    "payment-method",
    "sales-tax-code",
    "customer-type",
    "vendor-type",
    "job-type",
    "sales-rep",
    "ship-method",
    "customer-message",
]

HIERARCHICAL_NOUNS = frozenset(
    {"item-category", "class", "customer-type", "vendor-type", "job-type"}
)

TABLES: dict[str, sa.Table] = {
    "item-category": c.item_categories,
    "class": c.classes,
    "term": c.terms,
    "payment-method": c.payment_methods,
    "sales-tax-code": c.sales_tax_codes,
    "customer-type": c.customer_types,
    "vendor-type": c.vendor_types,
    "job-type": c.job_types,
    "sales-rep": c.sales_reps,
    "ship-method": c.ship_methods,
    "customer-message": c.customer_messages,
}

SOURCE_TABLES: dict[str, sa.Table] = {
    "employee": c.employees,
    "vendor": c.vendors,
    "other_name": c.other_names,
}


def _error(field: str, problem: str, *, code: str = "E_VALIDATION", **details: Any) -> BookflowError:
    return BookflowError(
        code,
        details={"fields": [{"field": field, "problem": problem}], **details},
    )


class _ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class HierarchicalProfileInput(_ProfileInput):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None


class ItemCategoryInput(HierarchicalProfileInput):
    pass


class ClassInput(HierarchicalProfileInput):
    pass


class CustomerTypeInput(HierarchicalProfileInput):
    pass


class VendorTypeInput(HierarchicalProfileInput):
    pass


class JobTypeInput(HierarchicalProfileInput):
    pass


class TermInput(_ProfileInput):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["standard", "date_driven"]
    due_days: int | None = Field(default=None, ge=0, le=365)
    discount_days: int | None = Field(default=None, ge=0, le=365)
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    due_next_month_if_within_days: int | None = Field(default=None, ge=0, le=31)
    discount_day_of_month: int | None = Field(default=None, ge=1, le=31)
    discount_percent: str | None = None

    @field_validator("discount_percent")
    @classmethod
    def _exact_discount_percent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            stored = parse_percentage_millionths(value, field="discount_percent")
        except BookflowError as exc:
            raise ValueError(exc.details["fields"][0]["problem"]) from None
        if not 0 <= stored <= 100_000_000:
            raise ValueError("must be from 0 through 100")
        return format_percentage_millionths(stored, field="discount_percent")

    @model_validator(mode="after")
    def _kind_profile(self) -> "TermInput":
        if self.kind == "standard":
            if self.due_days is None:
                raise ValueError("standard terms require due_days")
            if any(
                value is not None
                for value in (
                    self.due_day_of_month,
                    self.due_next_month_if_within_days,
                    self.discount_day_of_month,
                )
            ):
                raise ValueError("standard terms forbid date-driven fields")
            if (self.discount_days is None) != (self.discount_percent is None):
                raise ValueError("discount_days and discount_percent must be supplied together")
        else:
            if self.due_day_of_month is None or self.due_next_month_if_within_days is None:
                raise ValueError(
                    "date-driven terms require due_day_of_month and "
                    "due_next_month_if_within_days"
                )
            if self.due_days is not None or self.discount_days is not None:
                raise ValueError("date-driven terms forbid standard fields")
            if (self.discount_day_of_month is None) != (self.discount_percent is None):
                raise ValueError(
                    "discount_day_of_month and discount_percent must be supplied together"
                )
        return self


class PaymentMethodInput(_ProfileInput):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal[
        "cash",
        "check",
        "credit_card",
        "debit_card",
        "gift_card",
        "e_check",
        "ach",
        "other",
    ]


class SalesTaxCodeInput(_ProfileInput):
    code: str = Field(min_length=1, max_length=3)
    description: str | None = Field(default=None, max_length=200)
    taxable: bool


class SalesRepInput(_ProfileInput):
    name: str = Field(min_length=1, max_length=200)
    initials: str = Field(min_length=1, max_length=5)
    name_type: Literal["employee", "vendor", "other_name"]
    name_id: str = Field(min_length=26, max_length=26)


class ShipMethodInput(_ProfileInput):
    name: str = Field(min_length=1, max_length=200)
    display_order: int = Field(default=0, ge=0)


class CustomerMessageInput(_ProfileInput):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=101)
    display_order: int = Field(default=0, ge=0)


PROFILE_MODELS: dict[str, type[_ProfileInput]] = {
    "item-category": ItemCategoryInput,
    "class": ClassInput,
    "term": TermInput,
    "payment-method": PaymentMethodInput,
    "sales-tax-code": SalesTaxCodeInput,
    "customer-type": CustomerTypeInput,
    "vendor-type": VendorTypeInput,
    "job-type": JobTypeInput,
    "sales-rep": SalesRepInput,
    "ship-method": ShipMethodInput,
    "customer-message": CustomerMessageInput,
}


@dataclass(frozen=True)
class ProfileMutation:
    noun: str
    table_name: str
    action: Literal["create", "update", "activate", "deactivate"]
    before: Mapping[str, Any] | None
    after: Mapping[str, Any]


@dataclass(frozen=True)
class ProjectionUpdate:
    table_name: str
    record_id: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class ProfileUpdatePlan:
    mutation: ProfileMutation | None
    projections: tuple[ProjectionUpdate, ...] = ()


@dataclass(frozen=True)
class ProfileActivePlan:
    noun: str
    requested_id: str
    requested_active: bool
    mutations: tuple[ProfileMutation, ...]

    @property
    def changed(self) -> bool:
        return bool(self.mutations)

    @property
    def affected_ids(self) -> tuple[str, ...]:
        return tuple(str(mutation.after["id"]) for mutation in self.mutations)


class TermDates(_ProfileInput):
    due_date: date
    discount_date: date | None


class ProfileApplyResult(_ProfileInput):
    manifest_id: Literal["standard"] = "standard"
    version: int
    inserted_by_list: dict[str, int]
    preserved_by_list: dict[str, int]
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)


class StandardProfileManifest(_ProfileInput):
    manifest_id: Literal["standard"]
    version: int = Field(ge=1)
    lists: list[
        Literal["term", "payment-method", "sales-tax-code", "ship-method", "customer-message"]
    ]
    records: dict[str, list[dict[str, Any]]]

    @model_validator(mode="after")
    def _complete_and_valid(self) -> "StandardProfileManifest":
        expected = [
            "term",
            "payment-method",
            "sales-tax-code",
            "ship-method",
            "customer-message",
        ]
        if self.lists != expected or list(self.records) != expected:
            raise ValueError("standard profile lists and records must use the canonical order")
        all_seed_keys: set[str] = set()
        expected_counts = {
            "term": 6,
            "payment-method": 11,
            "sales-tax-code": 2,
            "ship-method": 5,
            "customer-message": 3,
        }
        for noun in expected:
            if len(self.records[noun]) != expected_counts[noun]:
                raise ValueError(f"standard profile has the wrong {noun} record count")
            normalized_keys: set[str] = set()
            for raw in self.records[noun]:
                seed_key = raw.get("seed_key")
                if not isinstance(seed_key, str) or not seed_key or len(seed_key) > 128:
                    raise ValueError("every standard-profile record needs a bounded seed_key")
                if seed_key in all_seed_keys:
                    raise ValueError("standard-profile seed keys must be globally unique")
                all_seed_keys.add(seed_key)
                payload = {key: value for key, value in raw.items() if key != "seed_key"}
                try:
                    parsed = parse_profile_input(noun, payload)
                except BookflowError as exc:
                    raise ValueError(
                        f"standard profile has invalid {noun} data: {exc.code}"
                    ) from None
                display = parsed.code if isinstance(parsed, SalesTaxCodeInput) else parsed.name
                normalized = unicodedata.normalize("NFC", display.casefold())
                if normalized in normalized_keys:
                    raise ValueError(f"standard profile repeats a normalized {noun} key")
                normalized_keys.add(normalized)
        return self


def parse_profile_input(noun: str, payload: Mapping[str, Any]) -> _ProfileInput:
    """Parse one profile payload and expose stable Bookflow validation errors."""
    model = PROFILE_MODELS.get(noun)
    if model is None:
        raise _error("noun", "is not a supporting-profile noun")
    try:
        return model.model_validate(dict(payload))
    except ValidationError as exc:
        fields = []
        for item in exc.errors(include_url=False):
            location = ".".join(str(part) for part in item["loc"]) or "input"
            fields.append({"field": location, "problem": item["msg"]})
        raise BookflowError("E_VALIDATION", details={"fields": fields}) from None


def load_standard_profile() -> StandardProfileManifest:
    """Load and fully validate the packaged standard profile."""
    raw = files("bookflow.data").joinpath("profile_standard.json").read_text(encoding="utf-8")
    try:
        return StandardProfileManifest.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise BookflowError(
            "E_INTERNAL",
            message="The packaged standard profile is invalid.",
            details={"resource": "profile_standard.json", "cause": type(exc).__name__},
        ) from None


def _table(noun: str) -> sa.Table:
    try:
        return TABLES[noun]
    except KeyError:
        raise _error("noun", "is not a supporting-profile noun") from None


def _row(db: Database, table: sa.Table, record_id: str) -> dict[str, Any]:
    found = db.conn.execute(sa.select(table).where(table.c.id == record_id)).mappings().first()
    if found is None:
        raise BookflowError(
            "E_RECORD_NOT_FOUND",
            details={"record_type": table.name, "selector": record_id, "suggestions": []},
        )
    return dict(found)


def _normalized(value: str, *, field: str) -> tuple[str, str]:
    return normalize_display_name(value, field=field)


def _ensure_unique(
    db: Database,
    table: sa.Table,
    key_column: str,
    value: str,
    *,
    field: str,
    exclude_id: str | None = None,
) -> None:
    query = sa.select(table.c.id).where(table.c[key_column] == value)
    if exclude_id is not None:
        query = query.where(table.c.id != exclude_id)
    collision = db.conn.execute(query).first()
    if collision is not None:
        raise BookflowError(
            "E_NAME_TAKEN",
            details={"field": field, "normalized": value, "record_id": collision[0]},
        )


def _source_row(
    db: Database,
    source_type: str,
    source_id: str,
    *,
    require_active: bool = True,
) -> dict[str, Any]:
    table = SOURCE_TABLES[source_type]
    found = db.conn.execute(sa.select(table).where(table.c.id == source_id)).mappings().first()
    if found is None:
        raise BookflowError(
            "E_RECORD_NOT_FOUND",
            details={"record_type": source_type, "selector": source_id, "suggestions": []},
        )
    result = dict(found)
    if require_active and not result["active"]:
        raise BookflowError(
            "E_INACTIVE_REFERENCE",
            details={"record_type": source_type, "record_id": source_id},
        )
    return result


def _hierarchy_values(
    db: Database,
    *,
    noun: str,
    name: str,
    parent_id: str | None,
    record_id: str,
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    table = _table(noun)
    parent: dict[str, Any] | None = None
    if parent_id is not None:
        parent = _row(db, table, parent_id)
        if not parent["active"]:
            raise BookflowError(
                "E_INACTIVE_REFERENCE",
                details={"record_type": noun, "record_id": parent_id},
            )
        if parent_id == record_id or (
            current is not None and record_id in str(parent["path"]).split("/")
        ):
            raise BookflowError(
                "E_HIERARCHY_CYCLE",
                details={"record_type": noun, "record_id": record_id, "parent_id": parent_id},
            )
    depth = int(parent["depth"]) + 1 if parent is not None else 1
    display, name_key = _normalized(name, field="name")
    full_name, full_name_key = hierarchy_projection(
        display,
        str(parent["full_name"]) if parent is not None else None,
        depth=depth,
    )
    path = f"{parent['path']}/{record_id}" if parent is not None else record_id

    if current is not None:
        descendants = db.conn.execute(
            sa.select(table.c.depth).where(table.c.path.like(f"{current['path']}/%"))
        ).scalars().all()
        deepest_relative = max((int(value) - int(current["depth"]) for value in descendants), default=0)
        if depth + deepest_relative > 5:
            raise BookflowError(
                "E_HIERARCHY_DEPTH",
                details={"depth": depth + deepest_relative, "maximum": 5},
            )

    _ensure_unique(
        db,
        table,
        "full_name_key",
        full_name_key,
        field="full_name",
        exclude_id=record_id if current is not None else None,
    )
    return {
        "name": display,
        "name_key": name_key,
        "parent_id": parent_id,
        "full_name": full_name,
        "full_name_key": full_name_key,
        "depth": depth,
        "path": path,
    }


def _specific_values(
    db: Database,
    noun: str,
    parsed: _ProfileInput,
    *,
    record_id: str,
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    table = _table(noun)
    if isinstance(parsed, HierarchicalProfileInput):
        return _hierarchy_values(
            db,
            noun=noun,
            name=parsed.name,
            parent_id=parsed.parent_id,
            record_id=record_id,
            current=current,
        )

    if isinstance(parsed, SalesTaxCodeInput):
        code, code_key = _normalized(parsed.code, field="code")
        if len(code) > 3:
            raise _error("code", "must contain from one through three characters")
        _ensure_unique(
            db,
            table,
            "code_key",
            code_key,
            field="code",
            exclude_id=record_id if current is not None else None,
        )
        return {"code": code, "code_key": code_key, "description": parsed.description, "taxable": parsed.taxable}

    display, name_key = _normalized(str(getattr(parsed, "name")), field="name")
    _ensure_unique(
        db,
        table,
        "name_key",
        name_key,
        field="name",
        exclude_id=record_id if current is not None else None,
    )
    values: dict[str, Any] = {"name": display, "name_key": name_key}

    if isinstance(parsed, TermInput):
        values.update(
            {
                "kind": parsed.kind,
                "due_days": parsed.due_days,
                "discount_days": parsed.discount_days,
                "due_day_of_month": parsed.due_day_of_month,
                "due_next_month_if_within_days": parsed.due_next_month_if_within_days,
                "discount_day_of_month": parsed.discount_day_of_month,
                "discount_percent_millionths": (
                    parse_percentage_millionths(parsed.discount_percent, field="discount_percent")
                    if parsed.discount_percent is not None
                    else None
                ),
            }
        )
    elif isinstance(parsed, PaymentMethodInput):
        values["kind"] = parsed.kind
    elif isinstance(parsed, SalesRepInput):
        initials, initials_key = _normalized(parsed.initials, field="initials")
        if len(initials) > 5:
            raise _error("initials", "must contain at most five characters")
        _ensure_unique(
            db,
            table,
            "initials_key",
            initials_key,
            field="initials",
            exclude_id=record_id if current is not None else None,
        )
        _source_row(db, parsed.name_type, parsed.name_id)
        values.update(
            {
                "initials": initials,
                "initials_key": initials_key,
                "name_type": parsed.name_type,
                "name_id": parsed.name_id,
            }
        )
    elif isinstance(parsed, ShipMethodInput):
        values["display_order"] = parsed.display_order
    elif isinstance(parsed, CustomerMessageInput):
        values.update({"text": parsed.text, "display_order": parsed.display_order})
    return values


def plan_profile_create(
    db: Database,
    noun: str,
    payload: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    seed_key: str | None = None,
    record_id: str | None = None,
    at: str | None = None,
) -> ProfileMutation:
    """Validate one create and return its complete insert row without writing."""
    if seed_key is not None and (not seed_key or len(seed_key) > 128):
        raise _error("seed_key", "must be from one through 128 characters")
    table = _table(noun)
    if seed_key is not None:
        collision = db.conn.execute(sa.select(table.c.id).where(table.c.seed_key == seed_key)).first()
        if collision is not None:
            raise BookflowError(
                "E_NAME_TAKEN",
                details={"field": "seed_key", "seed_key": seed_key, "record_id": collision[0]},
            )
    parsed = parse_profile_input(noun, payload)
    identifier = record_id or new_id()
    timestamp = at or now_iso()
    values = {
        "id": identifier,
        "version": 1,
        "created_at": timestamp,
        "created_by": actor_id,
        "created_via": via,
        "updated_at": timestamp,
        "updated_by": actor_id,
        "updated_via": via,
        "active": True,
        "seed_key": seed_key,
        **_specific_values(db, noun, parsed, record_id=identifier),
    }
    return ProfileMutation(noun, table.name, "create", None, values)


def _payload_from_row(noun: str, row: Mapping[str, Any]) -> dict[str, Any]:
    if noun in HIERARCHICAL_NOUNS:
        return {"name": row["name"], "parent_id": row["parent_id"]}
    if noun == "term":
        return {
            "name": row["name"],
            "kind": row["kind"],
            "due_days": row["due_days"],
            "discount_days": row["discount_days"],
            "due_day_of_month": row["due_day_of_month"],
            "due_next_month_if_within_days": row["due_next_month_if_within_days"],
            "discount_day_of_month": row["discount_day_of_month"],
            "discount_percent": (
                format_percentage_millionths(
                    row["discount_percent_millionths"], field="discount_percent"
                )
                if row["discount_percent_millionths"] is not None
                else None
            ),
        }
    fields = {
        "payment-method": ("name", "kind"),
        "sales-tax-code": ("code", "description", "taxable"),
        "sales-rep": ("name", "initials", "name_type", "name_id"),
        "ship-method": ("name", "display_order"),
        "customer-message": ("name", "text", "display_order"),
    }[noun]
    return {field: row[field] for field in fields}


def plan_profile_update(
    db: Database,
    noun: str,
    record_id: str,
    changes: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> ProfileUpdatePlan:
    """Validate a profile patch and plan its primary and hierarchy projections."""
    table = _table(noun)
    current = _row(db, table, record_id)
    if noun == "term" and "kind" in changes and changes["kind"] != current["kind"]:
        raise BookflowError("E_TYPE_CHANGE", details={"field": "kind"})
    if (
        noun == "sales-rep"
        and "name_type" in changes
        and changes["name_type"] != current["name_type"]
    ):
        raise BookflowError("E_TYPE_CHANGE", details={"field": "name_type"})
    payload = _payload_from_row(noun, current)
    payload.update(dict(changes))
    parsed = parse_profile_input(noun, payload)

    specific = _specific_values(db, noun, parsed, record_id=record_id, current=current)
    changed_specific = {key: value for key, value in specific.items() if current[key] != value}
    if not changed_specific:
        return ProfileUpdatePlan(None)
    timestamp = at or now_iso()
    after = {
        **current,
        **changed_specific,
        "version": int(current["version"]) + 1,
        "updated_at": timestamp,
        "updated_by": actor_id,
        "updated_via": via,
    }
    mutation = ProfileMutation(noun, table.name, "update", current, after)

    projections: list[ProjectionUpdate] = []
    if noun in HIERARCHICAL_NOUNS and any(
        field in changed_specific for field in ("name", "parent_id", "full_name", "path")
    ):
        descendants = db.conn.execute(
            sa.select(table)
            .where(table.c.path.like(f"{current['path']}/%"))
            .order_by(table.c.depth, table.c.path)
        ).mappings().all()
        depth_delta = int(after["depth"]) - int(current["depth"])
        old_name_prefix = f"{current['full_name']}:"
        new_name_prefix = f"{after['full_name']}:"
        old_path_prefix = f"{current['path']}/"
        new_path_prefix = f"{after['path']}/"
        subtree_ids = {record_id, *(str(row["id"]) for row in descendants)}
        for descendant in descendants:
            full_name = str(descendant["full_name"])
            path = str(descendant["path"])
            if not full_name.startswith(old_name_prefix) or not path.startswith(old_path_prefix):
                raise BookflowError("E_INTERNAL", details={"record_id": descendant["id"]})
            new_full_name = new_name_prefix + full_name[len(old_name_prefix) :]
            new_full_name_key = unicodedata.normalize("NFC", new_full_name.casefold())
            if len(new_full_name) > 1004 or len(new_full_name_key) > 2004:
                raise _error("full_name", "exceeds the hierarchy-name bound")
            collision = db.conn.execute(
                sa.select(table.c.id).where(
                    table.c.full_name_key == new_full_name_key,
                    table.c.id.not_in(subtree_ids),
                )
            ).first()
            if collision is not None:
                raise BookflowError(
                    "E_NAME_TAKEN",
                    details={"field": "full_name", "record_id": collision[0]},
                )
            projections.append(
                ProjectionUpdate(
                    table.name,
                    str(descendant["id"]),
                    {
                        "full_name": new_full_name,
                        "full_name_key": new_full_name_key,
                        "depth": int(descendant["depth"]) + depth_delta,
                        "path": new_path_prefix + path[len(old_path_prefix) :],
                    },
                )
            )
    return ProfileUpdatePlan(mutation, tuple(projections))


def persist_profile_mutation(db: Database, mutation: ProfileMutation) -> None:
    """Persist a previously validated mutation; the caller owns its transaction."""
    table = c.metadata.tables[mutation.table_name]
    if mutation.before is None:
        db.conn.execute(table.insert().values(**dict(mutation.after)))
        return
    db.conn.execute(
        table.update().where(table.c.id == mutation.after["id"]).values(**dict(mutation.after))
    )


def persist_profile_update(db: Database, plan: ProfileUpdatePlan) -> None:
    """Persist one update plan, including unversioned descendant projections."""
    if plan.mutation is not None:
        persist_profile_mutation(db, plan.mutation)
    for projection in plan.projections:
        table = c.metadata.tables[projection.table_name]
        db.conn.execute(
            table.update().where(table.c.id == projection.record_id).values(**dict(projection.values))
        )


def plan_profile_active_change(
    db: Database,
    noun: str,
    record_id: str,
    active: bool,
    *,
    actor_id: str,
    via: str,
    cascade: bool = False,
    at: str | None = None,
) -> ProfileActivePlan:
    """Plan idempotent activation or deterministic parent-first deactivation."""
    if not isinstance(active, bool):
        raise _error("active", "must be a boolean")
    table = _table(noun)
    requested = _row(db, table, record_id)
    if bool(requested["active"]) is active:
        return ProfileActivePlan(noun, record_id, active, ())

    rows = [requested]
    if noun in HIERARCHICAL_NOUNS:
        if active:
            parent_id = requested["parent_id"]
            while parent_id is not None:
                parent = _row(db, table, str(parent_id))
                if not parent["active"]:
                    raise BookflowError(
                        "E_INACTIVE_REFERENCE",
                        details={"record_type": noun, "record_id": parent["id"]},
                    )
                parent_id = parent["parent_id"]
        else:
            descendants = [
                dict(row)
                for row in db.conn.execute(
                    sa.select(table)
                    .where(table.c.active.is_(True), table.c.path.like(f"{requested['path']}/%"))
                    .order_by(table.c.depth, table.c.path)
                ).mappings().all()
            ]
            if descendants and not cascade:
                raise BookflowError(
                    "E_ACTIVE_DEPENDENTS",
                    details={"record_type": noun, "record_id": record_id, "count": len(descendants)},
                )
            rows.extend(descendants)

    timestamp = at or now_iso()
    action: Literal["activate", "deactivate"] = "activate" if active else "deactivate"
    mutations = []
    for before in rows:
        after = {
            **before,
            "active": active,
            "version": int(before["version"]) + 1,
            "updated_at": timestamp,
            "updated_by": actor_id,
            "updated_via": via,
        }
        mutations.append(ProfileMutation(noun, table.name, action, before, after))
    return ProfileActivePlan(noun, record_id, active, tuple(mutations))


def persist_profile_active_change(db: Database, plan: ProfileActivePlan) -> None:
    for mutation in plan.mutations:
        persist_profile_mutation(db, mutation)


def _clamped_month_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def compute_term_dates(term: TermInput | Mapping[str, Any], transaction_date: date) -> TermDates:
    """Compute due and early-discount dates without writing any state."""
    if not isinstance(transaction_date, date):
        raise _error("transaction_date", "must be a date")
    parsed = term if isinstance(term, TermInput) else parse_profile_input("term", term)
    assert isinstance(parsed, TermInput)
    if parsed.kind == "standard":
        assert parsed.due_days is not None
        due = transaction_date + timedelta(days=parsed.due_days)
        discount = (
            transaction_date + timedelta(days=parsed.discount_days)
            if parsed.discount_days is not None
            else None
        )
        return TermDates(due_date=due, discount_date=discount)

    assert parsed.due_day_of_month is not None
    assert parsed.due_next_month_if_within_days is not None
    year, month = transaction_date.year, transaction_date.month
    due = _clamped_month_date(year, month, parsed.due_day_of_month)
    days_until_due = (due - transaction_date).days
    if due < transaction_date or days_until_due <= parsed.due_next_month_if_within_days:
        year, month = _next_month(year, month)
        due = _clamped_month_date(year, month, parsed.due_day_of_month)
    discount = (
        _clamped_month_date(year, month, parsed.discount_day_of_month)
        if parsed.discount_day_of_month is not None
        else None
    )
    return TermDates(due_date=due, discount_date=discount)


def term_rule_summaries(term: TermInput | Mapping[str, Any]) -> tuple[str, str]:
    """Return stable human-readable due and discount rule summaries."""
    parsed = term if isinstance(term, TermInput) else parse_profile_input("term", term)
    assert isinstance(parsed, TermInput)
    if parsed.kind == "standard":
        due = f"Due {parsed.due_days} days after the transaction date"
        discount = (
            f"{parsed.discount_percent}% discount within {parsed.discount_days} days"
            if parsed.discount_percent is not None
            else "No early discount"
        )
    else:
        due = (
            f"Due on day {parsed.due_day_of_month}; move to the next month "
            f"when within {parsed.due_next_month_if_within_days} days"
        )
        discount = (
            f"{parsed.discount_percent}% discount through day {parsed.discount_day_of_month}"
            if parsed.discount_percent is not None
            else "No early discount"
        )
    return due, discount


def project_profile_record(
    db: Database,
    noun: str,
    row: Mapping[str, Any],
    *,
    transaction_date: date | None = None,
) -> dict[str, Any]:
    """Add the profile-specific derived fields used by every adapter."""
    result = dict(row)
    if noun == "term":
        payload = _payload_from_row(noun, row)
        result["discount_percent"] = payload["discount_percent"]
        result["due_rule_summary"], result["discount_rule_summary"] = term_rule_summaries(payload)
        result["calculated_due_date"] = None
        result["calculated_discount_date"] = None
        if transaction_date is not None:
            dates = compute_term_dates(payload, transaction_date)
            result["calculated_due_date"] = dates.due_date
            result["calculated_discount_date"] = dates.discount_date
    elif noun == "sales-rep":
        source = _source_row(
            db,
            str(row["name_type"]),
            str(row["name_id"]),
            require_active=False,
        )
        result["source_type"] = row["name_type"]
        result["source_name"] = source["name"]
    elif noun in HIERARCHICAL_NOUNS:
        result["usage_count"] = 0 if noun in {"item-category", "class"} else result.get("usage_count")
    return result


def plan_standard_profile(
    db: Database,
    *,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> tuple[StandardProfileManifest, tuple[ProfileMutation, ...], dict[str, int]]:
    """Validate the whole manifest and plan only absent immutable seed keys."""
    manifest = load_standard_profile()
    timestamp = at or now_iso()
    planned: list[ProfileMutation] = []
    preserved: dict[str, int] = {}
    for noun in manifest.lists:
        table = _table(noun)
        preserved[noun] = 0
        for raw in manifest.records[noun]:
            seed_key = str(raw["seed_key"])
            existing = db.conn.execute(
                sa.select(table.c.id).where(table.c.seed_key == seed_key)
            ).first()
            if existing is not None:
                preserved[noun] += 1
                continue
            payload = {key: value for key, value in raw.items() if key != "seed_key"}
            planned.append(
                plan_profile_create(
                    db,
                    noun,
                    payload,
                    actor_id=actor_id,
                    via=via,
                    seed_key=seed_key,
                    at=timestamp,
                )
            )
    return manifest, tuple(planned), preserved


def apply_standard_profile(
    db: Database,
    *,
    actor_id: str,
    via: str,
    dry_run: bool = False,
) -> ProfileApplyResult:
    """Insert missing standard rows atomically and preserve every seeded edit."""
    owns_transaction = not db.raw.in_transaction
    if owns_transaction and not dry_run:
        db.raw.execute("BEGIN IMMEDIATE")
    try:
        manifest, planned, preserved = plan_standard_profile(
            db,
            actor_id=actor_id,
            via=via,
        )
        inserted = {noun: 0 for noun in manifest.lists}
        for mutation in planned:
            inserted[mutation.noun] += 1
        result = ProfileApplyResult(
            version=manifest.version,
            inserted_by_list=inserted,
            preserved_by_list=preserved,
            dry_run=dry_run,
        )
        if dry_run:
            return result
        for mutation in planned:
            persist_profile_mutation(db, mutation)
        if owns_transaction:
            db.raw.execute("COMMIT")
    except BaseException:
        if owns_transaction and db.raw.in_transaction:
            db.raw.execute("ROLLBACK")
        raise
    return result
