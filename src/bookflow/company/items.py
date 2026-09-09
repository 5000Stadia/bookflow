"""Item catalogue validation, aggregate planning, and exact projections.

The catalogue is one versioned aggregate per item.  Ordered member and vendor
profile rows, custom values, and hierarchy projections are planned before any
write and persisted inside the dispatcher's company transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
import sqlalchemy as sa

from bookflow.company import custom_fields, list_service, schema, units
from bookflow.company.lists import get_list_definition, hierarchy_projection, normalize_display_name
from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    INT64_MAX,
    INT64_MIN,
    bom_component_extension_minor_units,
    convert_quantity_micro_units,
    format_percentage_millionths,
    format_quantity_micro_units,
    parse_percentage_millionths,
    parse_quantity_micro_units,
)
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.session import now_iso
from bookflow.storage.engine import Database


ItemType = Literal[
    "service",
    "inventory_part",
    "non_inventory_part",
    "other_charge",
    "subtotal",
    "group",
    "discount",
    "payment",
    "sales_tax_item",
    "sales_tax_group",
    "inventory_assembly",
    "fixed_asset",
]
DisposalStatus = Literal["in_service", "sold", "disposed"]
DepreciationMethod = Literal[
    "none",
    "straight_line",
    "declining_balance",
    "sum_of_years_digits",
    "units_of_production",
    "other",
]

ITEM_NOUN = "item"
ITEM_RECORD_TYPE = "item"
ITEM_TYPES = (
    "service", "inventory_part", "non_inventory_part", "other_charge",
    "subtotal", "group", "discount", "payment", "sales_tax_item",
    "sales_tax_group", "inventory_assembly", "fixed_asset",
)
_TRANSITIONABLE = frozenset({"service", "non_inventory_part", "other_charge"})
_NON_PARENT_TYPES = frozenset({"subtotal", "group", "payment", "sales_tax_group", "fixed_asset"})
_MEMBER_TYPES = frozenset({"group", "sales_tax_group", "inventory_assembly"})
_ASSEMBLY_COMPONENT_TYPES = frozenset(
    {"service", "inventory_part", "non_inventory_part", "other_charge", "inventory_assembly"}
)


def _validation(field: str, problem: str, *, code: str = "E_VALIDATION", **details: Any) -> BookflowError:
    return BookflowError(code, details={"fields": [{"field": field, "problem": problem}], **details})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ItemMemberInput(StrictModel):
    id: str | None = Field(default=None, min_length=26, max_length=26)
    component_item_id: str = Field(min_length=26, max_length=26)
    quantity: str
    unit_id: str | None = Field(default=None, min_length=26, max_length=26)

    @field_validator("quantity")
    @classmethod
    def _quantity(cls, value: str) -> str:
        try:
            stored = parse_quantity_micro_units(value, field="members.quantity")
        except BookflowError as exc:
            raise ValueError(exc.details["fields"][0]["problem"]) from None
        if stored < 0:
            raise ValueError("must be nonnegative")
        return format_quantity_micro_units(stored)


class ItemVendorProfileInput(StrictModel):
    id: str | None = Field(default=None, min_length=26, max_length=26)
    vendor_id: str = Field(min_length=26, max_length=26)
    preferred_rank: int = Field(ge=1)
    vendor_item_name: str | None = Field(default=None, max_length=200)
    purchase_cost: Any | None = None
    minimum_quantity: str | None = None
    lead_time_days: int | None = Field(default=None, ge=0)
    manufacturer_part_number: str | None = Field(default=None, max_length=128)
    availability_notes: str | None = None

    @field_validator("minimum_quantity")
    @classmethod
    def _minimum_quantity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            stored = parse_quantity_micro_units(value, field="vendor_profiles.minimum_quantity")
        except BookflowError as exc:
            raise ValueError(exc.details["fields"][0]["problem"]) from None
        if stored < 0:
            raise ValueError("must be nonnegative")
        return format_quantity_micro_units(stored)


class ItemInput(StrictModel):
    """Complete public item profile; the type registry rejects foreign fields."""

    name: str = Field(min_length=1, max_length=200)
    type: ItemType
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
    disposal_status: DisposalStatus | None = None
    disposal_date: str | None = None
    disposal_proceeds: Any | None = None
    disposal_costs: Any | None = None
    accumulated_depreciation_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    depreciation_expense_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    gain_loss_account_id: str | None = Field(default=None, min_length=26, max_length=26)
    depreciation_method: DepreciationMethod | None = None
    useful_life_months: int | None = Field(default=None, ge=1)
    book_basis: Any | None = None
    tax_basis: Any | None = None
    members: list[ItemMemberInput] | None = None
    vendor_profiles: list[ItemVendorProfileInput] | None = None
    custom_fields: dict[str, Any | None] | None = None

    @field_validator(
        "reorder_point_min", "reorder_point_max", "assembly_build_point",
    )
    @classmethod
    def _nonnegative_quantities(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        try:
            stored = parse_quantity_micro_units(value, field=info.field_name)
        except BookflowError as exc:
            raise ValueError(exc.details["fields"][0]["problem"]) from None
        if stored < 0:
            raise ValueError("must be nonnegative")
        return format_quantity_micro_units(stored)

    @field_validator("charge_percent", "discount_percent", "tax_percent")
    @classmethod
    def _bounded_percentages(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        try:
            stored = parse_percentage_millionths(value, field=info.field_name)
        except BookflowError as exc:
            raise ValueError(exc.details["fields"][0]["problem"]) from None
        if not 0 <= stored <= 100_000_000:
            raise ValueError("must be from 0 through 100")
        return format_percentage_millionths(stored)

    @field_validator("purchase_date", "warranty_expiration", "disposal_date")
    @classmethod
    def _dates(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError("must be an ISO date in YYYY-MM-DD form") from None
        if parsed.isoformat() != value:
            raise ValueError("must be a canonical ISO date in YYYY-MM-DD form")
        return value


class MoneyOutput(StrictModel):
    amount: str
    currency: str
    minor_units: int


class CustomFieldValueOutput(StrictModel):
    id: str
    definition_id: str
    name: str
    kind: str
    definition_active: bool
    value: Any


class ItemMemberOutput(StrictModel):
    id: str
    position: int
    active: bool
    component_item_id: str
    component_name: str
    component_type: ItemType
    quantity: str
    unit_id: str | None
    unit_name: str | None


class ItemVendorProfileOutput(StrictModel):
    id: str
    position: int
    active: bool
    vendor_id: str
    vendor_name: str
    preferred_rank: int
    vendor_item_name: str | None
    purchase_cost: MoneyOutput | None
    minimum_quantity: str | None
    lead_time_days: int | None
    manufacturer_part_number: str | None
    availability_notes: str | None


class ItemOutput(StrictModel):
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
    type: ItemType
    parent_id: str | None
    full_name: str
    depth: int
    category_id: str | None
    category: str | None
    description: str | None
    purchase_description: str | None
    sales_enabled: bool
    purchase_enabled: bool
    price: MoneyOutput | None
    cost: MoneyOutput | None
    income_account_id: str | None
    expense_account_id: str | None
    cogs_account_id: str | None
    asset_account_id: str | None
    deposit_account_id: str | None
    liability_account_id: str | None
    default_class_id: str | None
    default_class: str | None
    sales_tax_code_id: str | None
    sales_tax_code: str | None
    manufacturer_part_number: str | None
    barcode: str | None
    unit_of_measure_set_id: str | None
    unit_of_measure_set: str | None
    reorder_point_min: str | None
    reorder_point_max: str | None
    preferred_vendor_id: str | None
    preferred_vendor: str | None
    notes: str | None
    charge_percent: str | None
    print_members: bool | None
    discount_amount: MoneyOutput | None
    discount_percent: str | None
    payment_method_id: str | None
    use_undeposited_funds: bool | None
    tax_percent: str | None
    tax_agency_vendor_id: str | None
    assembly_build_point: str | None
    asset_number: str | None
    purchase_date: str | None
    original_cost: MoneyOutput | None
    vendor_id: str | None
    location: str | None
    serial_number: str | None
    warranty_expiration: str | None
    disposal_status: DisposalStatus | None
    disposal_date: str | None
    disposal_proceeds: MoneyOutput | None
    disposal_costs: MoneyOutput | None
    accumulated_depreciation_account_id: str | None
    depreciation_expense_account_id: str | None
    gain_loss_account_id: str | None
    depreciation_method: DepreciationMethod | None
    useful_life_months: int | None
    book_basis: MoneyOutput | None
    tax_basis: MoneyOutput | None
    members: list[ItemMemberOutput]
    vendor_profiles: list[ItemVendorProfileOutput]
    custom_fields: list[CustomFieldValueOutput]
    quantity_on_hand: str
    quantity_available: str
    quantity_committed: str
    quantity_on_order: str
    quantity_pending_build: str
    average_cost: MoneyOutput
    inventory_value: MoneyOutput
    inventory_values_available: bool
    bill_of_material_cost: MoneyOutput | None
    combined_percent: str | None
    child_count: int
    has_children: bool


@dataclass(frozen=True)
class ItemProfile:
    allowed: frozenset[str]
    required: frozenset[str] = frozenset()


_IDENTITY = frozenset({"name", "type", "parent_id", "category_id", "default_class_id", "notes", "custom_fields"})
_SALES = frozenset({"description", "sales_enabled", "price", "income_account_id", "sales_tax_code_id"})
_PURCHASE = frozenset({"purchase_description", "purchase_enabled", "cost", "expense_account_id", "preferred_vendor_id", "vendor_profiles"})
_INVENTORY_PURCHASE = frozenset({"purchase_description", "purchase_enabled", "cost", "preferred_vendor_id", "vendor_profiles"})
_STOCK = frozenset({"cogs_account_id", "asset_account_id", "manufacturer_part_number", "barcode", "unit_of_measure_set_id", "reorder_point_min", "reorder_point_max"})
_FIXED = frozenset({
    "asset_number", "purchase_date", "original_cost", "vendor_id", "location", "serial_number",
    "warranty_expiration", "disposal_status", "disposal_date", "disposal_proceeds",
    "disposal_costs", "accumulated_depreciation_account_id",
    "depreciation_expense_account_id", "gain_loss_account_id", "depreciation_method",
    "useful_life_months", "book_basis", "tax_basis", "asset_account_id", "description",
})

# This registry is the single profile inventory consumed by create and update.
ITEM_PROFILES: dict[str, ItemProfile] = {
    "service": ItemProfile(_IDENTITY | _SALES | _PURCHASE | {"unit_of_measure_set_id"}),
    "non_inventory_part": ItemProfile(_IDENTITY | _SALES | _PURCHASE | {"manufacturer_part_number", "barcode", "unit_of_measure_set_id"}),
    "inventory_part": ItemProfile(_IDENTITY | _SALES | _INVENTORY_PURCHASE | _STOCK, frozenset({"description", "price", "income_account_id", "purchase_description", "cost", "cogs_account_id", "asset_account_id"})),
    "inventory_assembly": ItemProfile(_IDENTITY | _SALES | _INVENTORY_PURCHASE | _STOCK | {"members", "assembly_build_point"}, frozenset({"description", "price", "income_account_id", "purchase_description", "cost", "cogs_account_id", "asset_account_id", "members"})),
    "other_charge": ItemProfile(_IDENTITY | _SALES | _PURCHASE | {"charge_percent"}),
    "subtotal": ItemProfile(_IDENTITY | {"description"}, frozenset({"description"})),
    "group": ItemProfile(_IDENTITY | {"description", "members", "print_members", "sales_tax_code_id"}, frozenset({"members", "print_members"})),
    "discount": ItemProfile(_IDENTITY | {"description", "discount_amount", "discount_percent", "income_account_id", "expense_account_id", "sales_tax_code_id"}),
    "payment": ItemProfile(_IDENTITY | {"description", "payment_method_id", "deposit_account_id", "use_undeposited_funds"}, frozenset({"description"})),
    "sales_tax_item": ItemProfile(_IDENTITY | {"description", "tax_percent", "tax_agency_vendor_id", "liability_account_id"}, frozenset({"tax_percent", "tax_agency_vendor_id", "liability_account_id"})),
    "sales_tax_group": ItemProfile(_IDENTITY | {"description", "members"}, frozenset({"members"})),
    "fixed_asset": ItemProfile(_IDENTITY | _FIXED, frozenset({"asset_number", "description", "purchase_date", "original_cost", "asset_account_id", "disposal_status", "depreciation_method"})),
}


_MONEY_FIELDS = (
    "price", "cost", "discount_amount", "original_cost", "disposal_proceeds",
    "disposal_costs", "book_basis", "tax_basis",
)
_PERCENT_FIELDS = {
    "charge_percent": "other_charge_percent_millionths",
    "discount_percent": "discount_percent_millionths",
    "tax_percent": "tax_percent_millionths",
}
_QUANTITY_FIELDS = {
    "reorder_point_min": "reorder_point_min_microunits",
    "reorder_point_max": "reorder_point_max_microunits",
    "assembly_build_point": "assembly_build_point_microunits",
}
_DIRECT_FIELDS = (
    "category_id", "description", "purchase_description", "income_account_id",
    "expense_account_id", "cogs_account_id", "asset_account_id", "deposit_account_id",
    "liability_account_id", "default_class_id", "sales_tax_code_id",
    "manufacturer_part_number", "barcode", "unit_of_measure_set_id", "notes",
    "print_members", "payment_method_id", "use_undeposited_funds",
    "tax_agency_vendor_id", "asset_number", "purchase_date", "vendor_id", "location",
    "serial_number", "warranty_expiration", "disposal_status", "disposal_date",
    "accumulated_depreciation_account_id", "depreciation_expense_account_id",
    "gain_loss_account_id", "depreciation_method", "useful_life_months",
)


@dataclass(frozen=True)
class ProjectionUpdate:
    record_id: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class ItemMutation:
    action: Literal["create", "update", "activate", "deactivate"]
    before: Mapping[str, Any] | None
    after: Mapping[str, Any]
    before_members: tuple[Mapping[str, Any], ...]
    after_members: tuple[Mapping[str, Any], ...]
    before_vendors: tuple[Mapping[str, Any], ...]
    after_vendors: tuple[Mapping[str, Any], ...]
    before_custom: tuple[Mapping[str, Any], ...]
    after_custom: tuple[Mapping[str, Any], ...]
    changed_fields: tuple[str, ...]
    custom_plan: custom_fields.OwnerCustomFieldPlan | None = None

    @property
    def before_snapshot(self) -> dict[str, Any] | None:
        if self.before is None:
            return None
        return aggregate_snapshot(self.before, self.before_members, self.before_vendors, self.before_custom)

    @property
    def after_snapshot(self) -> dict[str, Any]:
        return aggregate_snapshot(self.after, self.after_members, self.after_vendors, self.after_custom)


@dataclass(frozen=True)
class ItemUpdatePlan:
    mutation: ItemMutation | None
    projections: tuple[ProjectionUpdate, ...] = ()
    changed_fields: tuple[str, ...] = ()

    @property
    def affected_descendant_ids(self) -> tuple[str, ...]:
        return tuple(item.record_id for item in self.projections)


@dataclass(frozen=True)
class ItemActivePlan:
    requested_id: str
    requested_active: bool
    mutations: tuple[ItemMutation, ...]

    @property
    def changed(self) -> bool:
        return bool(self.mutations)

    @property
    def affected_ids(self) -> tuple[str, ...]:
        return tuple(str(item.after["id"]) for item in self.mutations)


def parse_item_input(payload: Mapping[str, Any]) -> ItemInput:
    list_service.assert_no_floats(payload)
    try:
        return ItemInput.model_validate(dict(payload))
    except ValidationError as exc:
        fields = [{"field": ".".join(str(part) for part in error["loc"]) or "input", "problem": error["msg"]} for error in exc.errors(include_url=False)]
        raise BookflowError("E_VALIDATION", details={"fields": fields}) from None


def _row(db: Database, record_id: str) -> dict[str, Any]:
    found = db.conn.execute(sa.select(schema.items).where(schema.items.c.id == record_id)).mappings().first()
    if found is None:
        raise BookflowError("E_RECORD_NOT_FOUND", details={"record_type": ITEM_RECORD_TYPE, "selector": record_id, "suggestions": []})
    return dict(found)


def resolve_item(db: Database, selector: str) -> dict[str, Any]:
    definition = get_list_definition(ITEM_NOUN)
    assert definition is not None
    try:
        return list_service.resolve_selector(db, schema.items, definition, selector)
    except BookflowError as exc:
        if exc.code == "E_RECORD_NOT_FOUND" and isinstance(selector, str) and is_ulid(selector):
            raise BookflowError(
                "E_RECORD_NOT_FOUND", details={"selector": None, "suggestions": []}
            ) from None
        raise


def _children(db: Database, table: sa.Table, owner_field: str, owner_id: str) -> tuple[dict[str, Any], ...]:
    rows = db.conn.execute(sa.select(table).where(table.c[owner_field] == owner_id).order_by(table.c.position, table.c.id)).mappings().all()
    return tuple(dict(row) for row in rows)


def _members(db: Database, item_id: str) -> tuple[dict[str, Any], ...]:
    return _children(db, schema.item_members, "owner_item_id", item_id)


def _vendors(db: Database, item_id: str) -> tuple[dict[str, Any], ...]:
    return _children(db, schema.item_vendor_profiles, "item_id", item_id)


def _info(db: Database) -> dict[str, Any]:
    return dict(db.conn.execute(sa.select(schema.company_info)).mappings().one())


def _home_currency(db: Database) -> str:
    return str(_info(db)["home_currency"])


def _money(value: Any | None, db: Database, field: str) -> dict[str, Any]:
    if value is None:
        return {f"{field}_minor_units": None, f"{field}_currency": None}
    result = Money.parse(value, _home_currency(db))
    if result.currency != _home_currency(db):
        raise _validation(field, "must use the company home currency")
    if not INT64_MIN <= result.minor_units <= INT64_MAX:
        raise _validation(field, "does not fit signed 64-bit storage", code="E_VALUE_RANGE")
    if result.minor_units < 0:
        raise _validation(field, "must be nonnegative")
    return {f"{field}_minor_units": result.minor_units, f"{field}_currency": result.currency}


def _money_output(row: Mapping[str, Any], field: str) -> MoneyOutput | None:
    minor = row.get(f"{field}_minor_units")
    currency = row.get(f"{field}_currency")
    return None if minor is None else MoneyOutput(**Money(int(minor), str(currency)).to_dict())


def _reference(db: Database, table: sa.Table, record_id: str, *, field: str, record_type: str, active: bool) -> dict[str, Any]:
    found = db.conn.execute(sa.select(table).where(table.c.id == record_id)).mappings().first()
    if found is None:
        raise BookflowError("E_RECORD_NOT_FOUND", details={"record_type": record_type, "field": field, "selector": record_id, "suggestions": []})
    result = dict(found)
    if active and not result.get("active", True):
        raise BookflowError("E_INACTIVE_REFERENCE", details={"record_type": record_type, "field": field, "record_id": record_id})
    return result


def _validate_account(db: Database, record_id: str, field: str, *, active: bool) -> dict[str, Any]:
    account = _reference(db, schema.accounts, record_id, field=field, record_type="account", active=active)
    allowed = {
        "income_account_id": {"income", "other_income"},
        "expense_account_id": {"expense", "other_expense", "cost_of_goods_sold"},
        "cogs_account_id": {"cost_of_goods_sold"},
        "asset_account_id": {"other_current_asset", "fixed_asset"},
        "deposit_account_id": {"bank", "other_current_asset"},
        "liability_account_id": {"other_current_liability"},
        "accumulated_depreciation_account_id": {"fixed_asset", "other_asset"},
        "depreciation_expense_account_id": {"expense", "other_expense"},
        "gain_loss_account_id": {"income", "other_income", "expense", "other_expense"},
    }[field]
    if account["type"] not in allowed:
        raise _validation(field, f"must reference an account of type {', '.join(sorted(allowed))}")
    return account


def _required(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _validate_profile(parsed: ItemInput, supplied: set[str]) -> None:
    item_type = parsed.type
    profile = ITEM_PROFILES[item_type]
    foreign = sorted(field for field in supplied if field not in profile.allowed and field not in {"custom_fields"} and getattr(parsed, field, None) is not None)
    if foreign:
        raise _validation(foreign[0], f"is not allowed for item type {item_type}")
    missing = sorted(field for field in profile.required if not _required(getattr(parsed, field)))
    if missing:
        raise _validation(missing[0], f"is required for item type {item_type}")

    sales, purchase = _logical_defaults(item_type, parsed)
    if item_type in {"service", "non_inventory_part", "other_charge"} and not (sales or purchase):
        raise _validation("sales_enabled", "at least one of sales or purchase must be enabled")
    if item_type in {"inventory_part", "inventory_assembly"} and not (sales and purchase):
        raise _validation("sales_enabled", "inventory items require sales and purchase profiles")
    sided_types = {"service", "non_inventory_part", "other_charge", "inventory_part", "inventory_assembly"}
    if sales and item_type in sided_types:
        for field in ("description", "income_account_id"):
            if not _required(getattr(parsed, field)):
                raise _validation(field, "is required when sales are enabled")
        if item_type == "service" and parsed.sales_tax_code_id is None:
            raise _validation("sales_tax_code_id", "is required when service sales are enabled")
        if item_type != "other_charge" or parsed.charge_percent is None:
            if parsed.price is None:
                raise _validation("price", "is required when sales are enabled")
    if purchase and item_type in sided_types:
        required_purchase_fields = (
            ("purchase_description", "cogs_account_id")
            if item_type in {"inventory_part", "inventory_assembly"}
            else ("purchase_description", "expense_account_id")
        )
        for field in required_purchase_fields:
            if not _required(getattr(parsed, field)):
                raise _validation(field, "is required when purchase is enabled")
        if item_type != "other_charge" or parsed.charge_percent is None:
            if parsed.cost is None:
                raise _validation("cost", "is required when purchase is enabled")
    if not purchase and (parsed.preferred_vendor_id is not None or parsed.vendor_profiles):
        raise _validation("vendor_profiles", "requires an enabled purchase profile")
    if item_type == "other_charge" and parsed.charge_percent is not None:
        if parsed.price is not None or parsed.cost is not None:
            raise _validation("charge_percent", "is mutually exclusive with fixed price and cost")
    if item_type == "discount":
        if (parsed.discount_amount is None) == (parsed.discount_percent is None):
            raise _validation("discount_amount", "exactly one of discount_amount or discount_percent is required")
        if (parsed.income_account_id is None) == (parsed.expense_account_id is None):
            raise _validation("income_account_id", "exactly one income or expense account is required")
        if parsed.sales_tax_code_id is None:
            raise _validation("sales_tax_code_id", "is required for a discount item")
    if item_type == "payment":
        deposit = parsed.deposit_account_id is not None
        undeposited = parsed.use_undeposited_funds is True
        if deposit == undeposited:
            raise _validation("deposit_account_id", "exactly one deposit treatment is required")
    if item_type in _MEMBER_TYPES and not parsed.members:
        raise _validation("members", f"is required for item type {item_type}")
    if item_type == "fixed_asset":
        if parsed.disposal_status == "in_service":
            if parsed.disposal_date is not None or parsed.disposal_proceeds is not None:
                raise _validation("disposal_date", "must be null while the asset is in service")
        else:
            if parsed.disposal_date is None:
                raise _validation("disposal_date", "is required outside in_service")
            if parsed.disposal_status == "sold" and parsed.disposal_proceeds is None:
                raise _validation("disposal_proceeds", "is required when disposal_status is sold")
            if parsed.disposal_status == "disposed" and parsed.disposal_proceeds is not None:
                raise _validation("disposal_proceeds", "is allowed only when disposal_status is sold")
        if parsed.warranty_expiration is not None and parsed.purchase_date is not None and parsed.warranty_expiration < parsed.purchase_date:
            raise _validation("warranty_expiration", "cannot precede purchase_date")


def _hierarchy_values(db: Database, *, record_id: str, name: str, item_type: str, parent_id: str | None, exclude_id: str | None = None) -> dict[str, Any]:
    display, name_key = normalize_display_name(name)
    parent = None
    if parent_id is not None:
        parent = _reference(db, schema.items, parent_id, field="parent_id", record_type="item", active=True)
        if parent["type"] != item_type:
            raise _validation("parent_id", "must reference an item of the same type")
        if parent["type"] in _NON_PARENT_TYPES:
            raise _validation("parent_id", f"{parent['type']} items cannot have children")
    depth = 1 if parent is None else int(parent["depth"]) + 1
    full_name, full_name_key = hierarchy_projection(display, None if parent is None else str(parent["full_name"]), depth=depth)
    definition = get_list_definition(ITEM_NOUN)
    assert definition is not None
    list_service.assert_name_available(db, schema.items, definition, full_name, exclude_id=exclude_id)
    return {
        "name": display, "name_key": name_key, "parent_id": parent_id,
        "full_name": full_name, "full_name_key": full_name_key, "depth": depth,
        "path": f"/{record_id}/" if parent is None else f"{parent['path']}{record_id}/",
    }


def _logical_defaults(item_type: str, parsed: ItemInput) -> tuple[bool, bool]:
    if item_type in {"inventory_part", "inventory_assembly"}:
        return True if parsed.sales_enabled is None else parsed.sales_enabled, True if parsed.purchase_enabled is None else parsed.purchase_enabled
    if item_type in {"subtotal", "group", "discount", "payment", "sales_tax_item", "sales_tax_group"}:
        return True, False
    if item_type == "fixed_asset":
        return False, True
    # A service, a non-inventory part and an other charge are all things the
    # company sells: each one is a line on an invoice, and its second, purchase
    # side is an explicit choice a bookkeeper makes for subcontracted labor,
    # parts bought for one job, or a passed-through charge.  Sales therefore
    # defaults on and purchase defaults off.  An omitted flag is the only thing
    # defaulted here; a flag the caller supplied is used exactly as supplied,
    # including False.
    return (
        True if parsed.sales_enabled is None else parsed.sales_enabled,
        False if parsed.purchase_enabled is None else parsed.purchase_enabled,
    )


def _owner_values(db: Database, parsed: ItemInput, *, supplied: set[str], reference_changes: set[str], creating: bool) -> dict[str, Any]:
    _validate_profile(parsed, supplied)
    sales_enabled, purchase_enabled = _logical_defaults(parsed.type, parsed)
    values: dict[str, Any] = {"type": parsed.type, "sales_enabled": sales_enabled, "purchase_enabled": purchase_enabled}
    for field in _DIRECT_FIELDS:
        values[field] = getattr(parsed, field)
    values["preferred_vendor_id"] = parsed.preferred_vendor_id
    for field in _MONEY_FIELDS:
        values.update(_money(getattr(parsed, field), db, field))
    for field, column in _PERCENT_FIELDS.items():
        raw = getattr(parsed, field)
        values[column] = None if raw is None else parse_percentage_millionths(raw, field=field)
    for field, column in _QUANTITY_FIELDS.items():
        raw = getattr(parsed, field)
        values[column] = None if raw is None else parse_quantity_micro_units(raw, field=field)
    if values["reorder_point_min_microunits"] is not None and values["reorder_point_max_microunits"] is not None and values["reorder_point_max_microunits"] < values["reorder_point_min_microunits"]:
        raise _validation("reorder_point_max", "cannot be below reorder_point_min")

    active_all = creating
    account_fields = (
        "income_account_id", "expense_account_id", "cogs_account_id", "asset_account_id",
        "deposit_account_id", "liability_account_id", "accumulated_depreciation_account_id",
        "depreciation_expense_account_id", "gain_loss_account_id",
    )
    for field in account_fields:
        identifier = values[field]
        if identifier is not None:
            account = _validate_account(db, str(identifier), field, active=active_all or field in reference_changes)
            if field == "asset_account_id" and parsed.type in {"inventory_part", "inventory_assembly"} and account.get("system_role") != "inventory_asset":
                raise _validation(field, "inventory items require the inventory-asset system account")
            if field == "asset_account_id" and parsed.type == "fixed_asset" and account["type"] != "fixed_asset":
                raise _validation(field, "fixed assets require a fixed-asset account")
            if field == "liability_account_id" and parsed.type == "sales_tax_item" and account.get("system_role") != "sales_tax_payable":
                raise _validation(field, "sales-tax items require the sales-tax-payable system account")
            if field == "deposit_account_id" and account["type"] == "other_current_asset" and account.get("system_role") != "undeposited_funds":
                raise _validation(field, "other-current-asset deposits require the undeposited-funds system account")
    soft = {
        "category_id": (schema.item_categories, "item_category"),
        "default_class_id": (schema.classes, "class"),
        "sales_tax_code_id": (schema.sales_tax_codes, "sales_tax_code"),
        "payment_method_id": (schema.payment_methods, "payment_method"),
        "vendor_id": (schema.vendors, "vendor"),
        "tax_agency_vendor_id": (schema.vendors, "vendor"),
        "preferred_vendor_id": (schema.vendors, "vendor"),
    }
    for field, (table, record_type) in soft.items():
        identifier = values[field]
        if identifier is not None:
            found = _reference(db, table, str(identifier), field=field, record_type=record_type, active=active_all or field in reference_changes)
            if field == "tax_agency_vendor_id" and not found["is_tax_agency"]:
                raise _validation(field, "must reference a vendor marked as a tax agency")
    if values["default_class_id"] is not None and (creating or "default_class_id" in reference_changes) and not bool(_info(db)["use_classes"]):
        raise _validation("default_class_id", "cannot be assigned while company classes are disabled")
    if parsed.unit_of_measure_set_id is not None and (creating or "unit_of_measure_set_id" in reference_changes):
        units.validate_unit_assignment(db, parsed.unit_of_measure_set_id)
    return values


def _prepare_member(db: Database, owner_id: str, owner_type: str, member: ItemMemberInput) -> dict[str, Any]:
    component = _reference(
        db, schema.items, member.component_item_id,
        field="members.component_item_id", record_type="item", active=True,
    )
    if component["id"] == owner_id:
        raise BookflowError("E_HIERARCHY_CYCLE", details={"record_id": owner_id, "component_item_id": owner_id})
    if owner_type == "inventory_assembly" and component["type"] not in _ASSEMBLY_COMPONENT_TYPES:
        raise _validation("members.component_item_id", "is not a permitted inventory-assembly component")
    if owner_type == "sales_tax_group" and component["type"] != "sales_tax_item":
        raise _validation("members.component_item_id", "sales-tax groups accept only sales-tax items")
    quantity = parse_quantity_micro_units(member.quantity, field="members.quantity")
    if owner_type == "sales_tax_group" and quantity != 1_000_000:
        raise _validation("members.quantity", "sales-tax group members must have quantity 1")
    if owner_type == "inventory_assembly" and quantity == 0:
        raise _validation("members.quantity", "inventory-assembly member quantity must be greater than zero")
    if member.unit_id is not None:
        if component["unit_of_measure_set_id"] is None:
            raise _validation("members.unit_id", "the component does not use a unit set")
        unit = _reference(
            db, schema.unit_conversions, member.unit_id,
            field="members.unit_id", record_type="unit_conversion", active=True,
        )
        if unit["unit_of_measure_id"] != component["unit_of_measure_set_id"]:
            raise _validation("members.unit_id", "must belong to the component item's unit set")
    values = {
        "component_item_id": member.component_item_id,
        "quantity_microunits": quantity,
        "unit_id": member.unit_id,
    }
    if member.id is not None:
        values["id"] = member.id
    return values


def _graph_reaches(db: Database, start_id: str, target_id: str) -> bool:
    """Return whether active membership edges reach target, bounded by visited ids."""
    pending = [start_id]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(
            str(value)
            for value in db.conn.execute(
                sa.select(schema.item_members.c.component_item_id).where(
                    schema.item_members.c.owner_item_id == current,
                    schema.item_members.c.active.is_(True),
                )
            ).scalars()
            if str(value) not in seen
        )
    return False


def _plan_members(
    db: Database,
    existing: Sequence[Mapping[str, Any]],
    submitted: Sequence[ItemMemberInput],
    *,
    owner_id: str,
    owner_type: str,
) -> tuple[dict[str, Any], ...]:
    prepared = [_prepare_member(db, owner_id, owner_type, member) for member in submitted]
    reconciled = list_service.reconcile_children(existing, prepared, semantic_key="component_item_id")
    active = [{**row, "owner_item_id": owner_id} for row in reconciled.active]
    for member in active:
        component_id = str(member["component_item_id"])
        if component_id == owner_id or _graph_reaches(db, component_id, owner_id):
            raise BookflowError(
                "E_HIERARCHY_CYCLE",
                details={"record_id": owner_id, "component_item_id": component_id},
            )
    return (
        *active,
        *({**row, "owner_item_id": owner_id} for row in reconciled.retired),
        *({**row, "owner_item_id": owner_id} for row in reconciled.inactive),
    )


def _prepare_vendor(db: Database, profile: ItemVendorProfileInput) -> dict[str, Any]:
    _reference(
        db, schema.vendors, profile.vendor_id,
        field="vendor_profiles.vendor_id", record_type="vendor", active=True,
    )
    values: dict[str, Any] = {
        "vendor_id": profile.vendor_id,
        "preferred_rank": profile.preferred_rank,
        "vendor_item_name": profile.vendor_item_name,
        **_money(profile.purchase_cost, db, "purchase_cost"),
        "minimum_quantity_microunits": (
            None if profile.minimum_quantity is None
            else parse_quantity_micro_units(profile.minimum_quantity, field="vendor_profiles.minimum_quantity")
        ),
        "lead_time_days": profile.lead_time_days,
        "manufacturer_part_number": profile.manufacturer_part_number,
        "availability_notes": profile.availability_notes,
    }
    if profile.id is not None:
        values["id"] = profile.id
    return values


def _normalize_vendor_ranks(
    rows: list[dict[str, Any]],
    preferred_vendor_id: str | None,
    *,
    preferred_supplied: bool,
) -> list[dict[str, Any]]:
    by_vendor = {str(row["vendor_id"]): row for row in rows}
    if preferred_supplied and preferred_vendor_id is None:
        prior_rank_one = next((row for row in rows if int(row["preferred_rank"]) == 1), None)
        if prior_rank_one is not None:
            prior_rank_one["preferred_rank"] = max(int(row["preferred_rank"]) for row in rows) + 1
        return rows
    if preferred_vendor_id is not None and preferred_vendor_id not in by_vendor:
        rows.append({
            "vendor_id": preferred_vendor_id,
            "preferred_rank": 1,
            "vendor_item_name": None,
            "purchase_cost_minor_units": None,
            "purchase_cost_currency": None,
            "minimum_quantity_microunits": None,
            "lead_time_days": None,
            "manufacturer_part_number": None,
            "availability_notes": None,
        })
        by_vendor[preferred_vendor_id] = rows[-1]
    if preferred_vendor_id is not None:
        preferred = by_vendor[preferred_vendor_id]
        previous_rank = int(preferred["preferred_rank"])
        prior_rank_one = next(
            (row for row in rows if row is not preferred and int(row["preferred_rank"]) == 1),
            None,
        )
        preferred["preferred_rank"] = 1
        if prior_rank_one is not None:
            occupied = {
                int(row["preferred_rank"])
                for row in rows
                if row is not preferred and row is not prior_rank_one
            }
            replacement = previous_rank if previous_rank > 1 and previous_rank not in occupied else max(occupied | {1}) + 1
            prior_rank_one["preferred_rank"] = replacement
    return rows


def _plan_vendors(
    db: Database,
    existing: Sequence[Mapping[str, Any]],
    submitted: Sequence[ItemVendorProfileInput],
    *,
    owner_id: str,
    preferred_vendor_id: str | None,
    preferred_supplied: bool = False,
) -> tuple[dict[str, Any], ...]:
    prepared = [_prepare_vendor(db, profile) for profile in submitted]
    prepared = _normalize_vendor_ranks(
        prepared, preferred_vendor_id, preferred_supplied=preferred_supplied,
    )
    reconciled = list_service.reconcile_children(existing, prepared, semantic_key="vendor_id")
    active = [{**row, "item_id": owner_id} for row in reconciled.active]
    ranks = [int(row["preferred_rank"]) for row in active]
    if len(ranks) != len(set(ranks)):
        raise _validation("vendor_profiles.preferred_rank", "active preference ranks must be unique")
    return (
        *active,
        *({**row, "item_id": owner_id} for row in reconciled.retired),
        *({**row, "item_id": owner_id} for row in reconciled.inactive),
    )


def _preferred_from_profiles(rows: Sequence[Mapping[str, Any]]) -> str | None:
    rank_one = [row for row in rows if row["active"] and int(row["preferred_rank"]) == 1]
    return None if not rank_one else str(rank_one[0]["vendor_id"])


def _custom_after(
    db: Database,
    record_id: str,
    plan: custom_fields.OwnerCustomFieldPlan | None,
) -> tuple[dict[str, Any], ...]:
    current = {
        item["definition_id"]: dict(item)
        for item in custom_fields.read_owner_values(db, record_type=ITEM_RECORD_TYPE, record_id=record_id)
    }
    if plan is not None:
        for mutation in plan.mutations:
            if not mutation.active:
                current.pop(mutation.definition_id, None)
                continue
            definition = custom_fields.read_definition(db, mutation.definition_id)
            current[mutation.definition_id] = {
                "id": mutation.row_id,
                "definition_id": mutation.definition_id,
                "name": definition["name"],
                "kind": definition["kind"],
                "definition_active": bool(definition["active"]),
                "value": custom_fields.typed_value_from_canonical(definition["kind"], mutation.canonical_text),
                "_position": definition["position"],
            }
    ordered = sorted(current.values(), key=lambda item: (item.get("_position", 0), item["name"].casefold(), item["definition_id"]))
    return tuple({key: value for key, value in item.items() if key != "_position"} for item in ordered)


def _money_input(row: Mapping[str, Any], field: str) -> str | None:
    value = _money_output(row, field)
    return None if value is None else f"{value.amount} {value.currency}"


def _member_input(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "component_item_id": row["component_item_id"],
        "quantity": format_quantity_micro_units(row["quantity_microunits"]), "unit_id": row["unit_id"],
    }


def _vendor_input(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "vendor_id": row["vendor_id"], "preferred_rank": row["preferred_rank"],
        "vendor_item_name": row["vendor_item_name"], "purchase_cost": _money_input(row, "purchase_cost"),
        "minimum_quantity": (
            None if row["minimum_quantity_microunits"] is None
            else format_quantity_micro_units(row["minimum_quantity_microunits"])
        ),
        "lead_time_days": row["lead_time_days"],
        "manufacturer_part_number": row["manufacturer_part_number"],
        "availability_notes": row["availability_notes"],
    }


def _payload_from_state(owner: Mapping[str, Any], members: Sequence[Mapping[str, Any]], vendors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": owner["name"], "type": owner["type"], "parent_id": owner["parent_id"],
        "category_id": owner["category_id"], "description": owner["description"],
        "purchase_description": owner["purchase_description"], "sales_enabled": bool(owner["sales_enabled"]),
        "purchase_enabled": bool(owner["purchase_enabled"]), "preferred_vendor_id": owner["preferred_vendor_id"],
    }
    payload.update({field: owner[field] for field in _DIRECT_FIELDS if field not in {"category_id", "description", "purchase_description"}})
    for field in _MONEY_FIELDS:
        payload[field] = _money_input(owner, field)
    for field, column in _PERCENT_FIELDS.items():
        payload[field] = None if owner[column] is None else format_percentage_millionths(owner[column])
    for field, column in _QUANTITY_FIELDS.items():
        payload[field] = None if owner[column] is None else format_quantity_micro_units(owner[column])
    if owner["type"] in _MEMBER_TYPES:
        payload["members"] = [_member_input(row) for row in members if row["active"]]
    if owner["type"] in {"service", "non_inventory_part", "inventory_part", "inventory_assembly", "other_charge"}:
        payload["vendor_profiles"] = [_vendor_input(row) for row in vendors if row["active"]]
    # Keep only fields admitted by the stored discriminator.  Derived storage
    # booleans for subtotal/group/payment/tax items are deliberately not input.
    allowed = ITEM_PROFILES[str(owner["type"])].allowed
    return {key: value for key, value in payload.items() if key in allowed}


def _collection_changed(before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]], converter) -> bool:
    return [converter(row) for row in before if row["active"]] != [converter(row) for row in after if row["active"]]


def _validate_type_change(db: Database, current: Mapping[str, Any], target_type: str) -> None:
    old_type = str(current["type"])
    if old_type == target_type:
        return
    if old_type not in _TRANSITIONABLE or target_type not in _TRANSITIONABLE:
        raise BookflowError("E_TYPE_CHANGE", details={"record_id": current["id"], "from": old_type, "to": target_type})
    clauses = [schema.items.c.parent_id == current["id"]]
    if current.get("parent_id") is not None:
        clauses.append(schema.items.c.id == current["parent_id"])
    related = db.conn.execute(
        sa.select(schema.items.c.id, schema.items.c.type).where(sa.or_(*clauses))
    ).mappings().all()
    if any(row["type"] != target_type for row in related):
        raise BookflowError("E_TYPE_CHANGE", details={"record_id": current["id"], "from": old_type, "to": target_type, "hierarchy": True})


def plan_item_create(
    db: Database,
    payload: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    record_id: str | None = None,
    at: str | None = None,
) -> ItemMutation:
    parsed = parse_item_input(payload)
    identifier = record_id or new_id()
    supplied = set(payload)
    hierarchy = _hierarchy_values(
        db, record_id=identifier, name=parsed.name,
        item_type=parsed.type, parent_id=parsed.parent_id,
    )
    owner_values = _owner_values(
        db, parsed, supplied=supplied, reference_changes=supplied, creating=True,
    )
    member_rows = _plan_members(
        db, (), parsed.members or (), owner_id=identifier, owner_type=parsed.type,
    ) if parsed.type in _MEMBER_TYPES else ()
    vendor_rows = _plan_vendors(
        db, (), parsed.vendor_profiles or (), owner_id=identifier,
        preferred_vendor_id=parsed.preferred_vendor_id,
        preferred_supplied="preferred_vendor_id" in supplied,
    ) if parsed.type in {"service", "non_inventory_part", "inventory_part", "inventory_assembly", "other_charge"} else ()
    owner_values["preferred_vendor_id"] = _preferred_from_profiles(vendor_rows)
    custom_plan = custom_fields.plan_owner_value_patch(
        db,
        record_type=ITEM_RECORD_TYPE,
        record_id=identifier,
        patch=parsed.custom_fields,
        creating=True,
    )
    custom_after = _custom_after(db, identifier, custom_plan)
    timestamp = at or now_iso()
    owner = {
        **list_service.create_metadata(actor_id, via, record_id=identifier, at=timestamp),
        "active": True,
        "seed_key": None,
        **hierarchy,
        **owner_values,
    }
    initial_changes = tuple(
        sorted(
            field for field in supplied
            if field not in {"custom_fields"}
        )
    )
    custom_changes = tuple(".".join(path) for path in custom_plan.logical_paths)
    return ItemMutation(
        "create", None, owner, (), tuple(member_rows), (), tuple(vendor_rows),
        (), custom_after, tuple(sorted((*initial_changes, *custom_changes))), custom_plan,
    )


def _hierarchy_plan(
    db: Database,
    current: Mapping[str, Any],
    parsed: ItemInput,
) -> tuple[dict[str, Any], tuple[ProjectionUpdate, ...]]:
    definition = get_list_definition(ITEM_NOUN)
    assert definition is not None

    def same_type(root: Mapping[str, Any], parent: Mapping[str, Any]) -> None:
        if parent["type"] != parsed.type:
            raise _validation("parent_id", "must reference an item of the same type")
        if parent["type"] in _NON_PARENT_TYPES:
            raise _validation("parent_id", f"{parent['type']} items cannot have children")

    planned = list_service.plan_reparent(
        db, schema.items, definition, current,
        parent_id=parsed.parent_id, name=parsed.name, parent_rule=same_type,
    )
    root = planned.updates[0]
    values = {
        "name": root.name, "name_key": root.name_key, "parent_id": parsed.parent_id,
        "full_name": root.full_name, "full_name_key": root.full_name_key,
        "depth": root.depth, "path": root.path,
    }
    projections = tuple(
        ProjectionUpdate(item.id, {
            "full_name": item.full_name, "full_name_key": item.full_name_key,
            "depth": item.depth, "path": item.path,
        })
        for item in planned.updates[1:]
    )
    return values, projections


def plan_item_update(
    db: Database,
    record_id: str,
    changes: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> ItemUpdatePlan:
    current = _row(db, record_id)
    existing_members = _members(db, record_id)
    existing_vendors = _vendors(db, record_id)
    existing_custom = tuple(custom_fields.read_owner_values(db, record_type=ITEM_RECORD_TYPE, record_id=record_id))
    target_type = changes.get("type", current["type"])
    if target_type is None:
        raise _validation("type", "cannot be cleared")
    _validate_type_change(db, current, str(target_type))

    payload = _payload_from_state(current, existing_members, existing_vendors)
    if target_type != current["type"]:
        target_allowed = ITEM_PROFILES[str(target_type)].allowed
        payload = {key: value for key, value in payload.items() if key in target_allowed or key in {"name", "type"}}
    payload.update({key: value for key, value in changes.items() if key != "custom_fields"})
    parsed = parse_item_input(payload)
    supplied = set(payload)
    owner_values = _owner_values(
        db,
        parsed,
        supplied=supplied,
        reference_changes=set(changes),
        creating=False,
    )

    hierarchy_values, projections = _hierarchy_plan(db, current, parsed)
    if parsed.type in _MEMBER_TYPES:
        planned_members = (
            _plan_members(
                db, existing_members, parsed.members or (),
                owner_id=record_id, owner_type=parsed.type,
            )
            if "members" in changes or parsed.type != current["type"]
            else existing_members
        )
    else:
        planned_members = tuple({**row, "active": False} for row in existing_members) if any(row["active"] for row in existing_members) else existing_members

    vendor_types = {"service", "non_inventory_part", "inventory_part", "inventory_assembly", "other_charge"}
    if parsed.type in vendor_types:
        if "vendor_profiles" in changes or "preferred_vendor_id" in changes or parsed.type != current["type"]:
            preferred = parsed.preferred_vendor_id if "preferred_vendor_id" in changes else None
            planned_vendors = _plan_vendors(
                db, existing_vendors, parsed.vendor_profiles or (),
                owner_id=record_id, preferred_vendor_id=preferred,
                preferred_supplied="preferred_vendor_id" in changes,
            )
        else:
            planned_vendors = existing_vendors
    else:
        planned_vendors = tuple({**row, "active": False} for row in existing_vendors) if any(row["active"] for row in existing_vendors) else existing_vendors
    owner_values["preferred_vendor_id"] = _preferred_from_profiles(planned_vendors)

    custom_plan = (
        custom_fields.plan_owner_value_patch(
            db,
            record_type=ITEM_RECORD_TYPE,
            record_id=record_id,
            patch=changes["custom_fields"],
            creating=False,
        )
        if "custom_fields" in changes else None
    )
    custom_after = _custom_after(db, record_id, custom_plan)
    after_candidate = {**current, **hierarchy_values, **owner_values}

    changed_fields: set[str] = set()
    storage_groups = {
        **{field: (field,) for field in _DIRECT_FIELDS},
        "name": ("name", "full_name"), "parent_id": ("parent_id", "full_name"),
        "type": ("type",), "sales_enabled": ("sales_enabled",),
        "purchase_enabled": ("purchase_enabled",), "preferred_vendor_id": ("preferred_vendor_id",),
        **{field: (f"{field}_minor_units", f"{field}_currency") for field in _MONEY_FIELDS},
        **{field: (column,) for field, column in _PERCENT_FIELDS.items()},
        **{field: (column,) for field, column in _QUANTITY_FIELDS.items()},
    }
    for field in changes:
        if field in {"members", "vendor_profiles", "custom_fields"}:
            continue
        columns = storage_groups.get(field, (field,))
        if any(current.get(column) != after_candidate.get(column) for column in columns):
            changed_fields.add(field)
    if _collection_changed(existing_members, planned_members, _member_input):
        changed_fields.add("members")
    if _collection_changed(existing_vendors, planned_vendors, _vendor_input):
        changed_fields.add("vendor_profiles")
        if current["preferred_vendor_id"] != owner_values["preferred_vendor_id"]:
            changed_fields.add("preferred_vendor_id")
    if custom_plan is not None:
        changed_fields.update(".".join(path) for path in custom_plan.logical_paths)
    if not changed_fields:
        return ItemUpdatePlan(None, (), ())
    after = {
        **after_candidate,
        **list_service.update_metadata(current, actor_id, via, at=at or now_iso()),
    }
    mutation = ItemMutation(
        "update", current, after,
        existing_members, tuple(planned_members), existing_vendors, tuple(planned_vendors),
        existing_custom, custom_after, tuple(sorted(changed_fields)), custom_plan,
    )
    return ItemUpdatePlan(mutation, projections, tuple(sorted(changed_fields)))


def _validate_stored(db: Database, owner: Mapping[str, Any], *, activating: bool) -> None:
    members = _members(db, str(owner["id"]))
    vendors = _vendors(db, str(owner["id"]))
    payload = _payload_from_state(owner, members, vendors)
    parsed = parse_item_input(payload)
    _validate_profile(parsed, set(payload))
    _owner_values(
        db, parsed, supplied=set(payload), reference_changes=set(payload) if activating else set(), creating=activating,
    )
    if activating:
        if owner["type"] in _MEMBER_TYPES:
            _plan_members(db, members, [ItemMemberInput.model_validate(_member_input(row)) for row in members if row["active"]], owner_id=str(owner["id"]), owner_type=str(owner["type"]))
        if any(row["active"] for row in vendors):
            if owner["preferred_vendor_id"] != _preferred_from_profiles(vendors):
                raise _validation("preferred_vendor_id", "does not match the active rank-one vendor profile")
            _plan_vendors(
                db,
                vendors,
                [ItemVendorProfileInput.model_validate(_vendor_input(row)) for row in vendors if row["active"]],
                owner_id=str(owner["id"]),
                preferred_vendor_id=None,
                preferred_supplied=False,
            )


def _member_dependents(db: Database, target_ids: Sequence[str]) -> tuple[dict[str, Any], ...]:
    if not target_ids:
        return ()
    owner = schema.items.alias("member_owner")
    count = int(db.conn.execute(
        sa.select(sa.func.count()).select_from(
            schema.item_members.join(owner, owner.c.id == schema.item_members.c.owner_item_id)
        ).where(
            schema.item_members.c.component_item_id.in_(target_ids),
            schema.item_members.c.active.is_(True),
            owner.c.active.is_(True),
            owner.c.id.not_in(target_ids),
        )
    ).scalar_one())
    return () if not count else ({"record_type": "item_member", "count": count},)


def plan_item_active_change(
    db: Database,
    record_id: str,
    active: bool,
    *,
    actor_id: str,
    via: str,
    cascade: bool = False,
    at: str | None = None,
) -> ItemActivePlan:
    current = _row(db, record_id)
    _validate_stored(db, current, activating=active)
    if bool(current["active"]) is active:
        if active:
            list_service.plan_activation(db, schema.items, current)
        else:
            list_service.plan_deactivation(db, schema.items, current, cascade=cascade)
        return ItemActivePlan(record_id, active, ())
    rows = (
        list_service.plan_activation(db, schema.items, current)
        if active else list_service.plan_deactivation(db, schema.items, current, cascade=cascade)
    )
    if not active:
        dependents = _member_dependents(db, [str(row["id"]) for row in rows])
        if dependents:
            raise BookflowError("E_RECORD_IN_USE", details={"record_id": record_id, "dependents": list(dependents)})
    timestamp = at or now_iso()
    mutations: list[ItemMutation] = []
    for row in rows:
        members = _members(db, str(row["id"]))
        vendors = _vendors(db, str(row["id"]))
        custom = tuple(custom_fields.read_owner_values(db, record_type=ITEM_RECORD_TYPE, record_id=str(row["id"])))
        after = {**row, "active": active, **list_service.update_metadata(row, actor_id, via, at=timestamp)}
        mutations.append(ItemMutation(
            "activate" if active else "deactivate", row, after,
            members, members, vendors, vendors, custom, custom, ("active",), None,
        ))
    return ItemActivePlan(record_id, active, tuple(mutations))


def _persist_collection(
    db: Database,
    table: sa.Table,
    owner_field: str,
    owner_id: str,
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> None:
    db.conn.execute(
        table.update().where(table.c[owner_field] == owner_id, table.c.active.is_(True)).values(active=False)
    )
    existing_ids = {str(row["id"]) for row in before}
    for row in after:
        values = {field: row[field] for field in table.c.keys()}
        if str(row["id"]) in existing_ids:
            db.conn.execute(table.update().where(table.c.id == row["id"]).values(**values))
        else:
            db.conn.execute(table.insert().values(**values))


def persist_item_mutation(db: Database, mutation: ItemMutation) -> None:
    """Persist one completely validated item aggregate mutation."""
    if mutation.before is None:
        db.conn.execute(schema.items.insert().values(**dict(mutation.after)))
    else:
        db.conn.execute(
            schema.items.update().where(schema.items.c.id == mutation.after["id"]).values(
                **{field: mutation.after[field] for field in schema.items.c.keys()}
            )
        )
    changed = set(mutation.changed_fields)
    owner_id = str(mutation.after["id"])
    if mutation.before is None or "members" in changed:
        _persist_collection(
            db, schema.item_members, "owner_item_id", owner_id,
            mutation.before_members, mutation.after_members,
        )
    if mutation.before is None or changed & {"vendor_profiles", "preferred_vendor_id"}:
        _persist_collection(
            db, schema.item_vendor_profiles, "item_id", owner_id,
            mutation.before_vendors, mutation.after_vendors,
        )
    if mutation.custom_plan is not None:
        custom_fields.apply_owner_value_plan(db, mutation.custom_plan)


def persist_item_update(db: Database, plan: ItemUpdatePlan) -> None:
    if plan.mutation is None:
        return
    persist_item_mutation(db, plan.mutation)
    for projection in plan.projections:
        db.conn.execute(
            schema.items.update().where(schema.items.c.id == projection.record_id).values(**dict(projection.values))
        )


def persist_item_active_change(db: Database, plan: ItemActivePlan) -> None:
    for mutation in plan.mutations:
        persist_item_mutation(db, mutation)


@dataclass(frozen=True)
class _ProjectionCache:
    item_rows: Mapping[str, Mapping[str, Any]]
    members: Mapping[str, tuple[Mapping[str, Any], ...]]
    vendors: Mapping[str, tuple[Mapping[str, Any], ...]]
    custom: Mapping[str, tuple[Mapping[str, Any], ...]]
    child_counts: Mapping[str, int]
    labels: Mapping[tuple[str, str], str]
    unit_rows: Mapping[str, Mapping[str, Any]]
    unit_sets: Mapping[str, Mapping[str, Any]]
    home_currency: str


def _build_projection_cache(
    db: Database,
    owners: Sequence[Mapping[str, Any]],
) -> _ProjectionCache:
    """Batch-load the relations needed to project an arbitrary item page."""
    owner_ids = tuple(str(row["id"]) for row in owners)
    if not owner_ids:
        return _ProjectionCache({}, {}, {}, {}, {}, {}, {}, {}, _home_currency(db))

    member_rows = tuple(dict(row) for row in db.conn.execute(
        sa.select(schema.item_members)
        .where(schema.item_members.c.owner_item_id.in_(owner_ids))
        .order_by(schema.item_members.c.owner_item_id, schema.item_members.c.position, schema.item_members.c.id)
    ).mappings())
    vendor_rows = tuple(dict(row) for row in db.conn.execute(
        sa.select(schema.item_vendor_profiles)
        .where(schema.item_vendor_profiles.c.item_id.in_(owner_ids))
        .order_by(schema.item_vendor_profiles.c.item_id, schema.item_vendor_profiles.c.position, schema.item_vendor_profiles.c.id)
    ).mappings())
    custom_rows = tuple(db.conn.execute(
        sa.select(
            schema.custom_field_values.c.id,
            schema.custom_field_values.c.record_id,
            schema.custom_field_values.c.def_id,
            schema.custom_field_values.c.canonical_text,
            schema.custom_field_defs.c.name,
            schema.custom_field_defs.c.kind,
            schema.custom_field_defs.c.active.label("definition_active"),
        )
        .select_from(schema.custom_field_values.join(
            schema.custom_field_defs,
            schema.custom_field_defs.c.id == schema.custom_field_values.c.def_id,
        ))
        .where(
            schema.custom_field_values.c.record_type == ITEM_RECORD_TYPE,
            schema.custom_field_values.c.record_id.in_(owner_ids),
            schema.custom_field_values.c.active.is_(True),
        )
        .order_by(
            schema.custom_field_values.c.record_id,
            schema.custom_field_defs.c.position,
            schema.custom_field_defs.c.name_key,
            schema.custom_field_defs.c.id,
        )
    ).mappings())

    members: dict[str, list[Mapping[str, Any]]] = {record_id: [] for record_id in owner_ids}
    vendors: dict[str, list[Mapping[str, Any]]] = {record_id: [] for record_id in owner_ids}
    custom: dict[str, list[Mapping[str, Any]]] = {record_id: [] for record_id in owner_ids}
    for row in member_rows:
        members[str(row["owner_item_id"])].append(row)
    for row in vendor_rows:
        vendors[str(row["item_id"])].append(row)
    for row in custom_rows:
        custom[str(row["record_id"])].append({
            "id": row["id"],
            "definition_id": row["def_id"],
            "name": row["name"],
            "kind": row["kind"],
            "definition_active": bool(row["definition_active"]),
            "value": custom_fields.typed_value_from_canonical(row["kind"], row["canonical_text"]),
        })

    child_counts = {
        str(parent_id): int(count)
        for parent_id, count in db.conn.execute(
            sa.select(schema.items.c.parent_id, sa.func.count())
            .where(schema.items.c.parent_id.in_(owner_ids), schema.items.c.active.is_(True))
            .group_by(schema.items.c.parent_id)
        )
    }
    component_ids = {str(row["component_item_id"]) for row in member_rows}
    missing_component_ids = component_ids.difference(owner_ids)
    component_rows = () if not missing_component_ids else tuple(dict(row) for row in db.conn.execute(
        sa.select(schema.items).where(schema.items.c.id.in_(missing_component_ids))
    ).mappings())
    item_rows: dict[str, Mapping[str, Any]] = {
        str(row["id"]): row for row in (*owners, *component_rows)
    }

    unit_set_ids = {
        str(row["unit_of_measure_set_id"])
        for row in item_rows.values()
        if row["unit_of_measure_set_id"] is not None
    }
    unit_set_rows = () if not unit_set_ids else tuple(dict(row) for row in db.conn.execute(
        sa.select(schema.units_of_measure).where(schema.units_of_measure.c.id.in_(unit_set_ids))
    ).mappings())
    unit_rows = () if not unit_set_ids else tuple(dict(row) for row in db.conn.execute(
        sa.select(schema.unit_conversions).where(schema.unit_conversions.c.unit_of_measure_id.in_(unit_set_ids))
    ).mappings())

    labels: dict[tuple[str, str], str] = {}

    def load_labels(table: sa.Table, field: str, ids: set[str]) -> None:
        if not ids:
            return
        labels.update({
            (table.name, str(record_id)): str(label)
            for record_id, label in db.conn.execute(
                sa.select(table.c.id, table.c[field]).where(table.c.id.in_(ids))
            )
        })

    load_labels(
        schema.item_categories,
        "full_name",
        {str(row["category_id"]) for row in owners if row["category_id"] is not None},
    )
    load_labels(
        schema.classes,
        "full_name",
        {str(row["default_class_id"]) for row in owners if row["default_class_id"] is not None},
    )
    load_labels(
        schema.sales_tax_codes,
        "code",
        {str(row["sales_tax_code_id"]) for row in owners if row["sales_tax_code_id"] is not None},
    )
    load_labels(schema.units_of_measure, "name", unit_set_ids)
    load_labels(
        schema.vendors,
        "name",
        {
            *(str(row["preferred_vendor_id"]) for row in owners if row["preferred_vendor_id"] is not None),
            *(str(row["vendor_id"]) for row in vendor_rows),
        },
    )
    load_labels(
        schema.unit_conversions,
        "name",
        {str(row["unit_id"]) for row in member_rows if row["unit_id"] is not None},
    )
    return _ProjectionCache(
        item_rows=item_rows,
        members={key: tuple(value) for key, value in members.items()},
        vendors={key: tuple(value) for key, value in vendors.items()},
        custom={key: tuple(value) for key, value in custom.items()},
        child_counts=child_counts,
        labels=labels,
        unit_rows={str(row["id"]): row for row in unit_rows},
        unit_sets={str(row["id"]): row for row in unit_set_rows},
        home_currency=_home_currency(db),
    )


def _label(
    db: Database,
    table: sa.Table,
    record_id: str | None,
    *fields: str,
    cache: _ProjectionCache | None = None,
) -> str | None:
    if record_id is None:
        return None
    if cache is not None:
        return cache.labels.get((table.name, str(record_id)))
    field = next(field for field in fields if field in table.c)
    return db.conn.execute(sa.select(table.c[field]).where(table.c.id == record_id)).scalar_one_or_none()


def _cached_item(db: Database, record_id: str, cache: _ProjectionCache | None) -> Mapping[str, Any]:
    if cache is not None and record_id in cache.item_rows:
        return cache.item_rows[record_id]
    return _row(db, record_id)


def _member_output(
    db: Database, row: Mapping[str, Any], cache: _ProjectionCache | None = None,
) -> ItemMemberOutput:
    component = _cached_item(db, str(row["component_item_id"]), cache)
    return ItemMemberOutput(
        id=str(row["id"]), position=int(row["position"]), active=bool(row["active"]),
        component_item_id=str(row["component_item_id"]), component_name=str(component["full_name"]),
        component_type=str(component["type"]), quantity=format_quantity_micro_units(row["quantity_microunits"]),
        unit_id=row["unit_id"],
        unit_name=_label(db, schema.unit_conversions, row["unit_id"], "name", cache=cache),
    )


def _vendor_output(
    db: Database, row: Mapping[str, Any], cache: _ProjectionCache | None = None,
) -> ItemVendorProfileOutput:
    return ItemVendorProfileOutput(
        id=str(row["id"]), position=int(row["position"]), active=bool(row["active"]),
        vendor_id=str(row["vendor_id"]),
        vendor_name=str(_label(db, schema.vendors, str(row["vendor_id"]), "name", cache=cache)),
        preferred_rank=int(row["preferred_rank"]), vendor_item_name=row["vendor_item_name"],
        purchase_cost=_money_output(row, "purchase_cost"),
        minimum_quantity=(
            None if row["minimum_quantity_microunits"] is None
            else format_quantity_micro_units(row["minimum_quantity_microunits"])
        ),
        lead_time_days=row["lead_time_days"],
        manufacturer_part_number=row["manufacturer_part_number"],
        availability_notes=row["availability_notes"],
    )


def _bom_cost(
    db: Database,
    members: Sequence[Mapping[str, Any]],
    cache: _ProjectionCache | None = None,
) -> MoneyOutput:
    total = 0
    for member in members:
        if not member["active"]:
            continue
        component = _cached_item(db, str(member["component_item_id"]), cache)
        cost = int(component["cost_minor_units"] or 0)
        quantity = int(member["quantity_microunits"])
        if member["unit_id"] is not None:
            source = (
                cache.unit_rows[str(member["unit_id"])] if cache is not None
                else db.conn.execute(
                    sa.select(schema.unit_conversions).where(schema.unit_conversions.c.id == member["unit_id"])
                ).mappings().one()
            )
            unit_set = (
                cache.unit_sets[str(component["unit_of_measure_set_id"])] if cache is not None
                else db.conn.execute(
                    sa.select(schema.units_of_measure).where(
                        schema.units_of_measure.c.id == component["unit_of_measure_set_id"]
                    )
                ).mappings().one()
            )
            target_id = unit_set["default_purchase_unit_id"]
            if target_id is None:
                if cache is not None:
                    target = next(
                        row for row in cache.unit_rows.values()
                        if row["unit_of_measure_id"] == unit_set["id"] and row["active"] and row["is_base"]
                    )
                else:
                    target = db.conn.execute(
                        sa.select(schema.unit_conversions).where(
                            schema.unit_conversions.c.unit_of_measure_id == unit_set["id"],
                            schema.unit_conversions.c.active.is_(True),
                            schema.unit_conversions.c.is_base.is_(True),
                        )
                    ).mappings().one()
            else:
                target = (
                    cache.unit_rows[str(target_id)] if cache is not None
                    else db.conn.execute(
                        sa.select(schema.unit_conversions).where(schema.unit_conversions.c.id == target_id)
                    ).mappings().one()
                )
            quantity = convert_quantity_micro_units(
                quantity,
                int(source["base_factor_nanounits"]),
                int(target["base_factor_nanounits"]),
            )
        extension = bom_component_extension_minor_units(quantity, cost)
        total += extension
        if not INT64_MIN <= total <= INT64_MAX:
            raise _validation("bill_of_material_cost", "does not fit signed 64-bit storage", code="E_VALUE_RANGE")
    currency = cache.home_currency if cache is not None else _home_currency(db)
    return MoneyOutput(**Money(total, currency).to_dict())


def project_item(
    db: Database,
    owner: Mapping[str, Any],
    *,
    members: Sequence[Mapping[str, Any]] | None = None,
    vendor_profiles: Sequence[Mapping[str, Any]] | None = None,
    custom_values: Sequence[Mapping[str, Any]] | None = None,
    cache: _ProjectionCache | None = None,
) -> ItemOutput:
    owner_id = str(owner["id"])
    members = tuple(members) if members is not None else (
        cache.members.get(owner_id, ()) if cache is not None else _members(db, owner_id)
    )
    vendor_profiles = tuple(vendor_profiles) if vendor_profiles is not None else (
        cache.vendors.get(owner_id, ()) if cache is not None else _vendors(db, owner_id)
    )
    custom_values = tuple(custom_values) if custom_values is not None else (
        cache.custom.get(owner_id, ()) if cache is not None else tuple(
            custom_fields.read_owner_values(db, record_type=ITEM_RECORD_TYPE, record_id=owner_id)
        )
    )
    child_count = cache.child_counts.get(owner_id, 0) if cache is not None else int(db.conn.execute(
        sa.select(sa.func.count()).select_from(schema.items).where(
            schema.items.c.parent_id == owner["id"], schema.items.c.active.is_(True),
        )
    ).scalar_one())
    combined = None
    if owner["type"] == "sales_tax_group":
        combined_value = sum(
            int(_cached_item(db, str(member["component_item_id"]), cache)["tax_percent_millionths"] or 0)
            for member in members if member["active"]
        )
        if not INT64_MIN <= combined_value <= INT64_MAX:
            raise _validation("combined_percent", "does not fit signed 64-bit storage", code="E_VALUE_RANGE")
        combined = format_percentage_millionths(combined_value)
    zero_money = MoneyOutput(**Money(0, cache.home_currency if cache is not None else _home_currency(db)).to_dict())
    return ItemOutput(
        **{field: owner[field] for field in (
            "id", "version", "created_at", "created_by", "created_via", "updated_at",
            "updated_by", "updated_via", "active", "seed_key", "name", "type",
            "parent_id", "full_name", "depth", "category_id", "description",
            "purchase_description", "sales_enabled", "purchase_enabled", "income_account_id",
            "expense_account_id", "cogs_account_id", "asset_account_id", "deposit_account_id",
            "liability_account_id", "default_class_id", "sales_tax_code_id",
            "manufacturer_part_number", "barcode", "unit_of_measure_set_id", "preferred_vendor_id",
            "notes", "print_members", "payment_method_id", "use_undeposited_funds",
            "tax_agency_vendor_id", "asset_number", "purchase_date", "vendor_id", "location",
            "serial_number", "warranty_expiration", "disposal_status", "disposal_date",
            "accumulated_depreciation_account_id", "depreciation_expense_account_id",
            "gain_loss_account_id", "depreciation_method", "useful_life_months",
        )},
        category=_label(db, schema.item_categories, owner["category_id"], "full_name", cache=cache),
        default_class=_label(db, schema.classes, owner["default_class_id"], "full_name", cache=cache),
        sales_tax_code=_label(db, schema.sales_tax_codes, owner["sales_tax_code_id"], "code", cache=cache),
        unit_of_measure_set=_label(db, schema.units_of_measure, owner["unit_of_measure_set_id"], "name", cache=cache),
        preferred_vendor=_label(db, schema.vendors, owner["preferred_vendor_id"], "name", cache=cache),
        price=_money_output(owner, "price"), cost=_money_output(owner, "cost"),
        reorder_point_min=(None if owner["reorder_point_min_microunits"] is None else format_quantity_micro_units(owner["reorder_point_min_microunits"])),
        reorder_point_max=(None if owner["reorder_point_max_microunits"] is None else format_quantity_micro_units(owner["reorder_point_max_microunits"])),
        charge_percent=(None if owner["other_charge_percent_millionths"] is None else format_percentage_millionths(owner["other_charge_percent_millionths"])),
        discount_amount=_money_output(owner, "discount_amount"),
        discount_percent=(None if owner["discount_percent_millionths"] is None else format_percentage_millionths(owner["discount_percent_millionths"])),
        tax_percent=(None if owner["tax_percent_millionths"] is None else format_percentage_millionths(owner["tax_percent_millionths"])),
        assembly_build_point=(None if owner["assembly_build_point_microunits"] is None else format_quantity_micro_units(owner["assembly_build_point_microunits"])),
        original_cost=_money_output(owner, "original_cost"), disposal_proceeds=_money_output(owner, "disposal_proceeds"),
        disposal_costs=_money_output(owner, "disposal_costs"), book_basis=_money_output(owner, "book_basis"),
        tax_basis=_money_output(owner, "tax_basis"),
        members=[_member_output(db, row, cache) for row in members],
        vendor_profiles=[_vendor_output(db, row, cache) for row in vendor_profiles],
        custom_fields=[CustomFieldValueOutput.model_validate(row) for row in custom_values],
        quantity_on_hand="0", quantity_available="0", quantity_committed="0", quantity_on_order="0",
        quantity_pending_build="0", average_cost=zero_money, inventory_value=zero_money,
        inventory_values_available=False,
        bill_of_material_cost=_bom_cost(db, members, cache) if owner["type"] == "inventory_assembly" else None,
        combined_percent=combined, child_count=child_count, has_children=bool(child_count),
    )


def aggregate_snapshot(
    owner: Mapping[str, Any],
    members: Sequence[Mapping[str, Any]],
    vendors: Sequence[Mapping[str, Any]],
    custom: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    logical = dict(owner)
    for field in _MONEY_FIELDS:
        logical[field] = (
            None if logical.get(f"{field}_minor_units") is None
            else Money(int(logical[f"{field}_minor_units"]), str(logical[f"{field}_currency"])).to_dict()
        )
        logical.pop(f"{field}_minor_units", None)
        logical.pop(f"{field}_currency", None)
    for field, column in _PERCENT_FIELDS.items():
        logical[field] = None if logical.get(column) is None else format_percentage_millionths(logical[column])
        logical.pop(column, None)
    for field, column in _QUANTITY_FIELDS.items():
        logical[field] = None if logical.get(column) is None else format_quantity_micro_units(logical[column])
        logical.pop(column, None)
    member_values = [
        {
            "id": row["id"], "position": row["position"], "active": bool(row["active"]),
            "component_item_id": row["component_item_id"],
            "quantity": format_quantity_micro_units(row["quantity_microunits"]), "unit_id": row["unit_id"],
        }
        for row in members
    ]
    vendor_values = []
    for row in vendors:
        vendor_values.append({
            "id": row["id"], "position": row["position"], "active": bool(row["active"]),
            "vendor_id": row["vendor_id"], "preferred_rank": row["preferred_rank"],
            "vendor_item_name": row["vendor_item_name"],
            "purchase_cost": (
                None if row["purchase_cost_minor_units"] is None
                else Money(int(row["purchase_cost_minor_units"]), str(row["purchase_cost_currency"])).to_dict()
            ),
            "minimum_quantity": (
                None if row["minimum_quantity_microunits"] is None
                else format_quantity_micro_units(row["minimum_quantity_microunits"])
            ),
            "lead_time_days": row["lead_time_days"],
            "manufacturer_part_number": row["manufacturer_part_number"],
            "availability_notes": row["availability_notes"],
        })
    snapshot = list_service.aggregate_snapshot(
        logical,
        collections={"members": member_values, "vendor_profiles": vendor_values},
    )
    for row in custom:
        snapshot[f"custom_fields.{row['definition_id']}"] = row["value"]
    return snapshot


def item_query_options(
    db: Database,
    *,
    filters: Sequence[str] = (),
) -> dict[str, Any]:
    """Authoritative item search/filter/sort expressions for list and query."""
    definition = get_list_definition(ITEM_NOUN)
    assert definition is not None
    owner = schema.items
    ordinary_filters: list[str] = []
    custom_predicates: list[sa.ColumnElement[bool]] = []
    for entry in filters:
        if not isinstance(entry, str) or "=" not in entry:
            ordinary_filters.append(entry)
            continue
        field, raw = entry.split("=", 1)
        prefix = "custom_fields."
        if not field.startswith(prefix):
            ordinary_filters.append(entry)
            continue
        definition_id = field[len(prefix):]
        definition_row = custom_fields.read_definition(db, definition_id)
        choice_map = None
        if definition_row["kind"] == "choice":
            choice_rows = db.conn.execute(
                sa.select(schema.custom_field_choices.c.value, schema.custom_field_choices.c.value_key).where(
                    schema.custom_field_choices.c.definition_id == definition_id,
                    schema.custom_field_choices.c.active.is_(True),
                )
            ).all()
            choice_map = {str(key): str(value) for value, key in choice_rows}
        typed_raw: Any = raw
        if definition_row["kind"] == "bool":
            if raw not in {"true", "false"}:
                raise BookflowError(
                    "E_LIST_FILTER",
                    details={"problem": "boolean custom filters accept only true or false", "field": field, "value": raw},
                )
            typed_raw = raw == "true"
        try:
            canonical, _ = custom_fields.parse_typed_value(
                definition_row["kind"], typed_raw, field=field, choices=choice_map,
            )
        except BookflowError as exc:
            raise BookflowError(
                "E_LIST_FILTER",
                details={"problem": exc.details["fields"][0]["problem"], "field": field, "value": raw},
            ) from None
        matching_ids = sa.select(schema.custom_field_values.c.record_id).where(
            schema.custom_field_values.c.record_type == ITEM_RECORD_TYPE,
            schema.custom_field_values.c.def_id == definition_id,
            schema.custom_field_values.c.active.is_(True),
            schema.custom_field_values.c.canonical_text == canonical,
        )
        custom_predicates.append(owner.c.id.in_(matching_ids))
    vendor_identifier = (
        sa.func.coalesce(schema.item_vendor_profiles.c.vendor_item_name, "")
        + " "
        + sa.func.coalesce(schema.item_vendor_profiles.c.manufacturer_part_number, "")
    )
    vendor_names = sa.select(sa.func.group_concat(vendor_identifier, " ")).where(
        schema.item_vendor_profiles.c.item_id == owner.c.id,
        schema.item_vendor_profiles.c.active.is_(True),
    ).scalar_subquery()
    category = sa.select(schema.item_categories.c.full_name).where(schema.item_categories.c.id == owner.c.category_id).scalar_subquery()
    preferred = sa.select(schema.vendors.c.name).where(schema.vendors.c.id == owner.c.preferred_vendor_id).scalar_subquery()
    custom_search = sa.select(
        sa.func.group_concat(schema.custom_field_values.c.canonical_text, " ")
    ).select_from(
        schema.custom_field_values.join(
            schema.custom_field_defs,
            schema.custom_field_defs.c.id == schema.custom_field_values.c.def_id,
        )
    ).where(
        schema.custom_field_values.c.record_type == ITEM_RECORD_TYPE,
        schema.custom_field_values.c.record_id == owner.c.id,
        schema.custom_field_values.c.active.is_(True),
        schema.custom_field_defs.c.active.is_(True),
        schema.custom_field_defs.c.kind.in_(("text", "choice")),
    ).scalar_subquery()
    return dict(
        filters=ordinary_filters,
        search_expressions={
            "category": sa.func.lower(sa.func.coalesce(category, "")),
            "vendor_item_identifiers": sa.func.lower(sa.func.coalesce(vendor_names, "")),
            "$custom-searchable": sa.func.lower(sa.func.coalesce(custom_search, "")),
        },
        sort_expressions={
            "price": owner.c.price_minor_units,
            "cost": owner.c.cost_minor_units,
            "quantity_on_hand": sa.literal(0),
            "preferred_vendor": preferred,
            "category": category,
        },
        visible=sa.and_(*custom_predicates) if custom_predicates else None,
    )


def list_items(
    db: Database,
    *,
    query: str | None = None,
    filters: Sequence[str] = (),
    sort: str | None = None,
    direction: Literal["asc", "desc"] = "asc",
    include_inactive: bool = False,
) -> list[ItemOutput]:
    definition = get_list_definition(ITEM_NOUN)
    rows = list_service.list_rows(
        db, schema.items, definition, query=query, sort=sort,
        direction=direction, include_inactive=include_inactive,
        **item_query_options(db, filters=filters),
    )
    cache = _build_projection_cache(db, rows)
    return [project_item(db, row, cache=cache) for row in rows]


__all__ = [
    "DepreciationMethod", "DisposalStatus", "ITEM_NOUN", "ITEM_PROFILES", "ITEM_RECORD_TYPE",
    "ITEM_TYPES", "ItemActivePlan", "ItemInput", "ItemMemberInput", "ItemMemberOutput",
    "ItemMutation", "ItemOutput", "ItemProfile", "ItemType", "ItemUpdatePlan",
    "ItemVendorProfileInput", "ItemVendorProfileOutput", "MoneyOutput", "aggregate_snapshot",
    "list_items", "parse_item_input", "persist_item_active_change", "persist_item_mutation",
    "persist_item_update", "plan_item_active_change", "plan_item_create", "plan_item_update",
    "project_item", "resolve_item",
]
