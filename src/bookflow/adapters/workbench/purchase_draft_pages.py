"""Saved purchase drafts through the shared purchase-draft commands."""
from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.money import Money

HEADERS = ('vendor', 'date', 'number', 'ap_account', 'terms', 'due_date', 'supplier_reference', 'memo', 'class_id')
ORDER_HEADERS = ('vendor', 'date', 'number', 'terms', 'memo', 'class_id', 'expected_date', 'ship_to', 'reference', 'site', 'status')
ORDER_FIELDS = ('item', 'account', 'quantity', 'rate', 'amount', 'description', 'customer', 'class_id', 'class_mode', 'line_id', 'site')
LINE_FIELDS = ('account', 'amount', 'memo', 'customer', 'class_id', 'class_mode', 'line_id')


def row_indices(values, prefix='expense'):
    indices = set()
    for key in values:
        if not key.startswith(prefix + ':'):
            continue
        parts = key.split(':')
        if len(parts) != 3 or not parts[1].isdigit() or len(parts[1]) > 3:
            raise BookflowError('E_VALIDATION', message='Reload the draft form to enter expense lines.')
        indices.add(int(parts[1]))
    return sorted(indices)


def payload_of(form):
    """Translate text controls without calculating amounts or resolving references."""
    is_order = form.get('kind') == 'purchase_order'
    prefix, fields = ('order', ORDER_FIELDS) if is_order else ('expense', LINE_FIELDS)
    payload = {key: form[key] for key in (ORDER_HEADERS if is_order else HEADERS) if form.get(key, '').strip()}
    payload['kind'] = 'purchase_order' if is_order else 'bill'
    rows = []
    indices = row_indices(form, prefix)
    for index in indices:
        row = {key: form[f'{prefix}:{index}:{key}'] for key in fields
               if form.get(f'{prefix}:{index}:{key}', '').strip()}
        if not row:
            continue
        row['billable'] = form.get(f'{prefix}:{index}:billable') == 'on'
        rows.append(row)
    payload['lines' if is_order else 'expenses'] = rows
    return payload


def values_of(draft, kind='bill'):
    payload = (draft or {}).get('payload', {})
    kind = (draft or {}).get('kind', kind)
    prefix = 'order' if kind == 'purchase_order' else 'expense'
    values = {key: payload.get(key) or '' for key in (ORDER_HEADERS if kind == 'purchase_order' else HEADERS)}
    values['kind'] = kind
    values.update(expected_version=(draft or {}).get('version', ''), operation_key=new_id(), reason='')
    for index, row in enumerate(payload.get('lines' if kind == 'purchase_order' else 'expenses') or [{}]):
        for key, value in row.items():
            if key in ('amount', 'rate') and isinstance(value, dict):
                value = value.get('amount') or Money.parse(value).amount
            values[f'{prefix}:{index}:{key}'] = 'on' if value is True else '' if value is None or value is False else value
    return values


