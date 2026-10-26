"""Typed inputs and outputs for the inventory adjustment document.

An adjustment says one of three things, and the shape of the input is what says which:

- **quantity in** -- ``quantity_change`` positive, with ``value_change`` saying what it is
  worth. This is how opening stock is set. With no stated value the units are *found*: valued
  at the running average as the anchor values a quantity adjustment (R104 increment 2), else the
  last average, else the item's purchase cost, else nothing with a warning.
- **quantity out** -- ``quantity_change`` negative and no ``value_change``. What it is worth
  is not a choice: the weighted average decides, and the last unit out takes the remainder.
- **value only** -- ``value_change`` alone, quantity unmoved. A write-up or write-down.

Supplying a value with a decrease is refused for the same reason the decrease has no value
field to fill in: naming both would be entering a cost the average does not support, which is
how a stock ledger and a balance sheet come apart.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from bookflow.company import serial_lot_models as _SL
from bookflow.company.custom_fields import CustomFieldKindExpectations, CustomFieldValuePatch
from bookflow.company.journal_models import MoneyInput
from bookflow.company.journal_outputs import (
    JournalMoneyOutput, JournalOutput, JournalWriteOutput,
)
from bookflow.core.exact import parse_quantity_micro_units


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


def _iso_date(value: Any) -> str:
    from datetime import date as calendar_date
    import re
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('must be an ISO date YYYY-MM-DD')
    calendar_date.fromisoformat(value)
    return value


def _quantity(value: Any) -> str:
    """A signed decimal quantity with at most six places, kept as the string it arrived as."""
    parse_quantity_micro_units(value, field='quantity_change')
    return value


_Date = Annotated[str, Field(min_length=10, max_length=10), BeforeValidator(_iso_date)]
_Selector = Annotated[str, Field(min_length=1, max_length=1000),
                      BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]
_Quantity = Annotated[str, Field(min_length=1, max_length=40), BeforeValidator(_quantity)]
_Version = Annotated[int, Field(strict=True, ge=1)]


_Position = Literal['before', 'after']


class InventoryAdjustLineInput(_Input):
    """One line of a many-item adjustment: the anchor's Adjust Quantity/Value on Hand grid row."""

    item: _Selector = Field(description='Inventory item ID, name or barcode whose stock this line adjusts.')
    quantity_change: _Quantity | None = Field(
        default=None, description='Signed change in quantity at the line\'s site (the anchor\'s Qty Difference). '
                                  'Give this or new_quantity, not both.')
    new_quantity: _Quantity | None = Field(
        default=None, description='The quantity at the line\'s site on the date, as counted (the anchor\'s New '
                                  'Quantity); the difference is worked out when written.')
    value_change: str | MoneyInput | None = Field(
        default=None, description='Signed change in the item\'s asset value. Omitted on an increase, the units are '
                                  'valued at the running average. Forbidden with a decrease.')
    new_value: str | MoneyInput | None = Field(
        default=None, description='The item\'s whole asset value on the date (the anchor\'s New Value); the change '
                                  'is worked out when written. Forbidden with a decrease.')
    negative_value: bool = Field(default=False, description='With value_change: write the value down rather than up.')
    site: _Selector | None = Field(default=None, description="Inventory site of this line; omitted, the adjustment's site, else the default site.")
    lot: _SL.LineLot = Field(default=None, description='Lot the units go into (an increase) or leave from (a decrease), of a lot-tracked item.')
    lot_expiry: _SL.IsoDate | None = Field(default=None, description=_SL.EXPIRY_HELP)
    serial_numbers: _SL.LineSerials = Field(default=None, max_length=10000, description=(
        'Serial numbers of the units found (an increase) or gone (a decrease), of a serial-tracked item; the rest are unassigned.'))
    class_id: _Selector | None = Field(default=None, description="Class of this line; omitted, the adjustment's class.")
    description: str | None = Field(default=None, max_length=2000, description='Line description on both posting lines.')

    @model_validator(mode='after')
    def one_complete_line(self) -> Self:
        if self.quantity_change is not None and self.new_quantity is not None:
            raise ValueError('give quantity_change or new_quantity, not both')
        if self.value_change is not None and self.new_value is not None:
            raise ValueError('give value_change or new_value, not both')
        if self.negative_value and self.value_change is None:
            raise ValueError('negative_value applies only with value_change')
        if self.new_quantity is not None and parse_quantity_micro_units(self.new_quantity, field='new_quantity') < 0:
            raise ValueError('new_quantity cannot be below zero')
        if self.quantity_change is None and self.new_quantity is None and self.value_change is None \
                and self.new_value is None:
            raise ValueError('a line must change quantity, value, or both')
        quantity = 0 if self.quantity_change is None else parse_quantity_micro_units(
            self.quantity_change, field='quantity_change')
        if quantity < 0 and (self.value_change is not None or self.new_value is not None):
            raise ValueError('a quantity decrease takes its value from the weighted average; '
                             'value_change and new_value do not apply')
        if quantity > 0 and self.negative_value:
            raise ValueError('a quantity increase cannot be worth a negative amount')
        return self


