"""R112 increment 1: a seeded check of messages and the outbox.

Four invoices to two customers take random drafts (some with --again), edits, releases, exports, cancels, hand-sent
marks, and edits and voids of the invoices themselves, in random order. After every step: at most one live message per
invoice besides those made with --again (and one live --again per earlier message); every live message's stale flag
says exactly whether its invoice's current revision differs from the drafted one or the invoice is voided; every
message's history is complete -- one event per act, in order, the last naming the message's status -- and every
attached file's hash is the frozen bytes' own. No act is refused by a guard trigger or an internal error.

Increment 2 adds the queue: fresh invoices released with a go, dispatch passes whose fake transport answers at random
(accepted, temporary, permanent, unknown, or a raise), crashes between the send and the record, the clock moved past
backoffs and leases, and a person's retry, mark-sent or cancel. After every step: no message reached a recipient twice,
every attempt is a claim with exactly one answer (unless still in flight), and nothing follows an unknown outcome
without a person's retry.

Five seeds of 30 steps in an ordinary run; BF_DELIVERY_SEEDS, BF_DELIVERY_STEPS and BF_DELIVERY_QUEUE_STEPS widen it.
"""
import base64
import hashlib
import os
import random

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books  # noqa: F401

SEEDS = tuple(range(1120, 1120 + int(os.environ.get('BF_DELIVERY_SEEDS', '5'))))
STEPS = int(os.environ.get('BF_DELIVERY_STEPS', '30'))
LIVE = {'draft', 'ready', 'sending', 'held', 'failed'}
EXPECTED_REFUSALS = {'E_WORK_DEPENDENCY', 'E_VALIDATION', 'E_REASON_REQUIRED'}


@pytest.fixture
def desk(books):  # noqa: F811
    client, company, run = books['client'], books['company'], books['run']
    visit = client.item.create(company=company, name='Site visit', type='service', income_account_id=books['income'],
                               price='50.00')['id']
    other = client.customer.create(company=company, name='Baker Homes', email='ap@baker.example')['id']
    run('customer update', {'customer': books['customer'], 'email': 'pay@adams.example'})
    invoices = []
    for n, customer in enumerate((books['customer'], other, books['customer'], other)):
        invoices.append(run('invoice post', {'number': f'S-{n + 1}', 'date': '2026-03-0' + str(n + 1),
                                             'customer': customer, 'lines': [{'item': visit, 'quantity': str(n + 1)}]},
                            reason='Seed')['number'])
    return dict(books, invoices=invoices)


def _check(run, model, seed, step):
    messages = {}
    for row in run('delivery query', {'status': 'all', 'limit': 500})['rows']:
        messages[row['number']] = run('delivery show', {'message': row['number'], 'include_files': True})
    assert set(messages) == set(model), (seed, step)
    plain, again = {}, {}
    for number, message in messages.items():
        [doc] = message['documents']
        if doc['content_base64'] is None:
            # Pruned: only a cancelled message never exported gives up its file, and only when no standing
            # message carries the same bytes.
            assert message['status'] == 'cancelled' and 'exported' not in model[number], (seed, step, number)
        else:
            assert hashlib.sha256(base64.b64decode(doc['content_base64'])).hexdigest() == doc['sha256']
        if message['status'] in LIVE:
            key = (doc['source_id'], message['resend_of'])
            bucket = again if message['resend_of'] else plain
            bucket[key] = bucket.get(key, 0) + 1
            invoice = run('invoice show', {'invoice': doc['source_id']})
            stale = invoice['status'] == 'voided' or invoice['current_revision_id'] != doc['source_revision']
            assert message['stale'] == stale, (seed, step, number)
        events = run('delivery history', {'message': number})['events']
        assert [e['event'] for e in events] == model[number], (seed, step, number)
        assert [e['ordinal'] for e in events] == list(range(1, len(events) + 1))
        assert events[-1]['status'] == message['status'], (seed, step, number)
    assert all(v <= 1 for v in plain.values()), (seed, step, plain)
    assert all(v <= 1 for v in again.values()), (seed, step, again)


