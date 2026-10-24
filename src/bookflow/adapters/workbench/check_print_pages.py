"""Print Checks: the anchor's File > Print Forms > Checks, on the `check print*` commands (R152).

One page per bank account: the cheques waiting to be printed (Select Checks to Print, all ticked),
the first check number and the check style with a partial first sheet, Print -- which records the
run and opens its PDF -- and then the anchor's "Did checks print OK?" for any run still waiting,
with the number of the first check that misprinted. Printer setup (style and fine alignment) with an
alignment sample, and reprinting one check, sit below. Every write is the command it names, with a
reason for the audit trail; confirming, cancelling and reprinting are refused to an agent by the
commands themselves.
"""
from __future__ import annotations

import base64

from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError


def _banks(run, request, company_id):
    try:
        out = run(request, 'account list', {}, company_id)
    except BookflowError:
        return []
    rows = out.get('items') or []
    return [row for row in rows if row.get('type') == 'bank' and row.get('active', True)]


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, account=None, values=None, result=None, run_out=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not role_allows('check print-queue', company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'ledger.read', 'required_role': 'member'})
        banks = _banks(run, request, company_id)
        account = account or (request.query_params.get('account') or '').strip() or None
        queue = run(request, 'check print-queue', {'account': account} if account else {}, company_id)
        if account is None and queue['accounts']:
            account = queue['accounts'][0]['account_id']
            queue = run(request, 'check print-queue', {'account': account}, company_id)
        elif account is None and banks:
            account = banks[0]['id']
            queue = run(request, 'check print-queue', {'account': account}, company_id)
        current = queue['accounts'][0] if queue['accounts'] else None
        waiting = [run(request, 'check print-run', {'run': run_id}, company_id) for run_id in queue['awaiting_confirmation']]
        allowed = lambda name: role_allows(name, company, credential(request))
        return render('check_print.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], banks=banks, account=account, current=current,
                      waiting=waiting, values=values or {}, result=result, run_out=run_out, error=error,
                      can_print=allowed('check print'), can_confirm=allowed('check print-confirm'),
                      can_setup=allowed('check print-setup'), can_reprint=allowed('check reprint'))

    @app.get('/c/{company_id}/print-checks')
    def print_checks_page(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def _pdf_response(out):
        return Response(content=base64.b64decode(out['content_base64']), media_type='application/pdf',
                        headers={'Content-Disposition': f'inline; filename="{out["filename"]}"'})

    @app.get('/c/{company_id}/print-checks/run/{run_id}.pdf')
    def print_run_pdf(request: Request, company_id: str, run_id: str):
        try:
            return _pdf_response(run(request, 'check print-pdf', {'run': run_id}, company_id))
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/print-checks/alignment.pdf')
    def alignment_pdf(request: Request, company_id: str):
        raw = {'account': (request.query_params.get('account') or '').strip()}
        for key in ('style',):
            if (request.query_params.get(key) or '').strip():
                raw[key] = request.query_params[key].strip()
        for key in ('offset_x_hundredths', 'offset_y_hundredths'):
            value = (request.query_params.get(key) or '').strip()
            if value.lstrip('-').isdigit():
                raw[key] = int(value)
        try:
            return _pdf_response(run(request, 'check print-alignment', raw, company_id))
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, form):
        action = form.get('action') or ''
        account = (form.get('account') or '').strip() or None
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        run_out = None
        try:
            if action == 'print':
                raw = {'account': account, 'checks': [key[5:] for key in form if key.startswith('pick:')]}
                if not raw['checks']:
                    raise BookflowError('E_VALIDATION', message='Tick at least one check to print.',
                                        details={'fields': [{'field': 'checks', 'problem': 'tick at least one check'}]})
                for key in ('first_number', 'style'):
                    if (form.get(key) or '').strip():
                        raw[key] = form[key].strip()
                if (form.get('checks_on_first_page') or '3') != '3':
                    raw['checks_on_first_page'] = int(form['checks_on_first_page'])
                run_out = run(request, 'check print', raw, company_id, headers=headers)
                result = (f"Printing {len(run_out['items'])} check(s), numbers {run_out['items'][0]['check_number']}"
                          f" to {run_out['items'][-1]['check_number']}. Open the PDF, print it, then answer below.")
            elif action.startswith('confirm:'):
                run_id = action.split(':', 1)[1]
                raw = {'run': run_id}
                if form.get(f'ok:{run_id}') == 'no' and (form.get(f'failed_from:{run_id}') or '').strip():
                    raw['failed_from'] = form[f'failed_from:{run_id}'].strip()
                out = run(request, 'check print-confirm', raw, company_id, headers=headers)
                good = sum(item['outcome'] == 'printed' for item in out['items'])
                result = f"{good} check(s) printed." + (
                    f" Spoiled: {', '.join(out['spoiled_numbers'])}; those checks are back in the queue."
                    if out['spoiled_numbers'] else '')
            elif action.startswith('cancel:'):
                run(request, 'check print-cancel', {'run': action.split(':', 1)[1]}, company_id, headers=headers)
                result = 'Print run cancelled; its checks are back in the queue and their numbers are free.'
            elif action == 'setup':
                raw = {'account': account}
                if (form.get('setup_style') or '').strip():
                    raw['style'] = form['setup_style'].strip()
                for key in ('offset_x_hundredths', 'offset_y_hundredths'):
                    value = (form.get(key) or '').strip()
                    if value:
                        raw[key] = int(value) if value.lstrip('-').isdigit() else value
                out = run(request, 'check print-setup', raw, company_id, headers=headers)
                result = (f"Printer setup saved: {out['style']} checks, horizontal {out['offset_x_hundredths']}, "
                          f"vertical {out['offset_y_hundredths']} hundredths of an inch.")
            elif action == 'reprint':
                raw = {'check': (form.get('reprint_check') or '').strip()}
                if (form.get('new_number') or '').strip():
                    raw['new_number'] = form['new_number'].strip()
                run_out = run(request, 'check reprint', raw, company_id, headers=headers)
                result = f"Reprinting check {run_out['items'][0]['check_number']}." + (
                    f" Number {run_out['spoiled_numbers'][0]} is recorded as spoiled." if run_out['spoiled_numbers'] else '')
            else:
                return page(request, company_id, account=account)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, account=account, values=dict(form), error=e.to_dict(), status_code=400)
        return page(request, company_id, account=account, result=result, run_out=run_out)

    @app.post('/c/{company_id}/print-checks')
    async def print_checks_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
