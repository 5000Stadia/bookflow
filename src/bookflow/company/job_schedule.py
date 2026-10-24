"""The job schedule (R106): who works where, when -- by employee and by job, by day, week or month.

A schedule entry puts one employee on a customer:job, or on a work order (whose customer:job it then
is), from a company-local start to an end, with notes. Work orders' own scheduled start and end,
with their assignees, show on the same calendar, read from the work order and never copied.

Double-booking is warned about, not refused: an entry that overlaps another entry or a scheduled
work order of the same employee saves, and says so. Nothing here posts or records time.
"""
from __future__ import annotations

import calendar as cal
from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c, timekeeping as tk
from bookflow.company.timekeeping_models import (
    CalendarDay, ScheduleCalendarOutput, ScheduleConflict, ScheduleEntryOutput, ScheduleWriteOutput,
)
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan
from bookflow.hub.users import common

RECORD = 'schedule_entry'
WORK_ORDER_OPEN = ('draft', 'scheduled', 'in_progress', 'on_hold', 'complete')


def resolve(s, selector: str) -> dict[str, Any]:
    t = c.schedule_entries
    found = tk.row(s, t, t.c.id == selector.strip().upper()) or tk.row(s, t, t.c.id == selector.strip())
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': selector,
                                                           'field': 'entry', 'suggestions': []})
    return found


def _work_order(s, selector: str) -> dict[str, Any]:
    from bookflow.company import work
    try:
        return work.resolve(s, selector, 'work_order')
    except BookflowError as exc:
        if exc.code == 'E_RECORD_NOT_FOUND':
            exc.details.setdefault('field', 'work_order')
        raise


def work_orders(s, date_from: str, date_to: str) -> list[dict[str, Any]]:
    """Work orders with a scheduled start whose local span touches the range."""
    from bookflow.company import work
    d = c.work_documents
    found = []
    for header in s.company.conn.execute(sa.select(d).where(d.c.kind == 'work_order',
                                                             d.c.status.in_(WORK_ORDER_OPEN))).mappings():
        header = dict(header)
        rev = work.revision(s, header)
        f = work.facts(rev)
        if not f.scheduled_start:
            continue
        starts = tk.local_text(s, f.scheduled_start)
        ends = tk.local_text(s, f.scheduled_end) if f.scheduled_end else starts
        if starts[:10] > date_to or ends[:10] < date_from:
            continue
        found.append(dict(id=header['id'], number=header['number'], title=rev['title'], status=header['status'],
                          customer_id=f.profile.customer.id, customer_name=f.profile.customer.label,
                          employees=[(a.id, a.label) for a in f.assignees], starts=starts, ends=ends))
    return found


def _overlap(a_start, a_end, b_start, b_end) -> bool:
    if a_start == a_end or b_start == b_end:
        return a_start <= b_end and b_start <= a_end
    return a_start < b_end and b_start < a_end


def conflicts(s, employee_id: str, starts: str, ends: str, *, own_id: str | None = None,
              own_work_order: str | None = None) -> list[ScheduleConflict]:
    t = c.schedule_entries
    found = []
    for r in s.company.conn.execute(sa.select(t).where(
            t.c.employee_id == employee_id, t.c.status == 'scheduled', t.c.starts_at < ends,
            t.c.ends_at > starts)).mappings():
        if r['id'] != own_id:
            names = tk.names(s, c.customers, [r['customer_id']])
            found.append(ScheduleConflict(kind='schedule', id=r['id'], label=names.get(r['customer_id'], ''),
                                          starts=r['starts_at'], ends=r['ends_at']))
    for wo in work_orders(s, starts[:10], ends[:10]):
        if wo['id'] == own_work_order or employee_id not in [e for e, _ in wo['employees']]:
            continue
        if _overlap(starts, ends, wo['starts'], wo['ends']):
            found.append(ScheduleConflict(kind='work_order', id=wo['id'], label=f"Work order {wo['number']}",
                                          starts=wo['starts'], ends=wo['ends']))
    return found


