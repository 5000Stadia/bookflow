"""Document delivery, increment 4: recurring schedules (R112; design/document-delivery.md §6).

A **schedule** is a saved batch with a frequency: the kind and selection each run drafts (as ``delivery batch`` takes
them, with a statement period word: ``previous-month``, ``month-to-date``, ``since-last-run``), the template,
``combine``, the frequency from a start date (the memorized schedule's own date arithmetic; a month-end start stays at
month end), an optional stop date or count of runs, ``mode`` and ``active``.

**Each slot is claimed once.** ``delivery_schedule_runs`` is unique on ``(schedule_id, slot_date)``. A step -- the
host's scheduler tick, or ``delivery-schedule run`` -- claims the due slot and drafts its first chunk in one write,
through the batch's own planner (``delivery_batches``); later steps draft one chunk each until the batch is done. A
second tick, a restart or a person's ``run`` the same day finds the run already there and continues it.

**No catch-up beyond the latest slot.** After the host was off, only the most recent missed slot runs, and only if it
is no more than ``catch_up_days`` old (default 7); every other missed slot is recorded ``skipped`` (``superseded`` or
``outside_window``), never run. A missed email is not the books.

**A send schedule is an automation** (design/principal-directives.md §3). Anyone who may write schedules writes it as a
draft: its runs release what they draft without a go, so every message waits for a person, and one to-do per run says
so. A person makes it live (``delivery-schedule live``) after one check of its exact details (``live_check``: the
selection, cadence, first run and who it would reach today, with a fingerprint the act must carry). Live, each run's
releases carry the establisher's go, checked at every firing: an establisher no longer active or no longer allowed to
send pauses it with the reason and one to-do, and its runs then leave messages waiting. An operative change by anyone
but a person confirming the changed schedule's check returns it to draft. Until R187 lands, "a person" stands where
``automate.delivery`` will (each place says so).

**Whose authority drafts.** A run drafts as ``run_as``, the last actor to set what the schedule drafts, through
``delivery-schedule run`` with that actor's reads checked afresh (an agent: its assignment, suspension and epoch, as
R111's entries are); recorded with interface ``system``, client ``scheduler`` and the reason "On schedule: <name> for
<slot>". An actor who can no longer lend authority pauses the schedule and skips its slots (``no_authority``).
"""
from __future__ import annotations

import calendar
import hashlib
import json
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert

from bookflow.company import delivery as d
from bookflow.company import delivery_batch_models as bm
from bookflow.company import delivery_batches as db
from bookflow.company import delivery_schedule_models as sm
from bookflow.company import schema as c
from bookflow.company.memorized_schedule import advance, parse
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched

RECORD = 'delivery_schedule'
RUN_RECORD = 'delivery_schedule_run'
DEFAULT_CATCH_UP_DAYS = 7
SKIPS_PER_STEP = 200
DEFAULT_RUN_TIME = '06:00'
OPERATIVE = ('kind', 'selection', 'template_id', 'combine', 'frequency', 'start_date', 'stop_date', 'remaining_runs',
             'mode', 'catch_up_days', 'run_time')
SELECTION_FIELDS = ('queued', 'unsent', 'ready', 'period', 'customer', 'customer_type', 'balance_over', 'method')
KIND_PLURAL = {'invoice': 'invoices', 'sales-receipt': 'sales receipts', 'estimate': 'estimates',
               'statement': 'statements', 'notice': 'overdue notices'}
FREQUENCY_TEXT = {'daily': 'Every day', 'weekly': 'Every week', 'every_other_week': 'Every other week',
                  'every_four_weeks': 'Every four weeks', 'twice_a_month': 'Twice a month', 'monthly': 'Every month',
                  'every_other_month': 'Every other month', 'quarterly': 'Every quarter',
                  'twice_a_year': 'Twice a year', 'annually': 'Every year'}
SKIP_TEXT = {'superseded': 'a later slot was due; only the latest missed slot runs',
             'outside_window': 'older than the catch-up window when the host next ran',
             'stopped': 'the schedule was stopped',
             'no_authority': 'nobody with the authority to run it',
             'unavailable': 'the books refused for now; the schedule goes on at its next slot',
             'failed': 'the run failed'}
# Refusals that pass: the slot is skipped with why and the schedule keeps running.
TRANSIENT = frozenset({'E_DB_BUSY', 'E_VERSION_CONFLICT'})
# Refusals that mean the actor no longer has the authority: the schedule pauses.
NO_AUTHORITY = frozenset({'E_PERMISSION', 'E_NO_ACTOR', 'E_UNAUTHENTICATED'})
# The host tick's company-local time of day (HH:MM) while it runs schedules; None otherwise.
_TICK_TIME: ContextVar[str | None] = ContextVar('delivery_schedule_tick_time', default=None)


def _invalid(field, problem):
    return d._invalid(field, problem)


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------- dates

def _unit(frequency):
    from bookflow.company.memorized_schema import FREQUENCIES
    return FREQUENCIES[frequency][0]


def anchor_day(frequency: str, start: str) -> int | None:
    """The day a month-family schedule keeps: the start's day, or 31 when the start is a month's last day. Twice a
    month keeps a pair: the start and 15 days after (a start after the 15th: 15 days before and the start); a start
    on the 29th to 31st keeps the 15th and the month's last day (anchor 31)."""
    unit = _unit(frequency)
    if unit not in ('month', 'semimonth'):
        return None
    at = parse(start)
    if unit == 'semimonth':
        return at.day if at.day <= 15 else (at.day - 15 if at.day <= 28 else 31)
    if at.day == calendar.monthrange(at.year, at.month)[1]:
        return 31
    return at.day


def _pair(year, month, anchor) -> list[date]:
    last = calendar.monthrange(year, month)[1]
    days = (15, last) if anchor == 31 else (anchor, min(anchor + 15, last))
    return [date(year, month, x) for x in sorted(set(days))]


def _advance(frequency, slot, anchor):
    """The slot after ``slot``: the memorized schedule's arithmetic, except twice a month (``_pair``)."""
    if _unit(frequency) != 'semimonth':
        return advance(frequency, slot, anchor)
    at = parse(slot)
    for candidate in _pair(at.year, at.month, anchor):
        if candidate > at:
            return candidate.isoformat()
    following = (at.replace(day=1) + timedelta(days=32)).replace(day=1)
    return _pair(following.year, following.month, anchor)[0].isoformat()


def first_slot(frequency, start) -> str:
    """The first slot on or after the start: the start itself, or for twice a month from the 29th to 31st, the
    month's last day."""
    at = parse(start)
    if _unit(frequency) == 'semimonth' and at.day >= 29:
        return at.replace(day=calendar.monthrange(at.year, at.month)[1]).isoformat()
    return start


def _local(zone, at=None) -> datetime:
    from zoneinfo import ZoneInfo
    moment = at or clock.now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return moment.astimezone(ZoneInfo(zone)) if zone else moment
    except Exception:  # noqa: BLE001 - an unknown zone reads as UTC, as the memorized schedule's today does
        return moment


def moment(s) -> tuple[str, str]:
    """Company-local (today, HH:MM): the host tick's own, else now (a fixed working day reads as its end)."""
    from bookflow.company.memorized_schedule import today
    day = today(s.company_tz)
    if _TICK_TIME.get() is not None:
        return day, _TICK_TIME.get()
    if clock.working_day() is not None:
        return day, '23:59'
    return day, _local(s.company_tz).strftime('%H:%M')


def through(row, today: str, hhmm: str) -> str:
    """The last day whose slot is due now: today once its run time has come, else yesterday."""
    if hhmm >= (row.get('run_time') or DEFAULT_RUN_TIME):
        return today
    return (parse(today) - timedelta(days=1)).isoformat()


def _finished(slot, row, runs_left) -> bool:
    if slot is None:
        return True
    if runs_left is not None and runs_left <= 0:
        return True
    return bool(row['stop_date'] and slot > row['stop_date'])


