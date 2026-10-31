"""Bank statement import, reversal and move: browser translations of the existing commands."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from bookflow.core.errors import BookflowError
from bookflow.adapters.workbench import home

MAX_BYTES = 20_000_000
REVERSE_SLICE = 25  # short requests; every slice remains the command's resumable unit
CSV_FIELDS = ('date', 'amount', 'debit', 'credit', 'payee', 'memo', 'number', 'fitid', 'balance')


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, step='file', values=None, **ctx):
        shown = run(request, 'company show', {}, company_id)
        accounts = run(request, 'account list', {}, company_id)['items']
        accounts = [a for a in accounts if a['type'] in ('bank', 'credit_card') and a.get('active', True)]
        permits = lambda cmd: role_allows(cmd.name, shown, credential(request))
        history = run(request, 'bank-feed query', {'include_reversed': True, 'limit': 50,
                     **({'cursor': request.query_params['cursor']} if request.query_params.get('cursor') else {})}, company_id)
        response = render('bank_import.html', request, company=shown, company_id=shown['company_id'],
                          accounts=accounts, history=history, may_open=lambda path: home.page_open(shown['company_id'], path, permits) is True, step=step, values=values or {}, csv_fields=CSV_FIELDS,
                          can_import=role_allows('bank-feed import', shown, credential(request)),
                          can_reverse=role_allows('bank-feed reverse', shown, credential(request)),
                          can_move=role_allows('bank-feed move', shown, credential(request)), **ctx)
        detail = ctx.get('detail')
        if detail:
            response.headers['HX-Push-Url'] = f"/c/{quote(shown['company_id'], safe='')}/bank-import?import={detail['bank_import']['id']}"
        return response

    def detail_page(request, company_id, import_id, **ctx):
        detail = run(request, 'bank-feed show', {'import_id': import_id}, company_id)
        return page(request, company_id, step='done', detail=detail, **ctx)

    @app.get('/c/{company_id}/bank-import')
    def bank_import_page(request: Request, company_id: str):
        try:
            if request.query_params.get('import'):
                return detail_page(request, company_id, request.query_params['import'])
            return page(request, company_id, values={'account': request.query_params.get('account', '')})
        except BookflowError as e:
            return page_error(request, e)

    def import_input(form):
        raw = {k: form[k] for k in ('account', 'content', 'format', 'mapping_name', 'save_mapping') if form.get(k)}
        raw['csv_mapping'] = {k: form['csv_' + k] for k in CSV_FIELDS if form.get('csv_' + k)}
        if form.get('date_format'):
            raw['csv_mapping']['date_format'] = form['date_format']
        if form.get('invert') == 'on':
            raw['csv_mapping']['invert'] = True
        for k in ('again', 'confirm_account', 'account_confirmed'):
            if form.get(k) == 'on':
                raw[k] = True
        raw['auto_match'] = form.get('auto_match', 'on') == 'on'
        return raw

    def act(request, company_id, form, upload):
        action = form.get('action', 'check')
        headers = {'X-Bookflow-Reason': form.get('reason') or 'Bank statement import'}
        if action in ('check', 'import'):
            if upload is not None:
                from bookflow.company.cutover_sources import decode
                data = upload.file.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise BookflowError('E_VALIDATION', message='Statement file is too large (20 MB maximum).')
                form['content'] = decode(data)
                form['file_name'] = upload.filename
            raw = import_input(form)
            out = run(request, 'bank-feed import', raw, company_id, dry_run=action == 'check', headers=headers)
            if action == 'check':
                return page(request, company_id, step='check', values=form, preview=out)
            if out.get('import_id'):
                return detail_page(request, company_id, out['import_id'], outcome=out)
            return page(request, company_id, step='done', outcome=out)
        if action in ('undo-plan', 'undo', 'move-plan', 'move', 'resume'):
            import_id = form.get('import_id', '')
            moving = action.startswith('move') or (action == 'resume' and bool(form.get('to')))
            raw = {'import_id': import_id, 'limit': REVERSE_SLICE}
            if moving:
                raw['to'] = form.get('to', '')
                for k in ('confirm_account', 'account_confirmed'):
                    if form.get(k) == 'on':
                        raw[k] = True
            elif form.get('through') == 'on':
                raw['through'] = True
            if form.get('fingerprint'):
                raw['fingerprint'] = form['fingerprint']
            command = 'bank-feed move' if moving else 'bank-feed reverse'
            planning = action.endswith('-plan')
            out = run(request, command, raw, company_id, dry_run=planning, headers=headers)
            reversal = out['reversal'] if moving else out
            return detail_page(request, company_id, import_id, values=form,
                               undo={'plan': reversal, 'target': out.get('target') if moving else None,
                                     'moving': moving, 'planning': planning, 'raw': raw})
        raise BookflowError('E_VALIDATION', message='Unknown bank import action.')

    def submit(request, company_id, form, upload):
        try:
            return act(request, company_id, form, upload)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            if form.get('import_id'):
                return detail_page(request, company_id, form['import_id'], error=e.to_dict(), values=form)
            return page(request, company_id, error=e.to_dict(), values=form)

    @app.post('/c/{company_id}/bank-import')
    async def bank_import_post(request: Request, company_id: str):
        try:
            # Bound the stream as well as the advertised size before parsing uploaded or pasted text.
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES + 2_000_000:
                    raise BookflowError('E_VALIDATION', message='Statement file is too large (20 MB maximum).')
                chunks.append(chunk)
            request._body = b''.join(chunks)
            data = await request.form(max_files=1, max_fields=50, max_part_size=MAX_BYTES + 1)
            try:
                form = {k: v for k, v in data.items() if isinstance(v, str)}
                upload = data.get('file')
                if not getattr(upload, 'filename', None):
                    upload = None
                return await run_in_threadpool(submit, request, company_id, form, upload)
            finally:
                await data.close()
        except HTTPException as e:
            if e.status_code != 400:
                raise
            error = BookflowError('E_VALIDATION', message=f'The file could not be read: {e.detail}')
            return await run_in_threadpool(page, request, company_id, error=error.to_dict())
        except BookflowError as e:
            return page_error(request, e)
