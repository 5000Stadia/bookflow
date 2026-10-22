"""Budget reports and period comparisons (R109).

Four reports, all rows-by-account statements whose columns come in groups:

* **Budget overview** -- the budget alone, by month, fiscal quarter or year, with the statement's
  section totals and net income, which the entry grid cannot show (the anchor's Budget Overview).
* **Budget vs. actual** -- for each period, Actual, Budget, $ Over Budget and % of Budget, then
  the same four for the whole range (the anchor's Budget vs. Actual).
* **Profit and loss comparison** -- the profit and loss for a range beside the same range a year
  earlier, or the period of the same length just before, with $ Change and % Change (the anchor's
  Prev Year Comparison and its Previous Period column).
* **Balance sheet comparison** -- balances on a date beside the same date a year earlier.

**Actual is the profit and loss, not a second arithmetic.** A period's actual figure for an
account is the period net that ``report profit-and-loss`` reads, through the same effects query
(``ledger_reports._effects``) and the same cash projection, so the Actual of a whole-range column
is that report's figure for the same dates, account for account. A balance is the closing balance
the balance sheet reads. Nothing here writes, and a budget never reaches the ledger.

**Partial months are prorated by days.** A budget amount is for a whole month. A column that
covers part of a month takes the month's amount times the days covered over the days in the
month, rounded half to even to the minor unit, as the anchor prorates a budget over a date range
that is not whole months. A balance sheet budget is an ending balance and is never prorated: a
column takes the amount of the month it ends in.

**Percent** columns are exact ratios rounded half to even to one decimal place. % of Budget is
actual over budget; % Change is the change over the size of the earlier figure, so a loss that
shrinks reads as a positive change. Either is blank where there is nothing to divide by.

**Dimensions.** A customer or class budget compares, by default, the sum of every customer or
class it budgets with the actual figures posted to those same customers or classes; naming one
customer or class narrows both sides to it. A customer budget's actual is the lines that name
that customer or job itself, as profit and loss by job reads them.
"""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction
from typing import Any

from bookflow.company import budgets as B, ledger_reports as ledger
from bookflow.company.budget_models import (
    BalanceSheetComparisonOutput, BudgetOverviewOutput, BudgetVsActualOutput, ComparisonCell, ComparisonRow,
    ProfitAndLossComparisonOutput, ReportColumn, SummaryLine,
)
from bookflow.company.cash_report_view import reporting_connection

MONTH_NAMES = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
MEASURE_LABELS = {'actual': 'Actual', 'budget': 'Budget', 'over_budget': '$ Over Budget',
                  'percent_of_budget': '% of Budget', 'change': '$ Change', 'percent_change': '% Change'}
PL_LINES = (
    ('income', 'Total income'), ('cost_of_goods_sold', 'Total cost of goods sold'), ('gross_profit', 'Gross profit'),
    ('expense', 'Total expense'), ('net_operating_income', 'Net operating income'),
    ('other_income', 'Total other income'), ('other_expense', 'Total other expense'), ('net_income', 'Net income'),
)
BS_BUDGET_LINES = (('assets', 'Total assets'), ('liabilities', 'Total liabilities'), ('equity', 'Total equity'))
BS_LINES = (
    ('assets', 'Total assets'), ('liabilities', 'Total liabilities'), ('posted_equity', 'Total posted equity'),
    ('prior_earnings', 'Retained earnings (prior years)'), ('current_year_income', 'Net income (this fiscal year)'),
    ('total_equity', 'Total equity'), ('liabilities_and_equity', 'Total liabilities and equity'),
)


# ---------------------------------------------------------------------------- dates

def _d(text: str) -> date:
    return date.fromisoformat(text)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _whole_months(first: date, last: date) -> bool:
    return first.day == 1 and last == _month_end(last.year, last.month)


def _range_label(first: date, last: date) -> str:
    if _whole_months(first, last):
        a, b = f'{MONTH_NAMES[first.month - 1]}', f'{MONTH_NAMES[last.month - 1]}'
        if (first.year, first.month) == (last.year, last.month):
            return f'{a} {first.year}'
        if first.year == last.year:
            return f'{a} – {b} {last.year}'
        return f'{a} {first.year} – {b} {last.year}'
    if first == last:
        return f'{MONTH_NAMES[first.month - 1]} {first.day}, {first.year}'
    if first.year == last.year:
        return f'{MONTH_NAMES[first.month - 1]} {first.day} – {MONTH_NAMES[last.month - 1]} {last.day}, {last.year}'
    return f'{MONTH_NAMES[first.month - 1]} {first.day}, {first.year} – {MONTH_NAMES[last.month - 1]} {last.day}, {last.year}'


