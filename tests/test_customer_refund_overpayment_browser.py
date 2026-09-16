"""Sending an overpayment back, in a real browser, at desktop and phone widths.

The journey a person actually walks. A customer is invoiced 100.00 and pays 150.00. The
bookkeeper opens Customer payments, sees the row saying 50.00 is unapplied, follows the link on
that row -- never a typed address -- and finds the refund window already naming that payment and
that amount. They save it, read the saved refund, and go back to the payment list to see that
the row no longer offers the money.

Nothing here asserts that a command exists. Every step is a click on what the page renders, and
every figure read back is the one the server put there.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_credit_windows_browser import _books, _pick, _preview, _read, _save, _set, _text
from tests.test_service_sales_browser import _click, _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

UNIT = '25.00'
INVOICED = '100.00'
CHEQUE = '150.00'
OVERAGE = '50.00'


def _overpaid(books):
    """A 100.00 invoice and a 150.00 cheque, 100.00 of it applied to that invoice."""
    sale = books['run']('invoice.post', {
        'customer': books['customer'], 'date': '2026-03-02', 'due_date': '2026-04-01',
        'lines': [{'item': books['item'], 'quantity': '4', 'unit_price': UNIT}]})
    payment = books['run']('payment.receive', {
        'customer': books['customer'], 'date': '2026-03-05', 'amount': CHEQUE,
        'operation_key': 'overpaid-cheque', 'deposit_to': books['bank'],
        'payment_method': books['method'],
        'applications': {'mode': 'inline', 'items': [
            {'invoice': sale['id'], 'expected_version': sale['version'],
             'amount': INVOICED}]}})
    return sale, payment


def _row(b, payment_id):
    """The payment list row for one receipt, found by the link the page put in it.

    By id rather than by number: the number is the page's own to render, and a test that
    already knew it could not catch the page rendering somebody else's row.
    """
    link = json.dumps('a[href*="payment=' + payment_id + '"]')
    return (f'''[...document.querySelectorAll(".payment-invoices tbody tr")].find(
        tr => tr.querySelector({link}))''')


def _cell(b, row, label):
    selector = json.dumps(f'[data-label="{label}"]')
    return b.evaluate(f'{row}.querySelector({selector}).textContent.trim()')


@pytest.mark.timeout(1200)
@pytest.mark.parametrize('width', [1280, 390])
def test_the_payment_list_offers_the_overpayment_back_and_the_refund_takes_it(
        register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    books = _books(b, env.site, 'Over')
    sale, payment = _overpaid(books)
    base = f'{env.site.base_url}/c/{env.site.company_id}'

    # The list says what is standing, and the row carries the way to send it back.
    b.navigate(f'{base}/payment')
    b.wait_for('!!document.querySelector(".payment-invoices tbody tr")')
    row = _row(b, payment['id'])
    assert b.evaluate(f'!!{row}'), 'the receipt is not on the payment list'
    number = _cell(b, row, 'Payment')
    assert _cell(b, row, 'Unapplied credit') == OVERAGE, _cell(b, row, 'Unapplied credit')
    assert b.evaluate(f'!!{row}.querySelector("[data-refund-overpayment]")'), \
        'the overpaid row offers no way to send the money back'
    _contained(b, width)

    b.evaluate(f'{row}.querySelector("[data-refund-overpayment]").click()')
    b.wait_for('!!document.querySelector("[data-generated-form]") '
               '&& location.pathname.endsWith("/customer-refund/post")')

    # The window opened already naming that receipt and that exact figure.
    seeded = b.evaluate('''(() => {const e = document.querySelector(
        '[name^="c:sources:"][name$=":payment"]'); return e && e.value;})()''')
    assert seeded == payment['id'], seeded
    amount = b.evaluate('''(() => {const e = document.querySelector(
        '[name^="c:sources:"][name$=":amount"]'); return e && e.value;})()''')
    assert amount == OVERAGE, amount
    assert 'not applied to any invoice' in _text(b, 'body')
    _contained(b, width)

    _set(b, 'f:date', '2026-03-20')
    _pick(b, 'f:funding_account', 'Over checking')
    _pick(b, 'f:method', 'Check')
    _set(b, 'f:check_number', '3090')
    _set(b, 'f:memo', 'Overpayment returned')
    _preview(b)
    refund_id = _save(b, 'customer-refund', '.refund-sources')
    assert refund_id

    # The saved refund says which receipt it paid back and how much.
    shown = _text(b, '.sales-document')
    assert 'Payment ' + number in shown, shown[:1200]
    assert OVERAGE in shown, shown[:1200]
    _contained(b, width)

    # And the payment list no longer offers money that has been sent back.
    b.navigate(f'{base}/payment')
    b.wait_for('!!document.querySelector(".payment-invoices tbody tr")')
    row = _row(b, payment['id'])
    assert b.evaluate(f'!{row}.querySelector("[data-refund-overpayment]")'), \
        'a refunded overpayment is still being offered'
    assert _cell(b, row, 'Unapplied credit') == '0.00', _cell(b, row, 'Unapplied credit')
    _contained(b, width)
