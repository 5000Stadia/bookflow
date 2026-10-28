"""R112 increment 2: the queue and the transport seam (design/document-delivery.md §2, §4, §7, §11).

Production registers no transport, so nothing can leave the machine; the tests register a fake (tests only) and walk
the state table row by row: claims, acceptance, the backoff, permanent failure, the unknown outcome and the lease, a
crash between claim and record, the go (a person, an agent, a person who lost it before the claim), the daily limit,
tracking events and the bounce to-do, a restore that arrives with every connection disabled, the host's scheduler
tick (and its cost for a company with nothing to send), and a full pass with every socket and process call refused.
"""
import itertools
import os
import socket
import statistics
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pytest

from bookflow import BookflowError
from bookflow.core import clock as bf_clock
from bookflow.delivery import transport
from tests import delivery_transport_contract as contract
from tests.delivery_fake_transport import SETTINGS, FakeBackend, registered
from tests.test_bill_item_lines import books  # noqa: F401 - fixture
from tests.test_row3_host import hosted  # noqa: F401 - fixture

REASON = {'reason': 'Queue witness'}


class Clock:
    """``clock.now`` from a fixed start, moved by the test."""

    def __init__(self, monkeypatch):
        self.at = datetime.now(timezone.utc).replace(microsecond=0)
        monkeypatch.setattr(bf_clock, 'now', lambda: self.at)

    def advance(self, minutes):
        self.at += timedelta(minutes=minutes)


def _desk(books, monkeypatch, backend):  # noqa: F811
    client, company, run = books['client'], books['company'], books['run']
    visit = client.item.create(company=company, name='Site visit', type='service', income_account_id=books['income'],
                               price='50.00')['id']
    run('customer update', {'customer': books['customer'], 'email': 'pay@adams.example'})
    counter = itertools.count(1)

    def invoice():
        return run('invoice post', {'number': f'Q-{next(counter)}', 'date': '2026-03-02', 'customer': books['customer'],
                                    'lines': [{'item': visit, 'quantity': '1'}]}, reason='Seed')

    def ready(document=None):
        number = run('delivery draft', {'kind': 'invoice', 'document': document or invoice()['number']},
                     reason='Email it')['number']
        assert run('delivery send', {'messages': [number]}, reason='Send')['messages'][0]['go_by']
        return number

    return dict(books, backend=backend, clock=Clock(monkeypatch), ready=ready, invoice=invoice)


@pytest.fixture
def desk(books, monkeypatch):  # noqa: F811
    with registered(FakeBackend()) as backend:
        yield _desk(books, monkeypatch, backend)


def connect(run, **extra):
    added = run('delivery-connection add', {'name': 'Office', 'kind': 'fake', 'settings': SETTINGS,
                                            'secret_ref': 'env:FAKE_MAIL_TOKEN', **extra}, **REASON)
    assert not added['enabled'] and not added['usable']
    enabled = run('delivery-connection enable', {'connection': 'Office'}, **REASON)
    assert enabled['enabled_here'] and enabled['usable']
    return enabled


def _events(run, number):
    return [e['event'] for e in run('delivery history', {'message': number})['events']]


# ---------------------------------------------------------------- nothing leaves without a sender

def test_production_registers_no_transport_and_nothing_can_be_set_up(books):  # noqa: F811
    from bookflow.core import registry
    registry.load_all()
    assert transport.kinds() == [] and transport.REGISTRY == {}
    run = books['run']
    with pytest.raises(BookflowError) as refused:
        run('delivery-connection add', {'name': 'Office', 'kind': 'mcp-mail'}, **REASON)
    assert refused.value.code == 'E_VALIDATION' and 'available: none' in refused.value.message
    listed = run('delivery-connection query', {})
    assert listed['rows'] == [] and listed['kinds_available'] == [] and 'nothing leaves' in listed['note']
    out = run('delivery run', {}, **REASON)
    assert out['claimed'] == 0 and out['connection'] == 'none'
    assert out['warnings'][0].startswith('0 claimed, no sending connection')


def test_a_connection_never_holds_a_secret_and_needs_a_person(desk):
    run = desk['run']
    for bad in ({'secret_ref': 'hunter2'}, {'settings': {'password': 'hunter2'}}):
        with pytest.raises(BookflowError) as refused:
            run('delivery-connection add', {'name': 'Office', 'kind': 'fake', **bad}, **REASON)
        assert refused.value.code == 'E_VALIDATION'
    shown = connect(run, daily_limit=50)
    assert shown['secret_ref'] == 'env:FAKE_MAIL_TOKEN' and 'hunter2' not in str(shown)
    checked = run('delivery-connection check', {'connection': 'Office'}, **REASON)
    assert checked['last_check_ok'] and desk['backend'].checks == 1
    run('delivery-connection update', {'connection': 'Office', 'no_daily_limit': True}, **REASON)
    assert run('delivery-connection show', {'connection': 'Office'})['daily_limit'] is None
    run('delivery-connection disable', {'connection': 'Office'}, **REASON)
    number = desk['ready']()
    assert run('delivery run', {}, **REASON)['claimed'] == 0
    run('delivery-connection remove', {'connection': 'Office'}, **REASON)
    assert run('delivery-connection query', {})['rows'] == []
    assert run('delivery-connection query', {'include_removed': True})['rows'][0]['removed_at']
    assert run('delivery show', {'message': number})['status'] == 'ready'


