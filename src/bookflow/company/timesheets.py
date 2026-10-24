"""Weekly timesheets (R106): the anchor's Weekly Timesheet, as a view over a person's time entries.

A sheet is one person's work week. Each row is one customer:job, service item, class, billable flag
and note -- the anchor's row -- with one cell per day; a cell is the time entries of that row on
that day. Showing a sheet reads the entries; saving one writes them: one entry per row per day
with hours, created, corrected or withdrawn through ``time_tracking`` exactly as `time-activity`
writes them, so billing and every report read the result unchanged.

Saving is declarative and idempotent: a cell whose hours did not move writes nothing, so saving the
same sheet twice is a no-op. A cell some entries of which a posted sale carries is locked: its
hours were billed, and changing them is refused, as `time-activity update` refuses it, until that
sale is voided or edited. When a cell is made of several entries (two clock-outs on one day), a
change is absorbed by its newest entries.

The payroll item is a seam: each row reports `payroll_item` as null until payroll exists.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from bookflow.company import schema as c, timekeeping as tk
from bookflow.company.timekeeping_models import (
    TimesheetCell, TimesheetChange, TimesheetOutput, TimesheetRow, TimesheetWriteOutput,
)
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Applied, Plan

KEY = ('customer_id', 'item_id', 'class_id', 'billable', 'notes')


def _key(values: dict[str, Any]) -> tuple:
    return tuple(values[k] for k in KEY)


def _version(found: list[dict[str, Any]]) -> str:
    text = json.dumps([[e['id'], e['version']] for e in sorted(found, key=lambda e: e['id'])])
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _rows_from(found: list[dict[str, Any]], days: list[str], sales: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Group entries into the anchor's rows, in the order each row was first worked."""
    rows: dict[tuple, dict[str, Any]] = {}
    for entry in found:
        r = rows.setdefault(_key(entry), dict({k: entry[k] for k in KEY}, customer_name=entry['customer_name'],
                                               item_name=entry['item_name'], class_name=entry['class_name'],
                                               cells={d: [] for d in days}))
        r['cells'][entry['date']].append(entry)
    return list(rows.values())


def _lock_note(labels: list[str]) -> str:
    return (f"Billed on {', '.join(labels)}: these hours cannot change until that sale is voided or edited.")


def _output(s, person, days, rows, found, sales, *, cls=TimesheetOutput, copied_from=None, **extra):
    out_rows, day_totals = [], [0] * 7
    for r in rows:
        cells, hours, total, row_locked = [], [], 0, False
        for n, day in enumerate(days):
            items = r['cells'].get(day, [])
            quantity = sum(e['quantity'] for e in items) if items else r.get('pending', {}).get(day, 0)
            labels = [label for e in items for label in sales.get(e['id'], [])]
            locked = bool(labels)
            row_locked = row_locked or locked
            cells.append(TimesheetCell(date=day, hours=tk.hours_text(quantity), entries=[e['id'] for e in items],
                                       locked=locked, lock_note=_lock_note(labels) if locked else None))
            hours.append(tk.hours_text(quantity))
            total += quantity
            day_totals[n] += quantity
        out_rows.append(TimesheetRow(customer_id=r['customer_id'], customer_name=r['customer_name'],
            item_id=r['item_id'], item_name=r['item_name'], class_id=r['class_id'], class_name=r['class_name'],
            billable=r['billable'], notes=r['notes'], hours=hours, total=tk.total_text(total), cells=cells,
            locked=row_locked))
    locked_any = any(r.locked for r in out_rows)
    return cls(employee_id=person['id'], employee_name=person['name'], week_start=days[0], week_end=days[-1],
               week_start_day=tk.preferences(s)['week_start'], days=days, rows=out_rows,
               day_totals=[tk.total_text(t) for t in day_totals], week_total=tk.total_text(sum(day_totals)),
               sheet_version=_version(found), copied_from=copied_from,
               lock_note='Some hours this week are billed and locked; they change only once their sale is voided or edited.'
               if locked_any else None, **extra)


def _sheet(s, ctx, inp):
    person = tk.target_employee(s, ctx, inp.employee)
    days = tk.week_of(s, inp.week)
    found = tk.entries(s, person['id'], days[0], days[-1])
    return person, days, found


