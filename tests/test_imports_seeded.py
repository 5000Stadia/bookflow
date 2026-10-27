"""Seeded check of imports and reversible batches (R107 increments 0 and 1).

Each seed runs a random sequence over one company: imports of overlapping customer files from two sources (create and
upsert, some run in small slices), re-runs, edits of imported customers, invoices to them, and reversals of random
batches. A model kept beside the books predicts every outcome, and four invariants are checked as it goes:

- a re-run duplicates nothing (a row already made by its source is `already_in`; no two customers share a name);
- a reversal restores the exact prior state of every record it touched (an updated customer equals its snapshot from
  before the batch; a created one has left the lists);
- a reversal is refused, naming exactly the records that block it, when a later change depends on the batch (an edit
  since, an invoice), and then changes nothing;
- the books stay balanced, and receivables tie to the invoices posted, worked by hand in integers.
"""
import random

import pytest

from bookflow import BookflowError
from tests.test_imports import active_customers, books, run_all  # noqa: F401

SEEDS = (11, 23, 107)
STEPS = 28
POOL = [f'Seed Customer {i:02d}' for i in range(24)]
VOLATILE = {'version', 'updated_at', 'updated_by', 'updated_via'}
INVOICE_MINOR = 5000


def _file(rng, names):
    lines = ['Name,Phone,Notes']
    for name in names:
        lines.append(f"{name},{rng.randint(1000, 9999)},{rng.choice(['', 'Net payer', 'Call first'])}")
    return '\n'.join(lines) + '\n'


def _shown(books, record_id):
    shown = books.run('customer show', {'customer': record_id})
    return {k: v for k, v in shown.items() if k not in VOLATILE}


def _receivable(books):
    report = books.run('report trial-balance', {'date_to': '2026-12-31', 'limit': 200})
    debit, credit = report['totals']['debit']['minor_units'], report['totals']['credit']['minor_units']
    assert debit == credit  # the books balance
    receivable = {a['id'] for a in books.run('account query', {'limit': 200})['items']
                  if a['type'] == 'accounts_receivable'}
    return sum(row['debit']['minor_units'] - row['credit']['minor_units'] for row in report['rows']
               if row['account_id'] in receivable)


