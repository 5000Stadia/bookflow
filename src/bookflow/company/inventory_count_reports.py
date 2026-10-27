"""The Physical Inventory Worksheet and Inventory Valuation Detail (R104 increment 2, design §6, §8).

- **Physical Inventory Worksheet** [M 544; R 25, 44]: every active stocked item with its description,
  what the books hold on the as-of date (at one site, when ``site`` is named) and a blank column for
  the count. ``inventory-count sheet`` is the same sheet for a count, with the counted figures.
- **Inventory Valuation Detail** [R 25]: per item, its opening quantity and value, every stock movement
  in the period in replay order (date, then position against that day's counts, then recorded order)
  with the running quantity, value and average, and its closing figures. Corrections and reversals are
  rows too, so the running value is exactly what the balance sheet carries; the report refuses, as the
  valuation summary does, when the items' closing value disagrees with the inventory asset account.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from bookflow.company import ledger_reports as ledger, placements
from bookflow.company.inventory_costing import order_key
from bookflow.company.inventory_reports import _average, _check_tie, _quantity, _rows, _SELECTED, StockInput
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.core.clock import TODAY_DEFAULT, today_default


class WorksheetInput(StockInput):
    pass


class WorksheetRow(StrictModel):
    item_id: str
    item_name: str
    description: str | None
    site_id: str | None = Field(None, description='The site counted, when the worksheet is for one.')
    site_name: str | None = None
    quantity_on_hand: str = Field(description='What the books hold on the as-of date.')
    physical_count: str | None = Field(None, description='Blank: the column the counter fills in.')


class WorksheetOutput(ledger.Page):
    rows: list[WorksheetRow]


def physical_inventory_worksheet(inp: WorksheetInput, s, *, principal_id=None) -> WorksheetOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, 'physical-inventory-worksheet', principal_id, None, account_scoped=False)
        raw = s.company.raw
        items = [row for row in _rows(raw.execute(_SELECTED + 'SELECT * FROM selected ORDER BY full_name_key, id',
                                                  {'date_to': inp.as_of})) if row['active']]
        site = placements.resolve_site(s, inp.site, active=False) if inp.site else None
        held = placements.by_item(placements.on_hand(s, inp.as_of, site_id=site['id'])) if site else {}
        rows = [WorksheetRow(item_id=row['id'], item_name=row['full_name'], description=row['description'],
                             site_id=site['id'] if site else None, site_name=site['name'] if site else None,
                             quantity_on_hand=_quantity(held.get(row['id'], {}).get(site['id'], 0) if site
                                                        else int(row['quantity'])))
                for row in items]
    page = rows[offset:offset + inp.limit + 1]
    shown = page[:inp.limit]
    return WorksheetOutput(metadata=state.metadata, rows=shown, count=len(shown),
                           next_cursor=ledger._continuation(state, offset, len(shown), len(page) > inp.limit, s.company))


class ValuationDetailInput(StrictModel):
    date_from: str = Field(min_length=10, max_length=10, description='First date of the period, YYYY-MM-DD.')
    date_to: str = Field(default_factory=today_default, json_schema_extra=TODAY_DEFAULT, min_length=10, max_length=10, description='Last date of the period, YYYY-MM-DD.')
    item: str | None = Field(None, max_length=1000, description='Only this item, by id or name.')
    basis: Literal['accrual'] = 'accrual'
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _dates = field_validator('date_from', 'date_to')(iso_date)


class ValuationDetailRow(StrictModel):
    item_id: str
    item_name: str
    kind: Literal['opening', 'movement', 'closing']
    date: str | None = None
    document_type: str | None = Field(None, description='The document that moved the stock: invoice, bill, journal_entry...')
    number: str | None = None
    movement_kind: str | None = Field(None, description='receipt, issue, value, vendor_return, found, recost or reversal.')
    count_position: Literal['before', 'count', 'after'] | None = None
    quantity_change: str | None = None
    value_change: MoneyOutput | None = None
    quantity_on_hand: str
    asset_value: MoneyOutput
    average_cost: MoneyOutput


class ValuationDetailTotals(StrictModel):
    asset_value: MoneyOutput


class ValuationDetailOutput(ledger.Page):
    rows: list[ValuationDetailRow]
    totals: ValuationDetailTotals


def inventory_valuation_detail(inp: ValuationDetailInput, s, *, principal_id=None) -> ValuationDetailOutput:
    from bookflow.company import items as item_service
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, 'inventory-valuation-detail', principal_id, None, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        items = _rows(raw.execute(_SELECTED + 'SELECT * FROM selected ORDER BY full_name_key, id', {'date_to': inp.date_to}))
        if inp.item:
            chosen = item_service.resolve_item(s.company, inp.item)['id']
            items = [row for row in items if row['id'] == chosen]
        total = sum(money(int(row['value']), currency).minor_units for row in items)
        if not inp.item:
            _check_tie(raw, {'date_to': inp.date_to}, total)
        movements = _rows(raw.execute(
            'SELECT m.*, t.type AS document_type, t.number AS number FROM inventory_movements m '
            'JOIN transactions t ON t.id = m.transaction_id WHERE m.effective_date <= :date_to',
            {'date_to': inp.date_to}))
    by_item = {}
    for row in movements:
        by_item.setdefault(row['item_id'], []).append(row)
    rows = []
    for item in items:
        history = sorted(by_item.get(item['id'], []), key=order_key)
        quantity = value = 0
        inside = []
        for row in history:
            if row['effective_date'] < inp.date_from:
                quantity += int(row['quantity_microunits'])
                value += int(row['value_minor_units'])
            else:
                inside.append(row)
        if not inside and not quantity and not value:
            continue

        def line(kind, **extra):
            return ValuationDetailRow(item_id=item['id'], item_name=item['full_name'], kind=kind,
                                      quantity_on_hand=_quantity(quantity), asset_value=money(value, currency),
                                      average_cost=money(_average(value, quantity), currency), **extra)
        rows.append(line('opening', date=inp.date_from))
        for row in inside:
            quantity += int(row['quantity_microunits'])
            value += int(row['value_minor_units'])
            rows.append(line('movement', date=row['effective_date'], document_type=row['document_type'],
                             number=row['number'], movement_kind=row['kind'],
                             count_position=row.get('count_position') or 'before',
                             quantity_change=_quantity(int(row['quantity_microunits'])),
                             value_change=money(int(row['value_minor_units']), currency)))
        rows.append(line('closing', date=inp.date_to))
    page = rows[offset:offset + inp.limit + 1]
    shown = page[:inp.limit]
    return ValuationDetailOutput(metadata=state.metadata, rows=shown, count=len(shown),
                                 totals=ValuationDetailTotals(asset_value=money(total, currency)),
                                 next_cursor=ledger._continuation(state, offset, len(shown), len(page) > inp.limit,
                                                                  s.company))
