"""Memorized reports: a report and its options kept under a name (R109).

The anchor's Memorize button keeps a customized report's settings -- not its figures -- under a
name, optionally in a group, and its Memorized Report List runs, renames and deletes them. Here a
memorized report is the report's verb and its own input, shared by everyone in the company.

**Dates.** When the person chose a named range ("This Month", "Last Fiscal Year") the name is
kept, not the dates, and every run resolves it again against today in the company's timezone and
fiscal year, as the anchor does. Without one, the dates in the options are kept exactly.

**Running one** runs the report command itself, as the caller, through the same validation and
authorization as typing it: a memorized report never lets anyone read a report they could not.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.budget_models import (
    MemorizedReportOutput, MemorizedReportPageOutput, MemorizedReportRunOutput, MemorizedReportWriteOutput,
)
from bookflow.company.lists import normalize_display_name
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

PAGING = ('cursor', 'limit')
DATE_FIELDS = ('date_from', 'date_to', 'as_of')


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


# ---------------------------------------------------------------------------- date ranges

def _month_end(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + delta
    return index // 12, index % 12 + 1


def _same_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, _month_end(year, month).day))


def resolve_range(name: str, today: date, fiscal_month: int) -> tuple[date, date]:
    """A named range, the anchor's report date list, for one day and one fiscal year start."""
    t = today
    week_start = t - timedelta(days=(t.weekday() + 1) % 7)  # weeks run Sunday to Saturday
    fy_year = t.year if t.month >= fiscal_month else t.year - 1
    fy_start = date(fy_year, fiscal_month, 1)
    fq_offset = ((t.year * 12 + t.month - 1) - (fy_year * 12 + fiscal_month - 1)) // 3 * 3
    q_year, q_month = _months(fy_year, fiscal_month, fq_offset)
    q_start = date(q_year, q_month, 1)

    def quarter(delta):
        y, m = _months(q_year, q_month, 3 * delta)
        ey, em = _months(y, m, 2)
        return date(y, m, 1), _month_end(ey, em)

    def fiscal(delta):
        start = date(fy_year + delta, fiscal_month, 1)
        ey, em = _months(start.year, start.month, 11)
        return start, _month_end(ey, em)

    ly, lm = _months(t.year, t.month, -1)
    ny, nm = _months(t.year, t.month, 1)
    ranges = {
        'today': (t, t),
        'yesterday': (t - timedelta(days=1), t - timedelta(days=1)),
        'this_week': (week_start, week_start + timedelta(days=6)),
        'this_week_to_date': (week_start, t),
        'last_week': (week_start - timedelta(days=7), week_start - timedelta(days=1)),
        'this_month': (date(t.year, t.month, 1), _month_end(t.year, t.month)),
        'this_month_to_date': (date(t.year, t.month, 1), t),
        'last_month': (date(ly, lm, 1), _month_end(ly, lm)),
        'last_month_to_date': (date(ly, lm, 1), _same_day(ly, lm, t.day)),
        'next_month': (date(ny, nm, 1), _month_end(ny, nm)),
        'this_fiscal_quarter': quarter(0),
        'this_fiscal_quarter_to_date': (q_start, t),
        'last_fiscal_quarter': quarter(-1),
        'next_fiscal_quarter': quarter(1),
        'this_fiscal_year': fiscal(0),
        'this_fiscal_year_to_date': (fy_start, t),
        'last_fiscal_year': fiscal(-1),
        'last_fiscal_year_to_date': (fiscal(-1)[0], _same_day(t.year - 1, t.month, t.day)),
        'next_fiscal_year': fiscal(1),
        'this_calendar_year': (date(t.year, 1, 1), date(t.year, 12, 31)),
        'last_calendar_year': (date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)),
    }
    return ranges[name]


def _date_inputs(cmd, first: date, last: date) -> dict[str, str]:
    """The resolved range in the fields this report takes: a period, an end date, or an as-of date."""
    fields = cmd.input_model.model_fields
    out = {}
    if 'date_from' in fields:
        out['date_from'] = first.isoformat()
    if 'date_to' in fields:
        out['date_to'] = last.isoformat()
    elif 'as_of' in fields:
        out['as_of'] = last.isoformat()
    return out


def _today(s) -> date:
    from bookflow.company.memorized_schedule import today
    return date.fromisoformat(today(s.company_tz))