def output(s, r: dict[str, Any], *, cls=ScheduleEntryOutput, with_conflicts=True, **extra) -> ScheduleEntryOutput:
    customer = tk.names(s, c.customers, [r['customer_id']])
    person = tk.names(s, c.employees, [r['employee_id']], 'name')
    number = tk.names(s, c.work_documents, [r['work_document_id']], 'number')
    found = conflicts(s, r['employee_id'], r['starts_at'], r['ends_at'], own_id=r['id'],
                      own_work_order=r['work_document_id']) if with_conflicts and r['status'] == 'scheduled' else []
    return cls(id=r['id'], version=r['version'], employee_id=r['employee_id'], employee_name=person.get(r['employee_id']),
               customer_id=r['customer_id'], customer_name=customer.get(r['customer_id'], ''),
               work_order_id=r['work_document_id'], work_order_number=number.get(r['work_document_id']),
               starts=r['starts_at'], ends=r['ends_at'], notes=r['notes'], status=r['status'],
               cancel_reason=r['cancel_reason'], conflicts=found, **extra)


def _warnings(out) -> list[str]:
    return [f"{out.employee_name} is already booked {x.starts.replace('T', ' ')} to {x.ends.replace('T', ' ')}: "
            f"{x.label}" for x in out.conflicts]


def _resolved(s, inp, old: dict[str, Any] | None) -> dict[str, Any]:
    from bookflow.company.mileage import _customer_id
    given = inp.model_fields_set
    new = dict(old) if old else dict(work_document_id=None, notes=None)
    if old is None or ('employee' in given and inp.employee):
        new['employee_id'] = tk.employee(s, inp.employee, active=True)['id']
    if 'work_order' in given:
        if inp.work_order:
            wo = _work_order(s, inp.work_order)
            from bookflow.company import work
            new['work_document_id'] = wo['id']
            new['customer_id'] = work.facts(work.revision(s, wo)).profile.customer.id
        else:
            new['work_document_id'] = None
    if inp.customer and (old is None or 'customer' in given):
        customer = _customer_id(s, inp.customer)
        if new.get('work_document_id') and new.get('customer_id') != customer['id']:
            raise tk.invalid('customer', 'the entry is on a work order; its customer:job is the work order\'s')
        new['customer_id'] = customer['id']
    if not new.get('customer_id'):
        raise tk.invalid('customer', 'name a customer:job or a work order')
    if old is None or 'starts' in given:
        new['starts_at'] = tk.parse_local(inp.starts, 'starts')
    if old is None or 'ends' in given:
        new['ends_at'] = tk.parse_local(inp.ends, 'ends')
    if new['ends_at'] <= new['starts_at']:
        raise tk.invalid('ends', 'must be after the start')
    if 'notes' in given:
        new['notes'] = (inp.notes or '').strip() or None
    return new


def plan_write(s, ctx, inp, operation: str) -> Plan:
    if operation == 'create':
        old = None
        new = _resolved(s, inp, None)
        new.update(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), status='scheduled',
                   cancel_reason=None)
    else:
        old = resolve(s, inp.entry)
        tk.version_check(RECORD, old, inp.expected_version)
        if old['status'] == 'cancelled':
            if operation == 'cancel':
                return Plan(output(s, old, cls=ScheduleWriteOutput, changed=False), dict(changed=False))
            raise tk.invalid('entry', 'a cancelled schedule entry cannot be changed; schedule a new one')
        if operation == 'cancel':
            reason = (ctx.reason or '').strip()
            if not reason:
                raise BookflowError('E_REASON_REQUIRED')
            new = dict(old, status='cancelled', cancel_reason=reason[:140])
        else:
            new = _resolved(s, inp, old)
        keys = [k for k in new if k not in ('version', 'updated_at', 'updated_by', 'updated_via')]
        if all(new[k] == old[k] for k in keys):
            return Plan(output(s, old, cls=ScheduleWriteOutput, changed=False), dict(changed=False))
        new = tk.bumped(new, s, ctx)
    out = output(s, new, cls=ScheduleWriteOutput)
    out.warnings = _warnings(out)
    return Plan(out, dict(changed=True, row=new, before=old, operation=operation))