class InventoryAdjustInput(_Input):
    item: _Selector | None = Field(
        default=None, description='Inventory item ID, name or barcode whose stock this adjusts (the one-line form). '
                                  'Omit when giving lines.')
    date: _Date = Field(description='Accounting date this adjustment is valued and posted on.')
    adjustment_account: _Selector = Field(
        description='Account carrying the other side of the value change: shrinkage, cost of '
                    'goods sold, or the opening-balance equity account for opening stock.')
    quantity_change: _Quantity | None = Field(
        default=None,
        description='Signed change in quantity on hand, as a decimal string with at most six '
                    'places. A positive one with no value_change is valued at the running average '
                    '(else the last average, else the purchase cost); a negative one takes its value '
                    'from the weighted average and must not supply one.')
    value_change: str | MoneyInput | None = Field(
        default=None,
        description='Signed change in the asset value of this item. Optional with a quantity '
                    'increase, forbidden with a decrease, and on its own it is a write-up or '
                    'write-down that moves no quantity.')
    negative_value: bool = Field(
        default=False,
        description='Set with value_change to write the asset value down rather than up. '
                    'Money is entered as a positive amount everywhere in Bookflow; this says '
                    'which way it moves.')
    lines: list[InventoryAdjustLineInput] | None = Field(
        default=None, min_length=1, max_length=200,
        description='1-200 lines for many items in one adjustment, under one date and one adjustment account '
                    '(the anchor\'s Adjust Quantity/Value on Hand). Each gives quantity_change or new_quantity, '
                    'and optionally value_change or new_value. Leave item, quantity_change and value_change out.')
    class_id: _Selector | None = Field(default=None, description='Class captured on both posting lines.')
    site: _Selector | None = Field(default=None, description='Inventory site the quantity change is at, by id or name; omitted, the default site.')
    lot: _SL.LineLot = Field(default=None, description='One-item form: lot the units go into or leave from, of a lot-tracked item.')
    lot_expiry: _SL.IsoDate | None = Field(default=None, description=_SL.EXPIRY_HELP)
    serial_numbers: _SL.LineSerials = Field(default=None, max_length=10000, description=(
        'One-item form: serial numbers of the units found or gone, of a serial-tracked item.'))
    count_position: _Position = Field(
        default='before', description='before (the default) or after a physical count of its site on the same date.')
    memo: str | None = Field(default=None, max_length=2000, description='Why the stock was adjusted.')
    number: str | None = Field(default=None, min_length=1, max_length=64,
                               description='Document number; omit to take the next journal number.')
    custom_field_kinds: CustomFieldKindExpectations = Field(
        default_factory=lambda: CustomFieldKindExpectations({}),
        description='Optional captured kinds for supplied non-null custom values.')
    custom_fields: CustomFieldValuePatch = Field(
        default_factory=lambda: CustomFieldValuePatch({}),
        description='Header values keyed by journal_entry custom-field definition ID.')

    @model_validator(mode='after')
    def one_complete_intent(self) -> Self:
        if self.lines is not None:
            if self.item is not None or self.quantity_change is not None or self.value_change is not None \
                    or self.negative_value:
                raise ValueError('give lines, or item with quantity_change and value_change, not both')
            return self
        if self.item is None:
            raise ValueError('name the item, or give lines')
        quantity = 0 if self.quantity_change is None else parse_quantity_micro_units(
            self.quantity_change, field='quantity_change')
        if quantity == 0 and self.value_change is None:
            raise ValueError('an adjustment must change quantity, value, or both')
        if quantity < 0 and self.value_change is not None:
            raise ValueError('a quantity decrease takes its value from the weighted average; '
                             'value_change does not apply')
        if self.negative_value and self.value_change is None:
            raise ValueError('negative_value applies only with value_change')
        if quantity > 0 and self.negative_value:
            raise ValueError('a quantity increase cannot be worth a negative amount')
        return self

    def entered_lines(self) -> list[InventoryAdjustLineInput]:
        """The lines this adjustment enters; the one-line form is one line."""
        if self.lines is not None:
            return list(self.lines)
        return [InventoryAdjustLineInput.model_construct(
            item=self.item, quantity_change=self.quantity_change, new_quantity=None,
            value_change=self.value_change, new_value=None, negative_value=self.negative_value,
            site=None, class_id=None, description=None)]