# ---------------------------------------------------------------- the conformance suite, against the fake

@pytest.mark.parametrize('case', contract.CASES)
def test_the_fake_passes_the_conformance_suite(desk, case):
    connect(desk['run'])
    contract.check(case, desk['backend'], desk)


# ---------------------------------------------------------------- the state table

def test_backoff_is_five_minutes_thirty_minutes_two_hours_then_failed(desk):
    run, backend = desk['run'], desk['backend']
    connect(run)
    number = desk['ready']()
    backend.plan('temporary', 'temporary', 'temporary', 'temporary')
    waits = []
    for expected in (5, 30, 120):
        start = desk['clock'].at
        assert run('delivery run', {}, **REASON)['retrying'] == 1
        due = datetime.fromisoformat(run('delivery show', {'message': number})['next_attempt_at'].replace('Z', '+00:00'))
        waits.append(round((due - start).total_seconds() / 60))
        desk['clock'].advance(expected - 1)
        assert run('delivery run', {}, **REASON)['claimed'] == 0
        desk['clock'].advance(1)
    assert waits == [5, 30, 120]
    assert run('delivery run', {}, **REASON)['failed'] == 1
    failed = run('delivery show', {'message': number})
    assert failed['status'] == 'failed' and failed['attempts'] == 4 and failed['failure_code'] == 'busy'
    # A person's retry gives four more attempts, with fresh attempt keys.
    run('delivery retry', {'message': number}, **REASON)
    assert run('delivery run', {}, **REASON)['sent'] == 1
    assert sorted(backend.received) == [f"{failed['id']}:{n}" for n in range(1, 6)]
    assert backend.deliveries_of(number) == 1


def test_a_crash_between_claim_and_record_is_held_when_the_lease_runs_out(desk, monkeypatch):
    from bookflow.company import delivery_dispatch
    run, backend = desk['run'], desk['backend']
    connect(run)
    number = desk['ready']()
    real = delivery_dispatch.send

    def crash(claims, polls, **hooks):
        real(claims, polls, **hooks)  # the bytes left
        raise RuntimeError('host killed before the record')

    monkeypatch.setattr(delivery_dispatch, 'send', crash)
    with pytest.raises(Exception):
        run('delivery run', {}, **REASON)
    monkeypatch.setattr(delivery_dispatch, 'send', real)
    assert run('delivery show', {'message': number})['status'] == 'sending'
    desk['clock'].advance(9)
    assert run('delivery run', {}, **REASON)['expired'] == 0
    desk['clock'].advance(2)
    out = run('delivery run', {}, **REASON)
    assert out['expired'] == 1 and out['claimed'] == 0
    held = run('delivery show', {'message': number})
    assert held['status'] == 'held' and held['hold_reason'] == 'outcome_unknown'
    assert held['next_commands'][:2] == [f'delivery mark-sent {number}', f'delivery retry {number}']
    desk['clock'].advance(24 * 60)
    assert run('delivery run', {}, **REASON)['claimed'] == 0 and backend.deliveries_of(number) == 1
    assert _events(run, number) == ['drafted', 'released', 'claimed', 'held']
    # The person finds it in the sent mail and records it.
    sent = run('delivery mark-sent', {'message': number}, **REASON)
    assert sent['status_label'] == 'Sent by hand' and backend.deliveries_of(number) == 1


def test_an_unknown_outcome_or_a_raise_is_held_and_a_late_answer_moves_nothing(desk):
    run, backend = desk['run'], desk['backend']
    connect(run)
    first, second = desk['ready'](), desk['ready']()
    backend.plan('unknown', 'raise')
    out = run('delivery run', {}, **REASON)
    assert out['held'] == 2 and out['sent'] == 0
    for number in (first, second):
        message = run('delivery show', {'message': number})
        assert message['hold_reason'] == 'outcome_unknown'
    raised = run('delivery history', {'message': second})['events'][-1]
    assert raised['event'] == 'held' and 'TimeoutError' in raised['details']['text']
    # Retried by a person, it is sent once more: that is the person's choice, never the dispatcher's.
    retried = run('delivery retry', {'message': first}, **REASON)
    assert retried['status'] == 'ready' and any('sent mail' in w for w in retried['warnings'])
    assert run('delivery run', {}, **REASON)['sent'] == 1