def show(s, ctx, inp) -> TimesheetOutput:
    person, days, found = _sheet(s, ctx, inp)
    sales = tk.billed(s, [e['id'] for e in found])
    return _output(s, person, days, _rows_from(found, days, sales), found, sales)


def copy_last(s, ctx, inp) -> TimesheetOutput:
    """This week's rows plus the rows of the person's last earlier sheet, the copied ones without hours.

    The anchor's Copy Last Sheet. Nothing is written: the rows are a starting point to fill in and save.
    """
    from datetime import date, timedelta
    person, days, found = _sheet(s, ctx, inp)
    sales = tk.billed(s, [e['id'] for e in found])
    rows = _rows_from(found, days, sales)
    earlier = tk.entries(s, person['id'], (date.fromisoformat(days[0]) - timedelta(days=366)).isoformat(),
                         (date.fromisoformat(days[0]) - timedelta(days=1)).isoformat())
    copied_from = None
    if earlier:
        last_week = tk.week_of(s, earlier[-1]['date'])
        copied_from = last_week[0]
        have = {_key(r) for r in rows}
        for entry in earlier:
            if entry['date'] >= last_week[0] and _key(entry) not in have:
                have.add(_key(entry))
                rows.append(dict({k: entry[k] for k in KEY}, customer_name=entry['customer_name'],
                                 item_name=entry['item_name'], class_name=entry['class_name'],
                                 cells={d: [] for d in days}))
    return _output(s, person, days, rows, found, sales, copied_from=copied_from)


# ---------------------------------------------------------------------------- save

def _resolve_row(s, n: int, given, cache: dict) -> dict[str, Any]:
    from bookflow.company.items import resolve_item
    from bookflow.company.mileage import _class_id, _customer_id
    prefix = f'rows[{n}]'

    def once(kind, selector, fn):
        if (kind, selector) not in cache:
            cache[kind, selector] = fn(selector)
        return cache[kind, selector]
    customer = once('customer', given.customer, lambda v: _customer_id(s, v, prefix + '.customer'))
    item = once('item', given.item, lambda v: resolve_item(s.company, v))
    klass = once('class', given.class_id, lambda v: _class_id(s, v)) if given.class_id else None
    notes = (given.notes or '').strip() or None
    hours = [tk.parse_hours(h, f'{prefix}.hours[{d}]') for d, h in enumerate(given.hours)]
    return dict(customer_id=customer['id'], customer_name=customer['full_name'], item_id=item['id'],
                item_name=item.get('full_name') or item['name'], class_id=klass['id'] if klass else None,
                class_name=(klass.get('full_name') or klass.get('name')) if klass else None,
                billable=bool(given.billable), notes=notes, hours=hours, selectors=given)


def _changes(s, ctx, inp):
    """Every entry write this save makes, worked out against the entries as they stand."""
    person, days, found = _sheet(s, ctx, inp)
    if inp.expected_sheet_version and inp.expected_sheet_version != _version(found):
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': 'timesheet', 'record_id': f"{person['id']}:{days[0]}",
            'expected_version': inp.expected_sheet_version, 'current_version': _version(found),
            'problem': 'this week changed since the sheet was read; show it again'})
    sales = tk.billed(s, [e['id'] for e in found])
    cache: dict = {}
    wanted = [_resolve_row(s, n, given, cache) for n, given in enumerate(inp.rows)]
    seen = {}
    for n, r in enumerate(wanted):
        if _key(r) in seen:
            raise tk.invalid(f'rows[{n}]', f'row {seen[_key(r)] + 1} already has this customer:job, service item, class, '
                             'billable and notes; put the hours in one row')
        seen[_key(r)] = n
    for d, day in enumerate(days):
        if sum(r['hours'][d] for r in wanted) > tk.DAY_MICRO:
            raise BookflowError('E_VALUE_RANGE', details={'field': f'hours[{d}]',
                                                          'problem': f'more than 24 hours on {day}'})
    existing = {_key(r): r for r in _rows_from(found, days, sales)}
    plans = []
    for key in list(dict.fromkeys([*(_key(r) for r in wanted), *existing])):
        target = wanted[seen[key]] if key in seen else None
        current = existing.get(key)
        for d, day in enumerate(days):
            items = current['cells'][day] if current else []
            have = sum(e['quantity'] for e in items)
            want = target['hours'][d] if target else 0
            if have == want:
                continue
            labels = [label for e in items for label in sales.get(e['id'], [])]
            label = (target or current)
            if labels:
                raise BookflowError('E_WORK_DEPENDENCY', details={
                    'problem': f"{label['customer_name']}, {label['item_name']} on {day}: " + _lock_note(labels),
                    'date': day, 'time_activities': [e['id'] for e in items], 'sales': labels})
            plans.extend(_cell_plan(person, day, target or current, items, want - have, target))
    return person, days, found, sales, wanted, plans


