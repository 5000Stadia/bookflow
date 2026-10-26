"""Physical counts (R104 increment 2): the Counts list and the count screen, built for a phone.

``/inventory/counts`` lists open and recorded counts and starts a new one (site, date, blind).
``/inventory/counts/{number}`` is the count screen: an item-or-code field pinned on top with the
quantity beside it (the phone's number keypad), each entry appended through `inventory-count enter`,
the lines grouped by bin and place with counted (and, unless blind, expected and the difference), and Post,
which opens the preview -- every item's difference and value, and the documents entered after
counting began -- before `inventory-count post` records it with the preview's fingerprint. Every
write goes through its command with a reason for the audit trail, and every form submits through htmx.

Bins and cycle counts (R104 increment 5): a count may be of named bins, or of the next items due by cycle
class; the Counts page lists each site's due items with "Count the next N". On the count screen a scanned
bin label sets the current bin (the Bin field), and entries count in it.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.clock import today_iso
from bookflow.core.errors import BookflowError


def _groups(lines, by_bin):
    """``[(heading, lines)]``: by bin (no bin last) at a site that tracks bins, else by place."""
    out = {}
    for line in lines:
        key = (line.get('bin') or '\uffff') if by_bin else line['place']
        out.setdefault(key, []).append(line)
    return [(('Bin ' + key if key != '\uffff' else 'No bin') if by_bin else key.capitalize(), rows)
            for key, rows in sorted(out.items(), key=lambda kv: kv[0].casefold())]


def mount(app, *, render, run, credential, page_error, role_allows):
    def _company(request, company_id):
        company = run(request, 'company show', {}, company_id)
        if not role_allows('inventory-count query', company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'item', 'required_role': 'member'})
        return company

    def _accounts(request, company_id):
        try:
            rows = run(request, 'account query', {'limit': 200}, company_id)['items']
        except BookflowError:
            return []
        return [row.get('full_name') or row.get('name') for row in rows
                if row.get('type') in ('cost_of_goods_sold', 'expense', 'other_expense') and row.get('active', True)]

    def list_page(request, company_id, *, values=None, error=None, status_code=200):
        company = _company(request, company_id)
        sites = run(request, 'inventory-site list', {}, company_id)['sites']
        counts = run(request, 'inventory-count query', {'limit': 50}, company_id)['counts']
        due = []
        as_of = (request.query_params.get('as_of') or '').strip() or today_iso()
        for site in sites:
            try:
                answer = run(request, 'cycle-count due', {'site': site['name'], 'limit': 10, 'as_of': as_of}, company_id)
            except BookflowError:
                continue
            if answer['due'] or answer['not_due']:
                due.append(answer)
        return render('inventory_counts.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], sites=sites, counts=counts, values=values or {}, error=error,
                      due=due, any_bins=any(site['tracks_bins'] for site in sites),
                      today=today_iso(), can_write=role_allows('inventory-count create', company, credential(request)))

    def count_page(request, company_id, number, *, values=None, result=None, error=None, preview=None,
                   status_code=200):
        company = _company(request, company_id)
        count = run(request, 'inventory-count show', {'count': number}, company_id)
        try:
            bins = [row['full_name'] for row in run(request, 'bin list', {'site': count['site_id']}, company_id)['bins']]
        except BookflowError:
            bins = []
        return render('inventory_count.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], count=count, values=values or {}, result=result, error=error,
                      bins=bins, groups=_groups(count['lines'], bool(bins)),
                      preview=preview, accounts=_accounts(request, company_id),
                      can_enter=role_allows('inventory-count enter', company, credential(request)),
                      can_post=role_allows('inventory-count post', company, credential(request)))

    @app.get('/c/{company_id}/inventory/counts')
    def counts_page(request: Request, company_id: str):
        try:
            return list_page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/inventory/counts/{number}')
    def one_count_page(request: Request, company_id: str, number: str):
        try:
            return count_page(request, company_id, number)
        except BookflowError as e:
            return page_error(request, e)

    def _headers(form):
        return {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None

    def create(request, company_id, form):
        raw = {'blind': form.get('blind') == 'on'}
        for key in ('site', 'date', 'memo'):
            if (form.get(key) or '').strip():
                raw[key] = form[key].strip()
        items = [part.strip() for part in (form.get('items') or '').split(',') if part.strip()]
        bins = [part.strip() for part in (form.get('bins') or '').split(',') if part.strip()]
        if form.get('cycle') in ('on', '1', 'true'):
            raw['cycle'] = True
            if (form.get('limit') or '').strip():
                raw['limit'] = int(form['limit']) if form['limit'].strip().isdigit() else 0
        elif bins:
            raw['bins'] = bins
        elif items:
            raw['items'] = items
        try:
            out = run(request, 'inventory-count create', raw, company_id, headers=_headers(form))
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return list_page(request, company_id, values=dict(form), error=e.to_dict(), status_code=400)
        return count_page(request, company_id, out['number'],
                          result=f"Count {out['number']} of {out['site_name']} started: {len(out['lines'])} lines to count.")

    @app.post('/c/{company_id}/inventory/counts')
    async def counts_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(create, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, number, form):
        action = form.get('action') or ''
        headers = _headers(form)
        preview = None
        try:
            if action == 'enter':
                what = (form.get('scan') or '').strip()
                quantity = (form.get('quantity') or '').strip() or '1'
                if not what:
                    raise BookflowError('E_VALIDATION', message='Scan or type an item first.',
                                        details={'fields': [{'field': 'scan', 'problem': 'scan or type an item'}]})
                entry = {'item': what, 'quantity': quantity, 'mode': 'add' if form.get('mode') == 'add' else 'set'}
                if (form.get('bin') or '').strip():
                    entry['bin'] = form['bin'].strip()
                out = run(request, 'inventory-count enter', {'count': number, 'entries': [entry]}, company_id,
                          headers=headers or {'X-Bookflow-Reason': 'Counted'})
                line = out['entered'][0]
                result = f"{line['item_name']}" + (f" in bin {line['bin']}" if line.get('bin') else '') + f": counted {line['counted']}" + (
                    f", expected {line['expected']}, difference {line['difference']}." if line['expected'] is not None
                    else '.') + f" {out['uncounted_lines']} lines still to count."
            elif action in ('preview', 'post'):
                raw = {'count': number, 'expected_version': int(form.get('expected_version') or 0),
                       'adjustment_account': (form.get('adjustment_account') or '').strip()}
                preview = run(request, 'inventory-count post', raw, company_id, headers=headers, dry_run=True)
                if action == 'post':
                    raw['preview_fingerprint'] = (form.get('preview_fingerprint') or '').strip()
                    out = run(request, 'inventory-count post', raw, company_id, headers=headers)
                    preview = None
                    result = f"Count {out['number']} recorded: " + (
                        '; '.join(f"{d['item_name']} {d['difference']} ({d['value']['amount']})"
                                  for d in out['differences'] if d['difference'] != '0') or 'no differences') + '.'
                else:
                    result = None
            elif action in ('cancel', 'void'):
                out = run(request, f'inventory-count {action}', {'count': number,
                                                                  'expected_version': int(form.get('expected_version') or 0)},
                          company_id, headers=headers)
                result = f"Count {out['number']} {out['status']}."
            else:
                return count_page(request, company_id, number)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return count_page(request, company_id, number, values=dict(form), error=e.to_dict(), preview=preview,
                              status_code=400)
        kept = {'bin': form.get('bin', '')} if action == 'enter' else {}
        return count_page(request, company_id, number, result=result, preview=preview,
                          values={'adjustment_account': form.get('adjustment_account', ''),
                                  'ctx:reason': form.get('ctx:reason', ''), **kept} if preview else kept)

    @app.post('/c/{company_id}/inventory/counts/{number}')
    async def count_submit(request: Request, company_id: str, number: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, number, form)
        except BookflowError as e:
            return page_error(request, e)
