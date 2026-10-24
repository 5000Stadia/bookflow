"""What the host does for a company without a person pressing a button (R111).

``run_company`` is one tick of the scheduler for one company. The host calls it from its timer
thread, through its single writer, at most once a minute (``Host.schedule_now``); tests call
``Host.schedule_now(now=...)`` with an injected clock. It does three things, each idempotent:

**Automatic memorized entries.** Every slot of a memorized transaction (or group) whose mode is
``enter_automatically`` and whose due date -- the slot date less its days in advance -- has
arrived in the company's own timezone is entered, through exactly the path `memorized process`
takes: the slots are claimed and committed first (one occurrence per template and slot, a
storage fact), then each is replayed through the ordinary command, then the outcomes are
recorded. A restart, a second tick or a person running `memorized process` meanwhile cannot
enter a slot twice; a host killed between the post and the record adopts the posted document on
its next tick. ``remind`` templates get a waiting occurrence a person sees on the Reminders list.

*Whose authority.* An automatic entry runs with **no more authority than the author of the exact
revision it replays**: the person or agent who saved that revision, with that agent's principal
(the ``on_behalf_of`` of the save) when the author is an agent. A group member's entry uses its
own template's author, never the group's. Before an entry replays, a waiting occurrence is pointed
at its template's current revision -- the anchor enters the memorized transaction as it is now --
so content and authority always come from the same revision. Each entry is authorized exactly as
if its author had entered it, the author's grants and denies read afresh. An author who is
deactivated, suspended, no longer a member, or without the right makes a *blocked* occurrence
(E_PERMISSION, saying which) and a to-do for an active administrator of the company; a cause in
the books (a closed period, an inactive account) makes a blocked occurrence and a to-do for the
author. Blocked occurrences are never retried by the scheduler: a person runs `memorized retry`
or `memorized skip`.

*One person decides both.* An occurrence is entered unattended only when the author of the
revision it replays is the same principal who set the schedule that brings it due -- the same
person, or the same agent acting for the same person. A template's schedule is part of its
revision, so its author always set it; a group's schedule, mode and status are recorded with
whoever set them (``scheduled_group_authority``, with an agent's epoch). When the two differ --
content added to someone else's scheduled group, a group switched on or re-timed by someone else,
a group whose schedule was set before this upgrade (who set it is unknown) -- the occurrence waits
for a person exactly as a reminder does (``scheduled_holds``, shown on Reminders with both names)
and posts only when a person enters it, as that person. A slot let in by a widening of
``scheduler_catch_up_days`` (whatever its size) names who widened it in its reason, and waits for a
person when that widening was an agent's or anyone's but the slot's author's.

*How it is recorded.* The posting is the author's write: its audit event names the author as the
actor (an agent stays an agent, so `report entries-to-review` and `audit list --actor/--principal`
see it exactly as a direct write), ``on_behalf_of`` is the agent's principal (null for a person),
the interface is ``system`` and the client ``scheduler``, and the reason is the schedule
("On schedule: <name> (<frequency>) for <slot>"). The bookkeeping around it -- claiming the slots,
recording outcomes, assigning to-dos, generating notices -- is the host's own: the system principal,
interface ``system``, client ``scheduler``.

*Catch-up.* A host that was off for a while owes every slot it missed, each at its own accounting
date, as the anchor enters every missed automatic transaction when the books are next opened.
The company preference ``scheduler_catch_up_days`` limits that: a missed slot older than that
many days is left waiting for a person instead of entered. Left empty, the limit is a year
(``DEFAULT_CATCH_UP_DAYS``), so a template saved today with a start date years back waits for a
person rather than posting years of entries unattended.

*A fair share of the writer.* Every company shares the host's one writer, so one tick does at most
``ENTRIES_PER_TICK`` (25) entries and lists at most ``SLOTS_PER_TICK`` (200) slots per company,
automatic work before slots that are only listed; a backlog finishes over the following ticks.

**Overdue notices.** On the company's notice schedule, one notice per overdue customer into the
``notice`` queue (``company/notices.py``). Generated, never sent. They scan every receivable, so
the host looks at them at most once an hour per company (``NOTICE_INTERVAL``), not every tick.

Nothing here sends anything off the machine.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core.context import ActorKind

log = logging.getLogger("bookflow.scheduler")

CLIENT = 'scheduler'
PROCESS = 'memorized process'


def context():
    from bookflow.core.context import Context, Interface
    return Context.new(Interface.system, CLIENT)


AUTHORITY_CODES = ('E_PERMISSION', 'E_UNAUTHENTICATED')
# One tick's work for one company: at most ENTRIES_PER_TICK entries posted, and at most
# SLOTS_PER_TICK slots listed, so a long catch-up shares the host's single writer with everyone
# else rather than holding it; the rest follows on the next tick, a minute later.
ENTRIES_PER_TICK = 25
SLOTS_PER_TICK = 200
# With no catch-up preference, a missed slot more than a year old is still left for a person: a
# template saved today with a start date years back must not post years of entries unattended.
DEFAULT_CATCH_UP_DAYS = 365
NOTICE_INTERVAL = timedelta(hours=1)


def author_of(s, revision_id: str) -> tuple[str | None, str | None]:
    """Who wrote this exact revision, and for whom: its author and that write's on_behalf_of.

    An automatic entry runs with no more authority than the author of the revision it replays,
    and an agent author's write stays that agent's write, for the person it acted for then.
    """
    r, e = c.memorized_transaction_revisions, c.audit_events
    row = s.company.conn.execute(sa.select(r.c.created_by, e.c.on_behalf_of).select_from(r)
                                 .outerjoin(e, e.c.id == r.c.audit_event_id).where(r.c.id == revision_id)).first()
    return (row[0], row[1]) if row else (None, None)


def _act_as(s, user_id: str, principal: str | None = None, epoch: int | None = None) -> None:
    """Load ``user_id`` (with its memberships) as the session's actor, its authority read afresh."""
    from bookflow.core.dispatch import _load_actor_by_id
    from bookflow.core.identity_admin_binding import ScheduledBinding
    _load_actor_by_id(s, user_id)
    s.credential = ScheduledBinding(user_id, principal, epoch)
    s.directive_code = None


