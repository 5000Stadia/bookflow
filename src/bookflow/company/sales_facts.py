"""Versioned, bounded commercial facts captured by each sales revision."""
from __future__ import annotations

from typing import Literal
from pydantic import field_validator, Field, model_serializer, model_validator

from bookflow.company.items import TRACKED_TYPES
from bookflow.company.tax_policy import Policy, TaxOrigin
from bookflow.company.sales_models import Address, StrictModel
from bookflow.company.billing_facts import AllocationProof, TaxAllocationProof
from bookflow.core.exact import INT64_MAX

# The item families a sale may carry: the three that post one income account and nothing else,
# plus the stock-carrying families, which additionally take their cost out of the inventory
# ledger. The stock half is read off the item master's own profile registry rather than written
# out again, so a later stock-carrying type is not silently left out of the grid that sells it.
SELLABLE_ITEM_TYPES = ("service", "non_inventory_part", "other_charge") + TRACKED_TYPES
# The two families that sit on a sale's line grid without being sold themselves: a subtotal
# shows the lines above it, and a discount takes an amount off the line or subtotal above it.
# Neither carries an income account; a discount posts to its own captured account instead.
ADJUSTMENT_ITEM_TYPES = ("subtotal", "discount")
LINE_ITEM_TYPES = SELLABLE_ITEM_TYPES + ADJUSTMENT_ITEM_TYPES
# A discount item names one income or expense account (blueprint 11.7).
DISCOUNT_ACCOUNT_TYPES = ('income', 'other_income', 'expense', 'other_expense')


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


class CommercialProfile(StrictModel):
    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_schema_version(cls, value):
        if type(value) is not int:
            raise ValueError('schema_version must be an integer discriminator')
        return value

    schema_version: Literal[1, 2] = 1
    sales_tax_calculation: Policy | None = None
    tax_policy_origin: TaxOrigin | None = None

    @model_validator(mode="after")
    def captured_policy(self):
        if self.schema_version == 1:
            if self.sales_tax_calculation is not None or self.tax_policy_origin is not None:
                raise ValueError('version one has no captured tax policy')
        elif self.sales_tax_calculation is None or self.tax_policy_origin is None:
            raise ValueError('version two requires policy and origin')
        return self

    @model_serializer(mode="wrap")
    def legacy_policy_facts(self, handler):
        values = handler(self)
        if self.schema_version == 1:
            values.pop('sales_tax_calculation', None)
            values.pop('tax_policy_origin', None)
        return values
    customer: Customer
    preferences: Preferences
    billing_address: Address | None = None
    shipping_address: Address | None = None
    shipping_address_id: str | None = None
    terms: Term | None = None
    ship_date: str | None = None
    ship_method: Reference | None = None
    sales_rep: Reference | None = None
    class_id: Reference | None = None
    customer_tax_code: TaxCode | None = None
    sales_tax_item: Reference | None = None
    tax_rules: list[TaxRule] | None = Field(default=None, max_length=200)
    price_level: Reference | None = None
    customer_message: str | None = None
    customer_message_item: Reference | None = None
    customer_purchase_order: str | None = None
    origins: dict[str, Origin] = Field(default_factory=dict)


class SalesProfile(CommercialProfile):
    control_account: Account
    due_date: str | None = None
    discount_date: str | None = None
    discount_available: bool = False
    payment_method: Reference | None = None
    payment_reference: str | None = None

    @model_serializer(mode="wrap")
    def legacy_order(self, handler):
        values = handler(self)
        # Preserve the existing sales wire representation, including key order.
        order = (
            'schema_version', 'customer', 'control_account', 'preferences',
            'billing_address', 'shipping_address', 'shipping_address_id', 'terms',
            'due_date', 'discount_date', 'discount_available', 'ship_date',
            'ship_method', 'sales_rep', 'class_id', 'customer_tax_code',
            'sales_tax_item', 'tax_rules', 'price_level', 'payment_method',
            'payment_reference', 'customer_message', 'customer_message_item',
            'customer_purchase_order', 'origins',
        )
        result = {key: values[key] for key in order if key in values}
        if self.schema_version == 2:
            # A caller's include/exclude may leave either captured field out of `values`.
            result.update({key: values[key] for key in ('sales_tax_calculation', 'tax_policy_origin')
                           if key in values})
        return result


