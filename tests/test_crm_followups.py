"""Lead follow-ups remain native tasks, with source admission and complete due counts."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_scheduling import member


@pytest.fixture
def books(tmp_path, monkeypatch):
    from tests.crm_support import company
    return company(tmp_path, monkeypatch, 'America/Los_Angeles')


def lead(books):
    return books.run('lead create', {'display_name': 'Enquiry'})


def followup(books, target, **extra):
    return books.run('todo create', dict(title='Call back', link_type='lead', link=target['id'],
                                         due_date='2026-11-09', **extra))


def test_followup_company_date_and_completion_history(books, monkeypatch):
    from bookflow.core import clock
    monkeypatch.setattr(clock, 'now', lambda: datetime(2026, 11, 10, 1, 30, tzinfo=timezone.utc))
    target = lead(books)
    task = followup(books, target)
    assert task['link_label'] == 'Enquiry'
    assert not task['overdue']  # UTC tomorrow is still today in the company.
    due = books.run('reminders', {})
    assert due['as_of'] == '2026-11-09'
    assert [row['id'] for row in due['todos_due']] == [task['id']]
    finished = books.run('todo complete', {'todo': task['id'], 'expected_version': 1})
    assert finished['completed_at'] and finished['completed_by'] == task['created_by']
    assert books.run('reminders', {})['todos_due'] == []
    reopened = books.run('todo update', {'todo': task['id'], 'expected_version': 2, 'status': 'open'})
    assert reopened['completed_at'] is None and reopened['version'] == 3
    monkeypatch.setattr(clock, 'now', lambda: datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc))
    assert books.run('todo show', {'todo': task['id']})['overdue']


def test_followup_assignee_is_an_active_company_member(books):
    from tests.conftest import make_actor
    target = lead(books)
    outsider = make_actor(Path(books.root), 'outside')
    for selector in ('outside', outsider):
        with pytest.raises(BookflowError) as refused:
            followup(books, target, assignee=selector)
        assert refused.value.code == 'E_VALIDATION'
    user, _ = member(books, 'caller')
    task = followup(books, target, assignee='caller')
    assert task['assignee_id'] == user
    with sqlite3.connect(Path(books.root) / 'hub.db') as db:
        db.execute('UPDATE users SET active=0 WHERE id=?', (user,))
    with pytest.raises(BookflowError) as refused:
        books.run('todo update', {'todo': task['id'], 'expected_version': 1, 'assignee': user})
    assert refused.value.code == 'E_VALIDATION'
    assert books.run('todo show', {'todo': task['id']})['assignee_name']
    assert [row['id'] for row in books.run('todo list', {'assignee_state': 'departed'})['items']] == [task['id']]
    books.run('todo update', {'todo': task['id'], 'expected_version': 1, 'assignee': None})
    assert [row['id'] for row in books.run('todo list', {'assignee_state': 'unassigned'})['items']] == [task['id']]


def test_followup_target_permission_blocks_direct_and_aggregate_reads(books):
    target = lead(books)
    private = followup(books, target)
    public = books.run('todo create', {'title': 'Ordinary work', 'due_date': '2026-11-09'})
    _, denied = member(books, 'withoutcrm', denies=['crm.read'])
    for command, payload in (
        ('todo show', {'todo': private['id']}),
        ('todo complete', {'todo': private['id'], 'expected_version': 1}),
        ('todo update', {'todo': private['id'], 'expected_version': 1, 'link': None, 'link_type': None}),
        ('todo create', {'title': 'No admission', 'link_type': 'lead', 'link': target['id']}),
        ('todo list', {'link_type': 'lead', 'link': target['id']}),
    ):
        with pytest.raises(BookflowError) as refused:
            denied(command, payload)
        assert refused.value.code == 'E_PERMISSION'
    visible = denied('todo list', {})
    assert visible['total_count'] == 1
    assert [row['id'] for row in visible['items']] == [public['id']]
    due = denied('reminders', {'as_of': '2026-11-09'})
    assert [row['id'] for row in due['todos_due']] == [public['id']]
    assert next(row['count'] for row in due['sections'] if row['key'] == 'todos_due') == 1
    assert target['id'] not in str(due) and private['id'] not in str(due)



def test_followup_replay_rechecks_target_access(books):
    target = lead(books)
    _, actor = member(books, 'helper')
    payload = dict(title='Retained private follow-up', link_type='lead', link=target['id'])
    task = actor('todo create', payload, idempotency_key='crm-task')
    assert actor('todo create', payload, idempotency_key='crm-task')['id'] == task['id']
    books.client.run('membership grant', {'user': 'helper', 'company': books.company,
        'role': 'standard', 'expected_version': 1, 'denies': ['crm.read']})
    with pytest.raises(BookflowError) as refused:
        actor('todo create', payload, idempotency_key='crm-task')
    assert refused.value.code == 'E_PERMISSION'

def test_503_due_followups_have_exact_counts_and_continuation(books):
    target = lead(books)
    first = followup(books, target)
    path = Path(books.run('company show', {})['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        original = dict(db.execute('SELECT * FROM todos WHERE id=?', (first['id'],)).fetchone())
        columns = list(original)
        placeholders = ','.join('?' for _ in columns)
        sql = 'INSERT INTO todos (' + ','.join(columns) + ') VALUES (' + placeholders + ')'
        for index in range(502):
            row = dict(original, id=new_id(), title=f'Call {index}', priority='high' if index % 2 else 'low')
            db.execute(sql, [row[column] for column in columns])
    cursor, seen = None, []
    for page in range(3):
        result = books.run('reminders', {'as_of': '2026-11-09', 'limit': 200, 'todos_cursor': cursor})
        assert next(row['count'] for row in result['sections'] if row['key'] == 'todos_due') == 503
        seen.extend(row['id'] for row in result['todos_due'])
        cursor = result['todos_next_cursor']
        assert bool(cursor) == (page < 2)
    assert len(seen) == len(set(seen)) == 503
    initial = books.run('todo list', {'link_type': 'lead', 'link': target['id'], 'limit': 500})
    final = books.run('todo list', {'link_type': 'lead', 'link': target['id'], 'limit': 500,
                                   'cursor': initial['next_cursor']})
    assert initial['total_count'] == final['total_count'] == 503
    assert initial['count'] == 500 and final['count'] == 3
    assert final['next_cursor'] is None
    with pytest.raises(BookflowError) as mismatched:
        books.run('todo list', {'status': 'done', 'limit': 500, 'cursor': initial['next_cursor']})
    assert mismatched.value.code == 'E_VALIDATION'
    books.run('todo complete', {'todo': first['id'], 'expected_version': 1})
    with pytest.raises(BookflowError) as stale:
        books.run('todo list', {'link_type': 'lead', 'link': target['id'], 'limit': 500,
                               'cursor': initial['next_cursor']})
    assert stale.value.code == 'E_QUERY_STALE'


def test_service_due_count_and_continuation_without_full_company_bootstrap(monkeypatch):
    """Exercise the actual SQL and shared cursor with 503 protected rows and 2 ordinary rows."""
    from types import SimpleNamespace
    import sqlalchemy as sa
    from bookflow.company import todos, schema as c, query
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        c.todos.create(conn)
        c.audit_events.create(conn)
        session = SimpleNamespace(company=SimpleNamespace(conn=conn), company_row={'id': 'company'},
                                  actor=SimpleNamespace(id='actor'), memberships=[])
        monkeypatch.setattr(query, 'permission_fingerprint', lambda s, principal: 'test-permissions')
        monkeypatch.setattr(todos, 'may_read_leads', lambda s: True)
        base = dict(version=1, created_at='2026-11-09T12:00:00Z', created_by='actor', created_via='python',
                    updated_at='2026-11-09T12:00:00Z', updated_by='actor', updated_via='python',
                    notes=None, due_date='2026-11-09', priority='medium', assignee_id='actor', status='open',
                    completed_at=None, completed_by=None, source_ref=None)
        conn.execute(c.todos.insert(), [dict(base, id=new_id(), title=f'Work {n}',
            link_type='lead' if n < 503 else None, link_id='lead-id' if n < 503 else None) for n in range(505)])
        cursor, ids = None, []
        for index in range(3):
            rows, count, cursor = todos.paged_rows(session, due_by='2026-11-09', limit=200,
                                                  cursor=cursor, as_of='2026-11-09')
            assert count == 505
            ids.extend(row['id'] for row in rows)
            assert bool(cursor) == (index < 2)
        assert len(ids) == len(set(ids)) == 505
        rows, count, cursor = todos.paged_rows(session, due_by='2026-11-09', limit=200, as_of='2026-11-09')
        monkeypatch.setattr(todos, 'may_read_leads', lambda s: False)
        with pytest.raises(BookflowError) as changed_permission:
            todos.paged_rows(session, due_by='2026-11-09', limit=200, cursor=cursor, as_of='2026-11-09')
        assert changed_permission.value.code == 'E_VALIDATION'
        rows, count, cursor = todos.paged_rows(session, due_by='2026-11-09', limit=200, as_of='2026-11-09')
        assert count == len(rows) == 2 and cursor is None
        assert all(row['link_type'] is None for row in rows)
    engine.dispose()


def test_service_target_admission_precedes_resolution_and_cached_output(monkeypatch):
    import json
    from bookflow.company import todos
    from bookflow.hub import access

    def denied(session, capability, role):
        assert capability == 'crm.read' and role == 'member'
        raise BookflowError('E_PERMISSION')

    monkeypatch.setattr(access, 'require_resource', denied)
    for action in (lambda: todos.resolve_link(None, 'lead', 'not-a-known-lead'),
                   lambda: todos.replay(None, None, None, {'state': 'done', 'output': json.dumps({'link_type': 'lead'})})):
        with pytest.raises(BookflowError) as refusal:
            action()
        assert refusal.value.code == 'E_PERMISSION'