_NOT_RECORDED = object()


def _cannot_act(s, user_id: str | None, principal: str | None = None, saved=_NOT_RECORDED,
                what: str = 'saved this memorized transaction') -> tuple[str | None, int | None]:
    """Why this person or agent can no longer lend its authority to an entry (or None), and its epoch.

    Checked here, before anything is dispatched, in every permission mode -- a root still in the
    legacy mode never consults a credential at all -- and again by ``ScheduledBinding`` inside
    the policy check. An agent gets exactly a bearer token's checks: assigned to the person it
    acted for (who is active), not suspended, and the authority epoch it had when it ``saved``
    (``_NOT_RECORDED`` when nothing recorded it: an agent's save from before this upgrade).
    """
    from bookflow.hub import credentials, schema as h
    user = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == user_id)).mappings().first() if user_id else None
    if user is None:
        return 'is not a user of this installation', None
    name = user['display_name'] or user['username']
    if not user['active']:
        return f'{name} has been deactivated', None
    if user['kind'] != 'agent':
        return None, None
    suspended = s.hub.conn.execute(sa.select(h.agent_authority.c.suspended_at).where(
        h.agent_authority.c.agent_user_id == user_id)).scalar_one_or_none()
    if suspended:
        return f'{name} (an agent) is suspended', None
    eligible, epoch = credentials._binding(s.hub, user_id, principal)
    if not eligible:
        return f'{name} (an agent) no longer acts for the person it {what} for', None
    if saved is _NOT_RECORDED:
        return f'{name} (an agent) {what} before this upgrade; re-save it to schedule it', None
    if saved != epoch:
        return f'{name} (an agent) holds different authority than when it {what}', None
    return None, epoch


