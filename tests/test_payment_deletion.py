"""Customer-payment deletion: exact reversal, named refusals and permanent receipts."""
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _net
from tests.test_purchase_deletion import enable, location
from tests.payment_raw_evidence import database


def receive(books, *, amount='25.00', applications=None, deposit_to=None, key='receive-1'):
    raw = dict(customer=books['customer'], date='2017-01-02', amount=amount,
               payment_method=books['methods']['Cash'], operation_key=key,
               deposit_to=deposit_to if deposit_to is not None else books['bank'])
    if applications is not None:
        raw['applications'] = applications
    return books['run']('payment receive', raw, reason='Customer paid')


def invoice(books, amount='25.00'):
    from tests.test_sales_deletion import service_item
    item = service_item(books, 'Payment deletion service')
    return books['run']('invoice post', dict(customer=books['customer'], date='2017-01-01',
        lines=[dict(item=item, quantity='1', unit_price=amount)]), reason='Bill the customer')


@pytest.mark.parametrize('state', ['posted', 'voided'])
def test_exact_payment_cancellation_without_post_authority(books, state):
    run = books['run']
    post = receive(books)
    if state == 'voided':
        post = run('payment void', dict(payment=post['id'], expected_version=1, operation_key='prior-void'),
                   reason='Prior cancellation')
    version = run('payment show', dict(payment=post['id']))['version']
    path = location(books)
    enable(books, 'payment')
    raw = dict(payment=post['id'], expected_version=version, operation_key='permanent-payment-delete')
    before = database(path)
    with pytest.raises(BookflowError) as denied:
        run('payment void', dict(payment=post['id'], expected_version=version, operation_key='denied'), reason='Posting denied')
    assert denied.value.code == 'E_PERMISSION'
    preview = run('payment delete', raw, reason='Remove duplicate receipt', dry_run=True)
    assert preview['version'] == version + 1 and preview['status'] == 'deleted'
    assert preview['cancelled_posting_lines'] == (0 if state == 'voided' else 2)
    assert database(path) == before
    result = run('payment delete', raw, reason='Remove duplicate receipt')
    assert result['status'] == 'deleted' and result['version'] == version + 1
    assert result['from_status'] == ('voided' if state == 'voided' else 'posted')
    assert _net(books) == {}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM posting_batches WHERE transaction_id=? AND kind='reversal'",
                          (post['id'],)).fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM payment_deletions').fetchone() == (1,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    after = database(path)
    for table in ('transaction_revisions', 'document_lines', 'payment_profiles',
                  'payment_components', 'payment_component_keys', 'payment_operations'):
        assert after['tables'][table] == before['tables'][table], table
    replay = run('payment delete', raw, reason='Remove duplicate receipt')
    assert replay['idempotent_replay'] and not replay['changed'] and database(path) == after
    with pytest.raises(BookflowError) as mismatch:
        run('payment delete', raw, reason='Other intention')
    assert mismatch.value.code == 'E_IDEMPOTENCY_MISMATCH' and database(path) == after


def test_applied_receipt_refuses_with_named_applications_and_writes_nothing(books):
    run = books['run']
    bill = invoice(books)
    post = receive(books, applications=dict(mode='inline', items=[
        dict(invoice=bill['id'], expected_version=bill['version'], amount='25.00')]))
    application = post['effect']['applications'][0]['application_id']
    path = location(books)
    enable(books, 'payment', deny_post=False)
    before = database(path)
    raw = dict(payment=post['id'], expected_version=post['version'], operation_key='blocked-delete')
    with pytest.raises(BookflowError) as blocked:
        run('payment delete', raw, reason='Remove duplicate receipt')
    assert blocked.value.code == 'E_HAS_APPLICATIONS'
    assert blocked.value.details['payment_id'] == post['id']
    assert blocked.value.details['application_ids'] == [application]
    assert 'payment unapply' in blocked.value.details['next']
    assert database(path) == before
    settled = run('invoice settlement', dict(invoice=bill['id']))
    assert settled['due_minor_units'] == 0 and settled['applied_minor_units'] == 2500
    run('payment unapply', dict(payment=post['id'], expected_version=post['version'], operation_key='release',
        applications=[dict(application_id=application, invoice_expected_version=settled['version'])]),
        reason='Release the application')
    current = run('payment show', dict(payment=post['id']))
    result = run('payment delete', dict(raw, expected_version=current['version'], operation_key='after-release'),
                 reason='Remove duplicate receipt')
    assert result['status'] == 'deleted'
    restored = run('invoice settlement', dict(invoice=bill['id']))
    assert restored['due_minor_units'] == 2500 and restored['applied_minor_units'] == 0
    assert restored['status'] == 'unpaid'
    assert _net(books)[books['income']] == -2500


def test_deposited_receipt_refuses_naming_the_deposit_and_writes_nothing(books):
    run = books['run']
    with sqlite3.connect(location(books)) as db:
        undeposited = db.execute("SELECT id FROM accounts WHERE system_role='undeposited_funds'").fetchone()[0]
    post = receive(books, deposit_to=undeposited)
    deposit = run('deposit post', dict(operation_key='deposit-1', document=dict(mode='inline',
        date='2017-01-03', deposit_to=books['bank'], additional=[],
        sources=[dict(source_type='payment', source=post['id'], expected_version=post['version'])])),
        reason='Deposit the receipt')['deposit']
    path = location(books)
    enable(books, 'payment')
    claimed = run('payment show', dict(payment=post['id']))
    before = database(path)
    with pytest.raises(BookflowError) as blocked:
        run('payment delete', dict(payment=post['id'], expected_version=claimed['version'],
            operation_key='blocked-by-deposit'), reason='Remove duplicate receipt')
    assert blocked.value.code == 'E_DEPOSIT_DEPENDENCY'
    assert blocked.value.details['deposit'] == deposit['id'] and blocked.value.details['source'] == post['id']
    assert 'atomic source and deposit cancellation' in blocked.value.details['reason']
    assert database(path) == before


