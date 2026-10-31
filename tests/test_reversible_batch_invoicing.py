"""R186: one invoice run reverses as a unit, with exact accounting and durable history."""
import pytest

from bookflow import BookflowError
from bookflow.company import batches
from tests.test_import_documents import books, db, _nets, _due  # noqa: F401


def post(books, **extra):
    return books.run('batch-invoice post', {
        'date': '2026-03-01', 'customers': ['Acme Plumbing', 'Bolt Electric'],
        'lines': [{'item': 'Labor', 'quantity': '1'}], **extra})


def reverse(books, batch, **context):
    return books.run('batch reverse', {'batch': batch}, reason='Undo the mistaken invoices', **context)


def unchanged(books):
    with db(books) as conn:
        return tuple(conn.iterdump())


def test_invoice_batch_lists_exact_effects_and_restores_books(books):
    before = _nets(books)
    with db(books) as conn:
        stock_before = conn.execute('SELECT * FROM inventory_movements').fetchall()
    out = post(books)
    batch = out['reversal_batch_id']
    assert out['next'] == f'batch reverse {batch} --dry-run'
    shown = books.run('batch show', {'batch': batch})['batch']
    assert shown['source_type'] == 'invoice_batch' and shown['source_id'] == out['batch_id']
    ids = [row['transaction_id'] for row in out['batch_rows']]
    assert [(e['record_type'], e['record_id'], e['action']) for e in shown['effects']] == [
        ('invoice', identifier, 'created') for identifier in ids]
    # Two service invoices at 85.00: debit receivables 170.00, credit income 170.00;
    # each customer owes 85.00, and service lines move no stock.
    assert sorted(_nets(books).values()) == [-17000, 17000]
    assert [_due(books, identifier) for identifier in ids] == [8500, 8500]
    preview = reverse(books, batch, dry_run=True)
    assert [step['record_id'] for step in preview['steps']] == list(reversed(ids))
    assert all(step['steps'][-1] == 'invoice delete' for step in preview['steps'])
    assert reverse(books, batch)['state'] == 'reversed'
    assert _nets(books) == before == {}
    assert [books.run('invoice show', {'invoice': identifier, 'include_deleted': True})
            ['settlement_current']['due_minor_units'] for identifier in ids] == [0, 0]
    with db(books) as conn:
        assert conn.execute('SELECT * FROM inventory_movements').fetchall() == stock_before
    assert books.run('batch-invoice show', {'batch': out['batch_id']})['created_count'] == 2


@pytest.mark.parametrize('blocker', ['payment', 'closed_period'])
def test_invoice_batch_blocker_changes_nothing_and_names_it(books, blocker):
    out = post(books)
    first = out['batch_rows'][0]['transaction_id']
    if blocker == 'payment':
        shown = books.run('invoice show', {'invoice': first})
        payment = books.run('payment receive', {
            'customer': 'Acme Plumbing', 'date': '2026-03-02', 'amount': '10',
            'deposit_to': 'Undeposited Funds', 'operation_key': 'batch-blocker',
            'payment_method': 'Check', 'applications': {'mode': 'inline', 'items': [
                {'invoice': first, 'expected_version': shown['version'], 'amount': '10'}]}})
        payment = books.run('payment show', {'payment': payment['id']})
    else:
        books.run('company update', {'closing_date': '2026-03-31'})
    before = unchanged(books)
    for dry_run in (True, False):
        with pytest.raises(BookflowError) as refused:
            reverse(books, out['reversal_batch_id'], dry_run=dry_run)
        assert refused.value.code == 'E_BATCH_BLOCKED'
        blockers = refused.value.details['blockers']
        if blocker == 'payment':
            assert any('payment' in b['message'] and payment['number'] in b['message'] for b in blockers)
        else:
            assert any(b['code'] == 'E_PERIOD_CLOSED' for b in blockers)
        assert unchanged(books) == before