def _shift_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def previous_year(date_from: str, date_to: str) -> tuple[str, str]:
    first, last = _d(date_from), _d(date_to)
    if _whole_months(first, last):
        return date(first.year - 1, first.month, 1).isoformat(), _month_end(last.year - 1, last.month).isoformat()
    return _shift_year(first).isoformat(), _shift_year(last).isoformat()


def previous_period(date_from: str, date_to: str) -> tuple[str, str]:
    """The period of the same length just before: whole months by whole months, otherwise day for day."""
    first, last = _d(date_from), _d(date_to)
    if _whole_months(first, last):
        count = (last.year * 12 + last.month) - (first.year * 12 + first.month) + 1
        y, m = _add_months(first.year, first.month, -count)
        ly, lm = _add_months(last.year, last.month, -count)
        return date(y, m, 1).isoformat(), _month_end(ly, lm).isoformat()
    days = (last - first).days + 1
    end = first - timedelta(days=1)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


@dataclass
class Group:
    label: str
    date_from: str
    date_to: str
    # (YYYY-MM, days covered, days in the month)
    months: list[tuple[str, int, int]] = field(default_factory=list)
    total: bool = False


def _month_pieces(date_from: str, date_to: str) -> list[tuple[str, date, date, int]]:
    first, last = _d(date_from), _d(date_to)
    pieces = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        start, end = max(first, date(y, m, 1)), min(last, _month_end(y, m))
        pieces.append((f'{y:04}-{m:02}', start, end, calendar.monthrange(y, m)[1]))
        y, m = _add_months(y, m, 1)
    return pieces


def groups_for(date_from: str, date_to: str, by: str, fiscal_month: int) -> list[Group]:
    """Column groups over a range: months, fiscal quarters or one, then a Total when there are several."""
    pieces = _month_pieces(date_from, date_to)
    buckets: list[list] = []
    for piece in pieces:
        y, m = int(piece[0][:4]), int(piece[0][5:7])
        if by == 'month':
            key = (y, m)
        elif by == 'quarter':
            key = ((y * 12 + m - 1) - (fiscal_month - 1)) // 3
        else:
            key = 0
        if buckets and buckets[-1][0] == key:
            buckets[-1][1].append(piece)
        else:
            buckets.append([key, [piece]])
    groups = []
    for _, members in buckets:
        first, last = members[0][1], members[-1][2]
        groups.append(Group(_range_label(first, last), first.isoformat(), last.isoformat(),
                            [(month, (end - start).days + 1, days) for month, start, end, days in members]))
    if len(groups) > 1:
        groups.append(Group('Total', date_from, date_to,
                            [(month, (end - start).days + 1, days) for month, start, end, days in pieces], total=True))
    return groups


# ---------------------------------------------------------------------------- actual figures

def _dimension_scope(dimension: str | None) -> str:
    if dimension == 'customer':
        return " AND l.name_type='customer' AND l.name_id IN (SELECT value FROM json_each(:dimension_ids))"
    if dimension == 'class':
        return ' AND l.class_id IN (SELECT value FROM json_each(:dimension_ids))'
    return ''


def actual_figures(raw, accounts: dict[str, dict], kind: str, date_from: str, date_to: str,
                   dimension: str | None = None, dimension_ids: list[str] | None = None) -> dict[str, int]:
    """Statement-signed figures per account: the period net (profit and loss) or the closing balance.

    Read through ``ledger_reports._effects`` -- the profit and loss's and the balance sheet's own
    effects query -- narrowed only by the dimension a customer or class budget names.
    """
    query = ledger._effects(ledger.ONE_ACCOUNT + _dimension_scope(dimension)) + \
        'SELECT account_id, closing, debits, credits FROM balances'
    params = {'date_from': date_from, 'date_to': date_to, 'account': None,
              'dimension_ids': json.dumps(dimension_ids or [])}
    sections = B.sections_for(kind)
    out = {}
    for account_id, closing, debits, credits in raw.execute(query, params):
        account = accounts.get(account_id)
        if account is None or B.section_of(account['type']) not in sections:
            continue
        net = int(debits) - int(credits) if kind == 'profit_and_loss' else int(closing)
        if net:
            out[account_id] = B.statement_sign(account['type']) * net
    return out