@pytest.mark.parametrize('seed', SEEDS)
def test_random_imports_edits_and_reversals_keep_the_invariants(books, seed):
    rng = random.Random(seed)
    income = books.run('account show', {'account': 'Service Income'})['id']
    labor = books.client.item.create(company=books.company, name='Labor', type='service', description='Labor',
                                     price='50.00', income_account_id=income)['id']
    batches = {}  # batch id -> {'run', 'created': set, 'updated': {id: snapshot}, 'blocked': set, 'live': bool}
    files = []  # (source, text, names) already run
    standing = set()  # (source, name) whose import key stands
    invoices = 0
    totals = {'imports': 0, 'reruns': 0, 'edits': 0, 'invoices': 0, 'reversed': 0, 'refused': 0}

    def touched(record_id, *, use=False):
        """A later change to a record: every live batch that wrote it now depends on it."""
        for b in batches.values():
            if b['live'] and (record_id in b['created'] or record_id in b['updated']):
                if use and record_id not in b['created']:
                    continue
                b['blocked'].add(record_id)

    for step in range(STEPS):
        choice = rng.random()
        live = [bid for bid, b in batches.items() if b['live']]
        if choice < 0.35 or not batches:
            source = rng.choice(['north', 'south'])
            names = rng.sample(POOL, rng.randint(2, 6))
            mode = rng.choice(['create', 'upsert'])
            text = _file(rng, names)
            ids_before = {n: books.run('customer show', {'customer': n})['id'] for n in names
                          if n in active_customers(books)}
            snapshots = {i: _shown(books, i) for i in ids_before.values()}
            preview = books.run('import preview', {'kind': 'customer', 'content': text, 'source': source,
                                                   'mode': mode})
            run_id = preview['run']['id']
            while True:
                out = books.run('import run', {'run': run_id, 'limit': rng.choice([1, 2, 50])})
                if out['run']['state'] != 'running':
                    break
            assert out['run']['state'] == 'done', out
            batch = out['run']['batch']['id']
            effects = books.run('batch show', {'batch': batch})['batch']['effects']
            created = {e['record_id'] for e in effects if e['action'] == 'created'}
            updated = {e['record_id']: snapshots[e['record_id']] for e in effects if e['action'] == 'updated'}
            for record_id in updated:
                touched(record_id)
            assert not created & set(ids_before.values())  # an existing name is never made twice
            assert len(created) + len(updated) + out['counts'].get('unchanged', 0) + out['counts'].get(
                'differs', 0) + out['counts'].get('already_in', 0) == len(names), out['counts']
            made = {books.run('customer show', {'customer': r})['full_name'] for r in created}
            standing.update((source, name) for name in made)
            batches[batch] = {'run': run_id, 'created': created, 'updated': updated, 'blocked': set(), 'live': True,
                              'source': source, 'made': made}
            files.append((source, text, names))
            totals['imports'] += 1
        elif choice < 0.45:
            source, text, names = rng.choice(files)
            preview = books.run('import preview', {'kind': 'customer', 'content': text, 'source': source})
            active = active_customers(books)
            # Nothing is made twice: only a name the lists lack now (its batch reversed) is made again.
            assert preview['counts'].get('create', 0) == len([n for n in names if n not in active]), preview['counts']
            assert preview['counts'].get('already_in', 0) == len([n for n in names if (source, n) in standing])
            totals['reruns'] += 1
        elif choice < 0.6:
            pool = [r for b in batches.values() if b['live'] for r in (b['created'] | set(b['updated']))]
            active = active_customers(books)
            pool = [r for r in pool if books.run('customer show', {'customer': r})['full_name'] in active]
            if not pool:
                continue
            record_id = rng.choice(pool)
            books.run('customer update', {'customer': record_id, 'notes': f'edited at step {step}'})
            touched(record_id)
            totals['edits'] += 1
        elif choice < 0.7:
            pool = [r for b in batches.values() if b['live'] for r in b['created']]
            active = active_customers(books)
            pool = [r for r in pool if books.run('customer show', {'customer': r})['full_name'] in active]
            if not pool:
                continue
            record_id = rng.choice(pool)
            books.run('invoice post', {'customer': record_id, 'date': '2026-02-01',
                                       'lines': [{'item': labor, 'quantity': '1'}]})
            touched(record_id, use=True)
            invoices += 1
            totals['invoices'] += 1
        elif live:
            batch = rng.choice(live)
            model = batches[batch]
            names_before = active_customers(books)
            if model['blocked']:
                with pytest.raises(BookflowError) as caught:
                    books.run('import reverse', {'run': model['run']})
                assert caught.value.code == 'E_BATCH_BLOCKED'
                named = {b['record_id'] for b in caught.value.details['blockers']}
                assert named == model['blocked'], (named, model['blocked'])
                assert active_customers(books) == names_before  # refused: nothing changed
                totals['refused'] += 1
                continue
            out = books.run('import reverse', {'run': model['run'], 'limit': rng.choice([1, 3, 200])})
            while out['state'] == 'reversing':
                out = books.run('import reverse', {'run': model['run']})
            assert out['state'] == 'reversed'
            for record_id, snapshot in model['updated'].items():
                assert _shown(books, record_id) == snapshot  # exactly as before the batch
                touched(record_id)
            for record_id in model['created']:
                shown = books.run('customer show', {'customer': record_id})
                assert shown['active'] is False and shown['full_name'] not in active_customers(books)
                touched(record_id)
            model['live'] = False
            standing.difference_update((model['source'], name) for name in model['made'])
            totals['reversed'] += 1
        names = [books.run('customer show', {'customer': n})['id'] for n in active_customers(books)]
        assert len(names) == len(set(names))
    assert _receivable(books) == invoices * INVOICE_MINOR
    assert totals['imports'] and (totals['reversed'] or totals['refused']), totals
    print(f'seed {seed}: {totals}')
