"""Synthetic audited header-only transitions; no payment implementation implied."""
import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.core import audit
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database
from tests.conftest import make_actor, as_user
from tests.test_row8_journal import database_path
from tests.test_service_sales_lifecycle import sale, snapshot, COMPANY


@pytest.fixture(params=['invoice', 'sales-receipt'])
def document(client, sale, request):
    noun = request.param
    extra = {}
    if noun == 'sales-receipt':
        extra = dict(deposit_to=client.account.create(name='History bank', type='bank', company=COMPANY)['id'],
            payment_method=client.run('payment-method create', dict(name='History cash', kind='cash'), company=COMPANY)['id'])
    result = client.run(noun + ' post', dict(date='2026-01-12', customer=sale['customer'],
        lines=[dict(item=sale['item'])], **extra), company=COMPANY)
    return noun, result


def write(client, document, verb='update', **values):
    noun, doc = document
    return client.run(noun + ' ' + verb, {noun.replace('-', '_'): doc['id'], **values},
        company=COMPANY, reason='Financial history witness')


def header_only(client, document, actor=None, on_behalf_of=None):
    """Append a synthetic audited header version, preserving every financial row."""
    before = snapshot(client)
    with open_database(database_path(client), writable=True) as db:
        old = dict(db.conn.execute(sa.select(c.transactions).where(c.transactions.c.id == document[1]['id'])).mappings().one())
        after = dict(old, version=old['version'] + 1)
        event = dict(db.conn.execute(sa.select(c.audit_events).order_by(c.audit_events.c.seq.desc()).limit(1)).mappings().one())
        event.update(id=new_id(), seq=event['seq'] + 1, command='test synthetic header-only',
            summary='Disposable future settlement seam', idempotency_key=None)
        if actor:
            event['actor_id'] = actor
        if on_behalf_of:
            event['on_behalf_of'] = on_behalf_of
        db.conn.execute(c.audit_events.insert().values(**event))
        db.conn.execute(c.audit_entries.insert().values(id=new_id(), event_id=event['id'],
            record_type='transaction', record_id=old['id'], action='update', version_before=old['version'],
            version_after=after['version'], before=audit.encode_snapshot(old), after=audit.encode_snapshot(after)))
        db.conn.execute(c.transactions.update().where(c.transactions.c.id == old['id']).values(version=after['version']))
        db.conn.commit()
    after_db = snapshot(client)
    for table in before.keys() - {'transactions', 'audit_entries', 'audit_events'}:
        assert before[table] == after_db[table], table
    return after


def rejected(client, document, expected, verb='update'):
    before = snapshot(client)
    with pytest.raises(BookflowError) as caught:
        write(client, document, verb, expected_version=expected)
    assert caught.value.code == 'E_VERSION_CONFLICT'
    assert snapshot(client) == before
    return caught.value


def test_decoupled_versions_and_latest_writer(client, root, sale, document):
    header_only(client, document)
    edited = write(client, document, expected_version=2, memo='Actual commercial change',
        lines=[dict(item=sale['item'], line_id=document[1]['revision']['lines'][0]['line_id'], quantity='3')])
    assert (edited['version'], edited['revision']['revision_number']) == (3, 2)
    # A different latest writer changes only the header. The memo belongs to the earlier writer.
    latest = make_actor(root, 'history-latest', company_role=(client.company.show(company=COMPANY)['id'], 'standard'))
    other = as_user(root, 'history-latest')
    other.account.create(name='Latest writer witness account', type='bank', company=COMPANY)
    with open_database(database_path(client), writable=False) as db:
        original_actor = db.conn.execute(sa.select(c.transactions.c.created_by).where(c.transactions.c.id == document[1]['id'])).scalar_one()
    header_only(client, document, actor=latest, on_behalf_of=original_actor)
    error = rejected(client, document, 2)
    fields = error.details['changed_fields']
    assert 'memo' in fields and any(p.startswith('lines.') for p in fields)
    assert fields == sorted(set(fields)) and 'status' not in fields
    assert error.details['updated_by'] == latest
    assert error.details['updated_on_behalf_of'] == original_actor
    assert error.details['updated_via'] == 'python'
    assert isinstance(error.details['seconds_since_update'], (int, float))
    assert 'Latest writer: History-Latest' in error.message
    assert 'Changes since the expected version:' in error.message
    assert 'History-Latest changed' not in error.message
    assert 'Latest writer: History-Latest' in str(error)
    assert 'on behalf of' in error.message


def test_header_status_and_current_noop(client, document):
    header_only(client, document)
    assert rejected(client, document, 1).details['changed_fields'] == ['version']
    assert not write(client, document, expected_version=2)['changed']
    voided = write(client, document, 'void', expected_version=2)
    assert voided['version'] == 3
    assert rejected(client, document, 2, 'void').details['changed_fields'] == ['status']
    header_only(client, document)
    assert rejected(client, document, 3, 'void').details['changed_fields'] == ['version']
    assert not write(client, document, 'void', expected_version=4)['changed']


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'bad_json', 'bad_zip', 'list', 'scalar',
    'wrong_id', 'wrong_version', 'bool_version', 'wrong_type', 'foreign_revision', 'missing_revision', 'migrate'])
