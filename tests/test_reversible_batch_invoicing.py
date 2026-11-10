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
    assert all(step['steps'][-1] == 'invoice void' for step in preview['steps'])
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
        if args[2] == 'invoice void' and not kwargs.get('dry_run') and not interrupted:
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
                            "AND json_extract(before, '$.command') = 'invoice void'").fetchone()[0] == 2


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


def test_agent_without_invoice_void_grant_is_refused_atomically(books):
    from tests.conftest import make_actor, make_agent
    from bookflow.core.config import Config

    out = post(books)
    owner = make_actor(books.root, 'invoice-reverse-owner', company_role=(books.company, 'owner'))
    owner_member = next(row for row in books.client.run('membership list', {'company': books.company})['items']
                        if row['user_id'] == owner and row['scope_type'] == 'company')
    books.client.run('membership grant', {'user': owner, 'company': books.company,
                                        'expected_version': owner_member['version'],
                                        'grants': ['ledger.post']})
    agent_id = make_agent(books.client.run, 'invoice-reverse-agent', principals=owner,
                          company=books.company, owner=owner)
    member = next(row for row in books.client.run('membership list', {'company': books.company})['items']
                  if row['user_id'] == agent_id and row['scope_type'] == 'company')
    books.client.run('membership grant', {
        'user': agent_id, 'company': books.company, 'role': 'standard', 'expected_version': member['version'],
        'denies': ['ledger.post']}, reason='Deny ledger writes for reversal witness')
    books.client.run('agent authorize', {'agent': agent_id, 'confirm_permitted_use': True,
                                         'acknowledge_fresh_context': True})
    config = Config.load(books.root / 'config.toml')
    config.set_user('invoice-reverse-agent', agent_id)
    config.save()
    from bookflow.core import registry
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run as dispatch_run
    before = unchanged(books)
    for dry_run in (True, False):
        with pytest.raises(BookflowError) as refused:
            ctx = Context.new(Interface.python, 'invoice reversal witness', on_behalf_of=owner,
                              reason='Undo the named invoice batch')
            dispatch_run(registry.get('batch reverse'), {'batch': out['reversal_batch_id']}, ctx,
                         data_root=str(books.root), company_selector=books.company, company_source='option',
                         dry_run=dry_run, _login='invoice-reverse-agent')
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


def test_standard_bookkeeper_voids_invoice_batch_through_door(books):
    from tests.conftest import make_actor, as_user

    make_actor(books.root, 'standard-bookkeeper', company_role=(books.company, 'standard'))
    clerk = as_user(books.root, 'standard-bookkeeper')
    before = _nets(books)
    out = post(books)
    ar = books.run('account show', {'account': 'Accounts Receivable'})['id']
    income = books.run('account show', {'account': 'Service Income'})['id']
    # Two untaxed services at $85: A/R +$170 debit, income +$170 credit.
    assert _nets(books)[ar] == 17000
    assert _nets(books)[income] == -17000
    preview = clerk.run('batch reverse', {'batch': out['reversal_batch_id']},
                        company=books.company, dry_run=True, reason='Correct the invoice run')
    assert all(step['steps'] == ['invoice void'] for step in preview['steps'])
    result = clerk.run('batch reverse', {'batch': out['reversal_batch_id'],
                                       'fingerprint': preview['fingerprint']},
                       company=books.company, reason='Correct the invoice run')
    assert result['state'] == 'reversed'
    assert _nets(books) == before == {}
    with db(books) as conn:
        assert conn.execute("SELECT DISTINCT status FROM transactions WHERE type='invoice'").fetchall() == [('voided',)]


@pytest.mark.parametrize('cause', ['permission', 'period', 'payment'])
def test_list_and_dry_run_report_complete_grouped_blockers(books, cause):
    from tests.conftest import make_actor, as_user

    out = post(books)
    caller = books.client
    if cause == 'permission':
        user = make_actor(books.root, 'blocked-clerk', company_role=(books.company, 'standard'))
        member = next(r for r in books.client.run('membership list', {'company': books.company})['items']
                      if r['user_id'] == user)
        books.client.run('membership grant', {'user': user, 'company': books.company,
                                             'expected_version': member['version'],
                                             'denies': ['ledger.post']})
        caller = as_user(books.root, 'blocked-clerk')
    elif cause == 'period':
        books.run('company update', {'closing_date': '2026-03-31'})
    else:
        invoice = out['batch_rows'][0]['transaction_id']
        shown = books.run('invoice show', {'invoice': invoice})
        books.run('payment receive', {'customer': 'Acme Plumbing', 'date': '2026-03-02',
                  'amount': '10', 'deposit_to': 'Undeposited Funds', 'operation_key': 'list-blocker',
                  'payment_method': 'Check', 'applications': {'mode': 'inline', 'items': [
                      {'invoice': invoice, 'expected_version': shown['version'], 'amount': '10'}]}})
    before = unchanged(books)
    listed = caller.run('batch list', {'kind': 'batch-invoice'}, company=books.company)['items'][0]
    with pytest.raises(BookflowError) as refused:
        caller.run('batch reverse', {'batch': out['reversal_batch_id']}, company=books.company,
                   dry_run=True, reason='Preview correction')
    assert refused.value.code == 'E_BATCH_BLOCKED'
    assert listed['reversible'] is False
    assert listed['blockers'] == refused.value.details['blockers']
    assert listed['blocker_count'] == 1
    blocker = listed['blockers'][0]
    assert blocker['count'] == (1 if cause == 'payment' else 2)
    assert len(blocker['members']) == blocker['count']
    if cause == 'permission':
        assert 'grant ledger.post' in blocker['clears']
    elif cause == 'payment':
        assert 'unapply payment' in blocker['clears'] and 'from invoice' in blocker['clears']
    assert unchanged(books) == before