def due_slots(row, today: str, limit: int = 100000) -> list[str]:
    """Every slot from ``next_date`` through today, oldest first (bounded)."""
    out, slot, left = [], row['next_date'], row['remaining_runs']
    while not _finished(slot, row, left) and slot <= today and len(out) < limit:
        out.append(slot)
        slot = _advance(row['frequency'], slot, row['anchor_day'])
    return out


def _after(row, slot):
    nxt = _advance(row['frequency'], slot, row['anchor_day'])
    return None if nxt is None or (row['stop_date'] and nxt > row['stop_date']) else nxt


def _days(a: str, b: str) -> int:
    return (parse(b) - parse(a)).days


def first_run(row, today: str) -> str | None:
    """The slot that runs next: the latest missed slot if inside the window, else the next one ahead."""
    if row['next_date'] is None:
        return None
    slots = due_slots(row, today)
    if slots and _days(slots[-1], today) <= row['catch_up_days']:
        return slots[-1]
    slot = slots[-1] if slots else row['next_date']
    while slot is not None and slot < today:
        slot = _after(row, slot)
    return slot


def period_for(s, row, slot: str) -> tuple[str | None, str | None]:
    """A statement run's period for its slot; (None, None) for other kinds."""
    word = json.loads(row['selection']).get('period')
    if row['kind'] != 'statement' or not word:
        return None, None
    at = parse(slot)
    first = at.replace(day=1)
    previous = ((first - timedelta(days=1)).replace(day=1).isoformat(), (first - timedelta(days=1)).isoformat())
    if word == 'month-to-date':
        return first.isoformat(), slot
    if word == 'since-last-run' and row.get('id'):
        r = c.delivery_schedule_runs
        last = s.company.conn.execute(sa.select(sa.func.max(r.c.date_to)).where(
            r.c.schedule_id == row['id'], r.c.state == 'done', r.c.date_to.is_not(None), r.c.slot_date < slot)).scalar()
        if last and last < slot:
            return (parse(last) + timedelta(days=1)).isoformat(), slot
    return previous


def batch_input(s, row, slot: str, *, send: bool | None = None) -> bm.BatchInput:
    sel = json.loads(row['selection'])
    data = {k: v for k, v in sel.items() if k in ('queued', 'unsent', 'ready', 'customer', 'customer_type',
                                                   'balance_over', 'method') and v not in (None, False)}
    date_from, date_to = period_for(s, row, slot)
    if row['kind'] == 'statement':
        data.update(date_from=date_from, date_to=date_to)
    if row['combine']:
        data['combine'] = True
    if row['template_id']:
        data['template'] = row['template_id']
    data.update(kind=row['kind'], send=row['mode'] == 'send' if send is None else send)
    return bm.BatchInput.model_validate(data)


# ---------------------------------------------------------------- words

def selection_text(s, row) -> str:
    sel = json.loads(row['selection'])
    kind = row['kind']
    if kind == 'statement':
        period = {'previous-month': 'the previous month', 'month-to-date': 'the month to date',
                  'since-last-run': 'the days since the last run'}.get(sel.get('period'), '?')
        text = f"Statements for {period} to every customer with a balance over {sel.get('balance_over') or '0'}"
        if sel.get('method') == 'email':
            text += ', email customers only'
    elif kind == 'notice':
        text = 'Every ready overdue notice'
    elif sel.get('queued'):
        text = f"Every {KIND_PLURAL[kind][:-1]} to be emailed (flagged Email later, or by the auto-flag date)"
    else:
        text = f"Every {KIND_PLURAL[kind][:-1]} with no live or sent message"
    if sel.get('customer'):
        text += f", customer {_customer_name(s, sel['customer'])}"
    if sel.get('customer_type'):
        text += f", customers of type {sel['customer_type']}"
    if row['combine']:
        text += ', one message per customer'
    return text


def _customer_name(s, selector):
    try:
        from bookflow.company.parties import resolve_party
        return resolve_party(s.company, 'customer', selector)['full_name']
    except BookflowError:
        return selector


def cadence(row) -> str:
    text = f"{FREQUENCY_TEXT[row['frequency']]} from {row['start_date']}"
    if row['anchor_day'] == 31:
        text += ' (the 15th and the last day)' if row['frequency'] == 'twice_a_month' else ' (month end)'
    text += f" at {row.get('run_time') or DEFAULT_RUN_TIME}"
    if row['stop_date']:
        text += f", until {row['stop_date']}"
    if row['remaining_runs'] is not None:
        text += f", {row['remaining_runs']} more run{'s' if row['remaining_runs'] != 1 else ''}"
    return text


def _name(s, user_id):
    from bookflow.company.scheduler import _name as name
    return name(s, user_id) if user_id else None


def is_finished(row) -> bool:
    return row['next_date'] is None or _finished(row['next_date'], row, row['remaining_runs'])


def status_label(s, row) -> str:
    if is_finished(row):
        return 'Finished'
    if not row['active']:
        return 'Stopped'
    if row['state'] == 'live':
        return f"Live: sends under {_name(s, row['established_by'])}"
    if row['state'] == 'paused':
        return f"Paused: {row['paused_reason']}"
    if row['mode'] == 'send':
        back = f" (back to draft: {row['draft_reason']})" if row.get('draft_reason') else ''
        return f'Draft{back}: its messages wait for a go until a person makes it live'
    return 'Drafts only'


def _template_name(s, row) -> str:
    if not row['template_id']:
        return 'the default template'
    t = c.delivery_templates
    return s.company.conn.execute(sa.select(t.c.name).where(t.c.id == row['template_id'])).scalar() or '?'


def _run_out(s, r) -> sm.RunOutput:
    number = None
    if r['batch_id']:
        number = s.company.conn.execute(sa.select(c.delivery_batches.c.number).where(
            c.delivery_batches.c.id == r['batch_id'])).scalar()
    return sm.RunOutput(run_id=r['id'], slot_date=r['slot_date'], state=r['state'], batch=number, mode=r['mode'],
                        go=bool(r['go_by']), date_from=r['date_from'], date_to=r['date_to'], planned=r['planned'],
                        drafted=r['drafted'], released=r['released'], skipped=r['skipped_count'], reason=r['reason'],
                        reason_text=r['reason_text'], started_at=r['created_at'], finished_at=r['finished_at'])


def _runs(s, schedule_id, limit=10):
    r = c.delivery_schedule_runs
    return [dict(x) for x in s.company.conn.execute(sa.select(r).where(r.c.schedule_id == schedule_id).order_by(
        r.c.slot_date.desc()).limit(limit)).mappings()]


def output(s, row, *, warnings=(), check=None, ran=(), runs=10) -> sm.ScheduleOutput:
    commands = []
    if row.get('id'):
        name = row['name']
        if row['mode'] == 'send' and row['state'] != 'live':
            commands.append(f'delivery-schedule live "{name}" --dry-run (a person: shows the check, then make it '
                            'live with its fingerprint as confirm)')
        commands.append(f'delivery-schedule {"resume" if not row["active"] else "pause"} "{name}"')
    return sm.ScheduleOutput(
        schedule_id=row.get('id'), name=row['name'], kind=row['kind'], selection=json.loads(row['selection']),
        selection_text=selection_text(s, row), template=_template_name(s, row), combine=bool(row['combine']),
        frequency=row['frequency'], cadence=cadence(row), start_date=row['start_date'], stop_date=row['stop_date'],
        remaining_runs=row['remaining_runs'], next_date=row['next_date'], mode=row['mode'], active=bool(row['active']),
        state=row['state'], status_label=status_label(s, row), established_by=row['established_by'],
        established_by_name=_name(s, row['established_by']), established_at=row['established_at'],
        paused_reason=row['paused_reason'], catch_up_days=row['catch_up_days'],
        run_time=row.get('run_time') or DEFAULT_RUN_TIME, draft_reason=row.get('draft_reason'),
        runs_as_name=_name(s, row['run_as']),
        runs=[_run_out(s, r) for r in _runs(s, row['id'], runs)] if runs and row.get('id') else [], check=check,
        ran=list(ran), version=row.get('version'), warnings=list(warnings), next_commands=commands)


