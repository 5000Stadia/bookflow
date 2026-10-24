"""Accountant review in the browser (R121): the anchor's Accountant's Copy and Client Data Review on one page.

The page shows whether a copy is out (and the date these books are locked through), makes one, previews
and imports an accountant's change file (choosing which changes), cancels a copy, writes the change file
in an accountant's copy, and shows the period review checklist. Every write goes through the command it
names, with a reason for the audit trail; importing and cancelling are refused to an agent by the commands.
"""
from __future__ import annotations

from datetime import date

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, result=None, error=None, status_code=200, preview=None, file=None, as_of=None):
        company = run(request, 'company show', {}, company_id)
        if not role_allows('accountant-copy status', company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'company', 'required_role': 'member'})
        status = run(request, 'accountant-copy status', {}, company_id)
        current = status['current'] or {}
        as_of = as_of or request.query_params.get('as_of') or current.get('dividing_date') or date.today().isoformat()
        checklist = run(request, 'report accountant-review', {'as_of': as_of}, company_id)
        accountants = run(request, 'accountant list', {}, company_id)['accountants']
        cred = credential(request)
        return render('accountant_review.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], status=status, checklist=checklist, accountants=accountants,
                      result=result, error=error, preview=preview, file=file or '', as_of=as_of,
                      can_admin=role_allows('accountant-copy export', company, cred))

    @app.get('/c/{company_id}/accountant')
    def accountant_page(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, form):
        action = form.get('action') or ''
        reason = (form.get('ctx:reason') or '').strip()
        headers = {'X-Bookflow-Reason': reason} if reason else None
        file = (form.get('file') or '').strip()
        preview = None
        try:
            if action == 'export':
                out = run(request, 'accountant-copy export', {'through': form.get('through', '').strip()}, company_id, headers=headers)
                result = (f"Made the accountant's copy {out['file_name']} (with {out['manifest_file']}) in the company folder's "
                          f"exports/. These books now refuse changes dated on or before {out['dividing_date']}.")
            elif action == 'preview':
                preview = run(request, 'accountant-copy preview', {'file': file}, company_id)
                result = None
            elif action == 'import':
                chosen = [key[len('change:'):] for key in form if key.startswith('change:')]
                out = run(request, 'accountant-copy import', {'file': file, 'changes': chosen}, company_id, headers=headers)
                result = (f"Imported {len(out['imported'])} of the accountant's changes; the copy is {out['status']} and "
                          'these books take changes on any open date again.')
            elif action == 'cancel':
                out = run(request, 'accountant-copy cancel', {}, company_id, headers=headers)
                result = f"Cancelled the accountant's copy through {out['dividing_date']}; its change file can no longer be imported."
            elif action == 'changes':
                out = run(request, 'accountant-copy changes', {}, company_id, headers=headers)
                result = f"Wrote the change file {out['file_name']} ({out['counts']['new']} changes) in this copy's exports/; send it to the owner."
            else:
                return page(request, company_id, as_of=form.get('as_of'))
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, error=e.to_dict(), status_code=400, file=file)
        return page(request, company_id, result=result, preview=preview, file=file)

    @app.post('/c/{company_id}/accountant')
    async def accountant_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
