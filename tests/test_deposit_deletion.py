"""Deposit deletion: exact reversal, returned receipts and permanent receipts of its own.

The money, written out so a reader can add it up without running anything: a 100.00 customer
payment and a 60.00 counter sale, banked together as one 160.00 deposit.
"""
import sqlite3
from pathlib import Path

import pytest

import bookflow
from bookflow.core.errors import BookflowError
from tests.payment_raw_evidence import database
from tests.test_deposit_command import (  # noqa: F401  (books is a fixture)
    COMPANY, balances, books, receipts_in_undeposited_funds,
)

RETAINED_TABLES = ('transaction_revisions', 'deposit_profiles', 'deposit_row_keys',
                   'deposit_component_keys', 'deposit_components', 'deposit_cash_cells',
                   'document_lines', 'document_line_identities')


def enable(books, *, deny_post=True):
    """Grant the exact family Delete and nothing else, taking posting away by default."""
    client = books['client']
    state = client.permission.show()
    client.permission.activate(expected_generation=state['generation'],
                               expected_catalog_sha256=state['catalog_sha256'])
    company = client.company.show(company=COMPANY)['company_id']
    rows = client.membership.list(company=company)['items']
    member = next(x for x in rows if x['scope_type'] == 'company' and x['scope_id'] == company)
    client.membership.grant(user=member['user_id'], company=company,
                            expected_version=member['version'],
                            grants=['transaction.deposit.delete'],
                            denies=['ledger.post'] if deny_post else [])
    return company


def location(books):
    return Path(books['client'].company.show(company=COMPANY)['path']) / 'company.db'


def banked(books, key='june-deposit'):
    client = books['client']
    _, payment, sale = receipts_in_undeposited_funds(books)
    available = client.run('deposit sources', dict(date='2026-06-03'), company=COMPANY)
    document = dict(mode='inline', deposit_to=books['bank'], date='2026-06-03',
                    sources=[dict(source_type=row['source_type'], source=row['source'],
                                  expected_version=row['expected_version'])
                             for row in available['items']])
    posted = client.run('deposit post', dict(operation_key=key, document=document),
                        company=COMPANY, reason='bank Saturday receipts')
    return posted, payment, sale, document


