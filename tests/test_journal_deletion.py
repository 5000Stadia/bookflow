"""Journal-entry deletion, and the one obstacle this family has that no other does.

A check, a credit card charge and a transfer are all *stored* as
``transactions.type = 'journal_entry'`` and are told apart only by a row in
``money_out_documents``. Two of the three already delete through ``purchase_deletions``, so a
journal delete that accepted one would write a second, conflicting tombstone on a document
that already carries one. The alias witness below is what holds that shut: a real cheque,
attempted as a journal entry, refused by name with the whole database unchanged, and then
deleted properly through its own command.
"""
from pathlib import Path
import sqlite3

import pytest

import bookflow
from bookflow.core.errors import BookflowError
from tests.payment_raw_evidence import database

DELETE = 'transaction.journal_entry.delete'
# What a deletion must leave exactly as it found it: the original entry, its revisions, its
# lines and the batch it posted. Deletion cancels; it never erases.
RETAINED_TABLES = ('transaction_revisions', 'document_lines', 'document_line_identities')


@pytest.fixture(scope='module')
def ledger(tmp_path_factory):
    """One migrated company for this module, so the evidence is only this module's."""
    with pytest.MonkeyPatch.context() as patch:
        root = tmp_path_factory.mktemp('journal-deletion')
        patch.setenv('BOOKFLOW_DATA_ROOT', str(root))
        patch.delenv('BOOKFLOW_COMPANY', raising=False)
        client = bookflow.connect(data_root=str(root))
        client.init()
        client.organization.new(name='Journal deletion organization')
        company = client.company.new(legal_name='Journal deletion', home_currency='USD',
            timezone='UTC', organization='Journal deletion organization', chart='general')['company_id']
        rows = client.account.query(company=company, limit=200)['items']
        bank = next(row['id'] for row in rows if row['type'] == 'bank')
        card = client.account.create(company=company, name='Company Card', type='credit_card')['id']
        savings = client.account.create(company=company, name='Savings', type='bank')['id']
        freight = client.account.create(company=company, name='Freight In', type='expense')['id']

        def run(name, raw, **context):
            return client.run(name, raw, company=company, **context)

        state = client.permission.show()
        client.permission.activate(expected_generation=state['generation'],
                                   expected_catalog_sha256=state['catalog_sha256'])
        books = dict(client=client, company=company, bank=bank, card=card, savings=savings,
                     freight=freight, run=run)

        def grant(*, delete=True, post=True, extra=()):
            """Re-grant against the membership's live version; this company is shared here.

            The Delete capabilities carry no role default at all, so the explicit grant is
            still what admits the command -- the role only keeps this one login able to
            administer the membership it is editing.
            """
            member = next(row for row in client.membership.list(company=company)['items']
                          if row['scope_type'] == 'company' and row['scope_id'] == company)
            client.membership.grant(user=member['user_id'], company=company, role='owner',
                expected_version=member['version'],
                grants=([DELETE] if delete else []) + list(extra),
                denies=[] if post else ['ledger.post'])

        books['grant'] = grant
        grant()
        yield books


@pytest.fixture
def admitted():
    """Nothing to stand in for any more: `journal-deletion-v1` carries the descriptor.

    This stood in for dispatch's activation gate while this family's catalog delta was
    unwritten, and a test beside it pinned exactly what was being skipped. The delta
    landed in `permission_journal_deletion_catalog`, that test went with it, and every
    test below now goes through the real gate. The fixture stays so those tests keep
    saying out loud that an activated catalog is a precondition of this family.
    """


def location(ledger):
    return Path(ledger['client'].company.show(company=ledger['company'])['path']) / 'company.db'


def entry(ledger, *, memo='Plain entry', amount='40.00', date='2017-03-03'):
    """A hand-typed journal entry: no marker of any kind, so it is only ever itself."""
    return ledger['run']('journal post', dict(date=date, memo=memo, lines=[
        dict(account=ledger['freight'], side='debit', amount=amount),
        dict(account=ledger['bank'], side='credit', amount=amount)]), reason='Enter the entry')


