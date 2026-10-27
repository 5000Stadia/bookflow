"""Reminders, to-dos and the overdue notice queue in the workbench (R111).

* **Reminders** (`/c/<company>/reminders`): the anchor's Reminders list -- one heading per kind with
  its count and total, each expanded to its lines: memorized transactions due, memorized entries
  that need a person, to-dos due, overdue notices to send, overdue invoices and bills to pay.
* **To-dos** (`/c/<company>/todo`): the To Do list, the reader's own by default (`?all=1` for
  everyone's), with Done on each open one and New to-do (the generated form).
* **Overdue notices** (`/c/<company>/notice`): the queue the scheduler fills. Each notice has Print
  (its PDF: the statement or the letter), Mark sent and Dismiss. Bookflow never sends one.

The home page shows the Reminders headings and the reader's own to-dos (``home.overview``).
Every figure is one the command returned; nothing here adds anything up.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError


def mount(app, *, render, run, credential, page_error):
    def company_label(request, company_id):
        company = run(request, 'company show', {}, company_id)
        return company.get('display_name') or company.get('legal_name')

    @app.get('/c/{company_id}/reminders')
    def reminders_page(request: Request, company_id: str):
        try:
            raw = {'mine': request.query_params.get('mine') == '1'}
            if request.query_params.get('as_of'):
                raw['as_of'] = request.query_params['as_of']
            if (request.query_params.get('bills_days_ahead') or '').strip():
                days = request.query_params['bills_days_ahead'].strip()
                raw['bills_days_ahead'] = int(days) if days.isdigit() else days  # anything else is refused by the command
            listed = run(request, 'reminders', raw, company_id)
            return render('reminders.html', request, company_id=company_id, company_label=company_label(request, company_id),
                          r=listed, mine=raw['mine'], bills_days_ahead=raw.get('bills_days_ahead', 10),
                          notice=request.query_params.get('done'))
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/todo')
    def todo_page(request: Request, company_id: str):
        try:
            everyone = request.query_params.get('all') == '1'
            status = request.query_params.get('status') or 'open'
            listed = run(request, 'todo list', {'mine': not everyone, 'status': status}, company_id)
            return render('todos.html', request, company_id=company_id, company_label=company_label(request, company_id),
                          todos=listed['items'], everyone=everyone, status=status, as_of=listed['as_of'],
                          notice=request.query_params.get('done'))
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/notice')
    def notice_page(request: Request, company_id: str):
        try:
            status = request.query_params.get('status') or 'ready'
            listed = run(request, 'notice list', {'status': status}, company_id)
            return render('notices.html', request, company_id=company_id, company_label=company_label(request, company_id),
                          notices=listed['items'], status=status, ready_total=listed['ready_total'],
                          notice=request.query_params.get('done'), error=None)
        except BookflowError as e:
            return page_error(request, e)

    def act(request, company_id, name, raw, back, said):
        form_reason = (raw.pop('ctx:reason', None) or '').strip()
        headers = {'X-Bookflow-Reason': form_reason} if form_reason else None
        try:
            run(request, name, raw, company_id, headers=headers)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            return page_error(request, e)
        joiner = '&' if '?' in back else '?'
        return RedirectResponse(back + joiner + urlencode({'done': said}), status_code=303)

    def back_to(form, company_id, default):
        target = (form.get('back') or '').strip()
        return target if target.startswith(f'/c/{company_id}/') else f'/c/{company_id}/{default}'

    @app.post('/c/{company_id}/todo/{todo_id:ulid}/_done')
    async def todo_done(request: Request, company_id: str, todo_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(act, request, company_id, 'todo complete', {'todo': todo_id},
                                           back_to(form, company_id, 'todo'), 'To-do marked done.')
        except BookflowError as e:
            return page_error(request, e)

    @app.post('/c/{company_id}/notice/{notice_id:ulid}/_sent')
    async def notice_sent(request: Request, company_id: str, notice_id: str):
        form = dict(await request.form())
        raw = {'notice': notice_id, 'method': form.get('method') or 'print'}
        try:
            return await run_in_threadpool(act, request, company_id, 'notice mark-sent', raw,
                                           back_to(form, company_id, 'notice'), 'Notice marked sent.')
        except BookflowError as e:
            return page_error(request, e)

    @app.post('/c/{company_id}/notice/{notice_id:ulid}/_dismiss')
    async def notice_dismiss(request: Request, company_id: str, notice_id: str):
        form = dict(await request.form())
        raw = {'notice': notice_id}
        if (form.get('note') or '').strip():
            raw['note'] = form['note'].strip()
        try:
            return await run_in_threadpool(act, request, company_id, 'notice dismiss', raw,
                                           back_to(form, company_id, 'notice'), 'Notice dismissed.')
        except BookflowError as e:
            return page_error(request, e)
