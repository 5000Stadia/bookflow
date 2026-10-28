"""The fulfillment worksheet in the browser (R181 increment 5, design/pick-pack-ship.md §8 Browser).

``/inventory/fulfillment`` opens on the orders that can ship now, with tabs (To pick, Picking, To pack while picking
and packing are on; To ship otherwise; Shipped not invoiced; All), the report's sort and filters, and per order its
lines with to ship and can ship now. Selected orders take batch actions, each making one document per order: Ship
(`shipment post` from the order), Create invoices (`sales-order invoice`), Print packing slips (one PDF, a slip per
order: its live draft, else its latest shipment); while picking is on, Create pick list (one list for the selected
orders) and Print pick lists (each live list holding them). Writes go through htmx, as a cookie session requires;
prints are plain GETs opened in a new tab. A batch is not one undoable unit: each order's document stands alone and is
voided on its own (the batch ledger is R186's candidate).
"""
from __future__ import annotations

from dataclasses import replace

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.clock import today_iso
from bookflow.core.errors import BookflowError

_PASS = ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER')
_FILTERS = ('site', 'customer', 'newly_shippable', 'sort')


def _tabs(picking):
    """``[(key, label, filters)]``: the worksheet opens on the first."""
    tabs = [('ready', 'Can ship now', {'can_ship': True})]
    if picking:
        tabs += [('to_pick', 'To pick', {'status': 'to_pick,partly_shipped'}), ('picking', 'Picking', {'status': 'picking'}),
                 ('to_pack', 'To pack', {'status': 'picked'})]
    else:
        tabs += [('to_ship', 'To ship', {'status': 'to_pick,partly_shipped'})]
    tabs += [('waiting', 'Shipped not invoiced', {'shipped_not_invoiced': True}), ('all', 'All', {})]
    return tabs


def _orders(form_or_query):
    getter = getattr(form_or_query, 'getlist', None)
    values = getter('order') if getter else form_or_query.get('order', [])
    if isinstance(values, str):
        values = [values]
    return [value.strip() for value in values if value and value.strip()]