def cheque(ledger, *, amount='25.00', date='2017-03-04'):
    """A cheque with expense lines and no item grid -- the case every other guard misses."""
    return ledger['run']('check post', dict(account=ledger['bank'], date=date, amount=amount,
        expenses=[dict(account=ledger['freight'], amount=amount)]), reason='Pay the carrier')


def net(ledger, date_from='2017-01-01', date_to='2017-12-31'):
    """Signed minor units per account, read back through the ledger's own report."""
    rows = ledger['run']('report general-ledger',
                         {'date_from': date_from, 'date_to': date_to, 'limit': 200})['rows']
    totals = {}
    for line in rows:
        if line['kind'] != 'posting':
            continue
        totals[line['account_id']] = totals.get(line['account_id'], 0) + (
            line['debit']['minor_units'] - line['credit']['minor_units'])
    return {account: value for account, value in totals.items() if value}


def counts(path, table):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT count(*) FROM ' + table).fetchone()[0]


# ---------------------------------------------------------------- the alias witness

def test_a_cheque_cannot_be_edited_or_voided_as_a_plain_journal_entry(ledger):
    """The gap, closed. Without the marker guard both of these succeed and rewrite a cheque.

    `money_out_item_lines` names no line of a cheque entered with expenses only, and
    `inventory_documents` names none of it either, so every guard that existed before this
    one let the journal editor through.
    """
    post = cheque(ledger)
    assert ledger['run']('check show', dict(check=post['id']))['document']['kind'] == 'check'
    path = location(ledger)
    before = database(path)
    for name, extra in (('journal void', {}), ('journal update', {'memo': 'Rewritten'})):
        with pytest.raises(BookflowError) as refused:
            ledger['run'](name, dict(journal=post['id'], expected_version=post['version'], **extra),
                          reason='Try it as a plain journal')
        assert refused.value.code == 'E_VALIDATION'
        assert 'This entry is a check, not a plain journal entry.' in refused.value.message
        assert refused.value.details['next'] == 'check ' + name.split()[1]
        assert refused.value.details['money_out_document'] == 'check'
    assert database(path) == before
    # The document's own verbs still work, which is what the refusal points at.
    corrected = ledger['run']('check update', dict(check=post['id'],
        expected_version=post['version'], memo='Corrected on its own form'), reason='Correct it')
    assert corrected['revision']['memo'] == 'Corrected on its own form'
    voided = ledger['run']('check void', dict(check=post['id'],
        expected_version=corrected['version']), reason='Cancel it on its own form')
    assert voided['status'] == 'voided'


