"""Saved bill drafts through the shared purchase-draft commands."""
from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id

HEADERS = ('vendor', 'date', 'number', 'ap_account', 'terms', 'due_date', 'supplier_reference', 'memo', 'class_id')
LINE_FIELDS = ('account', 'amount', 'memo', 'customer', 'class_id', 'class_mode', 'line_id')


def row_indices(values):
    indices = set()
    for key in values:
        if not key.startswith('expense:'):
            continue
        parts = key.split(':')
        if len(parts) != 3 or not parts[1].isdigit() or len(parts[1]) > 3:
            raise BookflowError('E_VALIDATION', message='Reload the draft form to enter expense lines.')
        indices.add(int(parts[1]))
    return sorted(indices)


def payload_of(form):
    """Translate text controls without calculating amounts or resolving references."""
    payload = {key: form[key] for key in HEADERS if form.get(key, '').strip()}
    rows = []
    indices = row_indices(form)
    for index in indices:
        row = {key: form[f'expense:{index}:{key}'] for key in LINE_FIELDS
               if form.get(f'expense:{index}:{key}', '').strip()}
        if not row:
            continue
        row['billable'] = form.get(f'expense:{index}:billable') == 'on'
        rows.append(row)
    payload['expenses'] = rows
    return payload


def values_of(draft):
    payload = (draft or {}).get('payload', {})
    values = {key: payload.get(key) or '' for key in HEADERS}
    values.update(expected_version=(draft or {}).get('version', ''), operation_key=new_id(), reason='')
    for index, row in enumerate(payload.get('expenses') or [{}]):
        for key, value in row.items():
            if key == 'amount' and isinstance(value, dict):
                value = value.get('amount', '')
            values[f'expense:{index}:{key}'] = 'on' if value is True else '' if value is None or value is False else value
    return values


def mount(app, *, render, run, credential, page_error, role_allows):
    def url(company_id, draft_id=None):
        base = f'/c/{quote(company_id, safe="")}/purchase-drafts'
        return base + ('?' + urlencode({'draft': draft_id}) if draft_id else '')

    def page(request, company_id, *, draft_id=None, editing=False, values=None, result=None,
             error=None, saved=False, cancelled=False):
        company = run(request, 'company show', {}, company_id)
        draft = run(request, 'purchase-draft show', {'draft': draft_id}, company_id) if draft_id else None
        can_write = role_allows('purchase-draft create', company, credential(request))
        listing = None
        if not draft_id and not editing:
            raw = {'limit': 50}
            for key in ('state', 'cursor'):
                if request.query_params.get(key):
                    raw[key] = request.query_params[key]
            listing = run(request, 'purchase-draft list', raw, company_id)
        values = values if values is not None else values_of(draft)
        indices = row_indices(values) or [0]
        response = render('purchase_drafts.html', request, company=company, company_id=company['company_id'],
                          draft=draft, listing=listing, editing=editing, values=values, indices=indices,
                          result=result, error=error, saved=saved, cancelled=cancelled, can_write=can_write)
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
            if action not in ('save', 'preview', 'cancel'):
                raise BookflowError('E_VALIDATION', message='Choose Save draft, Preview or Cancel draft.')
            raw = {'operation_key': form.get('operation_key', '')}
            if draft_id:
                try:
                    version = int(form.get('expected_version', ''))
                except ValueError:
                    raise BookflowError('E_VALIDATION', message='Reload the draft to read its current version.') from None
                raw.update(draft=draft_id, expected_version=version)
            if action == 'cancel':
                result = run(request, 'purchase-draft cancel', raw, company_id, headers=headers)
                return page(request, company_id, draft_id=draft_id, cancelled=True)
            raw.update(payload_of(form))
            command = 'purchase-draft update' if draft_id else 'purchase-draft create'
            result = run(request, command, raw, company_id, headers=headers, dry_run=action == 'preview')
            if action == 'preview':
                return page(request, company_id, draft_id=draft_id, editing=True, values=form, result=result)
            return page(request, company_id, draft_id=result['id'], saved=True)
        except BookflowError as error:
            if error.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER', 'E_PERMISSION'):
                raise
            safe_form = {key: value for key, value in form.items() if not key.startswith('expense:') or
                         (len(key.split(':')) == 3 and key.split(':')[1].isdigit() and len(key.split(':')[1]) <= 3)}
            return page(request, company_id, draft_id=draft_id, editing=action != 'cancel',
                        values=safe_form, error=error.to_dict())

    @app.post('/c/{company_id}/purchase-drafts')
    async def post(request: Request, company_id: str):
        form = {key: value for key, value in (await request.form(max_fields=2500)).multi_items()
                if isinstance(value, str)}
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as error:
            return page_error(request, error, company_id=company_id)
