"""Stock sent back to a vendor, entered and corrected through the vendor credit window.

A person opens the Enter vendor credit window, switches to the Items tab, sends four breakers
back, previews and saves. What comes out is the credit the command writes for an agent: the
quantity is off the shelf at the credited cost, the saved page lists the item row, and the
correction form opens with both grids baselined so that saving it untouched writes nothing.
"""
import pytest

from tests.test_bill_form_browser import (
    _act, _add_line, _fill, _item, _open_tab, _pick, _save, _tabs, _text, _totals, _value,
)
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_credit_windows_browser import _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

# 12 bought at 42.10 is 505.20; four sent back at 42.10 is 168.40, leaving 8 worth 336.80.
BOUGHT = '505.20'
CREDITED = '168.40'
LEFT = '336.80'


def _books(b, site, tag):
    run = lambda name, payload: _command(b, site, name, payload)
    income = run('account.create', {'name': f'{tag} sales', 'type': 'income'})['id']
    cogs = run('account.create', {'name': f'{tag} cost of sales', 'type': 'cost_of_goods_sold'})['id']
    item = run('item.create', {
        'name': f'{tag} breaker', 'type': 'inventory_part', 'description': '20A breaker',
        'price': '64.00', 'purchase_description': '20A breaker', 'cost': '42.10',
        'income_account_id': income, 'cogs_account_id': cogs})['id']
    vendor = run('vendor.create', {'name': f'{tag} supply'})['id']
    run('bill.post', {'vendor': vendor, 'date': '2026-03-02', 'supplier_reference': 'STOCK-1',
                      'items': [{'item': item, 'quantity': '12', 'unit_cost': '42.10'}]})
    return dict(run=run, tag=tag, item=item, vendor=vendor)


def _on_hand(books):
    rows = books['run']('report.stock-status', {'as_of': '2026-12-31', 'limit': 50})['rows']
    row = next(r for r in rows if r['item_id'] == books['item'])
    return row['quantity_on_hand'], row['asset_value']['amount']


def test_the_items_tab_sends_stock_back_and_the_saved_credit_says_what_went(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Back')
    assert _on_hand(books) == ('12', BOUGHT)

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/vendor-credit/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    b.wait_for('!!document.querySelector("[data-line-tab]")')
    assert _tabs(b) == {'labels': ['Expenses', 'Items'], 'selected': ['expenses'],
                        'shown': ['expenses']}, _tabs(b)
    _open_tab(b, 'items')

    _pick(b, 'f:vendor', 'Back supply')
    _fill(b, 'f:date', '2026-03-20')
    _fill(b, 'f:supplier_reference', 'CM-77')
    _add_line(b, 'items')
    _pick(b, _item(b, 0, 'item'), 'Back breaker')
    _fill(b, _item(b, 0, 'quantity'), '4')
    _fill(b, _item(b, 0, 'unit_cost'), '42.10')

    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    # The footer names the grid the rows are on; a credit sent wholly on Items is not told it
    # credited nothing on Expenses.
    assert _totals(b)['Credited items'] == f'${CREDITED}', _totals(b)
    assert _totals(b)['Taken off what you owe'] == f'${CREDITED}'
    assert _tabs(b)['shown'] == ['items'], _tabs(b)
    _contained(b, 1280)

    credit_id = _save(b)
    written = books['run']('vendor-credit.show', {'credit': credit_id})
    assert written['total']['amount'] == CREDITED and written['expense_total']['minor_units'] == 0
    row = written['revision']['items'][0]
    assert (row['item_id'], row['quantity'], row['amount']['amount']) == (books['item'], '4', CREDITED)
    # Four came off the shelf at 42.10 each, so eight remain, worth what is left of the bill.
    assert _on_hand(books) == ('8', LEFT)

    page = _text(b, '.sales-document')
    for part in ('Back breaker', '42.10', CREDITED, 'leaves the shelf at the credited cost'):
        assert part in page, (part, page[:1200])
    assert b.evaluate('!!document.querySelector(".vendor-credit-item-lines")')
    assert b.evaluate('!document.querySelector(".vendor-credit-lines")')


def test_correcting_a_credit_with_items_opens_both_grids_on_what_was_captured(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Fix')
    credit = books['run']('vendor-credit.post', {
        'vendor': books['vendor'], 'date': '2026-03-20', 'supplier_reference': 'CM-9',
        'items': [{'item': books['item'], 'quantity': '4', 'unit_cost': '42.10'}]})

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/vendor-credit/{credit["id"]}/update')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    b.wait_for('!!document.querySelector("[data-line-tab]")')
    _open_tab(b, 'items')
    assert _value(b, _item(b, 0, 'item')) == books['item']
    assert _value(b, _item(b, 0, 'quantity')) == '4'
    assert _value(b, _item(b, 0, 'unit_cost')) == '42.10'
    assert _value(b, _item(b, 0, 'line_id'))

    # One more came back: five at 42.10 is 210.50, and the shelf goes with it.
    _fill(b, 'ctx:reason', 'A fifth breaker went back as well')
    _fill(b, _item(b, 0, 'quantity'), '5')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    _save(b)
    corrected = books['run']('vendor-credit.show', {'credit': credit['id']})
    assert corrected['revision']['revision_number'] == 2
    assert corrected['total']['amount'] == '210.50'
    assert _on_hand(books) == ('7', '294.70')   # 505.20 - 210.50 = 294.70