def test_deleting_a_cheque_as_a_journal_entry_refuses_by_name_and_changes_nothing(ledger, admitted):
    """The witness this family exists to pass: one document, one tombstone, its own command."""
    post = cheque(ledger, amount='31.00', date='2017-03-05')
    path = location(ledger)
    before = database(path)
    with pytest.raises(BookflowError) as refused:
        ledger['run']('journal delete', dict(journal=post['id'], expected_version=post['version'],
                      operation_key='alias-witness'), reason='Remove the duplicate')
    assert refused.value.code == 'E_VALIDATION'
    assert refused.value.message == ('This entry is a check, not a plain journal entry. '
                                     'Use `check delete` so the document and its money change together.')
    assert refused.value.details['next'] == 'check delete'
    assert refused.value.details['transaction_id'] == post['id']
    # Nothing at all moved: no tombstone, no reversal, no version.
    assert database(path) == before
    assert counts(path, 'journal_deletions') == 0

    # And the cheque still deletes properly, through the command that owns it.
    ledger['grant'](extra=['transaction.check.delete'])
    deleted = ledger['run']('check delete', dict(check=post['id'],
        expected_version=post['version'], operation_key='alias-witness-owning'),
        reason='Remove the duplicate cheque')
    assert deleted['status'] == 'deleted' and deleted['family'] == 'check'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM purchase_deletions WHERE transaction_id=?',
                          (post['id'],)).fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM journal_deletions').fetchone() == (0,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_a_transfer_and_a_card_charge_are_refused_by_the_command_that_owns_them(ledger, admitted):
    charge = ledger['run']('card-charge post', dict(account=ledger['card'], date='2017-03-06',
        amount='12.00', expenses=[dict(account=ledger['freight'], amount='12.00')]),
        reason='Buy supplies on the card')
    moved = ledger['run']('transfer post', dict(from_account=ledger['bank'],
        to_account=ledger['savings'], date='2017-03-07', amount='500.00'), reason='Sweep to savings')
    path = location(ledger)
    before = database(path)
    for record, expected, message in (
            (charge, 'card-charge delete', 'This entry is a credit card charge, not a plain journal entry.'),
            (moved, 'transfer void', 'This entry is a transfer, not a plain journal entry.')):
        with pytest.raises(BookflowError) as refused:
            ledger['run']('journal delete', dict(journal=record['id'],
                expected_version=record['version'], operation_key='alias-' + expected),
                reason='Try it as a journal entry')
        assert refused.value.code == 'E_VALIDATION'
        assert message in refused.value.message
        assert refused.value.details['next'] == expected
    assert database(path) == before
    # A transfer has no deletion family of its own, so the refusal points at what does own it.
    assert ledger['run']('transfer void', dict(transfer=moved['id'],
        expected_version=moved['version']), reason='Put it back')['status'] == 'voided'


# ---------------------------------------------------------------- the family itself

def test_exact_cancellation_of_a_journal_entry_without_post_authority(ledger, admitted):
    post = entry(ledger, memo='Duplicated freight accrual', amount='61.50', date='2017-04-02')
    path = location(ledger)
    before_ledger = net(ledger, '2017-04-01', '2017-04-30')
    assert before_ledger[ledger['freight']] == 6150
    ledger['grant'](delete=True, post=False)
    with pytest.raises(BookflowError) as denied:
        ledger['run']('journal void', dict(journal=post['id'], expected_version=post['version']),
                      reason='Posting denied')
    assert denied.value.code == 'E_PERMISSION'
    raw = dict(journal=post['id'], expected_version=post['version'], operation_key='exact-cancellation')
    before = database(path)
    preview = ledger['run']('journal delete', raw, reason='Remove duplicate entry', dry_run=True)
    assert preview['status'] == 'deleted' and preview['version'] == post['version'] + 1
    assert preview['from_status'] == 'posted' and preview['number'] == post['number']
    assert database(path) == before

    result = ledger['run']('journal delete', raw, reason='Remove duplicate entry')
    assert result['status'] == 'deleted' and result['version'] == post['version'] + 1
    assert result['cancellation_batch_id'] is not None
    # Nothing of this entry is left in the books, at any date, and the reports still sum
    # immutable effects: the reversal is a posting, not an erasure.
    assert net(ledger, '2017-04-01', '2017-04-30') == {}
    after = database(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM posting_batches WHERE transaction_id=? AND kind='reversal'",
                          (post['id'],)).fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM journal_deletions WHERE transaction_id=?',
                          (post['id'],)).fetchone() == (1,)
        assert db.execute('SELECT principal_id FROM journal_deletions WHERE transaction_id=?',
                          (post['id'],)).fetchone() == (None,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    for table in RETAINED_TABLES:
        assert after['tables'][table] == before['tables'][table], table
    ledger['grant'](delete=True, post=True)


def test_the_same_key_returns_the_original_result_and_never_acts_twice(ledger, admitted):
    post = entry(ledger, memo='Retried removal', amount='7.25', date='2017-04-03')
    path = location(ledger)
    raw = dict(journal=post['id'], expected_version=post['version'], operation_key='permanent-journal')
    first = ledger['run']('journal delete', raw, reason='Remove duplicate entry')
    # The second entry exists before the state is captured, so the only thing that could
    # move the database afterwards is the retry itself.
    other = entry(ledger, memo='A different entry', amount='9.00', date='2017-04-04')
    after = database(path)
    replay = ledger['run']('journal delete', raw, reason='Remove duplicate entry')
    assert replay['idempotent_replay'] and not replay['changed']
    assert {k: v for k, v in replay.items() if k not in ('changed', 'idempotent_replay')} == \
           {k: v for k, v in first.items() if k not in ('changed', 'idempotent_replay')}
    assert database(path) == after
    # A different request under the same permanent key is a conflict, not a second act.
    with pytest.raises(BookflowError) as clash:
        ledger['run']('journal delete', dict(journal=other['id'],
            expected_version=other['version'], operation_key='permanent-journal'),
            reason='Remove duplicate entry')
    assert clash.value.code == 'E_IDEMPOTENCY_MISMATCH'
    assert database(path) == after


def test_an_already_voided_entry_adds_metadata_once_and_posts_no_second_inverse(ledger, admitted):
    post = entry(ledger, memo='Voided then removed', amount='14.00', date='2017-04-05')
    voided = ledger['run']('journal void', dict(journal=post['id'],
        expected_version=post['version']), reason='Cancelled first')
    path = location(ledger)
    with sqlite3.connect(path) as db:
        batches = db.execute("SELECT count(*) FROM posting_batches WHERE transaction_id=? AND kind='reversal'",
                             (post['id'],)).fetchone()[0]
    assert batches == 1
    result = ledger['run']('journal delete', dict(journal=post['id'],
        expected_version=voided['version'], operation_key='already-voided'),
        reason='Remove the cancelled entry')
    assert result['from_status'] == 'voided' and result['version'] == voided['version'] + 1
    assert result['cancellation_batch_id'] == voided['void_posting_batch_id']
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM posting_batches WHERE transaction_id=? AND kind='reversal'",
                          (post['id'],)).fetchone() == (1,)
        assert db.execute('SELECT void_reason FROM transactions WHERE id=?',
                          (post['id'],)).fetchone() == ('Cancelled first',)


