"""Assess Finance Charges: the anchor's window, on `finance-charge assess`.

The page asks for an assessment date and shows the dry run: every customer and job with a balance
overdue past the grace period, its overdue balance, last finance charge date and proposed charge,
each with a checkbox (all marked, as the anchor marks them) and an amount that can be changed. A
customer with unapplied payments or credits is starred, as in the anchor, so they can be applied
first. *Assess charges* submits the checked rows with their amounts and the preview's fingerprint,
so a change to the books in between is refused rather than charged; *Recalculate* reads the date
again. With finance charges off, the page says so and links to the company settings.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

COMMAND = 'finance-charge assess'


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, date=None, values=None, result=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not role_allows(COMMAND, company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'ledger.post', 'required_role': 'standard'})
        preview = off = None
        try:
            preview = run(request, COMMAND, {'date': date} if date else {}, company_id, dry_run=True)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER', 'E_PERMISSION'):
                raise
            if e.code == 'E_VALIDATION' and 'next' in (e.details or {}):
                off = e.to_dict()
            elif error is None:
                error = e.to_dict()
        return render('finance_charges.html', request, status_code=status_code, company=company, company_id=company['company_id'],
                      preview=preview, off=off, result=result, error=error, date=date or (preview or {}).get('date', ''),
                      values=values or {}, can_set=role_allows('company update', company, credential(request)))

    @app.get('/c/{company_id}/finance-charge/assess')
    def finance_charge_page(request: Request, company_id: str):
        try:
            return page(request, company_id, date=request.query_params.get('date') or None)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, form):
        date = (form.get('date') or '').strip() or None
        if form.get('action') != 'assess':
            return page(request, company_id, date=date)
        chosen = [key[4:] for key in form if key.startswith('sel:')]
        customers = []
        for customer in chosen:
            choice = {'customer': customer}
            amount = (form.get('amount:' + customer) or '').strip()
            if amount and amount != (form.get('computed:' + customer) or ''):
                choice['amount'] = amount
            customers.append(choice)
        raw = {'customers': customers}
        if date:
            raw['date'] = date
        if form.get('fingerprint'):
            raw['expected_facts_fingerprint'] = form['fingerprint']
        raw['mark_to_print'] = form.get('mark_to_print') == 'yes'
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        try:
            if not customers:
                raise BookflowError('E_VALIDATION', message='Mark at least one customer to assess.',
                                    details={'fields': [{'field': 'customers', 'problem': 'mark at least one customer'}]})
            out = run(request, COMMAND, raw, company_id, headers=headers)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, date=date, values=dict(form), error=e.to_dict(), status_code=400)
        return page(request, company_id, date=date, result=out)

    @app.post('/c/{company_id}/finance-charge/assess')
    async def finance_charge_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
