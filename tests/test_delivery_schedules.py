"""R112 increment 4: recurring schedules (design/document-delivery.md §6, §11).

On a test clock: a month-end statement schedule across February and a daily queued-invoice schedule run once per slot;
after downtime only the latest missed slot runs, and only inside the catch-up window; a run interrupted mid-way goes on
from its batch; a send schedule an agent drafted sends nothing until a person makes it live after one check of its
exact details; an edit by an agent returns it to draft; an establisher deactivated or without the authority pauses it
with one to-do, and nothing is sent; and the scheduler's idle tick for a company with no schedules is measured.
"""
import statistics
import time
from datetime import datetime, timezone

import pytest

from bookflow import BookflowError
from bookflow.core import clock as bf_clock
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_row3_host import hosted  # noqa: F401


class Clock:
    def __init__(self, monkeypatch, day='2026-01-01'):
        self.set(day)
        monkeypatch.setattr(bf_clock, 'now', lambda: self.at)

    def set(self, day, hour=12):
        self.at = datetime.fromisoformat(day).replace(hour=hour, tzinfo=timezone.utc)


def _slots(run, name):
    return sorted((r['slot_date'], r['state'], r['reason']) for r in run('delivery-schedule show',
                                                                          {'schedule': name, 'runs': 100})['runs'])


@pytest.fixture
def office(books, monkeypatch):  # noqa: F811
    client, company, run = books['client'], books['company'], books['run']
    visit = client.item.create(company=company, name='Site visit', type='service', income_account_id=books['income'],
                               price='50.00')['id']
    run('customer update', {'customer': books['customer'], 'email': 'pay@adams.example',
                            'preferred_delivery_method': 'email'})
    baker = client.customer.create(company=company, name='Baker Homes', email='ap@baker.example',
                                   preferred_delivery_method='email')['id']

    def invoice(number, day, customer=None):
        return run('invoice post', {'number': number, 'date': day, 'customer': customer or books['customer'],
                                    'lines': [{'item': visit, 'quantity': '1'}]}, reason='Seed')['number']

    return dict(books, visit=visit, baker=baker, invoice=invoice, clock=Clock(monkeypatch))


def test_month_end_statements_across_february_run_once_per_slot(office):
    run, clock, invoice = office['run'], office['clock'], office['invoice']
    from bookflow.company.delivery_schedules import anchor_day, due_slots
    # A month-end start stays at month end, leap years included; a 30th stays the 30th (clamped in February).
    row = dict(frequency='monthly', next_date='2027-01-31', anchor_day=anchor_day('monthly', '2027-01-31'),
               stop_date=None, remaining_runs=None)
    assert due_slots(row, '2027-05-01') == ['2027-01-31', '2027-02-28', '2027-03-31', '2027-04-30']
    row.update(next_date='2028-02-29', anchor_day=anchor_day('monthly', '2028-02-29'))
    assert due_slots(row, '2028-04-30') == ['2028-02-29', '2028-03-31', '2028-04-30']
    row.update(next_date='2027-01-30', anchor_day=anchor_day('monthly', '2027-01-30'))
    assert due_slots(row, '2027-03-31') == ['2027-01-30', '2027-02-28', '2027-03-30']

    # Missing details are asked for, never guessed.
    for bad, field in (({'kind': 'statement', 'frequency': 'monthly', 'start_date': '2026-01-31'}, 'period'),
                       ({'kind': 'statement', 'period': 'month-to-date'}, 'frequency'),
                       ({'kind': 'invoice', 'frequency': 'daily', 'start_date': '2026-01-31'}, 'selection')):
        with pytest.raises(BookflowError) as refused:
            run('delivery-schedule create', {'name': 'Bad', **bad})
        assert refused.value.code == 'E_VALIDATION' and refused.value.details['fields'][0]['field'] == field

    invoice('F-1', '2026-02-10')
    invoice('F-2', '2026-03-05', office['baker'])
    made = run('delivery-schedule create', {'name': 'Month-end statements', 'kind': 'statement',
                                            'period': 'month-to-date', 'balance_over': '0', 'frequency': 'monthly',
                                            'start_date': '2026-01-31'}, reason='Statements each month end')
    assert made['state'] == 'draft' and made['mode'] == 'draft' and made['next_date'] == '2026-01-31'
    assert '(month end)' in made['cadence'] and 'month to date' in made['selection_text']

    clock.set('2026-01-30')
    assert 'Nothing is due' in run('delivery-schedule run', {'schedule': 'Month-end statements'})['warnings'][0]
    for day, owed in (('2026-01-31', 0), ('2026-02-28', 1), ('2026-03-31', 2)):
        clock.set(day)
        out = run('delivery-schedule run', {'schedule': 'Month-end statements'}, reason='On schedule')
        assert [(r['slot_date'], r['state'], r['drafted']) for r in out['ran']] == [(day, 'done', owed)]
        # A second run the same day finds the slot already there.
        again = run('delivery-schedule run', {'schedule': 'Month-end statements'})
        assert again['ran'] == [] and 'Nothing is due' in again['warnings'][0]
    assert _slots(run, 'Month-end statements') == [('2026-01-31', 'done', None), ('2026-02-28', 'done', None),
                                                   ('2026-03-31', 'done', None)]
    march = run('delivery-schedule show', {'schedule': 'Month-end statements'})['runs'][0]
    assert (march['date_from'], march['date_to']) == ('2026-03-01', '2026-03-31')
    assert run('delivery-schedule show', {'schedule': 'Month-end statements'})['next_date'] == '2026-04-30'
    # Draft mode leaves drafts; the messages name the statement period.
    rows = run('delivery query', {'status': 'all', 'kind': 'statement', 'limit': 50})['rows']
    assert len(rows) == 3 and {r['status'] for r in rows} == {'draft'}


