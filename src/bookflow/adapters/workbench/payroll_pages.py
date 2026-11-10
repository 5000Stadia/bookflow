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


def _rate(text, field):
    """A percentage typed as text ('6.2') in exact millionths of a percent; no float ever."""
    text = (text or '').strip()
    if not re.fullmatch(r'\d{1,3}(\.\d{1,6})?', text):
        raise BookflowError('E_VALIDATION', message='Enter a percentage with at most six decimal places.',
                            details={'fields': [{'field': field, 'problem': 'Use a percentage such as 6.2.'}]})
    whole, _, fraction = text.partition('.')
    return int(whole) * 1_000_000 + int(fraction.ljust(6, '0'))


def _percent(millionths):
    whole, fraction = divmod(millionths, 1_000_000)
    return f'{whole}.{fraction:06d}'.rstrip('0').rstrip('.')


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
        values[f'i{i}_method'] = row.get('method') or 'fixed'
        if row.get('amount_minor_units') is not None:
            values[f'i{i}_amount'] = _money(row['amount_minor_units'])
        if row.get('rate_millionths') is not None:
            values[f'i{i}_rate'] = _percent(row['rate_millionths'])
            values[f'i{i}_base'] = ', '.join(row.get('base') or ())
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
        if not any(form.get(prefix + k) for k in ('code', 'amount', 'rate', 'liability_account', 'payee', 'source_note')):
            continue
        row = {k: (form.get(prefix + k) or '').strip() for k in ('code', 'kind', 'liability_account', 'payee', 'source_note')}
        if form.get(prefix + 'method') == 'percent':
            row.update(method='percent', rate_millionths=_rate(form.get(prefix + 'rate'), prefix + 'rate'),
                       base=[code.strip() for code in (form.get(prefix + 'base') or '').split(',') if code.strip()])
        else:
            row['amount_minor_units'] = _cents(form.get(prefix + 'amount'), prefix + 'amount')
        raw['items'].append(row)
    return raw


def net_check(form):
    check = {'account': (form.get('check_account') or '').strip(), 'date': form.get('check_date') or None,
             'to_be_printed': form.get('check_print') == 'yes'}
    if (form.get('check_number') or '').strip() and not check['to_be_printed']:
        check['number'] = form['check_number'].strip()
    return check


def mount(app, *, render, run, credential, page_error, role_allows):
    def directive_link(request, company_id, directive):
        """Where a recorded directive may be opened: its page only for a reader who may see it, else nothing."""
        if not directive:
            return None
        try:
            run(request, 'directive show', {'directive': directive}, company_id)
        except BookflowError:
            return False
        return f'/c/{company_id}/directive/{directive}'

    def page(request, company_id, *, values=None, draft_id=None, error=None, result=None, status_code=200,
             paycheck_id=None):
        company = run(request, 'company show', {}, company_id)
        # Execute a private read before rendering any supplied form data, even after a refused write.
        settings = run(request, 'payroll settings', {}, company_id)
        drafts = run(request, 'payroll list', {}, company_id)['rows']
        draft_id = draft_id or request.query_params.get('draft')
        draft = run(request, 'payroll show', {'draft': draft_id}, company_id) if draft_id else None
        paychecks = run(request, 'payroll paychecks', {}, company_id)['rows']
        paycheck_id = paycheck_id or request.query_params.get('paycheck') or (draft or {}).get('paycheck')
        paycheck = run(request, 'payroll paycheck', {'paycheck': paycheck_id}, company_id) if paycheck_id else None
        allowed = lambda name: role_allows(name, company, credential(request))
        if values is None:
            values = draft_values(draft) if draft else {}
        response = render('payroll.html', request, status_code=status_code, company=company,
                          company_id=company['company_id'], settings=settings, drafts=drafts, draft=draft,
                          values=values, error=error, result=result, money=_money,
                          earning_count=_count(values, 'earning_count', 1), item_count=_count(values, 'item_count', 2),
                          operation_key=values.get('operation_key') or uuid4().hex,
                          paychecks=paychecks, paycheck=paycheck, percent=_percent,
                          draft_directive=directive_link(request, company_id, (draft or {}).get('directive')),
                          paycheck_directive=directive_link(request, company_id, (paycheck or {}).get('directive')),
                          can_employees=allowed('employee list'), can_setup=allowed('payroll setup'),
                          can_prepare=allowed('payroll draft'), can_post=allowed('payroll post'),
                          can_pay=allowed('payroll pay-net'), can_void=allowed('payroll void'))
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
        paycheck_id = form.get('paycheck') or None
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
            elif action in ('post', 'post_pay'):
                raw = {'draft': draft_id, 'expected_version': int(form['expected_version']),
                       'operation_key': form['operation_key']}
                if action == 'post_pay':
                    raw.update({f'check_{key}': value for key, value in net_check(form).items()})
                paycheck_id = run(request, 'payroll post', raw, company_id, headers=headers)['id']
                result = 'Paycheck posted' + (' and its net-pay check recorded.' if action == 'post_pay' else '.')
            elif action in ('pay_net', 'void', 'void_payment'):
                raw = {'paycheck': paycheck_id, 'expected_version': int(form['expected_version']),
                       'operation_key': form['operation_key']}
                if action == 'pay_net':
                    raw.update(net_check(form))
                name = {'pay_net': 'payroll pay-net', 'void': 'payroll void', 'void_payment': 'payroll void-payment'}[action]
                run(request, name, raw, company_id, headers=headers)
                result = {'pay_net': 'Net-pay check recorded.', 'void': 'Paycheck voided.',
                          'void_payment': 'Net-pay check voided; the paycheck stays posted.'}[action]
            else:
                raise BookflowError('E_VALIDATION', message='Choose a payroll action.')
            response = page(request, company_id, draft_id=draft_id, result=result, paycheck_id=paycheck_id)
            query = f'paycheck={paycheck_id}' if paycheck_id and not draft_id else f'draft={draft_id}'
            response.headers['HX-Push-Url'] = f'/c/{company_id}/payroll?{query}'
            return response
        except (ValueError, KeyError):
            error = BookflowError('E_VALIDATION', message='The form is incomplete. Reopen the draft and try again.')
        except BookflowError as caught:
            error = caught
        if error.code in ('E_PERMISSION', 'E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
            return page_error(request, error)
        # A refused save echoes the typed inputs; a refused post, payment or void reopens the saved records.
        values = form if form.get('action') in ('save', 'setup') else None
        return page(request, company_id, values=values, draft_id=draft_id, error=error.to_dict(), status_code=400,
                    paycheck_id=paycheck_id)

    @app.post('/c/{company_id}/payroll')
    async def payroll_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as error:
            return page_error(request, error)
