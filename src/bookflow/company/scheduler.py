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

*Whose authority.* The scheduler acts **for the template's owner** -- the person or agent who
last saved it (its current revision's author) -- so each entry is authorized exactly as if that
owner had entered it, and a closed period, a deactivated account or an owner who has since lost
the right to post is a *blocked* occurrence with its reason, never a silent skip and never a
retry loop: blocked occurrences wait for a person (`memorized retry` or `memorized skip`), and
the scheduler assigns that person a to-do saying so. Every audit event it writes names the host
as the actor (the system principal, client ``scheduler``, interface ``system``) and the owner as
``on_behalf_of``, with the schedule as the reason.

*Catch-up.* A host that was off for a while owes every slot it missed, each at its own accounting
date, as the anchor enters every missed automatic transaction when the books are next opened.
The company preference ``scheduler_catch_up_days`` limits that: a missed slot older than that
many days is left waiting for a person instead of entered.

**Overdue notices.** On the company's notice schedule, one notice per overdue customer into the
``notice`` queue (``company/notices.py``). Generated, never sent.

Nothing here sends anything off the machine.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c

log = logging.getLogger("bookflow.scheduler")

CLIENT = 'scheduler'
PROCESS = 'memorized process'


def context():
    from bookflow.core.context import Context, Interface
    return Context.new(Interface.system, CLIENT)


def owner_of(s, item) -> str | None:
    """The person or agent whose authority an automatic entry uses."""
    group = item.get('group')
    if group is not None and group.get('frequency') != 'never':
        return group['updated_by']
    revision = s.company.conn.execute(
        sa.select(c.memorized_transaction_revisions.c.created_by)
        .where(c.memorized_transaction_revisions.c.id == item['template']['current_revision_id'])).scalar_one_or_none()
    return revision or item['template']['created_by']


def _act_as(s, user_id: str | None, system_id: str) -> str:
    """Load ``user_id`` (with its memberships) as the session's actor, or the system principal."""
    from bookflow.core.dispatch import _load_actor_by_id
    from bookflow.core.errors import BookflowError
    from bookflow.core.identity_admin_binding import ScheduledBinding
    from bookflow.hub import schema as h
    acting = system_id
    if user_id is not None:
        try:
            _load_actor_by_id(s, user_id)
            acting = user_id
        except BookflowError:
            pass
    if acting == system_id:
        _load_actor_by_id(s, system_id)
    # Authority is the owner's own, read afresh for every entry; an agent owner acts for the
    # person it belongs to, as its tokens do.
    principal = None
    if s.actor.kind == 'agent':
        principal = s.hub.conn.execute(sa.select(h.users.c.owner_user_id).where(h.users.c.id == acting)).scalar_one_or_none()
    s.credential = ScheduledBinding(acting, principal)
    return acting


def enter_due(s, system_id: str, as_of: str) -> dict[str, Any]:
    """Claim and enter every automatic slot due by ``as_of``; list the reminder ones."""
    from bookflow.company import memorized_entry
    from bookflow.core import audit
    from bookflow.core.registry import Plan
    info = s.company.conn.execute(sa.select(c.company_info.c.scheduler_catch_up_days)).mappings().one()
    catch_up = info['scheduler_catch_up_days']
    cutoff = (date.fromisoformat(as_of) - timedelta(days=catch_up)).isoformat() if catch_up is not None else None
    summary = {'entered': 0, 'blocked': 0, 'waiting': 0, 'left_for_a_person': 0}
    work = memorized_entry._due_work(s, SimpleNamespace(as_of=as_of, limit=200, memorized=None), as_of)
    for item in work:
        if item['enter'] and cutoff is not None and item['slot'] < cutoff:
            item['enter'] = False
            if item.get('occurrence') is None:
                summary['left_for_a_person'] += 1
    # A slot already waiting that this run will not enter is already on the Reminders list:
    # touching it again would only write an empty audit event every minute.
    work = [item for item in work if item['enter'] or item.get('occurrence') is None]
    if not work:
        return summary
    by_owner: dict[str | None, list[dict[str, Any]]] = {}
    for item in work:
        by_owner.setdefault(owner_of(s, item), []).append(item)
    for owner, items in by_owner.items():
        acting = _act_as(s, owner, system_id)
        s.directive_code = None
        ctx = context().model_copy(update={
            'actor_id': acting, 'reason': f'On schedule: memorized transactions due by {as_of}'})
        data = {'verb': 'process', 'as_of': as_of, 'warnings': [], 'work': items, 'command_name': PROCESS}
        try:
            with audit.scheduled_as(system_id):
                # As dispatch does for every write: the claim runs inside the caller's transaction.
                s.company.raw.execute('BEGIN IMMEDIATE')
                out = memorized_entry.apply_entry(Plan(None, data), ctx, s).output
        except Exception as error:  # noqa: BLE001 - one owner's run failing never stops the others
            log.warning('scheduled memorized entries for %s failed: %s', owner, error)
            summary.setdefault('errors', []).append(getattr(error, 'code', type(error).__name__))
            continue
        summary['entered'] += out.entered_count
        summary['blocked'] += out.blocked_count
        summary['waiting'] += out.pending_count
    return summary


def assign_blocked(s, system_id: str, as_of: str) -> int:
    """A to-do for the owner of every blocked occurrence that does not have one yet."""
    from bookflow.company import todos
    from bookflow.core.audit import write_event_to
    from bookflow.core.ids import new_id
    from bookflow.core.registry import Touched
    o, t = c.memorized_occurrences, c.memorized_transactions
    blocked = s.company.conn.execute(
        sa.select(o.c.id, o.c.slot_date, o.c.error_code, o.c.error_message, o.c.template_id,
                  t.c.name, t.c.current_revision_id, t.c.created_by, t.c.group_id)
        .join(t, t.c.id == o.c.template_id).where(o.c.status == 'blocked')
        .outerjoin(c.todos, c.todos.c.source_ref == sa.literal('memorized-occurrence:') + o.c.id)
        .where(c.todos.c.id.is_(None))).mappings().all()
    if not blocked:
        return 0
    _act_as(s, system_id, system_id)
    ctx = context().model_copy(update={'actor_id': system_id, 'reason': 'On schedule: an automatic entry needs a person'})
    rows = []
    for occ in blocked:
        owner = owner_of(s, {'template': dict(occ, id=occ['template_id']), 'group': None})
        row = todos.scheduler_todo(
            s, ctx, source_ref='memorized-occurrence:' + occ['id'],
            title=f"Enter {occ['name']} for {occ['slot_date']}: the scheduler could not",
            notes=(f"{occ['error_code']}: {occ['error_message']}\n\nFix the cause, then `memorized retry {occ['id']}`"
                   f" (or `memorized skip {occ['id']}` if it should not be entered)."),
            assignee_id=owner, due_date=as_of, link_type='memorized', link_id=occ['template_id'])
        if row is not None:
            rows.append(row)
    if not rows:
        return 0
    s.company.raw.execute('BEGIN IMMEDIATE')
    try:
        from bookflow.core.dispatch import _upsert_principals
        _upsert_principals(s, ctx)
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


def run_company(s, system_id: str, now) -> dict[str, Any]:
    """One scheduler tick for the company open in ``s`` (writable, on the host's writer)."""
    from bookflow.company import notices
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
        try:
            _act_as(s, system_id, system_id)
            out['notices'] = notices.scheduled(s, context().model_copy(update={
                'actor_id': system_id, 'reason': 'On schedule: overdue notices'}), as_of)
        except Exception as error:  # noqa: BLE001
            log.warning('scheduled overdue notices: %s', error)
            out['notices'] = {'error': getattr(error, 'code', type(error).__name__)}
    return out
