"""The Pay Bills window, the bill payment list and the saved payment, driven in real Chrome.

Five things are checked here that nothing else checks. That the Pay Bills tile on the home
board is live in the only sense the availability contract accepts -- a person follows the
tile's own link and a payment comes out the other end, with the bills' open balances falling
by what was typed. That a selection spanning two vendors says so *before* the save and then
writes exactly those two payments, one per payee. That the check-number field is offered only
where the command accepts one: a check drawn on a bank account. That a saved payment can be
found in a list and read back with the bills it settled. And that at 390px nothing on either
page has anything to scroll sideways -- each element's own scrollWidth against its own
clientWidth, not merely a page that does not move.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

READY = '!!document.querySelector("#pay-bills[data-loaded]")'
# `perform` holds aria-busy for the whole of an action, so its absence is the action's end.
IDLE = '!document.querySelector("#pay-bills").hasAttribute("aria-busy")'


def _books(b, site, tag):
    run = lambda name, payload: _command(b, site, name, payload)
    accounts = run('account.list', {})['items']
    methods = run('payment-method.list', {})['items']
    return dict(
        run=run, tag=tag,
        expense=run('account.create', {'name': f'{tag} parts', 'type': 'expense'})['id'],
        bank=next(a for a in accounts if a['type'] == 'bank')['id'],
        card=next(a for a in accounts if a['type'] == 'credit_card')['id'],
        ap=next(a for a in accounts if a['type'] == 'accounts_payable')['id'],
        check=next(m for m in methods if m['kind'] == 'check')['id'],
        card_method=next(m for m in methods if m['kind'] == 'credit_card')['id'],
    )


def _vendor(books, name):
    return books['run']('vendor.create', {'name': name})['id']


def _bill(books, vendor, date, amount, reference=None):
    return books['run']('bill.post', {
        'vendor': vendor, 'date': date,
        **({'supplier_reference': reference} if reference else {}),
        'expenses': [{'account': books['expense'], 'amount': amount}]})


def _open(books, bill_id):
    return books['run']('bill.show', {'bill': bill_id})['settlement_current']


def _set(b, element_id, value):
    b.evaluate(f'''(() => {{const e = document.getElementById({json.dumps(element_id)});
        e.value = {json.dumps(value)};
        if (e.tagName === 'SELECT' && e.value !== {json.dumps(value)})
            throw new Error('no such option: ' + {json.dumps(value)});
        e.dispatchEvent(new Event('input', {{bubbles: true}}));
        e.dispatchEvent(new Event('change', {{bubbles: true}}));}})()''')


def _row(bill_id):
    return f"""document.querySelector('#pay-bills-rows tr[data-bill="{bill_id}"]')"""


def _select_bill(b, bill_id, amount=None):
    b.evaluate(f'{_row(bill_id)}.querySelector(".pay-bills-select").click()')
    if amount is not None:
        b.evaluate(f'''(() => {{const e = {_row(bill_id)}.querySelector(".pay-bills-amount");
            e.value = {json.dumps(amount)};
            e.dispatchEvent(new Event('input', {{bubbles: true}}));
            e.dispatchEvent(new Event('change', {{bubbles: true}}));}})()''')


def _cell(b, bill_id, label):
    """The value in one cell, past the field name the block carries on a phone."""
    return b.evaluate(f'{_row(bill_id)}.querySelector(`td[data-label="{label}"]`)'
                      '.lastElementChild.textContent.trim()')


def _text(b, selector):
    return b.evaluate(f'document.querySelector({json.dumps(selector)})?.innerText')


def _groups(b):
    return b.evaluate('''(() => {const summary = document.querySelector("#pay-bills-groups-summary");
        return {count: Number(summary.dataset.groupCount), summary: summary.innerText,
                items: [...document.querySelectorAll("#pay-bills-group-list li")].map(
                    li => ({vendor: li.dataset.vendorId, ap: li.dataset.apAccountId, text: li.innerText}))};})()''')


def _total(b):
    return b.evaluate('''(() => {const line = document.querySelector("#pay-bills-total");
        return line && {minor: Number(line.dataset.totalMinor), bills: Number(line.dataset.billCount),
                        text: line.innerText};})()''')


def _open_window(b, env, books, *, click_tile=False):
    """Land on the Pay Bills window, through the tile itself when that is what is being proved."""
    if click_tile:
        b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/')
        b.wait_for('!!document.querySelector("a.flow-tile")')
        tile = '''[...document.querySelectorAll("a.flow-tile")].find(
            a => a.querySelector(".flow-tile-title")?.textContent.trim() === "Pay bills")'''
        assert b.evaluate(f'!!{tile}'), 'the Pay bills tile is not a link on the home board'
        assert b.evaluate(f'{tile}.getAttribute("href")') == f'/c/{env.site.company_id}/pay-bills'
        b.evaluate(f'{tile}.click()')
    else:
        b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/pay-bills')
    b.wait_for(READY)
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'Pay bills'
    _set(b, 'pay-bills-date', '2026-06-30')
    _set(b, 'pay-bills-funding', books['bank'])
    _set(b, 'pay-bills-method', books['check'])


def _save(b):
    """Saving writes over fetch; the result panel and the end of the action are the signal."""
    b.evaluate('document.querySelector("#pay-bills-save").click()')
    b.wait_for(f'!document.querySelector("#pay-bills-result").hidden && {IDLE}')
    assert b.evaluate('document.querySelector("#pay-bills-error").hidden'), \
        _text(b, '#pay-bills-error')


def test_the_pay_bills_tile_pays_two_bills_and_each_open_balance_falls_by_what_was_typed(register_browser):
    """The tile is live: a person follows its own link and two bills come back settled."""
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Paying')
    vendor = _vendor(books, 'Paying supply')
    first = _bill(books, vendor, '2026-03-04', '184.60', 'SUP-1')
    second = _bill(books, vendor, '2026-03-09', '100.00', 'SUP-2')

    _open_window(b, env, books, click_tile=True)
    b.wait_for(f'document.querySelectorAll("#pay-bills-rows tr").length === 2')

    # The window shows what the bill itself reports: due date, original amount, open balance.
    assert _cell(b, first['id'], 'Due date') == '2026-03-04'
    assert _cell(b, first['id'], 'Original amount') == '184.60'
    assert _cell(b, first['id'], 'Open balance') == '184.60'
    assert _cell(b, second['id'], 'Open balance') == '100.00'

    _set(b, 'pay-bills-check', '1044')
    _select_bill(b, first['id'], '169.20/2')       # a partial payment, typed as a calculation
    _select_bill(b, second['id'])                  # defaults to the whole open balance

    # The amount a selected row defaults to is the open balance, not a blank box.
    assert b.evaluate(f'{_row(second["id"])}.querySelector(".pay-bills-amount").value') == '100.00'
    # The shared arithmetic entry works in these boxes too: what it offers is what is submitted,
    # normalised the way it normalises everywhere else in the product.
    assert b.evaluate(f'{_row(first["id"])}.querySelector(".pay-bills-amount").value') == '84.6'
    assert _total(b) == {'minor': 18460, 'bills': 2,
                         'text': 'Total to be paid: 184.60 USD across 2 bills.'}
    # One vendor, one payable: one payment, and the page says so before anything is written.
    before = _groups(b)
    assert before['count'] == 1, before
    assert 'makes 1 payment' in before['summary'], before
    assert before['items'][0]['vendor'] == vendor and before['items'][0]['ap'] == books['ap'], before
    assert '184.60 USD' in before['items'][0]['text'], before

    _save(b)

    result = _text(b, '#pay-bills-result')
    assert 'Bill payment written' in result, result
    assert '184.60 USD paid across 2 bills' in result, result
    payment_id = b.evaluate('document.querySelector("#pay-bills-result [data-payment]").dataset.payment')

    written = books['run']('bill.payment.show', {'payment': payment_id})
    assert written['vendor_name'] == 'Paying supply'
    assert written['total'] == {'amount': '184.60', 'currency': 'USD', 'minor_units': 18460}
    assert written['check_number'] == '1044'
    assert written['funding_account_id'] == books['bank']
    assert written['settlement_current']['status'] == 'applied'
    settled = {line['bill_number']: line['amount']['amount'] for line in written['revision']['lines']}
    assert settled == {first['number']: '84.60', second['number']: '100.00'}, settled

    # Each bill's open balance fell by exactly what was entered against it.
    assert _open(books, first['id'])['open'] == {'amount': '100.00', 'currency': 'USD',
                                                 'minor_units': 10000}
    assert _open(books, first['id'])['status'] == 'partial'
    assert _open(books, second['id'])['open']['minor_units'] == 0
    assert _open(books, second['id'])['status'] == 'paid'

    # And the window reloaded itself on what is open now: the settled bill is gone from the
    # list, and the partly paid one stands at its remainder.
    b.wait_for('document.querySelectorAll("#pay-bills-rows tr").length === 1')
    assert _cell(b, first['id'], 'Open balance') == '100.00'
    assert b.evaluate(f'!{_row(second["id"])}')


def test_two_vendors_say_two_payments_before_the_save_and_one_save_writes_both(register_browser):
    """The grouping is visible up front, and a check number is refused where it names two checks."""
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Split')
    one, two = _vendor(books, 'Split supply'), _vendor(books, 'Split fuel')
    first = _bill(books, one, '2026-03-04', '120.00')
    second = _bill(books, one, '2026-03-05', '30.00')
    third = _bill(books, two, '2026-03-06', '70.00')

    _open_window(b, env, books)
    b.wait_for('document.querySelectorAll("#pay-bills-rows tr").length === 3')
    for bill in (first, second, third):
        _select_bill(b, bill['id'])

    shown = _groups(b)
    assert shown['count'] == 2, shown
    assert 'makes 2 payments, one for each payee' in shown['summary'], shown
    assert [item['vendor'] for item in shown['items']] == [one, two], shown
    assert all(item['ap'] == books['ap'] for item in shown['items']), shown
    assert '150.00 USD · 2 bills' in shown['items'][0]['text'], shown['items'][0]
    assert '70.00 USD · 1 bill' in shown['items'][1]['text'], shown['items'][1]
    assert _total(b)['minor'] == 22000

    # A check number names one check. Two payees is two checks, so the save is held back and
    # says which of the two things to change -- the same rule the command itself enforces.
    _set(b, 'pay-bills-check', '2001')
    assert b.evaluate('document.querySelector("#pay-bills-save").disabled') is True
    held = _text(b, '#pay-bills-message')
    assert 'check number names one check' in held and 'writes 2 payments' in held, held
    _set(b, 'pay-bills-check', '')
    assert b.evaluate('document.querySelector("#pay-bills-save").disabled') is False

    _save(b)
    assert b.evaluate('Number(document.querySelector("#pay-bills-result").dataset.groupCount)') == 2
    ids = b.evaluate('[...document.querySelectorAll("#pay-bills-result [data-payment]")]'
                     '.map(node => node.dataset.payment)')
    assert len(ids) == 2, ids

    payments = [books['run']('bill.payment.show', {'payment': identifier}) for identifier in ids]
    assert [row['vendor_name'] for row in payments] == ['Split supply', 'Split fuel']
    assert [row['total']['amount'] for row in payments] == ['150.00', '70.00']
    assert {line['bill_number'] for line in payments[0]['revision']['lines']} == \
        {first['number'], second['number']}
    assert {line['bill_number'] for line in payments[1]['revision']['lines']} == {third['number']}
    assert payments[0]['id'] != payments[1]['id']
    assert payments[0]['number'] != payments[1]['number']
    for bill in (first, second, third):
        assert _open(books, bill['id'])['open']['minor_units'] == 0, bill['number']


def test_the_check_number_is_offered_only_for_a_check_drawn_on_a_bank_account(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Cheque')
    vendor = _vendor(books, 'Cheque supply')
    bill = _bill(books, vendor, '2026-03-04', '60.00')

    _open_window(b, env, books)
    b.wait_for('document.querySelectorAll("#pay-bills-rows tr").length === 1')
    assert b.evaluate('!document.querySelector("#pay-bills-check-label").hidden')

    # A credit card carries no paper check, whatever the method says.
    _set(b, 'pay-bills-funding', books['card'])
    assert b.evaluate('document.querySelector("#pay-bills-check-label").hidden')
    # Nor does a bank account paid by a method that is not a check.
    _set(b, 'pay-bills-funding', books['bank'])
    _set(b, 'pay-bills-method', books['card_method'])
    assert b.evaluate('document.querySelector("#pay-bills-check-label").hidden')

    _set(b, 'pay-bills-method', books['check'])
    assert b.evaluate('!document.querySelector("#pay-bills-check-label").hidden')
    _set(b, 'pay-bills-check', '3090')
    _select_bill(b, bill['id'])
    _save(b)

    payment_id = b.evaluate('document.querySelector("#pay-bills-result [data-payment]").dataset.payment')
    written = books['run']('bill.payment.show', {'payment': payment_id})
    assert written['check_number'] == '3090'
    assert written['revision']['profile']['payment_method']['kind'] == 'check'
    assert written['funding_kind'] == 'bank_cash'


def test_a_saved_payment_is_found_in_the_list_and_read_back_with_the_bills_it_settled(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Reading')
    vendor = _vendor(books, 'Reading supply')
    first = _bill(books, vendor, '2026-03-04', '25.00')
    second = _bill(books, vendor, '2026-03-05', '75.00')
    paid = books['run']('bill.pay', {
        'date': '2026-06-30', 'funding_account': books['bank'], 'method': books['check'],
        'check_number': '5150', 'memo': 'June run', 'reference': 'REF-J',
        'bills': [{'bill': first['id']}, {'bill': second['id'], 'amount': '25.00'}]})
    payment = paid['payments'][0]

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill-payment')
    b.wait_for('!!document.querySelector("table td")')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'Bill payments'
    heads = b.evaluate('[...document.querySelectorAll("table th")].map(e => e.textContent.trim())')
    assert heads == ['Date', 'Payment', 'Vendor', 'Method', 'Check no.', 'Amount', 'Applied',
                     'Unapplied', 'Status'], heads
    row = b.evaluate(f'''[...document.querySelectorAll("table tbody tr")]
        .map(r => [...r.querySelectorAll("td")].map(c => c.innerText.trim()))
        .find(cells => cells[1] === {json.dumps(payment["number"])})''')
    assert row == ['2026-06-30', payment['number'], 'Reading supply', 'Check', '5150',
                   '50.00 USD', '50.00', '0.00', 'posted · applied'], row

    # The payment number is the way in, the way a bill number is.
    b.evaluate(f'''document.querySelector('a[href$="/bill-payment/{payment["id"]}"]').click()''')
    b.wait_for(f'location.pathname.endsWith("/bill-payment/{payment["id"]}") '
               '&& !!document.querySelector("[data-payment-settlement]")')

    detail = _text(b, '.sales-document')
    for part in ('Reading supply', '5150', 'REF-J', '50.00', 'Check', '2026-06-30'):
        assert part in detail, (part, detail[:1200])
    assert 'Checking' in b.evaluate('document.querySelector("[data-funding-account]").innerText')
    assert 'Applied to bills 50.00' in _text(b, '[data-payment-settlement]')
    settled = b.evaluate('''[...document.querySelectorAll(".payment-lines tbody tr")].map(r => [
        r.dataset.bill,
        r.querySelector('[data-label="Bill"] a').innerText.trim(),
        r.querySelector('[data-label="Paid on this bill"]').innerText.trim()])''')
    assert sorted(settled) == sorted([[first['id'], first['number'], '25.00'],
                                      [second['id'], second['number'], '25.00']]), settled
    # Every settled bill is a link back to the bill itself.
    assert b.evaluate(f'''!!document.querySelector('.payment-lines a[href$="/bill/{first["id"]}"]')''')
    history = b.evaluate('''[...document.querySelectorAll("[data-settlement-history] tbody tr")]
        .map(r => [...r.querySelectorAll("td")].map(c => c.textContent.trim()))''')
    assert sorted(history) == sorted([['apply', first['number'], '25.00', '2026-06-30', 'active'],
                                      ['apply', second['number'], '25.00', '2026-06-30', 'active']]), history

    # Following one of those links lands on the bill, and the bill says what is left on it.
    b.evaluate(f'''document.querySelector('.payment-lines a[href$="/bill/{second["id"]}"]').click()''')
    b.wait_for(f'location.pathname.endsWith("/bill/{second["id"]}") '
               '&& !!document.querySelector("[data-bill-settlement]")')
    assert 'Partial · Bill 75.00 · Paid 25.00 · Open 50.00 USD' in _text(b, '[data-bill-settlement]')

    # And the bill offers the way to pay what is left on it, filtered to that vendor.
    other = _vendor(books, 'Reading fuel')
    _bill(books, other, '2026-03-06', '9.00')
    b.evaluate('document.querySelector("[data-pay-this-bill]").click()')
    b.wait_for(f'location.pathname.endsWith("/pay-bills") && {READY}')
    assert 'Showing open bills for Reading supply.' == _text(b, '#pay-bills-vendor-chosen')
    on_screen = b.evaluate('[...document.querySelectorAll("#pay-bills-rows tr")]'
                           '.map(tr => tr.dataset.bill)')
    assert on_screen == [second['id']], on_screen


def test_the_pay_bills_window_has_nothing_to_scroll_sideways_at_phone_width(register_browser):
    """Below 700px an open bill stops being a row of a seven-column table and becomes a block."""
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    books = _books(b, env.site, 'Phone')
    vendor = _vendor(books, 'Phone supply and equipment company')
    first = _bill(books, vendor, '2026-03-04', '1184.60', 'A-LONG-SUPPLIER-REFERENCE-0001')
    _bill(books, vendor, '2026-03-09', '100.00')

    _open_window(b, env, books)
    b.wait_for('document.querySelectorAll("#pay-bills-rows tr").length === 2')
    _select_bill(b, first['id'])

    _contained(b, 390)
    table = b.evaluate('''(() => {const t = document.querySelector(".pay-bills-open");
        const w = t.closest(".pay-bills-table-wrap");
        return {scroll: t.scrollWidth, client: t.clientWidth, wrapScroll: w.scrollWidth,
                wrapClient: w.clientWidth,
                head: getComputedStyle(t.querySelector("thead")).position};})()''')
    assert table['scroll'] <= table['client'] + 1, table
    assert table['wrapScroll'] == table['wrapClient'], table
    assert table['head'] == 'absolute', table

    # Each element carries its own width, not merely a page that happens not to move.
    assert b.evaluate('''[...document.querySelectorAll("#pay-bills td, #pay-bills-group-list li, "
        + "#pay-bills-totals p, #pay-bills-header label, #pay-bills-header input, "
        + "#pay-bills-header select, #pay-bills-header textarea")]
        .every(e => e.scrollWidth <= e.clientWidth + 1)''')
    assert b.evaluate('''[...document.querySelectorAll("#pay-bills td")]
        .every(e => e.getBoundingClientRect().right <= innerWidth + 1)''')
    # Every cell names itself, because the column heads are off screen.
    assert b.evaluate('''[...document.querySelectorAll("#pay-bills-rows td")]
        .every(td => getComputedStyle(td.querySelector(".pay-bills-cell-label")).display === "block")''')

    # The block's own reading order: who it is owed to, which bill and when it is due each run
    # the whole width; the two money figures are the pair beside each other; the box you type
    # in is full width beneath them.
    boxes = b.evaluate(f'''(() => {{const row = {_row(first["id"])};
        const cell = label => row.querySelector(`[data-label="${{label}}"]`).getBoundingClientRect();
        const out = {{}};
        for (const label of ['Select', 'Vendor', 'Bill', 'Due date', 'Original amount',
                             'Open balance', 'Payment'])
            out[label] = {{top: Math.round(cell(label).top), width: Math.round(cell(label).width)}};
        return out;}})()''')
    assert boxes['Original amount']['top'] == boxes['Open balance']['top'], boxes
    assert boxes['Vendor']['width'] > boxes['Original amount']['width'] * 1.8, boxes
    assert boxes['Bill']['width'] == boxes['Vendor']['width'], boxes
    assert boxes['Due date']['width'] == boxes['Vendor']['width'], boxes
    assert boxes['Payment']['width'] == boxes['Vendor']['width'], boxes
    assert boxes['Due date']['top'] > boxes['Bill']['top'], boxes
    assert boxes['Original amount']['top'] > boxes['Due date']['top'], boxes
    assert boxes['Payment']['top'] > boxes['Original amount']['top'], boxes

    _save(b)
    _contained(b, 390)
    payment_id = b.evaluate('document.querySelector("#pay-bills-result [data-payment]").dataset.payment')

    # The saved payment reads as line cards on the same screen.
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill-payment/{payment_id}')
    b.wait_for('!!document.querySelector("[data-payment-settlement]")')
    _contained(b, 390)
    assert b.evaluate('''[...document.querySelectorAll(".payment-lines td, .sales-facts dd")]
        .every(e => e.scrollWidth <= e.clientWidth + 1
                    && e.getBoundingClientRect().right <= innerWidth + 1)''')
    assert b.evaluate('''[...document.querySelectorAll(".payment-lines tbody td")]
        .every(td => getComputedStyle(td.querySelector(".pay-bills-cell-label")).display === "block")''')

    # And the list of payments does too.
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill-payment')
    b.wait_for('!!document.querySelector("table td")')
    _contained(b, 390)
    assert b.evaluate('''[...document.querySelectorAll(".pay-bills-open td")]
        .every(e => e.scrollWidth <= e.clientWidth + 1
                    && e.getBoundingClientRect().right <= innerWidth + 1)''')
