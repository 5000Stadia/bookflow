"""Timekeeping (R106) shared core: preferences, who a login is, local time, and the time entries a person has.

A weekly timesheet and a clock-out both end in the same place: time entries, the one-line
customer-work documents of kind ``time_activity`` that `time-activity create` writes. They are written
through ``time_tracking`` and ``work`` exactly as that command writes them, so billing them once
through ``work_billing_allocations``, `report unbilled-costs` and job profitability read them unchanged.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.timekeeping_models import TimekeepingPreferencesOutput
from bookflow.company.timekeeping_schema import WEEK_DAYS
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

KIND = 'time_activity'
MICRO = 1_000_000
DAY_MICRO = 24 * MICRO
DEFAULTS = dict(week_start='monday', clock_rounding_minutes=0, max_shift_minutes=16 * 60)
PREFERENCE_RECORD = 'timekeeping_preferences'
LINK_RECORD = 'timekeeping_person'


def invalid(field: str, problem: str, **details) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}], **details})


def row(s, table, *where) -> dict[str, Any] | None:
    found = s.company.conn.execute(sa.select(table).where(*where)).mappings().first()
    return dict(found) if found else None


def bumped(old: dict[str, Any], s, ctx, **changes) -> dict[str, Any]:
    return dict(old, **changes, version=old['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                updated_via=ctx.interface.value)


def version_check(record_type: str, current: dict[str, Any], expected: int | None) -> None:
    if expected is not None and expected != current['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': record_type, 'record_id': current['id'], 'current_version': current['version'],
            'expected_version': expected, 'updated_at': current['updated_at'], 'updated_by': current['updated_by']})


def write_row(s, table, record_type: str, new: dict[str, Any], old: dict[str, Any] | None) -> Touched:
    """Insert or version-guarded update, returning what the audit records."""
    if old is None:
        s.company.conn.execute(table.insert().values(**new))
        return Touched(record_type, new['id'], 'create', None, new['version'], new, db='company')
    done = s.company.conn.execute(table.update().where(table.c.id == old['id'], table.c.version == old['version'])
                                  .values(**new)).rowcount
    if done != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': record_type, 'record_id': old['id']})
    return Touched(record_type, new['id'], 'update', old['version'], new['version'], new, old, db='company')


# ---------------------------------------------------------------------------- hours

def parse_hours(text, field: str) -> int:
    """Decimal hours as microunits; empty is zero. A day holds at most 24 hours."""
    if text is None or (isinstance(text, str) and not text.strip()):
        return 0
    units = parse_quantity_micro_units(str(text).strip(), field=field)
    if units < 0:
        raise invalid(field, 'hours cannot be negative')
    if units > DAY_MICRO:
        raise BookflowError('E_VALUE_RANGE', details={'field': field, 'problem': 'a day holds at most 24 hours'})
    return units


def hours_text(units: int | None) -> str | None:
    return None if not units else format_quantity_micro_units(units)


def total_text(units: int) -> str:
    return format_quantity_micro_units(units) if units else '0'


# ---------------------------------------------------------------------------- preferences

def _preference_row(s) -> dict[str, Any] | None:
    return row(s, c.timekeeping_preferences, c.timekeeping_preferences.c.singleton == 1)


def preferences(s) -> dict[str, Any]:
    found = _preference_row(s)
    values = dict(DEFAULTS, version=0)
    if found:
        values.update({k: found[k] for k in DEFAULTS}, version=found['version'])
    return values


def _preference_output(values, changed=None) -> TimekeepingPreferencesOutput:
    rounding = values['clock_rounding_minutes']
    return TimekeepingPreferencesOutput(week_start=values['week_start'],
        clock_rounding='none' if rounding == 0 else str(rounding), max_shift_hours=values['max_shift_minutes'] // 60,
        version=values['version'], changed=changed)


def preferences_show(s, inp) -> TimekeepingPreferencesOutput:
    return _preference_output(preferences(s))


def plan_preferences(s, ctx, inp) -> Plan:
    old = _preference_row(s)
    wanted = {}
    if inp.week_start is not None:
        wanted['week_start'] = inp.week_start
    if inp.clock_rounding is not None:
        wanted['clock_rounding_minutes'] = 0 if inp.clock_rounding == 'none' else int(inp.clock_rounding)
    if inp.max_shift_hours is not None:
        wanted['max_shift_minutes'] = inp.max_shift_hours * 60
    if old is None:
        new = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), singleton=1,
                   **dict(DEFAULTS, **wanted))
        changed = bool(wanted)
    else:
        changed = any(old[k] != v for k, v in wanted.items())
        new = bumped(old, s, ctx, **wanted) if changed else old
    out = _preference_output(new, changed)
    return Plan(out, dict(row=new, before=old, changed=changed))


def apply_preferences(plan: Plan, ctx, s) -> Applied:
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    new, old = plan.data['row'], plan.data['before']
    touched = write_row(s, c.timekeeping_preferences, PREFERENCE_RECORD, new, old)
    return Applied(plan.preview, [touched], 'timekeeping preferences changed')


# ---------------------------------------------------------------------------- local time

def zone(s) -> ZoneInfo | timezone:
    name = getattr(s, 'company_tz', None)
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone.utc


def now(s) -> datetime:
    return clock.now().astimezone(timezone.utc)


def today(s) -> date:
    from bookflow.company.memorized_schedule import today as company_today
    return date.fromisoformat(company_today(getattr(s, 'company_tz', None)))


_LOCAL = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}')


def parse_moment(s, text: str, field: str) -> datetime:
    """A UTC instant from an ISO timestamp with a zone, or a company-local YYYY-MM-DDTHH:MM."""
    text = (text or '').strip()
    try:
        if _LOCAL.fullmatch(text):
            return datetime.fromisoformat(text).replace(tzinfo=zone(s)).astimezone(timezone.utc)
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise invalid(field, 'use YYYY-MM-DDTHH:MM (company time) or an ISO 8601 timestamp with a zone') from None
    if moment.tzinfo is None:
        raise invalid(field, 'a full timestamp needs its zone, or give company-local YYYY-MM-DDTHH:MM')
    return moment.astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def local_text(s, moment: datetime | str | None) -> str | None:
    if moment is None:
        return None
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace('Z', '+00:00'))
    return moment.astimezone(zone(s)).strftime('%Y-%m-%dT%H:%M')


def parse_local(text: str, field: str) -> str:
    """A company-local wall-clock time, kept as YYYY-MM-DDTHH:MM."""
    text = (text or '').strip()
    if not _LOCAL.fullmatch(text):
        raise invalid(field, 'use company-local YYYY-MM-DDTHH:MM')
    try:
        datetime.fromisoformat(text)
    except ValueError:
        raise invalid(field, 'not a real date and time') from None
    return text


def week_of(s, day: str | None) -> list[str]:
    """The seven dates of the work week holding ``day`` (today by default)."""
    start = WEEK_DAYS.index(preferences(s)['week_start'])
    anchor = date.fromisoformat(day) if day else today(s)
    try:
        first = anchor - timedelta(days=(anchor.weekday() - start) % 7)
        return [(first + timedelta(days=n)).isoformat() for n in range(7)]
    except OverflowError:
        raise invalid('week', 'that week runs outside the years the calendar supports') from None


def round_segment(seconds: int, rounding_minutes: int) -> int:
    """Hours in microunits for a stretch of seconds, rounded per the company preference.

    With no rounding the stretch is converted to six-place hours, half to even. With 6 or 15 minutes
    it is rounded to the nearest increment (half up, as a punch clock does), which is an exact
    0.1 or 0.25 of an hour.
    """
    if rounding_minutes:
        increments = int(Fraction(seconds, 60 * rounding_minutes) + Fraction(1, 2))
        return increments * rounding_minutes * MICRO // 60
    exact = Fraction(seconds * MICRO, 3600)
    whole = exact.numerator // exact.denominator
    rest = exact - whole
    return whole + (1 if rest > Fraction(1, 2) or (rest == Fraction(1, 2) and whole % 2) else 0)


def day_segments(s, start: datetime, end: datetime) -> list[tuple[str, int]]:
    """(local date, seconds) for each company-local day a stretch touches, split at local midnight.

    Worked in UTC instants: two aware times in the same zone compare by wall clock in Python, which
    gets the repeated hour of a fall-back night wrong (01:30 EDT is after 01:15 EST).
    """
    tz = zone(s)
    cursor, finish = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    segments = []
    while cursor < finish:
        day = cursor.astimezone(tz).date()
        midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        stop = min(midnight, finish)
        segments.append((day.isoformat(), int((stop - cursor).total_seconds())))
        cursor = stop
    return [(d, seconds) for d, seconds in segments if seconds > 0]


# ---------------------------------------------------------------------------- people

def employee(s, selector: str, *, active: bool = False) -> dict[str, Any]:
    from bookflow.company.parties import resolve_party
    try:
        found = resolve_party(s.company, 'employee', selector)
    except BookflowError as exc:
        if exc.code == 'E_RECORD_NOT_FOUND':
            exc.details.setdefault('field', 'employee')
        raise
    if active and not found['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'employee', 'record_id': found['id']})
    return found


def caller_user(s, ctx) -> str | None:
    """The person a request speaks for: the person themselves, or an agent's principal."""
    if s.actor is None:
        return None
    return s.actor.id if s.actor.kind == 'human' else ctx.on_behalf_of