def test_a_deleted_entry_leaves_ordinary_reads_and_comes_back_when_asked_for(ledger, admitted):
    post = entry(ledger, memo='Hidden after removal', amount='3.30', date='2017-05-06')
    ledger['run']('journal delete', dict(journal=post['id'], expected_version=post['version'],
                  operation_key='hidden-after'), reason='Remove duplicate entry')
    with pytest.raises(BookflowError) as gone:
        ledger['run']('journal show', dict(journal=post['id']))
    assert gone.value.code == 'E_RECORD_NOT_FOUND'
    with pytest.raises(BookflowError):
        ledger['run']('journal history', dict(journal=post['id']))

    read = ledger['run']('journal show', dict(journal=post['id'], include_deleted=True))
    assert read['status'] == 'deleted'
    assert read['deletion']['reason'] == 'Remove duplicate entry'
    assert read['deletion']['from_status'] == 'posted'
    assert read['deletion']['created_by_name']
    assert read['revision']['lines'] and read['revision']['memo'] == 'Hidden after removal'
    assert ledger['run']('journal history', dict(journal=post['id'], include_deleted=True))['count'] >= 1

    listed = ledger['run']('journal query', dict(date_from='2017-05-01', date_to='2017-05-31', limit=50))
    assert post['id'] not in {row['id'] for row in listed['items']}
    retained = ledger['run']('journal query', dict(date_from='2017-05-01', date_to='2017-05-31',
                                                   limit=50, include_deleted=True))
    shown = next(row for row in retained['items'] if row['id'] == post['id'])
    assert shown['status'] == 'deleted' and shown['deletion']['reason'] == 'Remove duplicate entry'

    # The account register is a selector a person picks from, and it omits it too.
    register = ledger['run']('register query', dict(account=ledger['bank'],
        date_from='2017-05-01', date_to='2017-05-31', limit=100))
    assert post['id'] not in {row['transaction_id'] for row in register['rows']}

    # Retained history is read, never edited: the writers refuse it by name.
    for name in ('journal update', 'journal void'):
        with pytest.raises(BookflowError) as refused:
            ledger['run'](name, dict(journal=post['id'], expected_version=post['version'] + 1),
                          reason='Try to edit retained history')
        assert 'This journal entry was deleted' in str(refused.value.details)