def group_setter(s, occurrence):
    """Who set the schedule of the group that drives this occurrence: (setter, principal, saved epoch,
    group), or None when no scheduled group drives it."""
    from bookflow.company import memorized as service
    if not occurrence.get('group_run_id'):
        return None
    run = service._one(s, c.memorized_group_runs, c.memorized_group_runs.c.id == occurrence['group_run_id'])
    group = service._one(s, c.memorized_groups, c.memorized_groups.c.id == run['group_id']) if run else None
    if group is None or group['frequency'] == 'never':
        return None
    row = s.company.conn.execute(sa.select(c.scheduled_group_authority).where(
        c.scheduled_group_authority.c.group_id == group['id'])).mappings().first()
    if row is None:   # set before this upgrade: who set it is unknown, so nobody's decision can be assumed
        return None, None, _NOT_RECORDED, group
    return row['set_by'], row['principal_id'], row['authority_epoch'], group


def _blocked(code, message, **details):
    from bookflow.core.errors import BookflowError
    return BookflowError(code, message=message[:500], details=details)


def _name(s, user_id):
    from bookflow.hub import schema as h
    row = s.hub.conn.execute(sa.select(h.users.c.display_name, h.users.c.username).where(h.users.c.id == user_id)).first()
    return (row[0] or row[1]) if row else 'someone'


def _hold(s, occurrence, author, principal, widened) -> str | None:
    """Why this occurrence must wait for a person rather than be entered unattended, or None.

    It is entered unattended only when one principal decided both what it posts and when: the
    author of the revision it replays (an agent counted together with the person it acted for)
    also set the schedule that brings it due. Content added to someone else's scheduled group, a
    group switched on or re-timed by someone else, or a slot let in by a catch-up window someone
    else (or any agent) widened, waits for a person, as a reminder does, and posts only when a
    person enters it -- as that person.
    """
    setter = group_setter(s, occurrence)
    if setter is not None:
        set_by, set_principal, set_saved, group = setter
        if set_by is None:
            return "schedule set before this upgrade; open the group and save its schedule to let it run"
        if (set_by, set_principal) != (author, principal):
            return (f"memorized by {_name(s, author)}, scheduled by {_name(s, set_by)}; "
                    "waiting for a person to enter it")
        why, _ = _cannot_act(s, set_by, set_principal, set_saved, what="set this group's schedule")
        if why is not None:
            return f"whoever set its group's schedule, {why}; waiting for a person to enter it"
    if widened and occurrence['slot_date'] < widened['before'] and (
            widened['kind'] == 'agent' or widened['by_id'] != author):
        return (f"catch-up widened to {widened['days']} days by {widened['by']}, not by its author; "
                "waiting for a person to enter it")
    return None


def _enter(s, occurrence, as_of, widened=None):
    """Enter one claimed occurrence as the author of the revision it replays, with that author's
    full authority (the ordinary authorization, input checks and plan) -- or say why it waits."""
    from bookflow.company import memorized as service, memorized_entry
    from bookflow.core.context import ActorKind
    recovered = memorized_entry._recover(s, occurrence)
    if recovered is not None:
        return {'occurrence': occurrence, 'output': recovered['output'], 'recovered': recovered['source']}
    template = service._one(s, c.memorized_transactions, c.memorized_transactions.c.id == occurrence['template_id'])
    author, principal = author_of(s, occurrence['revision_id'])
    why, epoch = _cannot_act(s, author, principal, _revision_saved_or_missing(s, occurrence['revision_id']))
    if why is not None:
        return {'occurrence': occurrence, 'error': _blocked(
            'E_PERMISSION', f"Not entered: the author of this memorized transaction, {why}, so it no longer "
                            "carries anyone's authority. A person must review it.", reason='author_inactive')}
    held = _hold(s, occurrence, author, principal, widened)
    if held is not None:
        return {'occurrence': occurrence, 'hold': held}
    frequency = template['frequency']
    setter = group_setter(s, occurrence)
    if setter is not None:
        frequency = setter[3]['frequency']
    note = ''
    if widened and occurrence['slot_date'] < widened['before']:
        note = f"; catch-up widened to {widened['days']} days by {widened['by']}"
    _act_as(s, author, principal, epoch)
    ctx = context().model_copy(update={'actor_id': author, 'actor_kind': ActorKind(s.actor.kind),
                                       'on_behalf_of': principal})
    reason = f"On schedule: {template['name']} ({frequency.replace('_', ' ')}) for {occurrence['slot_date']}{note}"
    result = memorized_entry._enter_one(s, ctx, occurrence, reason=reason)
    error = result.get('error')
    if error is not None and error.code == 'E_COMPANY_NOT_FOUND':
        result['error'] = _blocked('E_PERMISSION', f'Not entered: its author, {_name(s, author)}, '
                                                   'is no longer a member of this company.', reason='author_not_member')
    return result