@pytest.mark.timeout(1800)
@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_outbox_keeps_one_live_message_true_stale_flags_and_whole_histories(desk, seed):
    rng, run, model = random.Random(seed), desk['run'], {}
    tally = {}

    def act(name):
        tally[name] = tally.get(name, 0) + 1

    for step in range(STEPS):
        choice = rng.choice(('draft', 'draft', 'again', 'update', 'send', 'export', 'cancel', 'mark', 'edit', 'void'))
        numbers = sorted(model)
        try:
            if choice in ('draft', 'again'):
                out = run('delivery draft', {'kind': 'invoice', 'document': rng.choice(desk['invoices']),
                                             'again': choice == 'again'}, reason='Email it')
                if not out['existing']:
                    model[out['number']] = ['drafted']
                act(choice)
            elif choice == 'edit':
                invoice = run('invoice show', {'invoice': rng.choice(desk['invoices'])})
                if invoice['status'] != 'voided':
                    run('invoice update', {'invoice': invoice['id'], 'expected_version': invoice['version'],
                                           'memo': f'memo {step}'}, reason='Correction')
                    act(choice)
            elif choice == 'void':
                invoice = run('invoice show', {'invoice': rng.choice(desk['invoices'])})
                if invoice['status'] != 'voided' and rng.random() < 0.4:
                    run('invoice void', {'invoice': invoice['id'], 'expected_version': invoice['version']},
                        reason='Wrong customer')
                    act(choice)
            elif numbers:
                number = rng.choice(numbers)
                before = run('delivery show', {'message': number})
                if choice == 'update':
                    run('delivery update', {'message': number, 'subject': f'Subject {step}'}, reason='Edit')
                    model[number].append('edited')
                elif choice == 'send':
                    out = run('delivery send', {'messages': [number]}, reason='Send')
                    model[number].append('released' if out['messages'][0]['status'] == 'ready' else 'held')
                elif choice == 'export':
                    out = run('delivery export', {'messages': [number]}, reason='By hand')
                    if before['status'] in LIVE and out['held']:
                        # A message already held for the same reason holds nothing more.
                        if not (before['status'] == 'held' and before['hold_reason'] == (
                                'document_voided' if before['stale_reason'] == 'document_voided' else
                                'document_changed')):
                            model[number].append('held')
                    else:
                        model[number].append('exported')
                elif choice == 'cancel':
                    run('delivery cancel', {'message': number}, reason='Not needed')
                    model[number].append('cancelled')
                elif choice == 'mark':
                    run('delivery mark-sent', {'message': number}, reason='Sent from Outlook')
                    model[number].append('marked_sent')
                act(choice)
        except BookflowError as e:
            assert e.code in EXPECTED_REFUSALS, (seed, step, choice, e.code, e.message)
            act('refused ' + e.code)
        _check(run, model, seed, step)
    print('seed', seed, 'messages', len(model), dict(sorted(tally.items())))


# ---------------------------------------------------------------- increment 2: the queue, against the fake transport

QUEUE_STEPS = int(os.environ.get('BF_DELIVERY_QUEUE_STEPS', '30'))


def _queue_check(run, backend, seed, step):
    rows = run('delivery query', {'status': 'all', 'limit': 500})['rows']
    for row in rows:
        number = row['number']
        message = run('delivery show', {'message': number})
        events = run('delivery history', {'message': number})['events']
        names = [e['event'] for e in events]
        # No message is sent twice.
        assert backend.deliveries_of(number) <= 1, (seed, step, number, names)
        # Every attempt is recorded: one claim per attempt and per send that reached the server.
        claims = [e for e in events if e['event'] == 'claimed']
        assert len(claims) == message['attempts'], (seed, step, number, names)
        reached = {k for k, m in backend.received.items() if m.number == number}
        assert reached <= {e['details']['attempt_key'] for e in claims}, (seed, step, number)
        # ... and each claim has exactly one answer, unless it is still in flight.
        answers = 0
        for e in events:
            if e['event'] in ('accepted', 'attempt_failed') and not e['details'].get('late') or (
                    e['event'] == 'held' and e['details'].get('reason') == 'outcome_unknown'):
                answers += 1
        assert answers == len(claims) - (message['status'] == 'sending'), (seed, step, number, names)
        # An unknown outcome waits for a person: no claim follows it without a retry in between.
        waiting = False
        for e in events:
            if e['event'] == 'held' and e['details'].get('reason') == 'outcome_unknown':
                waiting = True
            elif e['event'] == 'retried':
                waiting = False
            elif e['event'] == 'claimed':
                assert not waiting, (seed, step, number, names)
        assert [e['ordinal'] for e in events] == list(range(1, len(events) + 1))
        assert events[-1]['status'] == message['status'] or names[-1] in ('exported',), (seed, step, number)