def _fiscal_month(s) -> int:
    return int(s.company.raw.execute('SELECT fiscal_year_start_month FROM company_info').fetchone()[0])


# ---------------------------------------------------------------------------- records

def _report_command(verb: str):
    from bookflow.core import registry
    from bookflow.documents import report_csv
    registry.load_all('report')
    cmd = registry.get(f'report {verb}')
    if not report_csv.is_report(cmd):
        raise _invalid('report', f"not a report that can be memorized; choose one of: {', '.join(report_csv.reports())}")
    return cmd


def _checked(s, verb: str, filters: dict[str, Any], date_range: str | None) -> dict[str, Any]:
    """The options kept: paging dropped, the dates a named range supplies dropped, the rest validated."""
    from bookflow.core.dispatch import validate_input
    cmd = _report_command(verb)
    kept = {key: value for key, value in (filters or {}).items() if key not in PAGING}
    if date_range is not None:
        if not set(DATE_FIELDS) & set(cmd.input_model.model_fields):
            raise _invalid('date_range', f'report {verb} takes no dates')
        kept = {key: value for key, value in kept.items() if key not in DATE_FIELDS}
        first, last = resolve_range(date_range, _today(s), _fiscal_month(s))
        probe = {**kept, **_date_inputs(cmd, first, last)}
    else:
        probe = kept
    try:
        validate_input(cmd, probe)
    except BookflowError as err:
        if err.code == 'E_VALIDATION':
            fields = [{**f, 'field': 'filters.' + str(f.get('field'))} for f in (err.details or {}).get('fields', [])]
            raise BookflowError('E_VALIDATION', message=err.message, details={**(err.details or {}), 'fields': fields}) from None
        raise
    return kept


def resolve(s, selector: str) -> dict[str, Any]:
    from bookflow.core.ids import is_ulid, normalize_ulid
    table = c.memorized_reports
    trimmed = selector.strip() if isinstance(selector, str) else selector
    row = None
    if isinstance(trimmed, str) and is_ulid(trimmed):
        row = s.company.conn.execute(sa.select(table).where(table.c.id == normalize_ulid(trimmed))).mappings().first()
    if row is None and isinstance(trimmed, str) and trimmed:
        _, key = normalize_display_name(trimmed, field='memorized_report')
        row = s.company.conn.execute(sa.select(table).where(table.c.name_key == key)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'memorized_report', 'selector': selector,
                                                           'field': 'memorized_report'})
    return dict(row)


def _unique_name(s, name: str, *, own: str | None = None) -> tuple[str, str]:
    display, key = normalize_display_name(name, field='name')
    query = sa.select(c.memorized_reports.c.id).where(c.memorized_reports.c.name_key == key)
    if own is not None:
        query = query.where(c.memorized_reports.c.id != own)
    clash = s.company.conn.execute(query).first()
    if clash is not None:
        raise BookflowError('E_NAME_TAKEN', details={'record_type': 'memorized_report', 'name': display, 'record_id': clash[0]})
    return display, key


def _group(value: str | None) -> str | None:
    if value is None:
        return None
    display, _ = normalize_display_name(value, field='group')
    return display


def output(row: dict[str, Any]) -> MemorizedReportOutput:
    from bookflow.adapters.workbench import naming
    return MemorizedReportOutput(
        **{key: row[key] for key in ('id', 'version', 'name', 'report', 'date_range', 'created_at', 'created_by',
                                     'created_via', 'updated_at', 'updated_by', 'updated_via')},
        group=row['group_name'], title=naming.heading('report', row['report']), filters=json.loads(row['filters']))


def _canonical(filters: dict[str, Any]) -> str:
    return json.dumps(filters, sort_keys=True, separators=(',', ':'))


def plan_create(s, ctx, inp) -> Plan:
    display, key = _unique_name(s, inp.name)
    kept = _checked(s, inp.report, inp.filters, inp.date_range)
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), name=display, name_key=key,
               group_name=_group(inp.group), report=inp.report, filters=_canonical(kept), date_range=inp.date_range)
    return Plan(MemorizedReportWriteOutput(memorized_report=output(row)), dict(row=row))


def apply_create(plan: Plan, ctx, s) -> Applied:
    row = plan.data['row']
    s.company.conn.execute(c.memorized_reports.insert().values(**row))
    return Applied(plan.preview, [Touched('memorized_report', row['id'], 'create', None, 1, row, db='company')],
                   f"memorized report {row['name']}")