def _revision_saved_or_missing(s, revision_id):
    row = s.company.conn.execute(sa.select(c.scheduled_revision_authority.c.authority_epoch).where(
        c.scheduled_revision_authority.c.revision_id == revision_id)).first()
    return _NOT_RECORDED if row is None else row[0]


def _widened(s, catch_up, as_of):
    """The latest change to the catch-up window, when it widened it: by whom, as what, from what.

    Read from the company audit trail, where every `company update` records the change. Whatever the
    size, a widening lets in the slots older than the window it replaced (a value left empty counts
    as the default year); those entries name it in their reason, and wait for a person when the
    widening was an agent's or anyone's but their author's.
    """
    from bookflow.core.audit import decode_snapshot
    if catch_up is None:
        return None
    e, n = c.audit_events, c.audit_entries
    # Each `company update` keeps the company row as it stood after it: the change is the newest event
    # whose value differs from the one before it.
    seen = []
    for actor_id, kind, at, after in s.company.conn.execute(
            sa.select(e.c.actor_id, e.c.actor_kind, e.c.at, n.c.after).join(n, n.c.event_id == e.c.id)
            .where(e.c.command == 'company update', n.c.record_type == 'company_info').order_by(e.c.seq.desc())):
        new = decode_snapshot(after) or {}
        if 'scheduler_catch_up_days' in new:
            seen.append((actor_id, kind, at, new['scheduler_catch_up_days']))
    for index, (actor_id, kind, at, value) in enumerate(seen):
        previous = seen[index + 1][3] if index + 1 < len(seen) else None
        if value != previous:
            if value != catch_up:
                return None
            previous = DEFAULT_CATCH_UP_DAYS if previous is None else previous
            if catch_up <= previous:
                return None
            return {'days': catch_up, 'by': _name(s, actor_id), 'by_id': actor_id, 'kind': kind, 'at': at,
                    'before': (date.fromisoformat(as_of) - timedelta(days=previous)).isoformat()}
    return None


def _mode(s, item):
    group = item.get('group')
    return group['mode'] if group is not None and group.get('frequency') != 'never' else item['template']['mode']


def _batch(found):
    """This tick's share: whole streams' prefixes, automatic ones first, within the tick's bounds.

    A stream is one template's (or one scheduled group's) slots, oldest first; a group slot is one
    unit with all its members. Streams with something to enter go first, so slots that are only
    being listed -- reminders, or missed slots left for a person -- never starve an entry, and each
    stream contributes a prefix, which is what the claim's advance of its schedule assumes.
    """
    units: dict = {}
    for item in found:
        key = (item['group_run'], item['slot']) if item.get('group_run') and item.get('advance') else id(item)
        units.setdefault(key, []).append(item)
    streams: dict = {}
    for unit in units.values():
        first = unit[0]
        stream = ('group', first['group_run']) if first.get('group_run') and first.get('advance') \
            else ('template', first['template']['id'], first.get('occurrence') is not None)
        streams.setdefault(stream, []).append(unit)
    ordered = sorted(streams.values(), key=lambda units: not any(i['enter'] for u in units for i in u))
    taken, entries = [], 0
    for stream in ordered:
        for unit in stream:
            entering = sum(1 for item in unit if item['enter'])
            if taken and (entries + entering > ENTRIES_PER_TICK or len(taken) + len(unit) > SLOTS_PER_TICK):
                break
            taken.extend(unit)
            entries += entering
    return taken


