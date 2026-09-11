"""Typed inputs and outputs for the inventory adjustment document.

An adjustment says one of three things, and the shape of the input is what says which:

- **quantity in** -- ``quantity_change`` positive, with ``value_change`` saying what it is
  worth. This is how opening stock is set, and a quantity arriving with no stated value is
  refused rather than silently valued at nothing.
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


class InventoryAdjustInput(_Input):
    item: _Selector = Field(description='Inventory item ID or name whose stock this adjusts.')
    date: _Date = Field(description='Accounting date this adjustment is valued and posted on.')
    adjustment_account: _Selector = Field(
        description='Account carrying the other side of the value change: shrinkage, cost of '
                    'goods sold, or the opening-balance equity account for opening stock.')
    quantity_change: _Quantity | None = Field(
        default=None,
        description='Signed change in quantity on hand, as a decimal string with at most six '
                    'places. Positive requires value_change; negative takes its value from the '
                    'weighted average and must not supply one.')
    value_change: str | MoneyInput | None = Field(
        default=None,
        description='Signed change in the asset value of this item. Required with a quantity '
                    'increase, forbidden with a decrease, and on its own it is a write-up or '
                    'write-down that moves no quantity.')
    negative_value: bool = Field(
        default=False,
        description='Set with value_change to write the asset value down rather than up. '
                    'Money is entered as a positive amount everywhere in Bookflow; this says '
                    'which way it moves.')
    class_id: _Selector | None = Field(default=None, description='Class captured on both posting lines.')
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
        quantity = 0 if self.quantity_change is None else parse_quantity_micro_units(
            self.quantity_change, field='quantity_change')
        if quantity == 0 and self.value_change is None:
            raise ValueError('an adjustment must change quantity, value, or both')
        if quantity > 0 and self.value_change is None:
            raise ValueError('a quantity increase must say what it is worth; supply value_change')
        if quantity < 0 and self.value_change is not None:
            raise ValueError('a quantity decrease takes its value from the weighted average; '
                             'value_change does not apply')
        if self.negative_value and self.value_change is None:
            raise ValueError('negative_value applies only with value_change')
        if quantity > 0 and self.negative_value:
            raise ValueError('a quantity increase cannot be worth a negative amount')
        return self


class InventoryVoidInput(_Input):
    adjustment: _Selector = Field(description='Inventory adjustment ID or document number to void.')
    expected_version: _Version | None = None


class InventoryShowInput(_Input):
    adjustment: _Selector
    revision_number: _Version | None = None


class InventoryCorrectionOutput(BaseModel):
    """One dated value delta this change posted against an earlier issue."""

    model_config = ConfigDict(strict=True, extra='forbid')

    item_id: str
    item_name: str
    effective_date: str
    corrects_movement_id: str
    delta: JournalMoneyOutput
    transaction_id: str
    number: str


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
    movement_kind: Literal['receipt', 'issue', 'value', 'reversal']
    corrections: list[InventoryCorrectionOutput]


class InventoryOutput(JournalOutput):
    adjustment: InventoryAdjustmentSummary


class InventoryWriteOutput(JournalWriteOutput):
    adjustment: InventoryAdjustmentSummary
