"""A deposit of every check from a busy month, past the old 200-row limit (R153).

One module world takes real customer payments into Undeposited Funds (250 by default; set
BOOKFLOW_R153_DEPOSIT_ROWS=1000 for the full-size run the timings in notes come from), then:
lists them page by page, banks them all in one deposit, reads it back (totals on `deposit show`,
rows on `deposit items` 200 a page), voids it (every receipt returns to Undeposited Funds),
banks them again and clears that deposit on a bank reconciliation. Money is checked exactly at
each step. Each step's wall time is printed (run with -s to see them).
"""
import json
import os
import time

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_deposit_sources import uf
from tests.test_payment_receipts import method
from tests.test_service_sales_lifecycle import COMPANY

COUNT = int(os.environ.get('BOOKFLOW_R153_DEPOSIT_ROWS', '250'))
TIMES = {}


def _timed(name, fn):
    start = time.perf_counter()
    value = fn()
    TIMES[name] = round(time.perf_counter() - start, 2)
    print(f'R153 timing [{COUNT} rows] {name}: {TIMES[name]}s', flush=True)
    return value


def _run(client, name, body, **ctx):
    return client.run(name, body, company=COMPANY, **ctx)


@pytest.fixture(scope='module')
def world(_seeded_template, tmp_path_factory):
    import bookflow
    from tests.conftest import copy_seeded_root
    root = copy_seeded_root(_seeded_template, tmp_path_factory.mktemp('r153-deposit') / 'root')
    client = bookflow.connect(data_root=str(root))
    customer = client.customer.create(name='Busy month customer', company=COMPANY)['id']
    pay, account = method(client), uf(client)
    cents = {}

    def receive():
        for n in range(COUNT):
            # Amounts vary by the cent so totals are exact sums, not a count times a price.
            amount = f'{10 + n % 97}.{n % 100:02d}'
            result = _run(client, 'payment receive', dict(
                customer=customer, deposit_to=account, payment_method=pay, date='2026-08-03',
                number=f'CHK{n:05d}', amount=amount, operation_key=f'r153-{n}',
                applications=dict(mode='inline', items=[])))
            cents[result['id']] = int(amount.replace('.', ''))
    _timed(f'receive {COUNT} payments', receive)
    bank = client.account.create(name='Busy month bank', type='bank', company=COMPANY)['id']
    return dict(client=client, cents=cents, bank=bank)


def _sources(client):
    rows, cursor = [], None
    while True:
        page = _run(client, 'deposit sources', dict(date='2026-08-31', date_from='2026-08-03', date_to='2026-08-03',
                                                    limit=200, **({'cursor': cursor} if cursor else {})))
        rows += [{k: v[k] for k in ('source', 'source_type', 'expected_version')} for v in page['items']]
        cursor = page.get('next_cursor')
        if not cursor:
            return rows


def _post(world, rows, number):
    return _run(world['client'], 'deposit post', dict(operation_key=new_id(), document=dict(
        mode='inline', deposit_to=world['bank'], date='2026-08-31', number=number, sources=rows)))['deposit']


def _items(client, deposit):
    seen, sizes, cursor = [], [], None
    while True:
        page = _run(client, 'deposit items', dict(deposit=deposit, kind='sources',
                    page=dict(limit=200, **({'cursor': cursor} if cursor else {}))))
        seen += page['items']
        sizes.append(len(page['items']))
        cursor = page.get('next_cursor')
        if not cursor:
            return seen, sizes


