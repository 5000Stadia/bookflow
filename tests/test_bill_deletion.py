"""Vendor-bill deletion: exact reversal, named refusals and permanent receipts."""
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _bill, _inventory_part, _net
from tests.test_purchase_deletion import enable, location
from tests.payment_raw_evidence import database

RETAINED_TABLES = ('transaction_revisions', 'document_lines', 'document_line_identities',
                   'purchase_profiles', 'purchase_expense_lines', 'purchase_item_lines',
                   'ap_obligation_keys', 'ap_obligation_components')


def stocked(books):
    """A bill that moves money and stock, so a delete has both to cancel."""
    item = _inventory_part(books)
    return books['run']('bill post', dict(vendor=books['vendor'], date='2017-03-03',
        supplier_reference='STOCK-1', memo='Stocked March bill',
        expenses=[{'account': books['freight'], 'amount': '4.83', 'memo': 'Delivery'}],
        items=[{'item': item, 'quantity': '2', 'unit_cost': '8.00'}]), reason='Enter the bill'), item


@pytest.mark.parametrize('state', ['posted', 'voided'])
def test_exact_bill_cancellation_without_post_authority(books, state):
    run = books['run']
    post, item = stocked(books)
    if state == 'voided':
        post = run('bill void', dict(bill=post['id'], expected_version=post['version']),
                   reason='Prior cancellation')
    version = run('bill show', dict(bill=post['id']))['version']
    path = location(books)
    enable(books, 'bill')
    raw = dict(bill=post['id'], expected_version=version, operation_key='permanent-bill-delete')
    before = database(path)
    with pytest.raises(BookflowError) as denied:
        run('bill void', dict(bill=post['id'], expected_version=version), reason='Posting denied')
    assert denied.value.code == 'E_PERMISSION'
    preview = run('bill delete', raw, reason='Remove duplicate bill', dry_run=True)
    assert preview['status'] == 'deleted' and preview['version'] == version + 1
    assert preview['from_status'] == state
    assert preview['cancelled_stock_movements'] == (0 if state == 'voided' else 1)
    assert preview['number'] == post['number'] and preview['purchase_order_id'] is None
    assert database(path) == before
    result = run('bill delete', raw, reason='Remove duplicate bill')
    assert result['status'] == 'deleted' and result['version'] == version + 1
    assert result['from_status'] == state
    # Nothing of this bill is left in the books, at any date.
    assert _net(books) == {}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM posting_batches WHERE transaction_id=? AND kind='reversal'",
                          (post['id'],)).fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM bill_deletions').fetchone() == (1,)
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements WHERE item_id=?',
                          (item,)).fetchone() == (0, 0)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    after = database(path)
    for table in RETAINED_TABLES:
        assert after['tables'][table] == before['tables'][table], table
    replay = run('bill delete', raw, reason='Remove duplicate bill')
    assert replay['idempotent_replay'] and not replay['changed']
    assert database(path) == after
    with pytest.raises(BookflowError) as mismatch:
        run('bill delete', raw, reason='A different reason under the same key')
    assert mismatch.value.code == 'E_IDEMPOTENCY_MISMATCH'
    assert database(path) == after


def test_a_settled_bill_refuses_and_names_the_settlement_holding_it(books):
    run = books['run']
    post = run('bill post', _bill(books), reason='Enter the March bill')
    payment = run('bill pay', dict(funding_account=books['bank'], method=books['methods']['Check'],
        date='2017-04-10', bills=[{'bill': post['id'], 'amount': '200.00'}]),
        reason='Pay part of it')['payments'][0]
    path = location(books)
    # Posting stays granted: the recovery this refusal names is itself a posting command,
    # so the test has to be able to take the step the message tells the person to take.
    enable(books, 'bill', deny_post=False)
    current = run('bill show', dict(bill=post['id']))['version']
    before = database(path)
    with pytest.raises(BookflowError) as refused:
        run('bill delete', dict(bill=post['id'], expected_version=current), reason='Remove duplicate bill')
    assert refused.value.code == 'E_HAS_APPLICATIONS'
    details = refused.value.details
    assert details['bill_id'] == post['id']
    assert details['settlement_transaction_ids'] == [payment['id']]
    assert len(details['application_ids']) == 1
    assert 'bill payment unapply' in details['next']
    # A refusal writes nothing at all, not even the cancellation it was preparing.
    assert database(path) == before
    run('bill payment unapply', dict(payment=payment['id'], expected_version=payment['version']),
        reason='Take the money back off this bill')
    released = run('bill show', dict(bill=post['id']))['version']
    assert run('bill delete', dict(bill=post['id'], expected_version=released),
               reason='Remove duplicate bill')['status'] == 'deleted'