def test_retry_has_separate_batch_and_only_successful_effects(books):
    books.run('customer deactivate', {'customer': 'Bolt Electric'})
    first = post(books)
    assert first['created_count'] == first['failed_count'] == 1
    books.run('customer activate', {'customer': 'Bolt Electric'})
    retry = books.run('batch-invoice retry', {'batch': first['batch_id']})
    assert retry['batch_id'] != first['batch_id']
    assert retry['reversal_batch_id'] != first['reversal_batch_id']
    for out in (first, retry):
        shown = books.run('batch show', {'batch': out['reversal_batch_id']})['batch']
        assert len(shown['effects']) == 1
        assert shown['source_id'] == out['batch_id']
    reverse(books, retry['reversal_batch_id'])
    assert _due(books, first['batch_rows'][0]['transaction_id']) == 8500
    reverse(books, first['reversal_batch_id'])
    assert _nets(books) == {}


def test_interrupted_invoice_reversal_resumes_without_repeating(books, monkeypatch):
    out = post(books)
    original = batches.run_nested
    interrupted = False

    def stop_after_first(*args, **kwargs):
        nonlocal interrupted
        result = original(*args, **kwargs)
        if args[2] == 'invoice delete' and not kwargs.get('dry_run') and not interrupted:
            interrupted = True
            raise RuntimeError('simulated interruption after committed inverse')
        return result

    monkeypatch.setattr(batches, 'run_nested', stop_after_first)
    with pytest.raises(RuntimeError, match='simulated interruption'):
        reverse(books, out['reversal_batch_id'])
    monkeypatch.setattr(batches, 'run_nested', original)
    assert books.run('batch show', {'batch': out['reversal_batch_id']})['batch']['state'] == 'reversing'
    assert reverse(books, out['reversal_batch_id'])['state'] == 'reversed'
    assert _nets(books) == {}
    with db(books) as conn:
        assert conn.execute("SELECT count(*) FROM batch_effects WHERE action = 'reversed' "
                            "AND json_extract(before, '$.command') = 'invoice delete'").fetchone()[0] == 2