def enter_due(s, system_id: str, as_of: str) -> dict[str, Any]:
    """Claim, enter and record every automatic slot due by ``as_of``; list the reminder ones."""
    from bookflow.company import memorized_entry
    from bookflow.core.audit import write_event_to
    from bookflow.core.dispatch import _upsert_principals
    from bookflow.core.ids import new_id
    info = s.company.conn.execute(sa.select(c.company_info.c.scheduler_catch_up_days)).mappings().one()
    catch_up = info['scheduler_catch_up_days']
    widened = _widened(s, catch_up, as_of)
    if catch_up is None:
        catch_up = DEFAULT_CATCH_UP_DAYS
    cutoff = (date.fromisoformat(as_of) - timedelta(days=catch_up)).isoformat()
    summary = {'entered': 0, 'blocked': 0, 'waiting': 0, 'left_for_a_person': 0}

    held = {row[0] for row in s.company.conn.execute(sa.select(c.scheduled_holds.c.occurrence_id))}

    def keep_waiting(occurrence, mode):
        # Only waiting slots this run will enter: a reminder slot, one older than the catch-up window,
        # one held for a person, or one already blocked is on the Reminders list and must never crowd
        # out the rest.
        return mode == 'enter_automatically' and occurrence['slot_date'] >= cutoff and occurrence['id'] not in held

    found = memorized_entry._due_work(s, SimpleNamespace(as_of=as_of, limit=10 * SLOTS_PER_TICK, memorized=None),
                                      as_of, keep_waiting=keep_waiting)
    for item in found:
        if item['enter'] and item['slot'] < cutoff:
            item['enter'] = False
    work = _batch(found)
    summary['left_for_a_person'] = sum(1 for item in work if not item['enter'] and item.get('occurrence') is None
                                       and _mode(s, item) == 'enter_automatically')
    if not work:
        return summary
    _act_as(s, system_id)
    ctx = context().model_copy(update={'actor_id': system_id, 'actor_kind': ActorKind.system,
                                       'reason': f'On schedule: memorized transactions due by {as_of}'})
    data = {'verb': 'process', 'as_of': as_of, 'warnings': [], 'work': work}
    with s.commits.operation('memorized.enter', s.hub, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            _upsert_principals(s, ctx)
            claimed = memorized_entry._claim(s, ctx, data, PROCESS)
            for occurrence in claimed:
                # A blocked slot waits for a person (`memorized retry`); the scheduler never tries it again,
                # even when the same slot comes round in a group run.
                if occurrence['status'] == 'blocked':
                    occurrence['_enter'] = False
            memorized_entry.rebase(s, ctx, claimed, PROCESS)
            s.commits.commit(s.company, 'memorized.enter')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
        results, holds = [], []
        for occurrence in claimed:
            if occurrence['_enter']:
                outcome = _enter(s, occurrence, as_of, widened)
                (holds if 'hold' in outcome else results).append(outcome)
        _act_as(s, system_id)
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            rows, _ = memorized_entry._record(s, ctx, PROCESS, claimed, results)
            if holds:
                _write_holds(s, ctx, holds)
                summary['held'] = len(holds)
            s.commits.commit(s.company, 'memorized.enter')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    acted = {result['occurrence']['id'] for result in results}
    for row in rows:
        if row['id'] in acted:
            summary['entered' if row['status'] == 'entered' else 'blocked'] += 1
        elif row['status'] == 'pending':
            summary['waiting'] += 1
    return summary


def _write_holds(s, ctx, holds):
    """Record, once each, the slots that wait for a person and why (shown on Reminders)."""
    from bookflow.core.audit import write_event_to
    from bookflow.core.clock import now_iso
    from bookflow.core.ids import new_id
    from bookflow.core.registry import Touched
    at, rows = now_iso(), []
    for hold in holds:
        rows.append(dict(occurrence_id=hold['occurrence']['id'], reason=hold['hold'][:400], created_at=at))
    s.company.conn.execute(c.scheduled_holds.insert(), rows)
    write_event_to(s.company, ctx, PROCESS, f'{len(rows)} automatic entr{"y waits" if len(rows) == 1 else "ies wait"} for a person',
                   [Touched('scheduled_hold', row['occurrence_id'], 'create', None, 1, row, db='company') for row in rows],
                   actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=new_id())


def _active_admin(s) -> str | None:
    """An active person who administers this company and can open it: who reviews what no author can carry.

    A person holding the owner or admin role on the company or its organization; failing that, a hub
    administrator who is a member of it. Never someone who cannot open the company.
    """
    from bookflow.hub import schema as h
    u, m, row = h.users, h.memberships, s.company_row
    scope = sa.or_(sa.and_(m.c.scope_type == 'company', m.c.scope_id == row['id']),
                   sa.and_(m.c.scope_type == 'organization', m.c.scope_id == row['organization_id']))
    base = sa.select(u.c.id).join(m, m.c.user_id == u.c.id).where(
        u.c.active.is_(True), u.c.kind == 'human', m.c.revoked_at.is_(None), scope)
    rank = sa.case((m.c.role == 'owner', 0), else_=1)
    found = s.hub.conn.execute(base.where(m.c.role.in_(('owner', 'admin'))).order_by(rank, u.c.created_at, u.c.id)
                               .limit(1)).scalar_one_or_none()
    return found or s.hub.conn.execute(base.where(u.c.hub_admin.is_(True)).order_by(u.c.created_at, u.c.id)
                                       .limit(1)).scalar_one_or_none()


def assign_blocked(s, system_id: str, as_of: str) -> int:
    """A to-do for every blocked occurrence that does not have one yet.

    It goes to the author of the revision when the author can still act and the cause is in the
    books (a closed period, an inactive account); when the cause is the author's own authority --
    deactivated, suspended, no longer a member, or without the right -- it goes to an active
    administrator of the company instead, never to the author who cannot act on it.
    """
    from bookflow.company import todos
    from bookflow.core.audit import write_event_to
    from bookflow.core.ids import new_id
    from bookflow.core.registry import Touched
    from bookflow.hub import schema as h
    o, t = c.memorized_occurrences, c.memorized_transactions
    blocked = s.company.conn.execute(
        sa.select(o.c.id, o.c.slot_date, o.c.error_code, o.c.error_message, o.c.template_id, o.c.revision_id, t.c.name)
        .join(t, t.c.id == o.c.template_id).where(o.c.status == 'blocked')
        .outerjoin(c.todos, c.todos.c.source_ref == sa.literal('memorized-occurrence:') + o.c.id)
        .where(c.todos.c.id.is_(None))).mappings().all()
    if not blocked:
        return 0
    _act_as(s, system_id)
    ctx = context().model_copy(update={'actor_id': system_id, 'actor_kind': ActorKind.system,
                                       'reason': 'On schedule: an automatic entry needs a person'})
    rows = []
    for occ in blocked:
        author, principal = author_of(s, occ['revision_id'])
        names = s.hub.conn.execute(sa.select(h.users.c.display_name, h.users.c.username).where(h.users.c.id == author)).first()
        author_name = (names[0] or names[1]) if names else 'its author'
        authority = occ['error_code'] in AUTHORITY_CODES or _cannot_act(s, author, principal, _revision_saved_or_missing(s, occ['revision_id']))[0] is not None
        if authority:
            assignee = _active_admin(s)
            title = f"Review {occ['name']} for {occ['slot_date']}: its author can no longer post it"
            notes = (f"The scheduler did not enter {occ['name']} for {occ['slot_date']}: it runs with the authority of "
                     f"whoever saved it ({author_name}) and of whoever scheduled its group, and that authority is gone:\n"
                     f"{occ['error_code']}: {occ['error_message']}\n\nA retry enters the memorized transaction as it is "
                     f"now, in the name of whoever retries it. Read it first (`memorized show {occ['template_id']}`) and "
                     f"correct it if it is not what the company should post; then `memorized retry {occ['id']}`. If it "
                     f"should not be entered, `memorized skip {occ['id']}`.")
        else:
            assignee = author
            title = f"Enter {occ['name']} for {occ['slot_date']}: the scheduler could not"
            notes = (f"{occ['error_code']}: {occ['error_message']}\n\nFix the cause, then `memorized retry {occ['id']}`"
                     f" (or `memorized skip {occ['id']}` if it should not be entered).")
        row = todos.scheduler_todo(s, ctx, source_ref='memorized-occurrence:' + occ['id'], title=title, notes=notes,
                                   assignee_id=assignee, due_date=as_of, link_type='memorized', link_id=occ['template_id'])
        if row is not None:
            rows.append(row)
    if not rows:
        return 0
    s.company.raw.execute('BEGIN IMMEDIATE')
    try:
        from bookflow.core.dispatch import _upsert_principals
        from bookflow.company.info import upsert_principal
        _upsert_principals(s, ctx)
        for assignee in {row['assignee_id'] for row in rows if row['assignee_id']}:
            user = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == assignee)).mappings().first()
            if user:
                upsert_principal(s.company, user_id=user['id'], username=user['username'],
                                 display_name=user['display_name'], kind=user['kind'])
        s.company.conn.execute(c.todos.insert(), rows)
        write_event_to(s.company, ctx, 'todo create', f"assigned {len(rows)} to-do(s) for blocked memorized entries",
                       [Touched('todo', row['id'], 'create', None, 1, row, db='company') for row in rows],
                       actor_id=system_id, actor_kind='system', event_id=new_id())
        s.commits.commit(s.company, 'scheduler.write')
    except BaseException:
        if s.company.write_transaction:
            s.company.raw.rollback()
        raise
    return len(rows)