# ---------------------------------------------------------------- the check before going live

def _operative(row) -> dict:
    return {k: row[k] for k in OPERATIVE}


def _template_content(s, row):
    """The template a run would use, as (id, subject, body): the schedule's own, else the kind's default."""
    t = c.delivery_templates
    q = sa.select(t.c.id, t.c.subject, t.c.body)
    q = q.where(t.c.id == row['template_id']) if row['template_id'] else q.where(
        t.c.kind == row['kind'], t.c.is_default.is_(True), t.c.active.is_(True))
    found = s.company.conn.execute(q).first()
    return list(found) if found else None


def rule_fingerprint(s, row) -> str:
    """What a person confirms (design/document-delivery.md §6): the rule -- selection, cadence and run time, template
    id and the template's exact text (the kind's default when none is named), combine, mode, catch-up window. Not
    how many documents match on a given day: a dynamic selection is the point."""
    return _sha([_operative(row), _template_content(s, row)])[:32]


def live_check(s, ctx, row, today: str) -> sm.LiveCheck:
    """One check of the exact details a person makes live: what it drafts, how often, its first run, its template, and
    who it would reach if it ran today (shown, not bound). The fingerprint covers the rule (``rule_fingerprint``)."""
    inp = batch_input(s, row, today, send=True)
    _, targets, skipped, prints = db.resolve(s, ctx, inp)
    period = f'{inp.date_from} to {inp.date_to}' if row['kind'] == 'statement' else None
    fingerprint = rule_fingerprint(s, row)
    who = _name(s, s.actor.id) if s.actor.kind == 'human' else None
    return sm.LiveCheck(
        selection=selection_text(s, row), cadence=cadence(row), first_run=first_run(row, today), period=period,
        template=_template_name(s, row), would_reach=[db._item(x) for x in targets[:200]], reach_count=len(targets),
        skipped=[db._item(x) for x in skipped[:50]], skipped_count=len(skipped),
        runs_as=(f"Yours ({who}): each run releases its messages with your go while you are active and may send "
                 "this company's email; otherwise it pauses and raises a to-do") if who else
                "the authority of the person who makes it live",
        fingerprint=fingerprint)


# ---------------------------------------------------------------- reading

def resolve(s, selector) -> dict:
    t = c.delivery_schedules
    found = s.company.conn.execute(sa.select(t).where(sa.or_(
        t.c.id == selector, t.c.name_key == selector.strip().casefold()))).mappings().first()
    if found is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': selector})
    return dict(found)


def show(s, ctx, inp) -> sm.ScheduleOutput:
    row = resolve(s, inp.schedule)
    from bookflow.company.memorized_schedule import today
    check = live_check(s, ctx, row, today(s.company_tz)) if getattr(inp, 'check', False) else None
    return output(s, row, check=check, runs=inp.runs)


def query(s, inp) -> sm.SchedulePageOutput:
    t = c.delivery_schedules
    q = sa.select(t).order_by(t.c.name_key).limit(inp.limit + 1)
    if inp.kind:
        q = q.where(t.c.kind == inp.kind)
    rows = [dict(r) for r in s.company.conn.execute(q).mappings()]
    out = []
    for row in rows[:inp.limit]:
        last = _runs(s, row['id'], 1)
        out.append(sm.ScheduleRow(schedule_id=row['id'], name=row['name'], kind=row['kind'], cadence=cadence(row),
                                  next_date=row['next_date'], mode=row['mode'], active=bool(row['active']),
                                  state=row['state'], status_label=status_label(s, row),
                                  last_run=_run_out(s, last[0]) if last else None, version=row['version']))
    return sm.SchedulePageOutput(rows=out, count=len(out), truncated=len(rows) > inp.limit)


# ---------------------------------------------------------------- create and update

def _check_selection(s, row):
    """Missing specifics are refused, naming what to give, exactly as a batch refuses them."""
    sel = json.loads(row['selection'])
    if row['kind'] == 'statement' and not sel.get('period'):
        raise _invalid('period', 'say which period each run covers: previous-month, month-to-date or since-last-run')
    if row['kind'] != 'statement' and sel.get('period'):
        raise _invalid('period', 'is for statements only')
    try:
        inp = batch_input(s, row, row['start_date'])
    except BookflowError:
        raise
    except Exception as e:  # pydantic: a value the batch would refuse
        raise _invalid('selection', str(e).splitlines()[0][:200]) from None
    db.check_selection(inp)


def _authority(s, ctx) -> dict:
    """Who a run drafts as: this actor, with an agent's principal and epoch (R111's checks at every run)."""
    epoch = None
    if s.actor.kind == 'agent':
        from bookflow.hub.credentials import _binding
        _, epoch = _binding(s.hub, s.actor.id, ctx.on_behalf_of)
    return dict(run_as=s.actor.id, run_as_for=ctx.on_behalf_of if s.actor.kind == 'agent' else None,
                run_as_epoch=epoch)


def _fields(s, inp, row) -> dict:
    """Apply the given fields onto ``row`` (a copy), validating each."""
    out = dict(row)
    sel = json.loads(row['selection'])
    given = inp.model_fields_set
    for k in SELECTION_FIELDS:
        if k in given:
            value = getattr(inp, k)
            if value in (None, False):
                sel.pop(k, None)
            else:
                sel[k] = value
    if 'balance_over' in sel:
        db._decimal(sel['balance_over'], 'balance_over')
    out['selection'] = json.dumps(sel, sort_keys=True)
    if getattr(inp, 'name', None) is not None:
        name = ' '.join(inp.name.split())
        if not name:
            raise _invalid('name', 'give the schedule a name')
        out.update(name=name, name_key=name.casefold())
    if getattr(inp, 'kind', None) is not None:
        out['kind'] = inp.kind
    for k in ('combine', 'frequency', 'start_date', 'stop_date', 'remaining_runs', 'mode', 'catch_up_days', 'run_time'):
        if k in given and getattr(inp, k) is not None:
            out[k] = getattr(inp, k)
    if getattr(inp, 'no_end', False):
        out.update(stop_date=None, remaining_runs=None)
    if 'template' in given:
        out['template_id'] = None
        if inp.template:
            from bookflow.company import delivery_templates as t
            template = t.resolve(s, inp.template, out['kind'])
            if not template['active']:
                raise _invalid('template', f"{template['name']} is inactive")
            out['template_id'] = template['id']
    elif out.get('template_id') and out['kind'] != row.get('kind'):
        raise _invalid('template', 'the kind changed: choose a template of the new kind, or none for its default')
    if out['stop_date'] and out['stop_date'] < out['start_date']:
        raise _invalid('stop_date', 'is before start_date')
    new = row.get('id') is None
    # The slots move only when the cadence itself changed (the browser form sends both fields on every save).
    if new or out['frequency'] != row['frequency'] or out['start_date'] != row['start_date']:
        out['anchor_day'] = anchor_day(out['frequency'], out['start_date'])
        first = first_slot(out['frequency'], out['start_date'])
        out['next_date'] = first if new else _not_before(s, out, first)
    if {'stop_date', 'remaining_runs', 'no_end'} & given and is_finished(out) and not new:
        out['next_date'] = _not_before(s, out, _resume_from(s, out))
    if out['next_date'] and _finished(out['next_date'], out, out['remaining_runs']):
        out['next_date'] = None  # finished: reads Finished, and the tick never looks at it
    return out


def _last_slot(s, schedule_id):
    r = c.delivery_schedule_runs
    return s.company.conn.execute(sa.select(sa.func.max(r.c.slot_date)).where(
        r.c.schedule_id == schedule_id)).scalar()