@pytest.mark.parametrize('state', ['posted', 'voided'])
def test_exact_deposit_cancellation_without_post_authority(books, state):
    client = books['client']
    _, before_any = balances(client)
    posted, payment, sale, _ = banked(books)
    identity = posted['deposit']['id']
    if state == 'voided':
        void = dict(deposit=identity, expected_version=posted['deposit']['version'],
                    operation_key='prior-void')
        guard = client.run('deposit void', void, company=COMPANY, reason='Prior cancellation',
                           dry_run=True)['dependency_guard']
        posted = client.run('deposit void', dict(void, dependency_guard=guard),
                            company=COMPANY, reason='Prior cancellation')
    version = posted['deposit']['version']
    path = location(books)
    enable(books)
    before = database(path)

    # Posting authority is gone, and the ordinary cancellation with it.
    with pytest.raises(BookflowError) as denied:
        client.run('deposit void', dict(deposit=identity, expected_version=version,
                   operation_key='denied-void'), company=COMPANY, reason='Posting denied')
    assert denied.value.code == 'E_PERMISSION'

    raw = dict(deposit=identity, expected_version=version, operation_key='permanent-deposit-delete')
    preview = client.run('deposit delete', raw, company=COMPANY,
                         reason='Banked into the wrong account', dry_run=True)
    assert preview['status'] == 'deleted' and preview['version'] == version + 1
    assert preview['from_status'] == state and preview['number'] == posted['deposit']['number']
    assert preview['released_receipt_ids'] == (
        [] if state == 'voided' else sorted([payment['id'], sale['id']]))
    assert database(path) == before

    saved = client.run('deposit delete', dict(raw, dependency_guard=preview['dependency_guard']),
                       company=COMPANY, reason='Banked into the wrong account')
    assert saved['status'] == 'deleted' and saved['version'] == version + 1
    assert saved['from_status'] == state

    # Nothing of this deposit is left in the books, at any date: the trial balance is what
    # it was before the deposit was ever written, cent for cent.
    totals, after_balances = balances(client)
    assert totals['debit'] == totals['credit'] and totals['signed_net']['minor_units'] == 0
    assert after_balances[books['bank']] == 0, 'the bank never had it'
    assert after_balances[books['uf']] == 16000, 'the receipts are undeposited again, still posted'
    assert after_balances[books['income']] == before_any[books['income']] - 16000

    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM deposit_deletions').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM deposit_current_memberships WHERE transaction_id=?',
                          (identity,)).fetchone() == (0,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    after = database(path)
    for table in RETAINED_TABLES:
        assert after['tables'][table] == before['tables'][table], table

    # Permanent retry: the same key returns the original result and never acts twice --
    # including with the fresh dependency guard a second preview would legitimately mint.
    replay = client.run('deposit delete', dict(raw, dependency_guard='stale-guard-value'),
                        company=COMPANY, reason='Banked into the wrong account')
    assert replay['idempotent_replay'] and not replay['changed']
    assert replay['id'] == identity and replay['version'] == version + 1
    assert database(path) == after
    with pytest.raises(BookflowError) as mismatch:
        client.run('deposit delete', raw, company=COMPANY, reason='A different reason, same key')
    assert mismatch.value.code == 'E_IDEMPOTENCY_MISMATCH'
    assert database(path) == after


def test_the_receipts_a_deleted_deposit_banked_come_back_and_can_be_banked_again(books):
    """Owned handling, not a cascade: every receipt stays posted and its own document."""
    client = books['client']
    posted, payment, sale, document = banked(books)
    identity = posted['deposit']['id']
    enable(books, deny_post=False)
    raw = dict(deposit=identity, expected_version=posted['deposit']['version'],
               operation_key='freeing-delete')
    preview = client.run('deposit delete', raw, company=COMPANY, reason='Wrong bank', dry_run=True)
    client.run('deposit delete', dict(raw, dependency_guard=preview['dependency_guard']),
               company=COMPANY, reason='Wrong bank')

    freed = client.run('deposit sources', dict(date='2026-06-04'), company=COMPANY)
    assert {row['source'] for row in freed['items']} == {payment['id'], sale['id']}
    assert all(not row['deposited'] and row['eligible'] for row in freed['items'])
    again = dict(mode='inline', deposit_to=books['bank'], date='2026-06-04',
                 sources=[dict(source_type=row['source_type'], source=row['source'],
                               expected_version=row['expected_version']) for row in freed['items']])
    replacement = client.run('deposit post', dict(operation_key='second-deposit', document=again),
                             company=COMPANY, reason='Bank them into the right account')
    assert replacement['deposit']['status'] == 'posted'
    assert sorted(replacement['deposit']['banked_receipt_ids']) == sorted([payment['id'], sale['id']])


def test_a_deleted_deposit_leaves_ordinary_reads_and_stays_readable(books):
    client = books['client']
    kept, _, _, _ = banked(books, key='kept-deposit')
    client.run('sales-receipt post', dict(customer=books['customer'], deposit_to=books['uf'],
               payment_method=books['method'], date='2026-06-05',
               lines=[dict(item=books['item'], quantity='1', unit_price='25')]),
               company=COMPANY, reason='another counter sale')
    spare = client.run('deposit sources', dict(date='2026-06-06'), company=COMPANY)['items']
    gone = client.run('deposit post', dict(operation_key='gone-deposit', document=dict(
        mode='inline', deposit_to=books['bank'], date='2026-06-06',
        sources=[dict(source_type=row['source_type'], source=row['source'],
                      expected_version=row['expected_version']) for row in spare])),
        company=COMPANY, reason='bank it twice by mistake')
    identity = gone['deposit']['id']
    listed = client.run('deposit query', {}, company=COMPANY)
    assert {row['selected']['pin']['deposit_id'] for row in listed['items']} == {
        kept['deposit']['id'], identity}

    enable(books, deny_post=False)
    raw = dict(deposit=identity, expected_version=gone['deposit']['version'],
               operation_key='hide-me')
    preview = client.run('deposit delete', raw, company=COMPANY,
                         reason='Entered twice from one paying-in slip', dry_run=True)
    client.run('deposit delete', dict(raw, dependency_guard=preview['dependency_guard']),
               company=COMPANY, reason='Entered twice from one paying-in slip')

    ordinary = client.run('deposit query', {}, company=COMPANY)
    assert {row['selected']['pin']['deposit_id'] for row in ordinary['items']} == {kept['deposit']['id']}
    with_deleted = client.run('deposit query', dict(include_deleted=True), company=COMPANY)
    rows = {row['selected']['pin']['deposit_id']: row for row in with_deleted['items']}
    assert set(rows) == {kept['deposit']['id'], identity}
    assert rows[identity]['current']['status'] == 'deleted'
    only = client.run('deposit query', dict(status='deleted'), company=COMPANY)
    assert [row['selected']['pin']['deposit_id'] for row in only['items']] == [identity]

    for name, raw_read in (('deposit show', dict(deposit=identity)),
                           ('deposit items', dict(deposit=identity, kind='sources')),
                           ('deposit history', dict(deposit=identity))):
        with pytest.raises(BookflowError) as hidden:
            client.run(name, raw_read, company=COMPANY)
        assert hidden.value.code == 'E_RECORD_NOT_FOUND', name
    shown = client.run('deposit show', dict(deposit=identity, include_deleted=True), company=COMPANY)
    assert shown['current']['status'] == 'deleted'
    assert shown['selected']['number'] == gone['deposit']['number']
    history = client.run('deposit history', dict(deposit=identity, include_deleted=True), company=COMPANY)
    entry = next(row for row in history['items'] if row['kind'] == 'deleted')
    assert entry['reason'] == 'Entered twice from one paying-in slip'

    # The register omits it; the general ledger still sums both of its immutable effects,
    # so hiding the document has not changed the report math.
    window = dict(account=books['bank'], date_from='2026-01-01', date_to='2026-12-31')
    register = client.run('register query', dict(window, limit=100), company=COMPANY)
    ledger = client.run('report general-ledger', dict(window, limit=100), company=COMPANY)
    assert identity not in {row['transaction_id'] for row in register['rows'] if row['transaction_id']}
    assert sum(row['transaction_id'] == identity for row in ledger['rows']) == 2
    assert register['ledger_totals'] == ledger['totals']

    # The retained-history fence sits in the deposit aggregate's own preparation, which
    # every write verb comes through the moment it resolves the document, so voiding is
    # the whole witness: update and coordinate reach the same line on the same object.
    with pytest.raises(BookflowError) as frozen:
        client.run('deposit void', dict(deposit=identity, expected_version=2,
                   operation_key='edit-history'), company=COMPANY, reason='Edit retained history')
    assert frozen.value.code == 'E_VALIDATION'
    assert 'retained history' in str(frozen.value.details)


def test_co54_declaration_is_exact_and_storage_is_immutable(books):
    import importlib
    import re
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company import schema as c, deposit_deletion_schema
    from bookflow.core.deletion_families import DEPOSIT_FAMILIES, TOMBSTONE_TABLE
    migration = importlib.import_module('bookflow.storage.company_migrations.versions.0054_deposit_deletions')
    assert migration.revision == 'co0054' and migration.down_revision == 'co0053'
    assert migration.DDL == (str(CreateTable(c.deposit_deletions).compile(dialect=dialect())),
                             *deposit_deletion_schema.guards())
    check = next(x for x in c.deposit_deletions.constraints if x.name == 'ck_deposit_delete_family')
    assert tuple(re.findall("'([^']+)'", str(check.sqltext))) == DEPOSIT_FAMILIES == ('deposit',)
    assert TOMBSTONE_TABLE['deposit'] == 'deposit_deletions'

    client = books['client']
    posted, _, _, _ = banked(books)
    identity = posted['deposit']['id']
    path = location(books)
    enable(books)
    raw = dict(deposit=identity, expected_version=posted['deposit']['version'],
               operation_key='immutable')
    preview = client.run('deposit delete', raw, company=COMPANY, reason='Wrong bank', dry_run=True)
    client.run('deposit delete', dict(raw, dependency_guard=preview['dependency_guard']),
               company=COMPANY, reason='Wrong bank')
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for sql in ('DELETE FROM deposit_deletions', 'UPDATE deposit_deletions SET reason=reason',
                    'UPDATE transactions SET version=version WHERE id=?'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                db.execute(sql, (identity,) if '?' in sql else ())
            db.rollback()


def test_the_owner_trigger_refuses_a_tombstone_that_still_holds_a_receipt(books):
    """The storage is the proof, independently of what the writer above it released."""
    client = books['client']
    posted, _, _, _ = banked(books)
    identity = posted['deposit']['id']
    path = location(books)
    enable(books)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        row = db.execute('SELECT id,current_revision_id,version FROM transactions WHERE id=?',
                         (identity,)).fetchone()
        event = db.execute('SELECT id FROM audit_events LIMIT 1').fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match='owner mismatch'):
            db.execute('INSERT INTO deposit_deletions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (row[0], 'deposit', row[1], 'posted', row[2], row[2] + 1, None, None,
                        'forced', 'x', '{}', '{}', '2026-06-04T00:00:00Z', 'A', None, 'python',
                        'Forced', event))
        db.rollback()


