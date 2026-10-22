"""Budgets, the budget and comparison reports, and memorized reports in the workbench (R109).

* **Set Up Budgets** (`/c/<company>/budget/<id>`): the anchor's grid -- accounts down, months across,
  an annual total -- where a row is typed month by month and saved with `budget set`, a row is filled
  from a starting amount with a monthly change (`budget fill`), and the budget is cleared. On a phone
  each account is one card of twelve labelled months, one account at a time.
* **The four reports** read as one wide table: a header row of column groups (a month, a quarter, the
  Total) over the measures in each, the account rows, then the statement lines. Every figure is one
  the command returned; nothing here adds anything up.
* **Memorize** on any report page keeps the report's filters under a name (`memorized-report
  create`), offering the named date range the page's dates match, and **Open** runs a memorized
  report on today's dates by sending the reader to the report page with the resolved filters.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.convertors import Convertor, register_url_convertor

from bookflow.core.errors import BookflowError

REPORTS = {'report budget-overview', 'report budget-vs-actual', 'report profit-and-loss-comparison',
           'report balance-sheet-comparison'}
BUDGET_TYPES = {
    'profit_and_loss': ('income', 'cost_of_goods_sold', 'expense', 'other_income', 'other_expense'),
}
SECTION_LABELS = {'income': 'Income', 'cost_of_goods_sold': 'Cost of goods sold', 'expense': 'Expense',
                  'other_income': 'Other income', 'other_expense': 'Other expense', 'assets': 'Assets',
                  'liabilities': 'Liabilities', 'equity': 'Equity'}
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
RANGE_LABELS = {
    'today': 'Today', 'yesterday': 'Yesterday', 'this_week': 'This week', 'this_week_to_date': 'This week to date',
    'last_week': 'Last week', 'this_month': 'This month', 'this_month_to_date': 'This month to date',
    'last_month': 'Last month', 'last_month_to_date': 'Last month to date', 'next_month': 'Next month',
    'this_fiscal_quarter': 'This fiscal quarter', 'this_fiscal_quarter_to_date': 'This fiscal quarter to date',
    'last_fiscal_quarter': 'Last fiscal quarter', 'next_fiscal_quarter': 'Next fiscal quarter',
    'this_fiscal_year': 'This fiscal year', 'this_fiscal_year_to_date': 'This fiscal year to date',
    'last_fiscal_year': 'Last fiscal year', 'last_fiscal_year_to_date': 'Last fiscal year to date',
    'next_fiscal_year': 'Next fiscal year', 'this_calendar_year': 'This calendar year',
    'last_calendar_year': 'Last calendar year',
}


class UlidConvertor(Convertor):
    regex = '[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{26}'

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor('ulid', UlidConvertor())


def month_label(month: str) -> str:
    return f'{MONTHS[int(month[5:7]) - 1]} {month[2:4]}'


# ---------------------------------------------------------------------------- the reports

def view(result: dict, inputs: dict, company_id: str, command: str) -> dict:
    """Column groups over measures, rows and statement lines, as the template walks them."""
    columns = result['columns']
    groups = []
    for column in columns:
        if groups and groups[-1]['period'] == column['period'] and column['measure'] not in ('current', 'previous'):
            groups[-1]['span'] += 1
        elif groups and groups[-1]['period'] == column['period'] and result.get('compare'):
            groups[-1]['span'] += 1
        else:
            groups.append({'period': column['period'], 'span': 1})
    compare = 'compare' in result
    heads = [column['label'] if compare else column['label'].split(' · ')[-1] for column in columns]
    if compare:
        groups = []  # a single period reads its own headings: this period, the earlier one, the change
    rows = []
    for row in result['rows']:
        rows.append({**row, 'section_label': SECTION_LABELS.get(row['section'], row['section'])})
    next_fields = {f'f:{key}': (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != 'cursor' and value is not None}
    next_fields['f:cursor'] = result['next_cursor']
    budget = result.get('budget')
    return {**result, 'groups': groups, 'heads': heads, 'rows': rows, 'next_fields': next_fields,
            'kind': command.split(' ', 1)[1], 'budget_url': f"/c/{company_id}/budget/{budget['id']}" if budget else None}


# ---------------------------------------------------------------------------- the grid

def _grid_context(run, request, company_id, budget_id, dimension):
    raw = {'budget': budget_id}
    budget = run(request, 'budget show', raw, company_id)
    key = 'customer' if budget['dimension'] == 'customer' else 'class_id' if budget['dimension'] == 'class' else None
    if key and dimension:
        budget = run(request, 'budget show', {**raw, key: dimension}, company_id)
    kind = budget['kind']
    accounts = run(request, 'account query', {'limit': 200}, company_id).get('items', [])
    pl = BUDGET_TYPES['profit_and_loss']
    wanted = [a for a in accounts if a.get('active', True)
              and ((a['type'] in pl) if kind == 'profit_and_loss' else (a['type'] not in pl and a['type'] != 'non_posting'))]
    stored = {row['account_id']: row for row in budget['rows']}
    rows = []
    for account in wanted:
        row = stored.get(account['id'])
        amounts = [cell['amount'] for cell in row['amounts']] if row else [''] * 12
        rows.append({'account_id': account['id'], 'label': account.get('full_name') or account.get('name'),
                     'type': account['type'], 'amounts': ['' if a in ('0.00', '0') else a for a in amounts],
                     'total': row['total']['amount'] if row else ''})
    # An account that holds an amount but is no longer listed (inactive) still shows, read only.
    listed = {row['account_id'] for row in rows}
    for account_id, row in stored.items():
        if account_id not in listed:
            rows.append({'account_id': account_id, 'label': row['current_account_label'], 'type': row['account_type'],
                         'amounts': [cell['amount'] for cell in row['amounts']], 'total': row['total']['amount'],
                         'inactive': True})
    return dict(budget=budget, rows=rows, months=[(m, month_label(m)) for m in budget['months']],
                dimension_key=key, dimension=dimension or '')


def mount(app, *, render, run, credential, page_error):
    def grid(request, company_id, budget_id, *, dimension=None, notice=None, error=None, values=None, status_code=200):
        context = _grid_context(run, request, company_id, budget_id, dimension)
        company = run(request, 'company show', {}, company_id)
        return render('budget_grid.html', request, status_code=status_code, company_id=company_id,
                      company_label=company.get('display_name') or company.get('legal_name'), notice=notice,
                      error=error, values=values or {}, **context)

    @app.get('/c/{company_id}/budget/{budget_id:ulid}')
    def budget_grid(request: Request, company_id: str, budget_id: str):
        try:
            return grid(request, company_id, budget_id, dimension=request.query_params.get('dimension') or None)
        except BookflowError as e:
            return page_error(request, e)

    def save(request, company_id, budget_id, form):
        dimension = (form.get('dimension') or '').strip() or None
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        context = _grid_context(run, request, company_id, budget_id, dimension)
        key, budget = context['dimension_key'], context['budget']
        extra = {key: dimension} if key and dimension else {}
        action = form.get('action')
        try:
            if key and not dimension and action in ('save', 'fill'):
                raise BookflowError('E_VALIDATION', message='Choose the customer or class these amounts are for.',
                                    details={'fields': [{'field': key, 'problem': 'choose one first'}]})
            changed = 0
            if action == 'save':
                version = int(form.get('version') or budget['version'])
                for row in context['rows']:
                    if row.get('inactive'):
                        continue
                    typed = [(form.get(f"m:{row['account_id']}:{index}") or '').strip() for index in range(12)]
                    if [t.replace(',', '') for t in typed] == row['amounts']:
                        continue
                    raw = {'budget': budget_id, 'account': row['account_id'],
                           'amounts': [t.replace(',', '') for t in typed], **extra}
                    if not changed:
                        raw['expected_version'] = version
                    changed += run(request, 'budget set', raw, company_id, headers=headers)['changed_cells']
                notice = f"Saved {changed} month amount{'s' if changed != 1 else ''}." if changed else 'Nothing changed.'
            elif action == 'fill':
                raw = {'budget': budget_id, 'account': form.get('fill_account'), **extra}
                for field in ('start_month', 'amount', 'change', 'change_percent'):
                    value = (form.get('fill_' + field) or '').strip()
                    if value:
                        raw[field] = value
                changed = run(request, 'budget fill', raw, company_id, headers=headers)['changed_cells']
                notice = f"Filled the row: {changed} month amount{'s' if changed != 1 else ''} changed."
            elif action == 'clear':
                changed = run(request, 'budget clear', {'budget': budget_id, **extra}, company_id, headers=headers)['changed_cells']
                notice = f"Cleared {changed} month amount{'s' if changed != 1 else ''}."
            else:
                notice = None
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            return grid(request, company_id, budget_id, dimension=dimension, error=e.to_dict(), values=form, status_code=400)
        return grid(request, company_id, budget_id, dimension=dimension, notice=notice)

    @app.post('/c/{company_id}/budget/{budget_id:ulid}')
    async def budget_grid_save(request: Request, company_id: str, budget_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(save, request, company_id, budget_id, form)
        except BookflowError as e:
            return page_error(request, e)

    # ------------------------------------------------------------------------ memorize

    def memorize_page(request, company_id, verb, filters, *, error=None, values=None, status_code=200):
        from datetime import date
        from bookflow.adapters.workbench import naming
        from bookflow.company.budget_models import DATE_RANGES
        from bookflow.company.memorized_reports import resolve_range
        company = run(request, 'company show', {}, company_id)
        info = company.get('info') or {}
        from bookflow.adapters.workbench import date_defaults
        today = date.fromisoformat(date_defaults.company_today(company))
        fiscal = int(info.get('fiscal_year_start_month') or 1)
        matched = None
        first, last = filters.get('date_from'), filters.get('date_to') or filters.get('as_of')
        for name in DATE_RANGES:
            start, end = resolve_range(name, today, fiscal)
            if last == end.isoformat() and (first is None or first == start.isoformat()):
                matched = name
                break
        dated = first is not None or last is not None
        return render('memorize_report.html', request, status_code=status_code, company_id=company_id,
                      company_label=company.get('display_name') or company.get('legal_name'),
                      verb=verb, title=naming.heading('report', verb), filters=filters, dated=dated, matched=matched,
                      ranges=[(name, RANGE_LABELS[name]) for name in DATE_RANGES], first=first, last=last,
                      error=error, values=values or {})

    def _filters(verb, params):
        from bookflow.adapters.workbench.report_export import inputs_from
        from bookflow.core import registry
        cmd = registry.get(f'report {verb}')
        if cmd is None:
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'report', 'problem': 'unknown report'}]})
        return {key: value for key, value in inputs_from(cmd, params).items() if key not in ('cursor', 'limit')}

    @app.get('/c/{company_id}/report/{verb}/memorize')
    def memorize(request: Request, company_id: str, verb: str):
        try:
            return memorize_page(request, company_id, verb, _filters(verb, request.query_params))
        except BookflowError as e:
            return page_error(request, e)

    def memorize_save(request, company_id, verb, form):
        filters = _filters(verb, {key: value for key, value in form.items() if key.startswith('f:')})
        raw = {'name': (form.get('name') or '').strip(), 'report': verb, 'filters': filters}
        if (form.get('group') or '').strip():
            raw['group'] = form['group'].strip()
        if form.get('date_range'):
            raw['date_range'] = form['date_range']
        try:
            run(request, 'memorized-report create', raw, company_id)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            return memorize_page(request, company_id, verb, filters, error=e.to_dict(), values=form, status_code=400)
        return RedirectResponse(f'/c/{company_id}/memorized-report', status_code=303)

    @app.post('/c/{company_id}/report/{verb}/memorize')
    async def memorize_submit(request: Request, company_id: str, verb: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(memorize_save, request, company_id, verb, form)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/memorized-report')
    def memorized_list(request: Request, company_id: str):
        try:
            company = run(request, 'company show', {}, company_id)
            items, cursor = [], None
            while True:
                page = run(request, 'memorized-report list', {'limit': 200, **({'cursor': cursor} if cursor else {})}, company_id)
                items += page['items']
                cursor = page['next_cursor']
                if not cursor:
                    break
        except BookflowError as e:
            return page_error(request, e)
        groups: list[tuple[str, list]] = []
        for item in items:
            name = item['group'] or 'Not in a group'
            if not groups or groups[-1][0] != name:
                groups.append((name, []))
            groups[-1][1].append(item)
        return render('memorized_reports.html', request, company_id=company_id,
                      company_label=company.get('display_name') or company.get('legal_name'), groups=groups,
                      ranges=RANGE_LABELS)

    @app.get('/c/{company_id}/memorized-report/{record_id:ulid}/open')
    def memorized_open(request: Request, company_id: str, record_id: str):
        from bookflow.adapters.workbench import report_print
        try:
            ran = run(request, 'memorized-report run', {'memorized_report': record_id, 'limit': 1}, company_id)
        except BookflowError as e:
            return page_error(request, e)
        verb = ran['command'].split(' ', 1)[1]
        return RedirectResponse(report_print.return_url(company_id, verb, ran['inputs']), status_code=303)


def memorize_url(company_id: str, verb: str, inputs: dict) -> str:
    from bookflow.adapters.workbench.report_export import export_url
    url = export_url(company_id, verb, inputs)
    return re.sub(r'/export\.csv(\?|$)', r'/memorize\1', url, count=1)


__all__ = ['REPORTS', 'mount', 'view', 'memorize_url', 'urlencode']