def test_claims_hold_a_changed_or_voided_document_and_retry_points_at_refresh(desk):
    run = desk['run']
    connect(run)
    changed_doc, voided_doc = desk['invoice'](), desk['invoice']()
    changed, voided = desk['ready'](changed_doc['number']), desk['ready'](voided_doc['number'])
    run('invoice update', {'invoice': changed_doc['id'], 'memo': 'changed'}, reason='Correction')
    run('invoice void', {'invoice': voided_doc['id']}, reason='Wrong customer')
    out = run('delivery run', {}, **REASON)
    assert out['held'] == 2 and out['claimed'] == 0 and desk['backend'].delivered == []
    assert run('delivery show', {'message': changed})['hold_reason'] == 'document_changed'
    assert run('delivery show', {'message': voided})['hold_reason'] == 'document_voided'
    with pytest.raises(BookflowError) as refused:
        run('delivery retry', {'message': changed}, **REASON)
    assert refused.value.code == 'E_WORK_DEPENDENCY' and '--refresh' in refused.value.details['next']
    run('delivery update', {'message': changed, 'refresh': True}, **REASON)
    run('delivery send', {'messages': [changed]}, **REASON)
    assert run('delivery run', {}, **REASON)['sent'] == 1
    for number in (changed, voided):
        assert [e for e in _events(run, number) if e == 'held'] == ['held']


def test_the_daily_limit_caps_attempts_in_any_24_hours(desk):
    run = desk['run']
    connect(run, daily_limit=2)
    numbers = [desk['ready']() for _ in range(3)]
    assert run('delivery run', {}, **REASON)['sent'] == 2
    assert run('delivery show', {'message': numbers[2]})['status'] == 'ready'
    desk['clock'].advance(23 * 60)
    assert run('delivery run', {}, **REASON)['claimed'] == 0
    desk['clock'].advance(61)
    assert run('delivery run', {}, **REASON)['sent'] == 1
    assert run('delivery-connection show', {'connection': 'Office'})['attempts_last_24h'] == 1


def test_tracking_events_are_recorded_and_a_bounce_raises_one_to_do(desk):
    run, backend = desk['run'], desk['backend']
    connect(run)
    good, bad = desk['ready'](), desk['ready']()
    assert run('delivery run', {}, **REASON)['sent'] == 2
    refs = {n: run('delivery show', {'message': n})['provider_ref'] for n in (good, bad)}
    backend.report(refs[good], 'delivered')
    backend.report(refs[bad], 'bounced', 'mailbox full')
    assert run('delivery run', {}, **REASON)['tracking_events'] == 2
    assert run('delivery run', {}, **REASON)['tracking_events'] == 0
    assert run('delivery show', {'message': good})['delivery'] == 'delivered'
    bounced = run('delivery show', {'message': bad})
    assert bounced['delivery'] == 'bounced' and bounced['status'] == 'sent'
    todos = [t for t in run('todo list', {})['items'] if bad in t['title']]
    assert len(todos) == 1 and 'mailbox full' in todos[0]['notes']
    assert 'opened' not in str(bounced).lower()


def test_a_restored_copy_arrives_with_every_connection_disabled(desk):
    run, client = desk['run'], desk['client']
    connect(run)
    archive = client.run('company backup', {}, company=desk['company'])['path']
    restored = client.run('company restore', {'archive': archive, 'as_copy': True, 'name': 'Queue Copy'},
                          reason='Restore witness')
    copy = client.run('delivery-connection show', {'connection': 'Office'}, company=restored['company_id'])
    assert not copy['enabled'] and not copy['enabled_here'] and not copy['usable']
    assert run('delivery-connection show', {'connection': 'Office'})['usable']
    # Its ready messages wait: nothing in the copy can send until a person enables a connection there.
    assert client.run('delivery run', {}, company=restored['company_id'], **REASON)['claimed'] == 0


# ---------------------------------------------------------------- nothing reaches out

def _block_the_outside(monkeypatch):
    calls = []

    def refuse(name):
        def blocked(*args, **kwargs):
            calls.append(name)
            raise OSError(f'{name} is blocked in this test')
        return blocked

    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, refuse('socket.' + name))
    for name in ('create_connection', 'getaddrinfo', 'gethostbyname'):
        monkeypatch.setattr(socket, name, refuse('socket.' + name))
    monkeypatch.setattr(subprocess, 'Popen', refuse('subprocess.Popen'))
    for name in ('system', 'posix_spawn', 'posix_spawnp', 'fork', 'execv', 'execve'):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, refuse('os.' + name))
    return calls


