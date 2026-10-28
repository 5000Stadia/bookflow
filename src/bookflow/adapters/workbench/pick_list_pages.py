"""Pick lists in the browser (R181 increment 3): the list, the pick screen and the printed pick list.

``/inventory/pick-lists`` lists the lists and makes one (site, the orders or every order ready to ship, only whole
orders, picker, date). ``/inventory/pick-lists/{number}`` is the pick screen, built for a phone like the count screen:
the Scan field pinned on top (a bin label sets the current bin, an item adds 1 to the next unfilled line for it there,
``6*`` sets 6, a serial label picks that unit, a lot label sets the lot for the next scans), a quantity keypad, the
lines grouped by bin in pick order with short lines marked, then Close list, Cancel and, per order, Ship from the
list. ``/print`` is the printed list, grouped by bin with each order's quantity. Picking is an optional layer: while
the company has it off every page answers with the switch's own message.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.clock import today_iso
from bookflow.core.errors import BookflowError

_PASS = ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER')


def _groups(lines):
    """``[(heading, lines)]`` in pick order: the lines arrive sorted by bin, so consecutive runs share a heading."""
    out = []
    for line in lines:
        heading = ('Bin ' + line['bin']) if line.get('bin') else ('Short' if line['short'] else 'No bin')
        if not out or out[-1][0] != heading:
            out.append((heading, []))
        out[-1][1].append(line)
    return out


def mount(app, *, render, run, credential, page_error, role_allows):
    def _company(request, company_id):
        company = run(request, 'company show', {}, company_id)
        if not company.get('info', {}).get('pick_and_pack_enabled'):
            from bookflow.company.pick_lists import disabled_error
            raise disabled_error()
        return company

    def headers(form, default=None):
        reason = (form.get('ctx:reason') or '').strip() or default
        return {'X-Bookflow-Reason': reason} if reason else None

    def list_page(request, company_id, *, values=None, error=None, status_code=200):
        company = _company(request, company_id)
        query = {'limit': 100}
        for key in ('status', 'sales_order'):
            if request.query_params.get(key):
                query[key] = request.query_params[key]
        listing = run(request, 'pick-list query', query, company_id)
        sites = run(request, 'inventory-site list', {}, company_id)['sites']
        values = values if values is not None else {'orders': request.query_params.get('orders', '')}
        return render('pick_lists.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], listing=listing, sites=sites, values=values, error=error,
                      today=today_iso(), filters=query,
                      can_write=role_allows('pick-list create', company, credential(request)))

    def pick_page(request, company_id, number, *, values=None, result=None, error=None, status_code=200):
        company = _company(request, company_id)
        shown = run(request, 'pick-list show', {'pick_list': number}, company_id)
        try:
            bins = [row['full_name'] for row in run(request, 'bin list', {'site': shown['site_id']}, company_id)['bins']]
        except BookflowError:
            bins = []
        from bookflow.adapters.workbench.item_picture_pages import thumbs as item_thumbs
        thumbs = item_thumbs(lambda name, raw, company: run(request, name, raw, company), company_id,
                             [line['item_id'] for line in shown['lines']])
        return render('pick_list.html', request, status_code=status_code, company=company, thumbs=thumbs,
                      company_id=company['company_id'], pick=shown, groups=_groups(shown['lines']), bins=bins,
                      values=values or {}, result=result, error=error, today=today_iso(),
                      can_enter=role_allows('pick-list enter', company, credential(request)),
                      can_ship=role_allows('shipment post', company, credential(request)))

    @app.get('/c/{company_id}/inventory/pick-lists')
    def pick_lists_page(request: Request, company_id: str):
        try:
            return list_page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/inventory/pick-lists/new')
    def pick_list_new(request: Request, company_id: str):
        try:
            return list_page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def create(request, company_id, form):
        raw = {'ready': form.get('ready') == 'on', 'complete_only': form.get('complete_only') == 'on'}
        for key in ('site', 'date', 'picker', 'memo'):
            if (form.get(key) or '').strip():
                raw[key] = form[key].strip()
        orders = [part.strip() for part in (form.get('orders') or '').split(',') if part.strip()]
        if orders and not raw['ready']:
            raw['orders'] = orders
        try:
            out = run(request, 'pick-list create', raw, company_id, headers=headers(form, 'Pick list'))
        except BookflowError as e:
            if e.code in _PASS:
                raise
            return list_page(request, company_id, values=dict(form), error=e.to_dict(),
                             status_code=200 if request.headers.get('HX-Request') else 400)
        notes = ' '.join(out.get('notes') or [])
        return pick_page(request, company_id, out['number'],
                         result=f"Pick list {out['number']} made: {len(out['lines'])} lines to pick." +
                         (f' {notes}' if notes else ''))

    @app.post('/c/{company_id}/inventory/pick-lists')
    async def pick_list_create(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(create, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/inventory/pick-lists/{number}')
    def pick_list_page(request: Request, company_id: str, number: str):
        try:
            return pick_page(request, company_id, number)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/inventory/pick-lists/{number}/print')
    def pick_list_print(request: Request, company_id: str, number: str):
        try:
            company = _company(request, company_id)
            shown = run(request, 'pick-list show', {'pick_list': number}, company_id)
            return render('pick_list_print.html', request, company=company, company_id=company['company_id'],
                          pick=shown, groups=_groups(shown['lines']))
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, number, form):
        action = form.get('action') or ''
        try:
            if action == 'enter':
                entry = {'mode': 'set' if form.get('mode') == 'set' else 'add'}
                what = (form.get('code') or '').strip()
                line = (form.get('line') or '').strip()
                if what:
                    entry['code'] = what
                elif line:
                    entry['line'] = line
                else:
                    raise BookflowError('E_VALIDATION', message='Scan or type an item first.',
                                        details={'fields': [{'field': 'code', 'problem': 'scan or type an item'}]})
                for key in ('bin', 'lot', 'serial_number', 'quantity'):
                    if (form.get(key) or '').strip():
                        entry[key] = form[key].strip()
                out = run(request, 'pick-list enter', {'pick_list': number, 'entries': [entry]}, company_id,
                          headers=headers(form, 'Picked'))
                hit = out['entered'][0]
                result = (f"{hit['item_name']}" + (f" in bin {hit['bin']}" if hit.get('bin') else '') +
                          f" for sales order {hit['sales_order']}: picked {hit['picked']} of {hit['quantity']}.")
                if out.get('warnings'):
                    result += ' ' + ' '.join(out['warnings'])
            elif action in ('close', 'cancel'):
                out = run(request, f'pick-list {action}', {'pick_list': number}, company_id,
                          headers=headers(form, 'Done picking' if action == 'close' else None))
                result = f"Pick list {out['number']} {out['status']}." + (
                    ' ' + ' '.join(out['warnings']) if out.get('warnings') else '')
            elif action == 'ship':
                raw = {'pick_list': number}
                if (form.get('sales_order') or '').strip():
                    raw['sales_order'] = form['sales_order'].strip()
                if (form.get('date') or '').strip():
                    raw['date'] = form['date'].strip()
                out = run(request, 'shipment post', raw, company_id, headers=headers(form, 'Shipped what was picked'))
                result = (f"Shipment {out['number']} posted for sales order {out['sales_order_number']}: "
                          + ', '.join(f"{l['quantity']} {l['item_name']}" for l in out['lines']) + '.')
            else:
                return pick_page(request, company_id, number)
        except BookflowError as e:
            if e.code in _PASS:
                raise
            # htmx swaps only a 2xx answer: the page carrying the error is the answer to a scan.
            return pick_page(request, company_id, number, values=dict(form), error=e.to_dict(),
                             status_code=200 if request.headers.get('HX-Request') else 400)
        kept = {'bin': form.get('bin', ''), 'lot': form.get('lot', '')} if action == 'enter' else {}
        return pick_page(request, company_id, number, result=result, values=kept)

    @app.post('/c/{company_id}/inventory/pick-lists/{number}')
    async def pick_list_submit(request: Request, company_id: str, number: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, number, form)
        except BookflowError as e:
            return page_error(request, e)