def linked_user(s, employee_id: str) -> str | None:
    t = c.timekeeping_people
    return s.company.conn.execute(sa.select(t.c.user_id).where(t.c.employee_id == employee_id)).scalar()


def target_employee(s, ctx, selector: str | None, *, active: bool = False) -> dict[str, Any]:
    """The named employee, or the caller's own when none is named."""
    if selector:
        return employee(s, selector, active=active)
    user = caller_user(s, ctx)
    t = c.timekeeping_people
    found = s.company.conn.execute(sa.select(t.c.employee_id).where(t.c.user_id == user)).scalar() if user else None
    if not found:
        raise invalid('employee', 'name the employee: your login is not linked to one (a person links it with `clock link`)')
    return employee(s, found, active=active)


def require_own_instruction(s, ctx, employee_row: dict[str, Any], command: str) -> None:
    """An agent clocks a person in or out only on that person's own instruction.

    Live clocking records attendance as it happens, so its principal -- the person it acts for --
    must be the employee's linked login. Recording time after the fact (`timesheet save`,
    `time-activity create`) and scheduling are open to an agent for any employee, as they are to its
    principal at the standard role. A person may clock anyone, as the anchor lets anyone enter anyone's time.
    """
    if s.actor is None or s.actor.kind == 'human':
        return
    if ctx.on_behalf_of and linked_user(s, employee_row['id']) == ctx.on_behalf_of:
        return
    raise BookflowError('E_PERMISSION', message=(
        f"An agent clocks a person in or out only on that person's own instruction, and {employee_row['name']} "
        "is not the person you act for. Recording their time after the fact (`timesheet save`) is allowed."), details={
        'capability': 'customer-work', 'reason': 'own_instruction_only', 'command': command,
        'rule': "an agent clocks only its principal's own employee record",
        'next_step': ('Ask the person to clock in themselves, record the hours with `timesheet save`, or ask a '
                      'person to link your principal\'s login to their employee record with `clock link`.')})


