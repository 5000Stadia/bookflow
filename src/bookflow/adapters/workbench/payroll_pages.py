"""Private manual payroll workbench; all reads and writes use payroll commands."""
from __future__ import annotations

import re
from uuid import uuid4

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError


def _cents(text, field):
    text = (text or '').strip()
    if not re.fullmatch(r'\d{1,10}(\.\d{1,2})?', text):
        raise BookflowError('E_VALIDATION', message='Enter a nonnegative USD amount with at most two decimal places.',
                            details={'fields': [{'field': field, 'problem': 'Use dollars and cents.'}]})
    whole, _, fraction = text.partition('.')
    return int(whole) * 100 + int(fraction.ljust(2, '0'))


def _money(cents):
    return f'{cents // 100}.{cents % 100:02d}'


def _count(form, field, default):
    try:
        count = int(form.get(field, default))
    except (ValueError, TypeError):
        count = default
    return max(1, min(100, count))


def draft_values(draft):
    values = {key: draft[key] or '' for key in ('employee', 'period_start', 'period_end', 'pay_date')}
    values.update(draft=draft['id'], expected_version=draft['version'], taxes_confirmed=draft['taxes_confirmed'],
                  earning_count=max(1, len(draft['earnings'])), item_count=max(2, len(draft['items'])))
    for i, row in enumerate(draft['earnings']):
        for key in ('code', 'quantity', 'rate'):
            values[f'e{i}_{key}'] = row.get(key) or ''
        if row.get('amount_minor_units') is not None:
            values[f'e{i}_amount'] = _money(row['amount_minor_units'])
    for i, row in enumerate(draft['items']):
        for key in ('code', 'kind', 'liability_account', 'payee', 'source_note'):
            values[f'i{i}_{key}'] = row[key]
        values[f'i{i}_amount'] = _money(row['amount_minor_units'])
    return values


def draft_input(form):
    raw = {key: (form.get(key) or '').strip() for key in ('employee', 'period_start', 'period_end')}
    raw.update(draft=form.get('draft') or None, expected_version=int(form.get('expected_version') or 0),
               operation_key=form['operation_key'], pay_date=form.get('pay_date') or None,
               taxes_confirmed=form.get('taxes_confirmed') == 'yes', earnings=[], items=[])
    for i in range(_count(form, 'earning_count', 1)):
        prefix = f'e{i}_'
        if not any(form.get(prefix + k) for k in ('code', 'amount', 'quantity', 'rate')):
            continue
        row = {'code': form.get(prefix + 'code', '').strip()}
        if form.get(prefix + 'amount'):
            row['amount_minor_units'] = _cents(form[prefix + 'amount'], prefix + 'amount')
        for key in ('quantity', 'rate'):
            if form.get(prefix + key):
                row[key] = form[prefix + key].strip()
        raw['earnings'].append(row)
    for i in range(_count(form, 'item_count', 2)):
        prefix = f'i{i}_'
        if not any(form.get(prefix + k) for k in ('code', 'amount', 'liability_account', 'payee', 'source_note')):
            continue
        row = {k: (form.get(prefix + k) or '').strip() for k in ('code', 'kind', 'liability_account', 'payee', 'source_note')}
        row['amount_minor_units'] = _cents(form.get(prefix + 'amount'), prefix + 'amount')
        raw['items'].append(row)
    return raw


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, values=None, draft_id=None, error=None, result=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        # Execute a private read before rendering any supplied form data, even after a refused write.
        settings = run(request, 'payroll settings', {}, company_id)
        drafts = run(request, 'payroll list', {}, company_id)['rows']
        draft_id = draft_id or request.query_params.get('draft')
        draft = run(request, 'payroll show', {'draft': draft_id}, company_id) if draft_id else None
        if values is None:
            values = draft_values(draft) if draft else {}
        response = render('payroll.html', request, status_code=status_code, company=company,
                          company_id=company['company_id'], settings=settings, drafts=drafts, draft=draft,
                          values=values, error=error, result=result, money=_money,
                          earning_count=_count(values, 'earning_count', 1), item_count=_count(values, 'item_count', 2),
                          operation_key=values.get('operation_key') or uuid4().hex,
                          can_setup=role_allows('payroll setup', company, credential(request)),
                          can_prepare=role_allows('payroll draft', company, credential(request)))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/c/{company_id}/payroll')
    def payroll_page(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as error:
            return page_error(request, error)

    def submit(request, company_id, form):
        draft_id = form.get('draft') or None
        try:
            action = form.get('action')
            # Read authority is rechecked before echoing any posted private input.
            run(request, 'payroll settings', {}, company_id)
            if action in ('add_earning', 'add_item'):
                field = 'earning_count' if action == 'add_earning' else 'item_count'
                form[field] = min(100, _count(form, field, 1 if action == 'add_earning' else 2) + 1)
                return page(request, company_id, values=form, draft_id=draft_id)
            headers = {'X-Bookflow-Reason': (form.get('ctx:reason') or '').strip()}
            if action == 'setup':
                raw = dict(enabled=form.get('enabled') == 'yes', expected_version=int(form['settings_version']),
                           operation_key=form['operation_key'])
                raw.update({k: form.get(k) or None for k in ('wage_account', 'employer_tax_account', 'net_pay_account')})
                run(request, 'payroll setup', raw, company_id, headers=headers)
                return page(request, company_id, result='Payroll settings saved.')
            if action == 'save':
                draft = run(request, 'payroll draft', draft_input(form), company_id, headers=headers)
                draft_id = draft['id']
                result = 'Private draft saved. Calculate to review these inputs.'
            elif action == 'prepare':
                raw = {'draft': draft_id, 'expected_version': int(form['expected_version']),
                       'operation_key': form['operation_key']}
                run(request, 'payroll prepare', raw, company_id, headers=headers)
                result = 'Calculated from the saved inputs. Nothing posted.'
            else:
                raise BookflowError('E_VALIDATION', message='Choose a payroll action.')
            response = page(request, company_id, draft_id=draft_id, result=result)
            response.headers['HX-Push-Url'] = f'/c/{company_id}/payroll?draft={draft_id}'
            return response
        except (ValueError, KeyError):
            error = BookflowError('E_VALIDATION', message='The form is incomplete. Reopen the draft and try again.')
        except BookflowError as caught:
            error = caught
        if error.code in ('E_PERMISSION', 'E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
            return page_error(request, error)
        return page(request, company_id, values=form, draft_id=draft_id, error=error.to_dict(), status_code=400)

    @app.post('/c/{company_id}/payroll')
    async def payroll_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as error:
            return page_error(request, error)
