"""Bank rule browser surfaces; all decisions and previews belong to registry commands."""
from __future__ import annotations

import json
from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

FIELDS = ('name', 'account', 'direction', 'payee', 'payee_type', 'category', 'class_id',
          'memo', 'transfer_to', 'ignore_reason', 'priority')


def rule_values(rule):
    values = {key: '' if rule.get(key) is None else rule[key] for key in FIELDS}
    values['action'] = rule['action']
    values.update(account=rule.get('account_id') or '', transfer_to=rule.get('transfer_account_id') or '',
                  auto_add=rule['auto_add'])
    for i, condition in enumerate(rule['conditions']):
        for key in ('field', 'op', 'value', 'value_to'):
            values[f'condition_{i}_{key}'] = condition.get(key) or ''
    return values


def form_input(form):
    raw = {key: form[key].strip() for key in FIELDS if (form.get(key) or '').strip()}
    raw['action'] = form.get('rule_action') or 'categorise'
    raw['auto_add'] = form.get('auto_add') == 'on'
    raw['account'] = raw.get('account') or None
    if 'priority' in raw:
        try:
            raw['priority'] = int(raw['priority'])
        except ValueError:
            raise BookflowError('E_VALIDATION', message='Priority must be a whole number.')
    raw['conditions'] = []
    for i in range(10):
        if (form.get(f'condition_{i}_value') or '').strip():
            condition = {key: form.get(f'condition_{i}_{key}', '').strip() for key in ('field', 'op', 'value')}
            if (form.get(f'condition_{i}_value_to') or '').strip():
                condition['value_to'] = form[f'condition_{i}_value_to'].strip()
            raw['conditions'].append(condition)
    if form.get('rule'):
        raw.update(rule=form['rule'], expected_version=int(form['expected_version']))
        # Empty optional fields clear the previous choice on an edit.
        for key in ('payee', 'category', 'class_id', 'memo', 'transfer_to', 'ignore_reason'):
            raw.setdefault(key, None)
    return raw


def mount(app, *, render, run, credential, page_error, role_allows, automation_allowed):
    def page(request, company_id, *, values=None, selected=None, result=None, error=None, confirmation=None,
             preview=None, preview_payload=None, preview_text=''):
        company = run(request, 'company show', {}, company_id)
        query = {key: request.query_params[key] for key in ('account', 'status', 'source', 'cursor')
                 if request.query_params.get(key)}
        listed = run(request, 'bank-rule query', {**query, 'limit': 100}, company_id)
        selected = selected or request.query_params.get('rule')
        editing = run(request, 'bank-rule show', {'rule': selected}, company_id)['rule'] if selected else None
        if values is None:
            values = rule_values(editing) if editing else {}
        allowed = lambda command: role_allows(command, company, credential(request))
        from bookflow.adapters.workbench import home
        may_open = lambda path: home.page_allowed(path, lambda cmd: allowed(cmd.name))
        can_live = allowed('bank-rule update') and automation_allowed(request, company_id, 'automate.bank-rule')
        return render('bank_rules.html', request, company=company, company_id=company['company_id'],
                      rules=listed['rules'], next_cursor=listed.get('next_cursor'), filters=query,
                      values=values, editing=editing, result=result, error=error, confirmation=confirmation,
                      preview=preview, preview_payload=preview_payload, preview_text=preview_text,
                      can_write=allowed('bank-rule create'), can_live=can_live, allowed=allowed, may_open=may_open)

    @app.get('/c/{company_id}/bank-rules')
    def bank_rules_page(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as exc:
            return page_error(request, exc)

    def submit(request, company_id, form):
        action = form.get('action', '')
        selected = form.get('rule') or None
        headers = {'X-Bookflow-Reason': (form.get('ctx:reason') or '').strip() or 'Manage bank inbox rules'}
        try:
            if action in ('create', 'update'):
                raw = form_input(form)
                command = f'bank-rule {action}'
                # A live edit gets precisely the same details check as making it live.
                if action == 'update':
                    plan = run(request, command, raw, company_id, dry_run=True)
                    if plan.get('check') and (plan.get('next') or '').startswith('the same call with confirm'):
                        return page(request, company_id, selected=selected, values=form,
                                    confirmation={'payload': json.dumps(raw), 'check': plan['check'],
                                                  'notes': plan['notes'], 'label': 'Save and keep live'})
                out = run(request, command, raw, company_id, headers=headers)
            elif action == 'live-check':
                raw = {'rule': selected, 'live': True, 'expected_version': int(form['expected_version'])}
                out = run(request, 'bank-rule update', raw, company_id, dry_run=True)
                return page(request, company_id, selected=selected,
                            confirmation={'payload': json.dumps(raw), 'check': out['check'],
                                          'notes': out['notes'], 'label': 'Make this rule live'})
            elif action == 'confirm':
                raw = json.loads(form['payload'])
                raw['confirm'] = form.get('confirm')
                out = run(request, 'bank-rule update', raw, company_id, headers=headers)
            elif action in ('accept', 'decline', 'deactivate'):
                out = run(request, f'bank-rule {action}', {'rule': selected}, company_id, headers=headers)
            elif action in ('pause', 'activate'):
                raw = {'rule': selected, 'expected_version': int(form['expected_version'])}
                raw.update({'live': False} if action == 'pause' else {'status': 'active'})
                out = run(request, 'bank-rule update', raw, company_id, headers=headers)
            elif action in ('apply-check', 'apply'):
                raw = {key: form[key] for key in ('rule', 'account') if form.get(key)}
                out = run(request, 'bank-rule apply', raw, company_id, dry_run=action == 'apply-check', headers=headers)
                text = (form.get('test_payee') or '').strip()
                return page(request, company_id, selected=selected, preview=out, preview_payload=raw,
                            preview_text=text, result=None if action == 'apply-check' else out['summary'])
            else:
                raise BookflowError('E_VALIDATION', message='Choose a bank rule action.')
            return page(request, company_id, selected=out.get('rule', {}).get('id'), result=out['summary'])
        except (ValueError, TypeError, KeyError):
            return page(request, company_id, selected=selected, values=form,
                        error=BookflowError('E_VALIDATION', message='Check the rule fields and try again.').to_dict())
        except BookflowError as exc:
            if exc.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            return page(request, company_id, selected=selected, values=form, error=exc.to_dict())

    @app.post('/c/{company_id}/bank-rules')
    async def bank_rules_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as exc:
            return page_error(request, exc)