class InventoryVoidInput(_Input):
    adjustment: _Selector = Field(description='Inventory adjustment ID or document number to void.')
    expected_version: _Version | None = None


class InventoryShowInput(_Input):
    adjustment: _Selector
    revision_number: _Version | None = None


class InventoryCorrectionOutput(BaseModel):
    """One dated value delta this change posted against an earlier issue.

    A backdating correction is dated at the issue it corrects; a true-up of a provisional cost
    is dated at the receipt named in ``filled_by_movement_id``.
    """

    model_config = ConfigDict(strict=True, extra='forbid')

    item_id: str
    item_name: str
    effective_date: str
    corrects_movement_id: str
    filled_by_movement_id: str | None = Field(
        default=None, description='On a provisional-cost true-up, the receipt movement whose '
                                  'arrival settled the cost; null on a backdating correction.')
    delta: JournalMoneyOutput
    transaction_id: str
    number: str


class InventoryAdjustmentLineSummary(BaseModel):
    """One line of an adjustment: what moved, where, and what the item now holds."""

    model_config = ConfigDict(strict=True, extra='forbid')

    item_id: str
    item_name: str
    site_id: str | None = None
    site_name: str | None = None
    quantity_change: str
    value_change: JournalMoneyOutput
    movement_kind: Literal['receipt', 'issue', 'value', 'found', 'reversal']
    value_basis: Literal['stated', 'average', 'purchase_cost', 'none'] = Field(
        description='How the value was set: stated by the caller, or for found units the running average, the '
                    'purchase cost, or nothing.')
    quantity_on_hand: str
    inventory_value: JournalMoneyOutput


class InventoryAdjustmentSummary(BaseModel):
    """The adjustment's own footer, computed once by the server."""

    model_config = ConfigDict(strict=True, extra='forbid')

    kind: Literal['adjustment']
    item_id: str
    item_name: str
    item_type: str
    currency: str
    quantity_change: str
    value_change: JournalMoneyOutput
    quantity_on_hand: str
    average_cost: JournalMoneyOutput
    inventory_value: JournalMoneyOutput
    asset_account_id: str
    adjustment_account_id: str
    movement_kind: Literal['receipt', 'issue', 'value', 'found', 'reversal']
    corrections: list[InventoryCorrectionOutput]
    lines: list['InventoryAdjustmentLineSummary'] = Field(
        default_factory=list, description='Every line of the adjustment, in order; the fields above repeat the first.')


class InventoryOutput(JournalOutput):
    adjustment: InventoryAdjustmentSummary


class InventoryWriteOutput(JournalWriteOutput):
    adjustment: InventoryAdjustmentSummary