def test_tombstone_failure_rolls_the_whole_deletion_back(books, monkeypatch):
    from bookflow.company import deposit_deletions
    client = books['client']
    posted, _, _, _ = banked(books)
    identity = posted['deposit']['id']
    path = location(books)
    enable(books)
    before = database(path)
    original = deposit_deletions.persist_tombstone

    def fail(s, row):
        original(s, row)
        raise BookflowError('E_VALIDATION', message='Injected after deposit tombstone')
    raw = dict(deposit=identity, expected_version=posted['deposit']['version'],
               operation_key='rollback-witness')
    preview = client.run('deposit delete', raw, company=COMPANY, reason='Rollback witness', dry_run=True)
    monkeypatch.setattr(deposit_deletions, 'persist_tombstone', fail)
    with pytest.raises(BookflowError, match='Injected'):
        client.run('deposit delete', dict(raw, dependency_guard=preview['dependency_guard']),
                   company=COMPANY, reason='Rollback witness')
    assert database(path) == before


def test_co54_rebuild_preserves_every_stored_operation_value(tmp_path, monkeypatch):
    """The widened CHECK is a table rebuild, so the rows are the thing to prove.

    A deposit and its void are banked at co0053, where the operation ledger's frozen CHECK
    still refuses `deposit delete`. The upgrade to co0054 rebuilds that table to admit the
    new verb; every stored raw value in the whole database is compared before and after,
    and only then is a deletion recorded against it.
    """
    from bookflow.storage import migrate
    from tests.payment_raw_evidence import table
    with monkeypatch.context() as historical:
        historical.setitem(migrate.HEADS, 'company', 'co0053')
        b = books.__wrapped__(tmp_path, historical)
        posted, _, _, _ = banked(b, key='historical-deposit')
        identity = posted['deposit']['id']
        void = dict(deposit=identity, expected_version=posted['deposit']['version'],
                    operation_key='historical-void')
        guard = b['client'].run('deposit void', void, company=COMPANY,
                                reason='Historical cancellation', dry_run=True)['dependency_guard']
        posted = b['client'].run('deposit void', dict(void, dependency_guard=guard), company=COMPANY,
                                 reason='Historical cancellation')
        path = location(b)
        with sqlite3.connect(path) as db:
            commands = {row[0] for row in db.execute('SELECT command FROM deposit_operations')}
        assert commands == {'deposit post', 'deposit void'}
        current = posted['deposit']['version']
        enable(b)
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(tmp_path / 'root'))
    before = database(path)
    observed = []
    original = migrate.migrate_to_head

    def observing(db, chain, *args, **kwargs):
        result = original(db, chain, *args, **kwargs)
        if chain == 'company' and result == ('co0053', 'co0054'):
            for name, rows in before['tables'].items():
                if name != 'alembic_version':
                    assert table(db.raw, name) == rows, name
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
            observed.append(result)
        return result
    monkeypatch.setattr(migrate, 'migrate_to_head', observing)
    # The upgrade runs on the first write that needs it; a dry run migrates nothing, and
    # `deposit delete` refuses `E_SCHEMA_BEHIND` until its own storage exists. So an
    # ordinary write opens the door, and the preservation is asserted as it goes through.
    b['client'].customer.create(name='Post-upgrade customer', company=COMPANY)
    assert observed == [('co0053', 'co0054')]
    raw = dict(deposit=identity, expected_version=current, operation_key='co53-first')
    preview = b['client'].run('deposit delete', raw, company=COMPANY,
                              reason='First keyed deposit deletion', dry_run=True)
    result = b['client'].run('deposit delete', dict(raw, dependency_guard=preview['dependency_guard']),
                             company=COMPANY, reason='First keyed deposit deletion')
    assert result['status'] == 'deleted' and result['from_status'] == 'voided'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM deposit_operations '
                          "WHERE command='deposit delete'").fetchone() == (1,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
