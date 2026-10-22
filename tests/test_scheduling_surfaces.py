"""R111: the same to-do, overdue notices and Reminders list through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo. It creates a to-do about a customer, changes its due
date, reads it back, completes it and lists the done ones; turns overdue notices on, generates them as of
a far date (the demo's open invoices are all overdue by then), lists, shows, marks one sent and dismisses
another; and reads the Reminders list for the same date. Every surface must return the same figures.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('todo create', 'todo update', 'todo complete', 'todo show', 'todo list',
                      'notice generate', 'notice list', 'notice show', 'notice mark-sent', 'notice dismiss',
                      'reminders'))
AS_OF = '2099-06-15'


@pytest.mark.timeout(900)
def test_the_same_to_dos_notices_and_reminders_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                customer = (await call('customer query', {'limit': 1}))['items'][0]
                made = await call('todo create', {'title': 'Call about the balance', 'due_date': '2099-06-10',
                                                  'priority': 'high', 'link_type': 'customer', 'link': customer['id']})
                moved = await call('todo update', {'todo': made['id'], 'due_date': '2099-06-12', 'expected_version': 1})
                shown = await call('todo show', {'todo': made['id']})
                due = await call('reminders', {'as_of': AS_OF, 'limit': 5})
                done = await call('todo complete', {'todo': made['id']})
                listed = await call('todo list', {'status': 'done'})
                await call('company update', {'overdue_notice_days': 1, 'overdue_notice_template': 'letter'})
                preview = await call('notice generate', {'as_of': AS_OF}, dry_run=True)
                generated = await call('notice generate', {'as_of': AS_OF})
                again = await call('notice generate', {'as_of': AS_OF})
                ready = await call('notice list', {})
                first = await call('notice show', {'notice': ready['items'][0]['id']})
                sent = await call('notice mark-sent', {'notice': first['id'], 'method': 'email'})
                dismissed = (await call('notice dismiss', {'notice': ready['items'][1]['id'], 'note': 'Paying Friday'})
                             if len(ready['items']) > 1 else None)
                results[surface] = (customer, made, moved, shown, due, done, listed, preview, generated, again, ready,
                                    first, sent, dismissed)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (customer, made, moved, shown, due, done, listed, preview, generated, again, ready, first, sent,
                  dismissed) in results.items():
        assert made['link_label'] == customer['full_name'] and made['status'] == 'open', surface
        assert moved['due_date'] == shown['due_date'] == '2099-06-12' and shown['version'] == 2, surface
        assert 'Call about the balance' in [t['title'] for t in due['todos_due']], surface
        assert done['status'] == 'done' and [t['id'] for t in listed['items']] == [made['id']], surface
        assert preview['generated_count'] == generated['generated_count'] >= 1, surface
        assert again['generated_count'] == 0 and again['already_for_period'] == generated['generated_count'], surface
        assert sent['status'] == 'sent' and sent['sent_method'] == 'email', surface
        assert dismissed is None or dismissed['status'] == 'dismissed', surface
        sections = {s['key']: (s['count'], (s['total'] or {}).get('amount')) for s in due['sections']}
        shapes.add((tuple(sorted(sections.items())), generated['generated_count'],
                    tuple((n['customer_name'], n['overdue']['amount'], n['days_overdue']) for n in ready['items']),
                    first['letter']))
    assert len(shapes) == 1
