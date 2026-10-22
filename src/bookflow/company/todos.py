"""To-do notes: something to do by a date, assigned to a person or an agent (R111).

The anchor's To Do List: a title and details, a due date, a priority, an optional "with" -- the
customer, vendor or record it is about -- and a status that is Active until it is Done (or
Inactive when it no longer needs doing). Here a to-do is also *assigned*: to the person or agent
who should do it, which is how the 9 pm agent finds its work (`todo list --mine`) and how the
home page shows each person theirs.

An assignee must be a member of this company (or a hub administrator): a to-do for someone who
cannot open the books is one nobody will ever see. People and agents may both create, change and
complete to-dos; each change is an ordinary audited write.

The scheduler creates to-dos too, through ``scheduler_todo``: when an entry it was trusted to make
needs a person, it assigns one to the template's owner, keyed by the occurrence so a second run
cannot create it twice.
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched

RECORD = 'todo'


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def today(s) -> str:
    from bookflow.company.memorized_schedule import today as company_today
    return company_today(s.company_tz)


def _row(s, todo_id) -> dict[str, Any]:
    row = s.company.conn.execute(sa.select(c.todos).where(c.todos.c.id == todo_id)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': todo_id})
    return dict(row)


def _check_version(row, expected):
    if expected is not None and row['version'] != expected:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': RECORD, 'record_id': row['id'], 'expected_version': expected,
            'current_version': row['version'], 'updated_by': row['updated_by'], 'updated_via': row['updated_via']})


# ---------------------------------------------------------------- who and what

def resolve_assignee(s, selector: str | None) -> dict[str, Any]:
    """A hub user who can open this company: by username, by id, or "me"."""
    from bookflow.hub import schema as h
    from bookflow.hub.users import find_user
    if selector is None or selector.strip().casefold() == 'me':
        return {'id': s.actor.id, 'kind': s.actor.kind, 'display_name': s.actor.display_name, 'username': s.actor.username}
    user = find_user(s, username=selector.strip()) or find_user(s, id=selector.strip())
    # One answer for "no such user" and "not a member here": a to-do form must not tell a member
    # of one company who the users of another are.
    not_member = _invalid('assignee', 'is not a person or agent who is a member of this company')
    if user is None or not user.get('active', True) or user['kind'] == 'system':
        raise not_member
    row = s.company_row or {}
    member = s.hub.conn.execute(sa.select(h.memberships.c.id).where(
        h.memberships.c.user_id == user['id'], h.memberships.c.revoked_at.is_(None),
        sa.or_(sa.and_(h.memberships.c.scope_type == 'company', h.memberships.c.scope_id == row.get('id')),
               sa.and_(h.memberships.c.scope_type == 'organization', h.memberships.c.scope_id == row.get('organization_id'))))
    ).first()
    if member is None and not user.get('hub_admin'):
        raise not_member
    return user


def resolve_link(s, link_type: str | None, selector: str | None) -> tuple[str | None, str | None]:
    if link_type is None and selector is None:
        return None, None
    if link_type is None or selector is None:
        raise _invalid('link' if selector is None else 'link_type', 'give link_type and link together')
    if link_type in ('customer', 'vendor'):
        from bookflow.company.parties import resolve_party
        return link_type, resolve_party(s.company, link_type, selector)['id']
    if link_type == 'memorized':
        from bookflow.company import memorized
        return link_type, memorized._resolve_template(s, selector)['id']
    row = s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.id == selector)).first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'transaction', 'selector': selector, 'suggestions': []})
    return link_type, row[0]


def _labels(s, rows) -> tuple[dict[str, tuple[str, str]], dict[tuple[str, str], str]]:
    users = {row['user_id']: (row['display_name'], row['kind']) for row in s.company.conn.execute(
        sa.select(c.principals.c.user_id, c.principals.c.display_name, c.principals.c.kind)).mappings()}
    links: dict[tuple[str, str], str] = {}
    by_type: dict[str, set[str]] = {}
    for row in rows:
        if row['link_type']:
            by_type.setdefault(row['link_type'], set()).add(row['link_id'])
    for kind, ids in by_type.items():
        if kind == 'customer':
            q = sa.select(c.customers.c.id, c.customers.c.full_name).where(c.customers.c.id.in_(ids))
        elif kind == 'vendor':
            q = sa.select(c.vendors.c.id, c.vendors.c.name).where(c.vendors.c.id.in_(ids))
        elif kind == 'memorized':
            q = sa.select(c.memorized_transactions.c.id, c.memorized_transactions.c.name).where(c.memorized_transactions.c.id.in_(ids))
        else:
            q = sa.select(c.transactions.c.id, sa.func.replace(c.transactions.c.type, '_', '-') + ' ' + sa.func.coalesce(c.transactions.c.number, '')
                          ).where(c.transactions.c.id.in_(ids))
        for record_id, label in s.company.conn.execute(q):
            links[(kind, record_id)] = str(label).strip()
    return users, links


def project(s, row, labels=None, as_of=None) -> dict[str, Any]:
    from bookflow.commands.common import common_out
    users, links = labels or _labels(s, [row])
    as_of = as_of or today(s)
    who = users.get(row['assignee_id']) if row['assignee_id'] else None
    return dict(common_out(s, row), title=row['title'], notes=row['notes'], due_date=row['due_date'],
                priority=row['priority'], status=row['status'],
                overdue=row['status'] == 'open' and row['due_date'] < as_of,
                assignee_id=row['assignee_id'], assignee_name=who[0] if who else None, assignee_kind=who[1] if who else None,
                link_type=row['link_type'], link_id=row['link_id'],
                link_label=links.get((row['link_type'], row['link_id'])) if row['link_type'] else None,
                completed_at=row['completed_at'], completed_by=row['completed_by'], source_ref=row['source_ref'])


def _mirror(s, user):
    """Keep the assignee's name in the company, so the to-do renders without the hub."""
    from bookflow.company.info import upsert_principal
    if user['id'] != s.actor.id:
        upsert_principal(s.company, user_id=user['id'], username=user['username'],
                         display_name=user['display_name'], kind=user['kind'])