def _resume_from(s, row):
    """A finished schedule given a later end picks up from the slot after its last one."""
    last = _last_slot(s, row['id'])
    return _advance(row['frequency'], last, row['anchor_day']) if last else first_slot(row['frequency'],
                                                                                       row['start_date'])


def _not_before(s, row, slot):
    """``slot``, or the first slot after it that is neither in the past nor already claimed: an edit never moves the
    next slot into the past."""
    today, last = _today(s), _last_slot(s, row['id'])
    for _ in range(100000):
        if slot is None or (slot >= today and (last is None or slot > last)):
            return slot
        slot = _advance(row['frequency'], slot, row['anchor_day'])
    return None


def _name_taken(s, key, own=None):
    t = c.delivery_schedules
    q = sa.select(t.c.id).where(t.c.name_key == key)
    if own:
        q = q.where(t.c.id != own)
    return s.company.conn.execute(q).first() is not None


def prepare_create(s, ctx, inp) -> Plan:
    missing = [k for k in ('frequency', 'start_date') if getattr(inp, k) is None]
    if missing:
        raise _invalid(missing[0], 'say how often it runs (frequency) and its first day (start_date)')
    name = ' '.join(inp.name.split())
    base = dict(id=None, version=None, name=name, name_key=name.casefold(), kind=inp.kind, selection='{}',
                template_id=None, combine=False, frequency=inp.frequency, start_date=inp.start_date, stop_date=None,
                remaining_runs=None, next_date=inp.start_date, anchor_day=None, mode='draft', active=True,
                state='draft', established_by=None, established_at=None, paused_reason=None, paused_at=None,
                catch_up_days=DEFAULT_CATCH_UP_DAYS, run_time=DEFAULT_RUN_TIME, confirmed=None, draft_reason=None)
    row = _fields(s, inp, base)
    row['combine'] = bool(row['combine'])
    if _name_taken(s, row['name_key']):
        raise _invalid('name', f"a schedule named {row['name']!r} exists")
    _check_selection(s, row)
    row.update(_authority(s, ctx))
    warnings = []
    if row['mode'] == 'send':
        warnings.append('A send schedule is a draft until a person makes it live (delivery-schedule live); until '
                        'then each run leaves its messages waiting for a go.')
    from bookflow.company.memorized_schedule import today
    if row['start_date'] < today(s.company_tz):
        warnings.append(f"It starts in the past: only the latest missed slot runs, and only if within "
                        f"{row['catch_up_days']} days.")
    at = clock.now_iso()
    row.update(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
               updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    return Plan(output(s, row, warnings=warnings, runs=0), dict(row=row, warnings=warnings))


def apply_create(plan, ctx, s) -> Applied:
    row = plan.data['row']
    s.company.conn.execute(c.delivery_schedules.insert().values(**row))
    return Applied(output(s, row, warnings=plan.data['warnings']),
                   [Touched(RECORD, row['id'], 'create', None, 1, row, db='company')],
                   f"delivery schedule {row['name']}")


def _check_version(row, expected):
    if expected is not None and row['version'] != expected:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': RECORD, 'record_id': row['id'], 'expected_version': expected,
            'current_version': row['version'], 'updated_by': row['updated_by'], 'updated_via': row['updated_via']})


def _refuse_without_authority(s, row):
    """Making it live (or keeping it live through a change) is lending one's own go: refused, saying why, when the
    person could not give a go now (R187: automate.delivery and delivery.send)."""
    why = authority_lost(s, s.actor.id)
    if why is not None:
        text = f"You cannot make schedule {row['name']} live: {why}."
        raise BookflowError('E_PERMISSION', message=text, details={
            'reason': 'no_send_authority', 'command': 'delivery-schedule live', 'problem': why,
            'needs': 'automate.delivery'})


def _require_automate(s):
    """automate.delivery (R187 §3, company.automation): an agent needs its principal's grant, and everyone the
    establisher test each firing repeats. The go its runs release is still checked by _refuse_without_authority,
    which until R187's consumer step counts only a person's go."""
    from bookflow.company import automation
    automation.require_establish(s, CAPABILITY, 'delivery-schedule live')


def _establish(s, ctx, row, check) -> dict:
    """The fields a send schedule made live by this actor takes: under them, with exactly the check they confirmed."""
    if is_finished(row):
        raise BookflowError('E_WORK_DEPENDENCY', message=f"Schedule {row['name']} has run out: give it a later "
                            'stop date or more runs first.', details={
                                'record_type': RECORD, 'record_id': row['id'], 'status': 'finished',
                                'problem': 'it has no slot left'})
    return dict(state='live', established_by=s.actor.id, established_at=clock.now_iso(), paused_reason=None,
                paused_at=None, confirmed=check.fingerprint, draft_reason=None, **_authority(s, ctx))


def _today(s):
    from bookflow.company.memorized_schedule import today
    return today(s.company_tz)


def prepare_update(s, ctx, inp) -> Plan:
    before = resolve(s, inp.schedule)
    _check_version(before, inp.expected_version)
    after = _fields(s, inp, before)
    if after['name_key'] != before['name_key'] and _name_taken(s, after['name_key'], before['id']):
        raise _invalid('name', f"a schedule named {after['name']!r} exists")
    operative = _operative(after) != _operative(before)
    if operative:
        _check_selection(s, after)
        after.update(_authority(s, ctx))
    warnings, check = [], None
    if inp.confirm is not None:
        _require_automate(s)
        _refuse_without_authority(s, before)
    from bookflow.company import automation
    # Asked only where it decides something: a dry run's check, or an operative edit of a live schedule.
    holder = (s.dry_run or (operative and before['state'] == 'live')) and automation.holds_now(s, CAPABILITY)
    if before['state'] != 'live' and after['mode'] == 'send' and (inp.confirm is not None or (s.dry_run and holder)):
        # R187 §3: `update --mode send --confirm` by a holder of automate.delivery makes it live, as `live` does: one
        # check of the exact details (dry_run shows it), then its fingerprint as confirm.
        check = live_check(s, ctx, after, _today(s))
        if inp.confirm is not None and not s.dry_run:
            if inp.confirm != check.fingerprint:
                raise _changed(before)
            after.update(_establish(s, ctx, after, check))
            if not after['active']:
                warnings.append('It is stopped: resume it for it to run.')
    elif operative and before['state'] in ('live', 'paused'):
        stays = False
        if after['mode'] == 'send' and before['state'] == 'live' and holder:
            # R187: a holder of automate.delivery who confirms the changed schedule's check keeps it live, under them.
            check = live_check(s, ctx, after, _today(s))
            stays = inp.confirm == check.fingerprint
            if inp.confirm is not None and not stays and not s.dry_run:
                raise _changed(before)
        if stays:
            at = clock.now_iso()
            after.update(established_by=s.actor.id, established_at=at, confirmed=check.fingerprint)
        else:
            after.update(state='draft', established_by=None, established_at=None, paused_reason=None, paused_at=None,
                         confirmed=None)
            if before['state'] == 'live':
                after['draft_reason'] = f"changed by {_name(s, s.actor.id) or s.actor.id}"[:500]
                warnings.append('This change returns it to draft: its runs leave messages waiting for a go until a '
                                'person makes it live again with the check of the changed schedule.')
    if not operative and after == before and check is None:
        return Plan(output(s, before, warnings=['Nothing changed.']), dict(idle=True))
    after = d._touch(s, ctx, after) if not s.dry_run else dict(after, version=before['version'] + 1)
    drop = before['state'] == 'live' and after['state'] != 'live'
    return Plan(output(s, after, warnings=warnings, check=check), dict(before=before, after=after,
                                                                       warnings=warnings, drop_go=drop))


def _changed(row):
    text = (f"Schedule {row['name']} is not what the check showed (its details or who it would reach changed). "
            'Show the check again.')
    return BookflowError('E_WORK_DEPENDENCY', message=text, details={
        'record_type': RECORD, 'record_id': row['id'], 'status': row['state'], 'problem': text,
        'next': 'delivery-schedule live with dry_run, then again with its fingerprint as confirm'})