class AdjustmentTarget(StrictModel):
    """One share of a discount: the revision-local position of the line it reduces, and how much.

    ``taxable_minor_units`` is how much the discount took off this line's taxable base when that
    differs from its share: a taxable discount over taxable and non-taxable lines reduces taxable
    sales by the whole discount, taken from the taxable lines alone. Absent means the share.
    """
    position: int = Field(ge=1, le=200)
    amount_minor_units: int = Field(ge=0, le=INT64_MAX)
    taxable_minor_units: int | None = Field(default=None, ge=0, le=INT64_MAX)

    @model_serializer(mode="wrap")
    def legacy_share(self, handler):
        values = handler(self)
        if self.taxable_minor_units is None:
            values.pop('taxable_minor_units', None)
        return values


class LineAdjustment(StrictModel):
    """What a subtotal, discount or percentage-charge line computed from the lines above it.

    ``amount_minor_units`` is the signed amount the line shows: a subtotal's sum, a discount's
    negative amount, a charge's positive amount. ``base_minor_units`` is what a discount or
    charge applied its percentage or fixed amount to: the line directly above, or the subtotal
    directly above. A discount records every share it took, by position; the lines it names
    carry those shares out of their own nets.
    """
    schema_version: Literal[1] = 1
    kind: Literal["subtotal", "discount", "charge"]
    # "billed": a discount or charge billed from an estimate or work order. Its amounts are
    # the quote's (a discount's shares follow the part of each line billed), never worked out
    # again from the lines above it on the sale.
    applies_to: Literal["line", "subtotal", "billed"] | None = None
    percent_millionths: int | None = Field(default=None, ge=0, le=100_000_000)
    fixed_minor_units: int | None = Field(default=None, ge=0, le=INT64_MAX)
    base_minor_units: int | None = Field(default=None, ge=-INT64_MAX, le=INT64_MAX)
    amount_minor_units: int = Field(ge=-INT64_MAX, le=INT64_MAX)
    account: Account | None = None
    targets: list[AdjustmentTarget] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "subtotal":
            if (self.applies_to, self.percent_millionths, self.fixed_minor_units, self.base_minor_units,
                    self.account) != (None,) * 5 or self.targets:
                raise ValueError("a subtotal carries only its shown amount")
            return self
        if self.applies_to is None or self.base_minor_units is None or self.base_minor_units < 0:
            raise ValueError("a discount or charge names the nonnegative line or subtotal above it")
        if (self.percent_millionths is None) == (self.fixed_minor_units is None):
            raise ValueError("a discount or charge has exactly one of a percentage or a fixed amount")
        if self.kind == "charge":
            if self.account is not None or self.targets or self.amount_minor_units < 0:
                raise ValueError("a charge is a nonnegative sold amount with no discount account or shares")
            if self.fixed_minor_units is not None:
                raise ValueError("a fixed charge is an ordinary line, not a percentage charge")
            return self
        if self.account is None or self.amount_minor_units > 0:
            raise ValueError("a discount has a posting account and a nonpositive amount")
        positions = [target.position for target in self.targets]
        if len(positions) != len(set(positions)) or positions != sorted(positions):
            raise ValueError("discount shares name distinct lines in document order")
        if sum(target.amount_minor_units for target in self.targets) != -self.amount_minor_units:
            raise ValueError("discount shares must add up to the discount")
        split = [target.taxable_minor_units for target in self.targets if target.taxable_minor_units is not None]
        if split and len(split) != len(self.targets):
            raise ValueError("a discount names its taxable reduction on every line or on none")
        return self


class LineGroup(StrictModel):
    """The group item a line was expanded from; the line itself is an ordinary line."""
    item: Reference
    description: str | None = Field(default=None, max_length=2000)
    print_members: bool


