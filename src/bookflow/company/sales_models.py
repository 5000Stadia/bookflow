"""Typed commercial sales input, exact quantities and nonnegative prices and amounts."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_serializer, model_validator

from bookflow.company.tax_policy import Policy
from bookflow.company.custom_fields import CustomFieldKindExpectations, CustomFieldValuePatch
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    INT64_MAX, _parse_scaled_decimal, format_quantity_micro_units,
    parse_quantity_micro_units,
)
from bookflow.core.money import Money, is_currency, minor_units_of


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Selector = Annotated[str, Field(min_length=1, max_length=1004), BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]
Text = Annotated[str, Field(max_length=2000)]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
HeaderDefault = Literal[
    "sales_tax_calculation", "billing_address", "shipping_address", "terms", "due_date", "ship_method",
    "sales_rep", "class_id", "customer_tax_code", "sales_tax_item", "price_level", "payment_method",
]
LineDefault = Literal["description", "unit", "unit_price", "class_id", "tax_code", "price_level"]


def _quantity(value):
    units = parse_quantity_micro_units(value)
    if units <= 0:
        raise ValueError("quantity must be greater than zero")
    return format_quantity_micro_units(units)


Quantity = Annotated[str, BeforeValidator(_quantity)]


def _invalid(field, problem):
    return BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": problem}]})


def _money_text(value: str, currency: str, field: str) -> Money:
    match = re.fullmatch(r"\s*(-?[0-9]+(?:\.[0-9]+)?)\s*([A-Z]{3})?\s*", value)
    if not match or match[2] not in (None, currency):
        raise _invalid(field, "use an exact home-currency decimal string")
    scale = minor_units_of(currency)
    if "." in match[1] and len(match[1].split(".")[1]) > scale:
        raise BookflowError("E_AMOUNT_PRECISION", details={"field": field, "currency": currency})
    units = _parse_scaled_decimal(match[1], scale=scale, field=field)
    if units < 0:
        raise _invalid(field, "must be nonnegative")
    return Money(units, currency)


class SalesMoneyInput(StrictModel):
    minor_units: int = Field(ge=0, le=INT64_MAX)
    currency: str = Field(min_length=3, max_length=3)
    amount: str | None = None

    @model_validator(mode="after")
    def consistent(self):
        if not is_currency(self.currency):
            raise ValueError("unknown currency code")
        if "amount" in self.model_fields_set:
            if self.amount is None or _money_text(self.amount, self.currency, "amount").minor_units != self.minor_units:
                raise ValueError("amount must agree with minor_units")
        return self


def money(value: str | SalesMoneyInput | dict, currency: str, field="unit_price") -> Money:
    """Parse nonnegative home money; floats, booleans and foreign prices fail."""
    if not is_currency(currency):
        raise _invalid(field, "unknown home currency")
    if isinstance(value, str):
        return _money_text(value, currency, field)
    if isinstance(value, dict):
        value = SalesMoneyInput.model_validate(value)
    if not isinstance(value, SalesMoneyInput) or value.currency != currency:
        raise _invalid(field, "use exact home-currency money")
    return Money(value.minor_units, currency)


class Address(StrictModel):
    line1: str | None = Field(default=None, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    state: str | None = Field(default=None, max_length=200)
    postal_code: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=200)


def _default_conflicts(model):
    fields = model.use_defaults
    if len(fields) != len(set(fields)):
        raise ValueError("use_defaults contains duplicate fields")
    if set(fields) - set(type(model).model_fields):
        raise ValueError("use_defaults contains a field unavailable on this document type")
    conflicts = set(fields) & model.model_fields_set
    if conflicts:
        raise ValueError("use_defaults conflicts with explicit input: " + ", ".join(sorted(conflicts)))


class SalesLineInput(StrictModel):
    line_id: Selector | None = None
    item: Selector
    quantity: Quantity = "1"
    unit: Selector | None = None
    unit_price: str | SalesMoneyInput | None = None
    net_amount: str | SalesMoneyInput | None = None
    description: Text | None = None
    class_id: Selector | None = None
    tax_code: Selector | None = None
    price_level: Selector | None = None
    price_basis_amount: str | SalesMoneyInput | None = None
    refresh_defaults: bool = False
    use_defaults: list[LineDefault] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def defaults(self):
        _default_conflicts(self)
        for field in ("unit_price", "price_basis_amount", "net_amount"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null; use use_defaults for a default price")
        if "net_amount" in self.model_fields_set:
            if self.model_fields_set & {"unit_price", "price_level", "price_basis_amount"}:
                raise ValueError("net_amount conflicts with unit_price, price_level or price_basis_amount")
            if "unit_price" in self.use_defaults:
                raise ValueError("net_amount conflicts with use_defaults unit_price")
        return self


Lines = Annotated[list[SalesLineInput], Field(min_length=1, max_length=200)]


class SalesFields(StrictModel):
    @model_serializer(mode='wrap')
    def legacy_tax_request(self, handler):
        values = handler(self)
        if 'sales_tax_calculation' not in self.model_fields_set:
            values.pop('sales_tax_calculation', None)
        return values

    sales_tax_calculation: Policy = Field(None, description="Captured tax calculation; omission selects company default on creation and retains policy on correction; null rejects")
    number: _Number | None = None
    memo: Text | None = None
    customer_message: Text | None = None
    customer_message_item: Selector | None = None
    customer_purchase_order: str | None = Field(default=None, max_length=128)
    billing_address: Address | None = None
    shipping_address: Address | None = None
    shipping_address_id: Selector | None = None
    ship_date: _Date | None = None
    ship_method: Selector | None = None
    sales_rep: Selector | None = None
    class_id: Selector | None = None
    customer_tax_code: Selector | None = None
    sales_tax_item: Selector | None = None
    price_level: Selector | None = None
    refresh_defaults: bool = False
    use_defaults: list[HeaderDefault] = Field(default_factory=list, max_length=13)
    expected_facts_fingerprint: Fingerprint | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda: CustomFieldKindExpectations({}))

    @model_validator(mode="after")
    def defaults(self):
        _default_conflicts(self)
        for left, right in (("customer_message", "customer_message_item"), ("shipping_address", "shipping_address_id")):
            if left in self.model_fields_set and right in self.model_fields_set:
                raise ValueError(f"choose {left} or {right}, not both")
        if "shipping_address" in self.use_defaults and "shipping_address_id" in self.model_fields_set:
            raise ValueError("a selected shipping address conflicts with returning shipping to defaults")
        return self


class SalesPostInput(SalesFields):
    date: _Date
    customer: Selector
    lines: Lines

    @model_validator(mode="after")
    def new_lines(self):
        if any(line.line_id is not None for line in self.lines):
            raise ValueError("new sales lines cannot supply an existing line identity")
        return self


class InvoiceFields(StrictModel):
    ar_account: Selector | None = None
    terms: Selector | None = None
    due_date: _Date | None = None


class ReceiptFields(StrictModel):
    payment_method: Selector | None = None
    payment_reference: str | None = Field(default=None, max_length=128)


class InvoicePostInput(SalesPostInput, InvoiceFields):
    pass


class SalesReceiptPostInput(SalesPostInput, ReceiptFields):
    deposit_to: Selector


class SalesUpdateInput(SalesFields):
    expected_version: _Version | None = None
    date: _Date | None = None
    customer: Selector | None = None
    lines: Lines | None = None

    @model_validator(mode="after")
    def required_values(self):
        for field in ("date", "customer", "lines", "number", "deposit_to", "ar_account", "due_date"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class SettlementPaymentVersion(StrictModel):
    payment: Selector
    expected_version: _Version


class InvoiceUpdateInput(SalesUpdateInput, InvoiceFields):
    invoice: Selector
    operation_key: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')] | None = Field(default=None, description='Required when the invoice has active payment applications. Choose one unique key for this correction and reuse it for preview, save and retries. When supplied, also give a reason of 1–140 characters.')
    settlement_versions: list[SettlementPaymentVersion] = Field(default_factory=list, description='For a changed invoice with active payment applications, supply every funding payment and its current expected_version, or supply settlement_guard instead. Read invoice settlement to review current settlement evidence; do not provide both alternatives.')
    settlement_guard: str | None = Field(default=None, max_length=2048, description='For a changed invoice with active payment applications, pass the guard returned by invoice settlement (or the settlement_dependencies error), or supply settlement_versions instead. After a stale rejection, review current settlement and preview again; do not provide both alternatives.')

    @model_validator(mode='after')
    def settlement_input(self):
        if self.settlement_guard is not None and self.settlement_versions:
            raise ValueError('settlement_guard and settlement_versions are mutually exclusive')
        return self

    @model_serializer(mode='wrap')
    def compatible_settlement(self, handler):
        values = handler(self)
        for key in ('operation_key', 'settlement_versions', 'settlement_guard', 'sales_tax_calculation'):
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values


class SalesReceiptUpdateInput(SalesUpdateInput, ReceiptFields):
    sales_receipt: Selector
    deposit_to: Selector | None = None
    amount_received: str | SalesMoneyInput | None = None

    @model_serializer(mode='wrap')
    def legacy_request(self, handler):
        values = handler(self)
        for key in ('amount_received', 'sales_tax_calculation'):
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values

    @model_validator(mode='after')
    def received_value(self):
        if 'amount_received' in self.model_fields_set and self.amount_received is None:
            raise ValueError('amount_received cannot be null; omit it for an unchanged received total')
        return self


class SalesShowInput(StrictModel):
    revision_number: _Version | None = None


class InvoiceShowInput(SalesShowInput):
    invoice: Selector


class SalesReceiptShowInput(SalesShowInput):
    sales_receipt: Selector


class InvoiceVoidInput(StrictModel):
    invoice: Selector
    expected_version: _Version | None = None


class SalesReceiptVoidInput(StrictModel):
    sales_receipt: Selector
    expected_version: _Version | None = None


class SalesPageInput(StrictModel):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)


class SalesQueryInput(SalesPageInput):
    date_from: _Date | None = None
    date_to: _Date | None = None
    customer: Selector | None = None
    number: str | None = Field(default=None, max_length=64)
    status: Literal["posted", "voided"] | None = None
    direction: Literal["asc", "desc"] = Field(default="asc",
        description="Order of the accounting-date then stable-id page: asc pages the oldest sale first, "
                    "desc the most recent first. A cursor belongs to the direction that minted it; "
                    "changing direction rejects it, so restart without a cursor.")

    @model_validator(mode="after")
    def dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot follow date_to")
        return self


class InvoiceHistoryInput(SalesPageInput):
    invoice: Selector


class SalesReceiptHistoryInput(SalesPageInput):
    sales_receipt: Selector