def test_catch_up_runs_only_the_latest_missed_slot_inside_the_window(office):
    run, clock, invoice = office['run'], office['clock'], office['invoice']
    for n in range(3):
        invoice(f'Q-{n}', '2026-04-01')
    run('company update', {'delivery_auto_flag_from': '2026-04-01'})
    run('delivery-schedule create', {'name': 'Daily invoices', 'kind': 'invoice', 'queued': True,
                                     'frequency': 'daily', 'start_date': '2026-04-01'})
    run('delivery-schedule create', {'name': 'Weekly invoices', 'kind': 'invoice', 'unsent': True,
                                     'frequency': 'weekly', 'start_date': '2026-04-01', 'catch_up_days': 3})
    # The host was off from 31 March to 20 April: the daily schedule runs only 20 April; the 13th to 19th are
    # superseded, the 1st to 12th outside the window. The weekly one's latest slot (the 15th) is 5 days old, past its
    # 3-day window: nothing runs and every slot is skipped.
    clock.set('2026-04-20')
    out = run('delivery-schedule run', {'schedule': 'Daily invoices'}, reason='Back on')
    states = _slots(run, 'Daily invoices')
    assert states[-1] == ('2026-04-20', 'done', None) and len(states) == 20
    assert {s for s in states if s[2] == 'superseded'} == {(f'2026-04-{d}', 'skipped', 'superseded')
                                                           for d in range(13, 20)}
    assert sum(1 for s in states if s[2] == 'outside_window') == 12
    assert out['ran'][-1]['drafted'] == 3
    weekly = run('delivery-schedule run', {'schedule': 'Weekly invoices'}, reason='Back on')
    assert _slots(run, 'Weekly invoices') == [('2026-04-01', 'skipped', 'outside_window'),
                                              ('2026-04-08', 'skipped', 'outside_window'),
                                              ('2026-04-15', 'skipped', 'outside_window')]
    assert weekly['next_date'] == '2026-04-22' and all(r['state'] == 'skipped' for r in weekly['ran'])
    # Inside the window: the host off for two days runs the latest slot only.
    clock.set('2026-04-23')
    run('delivery-schedule run', {'schedule': 'Daily invoices'}, reason='Back on')
    assert _slots(run, 'Daily invoices')[-3:] == [('2026-04-21', 'skipped', 'superseded'),
                                                  ('2026-04-22', 'skipped', 'superseded'),
                                                  ('2026-04-23', 'done', None)]
    # Stopped and resumed: slots that passed meanwhile are skipped, never run.
    run('delivery-schedule pause', {'schedule': 'Daily invoices'})
    clock.set('2026-04-26')
    assert 'stopped' in run('delivery-schedule run', {'schedule': 'Daily invoices'})['warnings'][0]
    resumed = run('delivery-schedule resume', {'schedule': 'Daily invoices'})
    assert resumed['next_date'] == '2026-04-26' and '2 slot(s)' in resumed['warnings'][0]
    assert _slots(run, 'Daily invoices')[-2:] == [('2026-04-24', 'skipped', 'stopped'),
                                                  ('2026-04-25', 'skipped', 'stopped')]


