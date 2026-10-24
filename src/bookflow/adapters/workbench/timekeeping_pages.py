"""Weekly Timesheet, the time clock and the job calendar (R106), on the `timesheet`, `clock` and `schedule` commands.

- /c/<company>/timesheet: the anchor's Weekly Timesheet. One person's week, a row per customer:job,
  service item, class, billable and notes, a cell per day, totals by row, day and week; billed cells
  are locked with their note; Previous / Next week and Copy last sheet. At phone width each row is a
  card with its seven days.
- /c/<company>/clock: who is clocked in, a clock-in form and a Clock out button per running clock (with
  an end time for a forgotten one).
- /c/<company>/calendar: the job calendar by employee or job, a week grid at desk width and a list on a
  phone, day / week / month, with a form to schedule someone.

Every form submits through htmx (hx-post), as the generated forms do, so the workbench header is sent.
Every write goes through the command it names, with a reason for the audit trail when one is given.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.adapters.workbench.mileage_pages import _names
from bookflow.core.errors import BookflowError

BLANK_ROWS = 3


def _headers(form):
    reason = (form.get('ctx:reason') or '').strip()
    return {'X-Bookflow-Reason': reason} if reason else None


def mount(app, *, render, run, credential, page_error, role_allows):
    def lists(request, company_id):
        return dict(customers=_names(run, request, company_id, 'customer list', 'full_name'),
                    items=_names(run, request, company_id, 'item list', 'full_name'),
                    employees=_names(run, request, company_id, 'employee list', 'name'))

    def allowed(request, company, name):
        return role_allows(name, company, credential(request))

    # ------------------------------------------------------------------ timesheet

    def timesheet(request, company_id, *, employee=None, week=None, copy=False, values=None, result=None, error=None,
                  status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not allowed(request, company, 'timesheet show'):
            raise BookflowError('E_PERMISSION', details={'capability': 'customer-work', 'required_role': 'member'})
        employee = employee if employee is not None else (request.query_params.get('employee') or '').strip()
        week = week or (request.query_params.get('week') or '').strip() or None
        copy = copy or request.query_params.get('copy') == '1'
        sheet, sheet_error = None, None
        options = lists(request, company_id)
        if not employee and len(options['employees']) == 1:
            employee = options['employees'][0]
        try:
            raw = {k: v for k, v in (('employee', employee or None), ('week', week)) if v}
            sheet = run(request, 'timesheet copy-last' if copy else 'timesheet show', raw, company_id)
        except BookflowError as e:
            sheet_error = e.to_dict() if employee else None
        prev = next_ = None
        if sheet:
            start = date.fromisoformat(sheet['week_start'])
            prev, next_ = (start - timedelta(days=7)).isoformat(), (start + timedelta(days=7)).isoformat()
        return render('timesheet.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], sheet=sheet, employee=sheet['employee_name'] if sheet else employee,
                      prev_week=prev, next_week=next_,
                      day_names=[date.fromisoformat(d).strftime('%a') for d in sheet['days']] if sheet else [], blank_rows=BLANK_ROWS, values=values or {}, result=result,
                      error=error or sheet_error, can_write=allowed(request, company, 'timesheet save'), **options)

    @app.get('/c/{company_id}/timesheet')
    def timesheet_page(request: Request, company_id: str):
        try:
            return timesheet(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def save_timesheet(request, company_id, form):
        employee, week = (form.get('employee') or '').strip(), (form.get('week') or '').strip()
        rows = []
        count = int(form.get('row_count') or 0)
        for n in range(count):
            customer, item = (form.get(f'customer_{n}') or '').strip(), (form.get(f'item_{n}') or '').strip()
            hours = [(form.get(f'h_{n}_{d}') or '').strip() or None for d in range(7)]
            if not customer and not item and not any(hours):
                continue
            row = dict(customer=customer, item=item, billable=form.get(f'billable_{n}') == 'yes', hours=hours,
                       notes=(form.get(f'notes_{n}') or '').strip() or None)
            if (form.get(f'class_{n}') or '').strip():
                row['class_id'] = form[f'class_{n}'].strip()
            rows.append(row)
        raw = dict(employee=employee or None, week=week, rows=rows)
        if form.get('sheet_version'):
            raw['expected_sheet_version'] = form['sheet_version']
        try:
            out = run(request, 'timesheet save', {k: v for k, v in raw.items() if v is not None}, company_id,
                      headers=_headers(form))
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return timesheet(request, company_id, employee=employee, week=week, values=form, error=e.to_dict(),
                             status_code=400)
        result = (f"Saved {out['employee_name']}'s week of {out['week_start']}: {out['week_total']} hours, "
                  f"{len(out['changes'])} time entr{'y' if len(out['changes']) == 1 else 'ies'} written."
                  if out['changed'] else 'Nothing changed.')
        return timesheet(request, company_id, employee=out['employee_name'], week=out['week_start'], result=result)

    @app.post('/c/{company_id}/timesheet')
    async def timesheet_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(save_timesheet, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

    # ------------------------------------------------------------------ clock

    def clock_page(request, company_id, *, values=None, result=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not allowed(request, company, 'clock status'):
            raise BookflowError('E_PERMISSION', details={'capability': 'customer-work', 'required_role': 'member'})
        status = run(request, 'clock status', {}, company_id)
        return render('time_clock.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], status=status, values=values or {}, result=result, error=error,
                      can_write=allowed(request, company, 'clock in'), **lists(request, company_id))

    @app.get('/c/{company_id}/clock')
    def clock_get(request: Request, company_id: str):
        try:
            return clock_page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def clock_submit_sync(request, company_id, form):
        action = form.get('action') or ''
        headers = _headers(form)
        try:
            if action == 'in':
                raw = {k: form[k].strip() for k in ('employee', 'customer', 'item', 'class_id', 'notes')
                       if (form.get(k) or '').strip()}
                raw['billable'] = form.get('billable') == 'yes'
                out = run(request, 'clock in', raw, company_id, headers=headers)
                result = f"{out['employee_name']} clocked in at {out['clock_in_local'][11:]}."
            elif action.startswith('out:') or action.startswith('cancel:'):
                verb, employee = action.split(':', 1)
                raw = {'employee': employee}
                if verb == 'out' and (form.get(f'end_{employee}') or '').strip():
                    raw['end'] = form[f'end_{employee}'].strip()
                out = run(request, 'clock ' + verb, raw, company_id, headers=headers)
                if verb == 'out':
                    hours = ', '.join(f"{r['hours']} h on {r['date']}" for r in out['recorded_hours']) or 'no time'
                    result = f"{out['employee_name']} clocked out: {hours}."
                else:
                    result = f"{out['employee_name']}'s clock was cancelled; no time recorded."
            else:
                return clock_page(request, company_id)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return clock_page(request, company_id, values=form, error=e.to_dict(), status_code=400)
        return clock_page(request, company_id, result=result)

    @app.post('/c/{company_id}/clock')
    async def clock_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(clock_submit_sync, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

    # ------------------------------------------------------------------ calendar

    def calendar_page(request, company_id, *, values=None, result=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not allowed(request, company, 'schedule calendar'):
            raise BookflowError('E_PERMISSION', details={'capability': 'customer-work', 'required_role': 'member'})
        q = request.query_params
        raw = {k: (q.get(k) or '').strip() for k in ('employee', 'customer', 'view', 'date') if (q.get(k) or '').strip()}
        raw.setdefault('view', 'week')
        try:
            cal = run(request, 'schedule calendar', raw, company_id)
        except BookflowError as e:
            cal, error = run(request, 'schedule calendar', {'view': raw['view']}, company_id), error or e.to_dict()
        start = date.fromisoformat(cal['date_from'])
        step = {'day': 1, 'week': 7}.get(cal['view'])
        if step:
            prev, next_ = (start - timedelta(days=step)).isoformat(), (start + timedelta(days=step)).isoformat()
        else:
            prev = (start - timedelta(days=1)).replace(day=1).isoformat()
            next_ = (date.fromisoformat(cal['date_to']) + timedelta(days=1)).isoformat()
        return render('job_calendar.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], cal=cal, query=raw,
                      weekday=lambda d: date.fromisoformat(d).strftime('%a'), prev_date=prev, next_date=next_,
                      values=values or {}, result=result, error=error,
                      can_write=allowed(request, company, 'schedule create'), **lists(request, company_id))

    @app.get('/c/{company_id}/calendar')
    def calendar_get(request: Request, company_id: str):
        try:
            return calendar_page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def calendar_submit_sync(request, company_id, form):
        action = form.get('action') or ''
        headers = _headers(form)
        try:
            if action == 'schedule':
                raw = {k: form[k].strip() for k in ('employee', 'customer', 'work_order', 'notes')
                       if (form.get(k) or '').strip()}
                day = (form.get('day') or '').strip()
                raw['starts'] = f"{day}T{(form.get('start_time') or '').strip()}"
                raw['ends'] = f"{day}T{(form.get('end_time') or '').strip()}"
                out = run(request, 'schedule create', raw, company_id, headers=headers)
                result = (f"Scheduled {out['employee_name']} on {out['customer_name']} {out['starts'].replace('T', ' ')}"
                          f" to {out['ends'][11:]}." + (' ' + ' '.join(out['warnings']) if out['warnings'] else ''))
            elif action.startswith('cancel:'):
                _, entry, version = action.split(':', 2)
                out = run(request, 'schedule cancel', {'entry': entry, 'expected_version': int(version)}, company_id,
                          headers=headers or {'X-Bookflow-Reason': 'Cancelled from the calendar'})
                result = f"Cancelled {out['employee_name']} on {out['customer_name']} {out['starts'].replace('T', ' ')}."
            else:
                return calendar_page(request, company_id)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return calendar_page(request, company_id, values=form, error=e.to_dict(), status_code=400)
        return calendar_page(request, company_id, result=result)

    @app.post('/c/{company_id}/calendar')
    async def calendar_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(calendar_submit_sync, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
