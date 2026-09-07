"""Independent scalar parity and bounded reads; synthetic rows stay in memory."""
import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bookflow.company import payment_authority as pa, schema as c
from bookflow.core.errors import BookflowError


def outcome(fn):
    try:
        return ('ok', fn())
    except BookflowError as exc:
        return ('error', exc.code, exc.details)
    except Exception as exc:
        return ('internal-error', type(exc).__name__, str(exc))


def loaded(monkeypatch, tables):
    def scalar(db, table, field, value, cache):
        if cache is not None:
            return cache[(table.name, field)].get(value, [])
        return [r for r in tables.get(table.name, ()) if r.get(field) == value]
    def read(self, table, field, identifiers, columns):
        groups = {}
        identifiers = set(identifiers)
        for row in tables.get(table.name, ()):
            if row.get(field) in identifiers:
                groups.setdefault(row[field], []).append({k: row[k] for k in columns})
        return groups
    monkeypatch.setattr(pa, '_evidence_rows', scalar)
    monkeypatch.setattr(pa._EventCohort, '_read', read)
    # Exercise scalar work expansion through its existing cache-compatible API.
    original = pa.linked_work_required
    def work(db, ids, cache=None):
        if not ids:
            return False
        cache = {}
        for table, field in ((c.applications, 'paying_transaction_id'), (c.work_billing_allocations, 'transaction_id')):
            cache[(table.name, field)] = {}
            for row in tables.get(table.name, ()):
                cache[(table.name, field)].setdefault(row[field], []).append(row)
        def evidence(db, table, field, value, cache):
            return cache[(table.name, field)]
        with monkeypatch.context() as m:
            m.setattr(pa, '_evidence_rows', evidence)
            return original(db, ids, cache)
    monkeypatch.setattr(pa, 'linked_work_required', work)


def facts():
    return {
        'audit_entries': [dict(event_id='mixed', record_type=k, record_id=i) for k, i in
                          [('note', 'a'), ('note', 'b'), ('payment_operation_item', 'item'),
                           ('payment_selection_revision', 'rev'), ('attachment', 'file'), ('customer', 'ordinary'), ('note', 'a')]],
        'notes': [dict(id='a', record_type='note', record_id='b'),
                  dict(id='b', record_type='attachment', record_id='file')],
        'attachment_links': [dict(id='back', attachment_id='file', record_type='note', record_id='a'),
                             dict(id='endpoint', attachment_id='file', record_type='transaction', record_id='invoice')],
        'transactions': [dict(id='invoice'), dict(id='secondary')],
        'payment_operation_items': [dict(id='item', operation_id='op', transaction_id='receipt')],
        'payment_operations': [dict(id='op', request_snapshot=json.dumps({'resolved_transaction_ids': ['receipt', 'secondary', 'no-effect']}))],
        'payment_selection_revisions': [dict(id='rev', selection_id='selection', context_snapshot='{"payment_id":"old-payment"}'),
                                       dict(id='rev2', selection_id='selection', context_snapshot='{"payment_id":"new-payment"}')],
        'payment_selection_items': [dict(id='si', selection_id='selection', invoice_id='historical-invoice')],
        'applications': [dict(paying_transaction_id='receipt', paid_transaction_id='work-invoice'),
                         dict(paying_transaction_id='work-invoice', paid_transaction_id='second-hop')],
        'work_billing_allocations': [dict(transaction_id='work-invoice')],
    }


def test_complete_mixed_graph_cycles_secondary_and_historical_facts(monkeypatch):
    tables = facts(); loaded(monkeypatch, tables)
    reader = pa._EventCohort(None, ['mixed'])
    assert len(reader.entries['mixed']) == 7  # output siblings are never deduplicated
    expected = {'invoice', 'receipt', 'secondary', 'no-effect', 'old-payment', 'new-payment', 'historical-invoice'}
    assert reader.resolved['mixed'] == expected
    for root in [('note', 'a'), ('note', 'b'), ('attachment', 'file')]:
        assert reader._walk(root) == pa.record_transactions(None, *root) == {'invoice'}
    assert reader.requirements('mixed') == pa.event_requirements(None, 'mixed') == (
        ('ledger.read', 'member'), ('customer-work', 'member'))
    # Only one historical application expansion, never arbitrary transitive work.
    tables['work_billing_allocations'] = [dict(transaction_id='second-hop')]
    assert pa._EventCohort(None, ['mixed']).requirements('mixed') == pa.event_requirements(None, 'mixed') == (('ledger.read', 'member'),)


@pytest.mark.parametrize('payload', ['{}', '{', 'null', '{"resolved_transaction_ids":null}',
                                    '{"resolved_transaction_ids":[1]}'])
def test_malformed_owned_intent_is_local_and_scalar_equivalent(monkeypatch, payload):
    tables = facts(); tables['payment_operations'][0]['request_snapshot'] = payload
    tables['audit_entries'] += [dict(event_id='good', record_type='customer', record_id='ordinary')]
    loaded(monkeypatch, tables); reader = pa._EventCohort(None, ['good', 'mixed'])
    assert outcome(lambda: reader.requirements('mixed')) == outcome(lambda: pa.event_requirements(None, 'mixed'))
    assert outcome(lambda: reader.requirements('mixed'))[1] == 'E_PERMISSION'
    assert reader.requirements('good') == pa.event_requirements(None, 'good') == ()


@pytest.mark.parametrize('kind,identifier', [('transaction', 'absent'), ('note', 'absent'),
                                            ('payment_operation', 'absent'), ('attachment', 'unattached'), ('unknown', 'ordinary')])