def test_deleted_receipt_is_hidden_and_explicitly_readable(books):
    run = books['run']
    post = receive(books)
    kept = receive(books, amount='5.00', key='receive-2')
    path = location(books)
    enable(books, 'payment', deny_post=False)
    run('payment delete', dict(payment=post['id'], expected_version=1, operation_key='hide-me'),
        reason='Remove duplicate receipt')
    with pytest.raises(BookflowError) as hidden:
        run('payment show', dict(payment=post['id']))
    assert hidden.value.code == 'E_RECORD_NOT_FOUND'
    shown = run('payment show', dict(payment=post['id'], include_deleted=True))
    assert shown['status'] == 'deleted' and shown['deletion']['reason'] == 'Remove duplicate receipt'
    assert shown['deletion']['from_status'] == 'posted' and shown['deletion']['created_via'] == 'python'
    assert [row['id'] for row in run('payment query', {})['items']] == [kept['id']]
    assert post['id'] in [row['id'] for row in run('payment query', dict(include_deleted=True))['items']]
    with pytest.raises(BookflowError) as history:
        run('payment history', dict(payment=post['id']))
    assert history.value.code == 'E_RECORD_NOT_FOUND'
    assert run('payment history', dict(payment=post['id'], include_deleted=True))['total_count'] > 0
    register = run('register query', dict(account=books['bank'], date_from='2017-01-01', date_to='2017-12-31'))
    assert post['id'] not in [row['transaction_id'] for row in register['rows']]
    assert kept['id'] in [row['transaction_id'] for row in register['rows']]
    # Hiding a receipt cannot change report math: the ledger still sums both.
    assert register['ledger_totals']['period_debits']['minor_units'] == 3000
    assert register['ledger_totals']['period_credits']['minor_units'] == 2500
    for verb, extra in (('void', {}), ('update', {'memo': 'Late edit'})):
        with pytest.raises(BookflowError) as fenced:
            run('payment '+verb, dict(payment=post['id'], expected_version=2, operation_key='fence-'+verb, **extra),
                reason='Edit a deleted receipt')
        assert fenced.value.code == 'E_VALIDATION'


def test_late_tombstone_failure_rolls_back_the_whole_cancellation(books, monkeypatch):
    from bookflow.company import payment_deletions
    post = receive(books)
    path = location(books)
    enable(books, 'payment')
    before = database(path)
    original = payment_deletions.persist_tombstone
    def fail(s, row):
        original(s, row)
        raise BookflowError('E_VALIDATION', message='Injected after payment tombstone')
    monkeypatch.setattr(payment_deletions, 'persist_tombstone', fail)
    with pytest.raises(BookflowError, match='Injected'):
        books['run']('payment delete', dict(payment=post['id'], expected_version=1), reason='Rollback witness')
    assert database(path) == before


def test_co51_declaration_is_exact_and_storage_is_immutable(books):
    import importlib
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company import schema as c, payment_deletion_schema
    from bookflow.core.deletion_families import PAYMENT_FAMILIES
    import re
    migration = importlib.import_module('bookflow.storage.company_migrations.versions.0051_payment_deletions')
    assert migration.DDL == (str(CreateTable(c.payment_deletions).compile(dialect=dialect())), *payment_deletion_schema.guards())
    check = next(x for x in c.payment_deletions.constraints if x.name == 'ck_payment_delete_family')
    assert tuple(re.findall("'([^']+)'", str(check.sqltext))) == PAYMENT_FAMILIES == ('payment',)
    post = receive(books)
    path = location(books)
    enable(books, 'payment')
    books['run']('payment delete', dict(payment=post['id'], expected_version=1, operation_key='immutable'),
                 reason='Remove duplicate receipt')
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for sql in ('DELETE FROM payment_deletions', 'UPDATE payment_deletions SET reason=reason',
                    'UPDATE transactions SET version=version WHERE id=?'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                db.execute(sql, (post['id'],) if '?' in sql else ())
            db.rollback()


def test_payment_delete_needs_its_own_explicit_catalog_transition(books, monkeypatch):
    from bookflow.hub import permission_payment_deletion_catalog as current, permission_sales_deletion_catalog as previous
    post = receive(books)
    client = books['client']
    with monkeypatch.context() as historical:
        historical.setattr(current, 'CATALOG', previous.CATALOG)
        historical.setattr(current, 'MANIFEST', previous.MANIFEST)
        historical.setattr(current, 'catalog_bundle', previous.catalog_bundle)
        enable(books, 'payment')
    path = location(books)
    before = database(path)
    with pytest.raises(BookflowError) as error:
        books['run']('payment delete', dict(payment=post['id'], expected_version=1), reason='Not activated')
    assert error.value.code == 'E_PERMISSION' and database(path) == before
    state = client.permission.show()
    client.permission.activate(expected_generation=state['generation'], expected_catalog_sha256=state['catalog_sha256'])
    assert books['run']('payment delete', dict(payment=post['id'], expected_version=1),
                        reason='Explicitly activated')['status'] == 'deleted'