def test_a_full_pass_opens_no_socket_and_starts_no_process(desk, monkeypatch):
    run = desk['run']
    numbers = [desk['ready'](), desk['ready']()]  # drafted, rendered and released before the blocker
    calls = _block_the_outside(monkeypatch)
    # With no connection: release, export, a dispatch pass, each touching nothing outside.
    third = run('delivery draft', {'kind': 'invoice', 'document': desk['invoice']()['number']}, reason='Email it')
    run('delivery send', {'messages': [third['number']]}, **REASON)
    run('delivery export', {'messages': numbers}, **REASON)
    assert run('delivery run', {}, **REASON)['claimed'] == 0
    assert {run('delivery show', {'message': n})['status'] for n in numbers} == {'ready'}
    # With the fake (in-process) connection, the whole claim, send and record path touches nothing outside either.
    connect(run)
    assert run('delivery run', {}, **REASON)['sent'] == 3
    assert calls == []


# ---------------------------------------------------------------- the go, the host's tick, and its idle cost

@pytest.fixture
def hosted_queue(hosted):  # noqa: F811
    with registered(FakeBackend()) as backend:
        yield hosted, backend


def _hosted_mail(hosted):
    """The demo company with a customer address, the way increment 1's tests set it up."""
    from tests.test_document_print import commands as make_commands, seed
    books = seed(make_commands(hosted))
    run = books['run']
    run('customer update', {'customer': books['customer'], 'email': 'accounts@harbourmills.example'})
    return books


@pytest.mark.timeout(900)
def test_the_go_holder_non_holder_and_a_holder_who_lost_it_and_the_tick(hosted_queue):
    from tests.test_people_only import REASON as PERSON, _agent
    hosted, backend = hosted_queue
    cid, host = hosted.company_id, hosted.handle.host
    books = _hosted_mail(hosted)
    person = {**hosted.bearer, **PERSON}
    _, as_agent = _agent(hosted)

    # No connection: the tick costs one EXISTS and claims nothing.
    idle = host.schedule_now()[cid]
    assert 'delivery' not in idle

    hosted.ok('delivery-connection.add', {'name': 'Office', 'kind': 'fake', 'settings': SETTINGS}, company=cid,
              headers=person)
    refused = hosted.call('delivery-connection.enable', {'connection': 'Office'}, company=cid, headers=as_agent)
    assert refused.status_code == 403 and refused.json()['details']['reason'] == 'people_only'
    hosted.ok('delivery-connection.enable', {'connection': 'Office'}, company=cid, headers=person)

    # A non-holder (an agent): released, it waits; its approval is refused; a person's go sends it on the tick.
    draft = hosted.ok('delivery.draft', {'kind': 'invoice', 'document': 'DOC-100'}, company=cid, headers=as_agent)
    hosted.ok('delivery.send', {'messages': [draft['number']]}, company=cid, headers=as_agent)
    assert host.schedule_now()[cid]['delivery']['claimed'] == 0
    r = hosted.call('delivery.approve', {'messages': [draft['number']]}, company=cid, headers=as_agent)
    assert r.status_code == 403 and r.json()['details']['reason'] == 'people_only'
    approved = hosted.ok('delivery.approve', {'all_waiting': True}, company=cid, headers=person)
    assert [m['number'] for m in approved['messages']] == [draft['number']]
    ticked = host.schedule_now()[cid]['delivery']
    assert ticked['claimed'] == 1 and ticked['sent'] == 1 and backend.deliveries_of(draft['number']) == 1

    # A holder who lost it before the claim: released by a clerk, who is then deactivated; the go is withdrawn.
    hosted.ok('user.add', {'username': 'clerk', 'company': cid, 'role': 'standard', 'password': 'pw-clerk-12345'},
              headers=PERSON)
    clerk = hosted.ok('token.issue', {'user': 'clerk', 'label': 'clerk'}, headers=PERSON)['secret']
    as_clerk = {'Authorization': 'Bearer ' + clerk, **PERSON}
    receipt = hosted.ok('delivery.draft', {'kind': 'sales-receipt', 'document': 'DOC-200'}, company=cid,
                        headers=as_clerk)
    released = hosted.ok('delivery.send', {'messages': [receipt['number']]}, company=cid, headers=as_clerk)
    assert released['messages'][0]['go_by']
    hosted.ok('user.deactivate', {'user': 'clerk'}, headers=PERSON)
    run = books['run']
    out = run('delivery run', {})
    assert out['go_withdrawn'] == 1 and out['claimed'] == 0 and backend.deliveries_of(receipt['number']) == 0
    waiting = run('delivery show', {'message': receipt['number']})
    assert waiting['waiting_for_go'] and waiting['status_label'] == 'Waiting for your go'
    assert run('delivery history', {'message': receipt['number']})['events'][-1]['details']['go_withdrawn']