def test_a_month_of_checks_posts_reads_voids_reposts_and_clears(world):
    client, cents = world['client'], world['cents']
    total = sum(cents.values())
    rows = _timed('list sources (all pages)', lambda: _sources(client))
    assert {r['source'] for r in rows} == set(cents) and len(rows) == COUNT

    first = _timed(f'post {COUNT}-row deposit', lambda: _post(world, rows, 'BIG-1'))
    assert first['bank_total']['minor_units'] == total and len(first['banked_receipt_ids']) == COUNT
    assert _run(client, 'deposit sources', dict(date='2026-08-31', date_from='2026-08-03', date_to='2026-08-03'))['total_count'] == 0

    shown = _timed('show', lambda: _run(client, 'deposit show', dict(deposit=first['id'])))
    assert COUNT in shown['counts'].values()
    assert shown['totals']['bank_total']['minor_units'] == total
    seen, sizes = _timed('items, every page', lambda: _items(client, first['id']))
    assert sizes == [200] * (COUNT // 200) + ([COUNT % 200] if COUNT % 200 else [])
    assert len({json.dumps(row, sort_keys=True) for row in seen}) == COUNT     # every receipt once

    void = dict(deposit=first['id'], expected_version=first['version'], operation_key=new_id())
    preview = _timed('void preview', lambda: _run(client, 'deposit void', void, dry_run=True, reason='Banked twice'))
    _timed(f'void {COUNT}-row deposit', lambda: _run(client, 'deposit void', dict(
        void, dependency_guard=preview['dependency_guard']), reason='Banked twice'))
    again = _sources(client)
    assert {r['source'] for r in again} == set(cents)        # every receipt is back in Undeposited Funds

    second = _timed(f'post {COUNT}-row deposit again', lambda: _post(world, again, 'BIG-2'))
    assert second['bank_total']['minor_units'] == total

    def clear():
        opening = _run(client, 'reconcile opening start', dict(
            operation_key=new_id(), account=world['bank'], opening_date='2026-08-01', entered_balance='0.00',
            evidence=dict(format=1, statement_reference=None, entered_text='New account'), references=[]),
            reason='Open the busy bank')['draft']
        statement = _run(client, 'reconcile start', dict(
            operation_key=new_id(), account=world['bank'], statement_date='2026-08-31',
            ending_balance=f'{total // 100}.{total % 100:02d}', opening_draft_id=opening['id']),
            reason='August statement')['draft']
        ticked = _run(client, 'reconcile mark', dict(operation_key=new_id(), draft=statement['id'],
                      expected_version=statement['version'], all=True), reason='Mark all')['draft']
        page = _run(client, 'reconcile preview', dict(draft=statement['id'], expected_version=ticked['version']))
        assert page['balanced'] and page['totals']['difference'] == 0
        return _run(client, 'reconcile finish', dict(
            operation_key=new_id(), draft=statement['id'], expected_version=ticked['version'],
            expected_facts_fingerprint=page['expected_facts_fingerprint'], dependency_guard=page['dependency_guard']),
            reason='August reconciled')
    done = _timed('reconcile-clear the deposit', clear)
    assert done['certificate_id'] and done['totals']['difference'] == 0

    # As in the anchor, a cleared deposit can still be voided; its receipts return to Undeposited Funds.
    void = dict(deposit=second['id'], expected_version=second['version'], operation_key=new_id())
    preview = _run(client, 'deposit void', void, dry_run=True, reason='Void the cleared deposit')
    _timed(f'void the cleared {COUNT}-row deposit', lambda: _run(client, 'deposit void', dict(
        void, dependency_guard=preview['dependency_guard']), reason='Void the cleared deposit'))
    assert {r['source'] for r in _sources(client)} == set(cents)
    print('R153 timings', TIMES, flush=True)


def test_more_than_two_thousand_rows_is_refused_in_plain_words(world):
    client = world['client']
    rows = [dict(source=new_id(), source_type='payment', expected_version=1) for _ in range(2001)]
    with pytest.raises(BookflowError) as refused:
        _run(client, 'deposit post', dict(operation_key=new_id(), document=dict(
            mode='inline', deposit_to=world['bank'], date='2026-08-31', sources=rows)))
    assert refused.value.code == 'E_VALIDATION'
    assert 'at most 2000 rows' in str(refused.value.details) + refused.value.message
