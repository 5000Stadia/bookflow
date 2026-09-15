"""Credit-memo deletion: exact reversal, named refusals and permanent receipts.

The money, written out so a reader can add it up without running anything: a 100.00 invoice
of four taxable units at 25.00, and credits of 30.00 against it. Everything asserted below is
arithmetic on those figures through the real report commands.
"""
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from tests.credit_support import (  # noqa: F401  (books is a fixture)
    apply_credit, balances, books, goodwill_credit, invoice, refund, returned_credit,
    taxed_invoice, worth_version,
)
from tests.payment_raw_evidence import database

RETAINED_TABLES = ('transaction_revisions', 'document_lines', 'document_line_identities',
                   'credit_profiles', 'credit_line_profiles', 'credit_tax_components',
                   'credit_source_keys', 'credit_components')


def enable(books, *, deny_post=True, grants=None):
    """Grant the exact family Delete and nothing else, taking posting away by default."""
    client = books['client']
    state = client.permission.show()
    client.permission.activate(expected_generation=state['generation'],
                               expected_catalog_sha256=state['catalog_sha256'])
    rows = client.membership.list(company=books['company'])['items']
    member = next(x for x in rows if x['scope_type'] == 'company' and x['scope_id'] == books['company'])
    client.membership.grant(user=member['user_id'], company=books['company'],
                            expected_version=member['version'],
                            grants=grants or ['transaction.credit_memo.delete'],
                            denies=['ledger.post'] if deny_post else [])


def location(books):
    return books['database']