def _hub_user(s, text: str) -> dict[str, Any]:
    from bookflow.hub import schema as h
    hub = getattr(s, 'hub', None)
    if hub is None:
        raise invalid('user', 'the hub is not open')
    u = h.users
    found = hub.conn.execute(sa.select(u.c.id, u.c.username, u.c.kind).where(
        sa.or_(u.c.id == text, u.c.username == text))).mappings().first()
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'user', 'selector': text, 'field': 'user',
                                                           'suggestions': []})
    if found['kind'] != 'human':
        raise invalid('user', 'link a person\'s login; an agent acts for its principal')
    company = getattr(s, 'company_row', None) or {}
    m = h.memberships
    member = hub.conn.execute(sa.select(m.c.id).where(m.c.user_id == found['id'], m.c.revoked_at.is_(None), sa.or_(
        sa.and_(m.c.scope_type == 'company', m.c.scope_id == company.get('id')),
        sa.and_(m.c.scope_type == 'organization', m.c.scope_id == company.get('organization_id')))).limit(1)).scalar()
    if not member:
        raise invalid('user', 'that login is not a member of this company')
    return dict(found)


def plan_link(s, ctx, inp) -> Plan:
    from bookflow.company.timekeeping_models import ClockLinkOutput
    person = employee(s, inp.employee)
    user = _hub_user(s, inp.user.strip()) if inp.user else None
    t = c.timekeeping_people
    old = row(s, t, t.c.employee_id == person['id'])
    user_id = user['id'] if user else None
    if user_id:
        other = row(s, t, t.c.user_id == user_id)
        if other and other['employee_id'] != person['id']:
            raise invalid('user', 'that login is already linked to another employee; unlink it there first')
    changed = (old['user_id'] if old else None) != user_id
    if old is None:
        new = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), employee_id=person['id'],
                   user_id=user_id)
    else:
        new = bumped(old, s, ctx, user_id=user_id) if changed else old
    out = ClockLinkOutput(employee_id=person['id'], employee_name=person['name'], user_id=user_id,
                          username=user['username'] if user else None, changed=changed)
    return Plan(out, dict(row=new, before=old, changed=changed))