def test_a_deleted_bill_leaves_ordinary_reads_and_stays_readable(books):
    run = books['run']
    kept = run('bill post', _bill(books), reason='Enter the March bill')
    gone = run('bill post', dict(_bill(books), supplier_reference='INV-7743', date='2017-03-04'),
               reason='Enter it a second time by mistake')
    assert {row['id'] for row in run('bill query', {})['items']} == {kept['id'], gone['id']}
    # Posting stays granted here: this is about what a deleted bill reads like, and the
    # retained-history refusal has to be the writer's own, not the permission check above it.
    enable(books, 'bill', deny_post=False)
    run('bill delete', dict(bill=gone['id'], expected_version=gone['version']),
        reason='Entered twice from the same invoice')
    assert {row['id'] for row in run('bill query', {})['items']} == {kept['id']}
    with_deleted = {row['id']: row for row in run('bill query', {'include_deleted': True})['items']}
    assert set(with_deleted) == {kept['id'], gone['id']}
    assert with_deleted[gone['id']]['status'] == 'deleted'
    assert with_deleted[gone['id']]['deletion']['reason'] == 'Entered twice from the same invoice'
    # It is off the payables reports because it is worth nothing, not because it is filtered.
    assert {row['transaction_id'] for row in run('report unpaid-bills', {'as_of': '2017-12-31'})['rows']} == {kept['id']}
    for name, raw in (('bill show', dict(bill=gone['id'])), ('bill history', dict(bill=gone['id']))):
        with pytest.raises(BookflowError) as hidden:
            run(name, raw)
        assert hidden.value.code == 'E_RECORD_NOT_FOUND', name
    shown = run('bill show', dict(bill=gone['id'], include_deleted=True))
    assert shown['status'] == 'deleted' and shown['number'] == gone['number']
    assert shown['deletion']['created_via'] == 'python' and shown['deletion']['from_status'] == 'posted'
    assert shown['revision']['total'] == gone['revision']['total']
    history = run('bill history', dict(bill=gone['id'], include_deleted=True))
    assert history['status'] == 'deleted' and history['count'] == 1
    # The register omits it; the general ledger still sums both of its immutable effects,
    # so hiding the document has not changed the report math.
    payable = dict(account=books['payable'], date_from='2017-01-01', date_to='2017-12-31')
    register = run('register query', dict(payable, limit=100))
    ledger = run('report general-ledger', dict(payable, limit=100))
    assert {row['transaction_id'] for row in register['rows'] if row['transaction_id']} == {kept['id']}
    assert sum(row['transaction_id'] == gone['id'] for row in ledger['rows']) == 2
    assert register['ledger_totals'] == ledger['totals']
    for name, raw in (('bill update', dict(bill=gone['id'], memo='Rewrite it')),
                      ('bill void', dict(bill=gone['id']))):
        with pytest.raises(BookflowError) as frozen:
            run(name, raw, reason='Edit retained history')
        assert frozen.value.code == 'E_VALIDATION', name
        assert 'retained history' in str(frozen.value.details), name


def test_an_applied_vendor_credit_blocks_deletion_the_same_way_a_payment_does(books):
    """The refusal is the obligation edge, not the kind of money that settled it."""
    run = books['run']
    post = run('bill post', _bill(books), reason='Enter the March bill')
    credit = run('vendor-credit post', dict(vendor=books['vendor'], date='2017-03-04',
        ap_account=books['payable'],
        expenses=[dict(account=books['parts'], amount='10.00')]), reason='Vendor credited us')
    run('vendor-credit apply', dict(credit=credit['id'], expected_version=credit['version'],
        date='2017-03-05', bills=[dict(bill=post['id'], amount='10.00')]),
        reason='Put the credit against this bill')
    path = location(books)
    enable(books, 'bill', deny_post=False)
    before = database(path)
    with pytest.raises(BookflowError) as refused:
        run('bill delete', dict(bill=post['id'], expected_version=run('bill show', dict(bill=post['id']))['version']),
            reason='Remove duplicate bill')
    assert refused.value.code == 'E_HAS_APPLICATIONS'
    assert refused.value.details['settlement_transaction_ids'] == [credit['id']]
    assert 'vendor-credit unapply' in refused.value.details['next']
    assert database(path) == before