@pytest.mark.timeout(900)
def test_the_idle_tick_costs_nothing_measurable(hosted):
    """Measured: a company with no connection and nothing in flight, the scheduler tick with and without the
    delivery hook, and the hook's one statement on the writer."""
    from bookflow.company import delivery_dispatch
    host, cid = hosted.handle.host, hosted.company_id
    host.schedule_now()

    def ticks(n=25):
        times = []
        for _ in range(n):
            started = time.perf_counter()
            host.schedule_now()
            times.append(time.perf_counter() - started)
        return statistics.median(times)

    with_hook = ticks()
    original = delivery_dispatch.tick_claim
    delivery_dispatch.tick_claim = lambda *a, **k: None
    try:
        without = ticks()
    finally:
        delivery_dispatch.tick_claim = original

    def statement():
        db = host._companies.get(cid) or next(iter(host._companies.values()))
        started = time.perf_counter()
        for _ in range(1000):
            assert not delivery_dispatch.has_work(db.conn)
        return (time.perf_counter() - started) / 1000

    per_statement = host.submit(statement, _maintenance=True)
    print(f'idle tick median with the delivery hook {with_hook * 1000:.2f} ms, without {without * 1000:.2f} ms; '
          f'the hook statement {per_statement * 1e6:.1f} us')
    assert per_statement < 0.001


# ---------------------------------------------------------------- review fixes (r112-inc2-review.md)

def _hang(monkeypatch):
    """A fake transport whose ``send`` hangs until the test lets it go; returns (entered, release)."""
    import threading
    from tests.delivery_fake_transport import FakeTransport
    entered, release = threading.Event(), threading.Event()
    real = FakeTransport.send

    def hung(self, message, attempt_key):
        entered.set()
        release.wait(60)
        return real(self, message, attempt_key)

    monkeypatch.setattr(FakeTransport, 'send', hung)
    return entered, release


def test_a_send_that_hangs_is_bounded_and_held_for_a_person(desk, monkeypatch):
    run, backend = desk['run'], desk['backend']
    connect(run)
    number = desk['ready']()
    monkeypatch.setattr(transport, 'SEND_TIMEOUT', 0.5)
    entered, release = _hang(monkeypatch)
    started = time.monotonic()
    out = run('delivery run', {}, **REASON)
    assert time.monotonic() - started < 30 and entered.is_set()
    assert out['held'] == 1 and out['sent'] == 0
    held = run('delivery show', {'message': number})
    assert held['hold_reason'] == 'outcome_unknown' and held['go_by'] is None
    assert 'no answer within' in run('delivery history', {'message': number})['events'][-1]['details']['text']
    release.set()


def test_each_claim_is_read_again_before_its_send_and_a_lapsed_one_is_skipped(desk, monkeypatch):
    from tests.delivery_fake_transport import FakeTransport
    run, backend, clock = desk['run'], desk['backend'], desk['clock']
    connect(run)
    first, second = desk['ready'](), desk['ready']()
    real = FakeTransport.send

    def slow(self, message, attempt_key):
        if message.number == first:
            clock.advance(9)  # a slow first send: the chunk's lease has less left than one bounded send
        return real(self, message, attempt_key)

    monkeypatch.setattr(FakeTransport, 'send', slow)
    out = run('delivery run', {}, **REASON)
    assert out['sent'] == 1 and backend.deliveries_of(first) == 1 and backend.deliveries_of(second) == 0
    waiting = run('delivery show', {'message': second})
    assert waiting['status'] == 'ready' and waiting['go_by'] and waiting['next_attempt_at'] is None
    assert run('delivery run', {}, **REASON)['sent'] == 1 and backend.deliveries_of(second) == 1


def test_a_late_acceptance_resolves_to_sent_and_retry_refuses_after_one(desk, monkeypatch):
    from bookflow.company import delivery_dispatch
    run, backend, clock = desk['run'], desk['backend'], desk['clock']
    connect(run)
    real = delivery_dispatch.send
    lost = []

    def crash(claims, polls, **hooks):
        lost.extend(real(claims, polls, **hooks)[0])  # the bytes left; the answer never reached the record
        raise RuntimeError('host killed before the record')

    def late(claims, polls, **hooks):
        outcomes, reports = real(claims, polls, **hooks)
        return lost + outcomes, reports  # the lost answer arrives with this pass

    # 1. Accepted, the answer lost, the lease runs out: held. The answer arrives late: sent, and nothing sends it again.
    number = desk['ready']()
    monkeypatch.setattr(delivery_dispatch, 'send', crash)
    with pytest.raises(Exception):
        run('delivery run', {}, **REASON)
    clock.advance(11)
    monkeypatch.setattr(delivery_dispatch, 'send', real)
    run('delivery run', {}, **REASON)
    assert run('delivery show', {'message': number})['hold_reason'] == 'outcome_unknown'
    monkeypatch.setattr(delivery_dispatch, 'send', late)
    run('delivery run', {}, **REASON)
    lost.clear()
    sent = run('delivery show', {'message': number})
    assert sent['status'] == 'sent' and sent['provider_ref'] and backend.deliveries_of(number) == 1

    # 2. Retried before the late answer, claimed again and unknown: held, and the acceptance on record refuses a retry.
    other = desk['ready']()
    monkeypatch.setattr(delivery_dispatch, 'send', crash)
    with pytest.raises(Exception):
        run('delivery run', {}, **REASON)
    clock.advance(11)
    monkeypatch.setattr(delivery_dispatch, 'send', real)
    run('delivery run', {}, **REASON)
    run('delivery retry', {'message': other}, **REASON)
    backend.plan('unknown')
    monkeypatch.setattr(delivery_dispatch, 'send', late)
    run('delivery run', {}, **REASON)
    held = run('delivery show', {'message': other})
    assert held['status'] == 'held' and held['hold_reason'] == 'outcome_unknown'
    with pytest.raises(BookflowError) as refused:
        run('delivery retry', {'message': other}, **REASON)
    assert refused.value.code == 'E_WORK_DEPENDENCY' and refused.value.details['next'] == f'delivery mark-sent {other}'