def mount(app, *, render, run, credential, page_error, role_allows):
    def url(company_id, draft_id=None):
        base = f'/c/{quote(company_id, safe="")}/purchase-drafts'
        return base + ('?' + urlencode({'draft': draft_id}) if draft_id else '')

    def page(request, company_id, *, draft_id=None, editing=False, values=None, result=None,
             error=None, saved=False, cancelled=False, notice=None, committed=None, withdraw_preview=False):
        company = run(request, 'company show', {}, company_id)
        draft = run(request, 'purchase-draft show', {'draft': draft_id}, company_id) if draft_id else None
        can_write = role_allows('purchase-draft create', company, credential(request))
        actions = {verb: role_allows('approval decide' if verb == 'decide' else 'purchase-draft ' + verb,
                   company, credential(request)) for verb in ('submit', 'withdraw', 'commit', 'decide', 'cancel', 'preview')}
        approval = None
        if draft and draft.get('request_id') and role_allows('approval show', company, credential(request)):
            approval = run(request, 'approval show', {'request': draft['request_id']}, company_id)
        simple_edit = not draft or draft['kind'] == 'purchase_order' or (draft['operation'] == 'create' and not any(
            draft['payload'].get(key) for key in ('items', 'purchase_order', 'order_lines', 'receipts', 'custom_fields', 'site', 'count_position')))
        editing = editing and simple_edit
        listing = None
        if not draft_id and not editing:
            raw = {'limit': 50}
            for key in ('state', 'cursor'):
                if request.query_params.get(key):
                    raw[key] = request.query_params[key]
            listing = run(request, 'purchase-draft list', raw, company_id)
        values = values if values is not None else values_of(draft, 'purchase_order' if request.query_params.get('kind') == 'purchase_order' else 'bill')
        kind = draft['kind'] if draft else values.get('kind', 'bill')
        indices = row_indices(values, 'order' if kind == 'purchase_order' else 'expense') or [0]
        response = render('purchase_drafts.html', request, company=company, company_id=company['company_id'],
                          draft=draft, listing=listing, editing=editing, values=values, indices=indices, kind=kind,
                          result=result, error=error, saved=saved, cancelled=cancelled, can_write=can_write,
                          actions=actions, approval=approval, simple_edit=simple_edit, notice=notice, committed=committed,
                          withdraw_preview=withdraw_preview)
        if draft:
            response.headers['HX-Push-Url'] = url(company['company_id'], draft_id) + ('&edit=1' if editing else '')
        return response

    @app.get('/c/{company_id}/purchase-drafts')
    def get(request: Request, company_id: str):
        try:
            return page(request, company_id, draft_id=request.query_params.get('draft'),
                        editing=request.query_params.get('new') == '1' or request.query_params.get('edit') == '1')
        except BookflowError as error:
            return page_error(request, error, company_id=company_id)

    def submit(request, company_id, form):
        draft_id = form.get('draft') or None
        action = form.get('action')
        try:
            headers = {'X-Bookflow-Reason': form.get('reason', '')}
            if action == 'saved-preview':
                result = run(request, 'purchase-draft preview', {'draft': draft_id}, company_id)
                return page(request, company_id, draft_id=draft_id, result=result)
            workflow = ('submit', 'approve', 'reject', 'withdraw', 'withdraw-preview', 'commit')
            if action not in ('save', 'preview', 'cancel', *workflow):
                raise BookflowError('E_VALIDATION', message='Choose Save draft, Preview or Cancel draft.')
            raw = {'operation_key': form.get('operation_key', '')}
            if draft_id:
                try:
                    version = int(form.get('expected_version', ''))
                except ValueError:
                    raise BookflowError('E_VALIDATION', message='Reload the draft to read its current version.') from None
                raw.update(draft=draft_id, expected_version=version)
            if action in workflow:
                if action in ('approve', 'reject'):
                    raw = dict(request=form.get('request_id', ''), expected_version=int(form.get('request_version', '0')),
                               decision=action, step_id='review', comment=form.get('comment') or None,
                               operation_key=form.get('operation_key', ''))
                    command = 'approval decide'
                else:
                    command = 'purchase-draft ' + ('withdraw' if action == 'withdraw-preview' else action)
                    if action in ('withdraw', 'withdraw-preview', 'commit') and form.get('request_version'):
                        raw['expected_request_version'] = int(form['request_version'])
                    if action in ('withdraw', 'withdraw-preview'):
                        raw['confirm'] = action == 'withdraw-preview' or form.get('confirm') == 'true'
                result = run(request, command, raw, company_id, headers=headers, dry_run=action == 'withdraw-preview')
                notices = {'submit': 'Submitted for approval. Nothing posted.', 'approve': 'Approved. Nothing posted.',
                    'reject': 'Returned for revision. Nothing posted.', 'withdraw': 'Approval withdrawn; draft is editable.',
                    'withdraw-preview': 'Review withdrawal: this approval will be invalidated and the draft will become editable. Nothing has changed.',
                    'commit': 'Purchase committed. The commit is recorded as one reversible batch.'}
                return page(request, company_id, draft_id=draft_id, notice=notices[action],
                            values=form if action == 'withdraw-preview' else None,
                            withdraw_preview=action == 'withdraw-preview', committed=result if action == 'commit' else None)
            if action == 'cancel':
                result = run(request, 'purchase-draft cancel', raw, company_id, headers=headers)
                return page(request, company_id, draft_id=draft_id, cancelled=True)
            if draft_id:
                current = run(request, 'purchase-draft show', {'draft': draft_id}, company_id)
                if current['kind'] != 'purchase_order' and (current['operation'] != 'create' or any(current['payload'].get(key) for key in
                    ('items', 'purchase_order', 'order_lines', 'receipts', 'custom_fields', 'site', 'count_position'))):
                    raise BookflowError('E_VALIDATION', message='Use the full purchase-draft update form to preserve this draft’s source and item fields.')
            raw.update(payload_of(form))
            command = 'purchase-draft update' if draft_id else 'purchase-draft create'
            result = run(request, command, raw, company_id, headers=headers, dry_run=action == 'preview')
            if action == 'preview':
                return page(request, company_id, draft_id=draft_id, editing=True, values=form, result=result)
            return page(request, company_id, draft_id=result['id'], saved=True)
        except ValueError:
            return page(request, company_id, draft_id=draft_id, values=form,
                        error={'code': 'E_VALIDATION', 'message': 'Reload the draft to read its current versions.'})
        except BookflowError as error:
            if error.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER', 'E_PERMISSION'):
                raise
            safe_form = {key: value for key, value in form.items() if not key.startswith(('expense:', 'order:')) or
                         (len(key.split(':')) == 3 and key.split(':')[1].isdigit() and len(key.split(':')[1]) <= 3)}
            return page(request, company_id, draft_id=draft_id, editing=action in ('save', 'preview'),
                        values=safe_form, error=error.to_dict())

    @app.post('/c/{company_id}/purchase-drafts')
    async def post(request: Request, company_id: str):
        form = {key: value for key, value in (await request.form(max_fields=2500)).multi_items()
                if isinstance(value, str)}
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as error:
            return page_error(request, error, company_id=company_id)