def test_batch_list_filters_kind_and_inclusive_creation_dates(books):
    out = post(books)
    batch = out['reversal_batch_id']
    shown = books.run('batch show', {'batch': batch})['batch']
    day = shown['created_at'][:10]
    listed = books.run('batch list', {'kind': 'batch-invoice', 'date_from': day, 'date_to': day})['items']
    assert [row['id'] for row in listed] == [batch]
    assert listed[0]['reversible'] is True
    assert reverse(books, batch, dry_run=True)['blockers'] == listed[0]['blockers'] == []
    for filters in ({'kind': 'finance-charge'}, {'date_to': '2000-01-01'}, {'date_from': '2099-01-01'}):
        assert books.run('batch list', filters)['items'] == []
    with pytest.raises(BookflowError):
        books.run('batch list', {'date_from': '2026-02-01', 'date_to': '2026-01-01'})


def test_explicit_delete_permission_message_names_grant_without_role_contradiction():
    from bookflow.core.errors import permission_message

    message = permission_message({'reason': 'unavailable_target', 'capability': 'transaction.invoice.delete',
                                  'required_role': 'standard', 'role': 'owner'})
    assert 'explicit transaction.invoice.delete grant' in message
    assert 'no role includes' in message
    assert 'at role standard' not in message


def test_shared_blocker_summary_preserves_members_and_remedies():
    raw = [dict(seq=i, record_type='invoice', record_id=str(i), code='in_use',
                message=f'Invoice {i} uses payment 1', shared_message='In use by payment 1',
                clears=f'unapply payment 1 from invoice {i} first',
                used_by=[{'id': f'application-{i}', 'transaction_id': 'payment-1'}]) for i in (1, 2)]
    summary = batches.summarize_blockers(raw)
    assert len(summary) == 1
    assert summary[0]['count'] == 2
    assert [m['record_id'] for m in summary[0]['members']] == ['1', '2']
    assert all(b['clears'] in summary[0]['clears'] for b in raw)
    assert [m['used_by'] for m in summary[0]['members']] == [b['used_by'] for b in raw]


def test_invoice_inverse_is_scoped_to_invoice_batches():
    from bookflow.company.reversal_registry import DOCUMENTS
    invoice = DOCUMENTS['invoice']
    assert batches._document_inverse(invoice, {'kind': 'batch-invoice'}) == 'invoice void'
    for kind in ('import', 'memorized-run', 'opening'):
        assert batches._document_inverse(invoice, {'kind': kind}) == 'invoice delete'


def test_batch_date_range_validation_without_company():
    from pydantic import ValidationError
    from bookflow.company.import_models import BatchQueryInput
    assert BatchQueryInput(date_from='2026-11-09', date_to='2026-11-09').date_from.isoformat() == '2026-11-09'
    with pytest.raises(ValidationError, match='on or before'):
        BatchQueryInput(date_from='2026-11-10', date_to='2026-11-09')


def test_list_description_probes_native_authority_and_keeps_every_cause(monkeypatch):
    batch = dict.fromkeys(('id', 'kind', 'scope', 'ordering', 'label', 'state', 'source_type', 'source_id',
                          'reversal_of', 'actor_id', 'actor_kind', 'via', 'on_behalf_of', 'reason',
                          'created_at', 'reversed_by', 'reversed_at', 'reversal_reason', 'reversal_event_id'))
    batch.update(id='batch-1', kind='batch-invoice', state='live')
    blockers = [dict(code='E_PERMISSION', message='Missing ledger.post', record_id=str(i),
                     record_type='invoice', clears='ask an admin to grant ledger.post') for i in range(60)]
    blockers.append(dict(code='E_PERIOD_CLOSED', message='Closed period', record_id='60',
                         record_type='invoice', clears='reopen the period first'))
    monkeypatch.setattr(batches, 'counts', lambda *args: {})
    monkeypatch.setattr(batches, '_load_adapter', lambda *args: None)
    monkeypatch.setitem(batches.ADAPTERS, 'batch-invoice', batches.Adapter('batch-invoice'))

    def full_plan(s, ctx, identifier, *, probe):
        assert probe is True
        return {'blockers': blockers, 'steps': []}

    monkeypatch.setattr(batches, 'plan', full_plan)
    listed = batches.describe(None, batch, with_plan=True)
    assert listed['reversible'] is False
    assert listed['blocker_count'] == 2
    assert listed['blockers'] == batches.summarize_blockers(blockers)
    assert [b['count'] for b in listed['blockers']] == [60, 1]