def plan_update(s, ctx, inp) -> Plan:
    old = resolve(s, inp.memorized_report)
    if inp.expected_version is not None and inp.expected_version != old['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': 'memorized_report', 'record_id': old['id'], 'current_version': old['version'],
            'expected_version': inp.expected_version})
    given = inp.model_fields_set
    new = dict(old)
    if 'name' in given and inp.name is not None:
        new['name'], new['name_key'] = _unique_name(s, inp.name, own=old['id'])
    if 'group' in given:
        new['group_name'] = _group(inp.group)
    if 'date_range' in given:
        new['date_range'] = inp.date_range
    if 'report' in given and inp.report is not None:
        new['report'] = inp.report
    filters = inp.filters if 'filters' in given and inp.filters is not None else json.loads(old['filters'])
    if {'report', 'filters', 'date_range'} & given:
        new['filters'] = _canonical(_checked(s, new['report'], filters, new['date_range']))
    changed = any(new[key] != old[key] for key in ('name', 'group_name', 'report', 'filters', 'date_range'))
    if changed:
        new.update(version=old['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                   updated_via=ctx.interface.value)
    return Plan(MemorizedReportWriteOutput(memorized_report=output(new), changed=changed),
                dict(before=old, row=new, changed=changed))


def apply_update(plan: Plan, ctx, s) -> Applied:
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    row, before = plan.data['row'], plan.data['before']
    s.company.conn.execute(c.memorized_reports.update().where(c.memorized_reports.c.id == row['id']).values(**row))
    return Applied(plan.preview, [Touched('memorized_report', row['id'], 'update', before['version'], row['version'],
                                          row, before, db='company')], f"updated memorized report {row['name']}")


def plan_delete(s, ctx, inp) -> Plan:
    row = resolve(s, inp.memorized_report)
    if inp.expected_version is not None and inp.expected_version != row['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': 'memorized_report', 'record_id': row['id'], 'current_version': row['version'],
            'expected_version': inp.expected_version})
    return Plan(MemorizedReportWriteOutput(memorized_report=output(row), deleted=True), dict(row=row))


def apply_delete(plan: Plan, ctx, s) -> Applied:
    row = plan.data['row']
    s.company.conn.execute(c.memorized_reports.delete().where(c.memorized_reports.c.id == row['id']))
    return Applied(plan.preview, [Touched('memorized_report', row['id'], 'delete', row['version'], None, None, row,
                                          db='company')], f"deleted memorized report {row['name']}")


def show(s, inp) -> MemorizedReportOutput:
    return output(resolve(s, inp.memorized_report))


def page(s, inp) -> MemorizedReportPageOutput:
    rows = [dict(row) for row in s.company.conn.execute(sa.select(c.memorized_reports)).mappings().all()]
    if inp.group is not None:
        wanted = inp.group.strip().casefold()
        rows = [row for row in rows if (row['group_name'] or '').casefold() == wanted]
    rows.sort(key=lambda row: (row['group_name'] is not None, (row['group_name'] or '').casefold(), row['name_key'], row['id']))
    if inp.cursor is not None:
        ids = [row['id'] for row in rows]
        rows = rows[ids.index(inp.cursor) + 1:] if inp.cursor in ids else []
    shown = rows[:inp.limit]
    items = [output(row) for row in shown]
    return MemorizedReportPageOutput(items=items, count=len(items),
                                     next_cursor=shown[-1]['id'] if len(rows) > inp.limit else None)


def inputs_for(s, row: dict[str, Any]) -> dict[str, Any]:
    """The report's input for a run today: kept options plus the named range resolved to dates."""
    cmd = _report_command(row['report'])
    inputs = json.loads(row['filters'])
    if row['date_range']:
        first, last = resolve_range(row['date_range'], _today(s), _fiscal_month(s))
        inputs.update(_date_inputs(cmd, first, last))
    return inputs


def run(s, ctx, inp) -> MemorizedReportRunOutput:
    from bookflow.core.dispatch import run_in_session, validate_input
    row = resolve(s, inp.memorized_report)
    cmd = _report_command(row['report'])
    inputs = inputs_for(s, row)
    raw = {**inputs, 'limit': inp.limit, **({'cursor': inp.cursor} if inp.cursor else {})}
    result = run_in_session(cmd, validate_input(cmd, raw), ctx, s)
    return MemorizedReportRunOutput(memorized_report=output(row), command=cmd.name, inputs=inputs, result=result)
