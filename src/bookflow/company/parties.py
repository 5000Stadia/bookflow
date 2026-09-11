"""Customer, vendor, employee, and other-name master-data rules.

The module plans complete versioned mutations without writing.  Command
adapters own concurrency/audit metadata while this service owns party field,
reference, hierarchy, inheritance, exact-money, and completeness rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import sqlalchemy as sa

from bookflow.company import custom_fields, list_service, schema
from bookflow.company.lists import PARTY_LISTS, get_list_definition, hierarchy_projection, normalize_display_name
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX, INT64_MIN, format_quantity_micro_units
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.session import now_iso
from bookflow.core.versioning import current_writer, history_from_entries
from bookflow.storage.engine import Database


PartyNoun = Literal["customer", "vendor", "employee", "other-name"]
# The set itself is declared once in `lists`, where the form contracts read it too.
PARTY_NOUNS: tuple[PartyNoun, ...] = PARTY_LISTS  # type: ignore[assignment]
TABLES: dict[str, sa.Table] = {
    "customer": schema.customers,
    "vendor": schema.vendors,
    "employee": schema.employees,
    "other-name": schema.other_names,
}

_ADDRESS_LEAVES = ("line1", "line2", "city", "state", "postal_code", "country")
_PERSON_FIELDS = ("salutation", "first_name", "middle_name", "last_name", "job_title")
_CONTACT_FIELDS = (
    "role", "display_name", *_PERSON_FIELDS, "work_phone", "home_phone", "mobile_phone",
    "other_phone", "work_fax", "home_fax", "primary_email", "secondary_email", "website",
    "external_handle",
)
_SHORTCUTS: dict[str, tuple[str, str]] = {
    "contact": ("primary", "display_name"),
    "alt_contact": ("alternate", "display_name"),
    "phone": ("primary", "work_phone"),
    "alt_phone": ("alternate", "work_phone"),
    "fax": ("primary", "work_fax"),
    "email": ("primary", "primary_email"),
    "cc_email": ("primary", "secondary_email"),
    "website": ("primary", "website"),
}
_REFERENCE_TABLES: dict[str, tuple[sa.Table, str]] = {
    "terms_id": (schema.terms, "term"),
    "sales_tax_code_id": (schema.sales_tax_codes, "sales_tax_code"),
    "sales_tax_item_id": (schema.items, "item"),
    "price_level_id": (schema.price_levels, "price_level"),
    "customer_type_id": (schema.customer_types, "customer_type"),
    "sales_rep_id": (schema.sales_reps, "sales_rep"),
    "job_sales_rep_id": (schema.sales_reps, "sales_rep"),
    "preferred_payment_method_id": (schema.payment_methods, "payment_method"),
    "preferred_ship_method_id": (schema.ship_methods, "ship_method"),
    "job_type_id": (schema.job_types, "job_type"),
    "vendor_type_id": (schema.vendor_types, "vendor_type"),
    "default_class_id": (schema.classes, "class"),
}


def _validation(field: str, problem: str) -> BookflowError:
    return BookflowError(
        "E_VALIDATION",
        details={"fields": [{"field": field, "problem": problem}]},
    )


def _canonical_date(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date in YYYY-MM-DD form") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be a canonical ISO date in YYYY-MM-DD form")
    return value


class PartyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class AddressInput(PartyInput):
    line1: str | None = Field(default=None, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    state: str | None = Field(default=None, max_length=200)
    postal_code: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=200)


class ShippingAddressInput(AddressInput):
    id: str | None = Field(default=None, min_length=26, max_length=26)
    label: str = Field(min_length=1, max_length=128)
    is_default: bool = False


class ContactPointInput(PartyInput):
    id: str | None = Field(default=None, min_length=26, max_length=26)
    kind: Literal[
        "main_phone", "work_phone", "home_phone", "mobile_phone", "other_phone",
        "fax", "work_fax", "home_fax", "pager", "main_email", "additional_email",
        "cc_email", "website", "url_1", "url_2", "linked_in", "facebook",
        "twitter", "skype_id", "instant_messaging", "other_1", "other_2",
        "other_3", "other_4",
    ]
    custom_label: str | None = Field(default=None, max_length=64)
    value: str = Field(min_length=1, max_length=512)


class ContactInput(PartyInput):
    id: str | None = Field(default=None, min_length=26, max_length=26)
    role: Literal["primary", "alternate", "additional"]
    display_name: str | None = Field(default=None, max_length=200)
    salutation: str | None = Field(default=None, max_length=32)
    first_name: str | None = Field(default=None, max_length=128)
    middle_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    job_title: str | None = Field(default=None, max_length=128)
    work_phone: str | None = Field(default=None, max_length=64)
    home_phone: str | None = Field(default=None, max_length=64)
    mobile_phone: str | None = Field(default=None, max_length=64)
    other_phone: str | None = Field(default=None, max_length=64)
    work_fax: str | None = Field(default=None, max_length=64)
    home_fax: str | None = Field(default=None, max_length=64)
    primary_email: str | None = Field(default=None, max_length=254)
    secondary_email: str | None = Field(default=None, max_length=254)
    website: str | None = Field(default=None, max_length=254)
    external_handle: str | None = Field(default=None, max_length=128)
    # ``None`` means that a retained contact's nested collection was omitted
    # and must be preserved.  An explicit empty list is the clearing operation.
    points: list[ContactPointInput] | None = None


class VendorExpenseAccountInput(PartyInput):
    id: str | None = Field(default=None, min_length=26, max_length=26)
    account_id: str = Field(min_length=26, max_length=26)


class CustomerInput(PartyInput):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = Field(default=None, min_length=26, max_length=26)
    company_name: str | None = Field(default=None, max_length=200)
    salutation: str | None = Field(default=None, max_length=32)
    first_name: str | None = Field(default=None, max_length=128)
    middle_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    job_title: str | None = Field(default=None, max_length=128)
    billing_address: AddressInput | None = None
    terms_id: str | None = Field(default=None, min_length=26, max_length=26)
    sales_tax_code_id: str | None = Field(default=None, min_length=26, max_length=26)
    sales_tax_item_id: str | None = Field(default=None, min_length=26, max_length=26)
    price_level_id: str | None = Field(default=None, min_length=26, max_length=26)
    customer_type_id: str | None = Field(default=None, min_length=26, max_length=26)
    sales_rep_id: str | None = Field(default=None, min_length=26, max_length=26)
    preferred_payment_method_id: str | None = Field(default=None, min_length=26, max_length=26)
    preferred_ship_method_id: str | None = Field(default=None, min_length=26, max_length=26)
    resale_number: str | None = Field(default=None, max_length=128)
    credit_limit: Any | None = None
    preferred_delivery_method: Literal["none", "email", "mail"] | None = None
    account_number: str | None = Field(default=None, max_length=128)
    notes: str | None = None
    default_class_id: str | None = Field(default=None, min_length=26, max_length=26)
    job_status: Literal["none", "pending", "awarded", "in_progress", "closed", "not_awarded"] = "none"
    job_type_id: str | None = Field(default=None, min_length=26, max_length=26)
    job_start: str | None = None
    job_projected_end: str | None = None
    job_end: str | None = None
    job_description: str | None = None
    job_sales_rep_id: str | None = Field(default=None, min_length=26, max_length=26)
    address_mode: Literal["inherit", "own"] | None = None
    contact_mode: Literal["inherit", "own"] | None = None
    shipping_addresses: list[ShippingAddressInput] | None = None
    contacts: list[ContactInput] | None = None
    contact: str | None = Field(default=None, max_length=200)
    alt_contact: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=64)
    alt_phone: str | None = Field(default=None, max_length=64)
    fax: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    cc_email: str | None = Field(default=None, max_length=254)
    website: str | None = Field(default=None, max_length=254)
    custom_fields: dict[str, Any | None] | None = None

    @field_validator("job_start", "job_projected_end", "job_end")
    @classmethod
    def _dates_are_canonical(cls, value: str | None, info) -> str | None:
        return _canonical_date(value, info.field_name)

    @model_validator(mode="after")
    def _date_order(self) -> "CustomerInput":
        if self.job_start is not None:
            if self.job_projected_end is not None and self.job_projected_end < self.job_start:
                raise ValueError("job_projected_end cannot precede job_start")
            if self.job_end is not None and self.job_end < self.job_start:
                raise ValueError("job_end cannot precede job_start")
        return self


class VendorInput(PartyInput):
    name: str = Field(min_length=1, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    salutation: str | None = Field(default=None, max_length=32)
    first_name: str | None = Field(default=None, max_length=128)
    middle_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    job_title: str | None = Field(default=None, max_length=128)
    address: AddressInput | None = None
    terms_id: str | None = Field(default=None, min_length=26, max_length=26)
    vendor_type_id: str | None = Field(default=None, min_length=26, max_length=26)
    default_class_id: str | None = Field(default=None, min_length=26, max_length=26)
    account_number: str | None = Field(default=None, max_length=128)
    print_name_on_check_as: str | None = Field(default=None, max_length=200)
    credit_limit: Any | None = None
    eligible_1099: bool = False
    is_tax_agency: bool = False
    recall_last_transaction: bool | None = None
    notes: str | None = None
    contacts: list[ContactInput] | None = None
    expense_accounts: list[VendorExpenseAccountInput] | None = None
    contact: str | None = Field(default=None, max_length=200)
    alt_contact: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=64)
    alt_phone: str | None = Field(default=None, max_length=64)
    fax: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    cc_email: str | None = Field(default=None, max_length=254)
    website: str | None = Field(default=None, max_length=254)
    custom_fields: dict[str, Any | None] | None = None


class EmployeeInput(PartyInput):
    name: str = Field(min_length=1, max_length=200)
    salutation: str | None = Field(default=None, max_length=32)
    first_name: str | None = Field(default=None, max_length=128)
    middle_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    job_title: str | None = Field(default=None, max_length=128)
    print_name_on_check_as: str | None = Field(default=None, max_length=200)
    employment_type: Literal["full_time", "part_time", "seasonal", "temporary", "other"] | None = None
    address: AddressInput | None = None
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    hire_date: str | None = None
    release_date: str | None = None
    emergency_contact_name: str | None = Field(default=None, max_length=200)
    emergency_contact_relationship: str | None = Field(default=None, max_length=128)
    emergency_contact_phone: str | None = Field(default=None, max_length=64)
    emergency_contact_email: str | None = Field(default=None, max_length=254)
    default_class_id: str | None = Field(default=None, min_length=26, max_length=26)
    notes: str | None = None
    custom_fields: dict[str, Any | None] | None = None

    @field_validator("hire_date", "release_date")
    @classmethod
    def _dates_are_canonical(cls, value: str | None, info) -> str | None:
        return _canonical_date(value, info.field_name)

    @model_validator(mode="after")
    def _release_order(self) -> "EmployeeInput":
        if self.hire_date is not None and self.release_date is not None and self.release_date < self.hire_date:
            raise ValueError("release_date cannot precede hire_date")
        return self


class OtherNameInput(PartyInput):
    name: str = Field(min_length=1, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    salutation: str | None = Field(default=None, max_length=32)
    first_name: str | None = Field(default=None, max_length=128)
    middle_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    job_title: str | None = Field(default=None, max_length=128)
    address: AddressInput | None = None
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    contact: str | None = Field(default=None, max_length=200)
    account_number: str | None = Field(default=None, max_length=128)
    default_class_id: str | None = Field(default=None, min_length=26, max_length=26)
    notes: str | None = None
    custom_fields: dict[str, Any | None] | None = None


CREATE_MODELS: dict[str, type[PartyInput]] = {
    "customer": CustomerInput,
    "vendor": VendorInput,
    "employee": EmployeeInput,
    "other-name": OtherNameInput,
}


@dataclass(frozen=True)
class ProjectionUpdate:
    record_id: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class ChildTableWrite:
    table_name: str
    rows: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class PartyChildrenPlan:
    writes: tuple[ChildTableWrite, ...]
    collections_after: Mapping[str, tuple[Mapping[str, Any], ...]]

    @property
    def changed(self) -> bool:
        return bool(self.writes)


@dataclass(frozen=True)
class PartyMutation:
    noun: str
    action: Literal["create", "update", "activate", "deactivate"]
    before: Mapping[str, Any] | None
    after: Mapping[str, Any]
    before_snapshot: Mapping[str, Any] | None
    after_snapshot: Mapping[str, Any]
    custom_plan: custom_fields.OwnerCustomFieldPlan | None = None
    custom_values_after: tuple[Mapping[str, Any], ...] = ()
    children_plan: PartyChildrenPlan | None = None


@dataclass(frozen=True)
class PartyUpdatePlan:
    mutation: PartyMutation | None
    projections: tuple[ProjectionUpdate, ...]
    changed_fields: tuple[str, ...]
    custom_values_after: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class CustomerVendorLinkPlan:
    action: Literal["link", "unlink"]
    customer_before: Mapping[str, Any]
    customer_after: Mapping[str, Any]
    vendor_before: Mapping[str, Any]
    vendor_after: Mapping[str, Any]
    link_before: Mapping[str, Any] | None
    link_after: Mapping[str, Any]
    customer_before_snapshot: Mapping[str, Any]
    customer_after_snapshot: Mapping[str, Any]
    vendor_before_snapshot: Mapping[str, Any]
    vendor_after_snapshot: Mapping[str, Any]


@dataclass(frozen=True)
class OtherNameConversionPlan:
    source_before: Mapping[str, Any]
    source_after: Mapping[str, Any]
    source_before_snapshot: Mapping[str, Any]
    source_after_snapshot: Mapping[str, Any]
    target: PartyMutation
    mapped_fields: tuple[str, ...]
    retained_fields: tuple[str, ...]


@dataclass(frozen=True)
class PartyActivePlan:
    noun: str
    requested_id: str
    requested_active: bool
    mutations: tuple[PartyMutation, ...]

    @property
    def changed(self) -> bool:
        return bool(self.mutations)

    @property
    def affected_ids(self) -> tuple[str, ...]:
        return tuple(str(mutation.after["id"]) for mutation in self.mutations)


def _table(noun: str) -> sa.Table:
    try:
        return TABLES[noun]
    except KeyError:
        raise _validation("noun", "is not a party noun") from None


def _row(db: Database, table: sa.Table, record_id: str) -> dict[str, Any]:
    row = db.conn.execute(sa.select(table).where(table.c.id == record_id)).mappings().first()
    if row is None:
        raise BookflowError(
            "E_RECORD_NOT_FOUND",
            details={"record_type": table.name, "selector": record_id, "suggestions": []},
        )
    return dict(row)


def resolve_party(db: Database, noun: str, selector: str) -> dict[str, Any]:
    definition = get_list_definition(noun)
    assert definition is not None
    return list_service.resolve_selector(db, _table(noun), definition, selector)


def _parse(noun: str, payload: Mapping[str, Any]) -> PartyInput:
    try:
        return CREATE_MODELS[noun].model_validate(dict(payload))
    except ValidationError as exc:
        fields = [
            {
                "field": ".".join(str(part) for part in item["loc"]) or "input",
                "problem": item["msg"],
            }
            for item in exc.errors(include_url=False)
        ]
        raise BookflowError("E_VALIDATION", details={"fields": fields}) from None


def _home_currency(db: Database) -> str:
    value = db.conn.execute(sa.select(schema.company_info.c.home_currency)).scalar_one()
    return str(value)


def _money_storage(value: Any | None, db: Database, *, field: str) -> dict[str, Any]:
    if value is None:
        return {f"{field}_minor_units": None, f"{field}_currency": None}
    if isinstance(value, (int, float, bool)):
        raise _validation(field, "must be a decimal string or exact money object, never a number or boolean")
    money = Money.parse(value, _home_currency(db))
    if money.currency != _home_currency(db):
        raise _validation(field, "must use the company home currency")
    if not INT64_MIN <= money.minor_units <= INT64_MAX:
        raise BookflowError(
            "E_VALUE_RANGE",
            details={"field": field, "problem": "does not fit signed 64-bit storage"},
        )
    return {f"{field}_minor_units": money.minor_units, f"{field}_currency": money.currency}


def _address_storage(prefix: str, address: AddressInput | None) -> dict[str, Any]:
    return {
        f"{prefix}_{leaf}": None if address is None else getattr(address, leaf)
        for leaf in _ADDRESS_LEAVES
    }


def _address_output(row: Mapping[str, Any], prefix: str) -> dict[str, Any] | None:
    result = {leaf: row.get(f"{prefix}_{leaf}") for leaf in _ADDRESS_LEAVES}
    return result if any(value is not None for value in result.values()) else None


def _validate_references(db: Database, values: Mapping[str, Any], fields: set[str] | None = None) -> None:
    for field, (table, record_type) in _REFERENCE_TABLES.items():
        if fields is not None and field not in fields:
            continue
        record_id = values.get(field)
        if record_id is not None:
            referenced = list_service.require_active_reference(
                db,
                table,
                str(record_id),
                field=field,
                record_type=record_type,
            )
            if field == "sales_tax_item_id" and referenced.get("type") not in {
                "sales_tax_item",
                "sales_tax_group",
            }:
                raise _validation(field, "must reference a sales-tax item or sales-tax group")
    company = db.conn.execute(
        sa.select(
            schema.company_info.c.use_classes,
            schema.company_info.c.enable_price_levels,
            schema.company_info.c.sales_tax_enabled,
        )
    ).mappings().one()
    guarded = (
        ("default_class_id", "use_classes", "classes are disabled"),
        ("price_level_id", "enable_price_levels", "price levels are disabled"),
        ("sales_tax_code_id", "sales_tax_enabled", "sales tax is disabled"),
        ("sales_tax_item_id", "sales_tax_enabled", "sales tax is disabled"),
    )
    for field, flag, problem in guarded:
        if (fields is None or field in fields) and values.get(field) is not None and not company[flag]:
            raise _validation(field, problem)


def _flat_name_values(db: Database, noun: str, name: str, *, exclude_id: str | None = None) -> dict[str, Any]:
    display, key = normalize_display_name(name)
    definition = get_list_definition(noun)
    assert definition is not None
    list_service.assert_name_available(db, _table(noun), definition, display, exclude_id=exclude_id)
    return {"name": display, "name_key": key}


def _customer_hierarchy_values(
    db: Database,
    *,
    name: str,
    parent_id: str | None,
    record_id: str,
) -> dict[str, Any]:
    display, name_key = normalize_display_name(name)
    parent = None
    if parent_id is not None:
        parent = list_service.require_active_reference(
            db,
            schema.customers,
            parent_id,
            field="parent_id",
            record_type="customer",
        )
    depth = 1 if parent is None else int(parent["depth"]) + 1
    full_name, full_name_key = hierarchy_projection(
        display,
        None if parent is None else str(parent["full_name"]),
        depth=depth,
    )
    definition = get_list_definition("customer")
    assert definition is not None
    list_service.assert_name_available(db, schema.customers, definition, full_name)
    return {
        "name": display,
        "name_key": name_key,
        "parent_id": parent_id,
        "full_name": full_name,
        "full_name_key": full_name_key,
        "depth": depth,
        "path": f"/{record_id}/" if parent is None else f"{parent['path']}{record_id}/",
    }


def _specific_values(
    db: Database,
    noun: str,
    parsed: PartyInput,
    *,
    record_id: str,
    current: Mapping[str, Any] | None = None,
    changed_fields: set[str] | None = None,
) -> dict[str, Any]:
    if noun == "customer":
        assert isinstance(parsed, CustomerInput)
        if current is None:
            hierarchy = _customer_hierarchy_values(
                db,
                name=parsed.name,
                parent_id=parsed.parent_id,
                record_id=record_id,
            )
        else:
            definition = get_list_definition("customer")
            assert definition is not None
            plan = list_service.plan_reparent(
                db,
                schema.customers,
                definition,
                current,
                parent_id=parsed.parent_id,
                name=parsed.name,
            )
            root = plan.updates[0]
            hierarchy = {
                "name": root.name,
                "name_key": root.name_key,
                "parent_id": parsed.parent_id,
                "full_name": root.full_name,
                "full_name_key": root.full_name_key,
                "depth": root.depth,
                "path": root.path,
            }
        is_job = parsed.parent_id is not None
        address_mode = parsed.address_mode or ("inherit" if is_job else "own")
        contact_mode = parsed.contact_mode or ("inherit" if is_job else "own")
        if not is_job and (address_mode != "own" or contact_mode != "own"):
            raise _validation("address_mode", "top-level customers must own addresses and contacts")
        if not is_job and (
            parsed.job_status != "none"
            or any(
                value is not None
                for value in (
                    parsed.job_type_id,
                    parsed.job_start,
                    parsed.job_projected_end,
                    parsed.job_end,
                    parsed.job_description,
                    parsed.job_sales_rep_id,
                )
            )
        ):
            raise _validation("job_status", "job fields require a parent customer or job")
        values = {
            **hierarchy,
            "company_name": parsed.company_name,
            **{field: getattr(parsed, field) for field in _PERSON_FIELDS},
            **_address_storage("billing", parsed.billing_address),
            "terms_id": parsed.terms_id,
            "sales_tax_code_id": parsed.sales_tax_code_id,
            "sales_tax_item_id": parsed.sales_tax_item_id,
            "price_level_id": parsed.price_level_id,
            "customer_type_id": parsed.customer_type_id,
            "sales_rep_id": parsed.sales_rep_id,
            "preferred_payment_method_id": parsed.preferred_payment_method_id,
            "preferred_ship_method_id": parsed.preferred_ship_method_id,
            "resale_number": parsed.resale_number,
            **_money_storage(parsed.credit_limit, db, field="credit_limit"),
            "preferred_delivery_method": (
                parsed.preferred_delivery_method
                if parsed.preferred_delivery_method is not None
                else (None if is_job else "none")
            ),
            "account_number": parsed.account_number,
            "payment_profile_ref": None,
            "payment_brand": None,
            "payment_last4": None,
            "payment_expiry_month": None,
            "payment_expiry_year": None,
            **_address_storage("payment_billing", None),
            "notes": parsed.notes,
            "default_class_id": parsed.default_class_id,
            "job_status": parsed.job_status,
            "job_type_id": parsed.job_type_id,
            "job_start": parsed.job_start,
            "job_projected_end": parsed.job_projected_end,
            "job_end": parsed.job_end,
            "job_description": parsed.job_description,
            "job_sales_rep_id": parsed.job_sales_rep_id,
            "address_mode": address_mode,
            "contact_mode": contact_mode,
        }
        _validate_references(db, values, changed_fields)
        return values

    if noun == "vendor":
        assert isinstance(parsed, VendorInput)
        values = {
            **_flat_name_values(db, noun, parsed.name, exclude_id=None if current is None else record_id),
            "company_name": parsed.company_name,
            **{field: getattr(parsed, field) for field in _PERSON_FIELDS},
            **_address_storage("address", parsed.address),
            "terms_id": parsed.terms_id,
            "vendor_type_id": parsed.vendor_type_id,
            "default_class_id": parsed.default_class_id,
            "billing_rate_level_id": None if current is None else current["billing_rate_level_id"],
            "account_number": parsed.account_number,
            "print_name_on_check_as": parsed.print_name_on_check_as,
            **_money_storage(parsed.credit_limit, db, field="credit_limit"),
            "eligible_1099": parsed.eligible_1099,
            "is_tax_agency": parsed.is_tax_agency,
            "recall_last_transaction": parsed.recall_last_transaction,
            "notes": parsed.notes,
            "tax_id_kind": None if current is None else current["tax_id_kind"],
            "tax_id_last4": None if current is None else current["tax_id_last4"],
            "tax_profile_ref": None if current is None else current["tax_profile_ref"],
        }
        if current is not None and current["is_tax_agency"] and not values["is_tax_agency"]:
            count = _vendor_tax_dependents(db, record_id)
            if count:
                raise BookflowError(
                    "E_ACTIVE_DEPENDENTS",
                    details={"record_type": "sales_tax_item", "record_id": record_id, "count": count},
                )
        _validate_references(db, values, changed_fields)
        return values

    if noun == "employee":
        assert isinstance(parsed, EmployeeInput)
        values = {
            **_flat_name_values(db, noun, parsed.name, exclude_id=None if current is None else record_id),
            **{field: getattr(parsed, field) for field in _PERSON_FIELDS},
            "print_name_on_check_as": parsed.print_name_on_check_as,
            "employment_type": parsed.employment_type,
            **_address_storage("address", parsed.address),
            "phone": parsed.phone,
            "email": parsed.email,
            "hire_date": parsed.hire_date,
            "release_date": parsed.release_date,
            "emergency_contact_name": parsed.emergency_contact_name,
            "emergency_contact_relationship": parsed.emergency_contact_relationship,
            "emergency_contact_phone": parsed.emergency_contact_phone,
            "emergency_contact_email": parsed.emergency_contact_email,
            "default_class_id": parsed.default_class_id,
            "notes": parsed.notes,
            "tax_id_last4": None if current is None else current["tax_id_last4"],
        }
        _validate_references(db, values, changed_fields)
        return values

    assert noun == "other-name" and isinstance(parsed, OtherNameInput)
    values = {
        **_flat_name_values(db, noun, parsed.name, exclude_id=None if current is None else record_id),
        "company_name": parsed.company_name,
        **{field: getattr(parsed, field) for field in _PERSON_FIELDS},
        **_address_storage("address", parsed.address),
        "phone": parsed.phone,
        "email": parsed.email,
        "contact": parsed.contact,
        "account_number": parsed.account_number,
        "default_class_id": parsed.default_class_id,
        "notes": parsed.notes,
        "converted_to_type": None if current is None else current["converted_to_type"],
        "converted_to_id": None if current is None else current["converted_to_id"],
    }
    _validate_references(db, values, changed_fields)
    return values


def _plain_row(row: Mapping[str, Any], table: sa.Table) -> dict[str, Any]:
    return {name: row[name] for name in table.c.keys() if name in row}


def _read_contacts(
    db: Database,
    *,
    owner_id: str,
    owner_column: str,
    contact_table: sa.Table,
    point_table: sa.Table,
) -> tuple[dict[str, Any], ...]:
    contacts = db.conn.execute(
        sa.select(contact_table)
        .where(contact_table.c[owner_column] == owner_id)
        .order_by(contact_table.c.position, contact_table.c.id)
    ).mappings().all()
    result: list[dict[str, Any]] = []
    for raw in contacts:
        contact = dict(raw)
        points = db.conn.execute(
            sa.select(point_table)
            .where(point_table.c.contact_id == contact["id"])
            .order_by(point_table.c.position, point_table.c.id)
        ).mappings().all()
        contact["points"] = tuple(dict(point) for point in points)
        result.append(contact)
    return tuple(result)


def read_party_collections(
    db: Database,
    noun: str,
    record_id: str,
) -> dict[str, tuple[dict[str, Any], ...]]:
    if noun == "customer":
        addresses = db.conn.execute(
            sa.select(schema.customer_addresses)
            .where(schema.customer_addresses.c.customer_id == record_id)
            .order_by(schema.customer_addresses.c.position, schema.customer_addresses.c.id)
        ).mappings().all()
        return {
            "shipping_addresses": tuple(dict(row) for row in addresses),
            "contacts": _read_contacts(
                db,
                owner_id=record_id,
                owner_column="customer_id",
                contact_table=schema.customer_contacts,
                point_table=schema.customer_contact_points,
            ),
        }
    if noun == "vendor":
        expenses = db.conn.execute(
            sa.select(schema.vendor_expense_accounts)
            .where(schema.vendor_expense_accounts.c.vendor_id == record_id)
            .order_by(schema.vendor_expense_accounts.c.position, schema.vendor_expense_accounts.c.id)
        ).mappings().all()
        return {
            "contacts": _read_contacts(
                db,
                owner_id=record_id,
                owner_column="vendor_id",
                contact_table=schema.vendor_contacts,
                point_table=schema.vendor_contact_points,
            ),
            "expense_accounts": tuple(dict(row) for row in expenses),
        }
    return {}


def _child_write(
    table: sa.Table,
    existing: tuple[Mapping[str, Any], ...],
    desired: tuple[Mapping[str, Any], ...],
) -> ChildTableWrite | None:
    before = {str(row["id"]): _plain_row(row, table) for row in existing}
    final = tuple(_plain_row(row, table) for row in desired)
    changed = any(before.get(str(row["id"])) != row for row in final)
    return ChildTableWrite(table.name, final) if changed else None


def _address_submission(values: list[ShippingAddressInput]) -> list[dict[str, Any]]:
    submitted: list[dict[str, Any]] = []
    default_count = 0
    for item in values:
        raw = item.model_dump(mode="python")
        label = raw["label"].strip()
        if not label:
            raise _validation("shipping_addresses.label", "must not be empty")
        raw["label"] = label
        raw["label_key"] = list_service.normalize_lookup_key(label)
        for leaf in _ADDRESS_LEAVES:
            raw[f"address_{leaf}"] = raw.pop(leaf)
        if raw["is_default"]:
            default_count += 1
        submitted.append(raw)
    if default_count > 1:
        raise _validation("shipping_addresses", "at most one active address may be default")
    return submitted


def _contact_input_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        **{field: row.get(field) for field in _CONTACT_FIELDS},
        "points": [
            {
                "id": point["id"],
                "kind": point["kind"],
                "custom_label": point["custom_label"],
                "value": point["value"],
            }
            for point in row.get("points", ())
            if point.get("active", True)
        ],
    }


def _contact_submission(
    parsed: CustomerInput | VendorInput,
    *,
    provided: set[str],
    existing: tuple[Mapping[str, Any], ...],
) -> list[dict[str, Any]] | None:
    contacts_explicit = "contacts" in provided
    shortcut_fields = [field for field in _SHORTCUTS if field in provided]
    if not contacts_explicit and not shortcut_fields:
        return None
    if contacts_explicit:
        submitted = [
            item.model_dump(mode="python", exclude_unset=True)
            for item in (parsed.contacts or [])
        ]
    else:
        submitted = [_contact_input_from_row(row) for row in existing if row.get("active", True)]

    for item in submitted:
        display_was_supplied = "display_name" in item
        display = item.get("display_name")
        if display_was_supplied and (display is None or not display.strip()):
            raise _validation("contacts.display_name", "must not be empty")
        if item.get("id") is None and not display_was_supplied:
            item["display_name"] = (
                parsed.name if item["role"] == "primary" else f"{parsed.name} alternate"
            )

    by_role = {
        item["role"]: item
        for item in submitted
        if item["role"] in {"primary", "alternate"}
    }
    for field in shortcut_fields:
        role, target = _SHORTCUTS[field]
        desired = getattr(parsed, field)
        contact = by_role.get(role)
        if contacts_explicit:
            actual = None if contact is None else contact.get(target)
            if actual != desired:
                raise _validation(
                    field,
                    f"conflicts with contacts.{role}.{target}",
                )
            continue
        if contact is None:
            if desired is None:
                continue
            contact = {
                "role": role,
                "display_name": parsed.name if role == "primary" else f"{parsed.name} alternate",
            }
            submitted.append(contact)
            by_role[role] = contact
        contact[target] = desired
    return submitted


def _plan_contacts(
    db: Database,
    *,
    owner_id: str,
    owner_column: str,
    contact_table: sa.Table,
    point_table: sa.Table,
    existing: tuple[Mapping[str, Any], ...],
    submitted: list[dict[str, Any]],
) -> tuple[tuple[ChildTableWrite, ...], tuple[dict[str, Any], ...]]:
    primary_count = sum(item["role"] == "primary" for item in submitted)
    alternate_count = sum(item["role"] == "alternate" for item in submitted)
    if primary_count > 1 or alternate_count > 1:
        raise _validation("contacts", "primary and alternate roles may occur at most once")

    points_by_contact: dict[str, list[dict[str, Any]] | None] = {}
    contact_rows: list[dict[str, Any]] = []
    for item in submitted:
        raw = dict(item)
        points = raw.pop("points", None)
        retained = raw.get("id")
        if retained is not None:
            points_by_contact[str(retained)] = points
        contact_rows.append(raw)
    existing_plain = tuple(_plain_row(row, contact_table) for row in existing)
    reconciled = list_service.reconcile_children(existing_plain, contact_rows)
    desired_contacts = tuple(
        {**row, owner_column: owner_id}
        for row in reconciled.all_rows
    )

    # New ids are known only after reconciliation; align submitted order to the
    # resulting active contact order and carry each nested point collection.
    for position, row in enumerate(reconciled.active):
        raw_points = submitted[position].get("points")
        points_by_contact[str(row["id"])] = (
            None if raw_points is None else list(raw_points)
        )
        display_name = row.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise _validation("contacts.display_name", "must not be empty")

    existing_by_id = {str(row["id"]): row for row in existing}
    point_writes: list[ChildTableWrite] = []
    desired_points_by_contact: dict[str, tuple[dict[str, Any], ...]] = {}
    for contact in desired_contacts:
        contact_id = str(contact["id"])
        old_points = tuple(
            _plain_row(point, point_table)
            for point in existing_by_id.get(contact_id, {}).get("points", ())
        )
        if contact.get("active", True):
            submitted_points = points_by_contact.get(contact_id)
            if submitted_points is None:
                desired_points = old_points
            else:
                point_rows = [dict(item) for item in submitted_points]
                point_reconciled = list_service.reconcile_children(
                    old_points,
                    point_rows,
                    semantic_key="kind",
                )
                desired_points = tuple(
                    {**point, "contact_id": contact_id}
                    for point in point_reconciled.all_rows
                )
        else:
            desired_points = tuple({**point, "active": False} for point in old_points)
        desired_points_by_contact[contact_id] = desired_points
        write = _child_write(point_table, old_points, desired_points)
        if write is not None:
            point_writes.append(write)

    decorated = tuple(
        {
            **dict(contact),
            "points": desired_points_by_contact.get(str(contact["id"]), ()),
        }
        for contact in desired_contacts
    )
    writes: list[ChildTableWrite] = []
    contact_write = _child_write(contact_table, existing_plain, desired_contacts)
    if contact_write is not None:
        writes.append(contact_write)
    writes.extend(point_writes)
    return tuple(writes), decorated


def plan_party_children(
    db: Database,
    noun: str,
    record_id: str,
    parsed: PartyInput,
    *,
    provided: set[str],
    current: Mapping[str, Any] | None = None,
) -> PartyChildrenPlan:
    before = read_party_collections(db, noun, record_id)
    writes: list[ChildTableWrite] = []
    after: dict[str, tuple[Mapping[str, Any], ...]] = dict(before)
    if noun == "customer":
        assert isinstance(parsed, CustomerInput)
        old_address_mode = None if current is None else current["address_mode"]
        old_contact_mode = None if current is None else current["contact_mode"]
        address_mode = parsed.address_mode or ("inherit" if parsed.parent_id is not None else "own")
        contact_mode = parsed.contact_mode or ("inherit" if parsed.parent_id is not None else "own")
        if old_address_mode == "inherit" and address_mode == "own" and "shipping_addresses" not in provided:
            raise _validation("shipping_addresses", "switching from inherit to own requires the complete collection")
        if old_contact_mode == "inherit" and contact_mode == "own" and "contacts" not in provided:
            raise _validation("contacts", "switching from inherit to own requires the complete collection")
        if "shipping_addresses" in provided:
            if address_mode != "own":
                raise _validation("shipping_addresses", "an inherited job cannot store a replacement collection")
            existing = before.get("shipping_addresses", ())
            reconciled = list_service.reconcile_children(
                existing,
                _address_submission(parsed.shipping_addresses or []),
                semantic_key="label_key",
            )
            desired = tuple({**row, "customer_id": record_id} for row in reconciled.all_rows)
            write = _child_write(schema.customer_addresses, existing, desired)
            if write is not None:
                writes.append(write)
            after["shipping_addresses"] = desired

        shortcut_fields = set(_SHORTCUTS) & provided
        if shortcut_fields and current is not None and current["contact_mode"] == "inherit":
            if contact_mode != "own" or "contacts" not in provided:
                raise _validation(
                    "contacts",
                    "a shortcut on an inherited job requires switching to own and submitting the complete collection",
                )
        submitted = _contact_submission(
            parsed,
            provided=provided,
            existing=before.get("contacts", ()),
        )
        if submitted is not None:
            if contact_mode != "own":
                raise _validation("contacts", "an inherited job cannot store a replacement collection")
            contact_writes, desired = _plan_contacts(
                db,
                owner_id=record_id,
                owner_column="customer_id",
                contact_table=schema.customer_contacts,
                point_table=schema.customer_contact_points,
                existing=before.get("contacts", ()),
                submitted=submitted,
            )
            writes.extend(contact_writes)
            after["contacts"] = desired

    elif noun == "vendor":
        assert isinstance(parsed, VendorInput)
        submitted = _contact_submission(
            parsed,
            provided=provided,
            existing=before.get("contacts", ()),
        )
        if submitted is not None:
            contact_writes, desired = _plan_contacts(
                db,
                owner_id=record_id,
                owner_column="vendor_id",
                contact_table=schema.vendor_contacts,
                point_table=schema.vendor_contact_points,
                existing=before.get("contacts", ()),
                submitted=submitted,
            )
            writes.extend(contact_writes)
            after["contacts"] = desired
        if "expense_accounts" in provided:
            submitted_expenses = [
                item.model_dump(mode="python") for item in (parsed.expense_accounts or [])
            ]
            if len(submitted_expenses) > 3:
                raise _validation("expense_accounts", "accepts at most three active accounts")
            for position, item in enumerate(submitted_expenses):
                account = list_service.require_active_reference(
                    db,
                    schema.accounts,
                    item["account_id"],
                    field=f"expense_accounts.{position}.account_id",
                    record_type="account",
                )
                if account["type"] not in {"expense", "cost_of_goods_sold"}:
                    raise _validation(
                        f"expense_accounts.{position}.account_id",
                        "must reference an expense or cost-of-goods-sold account",
                    )
            existing = before.get("expense_accounts", ())
            reconciled = list_service.reconcile_children(
                existing,
                submitted_expenses,
                semantic_key="account_id",
            )
            desired = tuple({**row, "vendor_id": record_id} for row in reconciled.all_rows)
            write = _child_write(schema.vendor_expense_accounts, existing, desired)
            if write is not None:
                writes.append(write)
            after["expense_accounts"] = desired
    return PartyChildrenPlan(tuple(writes), after)


def _custom_after(
    db: Database,
    noun: str,
    record_id: str,
    plan: custom_fields.OwnerCustomFieldPlan | None,
) -> tuple[dict[str, Any], ...]:
    current = {
        item["definition_id"]: {**dict(item), "_position": position}
        for position, item in enumerate(custom_fields.read_owner_values(
            db,
            record_type=noun.replace("-", "_"),
            record_id=record_id,
        ))
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
                "value": custom_fields.typed_value_from_canonical(
                    definition["kind"], mutation.canonical_text
                ),
                "_position": definition["position"],
            }
    values = sorted(
        current.values(),
        key=lambda item: (item.get("_position", 0), item["name"].casefold(), item["definition_id"]),
    )
    return tuple({key: value for key, value in item.items() if key != "_position"} for item in values)


def _snapshot(
    row: Mapping[str, Any],
    values: tuple[Mapping[str, Any], ...],
    collections: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
) -> dict[str, Any]:
    snapshot_collections = dict(collections or {})
    if "contacts" in snapshot_collections:
        snapshot_collections["contact_points"] = tuple(
            {**dict(point), "contact_id": contact["id"]}
            for contact in snapshot_collections["contacts"]
            for point in contact.get("points", ())
        )
    logical_row = dict(row)
    if "billing_line1" in logical_row:
        logical_row["billing_address"] = _address_output(logical_row, "billing")
        for leaf in _ADDRESS_LEAVES:
            logical_row.pop(f"billing_{leaf}", None)
    if "address_line1" in logical_row:
        logical_row["address"] = _address_output(logical_row, "address")
        for leaf in _ADDRESS_LEAVES:
            logical_row.pop(f"address_{leaf}", None)
    if "credit_limit_minor_units" in logical_row:
        logical_row["credit_limit"] = _money_output(logical_row, "credit_limit")
        logical_row.pop("credit_limit_minor_units", None)
        logical_row.pop("credit_limit_currency", None)
    result = list_service.aggregate_snapshot(logical_row, collections=snapshot_collections)
    for protected in (
        "payment_profile_ref",
        "tax_profile_ref",
        "tax_id_kind",
        "tax_id_last4",
    ):
        result.pop(protected, None)
    for item in values:
        result[f"custom_fields.{item['definition_id']}"] = item["value"]
    return result


def _party_snapshot(
    db: Database,
    noun: str,
    row: Mapping[str, Any],
    values: tuple[Mapping[str, Any], ...],
    collections: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
) -> dict[str, Any]:
    result = _snapshot(row, values, collections)
    if noun in {"customer", "vendor"}:
        link = _active_link(db, noun, str(row["id"]))
        result["counterparty_link"] = (
            None
            if link is None
            else {
                "link_id": link["id"],
                "customer_id": link["customer_id"],
                "vendor_id": link["vendor_id"],
                "active": True,
            }
        )
    return result


def plan_party_create(
    db: Database,
    noun: str,
    payload: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    record_id: str | None = None,
    at: str | None = None,
) -> PartyMutation:
    provided = set(payload)
    parsed = _parse(noun, payload)
    identifier = record_id or new_id()
    timestamp = at or now_iso()
    custom_plan = custom_fields.plan_owner_value_patch(
        db,
        record_type=noun.replace("-", "_"),
        record_id=identifier,
        patch=getattr(parsed, "custom_fields", None),
        creating=True,
    )
    after = {
        **list_service.create_metadata(actor_id, via, record_id=identifier, at=timestamp),
        "active": True,
        "seed_key": None,
        **_specific_values(db, noun, parsed, record_id=identifier),
    }
    children = plan_party_children(
        db,
        noun,
        identifier,
        parsed,
        provided=provided,
    )
    values_after = _custom_after(db, noun, identifier, custom_plan)
    return PartyMutation(
        noun,
        "create",
        None,
        after,
        None,
        _party_snapshot(db, noun, after, values_after, children.collections_after),
        custom_plan,
        values_after,
        children,
    )


def _payload_from_row(noun: str, row: Mapping[str, Any]) -> dict[str, Any]:
    if noun == "customer":
        fields = (
            "name", "parent_id", "company_name", *_PERSON_FIELDS, "terms_id",
            "sales_tax_code_id", "sales_tax_item_id", "price_level_id", "customer_type_id",
            "sales_rep_id", "preferred_payment_method_id", "preferred_ship_method_id",
            "resale_number", "preferred_delivery_method", "account_number", "notes",
            "default_class_id", "job_status", "job_type_id", "job_start", "job_projected_end",
            "job_end", "job_description", "job_sales_rep_id", "address_mode", "contact_mode",
        )
        result = {field: row[field] for field in fields}
        result["billing_address"] = _address_output(row, "billing")
    elif noun == "vendor":
        fields = (
            "name", "company_name", *_PERSON_FIELDS, "terms_id", "vendor_type_id",
            "default_class_id", "account_number", "print_name_on_check_as", "eligible_1099",
            "is_tax_agency", "recall_last_transaction", "notes",
        )
        result = {field: row[field] for field in fields}
        result["address"] = _address_output(row, "address")
    elif noun == "employee":
        fields = (
            "name", *_PERSON_FIELDS, "print_name_on_check_as", "employment_type", "phone",
            "email", "hire_date", "release_date", "emergency_contact_name",
            "emergency_contact_relationship", "emergency_contact_phone",
            "emergency_contact_email", "default_class_id", "notes",
        )
        result = {field: row[field] for field in fields}
        result["address"] = _address_output(row, "address")
    else:
        fields = (
            "name", "company_name", *_PERSON_FIELDS, "phone", "email", "contact",
            "account_number", "default_class_id", "notes",
        )
        result = {field: row[field] for field in fields}
        result["address"] = _address_output(row, "address")
    if "credit_limit_minor_units" in row and row["credit_limit_minor_units"] is not None:
        result["credit_limit"] = Money(
            int(row["credit_limit_minor_units"]), str(row["credit_limit_currency"])
        ).to_dict()
    elif noun in {"customer", "vendor"}:
        result["credit_limit"] = None
    return result


def plan_party_update(
    db: Database,
    noun: str,
    record_id: str,
    changes: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> PartyUpdatePlan:
    table = _table(noun)
    current = _row(db, table, record_id)
    payload = _payload_from_row(noun, current)
    payload.update({key: value for key, value in changes.items() if key != "custom_fields"})
    parsed = _parse(noun, payload)
    custom_patch = changes.get("custom_fields") if "custom_fields" in changes else None
    custom_plan = (
        custom_fields.plan_owner_value_patch(
            db,
            record_type=noun.replace("-", "_"),
            record_id=record_id,
            patch=custom_patch,
            creating=False,
        )
        if "custom_fields" in changes
        else None
    )
    specific = _specific_values(
        db,
        noun,
        parsed,
        record_id=record_id,
        current=current,
        changed_fields=set(changes),
    )
    children = plan_party_children(
        db,
        noun,
        record_id,
        parsed,
        provided=set(changes),
        current=current,
    )
    changed_storage = {key: value for key, value in specific.items() if current.get(key) != value}
    values_before = _custom_after(db, noun, record_id, None)
    values_after = _custom_after(db, noun, record_id, custom_plan)
    logical_changes: set[str] = set()
    for field in changes:
        if field == "custom_fields":
            continue
        if any(key in changed_storage for key in _storage_keys_for_input(noun, field)):
            logical_changes.add(field)
    if custom_plan is not None:
        logical_changes.update(".".join(path) for path in custom_plan.logical_paths)
    child_tables = {write.table_name for write in children.writes}
    if "customer_addresses" in child_tables:
        logical_changes.add("shipping_addresses")
    if child_tables & {
        "customer_contacts", "customer_contact_points", "vendor_contacts", "vendor_contact_points"
    }:
        logical_changes.add("contacts")
    if "vendor_expense_accounts" in child_tables:
        logical_changes.add("expense_accounts")
    if not changed_storage and not (custom_plan and custom_plan.changed) and not children.changed:
        return PartyUpdatePlan(None, (), (), values_before)

    timestamp = at or now_iso()
    after = {
        **current,
        **changed_storage,
        "version": int(current["version"]) + 1,
        "updated_at": timestamp,
        "updated_by": actor_id,
        "updated_via": via,
    }
    projections: tuple[ProjectionUpdate, ...] = ()
    if noun == "customer" and any(
        key in changed_storage for key in ("name", "parent_id", "full_name", "path")
    ):
        definition = get_list_definition("customer")
        assert definition is not None
        hierarchy_plan = list_service.plan_reparent(
            db,
            schema.customers,
            definition,
            current,
            parent_id=after["parent_id"],
            name=after["name"],
        )
        projections = tuple(
            ProjectionUpdate(
                item.id,
                {
                    "full_name": item.full_name,
                    "full_name_key": item.full_name_key,
                    "depth": item.depth,
                    "path": item.path,
                },
            )
            for item in hierarchy_plan.updates[1:]
        )
    mutation = PartyMutation(
        noun,
        "update",
        current,
        after,
        _party_snapshot(db, noun, current, values_before, read_party_collections(db, noun, record_id)),
        _party_snapshot(db, noun, after, values_after, children.collections_after),
        custom_plan,
        values_after,
        children,
    )
    return PartyUpdatePlan(mutation, projections, tuple(sorted(logical_changes)), values_after)


def _storage_keys_for_input(noun: str, field: str) -> tuple[str, ...]:
    if field == "billing_address":
        return tuple(f"billing_{leaf}" for leaf in _ADDRESS_LEAVES)
    if field == "address":
        return tuple(f"address_{leaf}" for leaf in _ADDRESS_LEAVES)
    if field == "credit_limit":
        return ("credit_limit_minor_units", "credit_limit_currency")
    return (field,)


def persist_party_mutation(db: Database, mutation: PartyMutation) -> None:
    table = _table(mutation.noun)
    if mutation.before is None:
        db.conn.execute(table.insert().values(**dict(mutation.after)))
    else:
        db.conn.execute(
            table.update().where(table.c.id == mutation.after["id"]).values(**dict(mutation.after))
        )
    if mutation.custom_plan is not None:
        custom_fields.apply_owner_value_plan(db, mutation.custom_plan)
    if mutation.children_plan is not None:
        owner_fields = {
            "customer_addresses": "customer_id",
            "customer_contacts": "customer_id",
            "customer_contact_points": "contact_id",
            "vendor_contacts": "vendor_id",
            "vendor_contact_points": "contact_id",
            "vendor_expense_accounts": "vendor_id",
        }
        for write in mutation.children_plan.writes:
            table = schema.metadata.tables[write.table_name]
            owner_field = owner_fields[write.table_name]
            owner_ids = {str(row[owner_field]) for row in write.rows}
            if owner_ids:
                # Partial unique indexes make role/default/semantic-slot swaps
                # impossible row-by-row. Retire the aggregate's current rows
                # first, then restore the complete planned state.
                db.conn.execute(
                    table.update()
                    .where(table.c[owner_field].in_(owner_ids), table.c.active.is_(True))
                    .values(active=False)
                )
            for row in write.rows:
                exists = db.conn.execute(
                    sa.select(table.c.id).where(table.c.id == row["id"])
                ).first()
                if exists is None:
                    db.conn.execute(table.insert().values(**dict(row)))
                else:
                    db.conn.execute(
                        table.update().where(table.c.id == row["id"]).values(**dict(row))
                    )


def persist_party_update(db: Database, plan: PartyUpdatePlan) -> None:
    if plan.mutation is None:
        return
    persist_party_mutation(db, plan.mutation)
    table = _table(plan.mutation.noun)
    for projection in plan.projections:
        db.conn.execute(
            table.update().where(table.c.id == projection.record_id).values(**dict(projection.values))
        )


def _vendor_tax_dependents(db: Database, vendor_id: str) -> int:
    return int(
        db.conn.execute(
            sa.select(sa.func.count()).select_from(schema.items).where(
                schema.items.c.active.is_(True),
                schema.items.c.tax_agency_vendor_id == vendor_id,
            )
        ).scalar_one()
    )


def _vendor_item_dependents(
    db: Database,
    vendor_id: str,
) -> tuple[dict[str, Any], ...]:
    """Return active item uses that make a vendor structurally required.

    Vendor expense-account defaults are intentionally soft references, but an
    item's preferred-vendor projection and its active vendor profiles describe
    the item's complete purchase profile.  Inactive item owners never block a
    vendor lifecycle change.
    """
    preferred = int(
        db.conn.execute(
            sa.select(sa.func.count()).select_from(schema.items).where(
                schema.items.c.active.is_(True),
                schema.items.c.preferred_vendor_id == vendor_id,
            )
        ).scalar_one()
    )
    profile_owner = schema.items.alias("vendor_profile_owner")
    profiles = int(
        db.conn.execute(
            sa.select(sa.func.count())
            .select_from(
                schema.item_vendor_profiles.join(
                    profile_owner,
                    profile_owner.c.id == schema.item_vendor_profiles.c.item_id,
                )
            )
            .where(
                schema.item_vendor_profiles.c.vendor_id == vendor_id,
                schema.item_vendor_profiles.c.active.is_(True),
                profile_owner.c.active.is_(True),
            )
        ).scalar_one()
    )
    dependents: list[dict[str, Any]] = []
    if preferred:
        dependents.append({"record_type": "item_preferred_vendor", "count": preferred})
    if profiles:
        dependents.append({"record_type": "item_vendor_profile", "count": profiles})
    return tuple(dependents)


def _active_link(db: Database, noun: str, record_id: str) -> dict[str, Any] | None:
    field = "customer_id" if noun == "customer" else "vendor_id"
    row = db.conn.execute(
        sa.select(schema.customer_vendor_links).where(
            schema.customer_vendor_links.c[field] == record_id,
            schema.customer_vendor_links.c.active.is_(True),
        )
    ).mappings().first()
    return None if row is None else dict(row)


def plan_party_active_change(
    db: Database,
    noun: str,
    record_id: str,
    active: bool,
    *,
    actor_id: str,
    via: str,
    cascade: bool = False,
    at: str | None = None,
) -> PartyActivePlan:
    table = _table(noun)
    requested = _row(db, table, record_id)
    if noun == "other-name" and active and requested.get("converted_to_id") is not None:
        raise BookflowError(
            "E_RECORD_IN_USE",
            details={
                "record_type": "other_name",
                "record_id": record_id,
                "problem": "a converted source can be restored only by undoing its conversion",
            },
        )
    if bool(requested["active"]) is active:
        # No-op lifecycle calls still check current structural invariants.
        list_service.plan_activation(db, table, requested) if active else list_service.plan_deactivation(
            db, table, requested, cascade=cascade
        )
        return PartyActivePlan(noun, record_id, active, ())
    if noun == "vendor" and not active:
        count = _vendor_tax_dependents(db, record_id)
        if count:
            raise BookflowError(
                "E_ACTIVE_DEPENDENTS",
                details={"record_type": "sales_tax_item", "record_id": record_id, "count": count},
            )
        dependents = _vendor_item_dependents(db, record_id)
        if dependents:
            raise BookflowError(
                "E_RECORD_IN_USE",
                details={"record_id": record_id, "dependents": list(dependents)},
            )
    rows = (
        list_service.plan_activation(db, table, requested)
        if active
        else list_service.plan_deactivation(db, table, requested, cascade=cascade)
    )
    if not active and noun in {"customer", "vendor"}:
        for row in rows:
            link = _active_link(db, noun, str(row["id"]))
            if link is not None:
                raise BookflowError(
                    "E_RECORD_IN_USE",
                    details={
                        "record_id": row["id"],
                        "dependents": [{"record_type": "customer_vendor_link", "count": 1}],
                    },
                )
    timestamp = at or now_iso()
    mutations: list[PartyMutation] = []
    for row in rows:
        values = _custom_after(db, noun, str(row["id"]), None)
        collections = read_party_collections(db, noun, str(row["id"]))
        after = {
            **row,
            "active": active,
            "version": int(row["version"]) + 1,
            "updated_at": timestamp,
            "updated_by": actor_id,
            "updated_via": via,
        }
        mutations.append(
            PartyMutation(
                noun,
                "activate" if active else "deactivate",
                row,
                after,
                _party_snapshot(db, noun, row, values, collections),
                _party_snapshot(db, noun, after, values, collections),
                None,
                values,
            )
        )
    return PartyActivePlan(noun, record_id, active, tuple(mutations))


def persist_party_active_change(db: Database, plan: PartyActivePlan) -> None:
    for mutation in plan.mutations:
        persist_party_mutation(db, mutation)


def _versioned_after(
    row: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    at: str,
    **changes: Any,
) -> dict[str, Any]:
    return {
        **dict(row),
        **changes,
        "version": int(row["version"]) + 1,
        "updated_at": at,
        "updated_by": actor_id,
        "updated_via": via,
    }


def _principal_name(db: Database, principal_id: str | None) -> str | None:
    if principal_id is None:
        return None
    return db.conn.execute(
        sa.select(schema.principals.c.display_name).where(
            schema.principals.c.user_id == principal_id
        )
    ).scalar_one_or_none()


def _version_conflict_detail(
    db: Database,
    record_type: str,
    row: Mapping[str, Any],
    expected: int,
) -> dict[str, Any]:
    current = int(row["version"])
    writer = current_writer(db, record_type, str(row["id"]), dict(row))
    detail: dict[str, Any] = {
        "record_type": record_type,
        "record_id": row["id"],
        "expected_version": expected,
        "current_version": current,
        "updated_by": None if writer is None else writer.updated_by,
        "updated_by_name": None if writer is None else _principal_name(db, writer.updated_by),
        "updated_on_behalf_of": None if writer is None else writer.on_behalf_of,
        "updated_on_behalf_of_name": (
            None if writer is None else _principal_name(db, writer.on_behalf_of)
        ),
        "updated_via": None if writer is None else writer.updated_via,
        "seconds_since_update": (
            None
            if row.get("updated_at") is None
            else round((clock.now() - clock.parse_iso(str(row["updated_at"]))).total_seconds(), 1)
        ),
    }
    if expected >= current:
        detail["changed_fields"] = []
        return detail
    entries = history_from_entries(
        db,
        record_type,
        str(row["id"]),
        expected,
        audit.decode_snapshot,
    )
    covered = {entry.version_after for entry in entries}
    missing = [version for version in range(expected + 1, current + 1) if version not in covered]
    unknown = [entry.version_after for entry in entries if entry.changed_columns is None]
    if missing or unknown:
        detail["changed_fields"] = []
        detail["unknown_versions"] = missing or unknown
    else:
        detail["changed_fields"] = sorted(
            {
                field
                for entry in entries
                for field in (entry.changed_columns or ())
            }
        )
    return detail


def _require_versions(
    db: Database,
    checks: tuple[tuple[str, Mapping[str, Any], int], ...],
    *,
    additional_conflicts: tuple[dict[str, Any], ...] = (),
) -> None:
    conflicts = [
        _version_conflict_detail(db, record_type, row, expected)
        for record_type, row, expected in checks
        if int(row["version"]) != expected
    ]
    conflicts.extend(additional_conflicts)
    if conflicts:
        raise BookflowError("E_VERSION_CONFLICT", details={"records": conflicts})


def _endpoint_snapshot(
    db: Database,
    noun: Literal["customer", "vendor"],
    row: Mapping[str, Any],
    link: Mapping[str, Any] | None,
) -> dict[str, Any]:
    custom = _custom_after(db, noun, str(row["id"]), None)
    snapshot = _snapshot(
        row,
        custom,
        read_party_collections(db, noun, str(row["id"])),
    )
    if link is None or not link.get("active", True):
        snapshot["counterparty_link"] = None
    else:
        snapshot["counterparty_link"] = {
            "link_id": link["id"],
            "customer_id": link["customer_id"],
            "vendor_id": link["vendor_id"],
            "active": bool(link["active"]),
        }
    return snapshot


def plan_customer_vendor_link(
    db: Database,
    *,
    customer_selector: str,
    vendor_selector: str,
    expected_customer_version: int,
    expected_vendor_version: int,
    expected_link_version: int | None,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> CustomerVendorLinkPlan:
    customer = resolve_party(db, "customer", customer_selector)
    vendor = resolve_party(db, "vendor", vendor_selector)
    prior = db.conn.execute(
        sa.select(schema.customer_vendor_links).where(
            schema.customer_vendor_links.c.customer_id == customer["id"],
            schema.customer_vendor_links.c.vendor_id == vendor["id"],
        )
    ).mappings().first()
    link_before = None if prior is None else dict(prior)
    checks: list[tuple[str, Mapping[str, Any], int]] = [
        ("customer", customer, expected_customer_version),
        ("vendor", vendor, expected_vendor_version),
    ]
    missing_link_conflicts: tuple[dict[str, Any], ...] = ()
    if link_before is not None and expected_link_version is not None:
        checks.append(("customer_vendor_link", link_before, expected_link_version))
    elif link_before is None and expected_link_version is not None:
        missing_link_conflicts = (
            {
                "record_type": "customer_vendor_link",
                "record_id": None,
                "expected_version": expected_link_version,
                "current_version": None,
                "updated_by": None,
                "updated_by_name": None,
                "updated_on_behalf_of": None,
                "updated_on_behalf_of_name": None,
                "updated_via": None,
                "seconds_since_update": None,
                "changed_fields": [],
            },
        )
    _require_versions(
        db,
        tuple(checks),
        additional_conflicts=missing_link_conflicts,
    )
    if link_before is not None and expected_link_version is None:
        raise _validation("expected_link_version", "is required when reactivating an existing link")
    for noun, row in (("customer", customer), ("vendor", vendor)):
        if not row["active"]:
            raise BookflowError(
                "E_INACTIVE_REFERENCE",
                details={"record_type": noun, "record_id": row["id"]},
            )
    active_customer = _active_link(db, "customer", str(customer["id"]))
    active_vendor = _active_link(db, "vendor", str(vendor["id"]))
    if active_customer is not None or active_vendor is not None:
        blockers = sorted(
            {str(link["id"]) for link in (active_customer, active_vendor) if link is not None}
        )
        raise BookflowError(
            "E_RECORD_IN_USE",
            details={"record_type": "customer_vendor_link", "record_ids": blockers},
        )
    timestamp = at or now_iso()
    if link_before is None:
        link_after = {
            **list_service.create_metadata(actor_id, via, at=timestamp),
            "customer_id": customer["id"],
            "vendor_id": vendor["id"],
            "active": True,
        }
    else:
        link_after = _versioned_after(
            link_before,
            actor_id=actor_id,
            via=via,
            at=timestamp,
            active=True,
        )
    customer_after = _versioned_after(customer, actor_id=actor_id, via=via, at=timestamp)
    vendor_after = _versioned_after(vendor, actor_id=actor_id, via=via, at=timestamp)
    return CustomerVendorLinkPlan(
        "link",
        customer,
        customer_after,
        vendor,
        vendor_after,
        link_before,
        link_after,
        _endpoint_snapshot(db, "customer", customer, None),
        _endpoint_snapshot(db, "customer", customer_after, link_after),
        _endpoint_snapshot(db, "vendor", vendor, None),
        _endpoint_snapshot(db, "vendor", vendor_after, link_after),
    )


def plan_customer_vendor_unlink(
    db: Database,
    *,
    customer_selector: str,
    expected_customer_version: int,
    expected_vendor_version: int,
    expected_link_version: int,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> CustomerVendorLinkPlan:
    customer = resolve_party(db, "customer", customer_selector)
    link = _active_link(db, "customer", str(customer["id"]))
    if link is None:
        raise BookflowError(
            "E_RECORD_NOT_FOUND",
            details={"record_type": "customer_vendor_link", "selector": customer_selector, "suggestions": []},
        )
    vendor = _row(db, schema.vendors, str(link["vendor_id"]))
    _require_versions(
        db,
        (
            ("customer", customer, expected_customer_version),
            ("vendor", vendor, expected_vendor_version),
            ("customer_vendor_link", link, expected_link_version),
        )
    )
    timestamp = at or now_iso()
    customer_after = _versioned_after(customer, actor_id=actor_id, via=via, at=timestamp)
    vendor_after = _versioned_after(vendor, actor_id=actor_id, via=via, at=timestamp)
    link_after = _versioned_after(
        link,
        actor_id=actor_id,
        via=via,
        at=timestamp,
        active=False,
    )
    return CustomerVendorLinkPlan(
        "unlink",
        customer,
        customer_after,
        vendor,
        vendor_after,
        link,
        link_after,
        _endpoint_snapshot(db, "customer", customer, link),
        _endpoint_snapshot(db, "customer", customer_after, None),
        _endpoint_snapshot(db, "vendor", vendor, link),
        _endpoint_snapshot(db, "vendor", vendor_after, None),
    )


def persist_customer_vendor_link(db: Database, plan: CustomerVendorLinkPlan) -> None:
    db.conn.execute(
        schema.customers.update().where(schema.customers.c.id == plan.customer_after["id"]).values(
            **dict(plan.customer_after)
        )
    )
    db.conn.execute(
        schema.vendors.update().where(schema.vendors.c.id == plan.vendor_after["id"]).values(
            **dict(plan.vendor_after)
        )
    )
    if plan.link_before is None:
        db.conn.execute(schema.customer_vendor_links.insert().values(**dict(plan.link_after)))
    else:
        db.conn.execute(
            schema.customer_vendor_links.update()
            .where(schema.customer_vendor_links.c.id == plan.link_after["id"])
            .values(**dict(plan.link_after))
        )


def _conversion_custom_values(
    db: Database,
    source_id: str,
    target_type: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    values = custom_fields.read_owner_values(
        db,
        record_type="other_name",
        record_id=source_id,
    )
    applicable = set(
        db.conn.execute(
            sa.select(schema.custom_field_scopes.c.definition_id).where(
                schema.custom_field_scopes.c.record_type == target_type,
                schema.custom_field_scopes.c.active.is_(True),
                schema.custom_field_scopes.c.definition_active.is_(True),
            )
        ).scalars()
    )
    mapped = {
        item["definition_id"]: item["value"]
        for item in values
        if item["definition_id"] in applicable
    }
    retained = tuple(
        item["definition_id"]
        for item in values
        if item["definition_id"] not in applicable
    )
    return mapped, retained


def plan_other_name_conversion(
    db: Database,
    *,
    selector: str,
    target_type: Literal["customer", "vendor", "employee"],
    expected_version: int,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> OtherNameConversionPlan:
    source = resolve_party(db, "other-name", selector)
    _require_versions(db, (("other_name", source, expected_version),))
    if not source["active"] or source["converted_to_id"] is not None:
        raise BookflowError(
            "E_RECORD_IN_USE",
            details={"record_type": "other_name", "record_id": source["id"], "problem": "already converted or inactive"},
        )
    address = _address_output(source, "address")
    mapped_custom, retained_custom = _conversion_custom_values(
        db,
        str(source["id"]),
        target_type.replace("-", "_"),
    )
    common = {
        "name": source["name"],
        **{field: source[field] for field in _PERSON_FIELDS},
        "default_class_id": source["default_class_id"],
        "notes": source["notes"],
        "custom_fields": mapped_custom,
    }
    contact_name = source["contact"] or " ".join(
        value for value in (source["first_name"], source["last_name"]) if value
    ) or source["name"]
    if target_type == "customer":
        payload = {
            **common,
            "company_name": source["company_name"],
            "billing_address": address,
            "account_number": source["account_number"],
            "contacts": [
                {
                    "role": "primary",
                    "display_name": contact_name,
                    "work_phone": source["phone"],
                    "primary_email": source["email"],
                }
            ] if any((source["contact"], source["phone"], source["email"])) else [],
        }
    elif target_type == "vendor":
        payload = {
            **common,
            "company_name": source["company_name"],
            "address": address,
            "account_number": source["account_number"],
            "contacts": [
                {
                    "role": "primary",
                    "display_name": contact_name,
                    "work_phone": source["phone"],
                    "primary_email": source["email"],
                }
            ] if any((source["contact"], source["phone"], source["email"])) else [],
        }
    else:
        payload = {
            **common,
            "address": address,
            "phone": source["phone"],
            "email": source["email"],
        }
    timestamp = at or now_iso()
    target = plan_party_create(
        db,
        target_type,
        payload,
        actor_id=actor_id,
        via=via,
        at=timestamp,
    )
    source_custom = _custom_after(db, "other-name", str(source["id"]), None)
    source_after = _versioned_after(
        source,
        actor_id=actor_id,
        via=via,
        at=timestamp,
        active=False,
        converted_to_type=target_type,
        converted_to_id=target.after["id"],
    )
    source_collections = read_party_collections(db, "other-name", str(source["id"]))
    source_values = {
        "name": source["name"],
        "company_name": source["company_name"],
        **{field: source[field] for field in _PERSON_FIELDS},
        "address": address,
        "phone": source["phone"],
        "email": source["email"],
        "contact": source["contact"],
        "account_number": source["account_number"],
        "default_class_id": source["default_class_id"],
        "notes": source["notes"],
    }
    supported = {
        "customer": set(source_values),
        "vendor": set(source_values),
        "employee": set(source_values) - {"company_name", "contact", "account_number"},
    }[target_type]
    present = {
        field
        for field, value in source_values.items()
        if value not in (None, {}, [], "")
    }
    mapped = tuple(field for field in source_values if field in present & supported)
    mapped = (*mapped, *(f"custom_fields.{identifier}" for identifier in sorted(mapped_custom)))
    retained = (
        *(field for field in source_values if field in present - supported),
        *(f"custom_fields.{identifier}" for identifier in sorted(retained_custom)),
    )
    return OtherNameConversionPlan(
        source,
        source_after,
        _snapshot(source, source_custom, source_collections),
        _snapshot(source_after, source_custom, source_collections),
        target,
        mapped,
        retained,
    )


def persist_other_name_conversion(db: Database, plan: OtherNameConversionPlan) -> None:
    persist_party_mutation(db, plan.target)
    db.conn.execute(
        schema.other_names.update().where(schema.other_names.c.id == plan.source_after["id"]).values(
            **dict(plan.source_after)
        )
    )


def _reference_label(db: Database, table: sa.Table, record_id: str | None) -> str | None:
    if record_id is None:
        return None
    label = "full_name" if "full_name" in table.c else "code" if "code" in table.c else "name"
    return db.conn.execute(sa.select(table.c[label]).where(table.c.id == record_id)).scalar_one_or_none()


def _customer_vendor_links(
    db: Database,
    noun: Literal["customer", "vendor"],
    record_id: str,
) -> list[dict[str, Any]]:
    """Return deterministic public relationship state, including inactive pairs.

    Keeping former pairs visible is what lets a caller supply the exact link
    version when it intentionally reactivates a relationship.
    """

    endpoint = (
        schema.customer_vendor_links.c.customer_id
        if noun == "customer"
        else schema.customer_vendor_links.c.vendor_id
    )
    rows = db.conn.execute(
        sa.select(
            schema.customer_vendor_links.c.id,
            schema.customer_vendor_links.c.version,
            schema.customer_vendor_links.c.customer_id,
            schema.customer_vendor_links.c.vendor_id,
            schema.customer_vendor_links.c.active,
        )
        .where(endpoint == record_id)
        .order_by(schema.customer_vendor_links.c.active.desc(), schema.customer_vendor_links.c.id)
    ).mappings().all()
    return [
        {
            "id": str(row["id"]),
            "version": int(row["version"]),
            "customer_id": str(row["customer_id"]),
            "vendor_id": str(row["vendor_id"]),
            "active": bool(row["active"]),
        }
        for row in rows
    ]


def _vendor_item_profiles(db: Database, vendor_id: str) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        sa.select(schema.item_vendor_profiles, schema.items.c.full_name.label("item_name"))
        .join(schema.items, schema.items.c.id == schema.item_vendor_profiles.c.item_id)
        .where(
            schema.item_vendor_profiles.c.vendor_id == vendor_id,
            schema.item_vendor_profiles.c.active.is_(True),
            schema.items.c.active.is_(True),
        )
        .order_by(schema.items.c.full_name_key, schema.item_vendor_profiles.c.position,
                  schema.item_vendor_profiles.c.id)
    ).mappings().all()
    return [
        {
            "id": row["id"],
            "position": int(row["position"]),
            "active": True,
            "item_id": row["item_id"],
            "item_name": row["item_name"],
            "vendor_id": row["vendor_id"],
            "preferred_rank": int(row["preferred_rank"]),
            "vendor_item_name": row["vendor_item_name"],
            "purchase_cost": _money_output(row, "purchase_cost"),
            "minimum_quantity": (
                None
                if row["minimum_quantity_microunits"] is None
                else format_quantity_micro_units(row["minimum_quantity_microunits"])
            ),
            "lead_time_days": row["lead_time_days"],
            "manufacturer_part_number": row["manufacturer_part_number"],
            "availability_notes": row["availability_notes"],
        }
        for row in rows
    ]


def _money_output(row: Mapping[str, Any], field: str) -> dict[str, Any] | None:
    units = row.get(f"{field}_minor_units")
    currency = row.get(f"{field}_currency")
    return None if units is None else Money(int(units), str(currency)).to_dict()


def _effective_customer_value(
    row: Mapping[str, Any], ancestors: tuple[Mapping[str, Any], ...], field: str
) -> tuple[Any, str | None]:
    for candidate in (row, *ancestors):
        value = candidate.get(field)
        if value is not None:
            return value, str(candidate["id"])
    return None, None


def _effective_customer_address(
    row: Mapping[str, Any], ancestors: tuple[Mapping[str, Any], ...]
) -> tuple[dict[str, Any] | None, str | None]:
    for candidate in (row, *ancestors):
        address = _address_output(candidate, "billing")
        if address is not None:
            return address, str(candidate["id"])
    return None, None


def _public_shipping_addresses(
    rows: tuple[Mapping[str, Any], ...],
) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "label": row["label"],
            "is_default": bool(row["is_default"]),
            **{leaf: row.get(f"address_{leaf}") for leaf in _ADDRESS_LEAVES},
        }
        for row in rows
        if row.get("active", True)
    ]


def _public_contacts(rows: tuple[Mapping[str, Any], ...]) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            **{field: row.get(field) for field in _CONTACT_FIELDS},
            "points": [
                {
                    "id": point["id"],
                    "kind": point["kind"],
                    "custom_label": point.get("custom_label"),
                    "value": point["value"],
                }
                for point in row.get("points", ())
                if point.get("active", True)
            ],
        }
        for row in rows
        if row.get("active", True)
    ]


def _contact_shortcuts(contacts: list[dict[str, Any]]) -> dict[str, Any]:
    roles = {
        role: next((item for item in contacts if item["role"] == role), None)
        for role in ("primary", "alternate")
    }
    fallback = roles["primary"] or roles["alternate"] or (contacts[0] if contacts else None)
    result: dict[str, Any] = {"primary_contact": None if fallback is None else fallback.get("display_name")}
    for output, (role, field) in _SHORTCUTS.items():
        contact = roles[role]
        result[output] = None if contact is None else contact.get(field)
    return result


def _effective_collection(
    db: Database,
    row: Mapping[str, Any],
    ancestors: tuple[Mapping[str, Any], ...],
    *,
    mode_field: str,
    collection: str,
    root_collections: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
) -> tuple[tuple[Mapping[str, Any], ...], str | None]:
    for candidate in (row, *ancestors):
        if candidate.get(mode_field) == "own":
            collections = (
                root_collections
                if candidate["id"] == row["id"] and root_collections is not None
                else read_party_collections(db, "customer", str(candidate["id"]))
            )
            return collections.get(collection, ()), str(candidate["id"])
    return (), None


def _employee_requirements(db: Database) -> list[list[str]]:
    raw = db.conn.execute(
        sa.select(schema.company_info.c.required_employee_profile_fields)
    ).scalar_one()
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        value = []
    return value if isinstance(value, list) else []


def _populated(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _employee_completeness(
    db: Database,
    values: Mapping[str, Any],
    custom_values: tuple[Mapping[str, Any], ...],
) -> tuple[bool, list[list[str]]]:
    custom_map = {item["definition_id"]: item["value"] for item in custom_values}
    missing: list[list[str]] = []
    for raw_group in _employee_requirements(db):
        if not isinstance(raw_group, list):
            continue
        satisfied = False
        for path in raw_group:
            if not isinstance(path, str):
                continue
            if path.startswith("custom_fields."):
                candidate = custom_map.get(path.split(".", 1)[1])
            elif path.startswith("address."):
                candidate = (values.get("address") or {}).get(path.split(".", 1)[1])
            else:
                candidate = values.get(path)
            if _populated(candidate):
                satisfied = True
                break
        if not satisfied:
            missing.append(list(raw_group))
    return not missing, missing


def project_party_record(
    db: Database,
    noun: str,
    row: Mapping[str, Any],
    *,
    custom_values: tuple[Mapping[str, Any], ...] | None = None,
    collections: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
    reveal_tax_suffix: bool = False,
) -> dict[str, Any]:
    """Return the complete scalar public projection for one party record."""
    values = dict(row)
    custom = (
        tuple(
            custom_fields.read_owner_values(
                db,
                record_type=noun.replace("-", "_"),
                record_id=str(row["id"]),
            )
        )
        if custom_values is None
        else custom_values
    )
    common = {
        key: values[key]
        for key in (
            "id", "version", "created_at", "created_by", "created_via", "updated_at",
            "updated_by", "updated_via", "active", "seed_key",
        )
    }
    if noun == "customer":
        from bookflow.company.customer_balances import own_balance, family_balance
        net = Money(own_balance(db, str(values["id"])), _home_currency(db)).to_dict()
        vendor_links = _customer_vendor_links(db, "customer", str(values["id"]))
        ancestors = list_service.hierarchy_ancestors(db, schema.customers, row)
        stored_collections = collections or read_party_collections(db, "customer", str(values["id"]))
        effective_addresses, effective_address_source = _effective_collection(
            db,
            values,
            ancestors,
            mode_field="address_mode",
            collection="shipping_addresses",
            root_collections=stored_collections,
        )
        effective_contacts, effective_contact_source = _effective_collection(
            db,
            values,
            ancestors,
            mode_field="contact_mode",
            collection="contacts",
            root_collections=stored_collections,
        )
        public_contacts = _public_contacts(effective_contacts)
        result = {
            **common,
            "name": values["name"],
            "parent_id": values["parent_id"],
            "full_name": values["full_name"],
            "depth": values["depth"],
            "company_name": values["company_name"],
            **{field: values[field] for field in _PERSON_FIELDS},
            "billing_address": _address_output(values, "billing"),
            "resale_number": values["resale_number"],
            "credit_limit": _money_output(values, "credit_limit"),
            "account_number": values["account_number"],
            "preferred_delivery_method": values["preferred_delivery_method"],
            "notes": values["notes"],
            "job_status": values["job_status"],
            "job_start": values["job_start"],
            "job_projected_end": values["job_projected_end"],
            "job_end": values["job_end"],
            "job_description": values["job_description"],
            "address_mode": values["address_mode"],
            "contact_mode": values["contact_mode"],
            "payment_brand": values["payment_brand"],
            "payment_last4": values["payment_last4"],
            "payment_expiry_month": values["payment_expiry_month"],
            "payment_expiry_year": values["payment_expiry_year"],
            "payment_billing_address": _address_output(values, "payment_billing"),
            "linked_vendor_id": next(
                (link["vendor_id"] for link in vendor_links if link["active"]),
                None,
            ),
            "vendor_links": vendor_links,
            "stored_shipping_addresses": _public_shipping_addresses(
                stored_collections.get("shipping_addresses", ())
            ),
            "shipping_addresses": _public_shipping_addresses(effective_addresses),
            "shipping_addresses_source_id": effective_address_source,
            "stored_contacts": _public_contacts(stored_collections.get("contacts", ())),
            "contacts": public_contacts,
            "contacts_source_id": effective_contact_source,
            **_contact_shortcuts(public_contacts),
            "custom_fields": list(custom),
            "balances_available": True,
            "current_balance": net,
            "open_balance": net,
            "family_balance": Money(family_balance(db, str(values["id"])), _home_currency(db)).to_dict(),
            "customer_or_job": "job" if values["parent_id"] is not None else "customer",
        }
        address, address_source = _effective_customer_address(values, ancestors)
        result["effective_billing_address"] = address
        result["billing_address_source_id"] = address_source
        inherited_fields = (
            "terms_id", "sales_tax_code_id", "sales_tax_item_id", "price_level_id",
            "customer_type_id", "preferred_payment_method_id", "preferred_ship_method_id",
            "preferred_delivery_method", "default_class_id",
        )
        for field in inherited_fields:
            effective, source = _effective_customer_value(values, ancestors, field)
            result[field] = values[field]
            result[f"effective_{field}"] = effective
            result[f"{field.removesuffix('_id')}_source_id"] = source
        sales_rep, sales_rep_source = _effective_customer_value(values, ancestors, "job_sales_rep_id")
        if sales_rep is None:
            sales_rep, sales_rep_source = _effective_customer_value(values, ancestors, "sales_rep_id")
        result["sales_rep_id"] = values["sales_rep_id"]
        result["job_sales_rep_id"] = values["job_sales_rep_id"]
        result["effective_sales_rep_id"] = sales_rep
        result["sales_rep_source_id"] = sales_rep_source
        result["customer_type"] = _reference_label(db, schema.customer_types, result["effective_customer_type_id"])
        result["job_type"] = _reference_label(db, schema.job_types, values["job_type_id"])
        result["sales_rep"] = _reference_label(db, schema.sales_reps, sales_rep)
        result["terms"] = _reference_label(db, schema.terms, result["effective_terms_id"])
        result["payment_method"] = _reference_label(db, schema.payment_methods, result["effective_preferred_payment_method_id"])
        return result

    address = _address_output(values, "address")
    base = {
        **common,
        "name": values["name"],
        **{field: values.get(field) for field in _PERSON_FIELDS},
        "address": address,
        "default_class_id": values.get("default_class_id"),
        "default_class": _reference_label(db, schema.classes, values.get("default_class_id")),
        "notes": values.get("notes"),
        "custom_fields": list(custom),
    }
    if noun == "vendor":
        customer_links = _customer_vendor_links(db, "vendor", str(values["id"]))
        party_collections = collections or read_party_collections(db, "vendor", str(values["id"]))
        public_contacts = _public_contacts(party_collections.get("contacts", ()))
        return {
            **base,
            "company_name": values["company_name"],
            "terms_id": values["terms_id"],
            "terms": _reference_label(db, schema.terms, values["terms_id"]),
            "vendor_type_id": values["vendor_type_id"],
            "vendor_type": _reference_label(db, schema.vendor_types, values["vendor_type_id"]),
            "billing_rate_level_id": None,
            "account_number": values["account_number"],
            "print_name_on_check_as": values["print_name_on_check_as"],
            "credit_limit": _money_output(values, "credit_limit"),
            "eligible_1099": bool(values["eligible_1099"]),
            "is_tax_agency": bool(values["is_tax_agency"]),
            "recall_last_transaction": values["recall_last_transaction"],
            "tax_id_kind": values["tax_id_kind"] if reveal_tax_suffix else None,
            "tax_id_last4": values["tax_id_last4"] if reveal_tax_suffix else None,
            "linked_customer_id": next(
                (link["customer_id"] for link in customer_links if link["active"]),
                None,
            ),
            "customer_links": customer_links,
            "item_vendor_profiles": _vendor_item_profiles(db, str(values["id"])),
            "last_purchase_date": None,
            "last_purchase_cost": None,
            "contacts": public_contacts,
            **_contact_shortcuts(public_contacts),
            "expense_accounts": [
                {"id": item["id"], "account_id": item["account_id"]}
                for item in party_collections.get("expense_accounts", ())
                if item.get("active", True)
            ],
            "expense_account_ids": [
                str(item["account_id"])
                for item in party_collections.get("expense_accounts", ())
                if item.get("active", True)
            ],
            "balances_available": False,
            "current_balance": Money(0, _home_currency(db)).to_dict(),
            "open_balance": Money(0, _home_currency(db)).to_dict(),
        }
    if noun == "employee":
        employee = {
            **base,
            "print_name_on_check_as": values["print_name_on_check_as"],
            "employment_type": values["employment_type"],
            "phone": values["phone"],
            "email": values["email"],
            "hire_date": values["hire_date"],
            "release_date": values["release_date"],
            "released": values["release_date"] is not None,
            "emergency_contact_name": values["emergency_contact_name"],
            "emergency_contact_relationship": values["emergency_contact_relationship"],
            "emergency_contact_phone": values["emergency_contact_phone"],
            "emergency_contact_email": values["emergency_contact_email"],
            "tax_id_last4": values["tax_id_last4"] if reveal_tax_suffix else None,
        }
        complete, missing = _employee_completeness(db, employee, custom)
        employee["profile_complete"] = complete
        employee["missing_profile_fields"] = missing
        return employee
    return {
        **base,
        "company_name": values["company_name"],
        "phone": values["phone"],
        "email": values["email"],
        "contact": values["contact"],
        "account_number": values["account_number"],
        "converted_to_type": values["converted_to_type"],
        "converted_to_id": values["converted_to_id"],
        "converted": values["converted_to_id"] is not None,
        "conversion_target": values["converted_to_type"],
    }


__all__ = [
    "AddressInput",
    "CREATE_MODELS",
    "ContactInput",
    "ContactPointInput",
    "CustomerInput",
    "CustomerVendorLinkPlan",
    "EmployeeInput",
    "OtherNameConversionPlan",
    "OtherNameInput",
    "PARTY_NOUNS",
    "PartyActivePlan",
    "PartyMutation",
    "PartyUpdatePlan",
    "TABLES",
    "VendorInput",
    "VendorExpenseAccountInput",
    "persist_customer_vendor_link",
    "persist_other_name_conversion",
    "persist_party_active_change",
    "persist_party_mutation",
    "persist_party_update",
    "plan_party_active_change",
    "plan_party_create",
    "plan_party_update",
    "plan_customer_vendor_link",
    "plan_customer_vendor_unlink",
    "plan_other_name_conversion",
    "project_party_record",
    "read_party_collections",
    "resolve_party",
]
