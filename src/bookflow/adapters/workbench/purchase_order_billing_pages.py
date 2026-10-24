"""Bill part of a purchase order, or close its lines, in the workbench (R153).

`/c/<company>/purchase-order/<id>/bill-part` lists each ordered line with what was ordered,
received, billed and is still open. Entering a quantity (or, on an account line, an amount)
against some lines and choosing Enter bill writes one `bill post` with `order_lines`: the
anchor's Enter Bills against an order. Ticking lines and choosing Close lines (or Reopen lines)
runs `purchase-order close-lines`. Every figure is one the commands returned.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError


def mount(app, *, render, run, credential, page_error):
    def page(request, company_id, order_id, *, notice=None, error=None, values=None, bill=None, status_code=200):
        order = run(request, 'purchase-order show', {'purchase_order': order_id}, company_id)
        return render('purchase_order_bill_part.html', request, status_code=status_code, company_id=company_id,
                      order=order, notice=notice, error=error, values=values or {}, bill=bill)

    @app.get('/c/{company_id}/purchase-order/{order_id:ulid}/bill-part')
    def bill_part_page(request: Request, company_id: str, order_id: str):
        try:
            return page(request, company_id, order_id)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, order_id, form):
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        action = form.get('action')
        try:
            if action in ('close', 'reopen'):
                lines = [key[len('close:'):] for key in form if key.startswith('close:')]
                if not lines:
                    raise BookflowError('E_VALIDATION', message='Tick the lines to close or reopen.')
                done = run(request, 'purchase-order close-lines', {
                    'purchase_order': order_id, 'lines': lines, 'reopen': action == 'reopen'}, company_id, headers=headers)
                verb = 'Reopened' if action == 'reopen' else 'Closed'
                return page(request, company_id, order_id,
                            notice=f'{verb} {len(lines)} line(s). The order is now {done["status"].replace("_", " ")}.')
            selections = []
            for key, value in form.items():
                value = (value or '').strip().replace(',', '')
                if not value:
                    continue
                if key.startswith('qty:'):
                    selections.append({'line': key[4:], 'quantity': value})
                elif key.startswith('amount:'):
                    selections.append({'line': key[7:], 'amount': value})
            if not selections:
                raise BookflowError('E_VALIDATION', message='Enter how much of at least one line the vendor billed.')
            raw = {'purchase_order': order_id, 'order_lines': selections}
            for field in ('date', 'supplier_reference', 'memo'):
                value = (form.get(field) or '').strip()
                if value:
                    raw[field] = value
            bill = run(request, 'bill post', raw, company_id, headers=headers)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            return page(request, company_id, order_id, error=e.to_dict(), values=form, status_code=400)
        return page(request, company_id, order_id, bill=bill,
                    notice=f'Bill {bill["number"]} entered for {bill["total"]["amount"]}.')

    @app.post('/c/{company_id}/purchase-order/{order_id:ulid}/bill-part')
    async def bill_part_save(request: Request, company_id: str, order_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, order_id, form)
        except BookflowError as e:
            return page_error(request, e)