def _ensure_principal(s, system_id: str) -> None:
    """The host is the actor of record, so the company keeps its name for provenance rendering."""
    from bookflow.company.info import upsert_principal
    from bookflow.hub import schema as h
    if s.company.conn.execute(sa.select(c.principals.c.user_id).where(c.principals.c.user_id == system_id)).first():
        return
    user = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == system_id)).mappings().one()
    s.company.raw.execute('BEGIN IMMEDIATE')
    try:
        upsert_principal(s.company, user_id=user['id'], username=user['username'],
                         display_name=user['display_name'], kind=user['kind'])
        s.commits.commit(s.company, 'scheduler.write')
    except BaseException:
        if s.company.write_transaction:
            s.company.raw.rollback()
        raise


def run_company(s, system_id: str, now, *, notices: bool = True) -> dict[str, Any]:
    """One scheduler tick for the company open in ``s`` (writable, on the host's writer)."""
    from bookflow.company import notices as notice_queue
    from bookflow.company.memorized_schedule import today
    as_of = today(s.company_tz, at=now)
    out: dict[str, Any] = {'as_of': as_of}
    with s.commits.operation('scheduler.run', s.hub, s.company):
        _ensure_principal(s, system_id)
        try:
            out['memorized'] = enter_due(s, system_id, as_of)
        except Exception as error:  # noqa: BLE001 - recorded, never fatal to the host
            log.warning('scheduled memorized entries: %s', error)
            out['memorized'] = {'error': getattr(error, 'code', type(error).__name__)}
        try:
            out['todos_assigned'] = assign_blocked(s, system_id, as_of)
        except Exception as error:  # noqa: BLE001
            log.warning('scheduler to-dos: %s', error)
            out['todos_assigned'] = 0
        if not notices:
            out['notices'] = {'checked': 'within the hour'}
            return out
        try:
            _act_as(s, system_id)
            out['notices'] = notice_queue.scheduled(s, context().model_copy(update={
                'actor_id': system_id, 'reason': 'On schedule: overdue notices'}), as_of)
        except Exception as error:  # noqa: BLE001
            log.warning('scheduled overdue notices: %s', error)
            out['notices'] = {'error': getattr(error, 'code', type(error).__name__)}
    return out
