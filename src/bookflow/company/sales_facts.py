"""Versioned, bounded commercial facts captured by each sales revision."""
from __future__ import annotations

from typing import Literal
from pydantic import Field

from bookflow.company.sales_models import Address, StrictModel
from bookflow.core.exact import INT64_MAX


class Reference(StrictModel):
    id: str
    label: str
    version: int = Field(ge=1)


class Account(StrictModel):
    id: str
    name: str
    full_name: str
    number: str | None
    type: str
    normal_balance: Literal["debit", "credit"]


class Origin(StrictModel):
    kind: Literal["explicit", "default"]
    source_id: str | None = None


class Customer(Reference):
    company_name: str | None = None
    salutation: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    resale_number: str | None = None


class TaxCode(Reference):
    taxable: bool


class TaxRule(Reference):
    rate_percent_millionths: int = Field(ge=0, le=100_000_000)
    agency: Reference
    liability_account: Account


class Term(Reference):
    kind: Literal["standard", "date_driven"]
    due_days: int | None
    discount_days: int | None
    due_day_of_month: int | None
    due_next_month_if_within_days: int | None
    discount_day_of_month: int | None
    discount_percent_millionths: int | None


class Unit(Reference):
    set_id: str
    abbreviation: str
    factor_nanounits: int = Field(gt=0, le=INT64_MAX)


class PriceRule(Reference):
    kind: Literal["fixed_percent", "per_item"]
    currency: str
    rounding_mode: Literal["nearest", "up", "down"]
    increment_minor_units: int = Field(gt=0, le=INT64_MAX)
    offset_minor_units: int = Field(ge=-INT64_MAX-1, le=INT64_MAX)
    percent_millionths: int | None = None
    fixed_minor_units: int | None = None
    adjustment_basis: Literal["standard_price", "cost", "current_custom_price"] = "standard_price"
    matched: bool = True


class Preferences(StrictModel):
    sales_tax_enabled: bool
    sales_tax_liability_basis: Literal["invoice_date", "payment_receipt"]
    enable_price_levels: bool
    use_classes: bool
    prompt_for_class: bool
    units_of_measure_mode: Literal["disabled", "single_unit_per_item", "multiple_related_units"]


class SalesProfile(StrictModel):
    schema_version: Literal[1] = 1
    customer: Customer
    control_account: Account
    preferences: Preferences
    billing_address: Address | None = None
    shipping_address: Address | None = None
    shipping_address_id: str | None = None
    terms: Term | None = None
    due_date: str | None = None
    discount_date: str | None = None
    discount_available: bool = False
    ship_date: str | None = None
    ship_method: Reference | None = None
    sales_rep: Reference | None = None
    class_id: Reference | None = None
    customer_tax_code: TaxCode | None = None
    sales_tax_item: Reference | None = None
    tax_rules: list[TaxRule] | None = Field(default=None, max_length=200)
    price_level: Reference | None = None
    payment_method: Reference | None = None
    payment_reference: str | None = None
    customer_message: str | None = None
    customer_message_item: Reference | None = None
    customer_purchase_order: str | None = None
    origins: dict[str, Origin] = Field(default_factory=dict)


class SalesLineProfile(StrictModel):
    schema_version: Literal[1] = 1
    item: Reference
    item_type: Literal["service", "non_inventory_part", "other_charge"]
    income_account: Account
    unit: Unit | None = None
    class_id: Reference | None = None
    tax_code: TaxCode | None = None
    price_rule: PriceRule | None = None
    standard_price_minor_units: int | None = None
    cost_minor_units: int | None = None
    price_basis_minor_units: int | None = None
    origins: dict[str, Origin] = Field(default_factory=dict)


class SalesTaxComponent(StrictModel):
    schema_version: Literal[1] = 1
    position: int = Field(ge=1)
    tax_item: Reference
    agency: Reference
    liability_account: Account
