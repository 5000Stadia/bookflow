"""Inputs and outputs of price rules, the pricing-rule preference and the price preview (R110)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company.journal_models import _Date, _Version
from bookflow.company.sales_models import Selector


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


Kind = Literal['percent', 'amount']
Direction = Literal['down', 'up']
Basis = Literal['sales_price', 'cost']
Rounding = Literal['cent', 'x.99', 'x.95', 'dollar']
Policy = Literal['lowest_price', 'priority']
_LIST = 'Item, customer, class or rep IDs or names; an empty list (the default) matches every one.'


class BreakInput(_Strict):
    min_quantity: str = Field(min_length=1, max_length=32, description='Smallest quantity, in the item base unit, this break applies from, such as 10.')
    value: str = Field(min_length=1, max_length=32, description='The percent or amount for quantities from min_quantity up to the next break.')


class _RuleFields(_Strict):
    description: str | None = Field(None, max_length=500)
    priority: int | None = Field(None, ge=1, le=1_000_000, description='Order among rules, 1 first. Defaults to after every rule there is.')
    items: list[Selector] | None = Field(None, max_length=500, description='Items the rule prices; a parent item covers its subitems. ' + _LIST)
    item_categories: list[Selector] | None = Field(None, max_length=200, description='Item categories the rule prices, with their subcategories.')
    customers: list[Selector] | None = Field(None, max_length=500, description='Customers the rule prices for; a customer covers its jobs.')
    customer_types: list[Selector] | None = Field(None, max_length=200, description='Customer types the rule prices for, with their subtypes.')
    classes: list[Selector] | None = Field(None, max_length=200, description='Classes of the line (or the sale) the rule applies to, with their subclasses.')
    sales_reps: list[Selector] | None = Field(None, max_length=200, description='Sales reps of the sale the rule applies to.')
    date_from: _Date | None = Field(None, description='First transaction date the rule applies to.')
    date_to: _Date | None = Field(None, description='Last transaction date the rule applies to.')
    min_quantity: str | None = Field(None, max_length=32, description='Smallest line quantity, in the item base unit, the rule applies to.')
    kind: Kind | None = Field(None, description='percent (of the basis) or amount (per base unit). Default percent.')
    direction: Direction | None = Field(None, description='down (a discount, the default) or up (a markup).')
    basis: Basis | None = Field(None, description='sales_price (the item price, the default) or cost (the item cost).')
    value: str | None = Field(None, max_length=32, description='The percent, such as 10, or the amount, such as 1.50. Give value or breaks.')
    breaks: list[BreakInput] | None = Field(None, max_length=20, description='Quantity breaks in place of value: below the first, the rule does not apply.')
    rounding: Rounding | None = Field(None, description='cent (the default), x.99 or x.95 (up to the next price ending so), or dollar (nearest whole unit).')


class PriceRuleCreateInput(_RuleFields):
    name: str = Field(min_length=1, max_length=200, description='Rule name, unique, such as "Acme fittings 10% off".')


class PriceRuleUpdateInput(_RuleFields):
    rule: Selector = Field(description='Price rule ID or name.')
    expected_version: _Version | None = None
    name: str | None = Field(None, min_length=1, max_length=200)


class PriceRuleSelectInput(_Strict):
    rule: Selector = Field(description='Price rule ID or name.')


class PriceRuleStateInput(PriceRuleSelectInput):
    expected_version: _Version | None = None


class PriceRuleListInput(_Strict):
    include_inactive: bool = False
    item: Selector | None = Field(None, description='Only rules that could price this item.')
    customer: Selector | None = Field(None, description='Only rules that could price for this customer.')


class Named(_Strict):
    id: str
    label: str


class BreakOutput(_Strict):
    min_quantity: str
    value: str


class PriceRuleOutput(_Strict):
    id: str
    version: int
    name: str
    description: str | None
    active: bool
    priority: int
    date_from: str | None
    date_to: str | None
    min_quantity: str | None
    items: list[Named]
    item_categories: list[Named]
    customers: list[Named]
    customer_types: list[Named]
    classes: list[Named]
    sales_reps: list[Named]
    kind: Kind
    direction: Direction
    basis: Basis
    value: str | None
    breaks: list[BreakOutput] | None
    rounding: Rounding
    currency: str
    summary: str = Field(description='The rule in words, such as "10% off sales price for Acme; rounded to the cent".')


class PriceRuleWriteOutput(PriceRuleOutput):
    changed: bool = True


class PriceRulePageOutput(_Strict):
    rules: list[PriceRuleOutput]
    conflict_policy: Policy


class PreferencesInput(_Strict):
    pass


class PreferencesSetInput(_Strict):
    conflict_policy: Policy = Field(description='lowest_price: the lowest of the price level and the matching rules wins; priority: the first matching rule by priority wins over the price level.')


class PreferencesOutput(_Strict):
    conflict_policy: Policy
    version: int = Field(description='0 until the preference is first set.')
    changed: bool = False


class PricePreviewInput(_Strict):
    item: Selector = Field(description='Item ID or name.')
    customer: Selector = Field(description='Customer:job ID or name.')
    quantity: str = Field('1', max_length=32, description="Quantity, in the item's default sales unit, as a line takes it.")
    date: _Date | None = Field(None, description='Transaction date; today when omitted.')
    class_id: Selector | None = Field(None, description="Class of the sale; the customer's default when omitted (an item's own default class still wins on the line).")
    sales_rep: Selector | None = Field(None, description="Sales rep of the sale; the job's, else the customer's, when omitted.")
    price_level: Selector | None = Field(None, description="Price level of the sale; the job's, else the customer's, when omitted.")


class Candidate(_Strict):
    source: Literal['item', 'price_level', 'rule']
    id: str | None
    name: str | None
    priority: int | None = None
    unit_price: str
    chosen: bool


class PricePreviewOutput(_Strict):
    item: str
    customer: str
    quantity: str
    date: str
    conflict_policy: Policy
    unit_price: str
    currency: str
    source: Literal['item', 'price_level', 'rule']
    rule: Named | None
    note: str = Field(description='Where the price came from, such as "price from rule Acme fittings 10% off".')
    candidates: list[Candidate]