def _split_figures(raw, accounts, date_from: str, date_to: str, dimension: str) -> dict[tuple[str, str], int]:
    """Profit and loss nets per account and customer:job or class, for one period."""
    expression = "CASE WHEN l.name_type='customer' THEN l.name_id END" if dimension == 'customer' else 'l.class_id'
    query = f"""SELECT l.account_id, {expression} AS dimension,
        bookflow_sum_int(l.debit_minor_units - l.credit_minor_units) AS net
        FROM posting_lines l JOIN posting_batches b ON b.id = l.batch_id
        WHERE b.effective_date >= :date_from AND b.effective_date <= :date_to
        GROUP BY l.account_id, {expression}"""
    out = {}
    for account_id, dim, net in raw.execute(query, {'date_from': date_from, 'date_to': date_to}):
        account = accounts.get(account_id)
        if dim is None or account is None or B.section_of(account['type']) not in B.PL_SECTIONS or not int(net):
            continue
        out[(account_id, dim)] = out.get((account_id, dim), 0) + B.statement_sign(account['type']) * int(net)
    return out


def previous_year_actuals(s, inp, budget: dict[str, Any]) -> dict[tuple[str, str, str], int]:
    """The anchor's "create budget from previous year's actual data": month by month, the year before.

    A profit and loss budget takes each month's net; a balance sheet budget takes each month's
    ending balance. Accounts that are inactive are left out, as the anchor's grid lists only
    active accounts. Returned by the new budget's own months.
    """
    from bookflow.company.report_basis import resolve
    ledger.register_ledger_functions(s.company)
    basis = inp.basis or resolve(s.company, inp, 'profit-and-loss')
    raw = reporting_connection(s.company, basis)
    accounts = {row['id']: row for row in B.accounts_in_order(s.company.raw) if row['active']}
    out = {}
    for month in B.months_of(budget['first_month']):
        y, m = int(month[:4]), int(month[5:7])
        first, last = date(y - 1, m, 1).isoformat(), _month_end(y - 1, m).isoformat()
        if budget['dimension'] == 'none':
            for account_id, amount in actual_figures(raw, accounts, budget['kind'], first, last).items():
                out[(account_id, '', month)] = amount
        else:
            for (account_id, dim), amount in _split_figures(raw, accounts, first, last, budget['dimension']).items():
                out[(account_id, dim, month)] = amount
    return out


# ---------------------------------------------------------------------------- cells

def _money_cell(measure: str, units: int, currency: str) -> ComparisonCell:
    value = ledger.money(units, currency)
    return ComparisonCell(measure=measure, amount=value.amount, currency=currency, minor_units=value.minor_units)