def apply_link(plan: Plan, ctx, s) -> Applied:
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    touched = write_row(s, c.timekeeping_people, LINK_RECORD, plan.data['row'], plan.data['before'])
    out = plan.preview
    summary = (f"linked {out.employee_name} to login {out.username}" if out.user_id else f"unlinked {out.employee_name}")
    return Applied(out, [touched], summary)


# ---------------------------------------------------------------------------- time entries

def names(s, table, ids, column='full_name') -> dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return dict(s.company.conn.execute(sa.select(table.c.id, getattr(table.c, column)).where(table.c.id.in_(ids))).all())


def entries(s, employee_id: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
    """The person's recorded (not voided) time entries dated in the range, oldest first."""
    from bookflow.company import work
    d, r = c.work_documents, c.work_revisions
    found = s.company.conn.execute(
        sa.select(d).join(r, r.c.id == d.c.current_revision_id)
        .where(d.c.kind == KIND, d.c.status == 'recorded', r.c.date >= date_from, r.c.date <= date_to)
        .order_by(r.c.date, d.c.created_at, d.c.id)).mappings().all()
    result = []
    for header in found:
        header = dict(header)
        rev = work.revision(s, header)
        f = work.facts(rev)
        if not f.assignees or f.assignees[0].id != employee_id:
            continue
        lines = work.saved_lines(s, rev)
        if len(lines) != 1:
            continue
        line = work.line_facts(lines[0])
        klass = line.profile.class_id or f.profile.class_id
        result.append(dict(id=header['id'], version=header['version'], number=header['number'], date=rev['date'],
            customer_id=f.profile.customer.id, customer_name=f.profile.customer.label, item_id=line.item_id,
            item_name=line.profile.item.label, class_id=klass.id if klass else None,
            class_name=klass.label if klass else None, billable=line.billable,
            notes=(line.description or None), quantity=line.quantity_microunits))
    return result


def billed(s, document_ids) -> dict[str, list[str]]:
    """Each time entry an active sale carries, with that sale's type and number."""
    ids = list(document_ids)
    if not ids:
        return {}
    a, t = c.work_billing_allocations, c.transactions
    rows = s.company.conn.execute(
        sa.select(a.c.root_document_id, t.c.type, t.c.number).join(t, sa.and_(
            t.c.id == a.c.transaction_id, t.c.current_revision_id == a.c.revision_id, t.c.status == 'posted'))
        .where(a.c.root_document_id.in_(ids))).all()
    out: dict[str, list[str]] = {}
    for document_id, kind, number in rows:
        label = f"{kind.replace('_', ' ')} {number}"
        if label not in out.setdefault(document_id, []):
            out[document_id].append(label)
    return out


def entry_ctx(ctx, default_reason: str):
    """Corrections and withdrawals of time always carry a reason; a sheet or clock supplies its own."""
    if (ctx.reason or '').strip():
        return ctx
    return ctx.model_copy(update={'reason': default_reason})


def entry_write(s, ctx, operation: str, values: dict[str, Any], *, apply: bool, inactive_employee: str | None = None):
    """Plan, and when asked write, one time entry exactly as `time-activity` does.

    ``inactive_employee`` lets a clock that started while its person was active record their time
    although they were deactivated meanwhile.
    """
    from bookflow.company import time_tracking, work
    from bookflow.company.work_models import TimeActivityCreateInput, TimeActivityUpdateInput, TimeActivityVoidInput
    model = dict(create=TimeActivityCreateInput, update=TimeActivityUpdateInput, void=TimeActivityVoidInput)[operation]
    token = work.INACTIVE_ASSIGNEES_OK.set(frozenset({inactive_employee})) if inactive_employee else None
    try:
        plan = time_tracking.prepare(s, ctx, model(**values), operation)
        if not apply:
            return plan
        return work.apply(plan, ctx, s)
    finally:
        if token is not None:
            work.INACTIVE_ASSIGNEES_OK.reset(token)