def test_missing_and_empty_roots(monkeypatch, kind, identifier):
    tables = facts(); tables['audit_entries'] = [dict(event_id='event', record_type=kind, record_id=identifier)]
    loaded(monkeypatch, tables); reader = pa._EventCohort(None, ['event'])
    assert outcome(lambda: reader.requirements('event')) == outcome(lambda: pa.event_requirements(None, 'event'))


@pytest.mark.parametrize('count', [199, 200, 201, 403])
def test_sql_id_bound_and_complete_fanout(count):
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE audit_entries (event_id TEXT, record_type TEXT, record_id TEXT)')
        conn.exec_driver_sql('INSERT INTO audit_entries VALUES (?,?,?)',
                             [(str(i), 'customer', str(j)) for i in range(count) for j in range(3)] +
                             [('0', 'customer', 'fanout') for _ in range(403)])
        observed = []
        def record(conn, cursor, statement, parameters, context, many):
            observed.append(len(parameters))
        sa.event.listen(conn, 'before_cursor_execute', record)
        reader = object.__new__(pa._EventCohort); reader.db = SimpleNamespace(conn=conn)
        rows = reader._read(c.audit_entries, 'event_id', [str(i) for i in range(count)], ('event_id', 'record_type', 'record_id'))
        assert observed == [min(200, count - i) for i in range(0, count, 200)]
        assert sum(map(len, rows.values())) == count * 3 + 403
        assert len(rows['0']) == 406
    engine.dispose()


def test_all_seeded_events_match_independent_scalar(client):
    from pathlib import Path
    from bookflow.storage.engine import open_database
    path = Path(client.company.show(company='Demo Plumbing Co')['path']) / 'company.db'
    with open_database(path, writable=False) as db:
        events = list(db.conn.execute(sa.select(c.audit_entries.c.event_id).distinct()).scalars())
        for cohort in pa._groups(events):
            reader = pa._EventCohort(db, cohort)
            for event in cohort:
                assert outcome(lambda: reader.requirements(event)) == outcome(lambda: pa.event_requirements(db, event))


def test_separate_calls_see_public_note_link_and_edit(client):
    customer = client.customer.create(name='Authority call lifetime', company='Demo Plumbing Co')
    args = dict(record_type='customer', record_id=customer['id'], limit=1)
    first = client.run('activity', args, company='Demo Plumbing Co')
    note = client.note.add(record_type='customer', record_id=customer['id'], body='First', company='Demo Plumbing Co')['note']
    client.note.edit(note=note['id'], expected_version=1, body='Second', company='Demo Plumbing Co')
    current = client.run('activity', {**args, 'limit': 200}, company='Demo Plumbing Co')
    assert current['high_water'] > first['high_water']
    assert [item['body'] for item in current['items'] if item['kind'] == 'note'] == ['First', 'Second']
    assert len({item['entry_id'] for item in current['items']}) == current['count'] == 3


def test_public_all_pages_equal_scalar_for_payment_and_annotations(client, monkeypatch):
    """Ordinary public writes; the alternate publisher enforces the same real gate."""
    import io
    from tests.test_payment_receipts import method
    # Use already-public demo endpoints; new receipt intent has no application.
    company = 'Demo Plumbing Co'
    customer = client.customer.create(name='Audit parity payer', company=company)
    paid = client.run('payment receive', dict(customer=customer['id'], date='2026-06-02', amount='1.00',
        payment_method=method(client), operation_key='audit-parity-receive',
        applications=dict(mode='inline', items=[])), company=company)
    note = client.note.add(record_type='transaction', record_id=paid['id'], body='Original receipt note', company=company)['note']
    client.note.edit(note=note['id'], expected_version=1, body='Amended receipt note', company=company)
    attachment = client.attachment.add(record_type='transaction', record_id=paid['id'], original_filename='evidence.txt',
        input_stream=io.BytesIO(b'ordinary audit parity'), company=company)
    operation = paid['effect']['operation_id']
    original = pa.denied_events
    def scalar(s, resolved=None):
        events = s.company.conn.execute(sa.select(c.audit_entries.c.event_id).where(c.audit_entries.c.record_type.in_(
            (*pa.PAYMENT_TARGETS, 'transaction', 'transaction_revision', 'document_line', 'document_line_identity',
             'posting_batch', 'posting_line', 'posting_line_source', 'note', 'attachment', 'attachment_link'))).distinct()).scalars()
        denied = []
        for event in events:
            try:
                for resource, role in pa.event_requirements(s.company, event):
                    pa.require_resource(s, resource, role)
            except BookflowError as exc:
                if exc.code != 'E_PERMISSION':
                    raise
                denied.append(event)
        return denied
    def walk(command, args, key, cursor, stop):
        pages = []
        while True:
            page = client.run(command, args, company=company); pages.append(page)
            if stop(page):
                return pages
            assert page[key] is not None
            args = {**args, cursor: page[key]}
    def capture():
        results = []
        for kind, identifier in [('transaction', paid['id']), ('note', note['id']),
                                 ('attachment', attachment['attachment']['id']), ('payment_operation', operation)]:
            for size in (1, 20, 200):
                results.append(walk('activity', dict(record_type=kind, record_id=identifier, limit=size),
                                    'next_cursor', 'cursor', lambda p: not p['has_more']))
        results.append(walk('audit list', dict(record_type='payment_operation', record_id=operation, limit=1),
                            'next_before', 'before', lambda p: p['next_before'] is None))
        for scan in (None, 20):
            results.append(walk('audit tail', dict(after=0, limit=20, record_type='note', record_id=note['id'],
                                **({'scan_limit': scan} if scan else {})), 'next_after', 'after',
                                lambda p: not p['scan_more'] if scan else p['count'] == 0))
        return results
    batched = capture()
    monkeypatch.setattr(pa, 'denied_events', scalar)
    assert capture() == batched
    monkeypatch.setattr(pa, 'denied_events', original)
