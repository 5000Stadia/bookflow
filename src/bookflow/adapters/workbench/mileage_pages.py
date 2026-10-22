"""Enter Vehicle Mileage: the anchor's window, on the `trip`, `vehicle` and `mileage-rate` commands (R118).

One page holds what the anchor spreads over Enter Vehicle Mileage, the Vehicle List and Mileage Rates:
a trip entry form (vehicle, trip start and end dates, odometer start and end or total miles, billable
with customer:job and item, class, notes), the trips recorded this year with their deduction and
billing status, the vehicles, and the rates by effective date with a form for each. A billable trip
links to `trip invoice` and an unpaid one can be chosen for `trip reimburse`; the two mileage reports
are linked at the top. Every write goes through the command it names, with a reason for the audit trail.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

TRIP_FIELDS = ('vehicle', 'trip_start_date', 'trip_end_date', 'odometer_start', 'odometer_end', 'total_miles',
               'customer', 'item', 'class_id', 'notes', 'billing_rate')


def _names(run, request, company_id, command, key):
    try:
        out = run(request, command, {'limit': 200}, company_id)
    except BookflowError:
        return []
    rows = out.get('items') or out.get('rows') or []
    return sorted({row.get(key) or row.get('name') for row in rows if row.get(key) or row.get('name')})


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, values=None, result=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not role_allows('trip query', company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'customer-work', 'required_role': 'member'})
        year = (request.query_params.get('year') or '').strip()
        trips = run(request, 'trip query', {'limit': 200, **({'date_from': year + '-01-01', 'date_to': year + '-12-31'}
                                                             if len(year) == 4 and year.isdigit() else {})}, company_id)
        vehicles = run(request, 'vehicle list', {'include_inactive': True}, company_id)['vehicles']
        rates = run(request, 'mileage-rate list', {}, company_id)['rates']
        can_write = role_allows('trip create', company, credential(request))
        return render('mileage.html', request, status_code=status_code, company=company, company_id=company['company_id'],
                      trips=trips['trips'], vehicles=vehicles, rates=rates, values=values or {}, result=result,
                      error=error, can_write=can_write, can_reimburse=role_allows('trip reimburse', company, credential(request)),
                      customers=_names(run, request, company_id, 'customer list', 'full_name'),
                      items=_names(run, request, company_id, 'item list', 'full_name'))

    @app.get('/c/{company_id}/mileage')
    def mileage_page(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, form):
        action = form.get('action')
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        try:
            if action == 'trip':
                raw = {key: form[key].strip() for key in TRIP_FIELDS if (form.get(key) or '').strip()}
                raw['billable'] = form.get('billable') == 'yes'
                out = run(request, 'trip create', raw, company_id, headers=headers)
                result = f"Recorded a trip of {out['miles']} miles on {out['trip_end_date']}."
            elif action == 'vehicle':
                raw = {'name': (form.get('vehicle_name') or '').strip()}
                if (form.get('vehicle_description') or '').strip():
                    raw['description'] = form['vehicle_description'].strip()
                out = run(request, 'vehicle create', raw, company_id, headers=headers)
                result = f"Added vehicle {out['name']}."
            elif action == 'rate':
                out = run(request, 'mileage-rate set', {'effective_date': (form.get('effective_date') or '').strip(),
                                                        'rate': (form.get('rate') or '').strip()}, company_id, headers=headers)
                result = f"Mileage rate {out['rate']} from {out['effective_date']}."
            elif (action or '').startswith('void:'):
                _, trip, version = action.split(':', 2)
                out = run(request, 'trip void', {'trip': trip, 'expected_version': int(version)}, company_id, headers=headers)
                result = f"Voided the trip of {out['miles']} miles on {out['trip_end_date']}."
            elif action == 'reimburse':
                raw = {'trips': [key[5:] for key in form if key.startswith('pick:')],
                       'pay_to': {'name_type': form.get('pay_type') or 'employee', 'name_id': (form.get('pay_to') or '').strip()},
                       'account': (form.get('bank') or '').strip(), 'expense_account': (form.get('expense') or '').strip(),
                       'date': (form.get('pay_date') or '').strip()}
                if not raw['trips']:
                    raise BookflowError('E_VALIDATION', message='Choose at least one trip to pay back.',
                                        details={'fields': [{'field': 'trips', 'problem': 'choose at least one trip'}]})
                out = run(request, 'trip reimburse', raw, company_id, headers=headers)
                result = f"Check {out['check_number'] or ''} for {out['total']['amount']} pays back {len(out['trips'])} trip(s)."
            else:
                return page(request, company_id)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, values=dict(form), error=e.to_dict(), status_code=400)
        return page(request, company_id, result=result)

    @app.post('/c/{company_id}/mileage')
    async def mileage_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
