"""Input and output shapes for the costing method (R104 increment 7, design/advanced-inventory.md §3)."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from pydantic import field_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.journal_models import _Date as IsoDate
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date

Selector = Annotated[str, Field(min_length=1, max_length=1004), BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]
Method = Literal['average', 'fifo']


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CostingMethodSetInput(_Strict):
    method: Method = Field(description='average (weighted average, the default) or fifo (first in, first out).')
    effective_from: IsoDate = Field(description=(
        'First date the method costs, YYYY-MM-DD: after the closing date and after the last switch. Stock on hand '
        'that day becomes one opening layer (or the layers merge back into the average); nothing is revalued.'),
        json_schema_extra={'cli_flag': 'from'})



class CostingMethodWithdrawInput(_Strict):
    switch: Selector = Field(description='The switch to withdraw, by id or by its date (YYYY-MM-DD); its date must not have come.')


class CostingMethodShowInput(_Strict):
    pass


class SwitchRow(BaseModel):
    id: str
    method: Method
    effective_from: str
    status: Literal['standing', 'withdrawn']
    reason: str | None = None
    created_at: str
    created_by: str
    created_via: str
    withdrawn_at: str | None = None
    withdraw_reason: str | None = None


class ItemEffect(BaseModel):
    item_id: str
    item: str
    stock_value_change: str = Field(description='Change in the item\'s stock value today, from the corrections.')
    stock_value_change_minor_units: int
    cost_of_goods_sold_change: str = Field(description='Change in cost of goods sold (and other offsets) from the switch date on.')
    cost_of_goods_sold_change_minor_units: int


class CorrectionEntry(BaseModel):
    date: str
    memo: str
    items: int
    amount: str = Field(description='Total of the entry\'s inventory-asset changes.')
    amount_minor_units: int


class CostingMethodOutput(BaseModel):
    method_today: Method = Field(description='The method in force today.')
    switches: list[SwitchRow] = Field(default_factory=list, description='Every switch, oldest first; withdrawn ones say so.')
    message: str = ''


class CostingMethodWriteOutput(BaseModel):
    changed: bool
    switch: SwitchRow | None = None
    method_today: Method
    items: list[ItemEffect] = Field(default_factory=list, description='Each item whose cost the switch changes.')
    stock_value_change: str
    stock_value_change_minor_units: int
    cost_of_goods_sold_change: str
    cost_of_goods_sold_change_minor_units: int
    entries: list[CorrectionEntry] = Field(default_factory=list, description='The correction entries it posts, each at its own date.')
    message: str = ''
    warnings: list[str] = Field(default_factory=list)


class InventoryLayersInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description='Report date, YYYY-MM-DD: the layers on hand at its end.')
    item: str | None = Field(default=None, max_length=1000, description='Only this item, by name, id or barcode.')
    basis: Literal['accrual'] = 'accrual'
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator('as_of')(iso_date)

    @property
    def date_to(self) -> str:
        return self.as_of


class LayerRow(StrictModel):
    item_id: str
    item_name: str
    source: str = Field(description='The document that brought the units in, "Opening (FIFO from <date>)", or "Average".')
    source_movement_id: str | None = None
    date: str | None = Field(default=None, description='Date the layer came in; the switch date for an opening layer.')
    quantity: str
    value: MoneyOutput
    unit_cost: str = Field(description='Value over quantity, to four places (a two-decimal home currency assumed).')


class InventoryLayersTotals(StrictModel):
    asset_value: MoneyOutput


class InventoryLayersOutput(ledger.Page):
    method: Method = Field(description='The costing method in force on as_of.')
    rows: list[LayerRow]
    totals: InventoryLayersTotals