def _cell_plan(person, day, row, items, delta, target):
    """The writes that move one cell by ``delta`` microunits; the newest entries absorb a change."""
    meta = dict(date=day, customer_name=row['customer_name'], item_name=row['item_name'])
    if not items:
        # Every field is given, null included, so no customer default (a class, a description)
        # slips in and makes the saved entry read back as a different row.
        values = dict(employee=person['id'], customer=row['customer_id'], date=day, duration=tk.hours_text(delta),
                      item=row['item_id'], note=row['notes'], billable=row['billable'], class_id=row['class_id'])
        return [dict(meta, action='create', values=values, hours=delta, time_activity=None)]
    plans = []
    for entry in reversed(items):
        if delta == 0:
            break
        if delta > 0:
            if entry['quantity'] + delta > tk.DAY_MICRO:
                continue
            new = entry['quantity'] + delta
            delta = 0
        else:
            new = max(0, entry['quantity'] + delta)
            delta += entry['quantity'] - new
        if new == 0:
            plans.append(dict(meta, action='void', hours=0, time_activity=entry['id'],
                              values=dict(time_activity=entry['id'], expected_version=entry['version'])))
        else:
            plans.append(dict(meta, action='update', hours=new, time_activity=entry['id'],
                              values=dict(time_activity=entry['id'], expected_version=entry['version'],
                                          duration=tk.hours_text(new))))
    return plans


def _preview_rows(wanted, days):
    return [dict({k: r[k] for k in KEY}, customer_name=r['customer_name'], item_name=r['item_name'],
                 class_name=r['class_name'], cells={d: [] for d in days},
                 pending={d: r['hours'][n] for n, d in enumerate(days)}) for r in wanted]


def _reason(days):
    return f'Weekly timesheet for the week of {days[0]}'


def plan_save(s, ctx, inp) -> Plan:
    person, days, found, sales, wanted, plans = _changes(s, ctx, inp)
    entry_ctx = tk.entry_ctx(ctx, _reason(days))
    warnings = []
    for p in plans:
        # Planned now, so whatever recording time would refuse -- an inactive item, a closed
        # period -- is refused at preview, with nothing written.
        planned = tk.entry_write(s, entry_ctx, p['action'], p['values'], apply=False)
        warnings += [w for w in (getattr(planned.preview, 'warnings', None) or []) if w not in warnings]
    changes = [TimesheetChange(date=p['date'], customer_name=p['customer_name'], item_name=p['item_name'],
                               action=p['action'], hours=tk.total_text(p['hours']), time_activity=p['time_activity'])
               for p in plans]
    if not plans:
        out = _output(s, person, days, _rows_from(found, days, sales), found, sales, cls=TimesheetWriteOutput,
                      changed=False)
        return Plan(out, dict(changed=False))
    out = _output(s, person, days, _preview_rows(wanted, days), found, {}, cls=TimesheetWriteOutput, changed=True,
                  changes=changes, warnings=warnings)
    return Plan(out, dict(changed=True, input=inp))


def apply_save(plan: Plan, ctx, s) -> Applied:
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    inp = plan.data['input']
    # Worked out again inside the writer transaction, and each entry re-planned as it is written.
    person, days, _, _, _, plans = _changes(s, ctx, inp)
    entry_ctx = tk.entry_ctx(ctx, _reason(days))
    touched = []
    for p in plans:
        written = tk.entry_write(s, entry_ctx, p['action'], p['values'], apply=True)
        touched.extend(written.touched)
    out = show(s, ctx, inp.model_copy(update={'employee': person['id']}))
    out = TimesheetWriteOutput(**out.model_dump(), changed=bool(plans), changes=plan.preview.changes,
                               warnings=plan.preview.warnings)
    summary = f"saved {person['name']}'s timesheet for the week of {days[0]}: {len(plans)} time entr{'y' if len(plans) == 1 else 'ies'}"
    return Applied(out, touched, summary, audited=True)
