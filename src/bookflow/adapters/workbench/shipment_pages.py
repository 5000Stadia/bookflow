"""Shipments in the browser (R181 increment 1): ship an order, the shipment page, the shipments list.

*Ship* opens from the order page: each stocked line with what it still has to ship, a quantity to ship now, the
ship date, carrier and tracking numbers; *Preview* shows the dry run of `shipment post` (each line's cost, what stays
to ship) and *Ship* posts it. The shipment page shows its lines with cost, invoiced and waiting, the invoices that
bill it, its returns and every revision, with *Create invoice*, *Return* (units back before they are invoiced),
*Void return* and *Void*. The list is `shipment query`.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

POST = 'shipment post'
_PASS = ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER')


def mount(app, *, render, run, credential, page_error, role_allows):
    def can(name, company, request):
        return role_allows(name, company, credential(request))

    def headers(reason):
        return {'X-Bookflow-Reason': reason} if reason else None

    # ---------------------------------------------------------------- ship an order

    def ship_page(request, company_id, *, values, preview=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        order = None
        lines = []
        if values.get('sales_order'):
            try:
                order = run(request, 'sales-order show', {'sales_order': values['sales_order']}, company_id)
            except BookflowError as e:
                if e.code in _PASS:
                    raise
                error = error or e.to_dict()
        for line in (order or {}).get('revision', {}).get('lines', []):
            figures = line.get('order')
            profile = line['facts']['profile']
            if not figures or profile['item_type'] not in ('inventory_part', 'inventory_assembly'):
                continue
            to_ship = figures.get('to_ship', figures['open'])
            lines.append(dict(line_id=line['line_id'], item=profile['item']['label'], ordered=figures['ordered'],
                              shipped=figures.get('shipped', '0'), to_ship=to_ship,
                              quantity=values.get('qty:' + line['line_id'], to_ship)))
        return render('shipment_new.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], order=order, lines=lines, values=values, preview=preview,
                      error=error, can_write=can(POST, company, request))

    def ship_input(values, lines_from):
        raw = {'sales_order': values['sales_order']}
        if values.get('date'):
            raw['date'] = values['date']
        if values.get('carrier'):
            raw['carrier'] = values['carrier']
        tracking = [t.strip() for t in (values.get('tracking') or '').replace('\n', ',').split(',') if t.strip()]
        if tracking:
            raw['tracking_numbers'] = tracking
        if values.get('memo'):
            raw['memo'] = values['memo']
        if values.get('freight'):
            raw['freight_amount'] = values['freight']
        chosen = [dict(order_line=key[4:], quantity=value.strip()) for key, value in lines_from.items()
                  if key.startswith('qty:') and value.strip() not in ('', '0')]
        if chosen:
            raw['lines'] = chosen
        return raw

    @app.get('/c/{company_id}/shipments/new')
    def shipment_new(request: Request, company_id: str):
        try:
            return ship_page(request, company_id, values={'sales_order': request.query_params.get('sales_order', '')})
        except BookflowError as e:
            return page_error(request, e)

    def ship_submit(request, company_id, form):
        values = {key: (value or '').strip() for key, value in form.items() if not key.startswith('ctx:')}
        action = form.get('action')
        if action not in ('preview', 'ship', 'draft') or not values.get('sales_order'):
            return ship_page(request, company_id, values=values)
        raw = ship_input(values, values)
        if action != 'draft' and values.get('invoice') == '1':
            raw['invoice'] = True
        try:
            if action == 'draft':
                # The packing layer (R181 increment 4): a draft to pack on the pack screen, then ship.
                from fastapi.responses import RedirectResponse
                out = run(request, 'shipment create', raw, company_id,
                          headers=headers((form.get('ctx:reason') or '').strip() or 'Draft to pack'))
                target = f"/c/{quote(company_id, safe='')}/shipments/{quote(out['number'], safe='')}/pack"
                if request.headers.get('HX-Request'):
                    from fastapi.responses import Response
                    return Response(status_code=200, headers={'HX-Redirect': target})
                return RedirectResponse(target, status_code=303)
            if action == 'preview':
                out = run(request, POST, raw, company_id, dry_run=True)
                return ship_page(request, company_id, values=values, preview=out)
            out = run(request, POST, raw, company_id, headers=headers((form.get('ctx:reason') or '').strip()))
        except BookflowError as e:
            if e.code in _PASS:
                return page_error(request, e)
            return ship_page(request, company_id, values=values, error=e.to_dict(), status_code=400)
        return shipment_view(request, company_id, out['number'], result=out,
                             invoice=out.get('invoice') if out.get('invoice') else None)

    @app.post('/c/{company_id}/shipments/new')
    async def shipment_new_submit(request: Request, company_id: str):
        form = {k: str(v) for k, v in (await request.form()).items()}
        try:
            return await run_in_threadpool(ship_submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

    # ---------------------------------------------------------------- the list

    @app.get('/c/{company_id}/shipments')
    def shipments_list(request: Request, company_id: str):
        try:
            company = run(request, 'company show', {}, company_id)
            raw = {k: request.query_params[k] for k in ('sales_order', 'status') if request.query_params.get(k)}
            if request.query_params.get('not_invoiced'):
                raw['not_invoiced'] = True
            listing = run(request, 'shipment query', raw, company_id)
            return render('shipments.html', request, company=company, company_id=company['company_id'],
                          listing=listing, filters=raw, can_write=can(POST, company, request))
        except BookflowError as e:
            return page_error(request, e)

    # ---------------------------------------------------------------- one shipment

    def shipment_view(request, company_id, number, *, result=None, invoice=None, returned=None, error=None,
                      status_code=200):
        company = run(request, 'company show', {}, company_id)
        shipment = run(request, 'shipment show', {'shipment': number}, company_id)
        return render('shipment.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], shipment=shipment, result=result, invoice=invoice,
                      returned=returned,
                      error=error, can_write=can(POST, company, request), quote=quote,
                      packs=bool(company.get('info', {}).get('pick_and_pack_enabled'))
                      and can('shipment pack', company, request))

    @app.get('/c/{company_id}/shipments/{number}')
    def shipment_page(request: Request, company_id: str, number: str):
        try:
            return shipment_view(request, company_id, number)
        except BookflowError as e:
            return page_error(request, e)

    def shipment_submit(request, company_id, number, form):
        action = form.get('action')
        reason = (form.get('ctx:reason') or '').strip()
        try:
            if action == 'invoice':
                raw = {'shipment': number}
                if (form.get('date') or '').strip():
                    raw['date'] = form['date'].strip()
                out = run(request, 'shipment invoice', raw, company_id, headers=headers(reason))
                return shipment_view(request, company_id, number, invoice=out)
            if action == 'void':
                out = run(request, 'shipment void', {'shipment': number}, company_id, headers=headers(reason))
                return shipment_view(request, company_id, number, result=out)
            if action == 'return':
                raw = {'shipment': number, 'reopen': form.get('reopen') == '1'}
                line = {'shipment_line': (form.get('line') or '1').strip()}
                if (form.get('quantity') or '').strip():
                    line['quantity'] = form['quantity'].strip()
                raw['lines'] = [line]
                if (form.get('date') or '').strip():
                    raw['date'] = form['date'].strip()
                out = run(request, 'shipment return', raw, company_id, headers=headers(reason))
                return shipment_view(request, company_id, number, returned=out)
            if action == 'void_return':
                out = run(request, 'shipment-return void', {'shipment_return': (form.get('return') or '').strip()},
                          company_id, headers=headers(reason))
                return shipment_view(request, company_id, number, returned=out)
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'action', 'problem': 'unknown action'}]})
        except BookflowError as e:
            if e.code in _PASS:
                return page_error(request, e)
            return shipment_view(request, company_id, number, error=e.to_dict(), status_code=400)

    @app.post('/c/{company_id}/shipments/{number}')
    async def shipment_page_submit(request: Request, company_id: str, number: str):
        form = {k: str(v) for k, v in (await request.form()).items()}
        try:
            return await run_in_threadpool(shipment_submit, request, company_id, number, form)
        except BookflowError as e:
            return page_error(request, e)