def percent_text(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    tenths = round(Fraction(numerator * 1000, denominator))
    sign = '-' if tenths < 0 else ''
    return f'{sign}{abs(tenths) // 10}.{abs(tenths) % 10}'


def _percent_cell(measure: str, numerator: int, denominator: int) -> ComparisonCell:
    return ComparisonCell(measure=measure, amount=percent_text(numerator, denominator), currency=None, minor_units=None)


def _measure_cells(mode: str, first: int, second: int, currency: str, flags: tuple[bool, bool]) -> list[ComparisonCell]:
    if mode == 'overview':
        return [_money_cell('budget', first, currency)]
    if mode == 'budget':
        return [_money_cell('actual', first, currency), _money_cell('budget', second, currency),
                _money_cell('over_budget', first - second, currency), _percent_cell('percent_of_budget', first, second)]
    cells = [_money_cell('current', first, currency), _money_cell('previous', second, currency)]
    if flags[0]:
        cells.append(_money_cell('change', first - second, currency))
    if flags[1]:
        cells.append(_percent_cell('percent_change', first - second, abs(second)))
    return cells


def _measures(mode: str, flags: tuple[bool, bool]) -> list[str]:
    if mode == 'overview':
        return ['budget']
    if mode == 'budget':
        return ['actual', 'budget', 'over_budget', 'percent_of_budget']
    return ['current', 'previous'] + (['change'] if flags[0] else []) + (['percent_change'] if flags[1] else [])


def pl_lines(sections: dict[str, int]) -> dict[str, int]:
    out = dict(sections)
    out['gross_profit'] = out['income'] - out['cost_of_goods_sold']
    out['net_operating_income'] = out['gross_profit'] - out['expense']
    out['net_income'] = out['net_operating_income'] + out['other_income'] - out['other_expense']
    return out


# ---------------------------------------------------------------------------- the statement

class _ReportInput:
    """The report's own input with the dates it resolved, as the continuation machinery reads them."""

    def __init__(self, inp, **resolved):
        self._inp, self._resolved = inp, resolved
        for key, value in resolved.items():
            setattr(self, key, value)

    def __getattr__(self, name):
        return getattr(self._inp, name)

    def model_dump(self, **kwargs):
        return {**self._inp.model_dump(**kwargs), **self._resolved}


@dataclass
class _Series:
    """Per column group, two base figures per account (and the summary lines' own, where not additive)."""
    first: list[dict[str, int]]
    second: list[dict[str, int]]
    lines_first: list[dict[str, int]] | None = None
    lines_second: list[dict[str, int]] | None = None


def _statement(s, inp, *, report: str, kind: str, mode: str, groups: list[Group], series_of, principal_id,
               flags=(True, True), include_zero=False, budgeted=frozenset(), period_label=None, extra=None):
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, report, principal_id, None)
        currency = state.metadata.currency
        raw = reporting_connection(s.company, state.metadata.basis)
        accounts_list = B.accounts_in_order(s.company.raw)
        accounts = {row['id']: row for row in accounts_list}
        series: _Series = series_of(raw, accounts)
        numbers, lowest = B.display_preferences(s.company.raw)
        order = B.sections_for(kind)
        present = set(budgeted)
        for values in (*series.first, *series.second):
            present.update(account for account, amount in values.items() if amount)
        chosen = [row for row in accounts_list
                  if B.section_of(row['type']) in order
                  and (row['id'] in present or (include_zero and row['active']))]
        chosen.sort(key=lambda row: order.index(B.section_of(row['type'])))  # stable: account order within
        measures = _measures(mode, flags)
        columns = []
        for index, group in enumerate(groups):
            period = period_label(index, group) if period_label else group.label
            for measure in measures:
                if mode == 'overview' or measure == 'current':
                    label = period
                elif measure == 'previous':
                    label = period_label(index, group, previous=True)
                elif mode == 'compare':
                    label = MEASURE_LABELS[measure]
                else:
                    label = f'{period} · {MEASURE_LABELS[measure]}'
                columns.append(ReportColumn(label=label, period=period, measure=measure,
                                            date_from=group.date_from or None, date_to=group.date_to))
        rows = []
        for row in chosen[offset:offset + inp.limit]:
            cells = []
            for index in range(len(groups)):
                cells += _measure_cells(mode, series.first[index].get(row['id'], 0),
                                        series.second[index].get(row['id'], 0), currency, flags)
            rows.append(ComparisonRow(
                account_id=row['id'], current_account_label=row['full_name'], current_account_name=row['name'],
                current_account_number=row['number'],
                display_account_label=ledger._account_display(row['full_name'], row['name'], row['number'], numbers, lowest),
                account_type=row['type'], parent_id=row['parent_id'], active=bool(row['active']),
                section=B.section_of(row['type']), cells=cells))
        # The statement lines over every account, not just this page.
        line_specs = PL_LINES if kind == 'profit_and_loss' else (BS_LINES if mode == 'compare' else BS_BUDGET_LINES)
        lines_first = series.lines_first or [_section_lines(kind, values, accounts) for values in series.first]
        lines_second = series.lines_second or [_section_lines(kind, values, accounts) for values in series.second]
        summary, totals = [], {}
        for key, label in line_specs:
            cells = []
            for index in range(len(groups)):
                cells += _measure_cells(mode, lines_first[index][key], lines_second[index][key], currency, flags)
            summary.append(SummaryLine(key=key, label=label, cells=cells))
            last = cells[-len(measures):]
            totals[key] = {cell.measure: cell for cell in last}
        more = offset + inp.limit < len(chosen)
        result = dict(metadata=state.metadata, columns=columns, rows=rows, count=len(rows), summary=summary,
                      totals=totals, next_cursor=ledger._continuation(state, offset, len(rows), more, s.company),
                      **(extra or {}))
        return result