def _write(s, before, after) -> Touched:
    t = c.delivery_schedules
    done = s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version'])
                                  .values(**{k: v for k, v in after.items() if k != 'id'}))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': RECORD, 'record_id': before['id']})
    return Touched(RECORD, after['id'], 'update', before['version'], after['version'], after, before, db='company')


def apply_update(plan, ctx, s) -> Applied:
    if plan.data.get('idle'):
        return Applied(plan.preview, [], 'nothing changed')
    before, after = plan.data['before'], plan.data['after']
    touched = [_write(s, before, after)]
    if plan.data.get('drop_go'):
        touched += end_open_run(s, ctx, after, after['draft_reason'] or 'returned to draft', cancel=False)
    return Applied(output(s, after, warnings=plan.data['warnings']), touched,
                   f"delivery schedule {after['name']} updated")


# ---------------------------------------------------------------- live, pause, resume

def prepare_live(s, ctx, inp) -> Plan:
    """Make a send schedule live: one check of its exact details. A person's act until R187 (automate.delivery)."""
    _require_automate(s)  # dispatch asks an agent first; kept so the rule is visible here too
    before = resolve(s, inp.schedule)
    _check_version(before, inp.expected_version)
    _refuse_without_authority(s, before)
    if is_finished(before):
        raise BookflowError('E_WORK_DEPENDENCY', message=f"Schedule {before['name']} has run out: give it a later "
                            'stop date or more runs first.', details={
                                'record_type': RECORD, 'record_id': before['id'], 'status': 'finished',
                                'problem': 'it has no slot left'})
    candidate = dict(before, mode='send')
    check = live_check(s, ctx, candidate, _today(s))
    if before['state'] == 'live' and before['established_by'] == s.actor.id and before['mode'] == 'send':
        return Plan(output(s, before, check=check, warnings=['It is already live under you.']), dict(idle=True))
    if s.dry_run:
        return Plan(output(s, candidate, check=check), dict(idle=True))
    if not inp.confirm:
        raise _invalid('confirm', 'run it with dry_run first, show the person the check (selection, cadence, first '
                                  "run, who it would reach today), then pass the check's fingerprint as confirm")
    if inp.confirm != check.fingerprint:
        raise _changed(before)
    established = _establish(s, ctx, candidate, check)
    after = d._touch(s, ctx, dict(candidate, **established), established['established_at'])
    warnings = [] if after['active'] else ['It is stopped: resume it for it to run.']
    return Plan(output(s, after, check=check, warnings=warnings), dict(before=before, after=after,
                                                                       warnings=warnings))


def prepare_pause(s, ctx, inp) -> Plan:
    """Stop it: nothing runs until it is resumed. Open to anyone who may write schedules; stopping is safe."""
    before = resolve(s, inp.schedule)
    _check_version(before, inp.expected_version)
    if not before['active']:
        return Plan(output(s, before, warnings=['It is already stopped.']), dict(idle=True))
    after = d._touch(s, ctx, dict(before, active=False))
    return Plan(output(s, after), dict(before=before, after=after, warnings=[], drop_go='stopped'))


def prepare_resume(s, ctx, inp) -> Plan:
    """Run it again from today: slots that passed while it was stopped are skipped. A paused schedule comes back as a
    draft (a send schedule then waits for a person to make it live again)."""
    before = resolve(s, inp.schedule)
    _check_version(before, inp.expected_version)
    if before['active'] and before['state'] != 'paused':
        return Plan(output(s, before, warnings=['It is already running.']), dict(idle=True))
    after = dict(before, active=True)
    warnings = []
    if before['state'] == 'paused':
        after.update(state='draft', paused_reason=None, paused_at=None, established_by=None, established_at=None,
                     confirmed=None)
        if before['mode'] == 'send':
            warnings.append('It is a draft again: a person makes it live (delivery-schedule live) for its messages '
                            'to carry a go.')
    today = _today(s)
    stopped = [x for x in due_slots(before, today, SKIPS_PER_STEP) if x < today] if not before['active'] else []
    after = d._touch(s, ctx, after)
    if stopped:
        after['next_date'] = _after(before, stopped[-1])
        warnings.append(f"{len(stopped)} slot(s) passed while it was stopped; they are skipped.")
    return Plan(output(s, after, warnings=warnings), dict(before=before, after=after, warnings=warnings,
                                                          skip=[(x, 'stopped') for x in stopped]))


def _left(row, used):
    return None if row['remaining_runs'] is None else row['remaining_runs'] - used


def apply_act(plan, ctx, s) -> Applied:
    if plan.data.get('idle'):
        return Applied(plan.preview, [], 'nothing changed')
    before, after = plan.data['before'], plan.data['after']
    touched = [_write(s, before, after)]
    for slot, reason in plan.data.get('skip', ()):
        touched += _skip(s, ctx, after, slot, reason)
    if plan.data.get('drop_go'):
        touched += end_open_run(s, ctx, after, plan.data['drop_go'], cancel=False)
    out = output(s, after, warnings=plan.data['warnings'], check=plan.preview.check)
    return Applied(out, touched, f"delivery schedule {after['name']}: {out.status_label}")


# ---------------------------------------------------------------- runs

def _run_row(s, ctx, schedule, slot, state, **extra) -> dict:
    at = clock.now_iso()
    row = dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
               updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, schedule_id=schedule['id'],
               slot_date=slot, state=state, batch_id=None, mode=schedule['mode'], go_by=None, date_from=None,
               date_to=None, planned=0, drafted=0, released=0, skipped_count=0, reason=None, reason_text=None,
               finished_at=at if state != 'drafting' else None)
    row.update(extra)
    return row


def _claim(s, row) -> bool:
    """Insert a run for its slot; False when the slot was already claimed (a run or a skip is there)."""
    done = s.company.conn.execute(insert(c.delivery_schedule_runs).values(**row).on_conflict_do_nothing(
        index_elements=['schedule_id', 'slot_date']))
    return done.rowcount == 1


def _skip(s, ctx, schedule, slot, reason, text=None) -> list[Touched]:
    row = _run_row(s, ctx, schedule, slot, 'skipped', reason=reason, reason_text=text or SKIP_TEXT[reason])
    return [Touched(RUN_RECORD, row['id'], 'create', None, 1, row, db='company')] if _claim(s, row) else []


def claim_due(s, ctx, schedule, today, upto=None) -> tuple[dict, list[Touched], list[dict]]:
    """Claim what is due through ``upto`` (today once the run time has come, else yesterday): the latest missed slot
    as a run when inside the window; every other due slot skipped with why. Returns the schedule as now stored, what
    was written, and the runs claimed or skipped. A schedule that has finished gets no next slot."""
    slots = due_slots(schedule, upto or today)
    if not slots:
        if schedule['next_date'] and is_finished(schedule):
            after = d._touch(s, ctx, dict(schedule, next_date=None))
            return after, [_write(s, schedule, after)], []
        return schedule, [], []
    touched, made, used = [], [], 0
    latest = slots[-1]
    older = slots[:-1][:SKIPS_PER_STEP]
    for slot in older:
        reason = 'outside_window' if _days(slot, today) > schedule['catch_up_days'] else 'superseded'
        out = _skip(s, ctx, schedule, slot, reason)
        touched += out
        made += [t.after for t in out]
    if len(slots) - 1 > len(older):  # a long gap: the rest on the next step
        last = older[-1]
    else:
        last = latest
        if _days(latest, today) > schedule['catch_up_days']:
            out = _skip(s, ctx, schedule, latest, 'outside_window')
        else:
            row = _run_row(s, ctx, schedule, latest, 'drafting')
            out = [Touched(RUN_RECORD, row['id'], 'create', None, 1, row, db='company')] if _claim(s, row) else []
            used = len(out)
        touched += out
        made += [t.after for t in out]
    left = _left(schedule, used)
    nxt = _after(schedule, last)
    after = d._touch(s, ctx, dict(schedule, next_date=None if _finished(nxt, schedule, left) else nxt,
                                  remaining_runs=left))
    touched.append(_write(s, schedule, after))
    return after, touched, made


