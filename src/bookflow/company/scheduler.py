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


def _cannot_act(s, user_id: str | None, principal: str | None = None,
                revision_id: str | None = None) -> tuple[str | None, int | None]:
    """Why this author can no longer lend its authority to an entry (or None), and its epoch.

    Checked here, before anything is dispatched, in every permission mode -- a root still in the
    legacy mode never consults a credential at all -- and again by ``ScheduledBinding`` inside
    the policy check. An agent gets exactly a bearer token's checks: assigned to the person it
    acted for (who is active), not suspended, and the authority epoch it saved the revision with.
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
        return f'{name} (an agent) no longer acts for the person it saved it for', None
    saved = s.company.conn.execute(sa.select(c.memorized_revision_authority.c.authority_epoch).where(
        c.memorized_revision_authority.c.revision_id == revision_id)).first() if revision_id else None
    if saved is None or saved[0] != epoch:
        return f"{name} (an agent) holds different authority than when it saved this memorized transaction", None
    return None, epoch


def _blocked(code, message, **details):
    from bookflow.core.errors import BookflowError
    return BookflowError(code, message=message[:500], details=details)


def _rebase(s, ctx, claimed) -> list:
    """Point each waiting occurrence at its template as it is now (the anchor enters the memorized
    transaction as it stands), so content and authority come from the same, current revision."""
    from bookflow.company import memorized as service
    from bookflow.core import clock
    from bookflow.core.registry import Touched
    touched, at = [], clock.now_iso()
    for occurrence in claimed:
        if not occurrence['_enter'] or occurrence['status'] != 'pending' or occurrence['attempt_count']:
            continue
        template = service._one(s, c.memorized_transactions, c.memorized_transactions.c.id == occurrence['template_id'])
        if template is None or template['current_revision_id'] == occurrence['revision_id']:
            continue
        values = dict(revision_id=template['current_revision_id'], command=template['command'],
                      version=occurrence['version'] + 1, updated_at=at, updated_by=s.actor.id,
                      updated_via=ctx.interface.value)
        before = {k: v for k, v in occurrence.items() if not k.startswith('_')}
        touched.append(Touched('memorized_occurrence', occurrence['id'], 'update', occurrence['version'],
                               values['version'], dict(before, **values), before, db='company'))
        s.company.conn.execute(c.memorized_occurrences.update().where(c.memorized_occurrences.c.id == occurrence['id'])
                               .values(**values))
        occurrence.update(values)
    return touched


def _enter(s, occurrence, as_of):
    """Enter one claimed occurrence with the authority of its revision's author, and as that author."""
    from bookflow.company import memorized as service, memorized_entry
    recovered = memorized_entry._recover(s, occurrence)
    if recovered is not None:
        return {'occurrence': occurrence, 'output': recovered['output'], 'recovered': recovered['source']}
    template = service._one(s, c.memorized_transactions, c.memorized_transactions.c.id == occurrence['template_id'])
    author, principal = author_of(s, occurrence['revision_id'])
    why, epoch = _cannot_act(s, author, principal, occurrence['revision_id'])
    if why is not None:
        return {'occurrence': occurrence, 'error': _blocked(
            'E_PERMISSION', f"Not entered: the author of this memorized transaction, {why}, so it no longer "
                            "carries anyone's authority. A person must review it.", reason='author_inactive')}
    _act_as(s, author, principal, epoch)
    from bookflow.core.context import ActorKind
    ctx = context().model_copy(update={'actor_id': author, 'actor_kind': ActorKind(s.actor.kind), 'on_behalf_of': principal})
    frequency = template['frequency']
    if template['group_id']:
        group = service._one(s, c.memorized_groups, c.memorized_groups.c.id == template['group_id'])
        if group is not None and group['frequency'] != 'never':
            frequency = group['frequency']
    reason = f"On schedule: {template['name']} ({frequency.replace('_', ' ')}) for {occurrence['slot_date']}"
    result = memorized_entry._enter_one(s, ctx, occurrence, reason=reason)
    error = result.get('error')
    if error is not None and error.code == 'E_COMPANY_NOT_FOUND':
        name = s.actor.display_name or s.actor.username
        result['error'] = _blocked('E_PERMISSION', f'Not entered: the author of this memorized transaction, {name}, '
                                                   'is no longer a member of this company.', reason='author_not_member')
    return result


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
    if catch_up is None:
        catch_up = DEFAULT_CATCH_UP_DAYS
    cutoff = (date.fromisoformat(as_of) - timedelta(days=catch_up)).isoformat()
    summary = {'entered': 0, 'blocked': 0, 'waiting': 0, 'left_for_a_person': 0}

    def keep_waiting(occurrence, mode):
        # Only waiting slots this run will enter: a reminder slot, one older than the catch-up
        # window, or one already blocked is on the Reminders list and must never crowd out the rest.
        return mode == 'enter_automatically' and occurrence['slot_date'] >= cutoff

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
            rebased = _rebase(s, ctx, claimed)
            if rebased:
                write_event_to(s.company, ctx, PROCESS, f'{len(rebased)} waiting entr{"y" if len(rebased) == 1 else "ies"} '
                               'will replay the memorized transaction as it is now', rebased,
                               actor_id=system_id, actor_kind='system', event_id=new_id())
            s.commits.commit(s.company, 'memorized.enter')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
        results = []
        for occurrence in claimed:
            # A blocked slot waits for a person (`memorized retry`); the scheduler never tries it again,
            # even when the same slot comes round in a group run.
            if occurrence['_enter'] and occurrence['status'] != 'blocked':
                results.append(_enter(s, occurrence, as_of))
        _act_as(s, system_id)
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            rows, _ = memorized_entry._record(s, ctx, PROCESS, claimed, results)
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


def _active_admin(s) -> str | None:
    """An active person who administers this company: who reviews what no author can carry."""
    from bookflow.hub import schema as h
    u, m, row = h.users, h.memberships, s.company_row
    found = s.hub.conn.execute(sa.select(u.c.id).join(m, m.c.user_id == u.c.id).where(
        u.c.active.is_(True), u.c.kind == 'human', m.c.revoked_at.is_(None), m.c.role == 'admin',
        sa.or_(sa.and_(m.c.scope_type == 'company', m.c.scope_id == row['id']),
               sa.and_(m.c.scope_type == 'organization', m.c.scope_id == row['organization_id'])))
        .order_by(u.c.created_at, u.c.id).limit(1)).scalar_one_or_none()
    return found or s.hub.conn.execute(sa.select(u.c.id).where(
        u.c.active.is_(True), u.c.kind == 'human', u.c.hub_admin.is_(True)).order_by(u.c.created_at, u.c.id).limit(1)
    ).scalar_one_or_none()


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
        authority = occ['error_code'] in AUTHORITY_CODES or _cannot_act(s, author, principal, occ['revision_id'])[0] is not None
        if authority:
            assignee = _active_admin(s)
            title = f"Review {occ['name']} for {occ['slot_date']}: its author can no longer post it"
            notes = (f"The scheduler did not enter {occ['name']} for {occ['slot_date']} because it runs with the "
                     f"authority of the person or agent who last saved it, {author_name}, and that authority is gone:\n"
                     f"{occ['error_code']}: {occ['error_message']}\n\nIf it should still be entered, re-save the memorized "
                     f"transaction under your own name and `memorized retry {occ['id']}`; otherwise "
                     f"`memorized skip {occ['id']}`.")
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
