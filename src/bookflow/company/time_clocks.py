"""The time clock (R106): clock in, clock out, and the time entries clocking out writes.

The anchor times work with a stopwatch on Enter Single Activity; here the clock is kept by the books,
so it survives closing the window and shows on the home page while it runs. One clock may be open
per person.

Clocking out writes the elapsed time as time entries through ``time_tracking``, exactly as
`time-activity create` does: one entry per company-local day the stretch touches (a night shift is
split at midnight), each rounded per the company preference -- none, or to the nearest 6 or 15
minutes -- to an exact quantity of hours. A day whose rounded time is zero records nothing.

A forgotten clock is not guessed at: once a clock has run past the maximum shift it is closed only
with a stated end time, and a stated shift may not exceed the maximum either.

An agent clocks a person in or out only on that person's own instruction: the person it acts for
must be the employee's linked login (`clock link`). A person may clock anyone.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c, timekeeping as tk
from bookflow.company.timekeeping_models import ClockOutput, ClockStatusOutput, ClockWriteOutput
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan
from bookflow.hub.users import common

RECORD = 'time_clock'
FUTURE_SLACK = timedelta(minutes=1)


def _open(s, employee_id: str) -> dict[str, Any] | None:
    t = c.time_clocks
    return tk.row(s, t, t.c.employee_id == employee_id, t.c.status == 'open')


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def _segments(s, start: datetime, end: datetime) -> list[tuple[str, int]]:
    rounding = tk.preferences(s)['clock_rounding_minutes']
    return [(day, tk.round_segment(seconds, rounding)) for day, seconds in tk.day_segments(s, start, end)]


def output(s, row: dict[str, Any], *, cls=ClockOutput, **extra) -> ClockOutput:
    names = tk.names(s, c.customers, [row['customer_id']])
    items = tk.names(s, c.items, [row['item_id']], 'name')
    person = tk.names(s, c.employees, [row['employee_id']], 'name')
    start = _moment(row['clock_in_at'])
    end = _moment(row['clock_out_at']) if row['clock_out_at'] else tk.now(s)
    seconds = max(0, int((end - start).total_seconds()))
    limit = tk.preferences(s)['max_shift_minutes'] * 60
    recorded = extra.pop('recorded_hours', None)
    if recorded is None and row['status'] == 'closed':
        recorded = [dict(date=d, hours=tk.total_text(q)) for d, q in _segments(s, start, end)]
    return cls(id=row['id'], version=row['version'], employee_id=row['employee_id'],
               employee_name=person.get(row['employee_id'], ''), customer_id=row['customer_id'],
               customer_name=names.get(row['customer_id']), item_id=row['item_id'], item_name=items.get(row['item_id']),
               class_id=row['class_id'], billable=row['billable'], notes=row['notes'], status=row['status'],
               clock_in_at=row['clock_in_at'], clock_in_local=tk.local_text(s, row['clock_in_at']),
               clock_out_at=row['clock_out_at'], clock_out_local=tk.local_text(s, row['clock_out_at']),
               end_stated=row['end_stated'], elapsed_hours=tk.total_text(tk.round_segment(seconds, 0)),
               over_max_shift=row['status'] == 'open' and seconds > limit,
               entries=json.loads(row['entries']) if row['entries'] else [], recorded_hours=recorded or [],
               cancel_reason=row['cancel_reason'], **extra)


def status(s, ctx, inp) -> ClockStatusOutput:
    t = c.time_clocks
    query = sa.select(t).where(t.c.status == 'open').order_by(t.c.clock_in_at)
    if inp.employee:
        query = query.where(t.c.employee_id == tk.employee(s, inp.employee)['id'])
    rows = [dict(r) for r in s.company.conn.execute(query).mappings()]
    prefs = tk.preferences(s)
    rounding = prefs['clock_rounding_minutes']
    return ClockStatusOutput(now=tk.iso(tk.now(s)), clocks=[output(s, r) for r in rows],
                             max_shift_hours=prefs['max_shift_minutes'] // 60,
                             clock_rounding='none' if rounding == 0 else str(rounding))


def _references(s, inp, base: dict[str, Any]) -> dict[str, Any]:
    from bookflow.company.items import resolve_item
    from bookflow.company.mileage import _class_id, _customer_id
    new = dict(base)
    given = inp.model_fields_set
    if 'customer' in given and inp.customer is not None:
        found = _customer_id(s, inp.customer)
        if not found['active']:
            raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'customer', 'record_id': found['id']})
        new['customer_id'] = found['id']
    if 'item' in given and inp.item is not None:
        found = resolve_item(s.company, inp.item)
        if not found['active']:
            raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'item', 'record_id': found['id']})
        new['item_id'] = found['id']
    if 'class_id' in given and inp.class_id is not None:
        new['class_id'] = _class_id(s, inp.class_id)['id']
    if 'billable' in given and inp.billable is not None:
        new['billable'] = bool(inp.billable)
    if 'notes' in given:
        new['notes'] = (inp.notes or '').strip() or new.get('notes')
    return new


def plan_in(s, ctx, inp) -> Plan:
    person = tk.target_employee(s, ctx, inp.employee, active=True)
    tk.require_own_instruction(s, ctx, person, 'clock in')
    running = _open(s, person['id'])
    if running:
        raise tk.invalid('employee', f"{person['name']} is already clocked in since "
                         f"{tk.local_text(s, running['clock_in_at'])}; clock out first", clock=running['id'])
    now = tk.now(s)
    start = tk.parse_moment(s, inp.at, 'at') if inp.at else now
    if start > now + clock_slack():
        raise tk.invalid('at', 'a clock cannot start in the future')
    overlap = _overlapping(s, person['id'], start)
    if overlap:
        raise tk.invalid('at', f"{person['name']} was already clocked from {tk.local_text(s, overlap['clock_in_at'])} "
                         f"to {tk.local_text(s, overlap['clock_out_at'])}; a new clock starts after that")
    if now - start > timedelta(minutes=tk.preferences(s)['max_shift_minutes']):
        raise tk.invalid('at', 'that is longer ago than the maximum shift; enter the time with `timesheet save` '
                         'or `time-activity create` instead')
    base = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), employee_id=person['id'],
                customer_id=None, item_id=None, class_id=None, billable=True, notes=None,
                clock_in_at=tk.iso(start), clock_out_at=None, end_stated=False, status='open', cancel_reason=None,
                entries=None)
    new = _references(s, inp, base)
    new['billable'] = bool(inp.billable)
    return Plan(output(s, new, cls=ClockWriteOutput), dict(row=new))


def _overlapping(s, employee_id: str, start: datetime) -> dict[str, Any] | None:
    """A closed clock of the person's that ran past ``start``: a new clock would count that time twice."""
    t = c.time_clocks
    found = s.company.conn.execute(sa.select(t).where(
        t.c.employee_id == employee_id, t.c.status == 'closed', t.c.clock_out_at > tk.iso(start))
        .order_by(t.c.clock_out_at.desc()).limit(1)).mappings().first()
    return dict(found) if found else None