def open_run(s, schedule_id) -> dict | None:
    r = c.delivery_schedule_runs
    found = s.company.conn.execute(sa.select(r).where(r.c.schedule_id == schedule_id, r.c.state == 'drafting')
                                   .order_by(r.c.slot_date).limit(1)).mappings().first()
    return dict(found) if found else None


def authority_lost(s, user_id) -> str | None:
    """Why the establisher can no longer send under this schedule, or None. Interim rule until R187: what a person's
    go needs (active, a standing membership at standard that does not deny customer). R187: the establisher still
    holds automate.delivery and delivery.send."""
    from bookflow.company import delivery_dispatch
    return delivery_dispatch.go_lost(s, user_id)


def _assignee(s, schedule) -> str | None:
    from bookflow.company.scheduler import _active_admin
    from bookflow.hub import schema as h
    for user_id in (schedule['established_by'], schedule['run_as'], schedule['run_as_for']):
        if not user_id:
            continue
        user = s.hub.conn.execute(sa.select(h.users.c.kind, h.users.c.active).where(h.users.c.id == user_id)).first()
        if user is not None and user[0] == 'human' and user[1]:
            return user_id
    return _active_admin(s)


def _todo(s, ctx, schedule, source_ref, title, notes, assignee=None) -> list[Touched]:
    from bookflow.company import todos
    assignee = assignee or _assignee(s, schedule)
    if assignee is None:
        return []
    todo = todos.scheduler_todo(s, ctx, source_ref=source_ref, title=title, notes=notes, assignee_id=assignee,
                                due_date=clock.today_iso())
    if todo is None:
        return []
    s.company.conn.execute(c.todos.insert().values(**todo))
    return [Touched('todo', todo['id'], 'create', None, 1, todo, db='company')]


def _standing_todo(s, ctx, schedule, title, notes) -> list[Touched]:
    """One to-do per schedule, updated in place (reopened if it was done), never one per run."""
    t = c.todos
    ref = f"delivery-schedule-waiting:{schedule['id']}"
    found = s.company.conn.execute(sa.select(t).where(t.c.source_ref == ref)).mappings().first()
    if found is None:
        return _todo(s, ctx, schedule, ref, title, notes)
    before = dict(found)
    if before['status'] == 'inactive':
        return []
    after = d._touch(s, ctx, dict(before, title=title[:160], notes=notes, status='open', completed_at=None,
                                  completed_by=None, due_date=clock.today_iso()))
    done = s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version']).values(
        **{k: v for k, v in after.items() if k != 'id'}))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': 'todo', 'record_id': before['id']})
    return [Touched('todo', after['id'], 'update', before['version'], after['version'], after, before, db='company')]


def pause(s, ctx, schedule, why) -> tuple[dict, list[Touched]]:
    """Paused with the reason; the first pause of an establishment raises one to-do."""
    if schedule['state'] == 'paused':
        return schedule, []
    at = clock.now_iso()
    after = d._touch(s, ctx, dict(schedule, state='paused', paused_reason=why[:500], paused_at=at), at)
    touched = [_write(s, schedule, after)]
    touched += _todo(s, ctx, after, f"delivery-schedule-paused:{schedule['id']}:{at}",
                     f"Email schedule {schedule['name']} is paused",
                     f"{why}. Until a person makes it live again (delivery-schedule live), its runs leave messages "
                     'waiting for a go. To stop it entirely: delivery-schedule pause.')
    return after, touched


def _drop_released_go(s, ctx, batch_id, why) -> list[Touched]:
    """Released messages of a batch, not yet claimed for sending, lose the go and wait for a person's."""
    t, touched = c.delivery_messages, []
    for found in s.company.conn.execute(sa.select(t).where(t.c.batch_id == batch_id, t.c.status == 'ready',
                                                           t.c.go_by.is_not(None))).mappings().all():
        before = dict(found)
        after = d._touch(s, ctx, before)
        details = {'reason': why[:500], 'batch': batch_id}
        d.drop_go(before, after, details)
        d._write_row(s, before, after)
        event = d._event(s, ctx, after['id'], 'held', 'ready', details, at=after['updated_at'])
        touched += [Touched(d.RECORD, after['id'], 'update', before['version'], after['version'], after, before,
                            db='company'),
                    Touched('delivery_event', event['id'], 'create', None, None, event, db='company')]
    return touched


def end_batch(s, ctx, batch_id, why, *, cancel) -> list[Touched]:
    """An open run's batch, when its run failed or paused (``cancel``: its drafts are cancelled and the batch with
    them) or its schedule left live (the batch's go is dropped, so later chunks release without one). Either way its
    released messages lose the go and wait for a person's."""
    if not batch_id:
        return []
    t = c.delivery_batches
    batch = s.company.conn.execute(sa.select(t).where(t.c.id == batch_id)).mappings().first()
    if batch is None or batch['state'] != 'drafting':
        return []
    batch, touched = dict(batch), []
    if cancel:
        from types import SimpleNamespace
        cctx = ctx.model_copy(update={'reason': why[:140]})
        plan = db.prepare_cancel(s, cctx, SimpleNamespace(batch=batch['number'], expected_version=None))
        touched += db.apply_cancel(plan, cctx, s).touched
        batch = dict(s.company.conn.execute(sa.select(t).where(t.c.id == batch_id)).mappings().first())
    if batch['go_by']:
        after = d._touch(s, ctx, dict(batch, go_by=None))
        done = s.company.conn.execute(t.update().where(t.c.id == batch['id'], t.c.version == batch['version']).values(
            **{k: after[k] for k in ('go_by', 'version', 'updated_at', 'updated_by', 'updated_via')}))
        if done.rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': db.RECORD, 'record_id': batch['id']})
        touched.append(Touched(db.RECORD, batch['id'], 'update', batch['version'], after['version'], after, batch,
                               db='company'))
    return touched + _drop_released_go(s, ctx, batch_id, why)


def end_open_run(s, ctx, schedule, why, *, cancel, reason=None) -> list[Touched]:
    """The open run of a schedule: with ``reason`` it fails (and its batch is cancelled); without, it goes on with no
    go."""
    run = open_run(s, schedule['id'])
    if run is None:
        return []
    touched = end_batch(s, ctx, run['batch_id'], why, cancel=cancel)
    values = dict(go_by=None)
    if reason:
        values.update(state='failed', reason=reason, reason_text=why[:500], finished_at=clock.now_iso())
    if reason or run['go_by']:
        touched.append(_update_run(s, ctx, run, **values))
    return touched


def return_to_draft(s, ctx, schedule, why, *, todo=True) -> tuple[dict, list[Touched]]:
    """A live schedule whose confirmed rule changed: back to draft with why, its open run's go dropped, one to-do."""
    if schedule['state'] != 'live':
        return schedule, []
    who = schedule['established_by']
    after = d._touch(s, ctx, dict(schedule, state='draft', established_by=None, established_at=None, confirmed=None,
                                  draft_reason=why[:500]))
    touched = [_write(s, schedule, after)]
    touched += end_open_run(s, ctx, after, why, cancel=False)
    if todo:
        touched += _todo(s, ctx, after, f"delivery-schedule-draft:{schedule['id']}:{after['version']}",
                         f"Email schedule {schedule['name']} is a draft again",
                         f"{why}. Its runs leave messages waiting for a go until a person checks it and makes it live "
                         f'again: delivery-schedule live "{schedule["name"]}" --dry-run.', assignee=who)
    return after, touched


