"""No workbench document page carries bloat (R157).

The "All fields and technical details" section is fetched only when opened, so a document
page's size does not grow with the facts its lines carry. An invoice billed from a quote with
a long installment history used to repeat every earlier bill's span proofs inline.
"""
import os
import re

import pytest

from fastapi.testclient import TestClient

from tests.test_row3_host import OUTSIDER_PASSWORD, hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser
from tests.test_tax_policy_sales import sale, tax_sale, request, COMPANY  # noqa: F401
from tests.test_work_billing_lifecycle import accepted, bill

MEASURE = os.environ.get('BOOKFLOW_MEASURE_PAGES')


def first_id(client, noun):
    return client.run(noun + ' query', {'limit': 1}, company=COMPANY)['items'][0]['id']


def long_quote_invoice(client, tax_sale, installments=50, lines=3):
    """The invoice that finishes a quote billed in installments with every other one voided:
    each of its lines covers `installments / 2` separate spans of the quote's history."""
    source = accepted(client, tax_sale, **dict(request(tax_sale), lines=[dict(item=tax_sale['item'],
        quantity=str(installments + 1), net_amount=f'{(installments + 1) // 100}.{(installments + 1) % 100:02d}',
        tax_code=tax_sale['taxable']) for _ in range(lines)]))
    ids = [row['line_id'] for row in source['revision']['lines']]
    bills = []
    for index in range(installments):
        bills.append(bill(client, dict(source, version=2 + index), f'size-{index}',
                          selections=[dict(line_id=key, quantity='1') for key in ids]))
    for invoice in bills[::2]:
        client.run('invoice void', dict(invoice=invoice['id'], expected_version=1), company=COMPANY,
                   reason='Release alternating spans')
    source = client.run('estimate show', dict(estimate=source['id']), company=COMPANY)
    before = stored(client) if MEASURE else None
    last = bill(client, source, 'size-finish')
    if MEASURE:
        after = stored(client)
        print('STORED BYTES FOR FINISH INVOICE pages', after[0] - before[0], 'payload',
              sum(after[1].values()) - sum(before[1].values()),
              sorted(((after[1].get(t, 0) - before[1].get(t, 0), t) for t in after[1]), reverse=True)[:8])
    return last


def stored(client):
    import sqlite3
    from tests.test_row8_journal import database_path
    with sqlite3.connect(database_path(client)) as db:
        pages = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
        try:
            tables = dict(db.execute('SELECT name, SUM(payload) FROM dbstat GROUP BY name').fetchall())
        except sqlite3.OperationalError:
            tables = {}
    return pages, tables


@pytest.fixture
def documents(client, tax_sale):
    # Built before the server starts: the served host holds the data root.
    found = {noun: first_id(client, noun) for noun in ('invoice', 'bill', 'payment')}
    found['long-quote invoice'] = long_quote_invoice(client, tax_sale)['id']
    return found


# Measured 2026-09-28 before R157 (section inline) and after (section fetched when opened), bytes:
#   demo invoice         67,789 -> 24,376
#   demo bill            48,580 -> 21,549
#   demo payment         22,642 -> 16,494
#   long-quote invoice  199,391 -> 28,561  (50 installments x 3 lines, every other voided,
#                                                 finish invoice covering 26 spans per line)
# About twice the largest page after, so a real regression fails and ordinary growth does not.
BUDGET = 64_000


def test_document_pages_stay_small(documents, hosted):
    browser = _browser(hosted)
    cid = hosted.company_id
    sizes = {}
    for label, record in documents.items():
        noun = label.split()[-1]
        url = f'/c/{cid}/{noun}/{record}'
        page = browser.get(url)
        assert page.status_code == 200, page.text[:500]
        sizes[label] = len(page.content)
        # Closed, the section is its summary line and a request it has not made yet.
        section = re.search(r'(?s)<details class="technical-details[^"]*" hx-get="([^"]+)" hx-trigger="toggle once"[^>]*>'
                            r'<summary>All fields and technical details</summary>(.*?)</details>', page.text)
        assert section, label
        assert section.group(1) == url + '/technical-details'
        assert 'result-fields' not in section.group(2)
        details = browser.get(section.group(1))
        assert details.status_code == 200, details.text[:500]
        assert 'class="result-fields"' in details.text and '<html' not in details.text
        assert re.search(rf'<dt>Id</dt><dd>\s*{record}\s*</dd>', details.text)  # the full record, from its id down
        if label == 'long-quote invoice':
            assert details.text.count('1 Replace kitchen tap · Service labor: Bills 26 of 51 quoted units, in 26 separate parts of the source line') == 3
            assert details.text.index('Billed lines') < details.text.index('Allocation proof')
            assert 'Source basis hash' in details.text and 'Source basis hash' not in page.text
    if MEASURE:
        print('PAGE SIZES', sizes)
    over = {label: size for label, size in sizes.items() if size > BUDGET}
    assert not over, (BUDGET, sizes)
    # The details answer to the page's own permission: someone outside the company gets nothing.
    outsider = TestClient(hosted.handle.app, follow_redirects=False)
    assert outsider.post('/login', json={'username': 'outsider', 'password': OUTSIDER_PASSWORD}).status_code == 200
    denied = outsider.get(f"/c/{cid}/invoice/{documents['long-quote invoice']}/technical-details")
    assert denied.status_code in (403, 404), denied.status_code
    assert 'result-fields' not in denied.text