def clock_slack():
    return FUTURE_SLACK


def apply_in(plan: Plan, ctx, s) -> Applied:
    new = plan.data['row']
    if _open(s, new['employee_id']):
        raise tk.invalid('employee', 'already clocked in; clock out first')
    touched = tk.write_row(s, c.time_clocks, RECORD, new, None)
    out = output(s, new, cls=ClockWriteOutput)
    return Applied(out, [touched], f"clocked {out.employee_name} in at {out.clock_in_local}")


def _closing(s, ctx, inp, *, command: str):
    person = tk.target_employee(s, ctx, inp.employee)
    tk.require_own_instruction(s, ctx, person, command)
    running = _open(s, person['id'])
    if not running:
        raise BookflowError('E_RECORD_NOT_FOUND', details={
            'record_type': RECORD, 'selector': person['name'], 'field': 'employee', 'suggestions': [],
            'problem': f"{person['name']} is not clocked in"})
    tk.version_check(RECORD, running, getattr(inp, 'expected_version', None))
    return person, running


def _out_plan(s, ctx, inp):
    person, running = _closing(s, ctx, inp, command='clock out')
    now = tk.now(s)
    start = _moment(running['clock_in_at'])
    limit = timedelta(minutes=tk.preferences(s)['max_shift_minutes'])
    if inp.end:
        end, stated = tk.parse_moment(s, inp.end, 'end'), True
        if end > now + clock_slack():
            raise tk.invalid('end', 'a clock cannot stop in the future')
        if end <= start:
            raise tk.invalid('end', f"must be after the clock-in at {tk.local_text(s, start)}")
        if end - start > limit:
            raise tk.invalid('end', f"a shift may run at most {limit.seconds // 3600 + limit.days * 24} hours "
                             '(the maximum shift preference); give the time this person actually stopped')
    else:
        end, stated = now, False
        if end - start > limit:
            raise tk.invalid('end', (
                f"this clock has run since {tk.local_text(s, start)}, past the maximum shift of "
                f"{limit.seconds // 3600 + limit.days * 24} hours; it was probably left on: give the time "
                f"{person['name']} stopped"), clock=running['id'])
        end = max(end, start + timedelta(seconds=1))
    new = _references(s, inp, running)
    if not new['customer_id']:
        raise tk.invalid('customer', 'name the customer:job this time was for')
    if not new['item_id']:
        raise tk.invalid('item', 'name the service item this time is charged as')
    new = tk.bumped(new, s, ctx, status='closed', clock_out_at=tk.iso(end), end_stated=stated)
    segments = _segments(s, start, end)
    writes = [dict(employee=person['id'], customer=new['customer_id'], date=day, duration=tk.hours_text(q),
                   item=new['item_id'], note=new['notes'], billable=new['billable'], class_id=new['class_id'])
              for day, q in segments if q > 0]
    return person, running, new, segments, writes