def test_a_connection_from_another_installation_or_an_attached_file_is_disabled(desk):
    from bookflow.core.config import os_login
    run, client = desk['run'], desk['client']
    connect(run)
    number = desk['ready']()
    root = __import__('pathlib').Path(os.environ['BOOKFLOW_DATA_ROOT'])
    install = root / 'install-id'
    first = install.read_text()
    # The same host name and data folder under another installation (a machine replaced, the folder copied over).
    install.write_text('0' * 32 + '\n')
    out = run('delivery run', {}, **REASON)
    assert out['claimed'] == 0 and [m['act'] for m in out['messages']] == ['connection disabled']
    shown = run('delivery-connection show', {'connection': 'Office'})
    assert not shown['enabled'] and not shown['usable'] and desk['backend'].delivered == []
    install.write_text(first)
    connect_again = run('delivery-connection enable', {'connection': 'Office'}, **REASON)
    assert connect_again['usable']
    # Detached and attached by hand: it arrives disabled.
    detached = client.run('company detach', {'company': desk['company']}, reason='Hand copy witness')
    client.run('company attach', {'path': detached['path'], 'administrator': os_login()}, reason='Hand copy witness')
    shown = run('delivery-connection show', {'connection': 'Office'})
    assert not shown['enabled'] and not shown['usable']
    assert run('delivery run', {}, **REASON)['claimed'] == 0
    assert run('delivery show', {'message': number})['status'] == 'ready'


def test_tracking_goes_on_after_delivered_and_a_later_bounce_raises_the_to_do(desk):
    run, backend = desk['run'], desk['backend']
    connect(run)
    number = desk['ready']()
    run('delivery run', {}, **REASON)
    ref = run('delivery show', {'message': number})['provider_ref']
    backend.report(ref, 'delivered')
    assert run('delivery run', {}, **REASON)['tracking_events'] == 1
    backend.report(ref, 'bounced', 'mailbox gone; contact postmaster@far.example')
    assert run('delivery run', {}, **REASON)['tracking_events'] == 1
    assert run('delivery show', {'message': number})['delivery'] == 'bounced'
    todos = [t for t in run('todo list', {})['items'] if number in t['title']]
    assert len(todos) == 1 and 'postmaster@' not in todos[0]['notes'] and '[address]' in todos[0]['notes']


def test_a_dry_run_check_asks_nothing_and_transport_text_is_redacted(desk):
    run, backend = desk['run'], desk['backend']
    connect(run)
    preview = desk['client'].run('delivery-connection check', {'connection': 'Office'}, company=desk['company'],
                                 dry_run=True, **REASON)
    assert backend.checks == 0 and preview['last_check_at'] is None
    assert run('delivery-connection check', {'connection': 'Office'}, **REASON)['last_check_ok'] and backend.checks == 1
    assert transport.SECRET_REF.match('env:FAKE_MAIL_TOKEN') and not transport.SECRET_REF.match('env:FAKE_MAIL_TOKEN\n')
    said = transport.clean_text('550 no such mailbox pay@adams.example; password=hunter2 '
                                'Authorization: Bearer abcdefgh12345678 key sk_live_0123456789abcdefghijkl ' + 'x' * 900)
    for secret in ('pay@adams', 'hunter2', 'abcdefgh12345678', 'sk_live'):
        assert secret not in said
    assert said.startswith('550 no such mailbox [address]') and len(said) == transport.TEXT_LIMIT


