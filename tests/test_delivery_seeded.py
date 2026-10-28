"""R112 increment 1: a seeded check of messages and the outbox.

Four invoices to two customers take random drafts (some with --again), edits, releases, exports, cancels, hand-sent
marks, and edits and voids of the invoices themselves, in random order. After every step: at most one live message per
invoice besides those made with --again (and one live --again per earlier message); every live message's stale flag
says exactly whether its invoice's current revision differs from the drafted one or the invoice is voided; every
message's history is complete -- one event per act, in order, the last naming the message's status -- and every
attached file's hash is the frozen bytes' own. No act is refused by a guard trigger or an internal error.

Five seeds of 30 steps in an ordinary run; BF_DELIVERY_SEEDS and BF_DELIVERY_STEPS widen it.
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