def _section_lines(kind: str, values: dict[str, int], accounts) -> dict[str, int]:
    sections = dict.fromkeys(B.sections_for(kind), 0)
    for account_id, amount in values.items():
        section = B.section_of(accounts[account_id]['type'])
        if section in sections:
            sections[section] += amount
    return pl_lines(sections) if kind == 'profit_and_loss' else sections


# ---------------------------------------------------------------------------- budget reports

def _budget_scope(s, inp, budget):
    """The dimension key the report is narrowed to, and the ids its actuals are read for."""
    customer, klass = B._dimension(s, budget, inp, required=False)
    key = customer or klass
    if budget['dimension'] == 'none':
        return None, None
    stored = B.cells(s, budget['id'])
    ids = [key] if key else sorted({k[1] for k in stored if k[1]})
    return key, ids


def _budget_values(stored, budget, group: Group, key: str | None) -> dict[str, int]:
    """Budget per account over one column group: prorated month amounts, or the ending balance."""
    out: dict[str, int] = {}
    by_cell: dict[tuple[str, str], int] = {}
    for (account_id, dim, month), row in stored.items():
        if key is not None and dim != key:
            continue
        by_cell[(account_id, month)] = by_cell.get((account_id, month), 0) + row['amount_minor_units']
    if budget['kind'] == 'balance_sheet':
        last = group.months[-1][0] if group.months else None
        for (account_id, month), amount in by_cell.items():
            if month == last:
                out[account_id] = out.get(account_id, 0) + amount
        return out
    for month, covered, days in group.months:
        for (account_id, cell_month), amount in by_cell.items():
            if cell_month != month:
                continue
            share = amount if covered == days else round(Fraction(amount * covered, days))
            out[account_id] = out.get(account_id, 0) + share
    return out


def _budget_bounds(budget) -> tuple[str, str]:
    months = B.months_of(budget['first_month'])
    last = months[-1]
    return f'{months[0]}-01', _month_end(int(last[:4]), int(last[5:7])).isoformat()


def budget_overview(inp, s, *, principal_id=None) -> BudgetOverviewOutput:
    budget = B.resolve_budget(s, inp.budget)
    key, _ = _budget_scope(s, inp, budget)
    stored = B.cells(s, budget['id'])
    first, last = _budget_bounds(budget)
    groups = groups_for(first, last, inp.columns, int(budget['first_month'][5:7]))
    values = [_budget_values(stored, budget, group, key) for group in groups]
    budgeted = {k[0] for k in stored if key is None or k[1] == key}
    state = _ReportInput(inp, date_from=first, date_to=last, basis=None, include_zero=False)
    result = _statement(s, state, report='budget-overview', kind=budget['kind'], mode='overview', groups=groups,
                        series_of=lambda raw, accounts: _Series(values, [{} for _ in groups]),
                        principal_id=principal_id, budgeted=budgeted,
                        extra=dict(budget=B.budget_output(budget, len(stored))))
    return BudgetOverviewOutput(**result)


def budget_vs_actual(inp, s, *, principal_id=None) -> BudgetVsActualOutput:
    budget = B.resolve_budget(s, inp.budget)
    key, ids = _budget_scope(s, inp, budget)
    stored = B.cells(s, budget['id'])
    first, last = _budget_bounds(budget)
    date_from, date_to = inp.date_from or first, inp.date_to or last
    if date_from > date_to:
        raise B._invalid('date_from', 'must be on or before date_to')
    groups = groups_for(date_from, date_to, inp.columns, B.fiscal_start_month(s.company.raw))
    budgets = [_budget_values(stored, budget, group, key) for group in groups]
    budgeted = {account for values in budgets for account, amount in values.items() if amount}

    def series(raw, accounts):
        actual = [actual_figures(raw, accounts, budget['kind'], group.date_from, group.date_to,
                                 None if budget['dimension'] == 'none' else budget['dimension'], ids)
                  for group in groups]
        return _Series(actual, budgets)

    state = _ReportInput(inp, date_from=date_from, date_to=date_to)
    result = _statement(s, state, report='budget-vs-actual', kind=budget['kind'], mode='budget', groups=groups,
                        series_of=series, principal_id=principal_id, include_zero=inp.include_zero,
                        budgeted=budgeted, extra=dict(budget=B.budget_output(budget, len(stored))))
    return BudgetVsActualOutput(**result)


# ---------------------------------------------------------------------------- comparisons

