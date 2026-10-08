"""A receipt over many invoices, or an invoice paid by many receipts, previews and pages in bulk reads.

A settlement page re-prepares the whole receipt (pages carry no state, the facts fingerprint binds them),
and preparing used to ask a dozen questions of every invoice or receipt one at a time, so a page cost
statements x documents: 403 invoices took 8,937 statements and about 8 s a page. A preparation now reads
what it needs of those documents once per 400. This counts statements, not seconds, so the counts are the
same on any machine; the bounds sit well under the old per-document cost and well over today's.
"""
from tests.test_billing_history_reads import statements
from tests.test_payment_review_additional import run, posted, method, sale, COMPANY  # noqa: F401

JOBS = 60  # more than the 50 rows a page carries


def pages(client, preview, kind, listing):
    """Every page of one prospective collection, and the statements its first page took."""
    d = next(x for x in listing(preview) if x['kind'] == kind)
    items, cursor, cost = list(preview['effect'][kind] if 'effect' in preview else preview['settlement']['effect'][kind]), d['next_cursor'], None
    while cursor:
        with statements() as seen:
            page = run(client, 'payment preview items', dict(request=d['request'],
                facts_fingerprint=d['facts_fingerprint'], kind=kind, cursor=cursor))
        cost = len(seen) if cost is None else cost
        items.extend(page['items'])
        cursor = page['next_cursor']
    return items, cost


def test_receipt_over_many_invoices_pages_in_bulk_reads(client, sale):
    targets, expected = [], {}
    for i in range(JOBS):
        job = client.customer.create(name=f'Bulk job {i:03}', parent_id=sale['customer'], company=COMPANY)['id']
        cents = 1 + i % 7
        invoice = posted(client, job, sale['item'], f'0.0{cents}', f'BULK-{i:03}')
        expected[job] = cents
        targets.append(dict(invoice=invoice['id'], expected_version=1, amount=f'0.0{cents}'))
    total = sum(expected.values())
    bank = client.account.create(name='Bulk Bank', type='bank', company=COMPANY)['id']
    draft = run(client, 'payment selection create', dict(mode='new_receipt', customer=sale['customer'],
        date='2026-06-02', amount=f'{total / 100:.2f}'))
    draft = run(client, 'payment selection update', dict(selection=draft['id'], expected_version=draft['version'], set_items=targets))
    data = dict(customer=sale['customer'], date='2026-06-02', amount=f'{total / 100:.2f}', deposit_to=bank,
        payment_method=method(client), operation_key='bulk-cash',
        applications=dict(mode='selection', selection=draft['id'], expected_version=draft['version']))
    with statements() as seen:
        preview = run(client, 'payment receive', data, dry_run=True)
    # 60 jobs took 1,398 statements before (22 each); 328 now (about 5 each and a fixed part).
    assert len(seen) <= 6 * JOBS + 200, len(seen)
    for kind in ('source_components', 'applications', 'allocations', 'document_changes'):
        items, cost = pages(client, preview, kind, lambda p: p['prospective_pages'])
        assert len(items) == JOBS
        # A settlement page prepares the whole receipt; that preparation is what stayed cheap.
        assert cost is not None and cost <= 6 * JOBS + 200, (kind, cost)
        if kind in ('applications', 'allocations'):
            assert sum(r['amount']['minor_units'] for r in items) == total
        if kind == 'source_components':
            assert {r['party_id']: r['received_minor_units'] for r in items} == expected


def test_invoice_paid_by_many_receipts_pages_in_bulk_reads(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '5.00', 'BULK-INVOICE')
    cash = method(client)
    for i in range(JOBS):
        run(client, 'payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='0.01',
            payment_method=cash, operation_key=f'bulk-{i}', applications=dict(mode='inline',
            items=[dict(invoice=invoice['id'], expected_version=i + 1, amount='0.01')])))
    settlement = run(client, 'invoice settlement', dict(invoice=invoice['id']))
    args = dict(invoice=invoice['id'], expected_version=JOBS + 1, operation_key='bulk-correct',
        settlement_guard=settlement['settlement_guard'], lines=[dict(line_id=invoice['revision']['lines'][0]['line_id'],
        item=sale['item'], quantity='2', unit_price='5.00')])
    with statements() as seen:
        preview = run(client, 'invoice update', args, reason='Correct', dry_run=True)
    # 60 receipts took 2,655 statements before (41 each); 425 now (about 6 each and a fixed part).
    assert len(seen) <= 8 * JOBS + 300, len(seen)
    items, cost = pages(client, preview['settlement'], 'allocations', lambda p: p['prospective_pages'])
    assert len(items) == 2 * JOBS
    assert cost is not None and cost <= 8 * JOBS + 300, cost
