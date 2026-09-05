"""Public work lifecycle, irreversible lineage identity and zero posting effects."""
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import database_path


def run(client, noun, verb, **data):
    return client.run(noun + ' ' + verb, data, company=COMPANY, reason='Customer work test')


def make(client, sale, noun='estimate', **extra):
    return run(client, noun, 'create', date='2026-01-12', title='Replace kitchen tap',
               customer=sale['customer'], lines=[dict(item=sale['item'], quantity='2')], **extra)


def test_estimate_immutable_edit_and_noop(client, sale):
    first = make(client, sale)
    assert first['gross_minor_units'] == 2468
    assert first['revision']['lines'][0]['facts']['pricing_basis'] == 'catalog'
    unchanged = run(client, 'estimate', 'update', estimate=first['id'], expected_version=1)
    assert unchanged['version'] == 1 and not unchanged['changed']
    changed = run(client, 'estimate', 'update', estimate=first['id'], expected_version=1, memo='Site visit arranged')
    assert changed['version'] == 2
    old = run(client, 'estimate', 'show', estimate=first['id'], revision_number=1)
    assert old['revision']['facts']['memo'] is None
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'update', estimate=first['id'], expected_version=1, title='Stale title')
    assert caught.value.code == 'E_VERSION_CONFLICT'
    history = run(client, 'estimate', 'history', estimate=first['id'])
    assert [row['revision_number'] for row in history['items']] == [1, 2]


def test_linked_chain_permanent_replay_completion_and_no_ledger_effect(client, sale):
    with open_database(database_path(client), writable=False) as db:
        before = {table.name: db.conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                  for table in (c.transactions, c.posting_batches, c.posting_lines)}
    proposal = make(client, sale, 'proposal')
    estimate = run(client, 'proposal', 'estimate', proposal=proposal['id'], expected_version=1,
                   conversion_key='proposal estimate once', date='2026-01-13')
    accepted = run(client, 'estimate', 'update', estimate=estimate['id'], expected_version=1,
                   status='accepted', decision_note='Customer accepted by telephone')
    data = dict(estimate=estimate['id'], expected_version=accepted['version'],
                conversion_key='accepted work once', date='2026-01-14')
    order = run(client, 'estimate', 'work-order', **data)
    assert order['revision']['lines'][0]['root_document_id'] == estimate['id']
    completed = run(client, 'work-order', 'complete', work_order=order['id'], expected_version=1,
                    actual_start='2026-01-14T10:00:00Z', actual_end='2026-01-14T11:00:00Z')
    assert completed['status'] == 'complete'
    assert completed['revision']['lines'][0]['completed_quantity'] == '2'
    replay = run(client, 'estimate', 'work-order', **data)
    assert replay['id'] == order['id'] and replay['status'] == 'complete' and replay['idempotent_replay']
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'work-order', **dict(data, date='2026-01-15'))
    assert caught.value.code == 'E_CONVERSION_KEY_REUSED'
    with open_database(database_path(client), writable=False) as db:
        after = {table.name: db.conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                 for table in (c.transactions, c.posting_batches, c.posting_lines)}
    assert after == before


def test_alternatives_and_captured_manual_amount_prices(client, sale):
    first = make(client, sale)
    alternative = run(client, 'estimate', 'copy', estimate=first['id'], expected_version=1, date='2026-01-13')
    assert first['estimate_group_id'] == alternative['estimate_group_id']
    run(client, 'estimate', 'update', estimate=first['id'], expected_version=1,
        status='accepted', decision_note='Accepted option one')
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'update', estimate=alternative['id'], expected_version=1,
            status='accepted', decision_note='Conflicting alternative')
    assert caught.value.code == 'E_WORK_DEPENDENCY'
    item = alternative['revision']['lines'][0]
    changed = run(client, 'estimate', 'update', estimate=alternative['id'], expected_version=1,
        lines=[dict(item=sale['item'], line_id=item['line_id'], net_amount='10.01')])
    assert changed['net_minor_units'] == 1001
    assert changed['revision']['lines'][0]['unit_price'] is None
