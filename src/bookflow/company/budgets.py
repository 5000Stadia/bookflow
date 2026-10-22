"""Budgets: a named plan for one fiscal year, an amount per account per month (R109).

A budget follows the anchor's Set Up Budgets window. It is for one fiscal year and one kind --
profit and loss (each income and expense account's net for the month) or balance sheet (each
asset, liability and equity account's ending balance for the month) -- and a profit and loss
budget can be kept for the whole company, per customer:job, or per class. Several budgets may
cover the same year under different names, which is how the anchor's forecasts and what-if copies
are kept here.

Amounts are in the statement's own sign: income and expense are both entered positive, as the
anchor's grid takes them, and a balance is positive on its normal side. They are exact minor
units, parsed from decimal text, never a float. A cell with nothing in it is no row at all.

**A budget never posts.** Nothing here writes a posting line, and nothing the ledger reads
names a budget. Every change bumps the budget's version and is audited cell by cell, with the
amount before and after, so a cleared or replaced figure is still readable in the audit trail.

The entry operations are the anchor's: set amounts month by month (`budget set`), fill a row
from a starting amount with a monthly change by amount or percent, compounding, or copy one
month across the rest (`budget fill`), clear a row or the whole budget (`budget clear`), and
create a budget from the previous fiscal year's actual figures (`budget create --from-actuals`).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

import sqlalchemy as sa

from bookflow.company import accounts as chart, ledger_reports as ledger, schema as c
from bookflow.company.budget_models import (
    BudgetGridRow, BudgetOutput, BudgetPageOutput, BudgetShowOutput, BudgetWriteOutput, DimensionValue,
)
from bookflow.company.lists import normalize_display_name
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

PL_SECTIONS = ('income', 'cost_of_goods_sold', 'expense', 'other_income', 'other_expense')
BS_SECTIONS = ('assets', 'liabilities', 'equity')


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


# ---------------------------------------------------------------------------- calendar

def months_of(first_month: str) -> list[str]:
    year, month = int(first_month[:4]), int(first_month[5:7])
    out = []
    for _ in range(12):
        out.append(f'{year:04}-{month:02}')
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def fiscal_start_month(raw) -> int:
    return int(raw.execute('SELECT fiscal_year_start_month FROM company_info').fetchone()[0])


def first_month_of(raw, fiscal_year: int) -> str:
    return f'{fiscal_year:04}-{fiscal_start_month(raw):02}'


# ---------------------------------------------------------------------------- accounts

def section_of(account_type: str) -> str | None:
    if account_type in PL_SECTIONS:
        return account_type
    if account_type in chart.ASSET_TYPES:
        return 'assets'
    if account_type in chart.LIABILITY_TYPES:
        return 'liabilities'
    if account_type in chart.EQUITY_TYPES:
        return 'equity'
    return None


def sections_for(kind: str) -> tuple[str, ...]:
    return PL_SECTIONS if kind == 'profit_and_loss' else BS_SECTIONS


def statement_sign(account_type: str) -> int:
    """+1 where a debit is the statement's positive direction, -1 where a credit is."""
    return 1 if account_type in chart.DEBIT_NORMAL_TYPES else -1


