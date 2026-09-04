"""Routed lifecycle commands for customer, vendor, employee, and other-name."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
import sqlalchemy as sa

from bookflow.commands.list_factory import (
    LifecycleCallbacks,
    LifecycleModels,
    ModelPair,
    register_lifecycle,
)
from bookflow.company import list_service, parties, schema
from bookflow.company.lists import get_list_definition
from bookflow.core import audit
from bookflow.core.context import Context
from bookflow.core.models import ListOutput, WriteOutput
from bookflow.core.registry import REGISTRY, Applied, Plan, Touched, command, loading_target
from bookflow.core.session import Session, localize
from bookflow.core.versioning import check_update, current_writer, history_from_entries
from bookflow.hub import access


class AddressOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class MoneyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str
    currency: str
    minor_units: int


class CustomValueOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    definition_id: str
    name: str
    kind: Literal["text", "number", "date", "bool", "choice"]
    definition_active: bool
    value: Any


class ShippingAddressOutput(AddressOutput):
    id: str
    label: str
    is_default: bool


class ContactPointOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str
    custom_label: str | None = None
    value: str


class ContactOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    role: Literal["primary", "alternate", "additional"]
    display_name: str | None = None
    salutation: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    work_phone: str | None = None
    home_phone: str | None = None
    mobile_phone: str | None = None
    other_phone: str | None = None
    work_fax: str | None = None
    home_fax: str | None = None
    primary_email: str | None = None
    secondary_email: str | None = None
    website: str | None = None
    external_handle: str | None = None
    points: list[ContactPointOutput] = Field(default_factory=list)


class VendorExpenseAccountOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    account_id: str


class VendorItemProfileOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    position: int
    active: bool
    item_id: str
    item_name: str
    vendor_id: str
    preferred_rank: int
    vendor_item_name: str | None = None
    purchase_cost: MoneyOutput | None = None
    minimum_quantity: str | None = None
    lead_time_days: int | None = None
    manufacturer_part_number: str | None = None
    availability_notes: str | None = None


class CustomerVendorLinkStateOutput(BaseModel):
    """Authoritative version state for one customer/vendor relationship."""

    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    customer_id: str
    vendor_id: str
    active: bool


class PartyListInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    query: str | None = None
    include_inactive: bool = False
    filter: list[str] = Field(default_factory=list)
    sort: str | None = None
    direction: Literal["asc", "desc"] = "asc"


_COMMON_FIELDS: dict[str, tuple[object, object]] = {
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
_PERSON_FIELDS: dict[str, tuple[object, object]] = {
    "salutation": (str | None, None),
    "first_name": (str | None, None),
    "middle_name": (str | None, None),
    "last_name": (str | None, None),
    "job_title": (str | None, None),
}
_PARTY_BASE: dict[str, tuple[object, object]] = {
    **_COMMON_FIELDS,
    "name": (str, ...),
    **_PERSON_FIELDS,
    "default_class_id": (str | None, None),
    "default_class": (str | None, None),
    "notes": (str | None, None),
    "custom_fields": (list[CustomValueOutput], Field(default_factory=list)),
}

_CUSTOMER_FIELDS: dict[str, tuple[object, object]] = {
    **_COMMON_FIELDS,
    "name": (str, ...),
    "parent_id": (str | None, None),
    "full_name": (str, ...),
    "depth": (int, ...),
    "customer_or_job": (Literal["customer", "job"], ...),
    "company_name": (str | None, None),
    **_PERSON_FIELDS,
    "billing_address": (AddressOutput | None, None),
    "effective_billing_address": (AddressOutput | None, None),
    "billing_address_source_id": (str | None, None),
    "terms_id": (str | None, None),
    "effective_terms_id": (str | None, None),
    "terms_source_id": (str | None, None),
    "terms": (str | None, None),
    "sales_tax_code_id": (str | None, None),
    "effective_sales_tax_code_id": (str | None, None),
    "sales_tax_code_source_id": (str | None, None),
    "sales_tax_item_id": (str | None, None),
    "effective_sales_tax_item_id": (str | None, None),
    "sales_tax_item_source_id": (str | None, None),
    "price_level_id": (str | None, None),
    "effective_price_level_id": (str | None, None),
    "price_level_source_id": (str | None, None),
    "customer_type_id": (str | None, None),
    "effective_customer_type_id": (str | None, None),
    "customer_type_source_id": (str | None, None),
    "customer_type": (str | None, None),
    "sales_rep_id": (str | None, None),
    "job_sales_rep_id": (str | None, None),
    "effective_sales_rep_id": (str | None, None),
    "sales_rep_source_id": (str | None, None),
    "sales_rep": (str | None, None),
    "preferred_payment_method_id": (str | None, None),
    "effective_preferred_payment_method_id": (str | None, None),
    "preferred_payment_method_source_id": (str | None, None),
    "payment_method": (str | None, None),
    "preferred_ship_method_id": (str | None, None),
    "effective_preferred_ship_method_id": (str | None, None),
    "preferred_ship_method_source_id": (str | None, None),
    "default_class_id": (str | None, None),
    "effective_default_class_id": (str | None, None),
    "default_class_source_id": (str | None, None),
    "job_type_id": (str | None, None),
    "job_type": (str | None, None),
    "resale_number": (str | None, None),
    "credit_limit": (MoneyOutput | None, None),
    "preferred_delivery_method": (Literal["none", "email", "mail"] | None, None),
    "effective_preferred_delivery_method": (Literal["none", "email", "mail"] | None, None),
    "preferred_delivery_method_source_id": (str | None, None),
    "account_number": (str | None, None),
    "payment_brand": (str | None, None),
    "payment_last4": (str | None, None),
    "payment_expiry_month": (int | None, None),
    "payment_expiry_year": (int | None, None),
    "payment_billing_address": (AddressOutput | None, None),
    "linked_vendor_id": (str | None, None),
    "vendor_links": (list[CustomerVendorLinkStateOutput], Field(default_factory=list)),
    "stored_shipping_addresses": (list[ShippingAddressOutput], Field(default_factory=list)),
    "shipping_addresses": (list[ShippingAddressOutput], Field(default_factory=list)),
    "shipping_addresses_source_id": (str | None, None),
    "stored_contacts": (list[ContactOutput], Field(default_factory=list)),
    "contacts": (list[ContactOutput], Field(default_factory=list)),
    "contacts_source_id": (str | None, None),
    "job_status": (Literal["none", "pending", "awarded", "in_progress", "closed", "not_awarded"], ...),
    "job_start": (str | None, None),
    "job_projected_end": (str | None, None),
    "job_end": (str | None, None),
    "job_description": (str | None, None),
    "address_mode": (Literal["inherit", "own"], ...),
    "contact_mode": (Literal["inherit", "own"], ...),
    "contact": (str | None, None),
    "alt_contact": (str | None, None),
    "primary_contact": (str | None, None),
    "phone": (str | None, None),
    "alt_phone": (str | None, None),
    "fax": (str | None, None),
    "email": (str | None, None),
    "cc_email": (str | None, None),
    "website": (str | None, None),
    "notes": (str | None, None),
    "custom_fields": (list[CustomValueOutput], Field(default_factory=list)),
    "balances_available": (bool, False),
    "current_balance": (MoneyOutput, ...),
    "open_balance": (MoneyOutput, ...),
}

_VENDOR_FIELDS: dict[str, tuple[object, object]] = {
    **_PARTY_BASE,
    "company_name": (str | None, None),
    "address": (AddressOutput | None, None),
    "terms_id": (str | None, None),
    "terms": (str | None, None),
    "vendor_type_id": (str | None, None),
    "vendor_type": (str | None, None),
    "billing_rate_level_id": (None, None),
    "account_number": (str | None, None),
    "print_name_on_check_as": (str | None, None),
    "credit_limit": (MoneyOutput | None, None),
    "eligible_1099": (bool, ...),
    "is_tax_agency": (bool, ...),
    "recall_last_transaction": (bool | None, None),
    "tax_id_kind": (Literal["ein", "ssn"] | None, None),
    "tax_id_last4": (str | None, None),
    "linked_customer_id": (str | None, None),
    "customer_links": (list[CustomerVendorLinkStateOutput], Field(default_factory=list)),
    "contacts": (list[ContactOutput], Field(default_factory=list)),
    "contact": (str | None, None),
    "alt_contact": (str | None, None),
    "primary_contact": (str | None, None),
    "phone": (str | None, None),
    "alt_phone": (str | None, None),
    "fax": (str | None, None),
    "email": (str | None, None),
    "cc_email": (str | None, None),
    "website": (str | None, None),
    "expense_account_ids": (list[str], Field(default_factory=list)),
    "expense_accounts": (list[VendorExpenseAccountOutput], Field(default_factory=list)),
    "item_vendor_profiles": (list[VendorItemProfileOutput], Field(default_factory=list)),
    "last_purchase_date": (str | None, None),
    "last_purchase_cost": (MoneyOutput | None, None),
    "balances_available": (bool, False),
    "current_balance": (MoneyOutput, ...),
    "open_balance": (MoneyOutput, ...),
}

_EMPLOYEE_FIELDS: dict[str, tuple[object, object]] = {
    **_PARTY_BASE,
    "print_name_on_check_as": (str | None, None),
    "employment_type": (Literal["full_time", "part_time", "seasonal", "temporary", "other"] | None, None),
    "address": (AddressOutput | None, None),
    "phone": (str | None, None),
    "email": (str | None, None),
    "hire_date": (str | None, None),
    "release_date": (str | None, None),
    "released": (bool, ...),
    "emergency_contact_name": (str | None, None),
    "emergency_contact_relationship": (str | None, None),
    "emergency_contact_phone": (str | None, None),
    "emergency_contact_email": (str | None, None),
    "tax_id_last4": (str | None, None),
    "profile_complete": (bool, ...),
    "missing_profile_fields": (list[list[str]], Field(default_factory=list)),
}

_OTHER_NAME_FIELDS: dict[str, tuple[object, object]] = {
    **_PARTY_BASE,
    "company_name": (str | None, None),
    "address": (AddressOutput | None, None),
    "phone": (str | None, None),
    "email": (str | None, None),
    "contact": (str | None, None),
    "account_number": (str | None, None),
    "converted_to_type": (Literal["customer", "vendor", "employee"] | None, None),
    "converted_to_id": (str | None, None),
    "converted": (bool, ...),
    "conversion_target": (Literal["customer", "vendor", "employee"] | None, None),
}

_PUBLIC_FIELDS = {
    "customer": _CUSTOMER_FIELDS,
    "vendor": _VENDOR_FIELDS,
    "employee": _EMPLOYEE_FIELDS,
    "other-name": _OTHER_NAME_FIELDS,
}


def _class_name(noun: str) -> str:
    return "".join(part.title() for part in noun.split("-"))


def _selector_field(noun: str) -> str:
    return noun.replace("-", "_")


def _record_model(noun: str) -> type[BaseModel]:
    return create_model(
        f"{_class_name(noun)}Output",
        __config__=ConfigDict(extra="forbid", defer_build=True),
        **_PUBLIC_FIELDS[noun],
    )


def _write_model(noun: str, record: type[BaseModel], verb: str) -> type[BaseModel]:
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
    elif verb in {"activate", "deactivate"}:
        additions.update(
            changed=(bool, ...),
            affected_ids=(list[str], Field(default_factory=list)),
        )
    return create_model(f"{_class_name(noun)}{verb.title()}Output", __base__=record, **additions)


def _selector_model(noun: str) -> type[BaseModel]:
    return create_model(
        f"{_class_name(noun)}SelectorInput",
        __config__=ConfigDict(
            extra="forbid",
            strict=True,
            str_strip_whitespace=True,
            defer_build=True,
        ),
        **{
            _selector_field(noun): (
                str,
                Field(description=f"{noun} id or canonical name"),
            )
        },
    )


def _update_model(noun: str, create_input: type[BaseModel]) -> type[BaseModel]:
    fields: dict[str, tuple[object, object]] = {
        _selector_field(noun): (str, Field(description=f"{noun} id or canonical name")),
        "expected_version": (int | None, Field(default=None, ge=1)),
    }
    for name, model_field in create_input.model_fields.items():
        field_data = model_field.asdict()
        attributes = {
            key: value
            for key, value in field_data["attributes"].items()
            if key not in {"default", "default_factory"} and value is not None
        }
        annotation = Annotated[
            field_data["annotation"] | None,
            *field_data["metadata"],
            Field(**attributes),
        ]
        fields[name] = (annotation, None)
    return create_model(
        f"{_class_name(noun)}UpdateInput",
        __config__=ConfigDict(
            extra="forbid",
            strict=True,
            str_strip_whitespace=True,
            defer_build=True,
        ),
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
        __config__=ConfigDict(
            extra="forbid",
            strict=True,
            str_strip_whitespace=True,
            defer_build=True,
        ),
        **fields,
    )


def _can_reveal_tax(s: Session) -> bool:
    if s.is_hub_admin:
        return True
    if s.company_row is None:
        return False
    _kind, role = access.company_role(
        s,
        str(s.company_row["id"]),
        str(s.company_row["organization_id"]),
    )
    return role in {"admin", "owner"}


def _public_values(
    s: Session,
    noun: str,
    row: dict[str, Any],
    *,
    custom_values: tuple[dict[str, Any], ...] | tuple | None = None,
    collections: dict[str, tuple] | None = None,
) -> dict[str, Any]:
    projected = parties.project_party_record(
        s.company,
        noun,
        row,
        custom_values=custom_values,
        collections=collections,
        reveal_tax_suffix=_can_reveal_tax(s),
    )
    projected["created_at"] = localize(s, projected["created_at"])
    projected["updated_at"] = localize(s, projected["updated_at"])
    return projected


def _model_output(model: type[BaseModel], values: dict[str, Any], **extra: Any) -> BaseModel:
    available = {name: values[name] for name in model.model_fields if name in values}
    return model.model_validate({**available, **extra})


def _version_meta(
    s: Session,
    noun: str,
    row: dict[str, Any],
    changes: set[str],
    expected_version: int | None,
):
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


def _concat(*columns: sa.ColumnElement[Any]) -> sa.ColumnElement[Any]:
    expression: sa.ColumnElement[Any] = sa.literal("")
    for column in columns:
        expression = expression + sa.literal(" ") + sa.func.coalesce(column, "")
    return sa.func.lower(expression)


def _reference_name(table: sa.Table, record_id: sa.ColumnElement[Any]) -> sa.ColumnElement[Any]:
    label = table.c.full_name if "full_name" in table.c else table.c.name
    return (
        sa.select(sa.func.lower(label))
        .where(table.c.id == record_id)
        .scalar_subquery()
    )


def _custom_search(record_type: str, outer: sa.Table) -> sa.ColumnElement[Any]:
    values = schema.custom_field_values
    definitions = schema.custom_field_defs
    return (
        sa.select(sa.func.group_concat(sa.func.lower(values.c.canonical_text), " "))
        .select_from(values.join(definitions, definitions.c.id == values.c.def_id))
        .where(
            values.c.record_type == record_type,
            values.c.record_id == outer.c.id,
            values.c.active.is_(True),
            definitions.c.active.is_(True),
            definitions.c.kind.in_(("text", "choice")),
        )
        .correlate(outer)
        .scalar_subquery()
    )


def _customer_effective(field: str) -> sa.ColumnElement[Any]:
    outer = schema.customers
    ancestor = schema.customers.alias(f"effective_{field}_ancestor")
    return (
        sa.select(ancestor.c[field])
        .where(
            outer.c.path.like(ancestor.c.path + "%"),
            ancestor.c[field].is_not(None),
        )
        .order_by(ancestor.c.depth.desc(), ancestor.c.id)
        .limit(1)
        .correlate(outer)
        .scalar_subquery()
    )


def _customer_collection_owner(mode_field: str) -> sa.ColumnElement[Any]:
    outer = schema.customers
    ancestor = schema.customers.alias(f"effective_{mode_field}_ancestor")
    return (
        sa.select(ancestor.c.id)
        .where(
            outer.c.path.like(ancestor.c.path + "%"),
            ancestor.c[mode_field] == "own",
        )
        .order_by(ancestor.c.depth.desc(), ancestor.c.id)
        .limit(1)
        .correlate(outer)
        .scalar_subquery()
    )


def _effective_customer_billing() -> sa.ColumnElement[Any]:
    outer = schema.customers
    ancestor = schema.customers.alias("effective_billing_ancestor")
    leaves = tuple(ancestor.c[f"billing_{leaf}"] for leaf in AddressOutput.model_fields)
    return (
        sa.select(_concat(*leaves))
        .where(
            outer.c.path.like(ancestor.c.path + "%"),
            sa.or_(*(leaf.is_not(None) for leaf in leaves)),
        )
        .order_by(ancestor.c.depth.desc(), ancestor.c.id)
        .limit(1)
        .correlate(outer)
        .scalar_subquery()
    )


def _contact_search(
    outer: sa.Table,
    contacts: sa.Table,
    owner_field: str,
    owner_id: sa.ColumnElement[Any],
) -> sa.ColumnElement[Any]:
    fields = tuple(
        contacts.c[field]
        for field in ContactOutput.model_fields
        if field not in {"id", "role", "points"}
    )
    return (
        sa.select(sa.func.group_concat(_concat(*fields), " "))
        .where(contacts.c[owner_field] == owner_id, contacts.c.active.is_(True))
        .correlate(outer)
        .scalar_subquery()
    )


def _contact_points_search(
    outer: sa.Table,
    contacts: sa.Table,
    points: sa.Table,
    owner_field: str,
    owner_id: sa.ColumnElement[Any],
) -> sa.ColumnElement[Any]:
    return (
        sa.select(sa.func.group_concat(_concat(points.c.custom_label, points.c.value), " "))
        .select_from(points.join(contacts, contacts.c.id == points.c.contact_id))
        .where(
            contacts.c[owner_field] == owner_id,
            contacts.c.active.is_(True),
            points.c.active.is_(True),
        )
        .correlate(outer)
        .scalar_subquery()
    )


def _contact_sort(
    outer: sa.Table,
    contacts: sa.Table,
    owner_field: str,
    owner_id: sa.ColumnElement[Any],
    field: str,
    *,
    primary_only: bool = False,
) -> sa.ColumnElement[Any]:
    statement = sa.select(sa.func.lower(contacts.c[field])).where(
        contacts.c[owner_field] == owner_id,
        contacts.c.active.is_(True),
    )
    if primary_only:
        statement = statement.where(contacts.c.role == "primary")
    else:
        statement = statement.order_by(
            sa.case(
                (contacts.c.role == "primary", 0),
                (contacts.c.role == "alternate", 1),
                else_=2,
            ),
            contacts.c.position,
            contacts.c.id,
        )
    return statement.limit(1).correlate(outer).scalar_subquery()


def _customer_expressions() -> tuple[dict[str, sa.ColumnElement], dict[str, sa.ColumnElement], dict[str, sa.ColumnElement]]:
    table = schema.customers
    linked = (
        sa.select(schema.customer_vendor_links.c.vendor_id)
        .where(
            schema.customer_vendor_links.c.customer_id == table.c.id,
            schema.customer_vendor_links.c.active.is_(True),
        )
        .scalar_subquery()
    )
    customer_or_job = sa.case((table.c.parent_id.is_(None), "customer"), else_="job")
    effective_customer_type = _customer_effective("customer_type_id")
    effective_sales_rep = sa.func.coalesce(
        _customer_effective("job_sales_rep_id"),
        _customer_effective("sales_rep_id"),
    )
    contact_owner = _customer_collection_owner("contact_mode")
    address_owner = _customer_collection_owner("address_mode")
    contact_names = _contact_search(
        table, schema.customer_contacts, "customer_id", contact_owner
    )
    contact_points = _contact_points_search(
        table,
        schema.customer_contacts,
        schema.customer_contact_points,
        "customer_id",
        contact_owner,
    )
    shipping = (
        sa.select(
            sa.func.group_concat(
                _concat(
                    schema.customer_addresses.c.label,
                    *(
                        schema.customer_addresses.c[f"address_{leaf}"]
                        for leaf in AddressOutput.model_fields
                    ),
                ),
                " ",
            )
        )
        .where(
            schema.customer_addresses.c.customer_id == address_owner,
            schema.customer_addresses.c.active.is_(True),
        )
        .correlate(table)
        .scalar_subquery()
    )
    search = {
        "effective-contact-names": contact_names,
        "effective-contact-points": contact_points,
        "effective-billing-address": _effective_customer_billing(),
        "effective-shipping-addresses": shipping,
        "customer_type": _reference_name(schema.customer_types, effective_customer_type),
        "job_type": _reference_name(schema.job_types, table.c.job_type_id),
        "sales_rep": _reference_name(schema.sales_reps, effective_sales_rep),
        "$custom-searchable": _custom_search("customer", table),
    }
    filters = {
        "customer_or_job": customer_or_job,
        "linked_vendor_id": linked,
        "customer_type_id": effective_customer_type,
        "sales_rep_id": effective_sales_rep,
        "price_level_id": _customer_effective("price_level_id"),
        "sales_tax_code_id": _customer_effective("sales_tax_code_id"),
        "preferred_payment_method_id": _customer_effective("preferred_payment_method_id"),
    }
    sorts = {
        "primary_contact": _contact_sort(
            table, schema.customer_contacts, "customer_id", contact_owner, "display_name"
        ),
        "phone": _contact_sort(
            table,
            schema.customer_contacts,
            "customer_id",
            contact_owner,
            "work_phone",
            primary_only=True,
        ),
        "current_balance": sa.literal(0),
        "customer_type": _reference_name(schema.customer_types, effective_customer_type),
        "sales_rep": _reference_name(schema.sales_reps, effective_sales_rep),
    }
    return search, filters, sorts


def _vendor_expressions() -> tuple[dict[str, sa.ColumnElement], dict[str, sa.ColumnElement], dict[str, sa.ColumnElement]]:
    table = schema.vendors
    person = _concat(*(table.c[field] for field in _PERSON_FIELDS))
    address = _concat(*(table.c[f"address_{leaf}"] for leaf in AddressOutput.model_fields))
    linked = (
        sa.select(schema.customer_vendor_links.c.customer_id)
        .where(
            schema.customer_vendor_links.c.vendor_id == table.c.id,
            schema.customer_vendor_links.c.active.is_(True),
        )
        .scalar_subquery()
    )
    terms = sa.select(schema.terms.c.name).where(schema.terms.c.id == table.c.terms_id).scalar_subquery()
    vendor_type = sa.select(schema.vendor_types.c.full_name).where(
        schema.vendor_types.c.id == table.c.vendor_type_id
    ).scalar_subquery()
    contacts = _contact_search(table, schema.vendor_contacts, "vendor_id", table.c.id)
    contact_points = _contact_points_search(
        table, schema.vendor_contacts, schema.vendor_contact_points, "vendor_id", table.c.id
    )
    item_profiles = (
        sa.select(
            sa.func.group_concat(
                _concat(
                    schema.items.c.full_name,
                    schema.item_vendor_profiles.c.vendor_item_name,
                    schema.item_vendor_profiles.c.manufacturer_part_number,
                ),
                " ",
            )
        )
        .select_from(
            schema.item_vendor_profiles.join(
                schema.items, schema.items.c.id == schema.item_vendor_profiles.c.item_id
            )
        )
        .where(
            schema.item_vendor_profiles.c.vendor_id == table.c.id,
            schema.item_vendor_profiles.c.active.is_(True),
            schema.items.c.active.is_(True),
        )
        .correlate(table)
        .scalar_subquery()
    )
    return (
        {
            "person_fields": person,
            "contact_fields": _concat(contacts, contact_points),
            "address": address,
            "vendor_type": vendor_type,
            "item_vendor_identifiers": item_profiles,
            "$custom-searchable": _custom_search("vendor", table),
        },
        {"linked_customer_id": linked},
        {
            "primary_contact": _contact_sort(
                table, schema.vendor_contacts, "vendor_id", table.c.id, "display_name"
            ),
            "phone": _contact_sort(
                table,
                schema.vendor_contacts,
                "vendor_id",
                table.c.id,
                "work_phone",
                primary_only=True,
            ),
            "open_balance": sa.literal(0),
            "terms": terms,
            "vendor_type": vendor_type,
        },
    )


def _employee_expressions() -> tuple[dict[str, sa.ColumnElement], dict[str, sa.ColumnElement], dict[str, sa.ColumnElement]]:
    table = schema.employees
    address = _concat(*(table.c[f"address_{leaf}"] for leaf in AddressOutput.model_fields))
    emergency = _concat(
        table.c.emergency_contact_name,
        table.c.emergency_contact_relationship,
        table.c.emergency_contact_phone,
        table.c.emergency_contact_email,
    )
    released = table.c.release_date.is_not(None)
    return (
        {
            "address": address,
            "emergency_contact": emergency,
            "$custom-searchable": _custom_search("employee", table),
        },
        {"released": released, "unreleased": ~released},
        {},
    )


def _other_name_expressions() -> tuple[dict[str, sa.ColumnElement], dict[str, sa.ColumnElement], dict[str, sa.ColumnElement]]:
    table = schema.other_names
    person = _concat(*(table.c[field] for field in _PERSON_FIELDS))
    contact = _concat(table.c.contact, table.c.phone, table.c.email)
    address = _concat(*(table.c[f"address_{leaf}"] for leaf in AddressOutput.model_fields))
    converted = table.c.converted_to_id.is_not(None)
    return (
        {
            "person_fields": person,
            "contact_fields": contact,
            "address": address,
            "$custom-searchable": _custom_search("other_name", table),
        },
        {
            "converted": converted,
            "unconverted": ~converted,
            "conversion_target": table.c.converted_to_type,
        },
        {"conversion_target": table.c.converted_to_type},
    )


def _expressions(noun: str):
    return {
        "customer": _customer_expressions,
        "vendor": _vendor_expressions,
        "employee": _employee_expressions,
        "other-name": _other_name_expressions,
    }[noun]()


def _register_party(noun: str) -> None:
    create_input = parties.CREATE_MODELS[noun]
    record_output = _record_model(noun)
    create_output = _write_model(noun, record_output, "create")
    update_input = _update_model(noun, create_input)
    update_output = _write_model(noun, record_output, "update")
    selector_input = _selector_model(noun)
    activate_input = _active_model(noun, cascade=False)
    deactivate_input = _active_model(noun, cascade=noun == "customer")
    active_output = _write_model(noun, record_output, "activate")
    deactivate_output = _write_model(noun, record_output, "deactivate")
    list_output = ListOutput[record_output]
    selector_field = _selector_field(noun)

    def create_plan(inp: BaseModel, ctx: Context, s: Session) -> Plan:
        mutation = parties.plan_party_create(
            s.company,
            noun,
            inp.model_dump(mode="python", exclude_unset=True),
            actor_id=s.actor.id,
            via=ctx.interface.value,
        )
        values = _public_values(
            s,
            noun,
            dict(mutation.after),
            custom_values=mutation.custom_values_after,
            collections=mutation.children_plan.collections_after if mutation.children_plan else None,
        )
        warnings = (
            ["Closed job has no actual end date."]
            if noun == "customer"
            and values.get("customer_or_job") == "job"
            and values.get("job_status") == "closed"
            and values.get("job_end") is None
            else []
        )
        return Plan(
            preview=_model_output(create_output, values, warnings=warnings),
            data={"mutation": mutation},
        )

    def create_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        mutation = plan.data["mutation"]
        parties.persist_party_mutation(s.company, mutation)
        touched = Touched(
            noun.replace("-", "_"),
            str(mutation.after["id"]),
            "create",
            None,
            1,
            dict(mutation.after_snapshot),
            db="company",
        )
        return Applied(plan.preview, [touched], f"created {noun} {mutation.after['name']}")

    def update_plan(inp: BaseModel, ctx: Context, s: Session) -> Plan:
        row = parties.resolve_party(s.company, noun, getattr(inp, selector_field))
        changes = inp.model_dump(
            mode="python",
            exclude_unset=True,
            exclude={selector_field, "expected_version"},
        )
        update = parties.plan_party_update(
            s.company,
            noun,
            str(row["id"]),
            changes,
            actor_id=s.actor.id,
            via=ctx.interface.value,
        )
        actual = set(update.changed_fields)
        meta = _version_meta(s, noun, row, actual, inp.expected_version)
        after = dict(update.mutation.after) if update.mutation is not None else row
        warnings: list[str] = []
        warning = list_service.blind_write_warning(meta)
        if warning:
            warnings.append(warning)
        if noun == "customer" and after.get("job_status") == "closed" and after.get("job_end") is None:
            warnings.append("Closed job has no actual end date.")
        values = _public_values(
            s,
            noun,
            after,
            custom_values=update.custom_values_after,
            collections=(
                update.mutation.children_plan.collections_after
                if update.mutation is not None and update.mutation.children_plan
                else None
            ),
        )
        return Plan(
            preview=_model_output(
                update_output,
                values,
                warnings=warnings,
                changed_fields=meta.changed_fields,
                merged_over_versions=meta.merged_over_versions,
                affected_descendant_ids=[item.record_id for item in update.projections],
            ),
            data={"update": update},
        )

    def update_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        update = plan.data["update"]
        if update.mutation is None:
            return Applied(plan.preview, [], "no change")
        parties.persist_party_update(s.company, update)
        mutation = update.mutation
        touched = Touched(
            noun.replace("-", "_"),
            str(mutation.after["id"]),
            "update",
            int(mutation.before["version"]),
            int(mutation.after["version"]),
            dict(mutation.after_snapshot),
            dict(mutation.before_snapshot),
            db="company",
        )
        return Applied(plan.preview, [touched], f"updated {noun} {mutation.after['name']}")

    def show_plan(inp: BaseModel, ctx: Context, s: Session) -> Plan:
        row = parties.resolve_party(s.company, noun, getattr(inp, selector_field))
        return Plan(preview=_model_output(record_output, _public_values(s, noun, row)))

    def list_plan(inp: PartyListInput, ctx: Context, s: Session) -> Plan:
        definition = get_list_definition(noun)
        assert definition is not None
        parsed = definition.parse_filters(inp.filter)
        manual_profile = next((item.value for item in parsed if item.field == "profile_complete"), None)
        sql_filters = [entry for entry in inp.filter if not entry.startswith("profile_complete=")]
        search, filters, sorts = _expressions(noun)
        manual_sort = inp.sort == "profile_complete"
        rows = list_service.list_rows(
            s.company,
            parties.TABLES[noun],
            definition,
            query=inp.query,
            filters=sql_filters,
            sort=None if manual_sort else inp.sort,
            direction=inp.direction,
            include_inactive=inp.include_inactive,
            search_expressions=search,
            filter_expressions=filters,
            sort_expressions=sorts,
        )
        projected = [_public_values(s, noun, row) for row in rows]
        if manual_profile is not None:
            projected = [item for item in projected if item["profile_complete"] is manual_profile]
        if manual_sort:
            reverse = inp.direction == "desc"
            projected.sort(key=lambda item: (item["profile_complete"], item["id"]), reverse=reverse)
        items = [_model_output(record_output, item) for item in projected]
        return Plan(preview=list_output(items=items, count=len(items)))

    def active_plan(active: bool):
        def planner(inp: BaseModel, ctx: Context, s: Session) -> Plan:
            row = parties.resolve_party(s.company, noun, getattr(inp, selector_field))
            change = parties.plan_party_active_change(
                s.company,
                noun,
                str(row["id"]),
                active,
                actor_id=s.actor.id,
                via=ctx.interface.value,
                cascade=bool(getattr(inp, "cascade", False)),
            )
            if change.changed:
                _version_meta(s, noun, row, {"active"}, inp.expected_version)
            requested = dict(change.mutations[0].after) if change.changed else row
            values = _public_values(s, noun, requested)
            output = active_output if active else deactivate_output
            return Plan(
                preview=_model_output(
                    output,
                    values,
                    changed=change.changed,
                    affected_ids=list(change.affected_ids),
                ),
                data={"change": change},
            )

        return planner

    def active_apply(plan: Plan, ctx: Context, s: Session) -> Applied:
        change = plan.data["change"]
        if not change.changed:
            return Applied(plan.preview, [], "no change")
        parties.persist_party_active_change(s.company, change)
        touched = [
            Touched(
                noun.replace("-", "_"),
                str(mutation.after["id"]),
                mutation.action,
                int(mutation.before["version"]),
                int(mutation.after["version"]),
                dict(mutation.after_snapshot),
                dict(mutation.before_snapshot),
                db="company",
            )
            for mutation in change.mutations
        ]
        return Applied(plan.preview, touched, f"{change.mutations[0].action}d {noun}")

    models = LifecycleModels(
        create=ModelPair(create_input, create_output),
        update=ModelPair(update_input, update_output),
        show=ModelPair(selector_input, record_output),
        list=ModelPair(PartyListInput, list_output),
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
    errors: dict[str, tuple[str, ...]] = {}
    if noun in {"customer", "vendor"}:
        errors.update(
            create=("E_AMOUNT_PRECISION", "E_VALUE_RANGE"),
            update=("E_AMOUNT_PRECISION", "E_VALUE_RANGE"),
        )
    if noun == "other-name":
        errors["activate"] = ("E_RECORD_IN_USE",)
    register_lifecycle(
        noun,
        models=models,
        callbacks=callbacks,
        selector_field=selector_field,
        error_codes=errors,
    )


def _load_target(target: str | None) -> None:
    requested = (
        parties.PARTY_NOUNS
        if target is None
        else tuple(
            noun
            for noun in parties.PARTY_NOUNS
            if target == noun or target.startswith(noun + " ") or noun.startswith(target + " ")
        )
    )
    for noun in requested:
        if f"{noun} show" not in REGISTRY:
            _register_party(noun)


_load_target(loading_target())


# Customer/vendor relationship ---------------------------------------------


class CustomerVendorLinkInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=True, defer_build=True
    )
    customer: str
    vendor: str
    expected_customer_version: int = Field(ge=1)
    expected_vendor_version: int = Field(ge=1)
    expected_link_version: int | None = Field(default=None, ge=1)


class CustomerVendorUnlinkInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=True, defer_build=True
    )
    customer: str
    expected_customer_version: int = Field(ge=1)
    expected_vendor_version: int = Field(ge=1)
    expected_link_version: int = Field(ge=1)


class CustomerVendorLinkOutput(WriteOutput):
    model_config = ConfigDict(extra="forbid", defer_build=True)

    customer_id: str
    customer_version: int
    vendor_id: str
    vendor_version: int
    link_id: str
    link_version: int
    active: bool


def _link_output(plan: parties.CustomerVendorLinkPlan) -> CustomerVendorLinkOutput:
    return CustomerVendorLinkOutput(
        customer_id=str(plan.customer_after["id"]),
        customer_version=int(plan.customer_after["version"]),
        vendor_id=str(plan.vendor_after["id"]),
        vendor_version=int(plan.vendor_after["version"]),
        link_id=str(plan.link_after["id"]),
        link_version=int(plan.link_after["version"]),
        active=bool(plan.link_after["active"]),
    )


customer_link_vendor = command(
    "customer link-vendor",
    scope="company",
    description="Link one active customer or job to one active vendor.",
    input_model=CustomerVendorLinkInput,
    output_model=CustomerVendorLinkOutput,
    writes={"company"},
    required_role="standard",
    positional=["customer", "vendor"],
    error_codes=["E_RECORD_NOT_FOUND", "E_INACTIVE_REFERENCE", "E_RECORD_IN_USE", "E_VERSION_CONFLICT"],
    accepts_idempotency_key=True,
    capability="customer",
)


@customer_link_vendor
def plan_customer_link_vendor(inp: CustomerVendorLinkInput, ctx: Context, s: Session) -> Plan:
    planned = parties.plan_customer_vendor_link(
        s.company,
        customer_selector=inp.customer,
        vendor_selector=inp.vendor,
        expected_customer_version=inp.expected_customer_version,
        expected_vendor_version=inp.expected_vendor_version,
        expected_link_version=inp.expected_link_version,
        actor_id=s.actor.id,
        via=ctx.interface.value,
    )
    return Plan(preview=_link_output(planned), data={"link": planned})


@customer_link_vendor.applier
def apply_customer_link_vendor(plan: Plan, ctx: Context, s: Session) -> Applied:
    linked = plan.data["link"]
    parties.persist_customer_vendor_link(s.company, linked)
    touched = [
        Touched(
            "customer",
            str(linked.customer_after["id"]),
            "link",
            int(linked.customer_before["version"]),
            int(linked.customer_after["version"]),
            dict(linked.customer_after_snapshot),
            dict(linked.customer_before_snapshot),
            db="company",
        ),
        Touched(
            "vendor",
            str(linked.vendor_after["id"]),
            "link",
            int(linked.vendor_before["version"]),
            int(linked.vendor_after["version"]),
            dict(linked.vendor_after_snapshot),
            dict(linked.vendor_before_snapshot),
            db="company",
        ),
        Touched(
            "customer_vendor_link",
            str(linked.link_after["id"]),
            "link",
            None if linked.link_before is None else int(linked.link_before["version"]),
            int(linked.link_after["version"]),
            dict(linked.link_after),
            None if linked.link_before is None else dict(linked.link_before),
            db="company",
        ),
    ]
    return Applied(plan.preview, touched, f"linked customer {linked.customer_after['name']} to vendor {linked.vendor_after['name']}")


customer_unlink_vendor = command(
    "customer unlink-vendor",
    scope="company",
    description="Unlink a customer or job from its current vendor.",
    input_model=CustomerVendorUnlinkInput,
    output_model=CustomerVendorLinkOutput,
    writes={"company"},
    required_role="standard",
    positional=["customer"],
    error_codes=["E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT"],
    accepts_idempotency_key=True,
    capability="customer",
)


@customer_unlink_vendor
def plan_customer_unlink_vendor(inp: CustomerVendorUnlinkInput, ctx: Context, s: Session) -> Plan:
    planned = parties.plan_customer_vendor_unlink(
        s.company,
        customer_selector=inp.customer,
        expected_customer_version=inp.expected_customer_version,
        expected_vendor_version=inp.expected_vendor_version,
        expected_link_version=inp.expected_link_version,
        actor_id=s.actor.id,
        via=ctx.interface.value,
    )
    return Plan(preview=_link_output(planned), data={"link": planned})


@customer_unlink_vendor.applier
def apply_customer_unlink_vendor(plan: Plan, ctx: Context, s: Session) -> Applied:
    linked = plan.data["link"]
    parties.persist_customer_vendor_link(s.company, linked)
    touched = [
        Touched(
            "customer_vendor_link",
            str(linked.link_after["id"]),
            "unlink",
            int(linked.link_before["version"]),
            int(linked.link_after["version"]),
            dict(linked.link_after),
            dict(linked.link_before),
            db="company",
        ),
        Touched(
            "customer",
            str(linked.customer_after["id"]),
            "unlink",
            int(linked.customer_before["version"]),
            int(linked.customer_after["version"]),
            dict(linked.customer_after_snapshot),
            dict(linked.customer_before_snapshot),
            db="company",
        ),
        Touched(
            "vendor",
            str(linked.vendor_after["id"]),
            "unlink",
            int(linked.vendor_before["version"]),
            int(linked.vendor_after["version"]),
            dict(linked.vendor_after_snapshot),
            dict(linked.vendor_before_snapshot),
            db="company",
        ),
    ]
    return Applied(plan.preview, touched, f"unlinked customer {linked.customer_after['name']} from vendor {linked.vendor_after['name']}")


# Other-name conversion ----------------------------------------------------


class OtherNameConvertInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=True, defer_build=True
    )
    other_name: str
    to: Literal["customer", "vendor", "employee"]
    expected_version: int = Field(ge=1)


class OtherNameConvertOutput(WriteOutput):
    model_config = ConfigDict(extra="forbid", defer_build=True)

    source_id: str
    source_version: int
    target_type: Literal["customer", "vendor", "employee"]
    target_id: str
    target_version: int
    mapped_fields: list[str]
    source_retained_fields: list[str]


other_name_convert = command(
    "other-name convert",
    scope="company",
    description="Convert an active other name into a new customer, vendor, or employee.",
    input_model=OtherNameConvertInput,
    output_model=OtherNameConvertOutput,
    writes={"company"},
    required_role="standard",
    positional=["other_name"],
    error_codes=["E_RECORD_NOT_FOUND", "E_RECORD_IN_USE", "E_NAME_TAKEN", "E_INACTIVE_REFERENCE", "E_VERSION_CONFLICT"],
    accepts_idempotency_key=True,
    capability="other-name",
)


@other_name_convert
def plan_other_name_convert(inp: OtherNameConvertInput, ctx: Context, s: Session) -> Plan:
    converted = parties.plan_other_name_conversion(
        s.company,
        selector=inp.other_name,
        target_type=inp.to,
        expected_version=inp.expected_version,
        actor_id=s.actor.id,
        via=ctx.interface.value,
    )
    output = OtherNameConvertOutput(
        source_id=str(converted.source_after["id"]),
        source_version=int(converted.source_after["version"]),
        target_type=inp.to,
        target_id=str(converted.target.after["id"]),
        target_version=int(converted.target.after["version"]),
        mapped_fields=list(converted.mapped_fields),
        source_retained_fields=list(converted.retained_fields),
    )
    return Plan(preview=output, data={"conversion": converted})


@other_name_convert.applier
def apply_other_name_convert(plan: Plan, ctx: Context, s: Session) -> Applied:
    converted = plan.data["conversion"]
    parties.persist_other_name_conversion(s.company, converted)
    touched = [
        Touched(
            "other_name",
            str(converted.source_after["id"]),
            "convert",
            int(converted.source_before["version"]),
            int(converted.source_after["version"]),
            dict(converted.source_after_snapshot),
            dict(converted.source_before_snapshot),
            db="company",
        ),
        Touched(
            converted.target.noun.replace("-", "_"),
            str(converted.target.after["id"]),
            "create",
            None,
            int(converted.target.after["version"]),
            dict(converted.target.after_snapshot),
            db="company",
        ),
    ]
    return Applied(
        plan.preview,
        touched,
        f"converted other name {converted.source_after['name']} to {converted.target.noun}",
    )


__all__ = [
    "ContactOutput",
    "CustomerVendorLinkInput",
    "CustomerVendorLinkOutput",
    "CustomerVendorUnlinkInput",
    "OtherNameConvertInput",
    "OtherNameConvertOutput",
    "PartyListInput",
    "ShippingAddressOutput",
    "VendorExpenseAccountOutput",
    "VendorItemProfileOutput",
]