def test_a_restart_mid_run_goes_on_from_its_batch_and_duplicates_nothing(office, monkeypatch):
    run, clock, invoice = office['run'], office['clock'], office['invoice']
    from bookflow.company import delivery_batches
    monkeypatch.setattr(delivery_batches, 'CHUNK', 2)
    for n in range(5):
        invoice(f'R-{n}', '2026-05-02')
    run('delivery-schedule create', {'name': 'Unsent', 'kind': 'invoice', 'unsent': True, 'frequency': 'daily',
                                     'start_date': '2026-05-03'})
    clock.set('2026-05-03')
    first = run('delivery-schedule run', {'schedule': 'Unsent'}, reason='Tick')
    assert [(r['state'], r['drafted'], r['planned']) for r in first['ran']] == [('drafting', 2, 5)]
    # The process restarts: a fresh connection to the same company finds the open run and continues it.
    import os
    import bookflow
    client = bookflow.connect(data_root=os.environ['BOOKFLOW_DATA_ROOT'])
    fresh = lambda name, inp, **kw: client.run(name, inp, company=office['company'], **kw)  # noqa: E731
    seen = []
    while True:
        out = fresh('delivery-schedule run', {'schedule': 'Unsent'}, reason='Tick')
        if not out['ran']:
            break
        seen.append((out['ran'][-1]['state'], out['ran'][-1]['drafted']))
    assert seen == [('drafting', 4), ('done', 5)]
    numbers = [r['number'] for r in run('delivery query', {'status': 'all', 'limit': 50})['rows']]
    assert len(numbers) == len(set(numbers)) == 5
    shown = run('delivery-schedule show', {'schedule': 'Unsent'})
    assert [r['slot_date'] for r in shown['runs']] == ['2026-05-03']
    assert shown['next_date'] == '2026-05-04'


# ---------------------------------------------------------------- authority, on the host

def _host_books(hosted):  # noqa: F811
    from tests.test_document_print import commands as make_commands, seed
    books = seed(make_commands(hosted))
    books['run']('customer update', {'customer': books['customer'], 'email': 'accounts@harbourmills.example'})
    return books


def _clerk(hosted, name='clerk', role='standard'):  # noqa: F811
    from tests.test_people_only import REASON as PERSON
    hosted.ok('user.add', {'username': name, 'company': hosted.company_id, 'role': role,
                           'password': f'pw-{name}-12345'}, headers=PERSON)
    secret = hosted.ok('token.issue', {'user': name, 'label': name}, headers=PERSON)['secret']
    return {'Authorization': 'Bearer ' + secret, **PERSON}