def test_deleting_a_linked_bill_releases_the_received_lines_it_claimed(books):
    """Item receipts are owned handling, not a refusal: the claim goes back."""
    run = books['run']
    item = _inventory_part(books)
    receipt = run('item-receipt post', dict(date='2017-01-05', vendor=books['vendor'],
        shipping='12.00', items=[dict(item=item, quantity='3', unit_cost='8.00')]),
        reason='Receive three')
    selection = dict(receipt_line=receipt['items'][0]['id'], expected_receipt_version=1,
                     quantity='2', unit_cost='8.00')
    post = run('bill post', dict(date='2017-01-15', receipts=[selection]),
               reason='Bill the received lines')
    path = location(books)
    enable(books, 'bill', deny_post=False)
    preview = run('bill delete', dict(bill=post['id'], expected_version=post['version']),
                  reason='Billed the wrong receipt', dry_run=True)
    assert preview['released_receipt_claims'] == 1
    result = run('bill delete', dict(bill=post['id'], expected_version=post['version']),
                 reason='Billed the wrong receipt')
    assert result['released_receipt_claims'] == 1
    with sqlite3.connect(path) as db:
        held = db.execute('SELECT count(*) FROM receipt_bill_claims k WHERE k.bill_id=? '
                          'AND NOT EXISTS (SELECT 1 FROM receipt_bill_releases r WHERE r.claim_id=k.id)',
                          (post['id'],)).fetchone()
        assert held == (0,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    # The released interval is genuinely available again to a replacement bill.
    again = run('bill post', dict(date='2017-01-16', receipts=[
        dict(selection, expected_receipt_version=run('item-receipt show',
             dict(receipt=receipt['id']))['version'])]), reason='Bill it correctly')
    assert again['status'] == 'posted'


def test_co52_declaration_is_exact_and_storage_is_immutable(books):
    import importlib
    import re
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company import schema as c, bill_deletion_schema
    from bookflow.core.deletion_families import BILL_FAMILIES, TOMBSTONE_TABLE
    migration = importlib.import_module('bookflow.storage.company_migrations.versions.0052_bill_deletions')
    assert migration.revision == 'co0052' and migration.down_revision == 'co0051'
    assert migration.DDL == (str(CreateTable(c.bill_deletions).compile(dialect=dialect())),
                             *bill_deletion_schema.guards())
    check = next(x for x in c.bill_deletions.constraints if x.name == 'ck_bill_delete_family')
    assert tuple(re.findall("'([^']+)'", str(check.sqltext))) == BILL_FAMILIES == ('bill',)
    assert TOMBSTONE_TABLE['bill'] == 'bill_deletions'
    post, _ = stocked(books)
    path = location(books)
    enable(books, 'bill')
    books['run']('bill delete', dict(bill=post['id'], expected_version=post['version'],
                 operation_key='immutable'), reason='Remove duplicate bill')
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for sql in ('DELETE FROM bill_deletions', 'UPDATE bill_deletions SET reason=reason',
                    'UPDATE transactions SET version=version WHERE id=?'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                db.execute(sql, (post['id'],) if '?' in sql else ())
            db.rollback()


def test_the_owner_trigger_refuses_a_tombstone_the_settlement_graph_does_not_support(books):
    """The storage is the proof, independently of the refusal the owner raises above it."""
    run = books['run']
    post = run('bill post', _bill(books), reason='Enter the March bill')
    run('bill pay', dict(funding_account=books['bank'], method=books['methods']['Check'],
        date='2017-04-10', bills=[{'bill': post['id'], 'amount': '200.00'}]), reason='Pay part of it')
    path = location(books)
    enable(books, 'bill')
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        row = db.execute('SELECT id,current_revision_id,version FROM transactions WHERE id=?',
                         (post['id'],)).fetchone()
        event = db.execute('SELECT id FROM audit_events LIMIT 1').fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match='owner mismatch'):
            db.execute('INSERT INTO bill_deletions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (row[0], 'bill', row[1], 'posted', row[2], row[2] + 1, None, 'forced',
                        'x', '{}', '{}', '2017-04-11T00:00:00Z', 'A', None, 'python', 'Forced', event))
        db.rollback()


def test_tombstone_failure_rolls_the_whole_deletion_back(books, monkeypatch):
    from bookflow.company import bill_deletions
    post, _ = stocked(books)
    path = location(books)
    enable(books, 'bill')
    before = database(path)
    original = bill_deletions.persist_tombstone

    def fail(s, row):
        original(s, row)
        raise BookflowError('E_VALIDATION', message='Injected after bill tombstone')
    monkeypatch.setattr(bill_deletions, 'persist_tombstone', fail)
    with pytest.raises(BookflowError, match='Injected'):
        books['run']('bill delete', dict(bill=post['id'], expected_version=post['version']),
                     reason='Rollback witness')
    assert database(path) == before
