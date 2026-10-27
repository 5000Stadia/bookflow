"""Costing method (R104 increment 7): Settings > Costing method, for a person.

The page shows the method in force today, every switch (scheduled ones with Withdraw), and a switch form whose
Preview is the command's dry run: per item and in total, the change in stock value and cost of goods sold from the
switch date on, and the correction entries it will post. Every write goes through its command (`costing-method
set`, `withdraw`) with a reason; both are people-only, which the browser always is.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.clock import today_iso
from bookflow.core.errors import BookflowError


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, values=None, result=None, preview=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not role_allows('costing-method show', company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'ledger.read', 'required_role': 'member'})
        shown = run(request, 'costing-method show', {}, company_id)
        return render('costing_method.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], shown=shown, values=values or {}, result=result,
                      preview=preview, error=error, today=today_iso(),
                      can_write=role_allows('costing-method set', company, credential(request)))

    @app.get('/c/{company_id}/inventory/costing-method')
    def costing_page(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, form):
        action = form.get('action') or ''
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        try:
            if action in ('preview', 'set'):
                raw = {'method': (form.get('method') or '').strip(), 'effective_from': (form.get('effective_from') or '').strip()}
                if action == 'preview':
                    preview = run(request, 'costing-method set', raw, company_id, headers=headers, dry_run=True)
                    return page(request, company_id, values=dict(form), preview=preview)
                out = run(request, 'costing-method set', raw, company_id, headers=headers)
                result = out['message']
            elif action.startswith('withdraw:'):
                out = run(request, 'costing-method withdraw', {'switch': action.split(':', 1)[1]}, company_id,
                          headers=headers)
                result = out['message']
            else:
                return page(request, company_id)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, values=dict(form), error=e.to_dict(), status_code=400)
        return page(request, company_id, result=result)

    @app.post('/c/{company_id}/inventory/costing-method')
    async def costing_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