def affected_by_template(s, kind, template_id, *, content, default) -> list[str]:
    """Live schedules a template change would return to draft: those using it whose text changes (``content``), and
    those using the kind's default when the default changes (``default``)."""
    t = c.delivery_schedules
    rows = s.company.conn.execute(sa.select(t.c.name, t.c.template_id).where(
        t.c.state == 'live', t.c.kind == kind).order_by(t.c.name_key)).all()
    current = _template_content(s, dict(template_id=None, kind=kind))
    is_default = current is not None and current[0] == template_id
    return [name for name, used in rows
            if (used == template_id and content) or (used is None and (default or (content and is_default)))]


def recheck_live(s, ctx, kind, why) -> tuple[list[str], list[Touched]]:
    """After a template of ``kind`` changed: each live schedule whose rule no longer matches what was confirmed
    returns to draft with why and one to-do."""
    t, names, touched = c.delivery_schedules, [], []
    for found in s.company.conn.execute(sa.select(t).where(t.c.state == 'live', t.c.kind == kind)
                                        .order_by(t.c.name_key)).mappings().all():
        row = dict(found)
        if rule_fingerprint(s, row) != row['confirmed']:
            _, written = return_to_draft(s, ctx, row, why)
            touched += written
            names.append(row['name'])
    return names, touched


def _go(s, ctx, schedule) -> tuple[str | None, dict, list[Touched]]:
    """The go a starting run's releases carry, decided once at the firing: the establisher's, only when the run is
    drafted as the establisher (the host's tick, which acts as ``run_as``, the establisher of a live schedule; or the
    establisher's own Run now), while the schedule is live, its rule is still the one confirmed, and they still hold
    the authority (R187: automate.delivery and delivery.send). Anyone else's run drafts with no go."""
    if schedule['mode'] != 'send' or schedule['state'] != 'live' or s.actor.id != schedule['established_by']:
        return None, schedule, []
    if rule_fingerprint(s, schedule) != schedule['confirmed']:
        after, touched = return_to_draft(s, ctx, schedule, 'its rule or template changed since it was confirmed')
        return None, after, touched
    from bookflow.company import automation
    why = automation.firing_check(s, KIND, schedule) or authority_lost(s, schedule['established_by'])
    if why is None:
        return schedule['established_by'], schedule, []
    after, touched = pause(s, ctx, schedule, why)
    return None, after, touched


def _counts(s, run, batch_out: bm.BatchOutput) -> dict:
    return dict(planned=batch_out.planned, drafted=batch_out.drafted, released=batch_out.released,
                skipped_count=len(batch_out.skipped))


def _update_run(s, ctx, run, **values) -> Touched:
    r = c.delivery_schedule_runs
    after = d._touch(s, ctx, dict(run, **values))
    done = s.company.conn.execute(r.update().where(r.c.id == run['id'], r.c.version == run['version'])
                                  .values(**{k: v for k, v in after.items() if k != 'id'}))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': RUN_RECORD, 'record_id': run['id']})
    return Touched(RUN_RECORD, after['id'], 'update', run['version'], after['version'], after, run, db='company')


def draft_step(s, ctx, schedule, run) -> tuple[dict, dict, list[Touched], list[str]]:
    """One chunk of a run: begin its batch (deciding its go) or continue it."""
    touched, warnings = [], []
    if run['batch_id'] is None:
        go_by, schedule, paused = _go(s, ctx, schedule)
        touched += paused
        inp = batch_input(s, schedule, run['slot_date'])
        plan = db.prepare_batch(s, ctx, inp)
        if plan.data.get('batch'):
            plan.data['batch'].update(go_by=go_by, schedule_run_id=run['id'])
        applied = db.apply_batch(plan, ctx, s)
        out = applied.output
        touched += applied.touched
        values = dict(_counts(s, run, out), go_by=go_by, mode=schedule['mode'], date_from=inp.date_from,
                      date_to=inp.date_to, batch_id=out.batch_id)
    else:
        number = s.company.conn.execute(sa.select(c.delivery_batches.c.number).where(
            c.delivery_batches.c.id == run['batch_id'])).scalar()
        plan = db.prepare_batch(s, ctx, bm.BatchInput(batch=number))
        applied = db.apply_batch(plan, ctx, s)
        out = applied.output
        touched += applied.touched
        values = _counts(s, run, out)
    warnings += out.warnings
    if out.state != 'drafting':
        values.update(state='done', finished_at=clock.now_iso())
    touched.append(_update_run(s, ctx, run, **values))
    run = touched[-1].after
    if run['state'] == 'done' and run['mode'] == 'send' and not run['go_by'] and run['released']:
        touched += _standing_todo(
            s, ctx, schedule, f"Emails from {schedule['name']} wait for your go",
            f"The {run['slot_date']} run of {schedule['name']} released {run['released']} message(s) (batch "
            f"{out.number}); none leaves until a person approves it: delivery approve --batch {out.number}, or the "
            "Outbox's Waiting for your go. This to-do is updated by each such run.")
    return schedule, run, touched, warnings


def step(s, ctx, schedule, today, upto=None) -> tuple[dict, list[dict], list[Touched], list[str]]:
    """What is due for one schedule now: claim and skip slots, then draft one chunk of its open run."""
    touched, warnings, ran = [], [], []
    upto = upto or today
    if schedule['active'] and schedule['next_date'] and schedule['next_date'] <= upto:
        schedule, written, made = claim_due(s, ctx, schedule, today, upto)
        touched += written
        ran += [r for r in made if r['state'] == 'skipped']
    run = open_run(s, schedule['id']) if schedule['active'] else None
    if run is not None:
        schedule, run, written, more = draft_step(s, ctx, schedule, run)
        touched += written
        warnings += more
        ran.append(run)
    return schedule, ran, touched, warnings


def prepare_run(s, ctx, inp) -> Plan:
    schedule = resolve(s, inp.schedule)
    today, hhmm = moment(s)
    upto = through(schedule, today, hhmm)
    if not schedule['active']:
        return Plan(output(s, schedule, warnings=['It is stopped: resume it first.']), dict(idle=True))
    due = due_slots(schedule, upto, 1) if schedule['next_date'] else []
    if not due and open_run(s, schedule['id']) is None:
        if is_finished(schedule):
            text = 'It has run out.'
        else:
            text = f"Nothing is due: the next slot is {schedule['next_date']} from {schedule['run_time']}."
        return Plan(output(s, schedule, warnings=[text]), dict(idle=True))
    if s.dry_run:
        slot = first_run(schedule, today)
        return Plan(output(s, schedule, warnings=[f'Would run the slot of {slot} (or continue its open run).']),
                    dict(idle=True))
    return Plan(output(s, schedule), dict(schedule=schedule, today=today, upto=upto))


def apply_run(plan, ctx, s) -> Applied:
    if plan.data.get('idle'):
        return Applied(plan.preview, [], 'nothing due')
    schedule, ran, touched, warnings = step(s, ctx, plan.data['schedule'], plan.data['today'], plan.data['upto'])
    out = output(s, schedule, warnings=warnings, ran=[_run_out(s, r) for r in ran])
    return Applied(out, touched, f"delivery schedule {schedule['name']}: {len(ran)} run(s)")


# ---------------------------------------------------------------- the host's tick

_DUE = "active = 1 AND next_date <= :today AND (next_date < :today OR run_time <= :hhmm)"


def has_due(conn, today: str, hhmm: str = '23:59') -> bool:
    """One statement: is any schedule due (its slot's day come, and on the day its run time), or any run still
    drafting? A company with no schedules pays only this."""
    return bool(conn.execute(sa.text(
        f"SELECT EXISTS (SELECT 1 FROM delivery_schedules WHERE {_DUE}) OR EXISTS "
        "(SELECT 1 FROM delivery_schedule_runs r JOIN delivery_schedules x ON x.id = r.schedule_id "
        "WHERE r.state = 'drafting' AND x.active = 1)"), dict(today=today, hhmm=hhmm)).scalar())