def test_effect_failure_rolls_back_its_invoice_savepoint(books, monkeypatch):
    original = batches.record_effect
    calls = 0

    def fail_one(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BookflowError('E_VALIDATION', message='effect write failed')
        return original(*args, **kwargs)

    monkeypatch.setattr(batches, 'record_effect', fail_one)
    out = post(books)
    assert (out['created_count'], out['failed_count']) == (1, 1)
    effects = books.run('batch show', {'batch': out['reversal_batch_id']})['batch']['effects']
    assert len(effects) == 1
    assert effects[0]['record_id'] == out['batch_rows'][1]['transaction_id']
    assert books.run('invoice query', {'limit': 50})['count'] == 1
    assert sorted(_nets(books).values()) == [-8500, 8500]


def test_empty_failed_run_reports_nothing_reversible(books):
    books.run('customer deactivate', {'customer': 'Acme Plumbing'})
    books.run('customer deactivate', {'customer': 'Bolt Electric'})
    out = post(books)
    assert out['created_count'] == 0
    shown = books.run('batch show', {'batch': out['reversal_batch_id']})['batch']
    assert shown['effects'] == []
    with pytest.raises(BookflowError) as refused:
        reverse(books, out['reversal_batch_id'])
    assert 'nothing' in refused.value.message.lower()
    assert books.run('batch show', {'batch': out['reversal_batch_id']})['batch']['state'] == 'live'


def test_invoice_dry_run_lists_more_than_fifty_effects(books):
    customers = ['Acme Plumbing', 'Bolt Electric']
    for index in range(49):
        name = f'Batch customer {index}'
        books.run('customer create', {'name': name})
        customers.append(name)
    out = post(books, customers=customers)
    assert out['created_count'] == 51
    preview = reverse(books, out['reversal_batch_id'], dry_run=True)
    assert len(preview['steps']) == 51
    assert {step['record_id'] for step in preview['steps']} == {
        row['transaction_id'] for row in out['batch_rows']}
    assert sorted(_nets(books).values()) == [-433500, 433500]  # 51 x 85.00


def test_agent_without_invoice_delete_grant_is_refused_atomically(books):
    from tests.conftest import make_actor, make_agent, as_user
    from bookflow.core.config import Config

    out = post(books)
    owner = make_actor(books.root, 'invoice-reverse-owner', company_role=(books.company, 'owner'))
    owner_member = next(row for row in books.client.run('membership list', {'company': books.company})['items']
                        if row['user_id'] == owner and row['scope_type'] == 'company')
    books.client.run('membership grant', {'user': owner, 'company': books.company,
                                        'expected_version': owner_member['version'],
                                        'grants': ['transaction.invoice.delete']})
    agent_id = make_agent(books.client.run, 'invoice-reverse-agent', principals=owner,
                          company=books.company, owner=owner)
    member = next(row for row in books.client.run('membership list', {'company': books.company})['items']
                  if row['user_id'] == agent_id and row['scope_type'] == 'company')
    books.client.run('membership grant', {
        'user': agent_id, 'company': books.company, 'role': 'standard', 'expected_version': member['version'],
        'denies': ['transaction.invoice.delete']}, reason='Allow posting but not deletion')
    config = Config.load(books.root / 'config.toml')
    config.set_user('invoice-reverse-agent', agent_id)
    config.save()
    agent = as_user(books.root, 'invoice-reverse-agent')
    before = unchanged(books)
    for dry_run in (True, False):
        with pytest.raises(BookflowError) as refused:
            agent.run('batch reverse', {'batch': out['reversal_batch_id']}, company=books.company,
                      reason='Undo the named invoice batch', dry_run=dry_run)
        assert refused.value.code == 'E_BATCH_BLOCKED'
        assert any(b['code'] == 'E_PERMISSION' for b in refused.value.details['blockers'])
        assert unchanged(books) == before



def test_invoice_batch_restores_tracked_stock_quantity_value_and_balances(books):
    from tests.test_import_documents import _inventory

    _inventory(books)  # Widget sells for 20.00 and costs 8.00.
    books.run('inventory adjust', {'item': 'Widget', 'date': '2026-02-01',
                                   'adjustment_account': 'Opening Balance Equity',
                                   'quantity_change': '10', 'value_change': '80.00'})
    item = books.run('item show', {'item': 'Widget'})['id']

    def stock():
        report = books.run('report inventory-valuation', {'as_of': '2026-03-31', 'limit': 200})
        row = next(row for row in report['rows'] if row['item_id'] == item)
        return row['quantity_on_hand'], row['asset_value']['minor_units']

    before = _nets(books)
    assert stock() == ('10', 8000)
    out = post(books, lines=[{'item': 'Widget', 'quantity': '2'}])
    assert out['created_count'] == 2
    # Two customers each receive two widgets: four units leave at 8.00 cost,
    # leaving six units worth 48.00. Each owes 40.00; total income is 80.00.
    assert stock() == ('6', 4800)
    assert [_due(books, row['transaction_id']) for row in out['batch_rows']] == [4000, 4000]
    accounts = {name: books.run('account show', {'account': name})['id'] for name in (
        'Inventory Asset', 'Cost of Goods Sold', 'Sales Income', 'Accounts Receivable')}
    net = _nets(books)
    assert net[accounts['Inventory Asset']] == 4800
    assert net[accounts['Cost of Goods Sold']] == 3200
    assert net[accounts['Sales Income']] == -8000
    assert net[accounts['Accounts Receivable']] == 8000
    reverse(books, out['reversal_batch_id'])
    assert stock() == ('10', 8000)
    assert _nets(books) == before
    assert [books.run('invoice show', {'invoice': row['transaction_id'], 'include_deleted': True})
            ['settlement_current']['due_minor_units'] for row in out['batch_rows']] == [0, 0]