def profit_and_loss_comparison(inp, s, *, principal_id=None) -> ProfitAndLossComparisonOutput:
    earlier = previous_year if inp.compare == 'previous_year' else previous_period
    try:
        prev_from, prev_to = earlier(inp.date_from, inp.date_to)
    except (ValueError, OverflowError):
        raise B._invalid('date_from', 'the earlier period would begin before the year 1') from None
    group = Group(_range_label(_d(inp.date_from), _d(inp.date_to)), inp.date_from, inp.date_to)
    prev_label = _range_label(_d(prev_from), _d(prev_to))

    def label(index, group, previous=False):
        return prev_label if previous else group.label

    def series(raw, accounts):
        current = actual_figures(raw, accounts, 'profit_and_loss', inp.date_from, inp.date_to)
        # The earlier period through the same projection, its own cutoffs.
        previous = actual_figures(raw, accounts, 'profit_and_loss', prev_from, prev_to)
        return _Series([current], [previous])

    result = _statement(s, inp, report='profit-and-loss-comparison', kind='profit_and_loss', mode='compare',
                        groups=[group], series_of=series, principal_id=principal_id,
                        flags=(inp.dollar_change, inp.percent_change), include_zero=inp.include_zero,
                        period_label=label, extra=dict(compare=inp.compare))
    return ProfitAndLossComparisonOutput(**result)


def _balance_sheet_figures(raw, accounts, date_to: str, fiscal_month: int) -> tuple[dict[str, int], dict[str, int]]:
    """Closing balances on a date, and the balance sheet's lines, read as ``report balance-sheet`` reads them."""
    year = int(date_to[:4]) - (int(date_to[5:7]) < fiscal_month)
    fiscal_start = f'{year:04}-{fiscal_month:02}-01' if year >= 1 else '0001-01-01'
    params = {'date_from': fiscal_start, 'date_to': date_to, 'account': None}
    figures, prior, current = {}, 0, 0
    sections = dict.fromkeys(B.BS_SECTIONS, 0)
    for account_id, opening, closing in raw.execute(ledger._EFFECTS + 'SELECT account_id, opening, closing FROM balances', params):
        account = accounts.get(account_id)
        if account is None:
            continue
        section = B.section_of(account['type'])
        if section in B.PL_SECTIONS:
            prior -= int(opening)
            current -= int(closing) - int(opening)
        elif section in sections and int(closing):
            figures[account_id] = B.statement_sign(account['type']) * int(closing)
            sections[section] += figures[account_id]
    lines = dict(assets=sections['assets'], liabilities=sections['liabilities'], posted_equity=sections['equity'],
                 prior_earnings=prior, current_year_income=current)
    lines['total_equity'] = lines['posted_equity'] + prior + current
    lines['liabilities_and_equity'] = lines['liabilities'] + lines['total_equity']
    return figures, lines


def balance_sheet_comparison(inp, s, *, principal_id=None) -> BalanceSheetComparisonOutput:
    if inp.date_to < '0002-01-01':
        raise B._invalid('date_to', 'the year before would be before the year 1')
    prev_to = _shift_year(_d(inp.date_to))
    if _d(inp.date_to) == _month_end(_d(inp.date_to).year, _d(inp.date_to).month):
        prev_to = _month_end(prev_to.year, prev_to.month)
    prev_to = prev_to.isoformat()
    label_now = _range_label(_d(inp.date_to), _d(inp.date_to))
    label_prev = _range_label(_d(prev_to), _d(prev_to))
    group = Group(label_now, '', inp.date_to)

    def label(index, group, previous=False):
        return label_prev if previous else label_now

    def series(raw, accounts):
        month = B.fiscal_start_month(s.company.raw)
        now, lines_now = _balance_sheet_figures(raw, accounts, inp.date_to, month)
        # The cash projection is bound to the request's dates, so the earlier date reads its own.
        before, lines_before = _balance_sheet_figures(raw, accounts, prev_to, month)
        return _Series([now], [before], [lines_now], [lines_before])

    result = _statement(s, inp, report='balance-sheet-comparison', kind='balance_sheet', mode='compare',
                        groups=[group], series_of=series, principal_id=principal_id,
                        flags=(inp.dollar_change, inp.percent_change), include_zero=inp.include_zero,
                        period_label=label, extra=dict(compare='previous_year'))
    return BalanceSheetComparisonOutput(**result)