@pytest.mark.timeout(1800)
@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_queue_never_sends_twice_records_every_attempt_and_holds_unknowns(books, monkeypatch, seed):  # noqa: F811
    from bookflow.company import delivery_dispatch
    from tests.delivery_fake_transport import FakeBackend, registered
    from tests.test_delivery_queue import _desk, connect
    rng = random.Random(seed)
    tally = {}
    real_send = delivery_dispatch.send

    def crash(claims, polls, **hooks):
        real_send(claims, polls, **hooks)
        raise RuntimeError('host killed between the send and the record')

    with registered(FakeBackend()) as backend:
        desk = _desk(books, monkeypatch, backend)
        run = desk['run']
        connect(run)
        for step in range(QUEUE_STEPS):
            choice = rng.choice(('ready', 'ready', 'run', 'run', 'run', 'crash', 'wait', 'person', 'cancel'))
            tally[choice] = tally.get(choice, 0) + 1
            if choice == 'ready':
                desk['ready']()
            elif choice in ('run', 'crash'):
                backend.plan(*rng.choices(('accepted', 'temporary', 'permanent', 'unknown', 'raise'),
                                          weights=(4, 3, 1, 1, 1), k=10))
                if choice == 'crash':
                    monkeypatch.setattr(delivery_dispatch, 'send', crash)
                try:
                    out = run('delivery run', {}, reason='Send what is due')
                    for key in ('sent', 'retrying', 'failed', 'held', 'expired'):
                        tally['run ' + key] = tally.get('run ' + key, 0) + out[key]
                except Exception as error:  # noqa: BLE001 - only the injected crash
                    assert choice == 'crash' and 'host killed' in str(error), error
                finally:
                    monkeypatch.setattr(delivery_dispatch, 'send', real_send)
                backend.script.clear()
            elif choice == 'wait':
                desk['clock'].advance(rng.choice((1, 6, 11, 31, 121)))
            else:
                rows = run('delivery query', {'status': 'attention', 'limit': 500})['rows']
                if rows:
                    row = rng.choice(rows)
                    message = run('delivery show', {'message': row['number']})
                    if choice == 'cancel':
                        run('delivery cancel', {'message': row['number']}, reason='Not needed')
                    elif message['hold_reason'] == 'outcome_unknown' or backend.deliveries_of(row['number']):
                        # The person finds it in the sent mail.
                        run('delivery mark-sent', {'message': row['number']}, reason='It went')
                        tally['marked sent'] = tally.get('marked sent', 0) + 1
                    else:
                        run('delivery retry', {'message': row['number']}, reason='Try again')
                        tally['retried'] = tally.get('retried', 0) + 1
            _queue_check(run, backend, seed, step)
    print('queue seed', seed, 'deliveries', len(backend.delivered), dict(sorted(tally.items())))


# ---------------------------------------------------------------- increment 3: flags, the auto-flag date and batches

BATCH_STEPS = int(os.environ.get('BF_DELIVERY_BATCH_STEPS', '30'))
SKIP_REASONS = {'no_address', 'prefers_mail', 'not_email', 'opted_out', 'in_outbox', 'sent', 'excluded', 'refused'}


def _expected_queue(run, desk, flags, auto_from, methods):
    """The queue worked by hand from what the steps did: flags, the date, each customer's method, the messages."""
    out = set()
    for number in desk['invoices']:
        invoice = run('invoice show', {'invoice': number})
        if invoice['status'] == 'voided':
            continue
        rows = run('delivery query', {'status': 'all', 'source': invoice['id']})['rows']
        if any(r['status'] != 'cancelled' for r in rows):
            continue
        flag = flags.get(number)
        if flag is True or (flag is None and auto_from and invoice['date'] >= auto_from
                            and methods[invoice['customer_id']] == 'email'):
            out.add(number)
    return out