@pytest.mark.timeout(900)
def test_an_agents_send_schedule_sends_nothing_until_a_person_makes_it_live(hosted, monkeypatch):  # noqa: F811
    from tests.delivery_fake_transport import SETTINGS, FakeBackend, registered
    from tests.test_people_only import REASON as PERSON, _agent
    cid, host = hosted.company_id, hosted.handle.host
    _host_books(hosted)
    person = {**hosted.bearer, **PERSON}
    _, as_agent = _agent(hosted)
    with registered(FakeBackend()) as backend:
        hosted.ok('delivery-connection.add', {'name': 'Office', 'kind': 'fake', 'settings': SETTINGS}, company=cid,
                  headers=person)
        hosted.ok('delivery-connection.enable', {'connection': 'Office'}, company=cid, headers=person)
        # The agent drafts a daily send schedule of every unsent invoice; it is a draft and cannot go live by it.
        made = hosted.ok('delivery-schedule.create', {'name': 'Daily invoices', 'kind': 'invoice', 'unsent': True,
                                                      'frequency': 'daily', 'start_date': '2026-06-01',
                                                      'mode': 'send'}, company=cid, headers=as_agent)
        assert made['state'] == 'draft' and made['mode'] == 'send' and 'go' in made['status_label']
        refused = hosted.call('delivery-schedule.live', {'schedule': 'Daily invoices'}, company=cid,
                              headers=as_agent)
        body = refused.json()
        assert refused.status_code == 403 and body['details']['reason'] == 'people_only'
        assert body['details']['needs'] == 'automate.delivery' and 'automate.delivery' in body['message']
        # The tick runs it as the agent: released, every message waits for a go; nothing leaves; one to-do.
        ticked = host.schedule_now(now=datetime(2026, 6, 1, 12, tzinfo=timezone.utc))[cid]
        ran = ticked['delivery_schedules']['Daily invoices']['ran']
        assert ran[-1]['state'] == 'done' and ran[-1]['released'] >= 1
        waiting = hosted.ok('delivery.query', {'status': 'waiting', 'limit': 50}, company=cid, headers=hosted.bearer)['rows']
        assert len(waiting) == ran[-1]['released'] and backend.delivered == []
        todos = hosted.ok('todo.list', {'status': 'open'}, company=cid, headers=hosted.bearer)['items']
        assert sum('wait for your go' in t['title'] for t in todos) == 1 and backend.delivered == []
        # A person: one check of the exact details, then live with its fingerprint (and only with it).
        check = hosted.ok('delivery-schedule.live?dry_run=true', {'schedule': 'Daily invoices'}, company=cid,
                          headers=person)['check']
        assert check['cadence'].startswith('Every day from 2026-06-01') and check['fingerprint']
        assert check['selection'].startswith('Every invoice with no live or sent message')
        bare = hosted.call('delivery-schedule.live', {'schedule': 'Daily invoices'}, company=cid, headers=person)
        assert bare.json()['code'] == 'E_VALIDATION' and bare.json()['details']['fields'][0]['field'] == 'confirm'
        wrong = hosted.call('delivery-schedule.live', {'schedule': 'Daily invoices', 'confirm': 'nope'}, company=cid,
                            headers=person)
        assert wrong.json()['code'] == 'E_WORK_DEPENDENCY'
        live = hosted.ok('delivery-schedule.live', {'schedule': 'Daily invoices', 'confirm': check['fingerprint']},
                         company=cid, headers=person)
        assert live['state'] == 'live' and live['status_label'].startswith('Live: sends under')
        # The agent's edit to what it sends returns it to draft.
        edited = hosted.ok('delivery-schedule.update', {'schedule': 'Daily invoices', 'combine': True}, company=cid,
                           headers=as_agent)
        assert edited['state'] == 'draft' and 'returns it to draft' in edited['warnings'][0]
        r = hosted.call('delivery-schedule.update', {'schedule': 'Daily invoices', 'confirm': 'x'}, company=cid,
                        headers=as_agent)
        assert r.status_code == 403 and r.json()['details']['needs'] == 'automate.delivery'
        check = hosted.ok('delivery-schedule.live?dry_run=true', {'schedule': 'Daily invoices'}, company=cid,
                          headers=person)['check']
        hosted.ok('delivery-schedule.live', {'schedule': 'Daily invoices', 'confirm': check['fingerprint']},
                  company=cid, headers=person)
        # A person's change with the changed schedule's check keeps it live, under them.
        changed = hosted.ok('delivery-schedule.update?dry_run=true', {'schedule': 'Daily invoices',
                                                                      'combine': False}, company=cid, headers=person)
        kept = hosted.ok('delivery-schedule.update', {'schedule': 'Daily invoices', 'combine': False,
                                                      'confirm': changed['check']['fingerprint']},
                         company=cid, headers=person)
        assert kept['state'] == 'live'
        # Live: the next slot's messages carry the person's go and leave on the same tick.
        hosted.ok('invoice.post', {'number': 'L-1', 'date': '2026-06-01', 'customer': 'Harbour Mills',
                                   'lines': [{'item': 'Site labour', 'quantity': '1'}]}, company=cid, headers=person)
        # The transports' own clock is the tick's (a claim's lease is read again just before its send).
        clock = Clock(monkeypatch, '2026-06-02')
        ticked = host.schedule_now(now=clock.at)[cid]
        assert ticked['delivery_schedules']['Daily invoices']['ran'][-1]['released'] == 1
        assert ticked['delivery']['claimed'] == 1 and ticked['delivery']['sent'] == 1
        sent = [r for r in hosted.ok('delivery.query', {'status': 'sent', 'limit': 50}, company=cid,
                                     headers=hosted.bearer)['rows']]
        assert len(sent) == 1 and len(backend.delivered) == 1
        assert len(waiting) == len(hosted.ok('delivery.query', {'status': 'waiting', 'limit': 50}, company=cid,
                                             headers=hosted.bearer)['rows'])


