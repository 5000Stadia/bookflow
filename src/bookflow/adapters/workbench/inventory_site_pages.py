"""Inventory sites and transfers (R104 increment 1): one page for where stock is and moving it.

The page lists each site with what it holds (Quantity on Hand by Site), the site list with a form to add
a site, a Transfer Inventory form (from site, to site, item and quantity lines), and the latest transfers
with Void. Every write goes through its command (`inventory-site create`, `inventory-transfer post`,
`inventory-transfer void`) with a reason for the audit trail; a transfer posts nothing.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.clock import today_iso
from bookflow.core.errors import BookflowError

LINES = 5


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, values=None, result=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not role_allows('inventory-site list', company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'item', 'required_role': 'member'})
        sites = run(request, 'inventory-site list', {'include_inactive': True}, company_id)['sites']
        as_of = (request.query_params.get('as_of') or '').strip() or today_iso()
        chosen = (request.query_params.get('site') or '').strip()
        stock = run(request, 'report quantity-by-site', {'as_of': as_of, 'limit': 200,
                                                         **({'site': chosen} if chosen else {})}, company_id)
        transfers = run(request, 'inventory-transfer query', {'limit': 25, 'include_voided': True}, company_id)['transfers']
        try:
            items = sorted(row['full_name'] for row in run(request, 'item list', {'limit': 200}, company_id)['items']
                           if row.get('type') in ('inventory_part', 'inventory_assembly') and row.get('active', True))
        except BookflowError:
            items = []
        active = [site['name'] for site in sites if site['active']]
        default_from = next((site['name'] for site in sites if site['is_default']), active[0] if active else '')
        default_to = next((name for name in active if name != default_from), default_from)
        return render('inventory_sites.html', request, status_code=status_code, company=company,
                      default_from=default_from, default_to=default_to,
                      company_id=company['company_id'], sites=sites, stock=stock['rows'], as_of=as_of, chosen=chosen,
                      transfers=transfers, items=items, values=values or {}, result=result, error=error, lines=LINES,
                      can_write=role_allows('inventory-transfer post', company, credential(request)), today=today_iso())

    @app.get('/c/{company_id}/inventory/sites')
    def sites_page(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, form):
        action = form.get('action') or ''
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        try:
            if action == 'site':
                raw = {'name': (form.get('site_name') or '').strip()}
                if (form.get('site_description') or '').strip():
                    raw['description'] = form['site_description'].strip()
                out = run(request, 'inventory-site create', raw, company_id, headers=headers)
                result = f"Added site {out['name']}."
            elif action == 'transfer':
                lines = []
                for n in range(LINES):
                    item, quantity = (form.get(f'item_{n}') or '').strip(), (form.get(f'quantity_{n}') or '').strip()
                    if item or quantity:
                        lines.append({'item': item, 'quantity': quantity})
                if not lines:
                    raise BookflowError('E_VALIDATION', message='Enter at least one item and quantity to move.',
                                        details={'fields': [{'field': 'lines', 'problem': 'enter at least one line'}]})
                raw = {'from_site': (form.get('from_site') or '').strip(), 'to_site': (form.get('to_site') or '').strip(),
                       'lines': lines}
                for key in ('date', 'memo'):
                    if (form.get(key) or '').strip():
                        raw[key] = form[key].strip()
                out = run(request, 'inventory-transfer post', raw, company_id, headers=headers)
                after = '; '.join(f"{r['site_name']} {r['quantity_on_hand']} {r['item_name']}" for r in out['on_hand_after'])
                result = f"Transfer {out['number']} moved the stock; nothing was posted. Now: {after}."
                if out['warnings']:
                    result += ' ' + ' '.join(out['warnings'])
            elif action.startswith('void:'):
                _, number, version = action.split(':', 2)
                out = run(request, 'inventory-transfer void', {'transfer': number, 'expected_version': int(version)},
                          company_id, headers=headers)
                result = f"Voided transfer {out['number']}; its stock is back where it came from."
            else:
                return page(request, company_id)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, values=dict(form), error=e.to_dict(), status_code=400)
        return page(request, company_id, result=result)

    @app.post('/c/{company_id}/inventory/sites')
    async def sites_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