@pytest.mark.parametrize('state', ['posted', 'voided'])
def test_exact_credit_cancellation_without_post_authority(books, state):
    run = books['run']
    sale = taxed_invoice(books)
    before_any, _ = balances(books)
    credit = returned_credit(books, sale['id'], sale['revision']['lines'][0]['line_id'])
    if state == 'voided':
        credit = run('credit-memo void', dict(credit_memo=credit['id'],
                     expected_version=credit['version']), reason='Prior cancellation')
    version = run('credit-memo show', dict(credit_memo=credit['id']))['version']
    path = location(books)
    enable(books)
    raw = dict(credit_memo=credit['id'], expected_version=version,
               operation_key='permanent-credit-delete')
    before = database(path)
    with pytest.raises(BookflowError) as denied:
        run('credit-memo void', dict(credit_memo=credit['id'], expected_version=version),
            reason='Posting denied')
    assert denied.value.code == 'E_PERMISSION'
    preview = run('credit-memo delete', raw, reason='Credited the wrong customer', dry_run=True)
    assert preview['status'] == 'deleted' and preview['version'] == version + 1
    assert preview['from_status'] == state
    assert preview['number'] == credit['number']
    # A live return claim is owned handling, not a refusal: the interval goes back.
    assert preview['released_source_claims'] == (0 if state == 'voided' else 1)
    assert preview['source_invoice_ids'] == ([] if state == 'voided' else [sale['id']])
    assert database(path) == before
    result = run('credit-memo delete', raw, reason='Credited the wrong customer')
    assert result['status'] == 'deleted' and result['version'] == version + 1
    assert result['from_status'] == state
    # Nothing of this credit is left in the books, at any date: the trial balance is what
    # it was before the credit was ever written, cent for cent.
    after_balances, _ = balances(books)
    assert after_balances == before_any
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM posting_batches WHERE transaction_id=? AND kind='reversal'",
                          (credit['id'],)).fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM credit_deletions').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM credit_source_claims c WHERE c.credit_transaction_id=? '
                          "AND c.kind='claim' AND NOT EXISTS (SELECT 1 FROM credit_source_claims r "
                          'WHERE r.reverses_claim_id=c.id)', (credit['id'],)).fetchone() == (0,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    after = database(path)
    for table in RETAINED_TABLES:
        assert after['tables'][table] == before['tables'][table], table
    replay = run('credit-memo delete', raw, reason='Credited the wrong customer')
    assert replay['idempotent_replay'] and not replay['changed']
    assert database(path) == after
    with pytest.raises(BookflowError) as mismatch:
        run('credit-memo delete', raw, reason='A different reason under the same key')
    assert mismatch.value.code == 'E_IDEMPOTENCY_MISMATCH'
    assert database(path) == after


def test_an_applied_credit_refuses_and_names_the_invoice_holding_it(books):
    run = books['run']
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    apply_credit(books, credit, sale['id'], amount='30.00')
    path = location(books)
    # Posting stays granted: the recovery this refusal names is itself a posting command,
    # so the test has to be able to take the step the message tells the person to take.
    enable(books, deny_post=False)
    current = worth_version(books, credit['id'])
    before = database(path)
    with pytest.raises(BookflowError) as refused:
        run('credit-memo delete', dict(credit_memo=credit['id'], expected_version=current),
            reason='Credited the wrong customer')
    assert refused.value.code == 'E_HAS_APPLICATIONS'
    details = refused.value.details
    assert details['credit_memo_id'] == credit['id']
    assert details['invoice_ids'] == [sale['id']]
    assert len(details['application_ids']) == 1
    assert 'customer-credit unapply' in details['next']
    # A refusal writes nothing at all, not even the cancellation it was preparing.
    assert database(path) == before
    run('customer-credit unapply', dict(
        credit_memo=credit['id'], expected_version=worth_version(books, credit['id']),
        applications=[{'application_id': details['application_ids'][0],
                       'invoice_expected_version': run('invoice show', {'invoice': sale['id']})['version']}]),
        reason='Take the credit back off')
    released = worth_version(books, credit['id'])
    assert run('credit-memo delete', dict(credit_memo=credit['id'], expected_version=released),
               reason='Credited the wrong customer')['status'] == 'deleted'


def test_a_refunded_credit_refuses_and_names_the_refund_drawn_on_it(books):
    """The refusal is the capacity edge, not the kind of claim that spent it."""
    run = books['run']
    credit = goodwill_credit(books, '30.00')
    paid = refund(books, credit['id'], amount='30.00')
    path = location(books)
    enable(books, deny_post=False)
    before = database(path)
    with pytest.raises(BookflowError) as refused:
        run('credit-memo delete', dict(credit_memo=credit['id'],
            expected_version=worth_version(books, credit['id'])), reason='Refunded in error')
    assert refused.value.code == 'E_HAS_REFUND'
    details = refused.value.details
    assert details['credit_memo_id'] == credit['id']
    assert details['refund_ids'] == [paid['id']]
    assert len(details['consumption_ids']) == 1
    assert 'customer-refund void' in details['next']
    assert database(path) == before
    run('customer-refund void', dict(refund=paid['id'], expected_version=paid['version']),
        reason='Undo the refund')
    assert run('credit-memo delete', dict(credit_memo=credit['id'],
               expected_version=worth_version(books, credit['id'])),
               reason='Refunded in error')['status'] == 'deleted'


def test_a_deleted_credit_leaves_ordinary_reads_and_stays_readable(books):
    run = books['run']
    kept = goodwill_credit(books, '10.00')
    gone = goodwill_credit(books, '30.00', date='2026-03-11')
    assert {row['id'] for row in run('credit-memo query', {})['items']} == {kept['id'], gone['id']}
    # Posting stays granted here: this is about what a deleted credit reads like, and the
    # retained-history refusal has to be the writer's own, not the permission check above it.
    enable(books, deny_post=False)
    run('credit-memo delete', dict(credit_memo=gone['id'], expected_version=gone['version']),
        reason='Entered twice from the same complaint')
    assert {row['id'] for row in run('credit-memo query', {})['items']} == {kept['id']}
    # It is off the "what has this customer got in hand" list too.
    assert {row['id'] for row in run('credit-memo query', {'available_only': True})['items']} == {kept['id']}
    with_deleted = {row['id']: row for row in run('credit-memo query', {'include_deleted': True})['items']}
    assert set(with_deleted) == {kept['id'], gone['id']}
    assert with_deleted[gone['id']]['status'] == 'deleted'
    assert with_deleted[gone['id']]['deletion']['reason'] == 'Entered twice from the same complaint'
    for name, raw in (('credit-memo show', dict(credit_memo=gone['id'])),
                      ('credit-memo history', dict(credit_memo=gone['id']))):
        with pytest.raises(BookflowError) as hidden:
            run(name, raw)
        assert hidden.value.code == 'E_RECORD_NOT_FOUND', name
    shown = run('credit-memo show', dict(credit_memo=gone['id'], include_deleted=True))
    assert shown['status'] == 'deleted' and shown['number'] == gone['number']
    assert shown['deletion']['created_via'] == 'python' and shown['deletion']['from_status'] == 'posted'
    assert shown['revision']['total'] == gone['revision']['total']
    history = run('credit-memo history', dict(credit_memo=gone['id'], include_deleted=True))
    assert history['status'] == 'deleted' and history['count'] == 1
    # The register omits it; the general ledger still sums both of its immutable effects,
    # so hiding the document has not changed the report math.
    receivable = dict(account=books['receivable'], date_from='2026-01-01', date_to='2026-12-31')
    register = run('register query', dict(receivable, limit=100))
    ledger = run('report general-ledger', dict(receivable, limit=100))
    assert {row['transaction_id'] for row in register['rows'] if row['transaction_id']} == {kept['id']}
    assert sum(row['transaction_id'] == gone['id'] for row in ledger['rows']) == 2
    assert register['ledger_totals'] == ledger['totals']
    # Every writer that could change or spend it refuses with the same retained-history
    # message, whichever direction it comes from.
    for name, raw in (('credit-memo update', dict(credit_memo=gone['id'], memo='Rewrite it')),
                      ('credit-memo void', dict(credit_memo=gone['id']))):
        with pytest.raises(BookflowError) as frozen:
            run(name, raw, reason='Edit retained history')
        assert frozen.value.code == 'E_VALIDATION', name
        assert 'retained history' in str(frozen.value.details), name
    sale = invoice(books)
    with pytest.raises(BookflowError) as spent:
        run('customer-credit apply', dict(credit_memo=gone['id'], expected_version=gone['version'] + 1,
            applications=[{'invoice': sale['id'],
                           'expected_version': run('invoice show', {'invoice': sale['id']})['version'],
                           'amount': '10.00'}]), reason='Spend retained history')
    assert spent.value.code == 'E_VALIDATION' and 'retained history' in str(spent.value.details)


def test_deleting_a_returned_credit_gives_the_invoice_quantity_back(books):
    """Its own claims are owned handling, not a refusal: the interval returns to the invoice."""
    run = books['run']
    sale = taxed_invoice(books)
    line = sale['revision']['lines'][0]['line_id']
    credit = returned_credit(books, sale['id'], line, quantity='4')
    path = location(books)
    enable(books, deny_post=False)
    # Everything is claimed, so a second return of the same line is refused outright.
    with pytest.raises(BookflowError) as exhausted:
        returned_credit(books, sale['id'], line, quantity='1', date='2026-04-06')
    assert exhausted.value.code == 'E_RETURN_EXHAUSTED'
    result = run('credit-memo delete', dict(credit_memo=credit['id'],
                 expected_version=credit['version']), reason='Returned against the wrong sale')
    assert result['released_source_claims'] == 1 and result['source_invoice_ids'] == [sale['id']]
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    # The released interval is genuinely available again to a replacement credit.
    again = returned_credit(books, sale['id'], line, quantity='4', date='2026-04-06')
    assert again['status'] == 'posted'


def test_co53_declaration_is_exact_and_storage_is_immutable(books):
    import importlib
    import re
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company import schema as c, credit_deletion_schema
    from bookflow.core.deletion_families import CREDIT_FAMILIES, TOMBSTONE_TABLE
    migration = importlib.import_module('bookflow.storage.company_migrations.versions.0053_credit_deletions')
    assert migration.revision == 'co0053' and migration.down_revision == 'co0052'
    assert migration.DDL == (str(CreateTable(c.credit_deletions).compile(dialect=dialect())),
                             *credit_deletion_schema.guards())
    check = next(x for x in c.credit_deletions.constraints if x.name == 'ck_credit_delete_family')
    assert tuple(re.findall("'([^']+)'", str(check.sqltext))) == CREDIT_FAMILIES == ('credit_memo',)
    assert TOMBSTONE_TABLE['credit_memo'] == 'credit_deletions'
    credit = goodwill_credit(books, '30.00')
    path = location(books)
    enable(books)
    books['run']('credit-memo delete', dict(credit_memo=credit['id'],
                 expected_version=credit['version'], operation_key='immutable'),
                 reason='Credited the wrong customer')
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for sql in ('DELETE FROM credit_deletions', 'UPDATE credit_deletions SET reason=reason',
                    'UPDATE transactions SET version=version WHERE id=?'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                db.execute(sql, (credit['id'],) if '?' in sql else ())
            db.rollback()


def test_the_owner_trigger_refuses_a_tombstone_the_settlement_graph_does_not_support(books):
    """The storage is the proof, independently of the refusal the owner raises above it."""
    run = books['run']
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    apply_credit(books, credit, sale['id'], amount='30.00')
    path = location(books)
    enable(books)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        row = db.execute('SELECT id,current_revision_id,version FROM transactions WHERE id=?',
                         (credit['id'],)).fetchone()
        event = db.execute('SELECT id FROM audit_events LIMIT 1').fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match='owner mismatch'):
            db.execute('INSERT INTO credit_deletions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (row[0], 'credit_memo', row[1], 'posted', row[2], row[2] + 1, None, 'forced',
                        'x', '{}', '{}', '2026-03-11T00:00:00Z', 'A', None, 'python', 'Forced', event))
        db.rollback()


def test_tombstone_failure_rolls_the_whole_deletion_back(books, monkeypatch):
    from bookflow.company import credit_deletions
    credit = goodwill_credit(books, '30.00')
    path = location(books)
    enable(books)
    before = database(path)
    original = credit_deletions.persist_tombstone

    def fail(s, row):
        original(s, row)
        raise BookflowError('E_VALIDATION', message='Injected after credit tombstone')
    monkeypatch.setattr(credit_deletions, 'persist_tombstone', fail)
    with pytest.raises(BookflowError, match='Injected'):
        books['run']('credit-memo delete', dict(credit_memo=credit['id'],
                     expected_version=credit['version']), reason='Rollback witness')
    assert database(path) == before
