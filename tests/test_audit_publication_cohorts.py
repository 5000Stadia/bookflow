"""Explicit publication occurrences, ordered failures and snapshot-local facts."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from bookflow.company import payment_authority as pa
from bookflow.core import publication_payment as pp
from bookflow.core.errors import BookflowError
from tests.test_audit_authority_batching import facts, loaded, outcome


def scalar(s, events):
    for event in events:
        pa.authorize_event(s, event)


@pytest.mark.parametrize('count', [0, 1, 200, 201])
def test_explicit_occurrences_match_scalar(monkeypatch, count):
    tables = facts()
    # Fully unapplied relationships still confer historical work authority.
    tables['applications'].append(dict(paying_transaction_id='receipt', paid_transaction_id='work-invoice',
                                      kind='unapply'))
    tables['audit_entries'].append(dict(event_id='ordinary', record_type='customer', record_id='ordinary'))
    loaded(monkeypatch, tables)
    events = (['mixed', 'ordinary', 'no-entries', 'mixed'] * 51)[:count]
    calls = []
    monkeypatch.setattr(pa, 'require_resource', lambda s, resource, role: calls.append((resource, role)))
    s = SimpleNamespace(company=None)
    expected = outcome(lambda: scalar(s, events)); expected_calls = calls[:]; calls.clear()
    cohorts = []
    original = pa._EventCohort
    def reader(db, ids):
        cohorts.append(list(ids))
        return original(db, ids)
    monkeypatch.setattr(pa, '_EventCohort', reader)
    assert outcome(lambda: pa.authorize_events(s, iter(events))) == expected
    assert calls == expected_calls
    assert cohorts == [events[i:i + 200] for i in range(0, count, 200)]


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('failure', ['permission', 'graph'])
def test_first_error_and_permission_order_match_scalar(monkeypatch, reverse, failure):
    tables = facts()
    tables['audit_entries'] += [dict(event_id='bad', record_type='payment_operation', record_id='bad-op')]
    tables['payment_operations'].append(dict(id='bad-op', request_snapshot='{}'))
    loaded(monkeypatch, tables)
    events = ['mixed', 'bad']
    if reverse:
        events.reverse()
    calls = []
    def gate(s, resource, role):
        calls.append((resource, role))
        if failure == 'permission':
            raise BookflowError('E_PERMISSION', details={'reason': 'earlier_gate'})
    monkeypatch.setattr(pa, 'require_resource', gate)
    s = SimpleNamespace(company=None)
    expected = outcome(lambda: scalar(s, events)); sequence = calls[:]; calls.clear()
    assert outcome(lambda: pa.authorize_events(s, events)) == expected
    assert calls == sequence
    assert expected[0] == 'error'
    assert expected[2]['reason'] == ('earlier_gate' if failure == 'permission' and not reverse else 'unresolved_payment_evidence')


def test_mixed_roots_do_not_prefetch_across_non_event(monkeypatch):
    sequence = []
    def events(s, ids):
        sequence.append(('events', list(ids)))
    def disclose(db, kind, identifier):
        sequence.append((kind, identifier))
        if identifier == 'stop':
            raise BookflowError('E_PERMISSION')
        return set()
    monkeypatch.setattr(pa, 'authorize_events', events)
    monkeypatch.setattr(pa, 'disclosure_transactions', disclose)
    monkeypatch.setattr(pp, 'work_access', lambda s: sequence.append(('work_access',)) or True)
    roots = [('audit_event', 'a', False), ('audit_event', 'a', False), ('work_access', True, False),
             ('customer', 'plain', False), ('audit_event', 'b', False), ('customer', 'stop', False),
             ('audit_event', 'never', False)]
    assert outcome(lambda: pp.check(SimpleNamespace(company=None), iter(roots)))[1] == 'E_PERMISSION'
    assert sequence == [('events', ['a', 'a']), ('work_access',), ('customer', 'plain'),
                        ('events', ['b']), ('customer', 'stop')]


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('later_send', [False, True])
def test_operational_prefetch_exception_blocks_pending_send(monkeypatch, reverse, later_send):
    """Existing in-memory loader and real middleware; no damaged storage."""
    from bookflow.adapters.http.publication import PublicationMiddleware, protect
    tables = facts()
    tables['audit_entries'] = [dict(event_id='denied', record_type='transaction', record_id='invoice'),
                              dict(event_id='io', record_type='payment_operation', record_id='op')]
    loaded(monkeypatch, tables)
    original = pa._EventCohort._read
    calls = []; enabled = False
    def read(self, table, *args):
        if enabled and table.name == 'payment_operations':
            raise BookflowError('E_DB_BUSY')
        return original(self, table, *args)
    def gate(s, resource, role):
        calls.append((resource, role))
        if enabled:
            raise BookflowError('E_PERMISSION', details={'reason': 'earlier_gate'})
    monkeypatch.setattr(pa._EventCohort, '_read', read)
    monkeypatch.setattr(pa, 'require_resource', gate)
    ids = ['denied', 'io'] if not reverse else ['io', 'denied']
    roots = [('audit_event', identifier, False) for identifier in ids]
    terminal = []
    def check(**kwargs):
        try:
            pp.check(SimpleNamespace(company=None), roots)
        except BookflowError as exc:
            terminal.append(exc.code)
            raise
    async def app(scope, receive, send):
        nonlocal enabled
        protect(SimpleNamespace(check=check, credential=SimpleNamespace(token_id='fixture')))
        enabled = not later_send
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        if later_send:
            await send({'type': 'http.response.body', 'body': b'previously authorized', 'more_body': True})
            calls.clear(); enabled = True
        await send({'type': 'http.response.body', 'body': b'pending protected payload', 'more_body': False})
    sent = []
    async def send(message): sent.append(message)
    async def receive(): return {'type': 'http.request', 'body': b''}
    invocation = PublicationMiddleware(app)({'type': 'http', 'path': '/fixture'}, receive, send)
    if later_send:
        with pytest.raises(ConnectionAbortedError, match='Publication authority changed'):
            asyncio.run(invocation)
        assert sent[-1]['body'] == b'previously authorized' and sent[-1]['more_body']
    else:
        asyncio.run(invocation)
        assert sent[0]['status'] == 403
        assert json.loads(sent[-1]['body'])['code'] == 'E_DB_BUSY'
    assert terminal == ['E_DB_BUSY']
    assert calls == []  # explicit allowed precedence, no earlier permission check
    assert all(message.get('body') != b'pending protected payload' for message in sent)


def test_denial_does_not_consume_or_load_the_next_cohort(monkeypatch):
    loaded(monkeypatch, facts())
    consumed = []; reads = []
    original = pa._EventCohort._read
    def read(self, table, field, identifiers, columns):
        if table.name == 'audit_entries':
            reads.append(list(identifiers))
        return original(self, table, field, identifiers, columns)
    def ids():
        for n in range(201):
            consumed.append(n)
            yield 'mixed'
    def deny(*args):
        raise BookflowError('E_PERMISSION')
    monkeypatch.setattr(pa._EventCohort, '_read', read)
    monkeypatch.setattr(pa, 'require_resource', deny)
    assert outcome(lambda: pa.authorize_events(SimpleNamespace(company=None), ids()))[1] == 'E_PERMISSION'
    assert consumed == list(range(200))
    assert reads == [['mixed'] * 200]