class SalesLineProfile(StrictModel):
    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_schema_version(cls, value):
        if type(value) is not int:
            raise ValueError('schema_version must be an integer discriminator')
        return value

    schema_version: Literal[1, 2, 3] = 1
    item: Reference
    item_type: Literal[LINE_ITEM_TYPES]
    # Null only on a subtotal or discount line: neither is sold, and a discount's own posting
    # account is captured in its adjustment.
    income_account: Account | None
    # The two accounts a stock-carrying item's cost moves between, captured beside the income
    # account so a correction years later posts the cost where the original posted it. Null on
    # every other family, which has no cost of its own to recognise on a sale.
    cogs_account: Account | None = None
    asset_account: Account | None = None
    unit: Unit | None = None
    class_id: Reference | None = None
    tax_code: TaxCode | None = None
    price_rule: PriceRule | None = None
    standard_price_minor_units: int | None = None
    cost_minor_units: int | None = None
    price_basis_minor_units: int | None = None
    origins: dict[str, Origin] = Field(default_factory=dict)

    pricing_basis: Literal["unit", "amount", "allocated"] = "unit"
    net_amount_minor_units: int | None = Field(default=None, ge=0, le=INT64_MAX)
    allocation_proof: AllocationProof | TaxAllocationProof | None = None
    adjustment: LineAdjustment | None = None
    group: LineGroup | None = None

    @model_validator(mode="before")
    @classmethod
    def amount_version(cls, values):
        if isinstance(values, dict) and values.get("pricing_basis") == "amount" and "schema_version" not in values:
            values = {**values, "schema_version": 2}
        return values

    @model_validator(mode="after")
    def line_kind(self):
        adjustment = self.adjustment
        if self.item_type in ADJUSTMENT_ITEM_TYPES:
            if (self.income_account is not None or adjustment is None or adjustment.kind != self.item_type
                    or self.pricing_basis != 'unit' or self.cogs_account is not None or self.asset_account is not None):
                raise ValueError('a subtotal or discount line carries its adjustment and no income account')
        elif self.income_account is None:
            raise ValueError('a sold line requires its income account')
        elif adjustment is not None and (adjustment.kind != 'charge' or self.item_type != 'other_charge'
                                         or self.pricing_basis not in ('amount', 'allocated')
                                         or (self.pricing_basis == 'allocated') != (adjustment.applies_to == 'billed')):
            raise ValueError('only an amount-priced other charge carries a percentage charge')
        return self

    @model_validator(mode="after")
    def pricing_consistency(self):
        if self.pricing_basis == 'allocated':
            if self.schema_version != 3 or self.allocation_proof is None or self.net_amount_minor_units is not None:
                raise ValueError('allocated pricing requires version three and an allocation proof')
            return self
        if self.allocation_proof is not None:
            raise ValueError('ordinary sale facts cannot carry an allocation proof')
        if self.pricing_basis == "amount":
            if self.net_amount_minor_units is None or self.schema_version != 2:
                raise ValueError("amount pricing requires version two and an exact net amount")
        elif self.net_amount_minor_units is not None or self.schema_version != 1:
            raise ValueError("unit pricing requires version one and no net amount basis")
        return self

    @model_serializer(mode="wrap")
    def legacy_unit_facts(self, handler):
        values = handler(self)
        for key in ('adjustment', 'group'):
            if getattr(self, key) is None:
                values.pop(key, None)
        if self.pricing_basis != 'allocated':
            values.pop('allocation_proof', None)
        if self.pricing_basis == "unit":
            values.pop("pricing_basis", None)
            values.pop("net_amount_minor_units", None)
        return values


# SalesLineProfile fields added after work-billing proofs were already being stored. Facts
# captured before then carry no key for them; absent and null mean the same captured fact.
LATER_LINE_PROFILE_FIELDS = ('cogs_account', 'asset_account')


class SalesTaxComponent(StrictModel):
    schema_version: Literal[1] = 1
    position: int = Field(ge=1)
    tax_item: Reference
    agency: Reference
    liability_account: Account
