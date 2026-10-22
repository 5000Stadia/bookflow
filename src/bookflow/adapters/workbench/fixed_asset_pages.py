"""Fixed assets and loans in the workbench (R119).

* **Fixed assets** (`/c/<company>/fixed-asset`): the anchor's fixed asset list -- cost, depreciation
  posted, book value, periods pending -- with Post depreciation; each asset opens its schedule
  (`/c/<company>/fixed-asset/<item>`), every period marked posted (with its entry), pending or stopped,
  with Set up and Dispose.
* **Loans** (`/c/<company>/loan`): the Loan Manager list; each loan (`/c/<company>/loan/<id>`) shows
  its terms, whether the liability account ties to the schedule, the amortization schedule paid and
  remaining, and a Pay form for the next payment (optionally with extra principal), which writes the
  check with `loan pay`.

Every figure is one the command returned; nothing here adds anything up.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError


def mount(app, *, render, run, credential, page_error):
    def company_label(request, company_id):
        company = run(request, 'company show', {}, company_id)
        return company.get('display_name') or company.get('legal_name')

    @app.get('/c/{company_id}/fixed-asset')
    def fixed_asset_list(request: Request, company_id: str):
        try:
            listed = run(request, 'fixed-asset list', {}, company_id)
            return render('fixed_assets.html', request, company_id=company_id, company_label=company_label(request, company_id),
                          assets=listed['items'])
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/fixed-asset/{item_id:ulid}')
    def fixed_asset_schedule(request: Request, company_id: str, item_id: str):
        try:
            schedule = run(request, 'fixed-asset schedule', {'item': item_id}, company_id)
            return render('fixed_asset_schedule.html', request, company_id=company_id,
                          company_label=company_label(request, company_id), schedule=schedule)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/loan')
    def loan_list(request: Request, company_id: str):
        try:
            listed = run(request, 'loan list', {}, company_id)
            return render('loans.html', request, company_id=company_id, company_label=company_label(request, company_id),
                          loans=listed['items'])
        except BookflowError as e:
            return page_error(request, e)

    def loan_page(request, company_id, loan_id, *, notice=None, error=None, values=None, status_code=200):
        shown = run(request, 'loan show', {'loan': loan_id}, company_id)
        return render('loan.html', request, status_code=status_code, company_id=company_id,
                      company_label=company_label(request, company_id), loan=shown['loan'], rows=shown['rows'],
                      notice=notice, error=error, values=values or {})

    @app.get('/c/{company_id}/loan/{loan_id:ulid}')
    def loan_detail(request: Request, company_id: str, loan_id: str):
        try:
            return loan_page(request, company_id, loan_id)
        except BookflowError as e:
            return page_error(request, e)

    def pay(request, company_id, loan_id, form):
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        raw = {'loan': loan_id}
        for field in ('date', 'from_account', 'number', 'extra_principal', 'memo'):
            value = (form.get(field) or '').strip()
            if value:
                raw[field] = value.replace(',', '') if field == 'extra_principal' else value
        if form.get('extra_only'):
            raw['extra_only'] = True
        try:
            paid = run(request, 'loan pay', raw, company_id, headers=headers)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            return loan_page(request, company_id, loan_id, error=e.to_dict(), values=form, status_code=400)
        return loan_page(request, company_id, loan_id, notice=paid['summary'] + '.')

    @app.post('/c/{company_id}/loan/{loan_id:ulid}')
    async def loan_pay(request: Request, company_id: str, loan_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(pay, request, company_id, loan_id, form)
        except BookflowError as e:
            return page_error(request, e)