@pytest.mark.timeout(900)
def test_a_go_ends_with_the_content_it_saw_and_counts_only_through_a_standing_membership(hosted_queue):
    """High 1 and Medium 6, through the host: a person releases, the document changes, the claim holds it, an agent
    refreshes and releases, and nothing sends. A go's giver denied ``customer`` on their membership, or an
    installation administrator with no membership, gives no go either. Medium 2: a `delivery run` through the host
    leaves the commit barrier open while the transport talks."""
    from tests.delivery_fake_transport import FakeTransport
    from tests.test_people_only import REASON as PERSON, _agent
    hosted, backend = hosted_queue
    cid, host = hosted.company_id, hosted.handle.host
    books = _hosted_mail(hosted)
    person = {**hosted.bearer, **PERSON}
    _, as_agent = _agent(hosted)
    hosted.ok('delivery-connection.add', {'name': 'Office', 'kind': 'fake', 'settings': SETTINGS}, company=cid,
              headers=person)
    hosted.ok('delivery-connection.enable', {'connection': 'Office'}, company=cid, headers=person)

    # High 1: the go does not survive the hold, the refresh or the agent's release.
    draft = hosted.ok('delivery.draft', {'kind': 'invoice', 'document': 'DOC-100'}, company=cid, headers=person)
    number = draft['number']
    assert hosted.ok('delivery.send', {'messages': [number]}, company=cid, headers=person)['messages'][0]['go_by']
    invoice = books['run']('invoice show', {'invoice': 'DOC-100'})
    change = {'invoice': invoice['id'], 'memo': 'Changed after the go', 'operation_key': 'go-1',
              'expected_version': invoice['version']}
    guard = hosted.call('invoice.update', change, company=cid, headers=person).json()['details']['settlement_guard']
    hosted.ok('invoice.update', dict(change, settlement_guard=guard), company=cid, headers=person)
    out = hosted.ok('delivery.run', {}, company=cid, headers=person)
    assert out['held'] == 1 and out['claimed'] == 0
    held = hosted.ok('delivery.show', {'message': number}, company=cid)
    assert held['hold_reason'] == 'document_changed' and held['go_by'] is None
    history = hosted.ok('delivery.history', {'message': number}, company=cid)['events']
    assert history[-1]['event'] == 'held' and history[-1]['details']['go_withdrawn']
    refreshed = hosted.ok('delivery.update', {'message': number, 'refresh': True}, company=cid, headers=as_agent)
    assert refreshed['status'] == 'draft' and refreshed['go_by'] is None
    released = hosted.ok('delivery.send', {'messages': [number]}, company=cid, headers=as_agent)['messages'][0]
    assert released['status'] == 'ready' and released['go_by'] is None and released['waiting_for_go']
    assert hosted.ok('delivery.run', {}, company=cid, headers=person)['claimed'] == 0
    assert host.schedule_now()[cid].get('delivery', {}).get('claimed', 0) == 0
    assert backend.deliveries_of(number) == 0

    # Medium 2: approved, it is sent by `delivery run` through the host with the commit barrier open.
    seen = []
    real = FakeTransport.send

    def watch(self, message, attempt_key):
        admission = host.publication_admission
        try:
            admission.begin_validation()
            seen.append(admission._barrier is None)
        except Exception as error:  # noqa: BLE001 - what a reader would meet
            seen.append(type(error).__name__)
        return real(self, message, attempt_key)

    FakeTransport.send = watch
    try:
        hosted.ok('delivery.approve', {'messages': [number]}, company=cid, headers=person)
        assert hosted.ok('delivery.run', {}, company=cid, headers=person)['sent'] == 1
    finally:
        FakeTransport.send = real
    assert seen == [True] and backend.deliveries_of(number) == 1

    # Medium 6: a clerk's go ends when the clerk's membership denies customer.
    def hub_sql(sql, *args):
        hub = host.submit(host._ensure_hub_on_writer, _maintenance=True)
        return host.submit(lambda: hub.raw.execute(sql, args).rowcount, _maintenance=True)

    for name in ('clerk', 'boss'):
        hosted.ok('user.add', {'username': name, 'company': cid, 'role': 'standard', 'password': f'pw-{name}-12345'},
                  headers=PERSON)
    tokens = {name: hosted.ok('token.issue', {'user': name, 'label': name}, headers=PERSON)['secret']
              for name in ('clerk', 'boss')}
    gone = []
    for name in ('clerk', 'boss'):
        made = hosted.ok('delivery.draft', {'kind': 'sales-receipt', 'document': 'DOC-200', 'again': name == 'boss'},
                         company=cid, headers={'Authorization': 'Bearer ' + tokens[name], **PERSON})
        hosted.ok('delivery.send', {'messages': [made['number']]}, company=cid,
                  headers={'Authorization': 'Bearer ' + tokens[name], **PERSON})
        gone.append(made['number'])
    assert hub_sql("UPDATE memberships SET denies = '[\"customer\"]' WHERE user_id = "
                   "(SELECT id FROM users WHERE username = 'clerk')") == 1
    # The boss becomes an installation administrator whose company membership is revoked.
    assert hub_sql("UPDATE users SET hub_admin = 1 WHERE username = 'boss'") == 1
    assert hub_sql("UPDATE memberships SET revoked_at = '2026-10-27T00:00:00.000Z' WHERE user_id = "
                   "(SELECT id FROM users WHERE username = 'boss')") == 1
    out = hosted.ok('delivery.run', {}, company=cid, headers=person)
    assert out['go_withdrawn'] == 2 and out['claimed'] == 0
    for gone_number in gone:
        assert backend.deliveries_of(gone_number) == 0
        assert hosted.ok('delivery.show', {'message': gone_number}, company=cid)['waiting_for_go']