# ---------------------------------------------------------------- writes

def prepare(s, ctx, inp, verb) -> Plan:
    from bookflow.company.scheduling_models import TodoWriteOutput
    at, via = clock.now_iso(), ctx.interface.value
    if verb == 'create':
        user = resolve_assignee(s, inp.assignee)
        link_type, link_id = resolve_link(s, inp.link_type, inp.link)
        title = inp.title.strip()
        if not title:
            raise _invalid('title', 'must not be blank')
        row = dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=via,
                   updated_at=at, updated_by=s.actor.id, updated_via=via, title=title,
                   notes=(inp.notes or '').strip() or None, due_date=inp.due_date or today(s),
                   priority=inp.priority, assignee_id=user['id'], status='open', link_type=link_type,
                   link_id=link_id, completed_at=None, completed_by=None, source_ref=None)
        before, assignee = None, user
    else:
        before = _row(s, inp.todo)
        _check_version(before, inp.expected_version)
        row, assignee = dict(before), None
        if verb == 'complete':
            if before['status'] == 'done':
                raise _invalid('todo', 'is already done')
            row.update(status='done', completed_at=at, completed_by=s.actor.id)
        else:
            given = inp.model_fields_set
            if 'title' in given:
                if inp.title is None or not inp.title.strip():
                    raise _invalid('title', 'must not be blank')
                row['title'] = inp.title.strip()
            if 'notes' in given:
                row['notes'] = (inp.notes or '').strip() or None
            if 'due_date' in given:
                if inp.due_date is None:
                    raise _invalid('due_date', 'a to-do always has a due date')
                row['due_date'] = inp.due_date
            if 'priority' in given and inp.priority is not None:
                row['priority'] = inp.priority
            if 'assignee' in given:
                if inp.assignee is None:
                    row['assignee_id'] = None
                else:
                    assignee = resolve_assignee(s, inp.assignee)
                    row['assignee_id'] = assignee['id']
            if 'link_type' in given or 'link' in given:
                kind = inp.link_type if 'link_type' in given else before['link_type']
                selector = inp.link if 'link' in given else (before['link_id'] if kind == before['link_type'] else None)
                row['link_type'], row['link_id'] = resolve_link(s, kind, selector)
            if 'status' in given and inp.status is not None:
                row['status'] = inp.status
                if inp.status != 'done':
                    row['completed_at'] = row['completed_by'] = None
        if row == before:
            raise _invalid('todo', 'nothing to change')
        row.update(version=before['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=via)
    preview = TodoWriteOutput(**project(s, row), dry_run=s.dry_run)
    return Plan(preview, dict(verb=verb, row=row, before=before, assignee=assignee))


def apply(plan: Plan, ctx, s) -> Applied:
    from bookflow.company.scheduling_models import TodoWriteOutput
    data = plan.data
    row, before = data['row'], data['before']
    if data['assignee'] is not None:
        _mirror(s, data['assignee'])
    if before is None:
        s.company.conn.execute(c.todos.insert().values(**row))
        touched = Touched(RECORD, row['id'], 'create', None, 1, row, db='company')
        summary = f"created the to-do {row['title']!r}"
    else:
        s.company.conn.execute(c.todos.update().where(c.todos.c.id == row['id'], c.todos.c.version == before['version'])
                               .values(**{k: v for k, v in row.items() if k != 'id'}))
        touched = Touched(RECORD, row['id'], 'update', before['version'], row['version'], row, before, db='company')
        summary = (f"completed the to-do {row['title']!r}" if data['verb'] == 'complete'
                   else f"changed the to-do {row['title']!r}")
    return Applied(TodoWriteOutput(**project(s, row)), [touched], summary)


def scheduler_todo(s, ctx, *, source_ref, title, notes, assignee_id, due_date, link_type=None, link_id=None):
    """A to-do the scheduler assigns, once per ``source_ref``; returns the row it wrote, or None."""
    exists = s.company.conn.execute(sa.select(c.todos.c.id).where(c.todos.c.source_ref == source_ref)).first()
    if exists is not None:
        return None
    at = clock.now_iso()
    return dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, title=title[:160],
                notes=notes, due_date=due_date, priority='high', assignee_id=assignee_id, status='open',
                link_type=link_type, link_id=link_id, completed_at=None, completed_by=None, source_ref=source_ref)


# ---------------------------------------------------------------- reads

def show(s, inp):
    from bookflow.company.scheduling_models import TodoOutput
    return TodoOutput(**project(s, _row(s, inp.todo)))


def query(s, *, assignee_id=None, status='open', due_by=None, link=None, limit=100):
    t = c.todos
    q = sa.select(t)
    if status != 'all':
        q = q.where(t.c.status == status)
    if assignee_id is not None:
        q = q.where(t.c.assignee_id == assignee_id)
    if due_by is not None:
        q = q.where(t.c.due_date <= due_by)
    if link is not None:
        q = q.where(t.c.link_type == link[0], t.c.link_id == link[1])
    priority = sa.case((t.c.priority == 'high', 0), (t.c.priority == 'medium', 1), else_=2)
    rows = [dict(r) for r in s.company.conn.execute(q.order_by(t.c.due_date, priority, t.c.created_at, t.c.id)
                                                    .limit(limit + 1)).mappings()]
    return rows[:limit], len(rows) > limit


def page(s, inp):
    from bookflow.company.scheduling_models import TodoListOutput
    assignee_id = None
    if inp.mine:
        assignee_id = s.actor.id
    elif inp.assignee is not None:
        assignee_id = resolve_assignee(s, inp.assignee)['id']
    link = resolve_link(s, inp.link_type, inp.link) if (inp.link_type or inp.link) else None
    rows, truncated = query(s, assignee_id=assignee_id, status=inp.status, due_by=inp.due_by, link=link, limit=inp.limit)
    labels, as_of = _labels(s, rows), today(s)
    items = [project(s, row, labels, as_of) for row in rows]
    return TodoListOutput(as_of=as_of, items=items, count=len(items), truncated=truncated)
