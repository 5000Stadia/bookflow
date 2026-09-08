"""Scalar/cohort parity over real history; corruption policy remains separate.

Faults below replace loaded evidence or raise at a named internal boundary.
They are not public permission-configuration journeys or persistent DB corruption.
"""
from contextlib import contextmanager

import pytest

from bookflow import BookflowError
from bookflow.company import payment_authority as authority
from bookflow.core import identity_admin_binding as binding
from bookflow.core.context import Context
from bookflow.core.publication_audit import open_selected
from bookflow.hub import audit_projection as projection
from tests.test_audit_projection_activity import world, storage
from tests.test_payment_receipts import method
from tests.test_row8_journal import database_path
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_work_billing_lifecycle import accepted, bill


@pytest.fixture(scope='module')
def financial(world):
    client = world['client']
    # Reuse the existing financial factory over this module's disposable copy.
    commercial = sale.__wrapped__(client)
    invoice = bill(client, accepted(client, commercial))
    paid = client.run('payment receive', dict(customer=commercial['customer'], date='2026-06-02',
        amount='1.00', payment_method=method(client), operation_key='cohort-omission',
        applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1,
            amount='1.00')])), company=COMPANY)
    notes = [client.note.add(record_type='transaction', record_id=paid['id'], body=body,
        company=COMPANY)['note'] for body in ('Good before', 'Missing evidence', 'Good after')]
    events = [client.audit.list(record_type='note', record_id=n['id'], company=COMPANY)['items'][0]['id']
        for n in notes]
    return dict(world=world, client=client, payment=paid, invoice=invoice, notes=notes,
        events=events, since=client.audit.show(event=events[0], company=COMPANY)['at'],
        company=client.company.show(company=COMPANY)['company_id'], path=database_path(client))


@contextmanager
def audience_for(financial, mode='list'):
    selection = projection.HistorySelection(mode=mode, company=financial['company'],
        **(dict(record_type='transaction', record_id=financial['payment']['id']) if mode=='activity' else {}))
    ctx = Context.new('python', 'Cohort parity')
    with binding.offline_reader(financial['world']['root'], request_id=ctx.request_id) as reader:
        open_selected(reader, selection, ctx)
        yield projection.make_audience(reader, read_capability='activity' if mode=='activity' else 'audit'), selection


def missing_note(monkeypatch, financial):
    original = authority._evidence_rows
    missing = financial['notes'][1]['id']
    def loaded(db, table, field, value, cache):
        rows = original(db, table, field, value, cache)
        return [] if table.name == 'notes' and field == 'id' and value == missing else rows
    monkeypatch.setattr(authority, '_evidence_rows', loaded)


@pytest.mark.parametrize('mode', ['list', 'activity'])
def test_mixed_history_counts_and_continuation(financial, monkeypatch, mode):
    client = financial['client']
    fields = dict(record_type='note', since=financial['since']) if mode=='list' else dict(
        record_type='transaction', record_id=financial['payment']['id'], kinds=['note'])
    def read(**extra):
        return client.run('audit list' if mode=='list' else 'activity', {**fields, **extra}, company=COMPANY)
    token_name, input_name = ('next_before', 'before') if mode=='list' else ('next_cursor', 'cursor')
    clean = read()
    identity = 'id' if mode=='list' else 'event_id'
    order = list(reversed(financial['events'])) if mode=='list' else financial['events']
    assert [row[identity] for row in clean['items']] == order
    assert clean['count'] == 3 and clean[token_name] is None
    before = storage(financial['path'])
    missing_note(monkeypatch, financial)
    with pytest.raises(BookflowError) as caught:
        client.audit.show(event=financial['events'][1], company=COMPANY)
    assert caught.value.code == 'E_EVENT_NOT_FOUND'
    expected = [event for event in order if event != financial['events'][1]]
    full = read()
    assert [row[identity] for row in full['items']] == expected
    assert full['count'] == 2 and full[token_name] is None
    first = read(limit=1)
    assert first['count'] == 1 and first[token_name]
    second = read(limit=1, **{input_name: first[token_name]})
    assert second['count'] == 1 and second[token_name] is None
    assert first['items'] + second['items'] == full['items']
    if mode=='list':
        assert all(row['entry_count'] == 1 for row in full['items'])
    else:
        assert first['has_more'] and not second['has_more'] and not full['has_more']
        assert [row['body'] for row in full['items']] == ['Good before', 'Good after']
        assert len({row['entry_id'] for row in full['items']}) == 2
    assert storage(financial['path']) == before


def test_real_scalar_cohort_requirements_and_once_resolution(financial):
    with audience_for(financial) as (audience, selection):
        db = audience.reader.session.company
        paid, invoice = financial['payment']['id'], financial['invoice']['id']
        assert db.raw.execute('SELECT 1 FROM applications WHERE paying_transaction_id=? AND paid_transaction_id=?',
            (paid, invoice)).fetchone()
        assert db.raw.execute('SELECT 1 FROM work_billing_allocations WHERE transaction_id=?', (invoice,)).fetchone()
        events = [r[0] for r in db.raw.execute('SELECT id FROM audit_events ORDER BY seq')]
        cohort = authority._EventCohort(db, events)
        requirements = {event: cohort.requirements(event) for event in events}
        assert requirements == {event: authority.event_requirements(db, event) for event in events}
        for event in financial['events']:
            assert ('customer-work', 'member') in requirements[event]
            seen = []
            def resolve(identifier):
                seen.append(identifier)
                return cohort.requirements(identifier)
            scalar = projection.project_event(audience, event, company=selection.company)
            assert scalar is not None
            assert projection.project_event(audience, event, company=selection.company,
                requirement_resolver=resolve) == scalar
            assert seen == [event]
        event = financial['events'][0]
        absent = authority._EventCohort(db, [])
        with pytest.raises(KeyError) as caught:
            projection.project_event(audience, event, company=selection.company,
                requirement_resolver=absent.requirements)
        assert caught.value.args == (event,)


@pytest.mark.parametrize('mode', ['list', 'activity'])
def test_scan_resolution_and_construction_failures_propagate(financial, monkeypatch, mode):
    # Actual reader and candidates; only the selected failure boundary is injected.
    with audience_for(financial, mode) as (audience, selection):
        for boundary in ('requirements', 'constructor'):
            failures = [OSError('owned I/O fault'), RuntimeError('owned schema fault'),
                BookflowError('E_VALIDATION'), KeyError('unknown cohort event')]
            if boundary == 'constructor':
                failures.append(BookflowError('E_PERMISSION'))
            for failure in failures:
                seen = []
                def fail(*args, **kwargs):
                    seen.append(args)
                    raise failure
                with monkeypatch.context() as patch:
                    if boundary == 'requirements':
                        patch.setattr(authority._EventCohort, 'requirements', fail)
                    else:
                        patch.setattr(authority, '_EventCohort', fail)
                    with pytest.raises(type(failure)) as caught:
                        projection.project_history(audience, selection)
                    assert caught.value is failure and len(seen) == 1