def mount(app, *, render, run, credential, page_error, role_allows):
    def worksheet(request, company_id, *, query=None, results=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        picking = bool(company.get('info', {}).get('pick_and_pack_enabled'))
        params = query if query is not None else dict(request.query_params)
        tabs = _tabs(picking)
        tab = params.get('tab') or 'ready'
        chosen = next((t for t in tabs if t[0] == tab), tabs[0])
        raw = dict(chosen[2], limit=200)
        for key in _FILTERS:
            if (params.get(key) or '').strip():
                raw[key] = params[key].strip()
        if params.get('complete_only'):
            raw['complete_only'] = True
        report = run(request, 'report order-fulfillment', raw, company_id)
        sites = run(request, 'inventory-site list', {}, company_id)['sites']
        cred = credential(request)
        return render('fulfillment.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], report=report, tabs=tabs, tab=chosen[0], params=params,
                      sites=sites, picking=picking, results=results, error=error, today=today_iso(),
                      can_ship=role_allows('shipment post', company, cred),
                      can_invoice=role_allows('sales-order invoice', company, cred),
                      can_pick=picking and role_allows('pick-list create', company, cred))

    @app.get('/c/{company_id}/inventory/fulfillment')
    def fulfillment_page(request: Request, company_id: str):
        try:
            return worksheet(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def batch(request, company_id, form, orders):
        action = form.get('action') or ''
        reason = (form.get('ctx:reason') or '').strip()
        date = (form.get('date') or '').strip()
        query = {key: form.get(key, '') for key in ('tab', 'complete_only', *_FILTERS)}
        if not orders:
            return worksheet(request, company_id, query=query, error={
                'code': 'E_VALIDATION', 'message': 'Select the orders first.', 'details': {}},
                status_code=200 if request.headers.get('HX-Request') else 400)
        results = []
        if action == 'pick':
            raw = {'orders': orders, **({'date': date} if date else {})}
            try:
                out = run(request, 'pick-list create', raw, company_id,
                          headers={'X-Bookflow-Reason': reason or 'Pick list from the fulfillment worksheet'})
                results.append(dict(order=', '.join(orders), ok=True, text=f"Pick list {out['number']} made: "
                                    f"{len(out['lines'])} lines to pick.", link=f"/inventory/pick-lists/{out['number']}"))
            except BookflowError as e:
                if e.code in _PASS:
                    raise
                results.append(dict(order=', '.join(orders), ok=False, text=e.message or e.code))
            return worksheet(request, company_id, query=query, results=results)
        for number in orders:
            # One document per order, each its own command: a refusal of one leaves the others standing.
            try:
                if action == 'ship':
                    out = run(request, 'shipment post', {'sales_order': number, **({'date': date} if date else {})},
                              company_id, headers={'X-Bookflow-Reason': reason or 'Shipped from the worksheet'})
                    results.append(dict(order=number, ok=True, link=f"/shipments/{out['number']}",
                                        text=f"Shipment {out['number']}: " + ', '.join(
                                            f"{line['quantity']} {line['item_name']}" for line in out['lines'])))
                elif action == 'invoice':
                    order = run(request, 'sales-order show', {'sales_order': number}, company_id)
                    # The key is the order at this version: a second click on the same page makes no second invoice.
                    out = run(request, 'sales-order invoice', {
                        'sales_order': number, 'expected_version': order['version'],
                        'conversion_key': f"worksheet {order['id']} v{order['version']}", **({'date': date} if date else {})},
                              company_id, headers={'X-Bookflow-Reason': reason or 'Invoiced from the worksheet'})
                    invoice = out.get('revision', {}).get('number') or out.get('number') or out.get('id')
                    results.append(dict(order=number, ok=True, text=f'Invoice {invoice} made.',
                                        link=f"/invoice/{out.get('id')}" if out.get('id') else None))
                else:
                    raise BookflowError('E_VALIDATION', message='Choose Ship or Create invoices.')
            except BookflowError as e:
                if e.code in _PASS:
                    raise
                results.append(dict(order=number, ok=False, text=e.message or e.code))
        return worksheet(request, company_id, query=query, results=results)

    @app.post('/c/{company_id}/inventory/fulfillment')
    async def fulfillment_submit(request: Request, company_id: str):
        form = await request.form()
        orders = _orders(form)
        try:
            return await run_in_threadpool(batch, request, company_id, dict(form), orders)
        except BookflowError as e:
            return page_error(request, e)

    def _read(request):
        return lambda name, raw, company: run(request, name, raw, company)

    @app.get('/c/{company_id}/inventory/fulfillment/packing-slips')
    def fulfillment_packing_slips(request: Request, company_id: str):
        """One PDF, a packing slip per selected order: its live draft, else its latest shipment."""
        from bookflow.adapters.workbench.document_print import _pdf
        from bookflow.documents.model import build
        from bookflow.documents.render import drawn
        try:
            documents, skipped = [], []
            for number in _orders(request.query_params):
                rows = run(request, 'shipment query', {'sales_order': number, 'limit': 50}, company_id)['rows']
                found = [row for row in rows if row['status'] == 'draft'] or \
                    [row for row in rows if row['status'] == 'shipped']
                if not found:
                    skipped.append(number)
                    continue
                document = build(_read(request), company_id, 'packing-slip', {'document': found[0]['number']})
                documents += list(document.parts) or [document]
            if not documents:
                raise BookflowError('E_VALIDATION', message=(
                    'None of the selected orders has a shipment or a draft to print a packing slip for: '
                    + ', '.join(skipped or ['no orders selected']) + '.'))
            whole = replace(documents[0], parts=tuple(documents), title='Packing slips', name='Packing slips')
            return _pdf(drawn('packing-slip', whole))
        except BookflowError as e:
            return page_error(request, e, company_id=company_id)

    @app.get('/c/{company_id}/inventory/fulfillment/pick-lists')
    def fulfillment_pick_lists(request: Request, company_id: str):
        """Every live pick list holding the selected orders, one after another, each on its own page."""
        from bookflow.adapters.workbench.pick_list_pages import _groups
        try:
            company = run(request, 'company show', {}, company_id)
            if not company.get('info', {}).get('pick_and_pack_enabled'):
                from bookflow.company.pick_lists import disabled_error
                raise disabled_error()
            numbers = []
            for order in _orders(request.query_params):
                for row in run(request, 'pick-list query', {'sales_order': order, 'limit': 100}, company_id)['rows']:
                    if row['status'] != 'cancelled' and row['number'] not in numbers:
                        numbers.append(row['number'])
            if not numbers:
                raise BookflowError('E_VALIDATION', message='None of the selected orders is on a live pick list.')
            picks = [run(request, 'pick-list show', {'pick_list': number}, company_id) for number in numbers]
            return render('fulfillment_pick_lists.html', request, company=company, company_id=company['company_id'],
                          picks=[(pick, _groups(pick['lines'])) for pick in picks])
        except BookflowError as e:
            return page_error(request, e)