# ---------------------------------------------------------------- the storage itself

def test_co55_declaration_is_exact_and_the_storage_is_immutable(ledger, admitted):
    import importlib
    import re
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company import schema as c, journal_deletion_schema
    from bookflow.core.deletion_families import PREPARED_FAMILIES, TOMBSTONE_TABLE
    from bookflow.storage.migrate import HEADS
    migration = importlib.import_module('bookflow.storage.company_migrations.versions.0056_journal_deletions')
    assert migration.revision == 'co0056' and migration.down_revision == 'co0055'
    assert HEADS['company'] == 'co0056'
    assert migration.DDL == (str(CreateTable(c.journal_deletions).compile(dialect=dialect())),
                             *journal_deletion_schema.guards())
    check = next(x for x in c.journal_deletions.constraints if x.name == 'ck_journal_delete_family')
    assert tuple(re.findall("'([^']+)'", str(check.sqltext))) == (PREPARED_FAMILIES[0],) == ('journal_entry',)
    assert TOMBSTONE_TABLE['journal_entry'] == 'journal_deletions'

    post = entry(ledger, memo='Immutable once removed', amount='2.20', date='2017-06-07')
    path = location(ledger)
    ledger['run']('journal delete', dict(journal=post['id'], expected_version=post['version'],
                  operation_key='immutable'), reason='Remove duplicate entry')
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for sql in ('DELETE FROM journal_deletions', 'UPDATE journal_deletions SET reason=reason',
                    'UPDATE transactions SET version=version WHERE id=?'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                db.execute(sql, (post['id'],) if '?' in sql else ())
            db.rollback()


def test_the_owner_trigger_refuses_a_journal_tombstone_on_a_document_that_is_not_one(ledger):
    """The storage is the proof, independently of the refusal the writer raises above it.

    Forced past every Python guard, a cheque still cannot carry a journal deletion: the
    second tombstone this family's whole shape exists to prevent cannot be written at all.
    """
    post = cheque(ledger, amount='44.00', date='2017-07-08')
    voided = ledger['run']('check void', dict(check=post['id'],
        expected_version=post['version']), reason='Cancel it')
    path = location(ledger)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        row = db.execute('SELECT id,current_revision_id,version,void_posting_batch_id FROM transactions WHERE id=?',
                         (post['id'],)).fetchone()
        event = db.execute('SELECT id FROM audit_events LIMIT 1').fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match='owner mismatch'):
            db.execute('INSERT INTO journal_deletions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (row[0], 'journal_entry', row[1], 'voided', row[2], row[2] + 1, row[3],
                        'forced', 'x', '{}', '{}', '2017-07-09T00:00:00Z', 'A', None, 'python',
                        'Forced', event))
        db.rollback()
    assert voided['status'] == 'voided'


def test_tombstone_failure_rolls_the_whole_deletion_back(ledger, admitted, monkeypatch):
    from bookflow.company import journal_deletions
    post = entry(ledger, memo='Rolled back', amount='5.55', date='2017-08-09')
    path = location(ledger)
    before = database(path)
    original = journal_deletions.persist_tombstone

    def fail(s, row):
        original(s, row)
        raise BookflowError('E_VALIDATION', message='Injected after journal tombstone')

    monkeypatch.setattr(journal_deletions, 'persist_tombstone', fail)
    with pytest.raises(BookflowError, match='Injected'):
        ledger['run']('journal delete', dict(journal=post['id'], expected_version=post['version'],
                      operation_key='rollback-witness'), reason='Rollback witness')
    assert database(path) == before