def accounts_in_order(raw) -> list[dict[str, Any]]:
    """Every account in the order statements print them: account number, then name."""
    cursor = raw.execute('SELECT id, full_name, name, number, type, parent_id, active FROM accounts ORDER BY '
                         + ledger.account_order())
    names = [col[0] for col in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def display_preferences(raw) -> tuple[bool, bool]:
    numbers, lowest = raw.execute('SELECT use_account_numbers, show_lowest_subaccount_only FROM company_info').fetchone()
    return bool(numbers), bool(lowest)


def _account(s, selector: str, kind: str) -> dict[str, Any]:
    found = chart.resolve_account(s.company, selector, by_number=True)
    section = section_of(found['type'])
    if section is None or section not in sections_for(kind):
        raise _invalid('account', 'a profit and loss budget takes income and expense accounts; a balance sheet budget '
                                  'takes asset, liability and equity accounts')
    if not found['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'account', 'record_type': 'account', 'record_id': found['id']})
    return found


def _dimension(s, budget: dict[str, Any], inp, *, required: bool) -> tuple[str | None, str | None]:
    """The (customer id, class id) a cell or row names, checked against the budget's dimension."""
    customer, klass = getattr(inp, 'customer', None), getattr(inp, 'class_id', None)
    dim = budget['dimension']
    if dim != 'customer' and customer is not None:
        raise _invalid('customer', 'this budget is not kept per customer:job')
    if dim != 'class' and klass is not None:
        raise _invalid('class_id', 'this budget is not kept per class')
    if dim == 'customer':
        if customer is None:
            if required:
                raise _invalid('customer', 'name the customer or job this row is for')
            return None, None
        return resolve_customer(s, customer), None
    if dim == 'class':
        if klass is None:
            if required:
                raise _invalid('class_id', 'name the class this row is for')
            return None, None
        return None, resolve_class(s, klass)
    return None, None


def resolve_customer(s, selector: str) -> str:
    from bookflow.company.parties import resolve_party
    try:
        return resolve_party(s.company, 'customer', selector)['id']
    except BookflowError as exc:
        if exc.code == 'E_RECORD_NOT_FOUND':
            exc.details.setdefault('field', 'customer')
        raise


def resolve_class(s, selector: str) -> str:
    from bookflow.company import list_service
    from bookflow.company.lists import get_list_definition
    try:
        return list_service.resolve_selector(s.company, c.classes, get_list_definition('class'), selector)['id']
    except BookflowError as exc:
        if exc.code == 'E_RECORD_NOT_FOUND':
            exc.details.setdefault('field', 'class_id')
        raise


# ---------------------------------------------------------------------------- money

def parse_amount(text: str, currency: str, field: str) -> int:
    """Signed exact money from decimal text; '' and '0' are zero."""
    if text.strip() == '':
        return 0
    value = Money.parse(text.strip(), currency)
    if value.currency != currency:
        raise _invalid(field, 'use the home currency')
    if not ledger.I64_MIN <= value.minor_units <= ledger.I64_MAX:
        raise BookflowError('E_VALUE_RANGE', details={'field': field})
    return value.minor_units


def parse_percent(text: str, field: str) -> Fraction:
    try:
        number = Decimal(text.strip())
    except InvalidOperation:
        raise _invalid(field, 'a percent such as 5 or 2.5') from None
    if not number.is_finite() or abs(number) > 1000:
        raise _invalid(field, 'a percent between -1000 and 1000')
    return Fraction(number)


def _check_range(value: int, field: str) -> int:
    if not ledger.I64_MIN <= value <= ledger.I64_MAX:
        raise BookflowError('E_VALUE_RANGE', details={'field': field})
    return value


# ---------------------------------------------------------------------------- records

def resolve_budget(s, selector: str) -> dict[str, Any]:
    from bookflow.core.ids import is_ulid, normalize_ulid
    table = c.budgets
    trimmed = selector.strip() if isinstance(selector, str) else selector
    row = None
    if isinstance(trimmed, str) and is_ulid(trimmed):
        row = s.company.conn.execute(sa.select(table).where(table.c.id == normalize_ulid(trimmed))).mappings().first()
    if row is None and isinstance(trimmed, str) and trimmed:
        _, key = normalize_display_name(trimmed, field='budget')
        row = s.company.conn.execute(sa.select(table).where(table.c.name_key == key)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'budget', 'selector': selector, 'field': 'budget'})
    return dict(row)


def _check_version(budget: dict[str, Any], expected: int | None) -> None:
    if expected is not None and expected != budget['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': 'budget', 'record_id': budget['id'], 'current_version': budget['version'],
            'expected_version': expected, 'updated_at': budget['updated_at'], 'updated_by': budget['updated_by']})