@pytest.mark.timeout(1800)
@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_batches_duplicate_nothing_name_every_skip_and_cancel_whole(desk, seed, monkeypatch):
    from bookflow.company import delivery_batches
    monkeypatch.setattr(delivery_batches, 'CHUNK', 2)
    rng, run, tally = random.Random(seed), desk['run'], {}
    customers = sorted({run('invoice show', {'invoice': n})['customer_id'] for n in desk['invoices']})
    methods = {cid: 'none' for cid in customers}
    flags, auto_from, batches = {}, None, []

    def act(name):
        tally[name] = tally.get(name, 0) + 1

    for step in range(BATCH_STEPS):
        choice = rng.choice(('flag', 'unflag', 'date', 'method', 'batch', 'batch', 'more', 'cancel', 'draft',
                             'release', 'export'))
        try:
            if choice in ('flag', 'unflag'):
                number = rng.choice(desk['invoices'])
                run('delivery ' + choice, {'kind': 'invoice', 'document': number}, reason='Seed')
                flags[number] = choice == 'flag'
            elif choice == 'date':
                auto_from = rng.choice((None, '2026-03-02', '2026-03-04'))
                run('company update', {'delivery_auto_flag_from': auto_from})
            elif choice == 'method':
                cid = rng.choice(customers)
                methods[cid] = rng.choice(('email', 'mail', 'none'))
                run('customer update', {'customer': cid, 'preferred_delivery_method': methods[cid]})
            elif choice == 'batch':
                raw = rng.choice(({'queued': True}, {'kind': 'invoice', 'unsent': True},
                                  {'kind': 'statement', 'period': '2026-03'}))
                if 'statement' not in raw.values() and rng.random() < 0.3:
                    raw['combine'] = True
                raw['send'] = rng.random() < 0.3
                preview = run('delivery batch', raw, dry_run=True)
                out = run('delivery batch', raw, reason='Batch')
                assert out['planned'] == preview['planned'], (seed, step)
                while out['next']:
                    out = run('delivery batch', out['next'], reason='Go on')
                if out['batch_id']:
                    batches.append(out['number'])
                # A rerun of the same selection drafts nothing new.
                again = run('delivery batch', dict(raw, send=False), dry_run=True)
                assert again['planned'] == 0, (seed, step, raw, again['would_draft'])
                act('batch ' + ('statement' if 'period' in raw else 'queued' if raw.get('queued') else 'unsent'))
            elif choice == 'more' and batches:
                out = run('delivery batch', {'batch': rng.choice(batches)}, reason='Go on')
                assert out['messages'] == [], (seed, step)  # every batch here was run to its end
            elif choice == 'cancel' and batches:
                number = rng.choice(batches)
                out = run('delivery batch-cancel', {'batch': number}, reason='Undo the batch')
                assert out['state'] == 'cancelled'
                act('batch cancel')
            elif choice == 'draft':
                run('delivery draft', {'kind': 'invoice', 'document': rng.choice(desk['invoices'])}, reason='One')
            elif choice in ('release', 'export'):
                rows = run('delivery query', {'status': 'draft', 'limit': 500})['rows']
                if rows:
                    number = rng.choice(rows)['number']
                    run('delivery send' if choice == 'release' else 'delivery export', {'messages': [number]},
                        reason='Seed')
            act(choice)
        except BookflowError as e:
            assert e.code in EXPECTED_REFUSALS, (seed, step, choice, e.code, e.message)
            act('refused ' + e.code)
        # After every step: the queue is what the hand-worked rule says; at most one live message per document;
        # every skip is named; a cancelled batch leaves no drafted message standing.
        queue = {r['number'] for r in run('delivery queue', {})['rows']}
        assert queue == _expected_queue(run, desk, flags, auto_from, methods), (seed, step, choice)
        live = {}
        for row in run('delivery query', {'status': 'live', 'limit': 500})['rows']:
            for source in row['source_ids']:
                live[source] = live.get(source, 0) + 1
        assert all(v == 1 for v in live.values()), (seed, step, live)
        for number in batches:
            shown = run('delivery batch-show', {'batch': number})
            assert all(s['reason'] in SKIP_REASONS and s['reason_text'] for s in shown['skipped']), (seed, step)
            if shown['state'] == 'cancelled':
                # Only a draft exported before the cancel stands (it may have been sent by hand), and it was named.
                for row in run('delivery query', {'batch': number, 'status': 'draft', 'limit': 500})['rows']:
                    events = [e['event'] for e in run('delivery history', {'message': row['number']})['events']]
                    assert 'exported' in events, (seed, step, number, row['number'])
    print('batch seed', seed, 'batches', len(batches), dict(sorted(tally.items())))


# ---------------------------------------------------------------- increment 4: schedules on a test clock

