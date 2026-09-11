"""The statement-charge window, list and settlement, driven in a real browser.

Three things are checked here that nothing else checks.

That the Statement charge tile is live in the only sense the availability contract accepts: a
person follows the tile's own link from the home board and a charge comes out the other end.
Registration and routing said nothing about this before -- the noun was registered, routed, and
filed under "Hub" with no group of its own, which is a command a person cannot reach.

That the charge's own list is a page and not a heading: the charge just entered is on it, with
its number, its date, the customer and what it was for, and its row opens the charge.

And that the charge can then be paid from the Receive payments window, which is the half the
accounting could not do at all until now: it appears in the list of what this customer owes,
named as a statement charge rather than described as an invoice, and taking it to zero leaves
the aging, the open receivables and the customer's own balance agreeing on the same figure.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command
from tests.test_service_sales_browser import _choose, _click, _contained, _fill, _value

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

RATE = '240.00'
QUANTITY = '0.25'
CHARGE = '60.00'        # 0.25 at 240.00


@pytest.fixture
def charge_browser(browser_site, tmp_path):
    """A signed-in browser on a fresh demo company, standing on the home board."""
    browser = _Cdp(tmp_path / 'charge-chrome')
    try:
        browser.navigate(browser_site.base_url + '/login')
        browser.evaluate(f"""(() => {{
          document.querySelector('[name="username"]').value = {json.dumps(browser_site.login)};
          document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
          document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()""")
        browser.wait_for("!!document.querySelector('.nav-group')")
        yield browser, browser_site
    finally:
        browser.close()


def _tile(title):
    return ('[...document.querySelectorAll("a.flow-tile")].find(a => '
            f'a.querySelector(".flow-tile-title")?.textContent.trim() === {json.dumps(title)})')


def _follow_tile(b, site, title, destination):
    """Click the tile itself, not its URL: the link is half of what is being tested."""
    b.navigate(f'{site.base_url}/c/{site.company_id}/')
    b.wait_for('!!document.querySelector("a.flow-tile")')
    assert b.evaluate(f'!!{_tile(title)}'), f'the {title} tile is not a link on the home board'
    assert b.evaluate(f'{_tile(title)}.getAttribute("href")') == \
        f'/c/{site.company_id}{destination}'
    b.evaluate(f'{_tile(title)}.click()')


def _books(b, site, tag):
    run = lambda name, payload: _command(b, site, name, payload)
    income = run('account.create', {'name': f'{tag} fees', 'type': 'income'})['id']
    code = next(row['id'] for row in run('sales-tax-code.list', {})['items'] if not row['taxable'])
    run('item.create', {'name': f'{tag} consultation', 'type': 'service', 'sales_enabled': True,
                        'description': 'Legal services', 'income_account_id': income,
                        'price': RATE, 'sales_tax_code_id': code})
    run('customer.create', {'name': f'{tag} holdings'})
    return dict(run=run, tag=tag, income=income)


def _enter_charge(b, books, *, number):
    _choose(b, 'f:customer', f'{books["tag"]} holdings')
    _fill(b, 'f:date', '2026-06-03')
    _fill(b, 'f:number', number)
    _choose(b, 'f:item', f'{books["tag"]} consultation')
    _fill(b, 'f:quantity', QUANTITY)
    _fill(b, 'f:rate', RATE)
    _fill(b, 'f:description', 'Call re lease, 15 minutes')
    _fill(b, 'ctx:reason', 'Enter the charge')


def test_the_statement_charge_tile_opens_a_window_that_posts_the_charge(charge_browser):
    """A person reaches the charge from the board and enters one through the page they land on."""
    b, site = charge_browser
    b.viewport(1280, 900)
    books = _books(b, site, 'Tile')

    _follow_tile(b, site, 'Statement charge', '/statement-charge/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'New statement charge'
    # The selectors are pickers, not boxes to type a stable id into. Without these the page
    # renders and is still unusable, which is the failure this whole file exists to catch.
    for field in ('f:customer', 'f:item', 'f:class_id', 'f:ar_account', 'f:tax_code'):
        assert b.evaluate(f'''!!document.getElementsByName({json.dumps(field)})[0]
            ?.closest("[data-reference]")'''), field

    _enter_charge(b, books, number='SC-1')
    _click(b, 'submit')
    b.wait_for('!document.querySelector("[data-generated-form]")')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]

    written = books['run']('statement-charge.show', {'statement_charge': 'SC-1'})
    assert written['total']['amount'] == CHARGE and written['due_date'] is None
    assert written['memo'] == 'Call re lease, 15 minutes'
    assert written['customer_name'] == f'{books["tag"]} holdings'


def test_the_statement_charges_tile_opens_a_list_whose_rows_open_the_charge(charge_browser):
    """The list is a page with the charge on it, and the row is the way back to the document."""
    b, site = charge_browser
    b.viewport(1280, 900)
    books = _books(b, site, 'List')
    charge = books['run']('statement-charge.post', {
        'customer': f'{books["tag"]} holdings', 'date': '2026-06-03', 'number': 'LC-1',
        'item': f'{books["tag"]} consultation', 'quantity': QUANTITY, 'rate': RATE,
        'description': 'Quarter hour'})

    _follow_tile(b, site, 'Statement charges', '/statement-charge')
    b.wait_for('!!document.querySelector("table")')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'Statement charges'
    headers = b.evaluate('[...document.querySelectorAll("thead th")].map(e=>e.textContent.trim())')
    assert headers[:6] == ['Number', 'Date', 'Customer name', 'Memo', 'Total', 'Status'], headers
    row = b.evaluate('[...document.querySelectorAll("tbody tr")].map(e=>e.innerText)')
    assert any('LC-1' in text and 'Quarter hour' in text and '60.00' in text for text in row), row
    _contained(b, 1280)

    link = ('[...document.querySelectorAll("tbody a")].find(a => a.textContent.trim() === "LC-1")')
    assert b.evaluate(f'!!{link}'), 'the list row does not open the charge'
    b.evaluate(f'{link}.click()')
    b.wait_for(f'location.pathname.endsWith({json.dumps("/" + charge["id"])})')
    assert CHARGE in b.evaluate('document.body.innerText')


def _settled(b):
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert b.evaluate("document.querySelector('#payment-error').hidden"), \
        b.evaluate("document.querySelector('#payment-error').innerText")


def _press(b, identity):
    b.evaluate(f"document.getElementById('payment-'+{json.dumps(identity)}).click()")
    _settled(b)


def _set(b, identity, value):
    b.evaluate(f"""(() => {{const x = document.getElementById('payment-'+{json.dumps(identity)});
        x.value = {json.dumps(value)};
        x.dispatchEvent(new Event('change', {{bubbles: true}}));}})()""")
    _settled(b)


def test_a_charge_is_paid_from_the_receive_payments_window(charge_browser):
    """The half the books could not do: a charge in the list of what is owed, taken to zero.

    The window is reached from its own tile, the charge is there among what this customer owes
    and named as a statement charge rather than described as an invoice, and after the receipt
    is saved the aging, the open receivables and the customer's own balance all read the same
    nothing. Every figure after the save is read back through a real command, not off the page.
    """
    b, site = charge_browser
    b.viewport(1280, 900)
    books = _books(b, site, 'Pay')
    run = books['run']
    customer = run('customer.show', {'customer': f'{books["tag"]} holdings'})['id']
    method = run('payment-method.create', {'name': 'Charge cheque', 'kind': 'check'})['id']
    bank = run('account.create', {'name': 'Charge bank', 'type': 'bank'})['id']
    charge = run('statement-charge.post', {
        'customer': customer, 'date': '2026-06-03', 'number': 'PC-1',
        'item': f'{books["tag"]} consultation', 'quantity': QUANTITY, 'rate': RATE,
        'description': 'Quarter hour'})

    _follow_tile(b, site, 'Receive payment', '/receive-payments')
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded === 'true'")
    _settled(b)
    b.navigate(f'{site.base_url}/c/{site.company_id}/receive-payments?customer={customer}')
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded === 'true'")
    _settled(b)
    _set(b, 'date', '2026-06-05')
    _set(b, 'amount', CHARGE)
    _set(b, 'method', method)
    _set(b, 'destination', bank)
    _press(b, 'load')

    b.wait_for(f'!!document.querySelector(\'[data-invoice="{charge["id"]}"]\')')
    row = f'document.querySelector(\'[data-invoice="{charge["id"]}"]\')'
    assert 'PC-1' in b.evaluate(f'{row}.innerText') and CHARGE in b.evaluate(f'{row}.innerText')
    # Named as what it is. A row labelled "Select invoice PC-1" would tell the reader something
    # untrue about the document they are settling, and its link would open the wrong page.
    assert b.evaluate(f'{row}.querySelector("input[type=checkbox]").getAttribute("aria-label")') \
        == 'Select statement charge PC-1'
    assert f'/statement-charge/{charge["id"]}' in b.evaluate(f'{row}.querySelector("a").getAttribute("href")')
    _contained(b, 1280)

    b.evaluate(f'{row}.querySelector("input[type=checkbox]").click()')
    _settled(b)
    assert CHARGE in b.evaluate("document.querySelector('#payment-totals').innerText")
    _press(b, 'preview')
    _press(b, 'save')
    assert not b.evaluate("document.querySelector('#payment-record').hidden")

    settled = run('invoice.settlement', {'invoice': charge['id']})
    assert (settled['status'], settled['applied_minor_units'], settled['due_minor_units']) \
        == ('paid', 6000, 0)
    aging = run('report.ar-aging', {'as_of': '2026-12-31'})
    assert [row for row in aging['rows']
            if row['display_customer_label'] == f'{books["tag"]} holdings'] == []
    assert [row for row in run('report.open-invoices', {'as_of': '2026-12-31'})['rows']
            if row['number'] == 'PC-1'] == []
    assert run('customer.show', {'customer': customer})['current_balance']['minor_units'] == 0