def _unique_name(s, name: str, *, own: str | None = None) -> tuple[str, str]:
    display, key = normalize_display_name(name, field='name')
    query = sa.select(c.budgets.c.id).where(c.budgets.c.name_key == key)
    if own is not None:
        query = query.where(c.budgets.c.id != own)
    clash = s.company.conn.execute(query).first()
    if clash is not None:
        raise BookflowError('E_NAME_TAKEN', details={'record_type': 'budget', 'name': display, 'record_id': clash[0]})
    return display, key


def cells(s, budget_id: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Every stored cell of a budget, by (account id, dimension key, month)."""
    t = c.budget_amounts
    rows = s.company.conn.execute(sa.select(t).where(t.c.budget_id == budget_id)).mappings().all()
    return {(row['account_id'], row['dimension_key'], row['month']): dict(row) for row in rows}


def budget_output(budget: dict[str, Any], cell_count: int) -> BudgetOutput:
    return BudgetOutput(**{key: budget[key] for key in (
        'id', 'version', 'name', 'fiscal_year', 'first_month', 'kind', 'dimension', 'currency',
        'created_at', 'created_by', 'created_via', 'updated_at', 'updated_by', 'updated_via')},
        months=months_of(budget['first_month']), cell_count=cell_count)


def _dimension_labels(s, dimension: str, ids) -> dict[str, str]:
    if dimension == 'none' or not ids:
        return {}
    table = c.customers if dimension == 'customer' else c.classes
    rows = s.company.conn.execute(sa.select(table.c.id, table.c.full_name).where(table.c.id.in_(list(ids)))).all()
    return {row[0]: row[1] for row in rows}


def show_output(s, budget: dict[str, Any], stored=None, *, dimension_key: str | None = None) -> BudgetShowOutput:
    """The budget grid: accounts down, months across, as the stored cells say now."""
    stored = cells(s, budget['id']) if stored is None else stored
    months = months_of(budget['first_month'])
    currency = budget['currency']
    raw = s.company.raw
    numbers, lowest = display_preferences(raw)
    order = sections_for(budget['kind'])
    labels = _dimension_labels(s, budget['dimension'], {key[1] for key in stored if key[1]})
    by_row: dict[tuple[str, str], dict[str, int]] = {}
    for (account_id, dim, month), row in stored.items():
        if dimension_key is not None and dim != dimension_key:
            continue
        by_row.setdefault((account_id, dim), {})[month] = row['amount_minor_units']
    accounts = accounts_in_order(raw)
    position = {row['id']: index for index, row in enumerate(accounts)}
    info = {row['id']: row for row in accounts}
    keys = sorted(by_row, key=lambda key: (order.index(section_of(info[key[0]]['type'])), position[key[0]],
                                           (labels.get(key[1]) or '').casefold(), key[1]))
    rows, month_totals = [], [0] * 12
    for account_id, dim in keys:
        account = info[account_id]
        section = section_of(account['type'])
        values = [by_row[(account_id, dim)].get(month, 0) for month in months]
        # Net income budgeted: income adds, expense subtracts; a balance sheet grid just sums.
        sign = 1 if budget['kind'] == 'balance_sheet' or section in ('income', 'other_income') else -1
        for index, value in enumerate(values):
            month_totals[index] += sign * value
        rows.append(BudgetGridRow(
            account_id=account_id, current_account_label=account['full_name'],
            display_account_label=ledger._account_display(account['full_name'], account['name'], account['number'], numbers, lowest),
            account_type=account['type'], section=section, active=bool(account['active']),
            dimension_id=dim or None, dimension_label=labels.get(dim) if dim else None,
            amounts=[ledger.money(_check_range(value, 'amount'), currency) for value in values],
            total=ledger.money(_check_range(sum(values), 'amount'), currency)))
    values = [DimensionValue(id=key, label=label) for key, label in sorted(labels.items(), key=lambda kv: (kv[1].casefold(), kv[0]))]
    base = budget_output(budget, len(stored))
    return BudgetShowOutput(**base.model_dump(), rows=rows, dimension_values=values,
                            month_totals=[ledger.money(_check_range(v, 'amount'), currency) for v in month_totals],
                            total=ledger.money(_check_range(sum(month_totals), 'amount'), currency))


def show(s, inp) -> BudgetShowOutput:
    budget = resolve_budget(s, inp.budget)
    customer, klass = _dimension(s, budget, inp, required=False)
    key = customer or klass
    return show_output(s, budget, dimension_key=key)


def page(s, inp) -> BudgetPageOutput:
    t = c.budgets
    query = sa.select(t)
    if inp.fiscal_year is not None:
        query = query.where(t.c.fiscal_year == inp.fiscal_year)
    rows = [dict(row) for row in s.company.conn.execute(query).mappings().all()]
    rows.sort(key=lambda row: (-row['fiscal_year'], row['name_key'], row['id']))
    if inp.cursor is not None:
        ids = [row['id'] for row in rows]
        rows = rows[ids.index(inp.cursor) + 1:] if inp.cursor in ids else []
    shown = rows[:inp.limit]
    counts = dict(s.company.conn.execute(
        sa.select(c.budget_amounts.c.budget_id, sa.func.count()).group_by(c.budget_amounts.c.budget_id)).all())
    items = [budget_output(row, counts.get(row['id'], 0)) for row in shown]
    return BudgetPageOutput(items=items, count=len(items),
                            next_cursor=shown[-1]['id'] if len(rows) > inp.limit else None)


# ---------------------------------------------------------------------------- changes

def _touched_cells(before: dict, after: dict) -> list[Touched]:
    out = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old is not None and new is not None and old['amount_minor_units'] == new['amount_minor_units']:
            continue
        if old is None:
            out.append(Touched('budget_amount', new['id'], 'create', None, 1, new, db='company'))
        elif new is None:
            out.append(Touched('budget_amount', old['id'], 'delete', 1, None, None, old, db='company'))
        else:
            out.append(Touched('budget_amount', new['id'], 'update', 1, 1, new, old, db='company'))
    return out


def _new_cells(s, ctx, budget, account_id, customer, klass, values: dict[str, int], stored, at) -> dict:
    """The stored cells after setting `values` (month -> minor units, 0 clears) on one row."""
    after = dict(stored)
    dim = customer or klass or ''
    for month, amount in values.items():
        key = (account_id, dim, month)
        old = stored.get(key)
        if amount == 0:
            after.pop(key, None)
            continue
        if old is not None and old['amount_minor_units'] == amount:
            continue
        after[key] = dict(id=old['id'] if old else new_id(), budget_id=budget['id'], account_id=account_id,
                          customer_id=customer, class_id=klass, dimension_key=dim, month=month,
                          amount_minor_units=amount, updated_at=at, updated_by=s.actor.id,
                          updated_via=ctx.interface.value)
    return after


def _plan_cells(s, ctx, budget, stored, after, *, action: str) -> Plan:
    touched = _touched_cells(stored, after)
    changed = bool(touched)
    new = dict(budget)
    if changed:
        new.update(version=budget['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                   updated_via=ctx.interface.value)
    output = BudgetWriteOutput(budget=show_output(s, new, after), changed_cells=len(touched), changed=changed)
    return Plan(output, dict(before=budget, budget=new, cells_before=stored, cells_after=after,
                             touched=touched, action=action))


def apply_cells(plan: Plan, ctx, s) -> Applied:
    data = plan.data
    if not data['touched']:
        return Applied(plan.preview, [], 'no change')
    t = c.budget_amounts
    budget, before = data['budget'], data['before']
    for entry in data['touched']:
        if entry.action == 'delete':
            s.company.conn.execute(t.delete().where(t.c.id == entry.record_id))
        elif entry.action == 'update':
            s.company.conn.execute(t.update().where(t.c.id == entry.record_id).values(**entry.after))
    created = [entry.after for entry in data['touched'] if entry.action == 'create']
    if created:
        s.company.conn.execute(t.insert(), created)
    s.company.conn.execute(c.budgets.update().where(c.budgets.c.id == budget['id']).values(**budget))
    touched = [Touched('budget', budget['id'], 'update', before['version'], budget['version'], budget, before, db='company'),
               *data['touched']]
    count = len(data['touched'])
    return Applied(plan.preview, touched, f"{data['action']} {count} cell{'s' if count != 1 else ''} of budget {budget['name']}")


def _months_from(budget, start: str | None, field: str) -> list[str]:
    months = months_of(budget['first_month'])
    start = start or months[0]
    if start not in months:
        raise _invalid(field, f"a month of this budget, {months[0]} through {months[-1]}")
    return months[months.index(start):]


def plan_set(s, ctx, inp) -> Plan:
    budget = resolve_budget(s, inp.budget)
    _check_version(budget, inp.expected_version)
    account = _account(s, inp.account, budget['kind'])
    customer, klass = _dimension(s, budget, inp, required=True)
    months = _months_from(budget, inp.start_month, 'start_month')
    if len(inp.amounts) > len(months):
        raise _invalid('amounts', f'{len(months)} months remain in the budget from {months[0]}')
    values = {month: parse_amount(text, budget['currency'], f'amounts[{index}]')
              for index, (month, text) in enumerate(zip(months, inp.amounts))}
    stored = cells(s, budget['id'])
    after = _new_cells(s, ctx, budget, account['id'], customer, klass, values, stored, clock.now_iso())
    return _plan_cells(s, ctx, budget, stored, after, action='set')


def fill_values(first: int, count: int, *, change: int | None = None, percent: Fraction | None = None) -> list[int]:
    """The anchor's row fill: the first amount, then each month from the one before.

    A dollar change adds to the month before; a percent change multiplies it by (1 + p/100)
    and rounds to the minor unit half to even, so 5000.00 at 1% gives 5050.00 then 5100.50.
    """
    values = [first]
    for _ in range(count - 1):
        previous = values[-1]
        if change is not None:
            values.append(previous + change)
        elif percent is not None:
            values.append(round(Fraction(previous) * (100 + percent) / 100))
        else:
            values.append(previous)
    return [_check_range(value, 'amount') for value in values]


def plan_fill(s, ctx, inp) -> Plan:
    budget = resolve_budget(s, inp.budget)
    _check_version(budget, inp.expected_version)
    account = _account(s, inp.account, budget['kind'])
    customer, klass = _dimension(s, budget, inp, required=True)
    months = _months_from(budget, inp.start_month, 'start_month')
    if inp.through_month is not None:
        if inp.through_month not in months:
            raise _invalid('through_month', f'a month of this budget from {months[0]} through {months[-1]}')
        months = months[:months.index(inp.through_month) + 1]
    stored = cells(s, budget['id'])
    dim = customer or klass or ''
    if inp.amount is None:
        existing = stored.get((account['id'], dim, months[0]))
        first = existing['amount_minor_units'] if existing else 0
    else:
        first = parse_amount(inp.amount, budget['currency'], 'amount')
    change = parse_amount(inp.change, budget['currency'], 'change') if inp.change is not None else None
    percent = parse_percent(inp.change_percent, 'change_percent') if inp.change_percent is not None else None
    values = dict(zip(months, fill_values(first, len(months), change=change, percent=percent)))
    after = _new_cells(s, ctx, budget, account['id'], customer, klass, values, stored, clock.now_iso())
    return _plan_cells(s, ctx, budget, stored, after, action='filled')


def plan_clear(s, ctx, inp) -> Plan:
    budget = resolve_budget(s, inp.budget)
    _check_version(budget, inp.expected_version)
    account = _account(s, inp.account, budget['kind']) if inp.account is not None else None
    customer, klass = _dimension(s, budget, inp, required=False)
    dim = customer or klass
    stored = cells(s, budget['id'])
    after = {key: row for key, row in stored.items()
             if not ((account is None or key[0] == account['id']) and (dim is None or key[1] == dim))}
    return _plan_cells(s, ctx, budget, stored, after, action='cleared')


# ---------------------------------------------------------------------------- create, rename, delete

def plan_create(s, ctx, inp) -> Plan:
    display, key = _unique_name(s, inp.name)
    raw = s.company.raw
    currency = raw.execute('SELECT home_currency FROM company_info').fetchone()[0]
    at = clock.now_iso()
    budget = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), name=display, name_key=key,
                  fiscal_year=inp.fiscal_year, first_month=first_month_of(raw, inp.fiscal_year), kind=inp.kind,
                  dimension=inp.by, currency=currency)
    stored: dict = {}
    if inp.from_actuals:
        from bookflow.company.budget_reports import previous_year_actuals
        for (account_id, dim, month), amount in previous_year_actuals(s, inp, budget).items():
            if amount == 0:
                continue
            stored[(account_id, dim, month)] = dict(
                id=new_id(), budget_id=budget['id'], account_id=account_id,
                customer_id=dim if budget['dimension'] == 'customer' and dim else None,
                class_id=dim if budget['dimension'] == 'class' and dim else None,
                dimension_key=dim, month=month, amount_minor_units=_check_range(amount, 'amount'),
                updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    output = BudgetWriteOutput(budget=show_output(s, budget, stored), changed_cells=len(stored))
    return Plan(output, dict(budget=budget, cells=list(stored.values())))


def apply_create(plan: Plan, ctx, s) -> Applied:
    budget, rows = plan.data['budget'], plan.data['cells']
    s.company.conn.execute(c.budgets.insert().values(**budget))
    if rows:
        s.company.conn.execute(c.budget_amounts.insert(), rows)
    touched = [Touched('budget', budget['id'], 'create', None, 1, budget, db='company')]
    touched += [Touched('budget_amount', row['id'], 'create', None, 1, row, db='company') for row in rows]
    return Applied(plan.preview, touched, f"created budget {budget['name']} for fiscal year {budget['fiscal_year']}")


def plan_rename(s, ctx, inp) -> Plan:
    budget = resolve_budget(s, inp.budget)
    _check_version(budget, inp.expected_version)
    display, key = _unique_name(s, inp.name, own=budget['id'])
    changed = display != budget['name']
    new = dict(budget)
    if changed:
        new.update(name=display, name_key=key, version=budget['version'] + 1, updated_at=clock.now_iso(),
                   updated_by=s.actor.id, updated_via=ctx.interface.value)
    return Plan(BudgetWriteOutput(budget=show_output(s, new), changed=changed),
                dict(before=budget, budget=new, changed=changed))


def apply_rename(plan: Plan, ctx, s) -> Applied:
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    budget, before = plan.data['budget'], plan.data['before']
    s.company.conn.execute(c.budgets.update().where(c.budgets.c.id == budget['id']).values(**budget))
    return Applied(plan.preview, [Touched('budget', budget['id'], 'update', before['version'], budget['version'],
                                          budget, before, db='company')], f"renamed budget to {budget['name']}")


def plan_delete(s, ctx, inp) -> Plan:
    budget = resolve_budget(s, inp.budget)
    _check_version(budget, inp.expected_version)
    stored = cells(s, budget['id'])
    return Plan(BudgetWriteOutput(budget=show_output(s, budget, stored), deleted=True, changed_cells=len(stored)),
                dict(budget=budget, cells=list(stored.values())))


def apply_delete(plan: Plan, ctx, s) -> Applied:
    budget, rows = plan.data['budget'], plan.data['cells']
    s.company.conn.execute(c.budget_amounts.delete().where(c.budget_amounts.c.budget_id == budget['id']))
    s.company.conn.execute(c.budgets.delete().where(c.budgets.c.id == budget['id']))
    # Removed for good, and kept whole in the audit trail: every cell's last amount is its before image.
    touched = [Touched('budget_amount', row['id'], 'delete', 1, None, None, row, db='company') for row in rows]
    touched.append(Touched('budget', budget['id'], 'delete', budget['version'], None, None, budget, db='company'))
    return Applied(plan.preview, touched, f"deleted budget {budget['name']}")