@pytest.mark.timeout(900)
def test_an_establisher_deactivated_or_without_authority_pauses_it_and_nothing_sends(hosted):  # noqa: F811
    from tests.test_people_only import REASON as PERSON
    cid, host = hosted.company_id, hosted.handle.host
    _host_books(hosted)
    person = {**hosted.bearer, **PERSON}
    for name in ('clerk', 'temp'):
        as_user = _clerk(hosted, name)
        hosted.ok('delivery-schedule.create', {'name': f'{name} invoices', 'kind': 'invoice', 'unsent': True,
                                               'frequency': 'daily', 'start_date': '2026-06-01', 'mode': 'send'},
                  company=cid, headers=as_user)
        check = hosted.ok('delivery-schedule.live?dry_run=true', {'schedule': f'{name} invoices'}, company=cid,
                          headers=as_user)['check']
        hosted.ok('delivery-schedule.live', {'schedule': f'{name} invoices', 'confirm': check['fingerprint']},
                  company=cid, headers=as_user)
    # The clerk is deactivated; the temp loses the right to send (denied customer).
    hosted.ok('user.deactivate', {'user': 'clerk'}, headers=PERSON)
    from bookflow.company import delivery_schedules as ds
    real = ds.authority_lost
    ds.authority_lost = lambda s, user: 'temp is denied customer in this company' if s.actor.username == 'temp' \
        or _username(s, user) == 'temp' else real(s, user)
    try:
        ticked = host.schedule_now(now=datetime(2026, 6, 1, 12, tzinfo=timezone.utc))[cid]['delivery_schedules']
    finally:
        ds.authority_lost = real
    assert 'clerk' in ticked['clerk invoices']['paused'] and 'deactivated' in ticked['clerk invoices']['paused']
    shown = hosted.ok('delivery-schedule.show', {'schedule': 'clerk invoices'}, company=cid, headers=hosted.bearer)
    assert shown['state'] == 'paused' and shown['runs'][0]['reason'] == 'no_authority'
    temp = hosted.ok('delivery-schedule.show', {'schedule': 'temp invoices'}, company=cid, headers=hosted.bearer)
    assert temp['state'] == 'paused' and 'denied' in temp['paused_reason']
    # The temp's run still drafted, but released nothing with a go: every message waits.
    assert temp['runs'][0]['state'] == 'done' and not temp['runs'][0]['go']
    rows = hosted.ok('delivery.query', {'status': 'all', 'limit': 50}, company=cid, headers=hosted.bearer)['rows']
    assert rows and all(r['status'] == 'ready' and r['waiting_for_go'] for r in rows)
    todos = [t['title'] for t in hosted.ok('todo.list', {'status': 'open'}, company=cid, headers=hosted.bearer)['items']]
    assert sum('is paused' in t for t in todos) == 2
    # The next tick pauses nothing again and raises no second to-do.
    host.schedule_now(now=datetime(2026, 6, 2, 12, tzinfo=timezone.utc))
    todos = [t['title'] for t in hosted.ok('todo.list', {'status': 'open'}, company=cid, headers=hosted.bearer)['items']]
    assert sum('is paused' in t for t in todos) == 2


def _username(s, user_id):
    import sqlalchemy as sa
    from bookflow.hub import schema as h
    return s.hub.conn.execute(sa.select(h.users.c.username).where(h.users.c.id == user_id)).scalar()


@pytest.mark.timeout(900)
def test_the_idle_tick_for_a_company_with_no_schedules_costs_nothing_measurable(hosted):  # noqa: F811
    """Measured: the scheduler tick with and without the schedules hook, and the hook's one statement."""
    from bookflow.company import delivery_schedules
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
    original = delivery_schedules.tick
    delivery_schedules.tick = lambda *a, **k: None
    try:
        without = ticks()
    finally:
        delivery_schedules.tick = original
    assert 'delivery_schedules' not in host.schedule_now()[cid]

    def statement():
        db = host._companies.get(cid) or next(iter(host._companies.values()))
        started = time.perf_counter()
        for _ in range(1000):
            assert not delivery_schedules.has_due(db.conn, '2026-06-01')
        return (time.perf_counter() - started) / 1000

    per_statement = host.submit(statement, _maintenance=True)
    print(f'idle tick median with the schedules hook {with_hook * 1000:.2f} ms, without {without * 1000:.2f} ms; '
          f'the hook statement {per_statement * 1e6:.1f} us')
    assert per_statement < 0.001