@pytest.mark.timeout(900)
def test_the_host_sends_off_its_timer_thread_bounded_and_holds_a_send_in_flight_at_shutdown(hosted_queue,
                                                                                           monkeypatch):
    """Medium 3 and 4 and the tick's lows, through the host: a claim held while an earlier send was slow is never sent
    and the late acceptance of the earlier one resolves it to sent; the backoff counts from the answer; a poll-only
    tick takes no commit barrier; a hung transport stalls no checkpoint and shutdown holds it for a person."""
    from tests.delivery_fake_transport import FakeTransport
    from tests.test_people_only import REASON as PERSON
    hosted, backend = hosted_queue
    cid, host = hosted.company_id, hosted.handle.host
    _hosted_mail(hosted)
    person = {**hosted.bearer, **PERSON}
    clock = Clock(monkeypatch)
    hosted.ok('delivery-connection.add', {'name': 'Office', 'kind': 'fake', 'settings': SETTINGS}, company=cid,
              headers=person)
    hosted.ok('delivery-connection.enable', {'connection': 'Office'}, company=cid, headers=person)

    def ready(kind, document):
        made = hosted.ok('delivery.draft', {'kind': kind, 'document': document}, company=cid, headers=person)
        hosted.ok('delivery.send', {'messages': [made['number']]}, company=cid, headers=person)
        return made['number']

    # Medium 3: the first send is slow; meanwhile a pass expires the chunk. The second is never sent.
    n1, n2 = ready('invoice', 'DOC-100'), ready('sales-receipt', 'DOC-200')
    real = FakeTransport.send
    log = {}

    def slow(self, message, attempt_key):
        if not log:
            log['first'] = message.number
            clock.advance(11)
            log['expired'] = hosted.ok('delivery.run', {}, company=cid, headers=person)['expired']
        return real(self, message, attempt_key)

    monkeypatch.setattr(FakeTransport, 'send', slow)
    host.schedule_now()
    monkeypatch.setattr(FakeTransport, 'send', real)
    assert log == {'first': n1, 'expired': 2}
    assert backend.deliveries_of(n1) == 1 and backend.deliveries_of(n2) == 0
    assert hosted.ok('delivery.show', {'message': n1}, company=cid)['status'] == 'sent'
    assert hosted.ok('delivery.show', {'message': n2}, company=cid)['hold_reason'] == 'outcome_unknown'

    # Low: a tick with only tracking to ask (n1 accepted, n2 held, the poll due) takes no commit barrier.
    clock.advance(16)
    epoch = host.publication_admission._epoch
    assert host.schedule_now()[cid]['delivery']['claimed'] == 0
    assert host.publication_admission._epoch is epoch

    # Low: a temporary failure's backoff counts from the answer, not the claim.
    hosted.ok('delivery.retry', {'message': n2}, company=cid, headers=person)
    backend.plan('temporary')

    def slower(self, message, attempt_key):
        time.sleep(1.5)
        return real(self, message, attempt_key)

    monkeypatch.setattr(FakeTransport, 'send', slower)
    claimed_at = clock.at
    host.schedule_now()
    monkeypatch.setattr(FakeTransport, 'send', real)
    waiting = hosted.ok('delivery.show', {'message': n2}, company=cid)
    due = datetime.fromisoformat(waiting['next_attempt_at'].replace('Z', '+00:00'))
    assert due - claimed_at >= timedelta(minutes=5, seconds=1)

    # Medium 4: a hung transport runs on the delivery thread; the timer's work goes on; shutdown holds it.
    clock.advance(10)
    entered, release = _hang(monkeypatch)
    try:
        host.schedule_now(wait=False)
        assert entered.wait(30)
        started = time.monotonic()
        host.checkpoint_now()
        host.sweep_now()
        assert time.monotonic() - started < 10
        host.delivery_stop_seconds = 0.5
        started = time.monotonic()
        host._stop_delivery()
        assert time.monotonic() - started < 10
    finally:
        release.set()
    held = hosted.ok('delivery.show', {'message': n2}, company=cid)
    assert held['status'] == 'held' and held['hold_reason'] == 'outcome_unknown'
    assert 'host stopped' in hosted.ok('delivery.history', {'message': n2}, company=cid)['events'][-1]['details']['text']