def _reason(person):
    return f"Clocked out: {person['name']}"


def plan_out(s, ctx, inp) -> Plan:
    person, running, new, segments, writes = _out_plan(s, ctx, inp)
    entry_ctx = tk.entry_ctx(ctx, _reason(person))
    warnings = []
    for values in writes:
        planned = tk.entry_write(s, entry_ctx, 'create', values, apply=False, inactive_employee=person['id'])
        warnings += [w for w in (getattr(planned.preview, 'warnings', None) or []) if w not in warnings]
    if not writes:
        warnings.append('the rounded time is zero, so no time entry is recorded')
    recorded = [dict(date=d, hours=tk.total_text(q)) for d, q in segments if q > 0]
    return Plan(output(s, new, cls=ClockWriteOutput, recorded_hours=recorded, warnings=warnings),
                dict(input=inp))


def apply_out(plan: Plan, ctx, s) -> Applied:
    person, running, new, segments, writes = _out_plan(s, ctx, plan.data['input'])
    entry_ctx = tk.entry_ctx(ctx, _reason(person))
    touched, ids = [], []
    for values in writes:
        written = tk.entry_write(s, entry_ctx, 'create', values, apply=True, inactive_employee=person['id'])
        touched.extend(written.touched)
        ids.append(written.output.id)
    new['entries'] = json.dumps(ids)
    own = tk.write_row(s, c.time_clocks, RECORD, new, running)
    recorded = [dict(date=d, hours=tk.total_text(q)) for d, q in segments if q > 0]
    out = output(s, new, cls=ClockWriteOutput, recorded_hours=recorded, warnings=plan.preview.warnings)
    total = sum(q for _, q in segments)
    summary = f"clocked {out.employee_name} out: {tk.total_text(total)} hours recorded"
    audit.write_event_to(s.company, ctx, 'clock out', summary, [own], actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None))
    return Applied(out, [*touched, own], summary, audited=True)


def plan_cancel(s, ctx, inp) -> Plan:
    person, running = _closing(s, ctx, inp, command='clock cancel')
    reason = (ctx.reason or '').strip()
    if not reason:
        raise BookflowError('E_REASON_REQUIRED')
    if len(reason) > 140:
        raise tk.invalid('reason', 'must be at most 140 characters')
    new = tk.bumped(running, s, ctx, status='cancelled', cancel_reason=reason)
    return Plan(output(s, new, cls=ClockWriteOutput), dict(row=new, before=running))


def apply_cancel(plan: Plan, ctx, s) -> Applied:
    touched = tk.write_row(s, c.time_clocks, RECORD, plan.data['row'], plan.data['before'])
    return Applied(plan.preview, [touched], f"cancelled {plan.preview.employee_name}'s clock; no time recorded")


def open_clocks(s) -> list[ClockOutput]:
    """Every running clock, for the home page."""
    t = c.time_clocks
    rows = s.company.conn.execute(sa.select(t).where(t.c.status == 'open').order_by(t.c.clock_in_at)).mappings()
    return [output(s, dict(r)) for r in rows]