SCHEDULE_STEPS = int(os.environ.get('BF_DELIVERY_SCHEDULE_STEPS', '30'))


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_schedules_run_once_per_slot_never_catch_up_past_the_window_and_send_nothing_without_authority(
        desk, seed, monkeypatch):
    """Two schedules (daily unsent invoices in send mode, made live; month-end statements as drafts) on a clock moved
    0 to 12 days a step, with runs, stops and resumes, a live schedule made live again, and its establisher losing
    and regaining the authority. After every step: one run per slot; every run began no more than its window after
    its slot; a run that began while the establisher lacked the authority carries no go, and neither do its
    messages; nothing ran while stopped."""
    from datetime import date, datetime, timedelta, timezone
    from bookflow.company import delivery_batches, delivery_schedules as ds
    from bookflow.core import clock as bf_clock
    monkeypatch.setattr(delivery_batches, 'CHUNK', 2)
    rng, run, tally = random.Random(seed), desk['run'], {}
    today = [date(2026, 3, 1)]
    monkeypatch.setattr(bf_clock, 'now', lambda: datetime.combine(today[0], datetime.min.time(),
                                                                  timezone.utc).replace(hour=12))
    lost = [False]
    real = ds.authority_lost
    monkeypatch.setattr(ds, 'authority_lost', lambda s, user: 'the establisher lost the authority' if lost[0]
                        else real(s, user))
    run('customer update', {'customer': desk['customer'], 'preferred_delivery_method': 'email'})
    run('delivery-schedule create', {'name': 'Daily', 'kind': 'invoice', 'unsent': True, 'frequency': 'daily',
                                     'start_date': '2026-03-01', 'mode': 'send', 'catch_up_days': 3})
    run('delivery-schedule create', {'name': 'Month end', 'kind': 'statement', 'period': 'previous-month',
                                     'frequency': 'monthly', 'start_date': '2026-03-31'})

    def make_live():
        check = run('delivery-schedule live', {'schedule': 'Daily'}, dry_run=True)['check']
        run('delivery-schedule live', {'schedule': 'Daily', 'confirm': check['fingerprint']}, reason='Turn on')

    make_live()
    began_without = set()
    stopped = {'Daily': False, 'Month end': False}
    counter = [0]
    for step in range(SCHEDULE_STEPS):
        choice = rng.choice(('advance', 'advance', 'run', 'run', 'run', 'lose', 'stop', 'invoice', 'invoice', 'live'))
        try:
            if choice == 'advance':
                today[0] += timedelta(days=rng.choice((0, 1, 1, 2, 3, 5, 12)))
            elif choice == 'run':
                for name in ('Daily', 'Month end'):
                    shown = run('delivery-schedule show', {'schedule': name, 'runs': 400})
                    before = {r['run_id'] for r in shown['runs']}
                    out = run('delivery-schedule run', {'schedule': name}, reason='Tick')
                    started = [r for r in out['ran'] if r['run_id'] not in before and r['state'] != 'skipped']
                    assert not (started and stopped[name]), (seed, step)
                    if name == 'Daily' and (lost[0] or out['state'] == 'paused'):
                        began_without |= {r['run_id'] for r in started}
            elif choice == 'lose':
                lost[0] = not lost[0]
            elif choice == 'stop':
                name = rng.choice(('Daily', 'Month end'))
                run('delivery-schedule ' + ('resume' if stopped[name] else 'pause'), {'schedule': name})
                stopped[name] = not stopped[name]
            elif choice == 'invoice':
                counter[0] += 1
                run('invoice post', {'number': f'SCH-{seed}-{counter[0]}', 'date': today[0].isoformat(),
                                     'customer': desk['customer'], 'lines': [{'item': 'Site visit', 'quantity': '1'}]},
                    reason='Seed')
            elif choice == 'live' and not lost[0]:
                make_live()
            tally[choice] = tally.get(choice, 0) + 1
        except BookflowError as e:
            assert e.code in EXPECTED_REFUSALS | {'E_RECORD_NOT_FOUND'}, (seed, step, choice, e.code, e.message)
            tally['refused ' + e.code] = tally.get('refused ' + e.code, 0) + 1
        for name in ('Daily', 'Month end'):
            shown = run('delivery-schedule show', {'schedule': name, 'runs': 400})
            slots = [r['slot_date'] for r in shown['runs']]
            assert len(slots) == len(set(slots)), (seed, step, name)  # one run per slot
            for r in shown['runs']:
                if r['state'] in ('done', 'drafting'):  # no catch-up beyond the window
                    began = date.fromisoformat(r['started_at'][:10])
                    assert (began - date.fromisoformat(r['slot_date'])).days <= shown['catch_up_days'], (seed, step, r)
                if r['run_id'] in began_without:
                    assert not r['go'], (seed, step, r)
                    if r['batch']:
                        msgs = run('delivery query', {'batch': r['batch'], 'status': 'all', 'limit': 500})['rows']
                        assert all(not m['status'] in ('sent', 'sending') and (m['status'] != 'ready'
                                   or m['waiting_for_go']) for m in msgs), (seed, step, r)
    runs = run('delivery-schedule show', {'schedule': 'Daily', 'runs': 400})['runs']
    tally['runs done'] = sum(r['state'] == 'done' for r in runs)
    tally['slots skipped'] = sum(r['state'] == 'skipped' for r in runs)
    tally['runs without authority'] = len(began_without)
    print('schedule seed', seed, dict(sorted(tally.items())))