def _due(s, today, hhmm) -> list[dict]:
    t, r = c.delivery_schedules, c.delivery_schedule_runs
    drafting = sa.select(r.c.schedule_id).where(r.c.state == 'drafting')
    return [dict(x) for x in s.company.conn.execute(sa.select(t).where(sa.or_(
        sa.text(_DUE).bindparams(today=today, hhmm=hhmm), sa.and_(t.c.active.is_(True), t.c.id.in_(drafting))))
        .order_by(t.c.name_key)).mappings()]


def _system_stop(s, system_id, schedule, why, today, upto, *, reason='no_authority', stop=True):
    """As the host, with the reason: skip what is due and fail an open run, cancelling its batch. ``stop`` (the actor
    lost the authority, or the run failed) pauses it too, with one to-do; a passing refusal (``unavailable``) leaves
    it running from its next slot."""
    from bookflow.company.scheduler import _act_as, context
    _act_as(s, system_id)
    ctx = context().model_copy(update={'actor_id': system_id, 'reason': f"On schedule: {schedule['name']}"[:140]})
    with s.commits.operation('delivery.tick', s.hub, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            touched = []
            if stop:
                schedule, touched = pause(s, ctx, schedule, why)
            slots = due_slots(schedule, upto, SKIPS_PER_STEP)
            for slot in slots:
                touched += _skip(s, ctx, schedule, slot, reason, why[:500])
            if slots:
                nxt = _after(schedule, slots[-1])
                after = d._touch(s, ctx, dict(schedule, next_date=None if _finished(
                    nxt, schedule, schedule['remaining_runs']) else nxt))
                touched.append(_write(s, schedule, after))
            touched += end_open_run(s, ctx, schedule, why, cancel=True, reason=reason)
            _audit(s, ctx, f"schedule {schedule['name']} {'paused' if stop else 'skipped a slot'}: {why}", touched)
            s.commits.commit(s.company, 'delivery.tick')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return {'paused': why} if stop else {'skipped': why}


def _audit(s, ctx, summary, touched):
    from bookflow.core.audit import write_event_to
    if touched:
        write_event_to(s.company, ctx, 'delivery-schedule run', summary, touched, actor_id=s.actor.id,
                       actor_kind=s.actor.kind, directive_code=s.directive_code)


def _pending_slot(schedule, upto):
    slots = due_slots(schedule, upto)
    return slots[-1] if slots else None


def _refusal(e) -> tuple[str, bool]:
    """How a refused step is recorded: (reason, stop). A passing refusal (the writer busy, a record moved meanwhile,
    an accountant's copy out) skips the slot and the schedule goes on; a lost authority or anything else pauses it."""
    if isinstance(e, BookflowError):
        if e.code in TRANSIENT or 'accountant' in str((e.details or {}).get('reason') or ''):
            return 'unavailable', False
        if e.code in NO_AUTHORITY:
            return 'no_authority', True
    return 'failed', True


def tick(s, system_id, now) -> dict | None:
    """The scheduler's step for one company, on the host's writer. None, after one statement, when nothing is due.

    Each due schedule takes one step as its ``run_as`` through ``delivery-schedule run`` (that actor's authority
    checked afresh; interface system, client scheduler; "On schedule: <name> for <slot>"). A slot is due from the
    schedule's run time, company-local."""
    local = _local(s.company_tz, now)
    today, hhmm = local.date().isoformat(), local.strftime('%H:%M')
    if not has_due(s.company.conn, today, hhmm):
        return None
    from bookflow.company import scheduler
    from bookflow.core import registry
    from bookflow.core.context import ActorKind
    from bookflow.core.dispatch import run_in_session
    cmd = registry.get('delivery-schedule run')
    out = {}
    for schedule in _due(s, today, hhmm):
        name = schedule['name']
        upto = through(schedule, today, hhmm)
        epoch = schedule['run_as_epoch'] if schedule['run_as_epoch'] is not None else scheduler._NOT_RECORDED
        why, epoch = scheduler._cannot_act(s, schedule['run_as'], schedule['run_as_for'], epoch,
                                           what='set what this schedule drafts')
        try:
            if why is not None:
                out[name] = _system_stop(s, system_id, schedule, f'{scheduler._name(s, schedule["run_as"])} cannot '
                                         f'run it: {why}', today, upto)
                continue
            run = open_run(s, schedule['id'])
            slot = run['slot_date'] if run else _pending_slot(schedule, upto)
            scheduler._act_as(s, schedule['run_as'], schedule['run_as_for'], epoch)
            cited = {}
            if schedule['state'] == 'live' and schedule['established_by'] == schedule['run_as']:
                # A live send schedule's writes cite the rule and its establisher (R187 §3: audit "On rule").
                from bookflow.company import automation
                cited = {'source_ref': automation.source_ref(KIND.kind, schedule['id'], schedule['established_by'])}
            ctx = scheduler.context().model_copy(update={
                'actor_id': schedule['run_as'], 'actor_kind': ActorKind(s.actor.kind),
                'on_behalf_of': schedule['run_as_for'],
                'reason': (f'On schedule: {name} for {slot}' if slot else f'On schedule: {name}')[:140], **cited})
            token = _TICK_TIME.set(hhmm)
            try:
                with clock.as_of_day(today):  # the tick's own day and time, which the command reads as now
                    result = run_in_session(cmd, cmd.input_model.model_validate({'schedule': schedule['id']}), ctx, s)
            finally:
                _TICK_TIME.reset(token)
            out[name] = {'ran': [{k: r.get(k) for k in ('slot_date', 'state', 'drafted', 'released', 'reason')}
                                 for r in result.get('ran', [])]}
        except Exception as e:  # noqa: BLE001 - every failure is recorded on the schedule; none retries every tick
            if s.company is not None and s.company.write_transaction:
                s.company.raw.rollback()
            reason, stop = _refusal(e)
            code = e.code if isinstance(e, BookflowError) else type(e).__name__
            text = (e.message or e.code) if isinstance(e, BookflowError) else str(e) or type(e).__name__
            who = scheduler._name(s, schedule['run_as'])
            words = f"{who} cannot run it ({code}): {text}" if stop else f"refused for now ({code}): {text}"
            try:
                out[name] = _system_stop(s, system_id, resolve(s, schedule['id']), words[:500], today, upto,
                                         reason=reason, stop=stop)
            except Exception as again:  # noqa: BLE001 - recorded, never fatal to the host
                out[name] = scheduler._failure(again)
    return out


# ---------------------------------------------------------------- as an automation (R187 increment 2)

CAPABILITY = 'automate.delivery'


def _automations(s, state=None) -> list[dict]:
    t = c.delivery_schedules
    q = sa.select(t).where(t.c.mode == 'send').order_by(t.c.name_key)
    if state:
        q = q.where(t.c.state == state)
    return [dict(r) for r in s.company.conn.execute(q).mappings()]


def _view(s, row) -> dict:
    return dict(rule_id=row['id'], name=row['name'], state=row['state'], established_by=row['established_by'],
                established_for=row['run_as_for'] if row['established_by'] and row['run_as'] == row['established_by']
                else None, established_at=row['established_at'], paused_reason=row['paused_reason'],
                draft_reason=row['draft_reason'],
                what=f"Emails {selection_text(s, row)}, {cadence(row)[:1].lower()}{cadence(row)[1:]}")


def pause_rule(s, ctx, schedule, why) -> tuple[dict, list[Touched]]:
    """`automation pause`, or a firing that found the establisher without the authority: paused with the reason and
    one to-do; an open run's releases lose the go."""
    if schedule['state'] != 'live':
        return schedule, []
    after, touched = pause(s, ctx, schedule, why)
    return after, touched + end_open_run(s, ctx, after, why, cancel=False)


def _register():
    from bookflow.company import automation
    return automation.register(automation.Kind(
        'delivery', CAPABILITY, 'delivery schedule', RECORD, 'delivery-schedule show', 'delivery-schedule update',
        'delivery-schedule live, or delivery-schedule update with confirm', _automations,
        lambda s, selector: resolve(s, selector), _view, pause_rule))


KIND = _register()