def test_unusable_expected_history_is_unknown(client, document, damage):
    header_only(client, document)
    # Corrupt only the disposable fixture; exercise BOTH gate decoding and enrichment.
    with open_database(database_path(client), writable=True) as db:
        row = dict(db.conn.execute(sa.select(c.audit_entries).where(c.audit_entries.c.record_type == 'transaction',
            c.audit_entries.c.record_id == document[1]['id'], c.audit_entries.c.version_after == 1)).mappings().one())
        old = audit.decode_snapshot(row['after'])
        if damage in ('missing', 'migrate'):
            if damage == 'missing':
                db.conn.execute(c.audit_entries.delete().where(c.audit_entries.c.id == row['id']))
            else:
                db.conn.execute(c.audit_entries.update().where(c.audit_entries.c.id == row['id']).values(action='migrate'))
        elif damage == 'duplicate':
            db.conn.execute(c.audit_entries.insert().values(**dict(row, id=new_id())))
        else:
            raw = {'bad_json': b'\x00{', 'bad_zip': b'\x01broken', 'list': b'\x00[]', 'scalar': b'\x001'}.get(damage)
            if raw is None:
                if damage == 'wrong_id': old['id'] = new_id()
                if damage == 'wrong_version': old['version'] = 99
                if damage == 'bool_version': old['version'] = True
                if damage == 'wrong_type': old['type'] = 'journal_entry'
                if damage == 'missing_revision': old['current_revision_id'] = new_id()
                if damage == 'foreign_revision':
                    old['current_revision_id'] = db.conn.execute(sa.select(c.transaction_revisions.c.id).where(c.transaction_revisions.c.transaction_id != document[1]['id']).limit(1)).scalar_one()
                raw = audit.encode_snapshot(old)
            db.conn.execute(c.audit_entries.update().where(c.audit_entries.c.id == row['id']).values(after=raw))
        db.conn.commit()
    error = rejected(client, document, 1)
    assert error.details['changed_fields'] == []
    assert error.details['unknown_versions']
    assert 'fields that cannot be determined' in error.message


def test_future_and_missing_intermediate_gate_diagnostics(client, document):
    header_only(client, document)
    future = rejected(client, document, 9)
    assert future.details['expected_version'] == 9
    assert future.details['changed_fields'] == []
    header_only(client, document)
    with open_database(database_path(client), writable=True) as db:
        db.conn.execute(c.audit_entries.delete().where(c.audit_entries.c.record_type == 'transaction',
            c.audit_entries.c.record_id == document[1]['id'], c.audit_entries.c.version_after == 2))
        db.conn.commit()
    assert rejected(client, document, 1).details['unknown_versions'] == [2]


def test_authority_and_company_before_history(client, root, document):
    header_only(client, document)
    company = client.company.show(company=COMPANY)['id']
    make_actor(root, 'history-reader', company_role=(company, 'readonly'))
    reader = as_user(root, 'history-reader')
    before = snapshot(client)
    with pytest.raises(BookflowError) as caught:
        write(reader, document, expected_version=1)
    assert caught.value.code != 'E_VERSION_CONFLICT'
    assert 'changed_fields' not in caught.value.details
    assert snapshot(client) == before
    noun, doc = document
    client.organization.new(name='History other organization')
    other = client.company.new(legal_name='History other company', organization='History other organization',
        home_currency='USD', timezone='UTC', chart='general')['company_id']
    with pytest.raises(BookflowError) as caught:
        client.run(noun + ' update', {noun.replace('-', '_'): doc['id'], 'expected_version': 1}, company=other)
    assert caught.value.code == 'E_RECORD_NOT_FOUND'
    assert 'changed_fields' not in caught.value.details
    assert snapshot(client) == before


@pytest.mark.parametrize('blob', [b'\x00{', b'\x01broken', b'\x00[]'])
def test_unusable_intervening_snapshot_preserves_gate_unknown(client, document, blob):
    header_only(client, document)
    with open_database(database_path(client), writable=True) as db:
        db.conn.execute(c.audit_entries.update().where(c.audit_entries.c.record_type == 'transaction',
            c.audit_entries.c.record_id == document[1]['id'], c.audit_entries.c.version_after == 2).values(after=blob))
        db.conn.commit()
    error = rejected(client, document, 1)
    assert error.details['unknown_versions'] == [2]
    assert error.details['changed_fields'] == []


def test_valid_legacy_baseline_snapshot(client, document):
    header_only(client, document)
    with open_database(database_path(client), writable=True) as db:
        db.conn.execute(c.audit_entries.update().where(c.audit_entries.c.record_type == 'transaction',
            c.audit_entries.c.record_id == document[1]['id'], c.audit_entries.c.version_after == 1).values(action='baseline'))
        db.conn.commit()
    assert rejected(client, document, 1).details['changed_fields'] == ['version']
