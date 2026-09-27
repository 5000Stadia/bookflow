"""Money and dates on document pages read as a person reads them (R60).

The server's pages format with display.py; the payment windows that draw themselves in the
browser format with BookflowExactJSON. Both must read a figure the same way, and neither may
touch a value the page sends back.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bookflow.adapters.workbench import display
from bookflow.adapters.workbench.pages import env

STATIC = Path(__file__).resolve().parents[1] / 'src/bookflow/adapters/workbench/static'
CASES = [('1855.95', 'USD'), ('-40.00', 'USD'), ('0.00', 'USD'), ('1234567.5', 'USD'),
         ('12000', 'JPY'), ('5.125', 'KWD'), ('90071992547409.93', 'USD'), ('12.34', 'EUR')]
DATES = ['2026-11-12', '2025-01-03', '2026-02-09T10:00:00Z']


def test_received_goods_read_as_money_and_keep_a_foreign_code():
    received = {'product_total': {'amount': '1200.00', 'currency': 'EUR'},
                'shipping': {'amount': '34.50', 'currency': 'EUR'},
                'total': {'amount': '1234.50', 'currency': 'EUR'},
                'items': [{'profile': {'item': {'label': 'Valve'}}, 'quantity': '6',
                           'product_amount': {'amount': '1200.00'}, 'shipping': {'amount': '34.50'},
                           'amount': {'amount': '1234.50'}, 'product_per_unit': '200.00',
                           'shipping_per_unit': '5.75', 'total_per_unit': '205.75',
                           'unbilled_quantity_microunits': 6000000}]}
    html = env.get_template('receipt_costs.html').render(received=received, math_company_currency='USD')
    # A sentence names the money; a foreign amount keeps its code so it never reads as dollars.
    assert 'Product €1,200.00 EUR + Shipping €34.50 EUR = Received value €1,234.50 EUR.' in html
    # A table cell is the figure alone, grouped, under a header that names the currency once.
    assert '<th>Product (EUR)</th>' in html and '<td>1,234.50</td>' in html
    assert '1234.50 EUR' not in html


@pytest.mark.skipif(not shutil.which('node'), reason='Node unavailable')
def test_the_browser_reads_money_and_dates_exactly_as_the_server_does():
    program = """
const [script, cases, dates] = process.argv.slice(1);
global.window = {}; global.crypto = require('crypto').webcrypto;
global.document = {body: {dataset: {mathCompanyCurrency: 'USD', companyToday: '2026-09-27'}}};
require(script);
const x = window.BookflowExactJSON;
console.log(JSON.stringify({
  money: JSON.parse(cases).map(([a, c]) => x.money(a, c)),
  amount: JSON.parse(cases).map(([a]) => x.amount(a)),
  day: JSON.parse(dates).map(d => x.day(d)),
  longday: JSON.parse(dates).map(d => x.longday(d))}));
"""
    out = subprocess.run(['node', '-e', program, str(STATIC / 'exact-json.js'), json.dumps(CASES),
                          json.dumps(DATES)], capture_output=True, text=True, check=True)
    shown = json.loads(out.stdout)
    today = __import__('datetime').date(2026, 9, 27)
    assert shown['money'] == [display.money(a, c, home='USD') for a, c in CASES]
    assert shown['amount'] == [display.amount(a) for a, _ in CASES]
    assert shown['day'] == [display.day(d, today) for d in DATES]
    assert shown['longday'] == [display.longday(d) for d in DATES]