def apply_write(plan: Plan, ctx, s) -> Applied:
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    new, old = plan.data['row'], plan.data['before']
    touched = tk.write_row(s, c.schedule_entries, RECORD, new, old)
    out = plan.preview
    verb = {'create': 'scheduled', 'update': 'rescheduled', 'cancel': 'cancelled'}[plan.data['operation']]
    return Applied(out, [touched], f"{verb} {out.employee_name} on {out.customer_name} {out.starts.replace('T', ' ')}")


def show(s, inp) -> ScheduleEntryOutput:
    return output(s, resolve(s, inp.entry))


def _range(view: str, day: date) -> tuple[date, date]:
    if view == 'day':
        return day, day
    if view == 'month':
        return day.replace(day=1), day.replace(day=cal.monthrange(day.year, day.month)[1])
    return day, day  # weeks are worked out by the caller, from the company's week start


def calendar(s, ctx, inp) -> ScheduleCalendarOutput:
    from bookflow.company.mileage import _customer_id
    day = date.fromisoformat(inp.date) if inp.date else tk.today(s)
    if inp.view == 'week':
        week = tk.week_of(s, day.isoformat())
        first, last = date.fromisoformat(week[0]), date.fromisoformat(week[-1])
    else:
        first, last = _range(inp.view, day)
    person = tk.employee(s, inp.employee) if inp.employee else None
    customer = _customer_id(s, inp.customer) if inp.customer else None
    t = c.schedule_entries
    query = sa.select(t).where(t.c.starts_at < (last + timedelta(days=1)).isoformat(),
                               t.c.ends_at >= first.isoformat()).order_by(t.c.starts_at, t.c.id)
    if not inp.include_cancelled:
        query = query.where(t.c.status == 'scheduled')
    if person:
        query = query.where(t.c.employee_id == person['id'])
    if customer:
        query = query.where(t.c.customer_id == customer['id'])
    items = [output(s, dict(r)) for r in s.company.conn.execute(query).mappings()]
    for wo in work_orders(s, first.isoformat(), last.isoformat()):
        if customer and wo['customer_id'] != customer['id']:
            continue
        people = wo['employees'] or [(None, None)]
        if person:
            people = [p for p in people if p[0] == person['id']]
        for employee_id, employee_name in people:
            found = conflicts(s, employee_id, wo['starts'], wo['ends'], own_work_order=wo['id']) if employee_id else []
            items.append(ScheduleEntryOutput(id=wo['id'], kind='work_order', version=0, employee_id=employee_id,
                employee_name=employee_name, customer_id=wo['customer_id'], customer_name=wo['customer_name'],
                work_order_id=wo['id'], work_order_number=wo['number'], starts=wo['starts'], ends=wo['ends'],
                notes=wo['title'], status=wo['status'], conflicts=found))
    days = []
    cursor = first
    while cursor <= last:
        text = cursor.isoformat()
        todays = sorted((i for i in items if i.starts[:10] <= text <= (i.ends[:10] if i.ends[11:] != '00:00' or
                         i.ends[:10] == i.starts[:10] else (date.fromisoformat(i.ends[:10]) - timedelta(days=1)).isoformat())),
                        key=lambda i: (i.starts, i.employee_name or '', i.id))
        days.append(CalendarDay(date=text, items=todays))
        cursor += timedelta(days=1)
    return ScheduleCalendarOutput(view=inp.view, date_from=first.isoformat(), date_to=last.isoformat(),
                                  employee_id=person['id'] if person else None,
                                  employee_name=person['name'] if person else None,
                                  customer_id=customer['id'] if customer else None,
                                  customer_name=customer['full_name'] if customer else None,
                                  days=days, conflicts=sum(1 for i in items if i.conflicts))
