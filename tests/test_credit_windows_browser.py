"""The three credit documents, driven in a real browser from the home board.

The availability contract, performed rather than asserted: each of the three grey tiles is
followed by a click on the tile itself, a document is entered through the page it lands on,
saved, and the rendered figures are read back off the saved document. Then a credit is applied
to an invoice from the credit memo's own page and the invoice's Balance Due is read again --
which is the only check that says the apply surface does what it claims.

Three further things are checked here that nothing else checks. That the window posts the same
command an agent sends, field for field. That a return opens from the invoice it returns and is
priced by it rather than by anything typed. And that at 390px every element on every one of
these pages fits inside its own box -- each element's own ``scrollWidth`` against its own
``clientWidth``, not merely a page that does not move sideways.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _click, _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

UNIT = '25.00'
INVOICE = '100.00'          # four units at 25.00
CREDIT = '30.00'
STILL_OWED = '70.00'

VOLATILE = {'id', 'transaction_id', 'revision_id', 'line_id', 'document_line_id', 'created_at',
            'audit_event_id', 'number', 'current_revision_id', 'idempotency_key', 'position',
            'updated_at', 'version', 'key_id', 'posting_source_id', 'ordinal',
            'credit_source_key_id', 'source_tax_component_id', 'supersedes_revision_id'}


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _rows(collection):
    return (f'[data-collection-path={collection}] > [data-collection-items] '
            '> [data-collection-item]')


def _name(b, collection, index, field):
    """The generated control name for one cell, read off the row the browser actually built."""
    return b.evaluate(f'''document.querySelectorAll({json.dumps(_rows(collection))})[{index}]
        .querySelector('[name^="c:"][name$=":{field}"]').name''')


def _set(b, name, value):
    b.evaluate(f'''(() => {{const e=document.getElementsByName({json.dumps(name)})[0];
        if (!e) throw new Error('no control named ' + {json.dumps(name)});
        e.value={json.dumps(value)}; e.dispatchEvent(new Event('input',{{bubbles:true}}));
        e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()''')


def _read(b, name):
    return b.evaluate(f'document.getElementsByName({json.dumps(name)})[0].value')


def _add_row(b, collection):
    b.evaluate(f'document.querySelector("[data-collection-path={collection}] > '
               '[data-collection-add]").click()')


def _pick(b, name, label):
    """Choose a record in a reference picker by typing its name and clicking the match."""
    _set(b, 'label:' + name, label)
    picker = f'document.getElementsByName({json.dumps(name)})[0].closest("[data-reference]")'
    b.wait_for(f'{picker}.querySelectorAll("[role=option]").length > 0')
    b.evaluate(f'''(() => {{const options=[...{picker}.querySelectorAll('[role=option]')];
        const option=options.find(e=>e.textContent.includes({json.dumps(label)}));
        if(!option) throw new Error('Missing choice ' + {json.dumps(label)}); option.click();}})()''')
    assert _read(b, name)


def _preview(b):
    """Preview swaps the form in place; the page that comes back is the one to read."""
    b.evaluate('void (window.creditForm = document.querySelector("[data-generated-form]"))')
    _click(b, 'preview')
    b.wait_for('!window.creditForm.isConnected')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:1200]


def _save(b, noun, marker):
    """Saving navigates to the saved document; the form's own window is gone by then."""
    _click(b, 'submit')
    b.wait_for(f'location.pathname.includes("/{noun}/") '
               '&& !document.querySelector("[data-generated-form]") '
               f'&& !!document.querySelector({json.dumps(marker)})')
    return b.evaluate('location.pathname').rsplit('/', 1)[-1]


def _totals(b):
    return b.evaluate('''(() => {const list = document.querySelector(".totals-list");
        if (!list) return null;
        const terms = [...list.querySelectorAll("dt")].map(e => e.textContent.trim());
        const values = [...list.querySelectorAll("dd")].map(e => e.textContent.trim());
        return Object.fromEntries(terms.map((t, i) => [t, values[i]]));})()''')


def _text(b, selector):
    return b.evaluate(f'document.querySelector({json.dumps(selector)})?.innerText')


def _tile(b, title):
    return f'''[...document.querySelectorAll("a.flow-tile")].find(
        a => a.querySelector(".flow-tile-title")?.textContent.trim() === {json.dumps(title)})'''


def _open_from_the_board(b, env, title, destination):
    """Follow a home-board tile by clicking the tile itself, never by typing its address."""
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/')
    b.wait_for('!!document.querySelector("a.flow-tile")')
    tile = _tile(b, title)
    assert b.evaluate(f'!!{tile}'), f'the {title} tile is not a link on the home board'
    assert b.evaluate(f'{tile}.getAttribute("href")') == \
        f'/c/{env.site.company_id}{destination}', title
    b.evaluate(f'{tile}.click()')
    b.wait_for('!!document.querySelector("[data-generated-form]")')


def _books(b, site, tag):
    run = lambda name, payload: _command(b, site, name, payload)
    # A sales document refuses a class while the company has classes switched off, so the
    # company that is going to class a credited line has to be a company that uses classes.
    run('company.update', {'use_classes': True})
    income = run('account.create', {'name': f'{tag} sales', 'type': 'income'})['id']
    exempt = next(row['id'] for row in run('sales-tax-code.list', {})['items']
                  if not row['taxable'])
    return dict(
        run=run, tag=tag, income=income, exempt=exempt,
        expense=run('account.create', {'name': f'{tag} parts', 'type': 'expense'})['id'],
        bank=run('account.create', {'name': f'{tag} checking', 'type': 'bank'})['id'],
        item=run('item.create', {'name': f'{tag} visit', 'type': 'service', 'sales_enabled': True,
                                 'income_account_id': income, 'price': UNIT,
                                 'description': 'Site visit', 'sales_tax_code_id': exempt})['id'],
        customer=run('customer.create', {'name': f'{tag} homeowner'})['id'],
        vendor=run('vendor.create', {'name': f'{tag} supply'})['id'],
        job=run('class.create', {'name': f'{tag} job'})['id'],
        method=next(row['id'] for row in run('payment-method.list', {})['items']
                    if row['kind'] == 'check'))


def _invoice(books, date='2026-03-02'):
    return books['run']('invoice.post', {
        'customer': books['customer'], 'date': date, 'due_date': '2026-04-01',
        'lines': [{'item': books['item'], 'quantity': '4', 'unit_price': UNIT}]})


def _credit(books, amount=CREDIT, date='2026-03-10'):
    return books['run']('credit-memo.post', {
        'customer': books['customer'], 'date': date,
        'lines': [{'item': books['item'], 'quantity': '1', 'unit_price': amount}]})


# ---------------------------------------------------------------- the three windows


def test_the_credit_memo_tile_opens_a_window_that_posts_what_the_command_posts(register_browser):
    """The tile is live: a person follows its own link and a credit memo comes out."""
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Memo')

    _open_from_the_board(b, env, 'Credit memo', '/credit-memo/post')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'New credit memo'

    _pick(b, 'f:customer', 'Memo homeowner')
    _set(b, 'f:date', '2026-03-10')
    _set(b, 'f:memo', 'Overcharged on the March visit')
    _add_row(b, 'lines')
    _pick(b, _name(b, 'lines', 0, 'item'), 'Memo visit')
    _set(b, _name(b, 'lines', 0, 'quantity'), '1')
    _set(b, _name(b, 'lines', 0, 'unit_price'), CREDIT)
    _set(b, _name(b, 'lines', 0, 'description'), 'Goodwill credit')
    _pick(b, _name(b, 'lines', 0, 'class_id'), 'Memo job')

    _preview(b)
    assert _totals(b) == {'Subtotal': CREDIT, 'Tax': '0.00', 'Total': f'{CREDIT} USD'}, _totals(b)

    saved = _save(b, 'credit-memo', '.credit-lines')
    written = books['run']('credit-memo.show', {'credit_memo': saved})

    # The same credit, sent the way an agent sends it.
    sent = books['run']('credit-memo.post', {
        'customer': books['customer'], 'date': '2026-03-10',
        'memo': 'Overcharged on the March visit',
        'lines': [{'item': books['item'], 'quantity': '1', 'unit_price': CREDIT,
                   'description': 'Goodwill credit', 'class_id': books['job']}]})
    assert _scrub(written['revision']['lines']) == _scrub(sent['revision']['lines'])
    assert _scrub(written['revision']['profile']) == _scrub(sent['revision']['profile'])
    assert written['total'] == {'amount': CREDIT, 'currency': 'USD', 'minor_units': 3000}
    assert written['origin'] == 'standalone'
    assert written['source_current']['available']['amount'] == CREDIT

    # The page the save landed on is the credit memo itself, showing what was entered.
    shown = _text(b, '.sales-document')
    for part in ('Memo homeowner', 'Memo visit', 'Goodwill credit', 'Memo job', CREDIT,
                 'Credit for the items named below'):
        assert part in shown, (part, shown[:1500])
    assert f'Still available {CREDIT} USD' in _text(b, '[data-credit-worth]')

    # And it is in the list of credit memos, at what it is still worth.
    b.evaluate('document.querySelector(".document-nav-find").click()')
    b.wait_for('location.pathname.endsWith("/credit-memo") && !!document.querySelector("table td")')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'Credit memos'
    heads = b.evaluate('[...document.querySelectorAll("table th")].map(e => e.textContent.trim())')
    assert heads == ['Number', 'Date', 'Customer', 'Total', 'Available', 'Status'], heads
    entered = b.evaluate(f'''[...document.querySelectorAll("table tr")].slice(1)
        .map(r => [...r.querySelectorAll("td")].map(c => c.innerText.trim()))
        .find(row => row[0] === {json.dumps(written["number"])})''')
    assert entered == [written['number'], '2026-03-10', 'Memo homeowner', f'{CREDIT} USD',
                       f'{CREDIT} USD', 'posted'], entered


def test_the_vendor_credit_tile_opens_a_window_that_posts_what_the_command_posts(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Vend')

    _open_from_the_board(b, env, 'Vendor credit', '/vendor-credit/post')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'New vendor credit'

    _pick(b, 'f:vendor', 'Vend supply')
    _set(b, 'f:date', '2026-03-11')
    _set(b, 'f:supplier_reference', 'CN-42')
    _set(b, 'f:memo', 'Returned two boxes')
    _add_row(b, 'expenses')
    _pick(b, _name(b, 'expenses', 0, 'account'), 'Vend parts')
    _set(b, _name(b, 'expenses', 0, 'amount'), '15.00')
    _set(b, _name(b, 'expenses', 0, 'memo'), 'Two boxes back')
    _pick(b, _name(b, 'expenses', 0, 'customer'), 'Vend homeowner')

    _preview(b)
    assert _totals(b) == {'Credited lines': '15.00 USD', 'Taken off what you owe': '15.00 USD',
                          'Still free to apply': '15.00 USD'}, _totals(b)
    said = b.evaluate('document.querySelector("[data-reconciliation]").textContent')
    assert 'Nothing is settled here' in said, said

    saved = _save(b, 'vendor-credit', '.vendor-credit-lines')
    written = books['run']('vendor-credit.show', {'credit': saved})

    sent = books['run']('vendor-credit.post', {
        'vendor': books['vendor'], 'date': '2026-03-11', 'supplier_reference': 'CN-agent',
        'memo': 'Returned two boxes',
        'expenses': [{'account': books['expense'], 'amount': '15.00', 'memo': 'Two boxes back',
                      'customer': books['customer']}]})
    assert _scrub(written['revision']['expenses']) == _scrub(sent['revision']['expenses'])
    assert written['total'] == {'amount': '15.00', 'currency': 'USD', 'minor_units': 1500}
    assert written['supplier_reference'] == 'CN-42'
    assert written['settlement_current']['status'] == 'unapplied'

    shown = _text(b, '.sales-document')
    for part in ('Vend supply', 'CN-42', 'Vend parts', 'Two boxes back', 'Vend homeowner',
                 '15.00', 'A credit is never due'):
        assert part in shown, (part, shown[:1500])
    assert 'Still free 15.00 USD' in _text(b, '[data-vendor-credit-settlement]')
    assert 'This credit answers no bill yet' in shown

    b.evaluate('document.querySelector(".document-nav-find").click()')
    b.wait_for('location.pathname.endsWith("/vendor-credit") && !!document.querySelector("table td")')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'Vendor credits'
    heads = b.evaluate('[...document.querySelectorAll("table th")].map(e => e.textContent.trim())')
    assert heads == ['Number', 'Date', 'Vendor', 'Total', 'Unapplied', 'Status'], heads


def test_the_refund_tile_opens_a_window_and_a_credit_pays_itself_back_through_it(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Cash')
    credit = _credit(books, '40.00')

    # The tile itself is live and lands on a usable refund window.
    _open_from_the_board(b, env, 'Refund', '/customer-refund/post')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'New customer refund'
    assert b.evaluate('!!document.querySelector("[data-collection-path=sources]")'), \
        'the refund window has no grid of credits to pay back'

    # A refund is written against a credit, so it is opened from one: the window comes back
    # already naming the credit and what it is still worth.
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/credit-memo/{credit["id"]}')
    b.wait_for('!!document.querySelector("[data-credit-refund]")')
    b.evaluate('document.querySelector("[data-credit-refund]").click()')
    b.wait_for('!!document.querySelector("[data-generated-form]") '
               '&& location.pathname.endsWith("/customer-refund/post")')
    note = _text(b, '.workflow-note')
    assert credit['number'] in note and '40.00' in note, note
    assert _read(b, _name(b, 'sources', 0, 'credit_memo')) == credit['id']
    assert _read(b, _name(b, 'sources', 0, 'amount')) == '40.00'
    assert _read(b, 'f:customer') == books['customer']

    _set(b, 'f:date', '2026-03-20')
    _pick(b, 'f:funding_account', 'Cash checking')
    _pick(b, 'f:method', 'Check')
    _set(b, 'f:check_number', '2041')
    _set(b, 'f:reference', 'Refund for March')

    _preview(b)
    assert _totals(b) == {'Credits paid out': '40.00 USD',
                          'Out of Cash checking': '40.00 USD'}, _totals(b)

    saved = _save(b, 'customer-refund', '.refund-sources')
    written = books['run']('customer-refund.show', {'refund': saved})
    assert written['total'] == {'amount': '40.00', 'currency': 'USD', 'minor_units': 4000}
    assert written['check_number'] == '2041' and written['reference'] == 'Refund for March'
    assert [row['credit_memo_id'] for row in written['revision']['profile']['sources']] == \
        [credit['id']]

    shown = _text(b, '.sales-document')
    for part in ('Cash homeowner', 'Cash checking', '2041', 'Refund for March',
                 credit['number'], '40.00'):
        assert part in shown, (part, shown[:1500])

    # The credit it paid out is worth nothing now, and its own page says so.
    assert books['run']('credit-memo.show', {'credit_memo': credit['id']})[
        'source_current']['available_minor_units'] == 0
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/credit-memo/{credit["id"]}')
    b.wait_for('!!document.querySelector("[data-credit-worth]")')
    assert 'Still available 0.00 USD' in _text(b, '[data-credit-worth]')
    assert 'Nothing is left on this credit' in _text(b, '.sales-document')


# ---------------------------------------------------------------- applying and unapplying


def test_a_credit_applied_from_its_own_page_lowers_what_the_invoice_owes(register_browser):
    """The claim the apply surface exists to make, checked on the invoice rather than on itself."""
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Apply')
    invoice = _invoice(books)
    credit = _credit(books)
    base = f'{env.site.base_url}/c/{env.site.company_id}'

    b.navigate(f'{base}/credit-memo/{credit["id"]}')
    b.wait_for('!!document.querySelector("[data-credit-apply]")')
    b.evaluate('document.querySelector("[data-credit-apply]").click()')
    b.wait_for('location.pathname.endsWith("/apply") && !!document.querySelector("#credit-apply-form")')
    assert f'Still available {CREDIT} USD' in _text(b, '[data-credit-available]')

    # The row carries the invoice's own saved version, so nobody is asked to find one.
    row = f'document.querySelector(\'tr[data-invoice="{invoice["id"]}"]\')'
    assert b.evaluate(f'{row}.querySelector(\'input[name^="version:"]\').value') == \
        str(invoice['version'])
    assert b.evaluate(f'{row}.querySelector("[data-invoice-due]").innerText.trim()') == INVOICE
    assert b.evaluate(f'{row}.querySelector(\'input[name^="amount:"]\').value') == CREDIT

    b.evaluate(f'{row}.querySelector(\'input[type=checkbox]\').click()')
    _set(b, 'reason', 'Goodwill applied')
    b.evaluate('document.querySelector("#credit-apply-save").click()')
    b.wait_for(f'location.pathname.endsWith("/credit-memo/{credit["id"]}")')
    b.wait_for('!!document.querySelector("[data-credit-worth]")')
    assert 'Still available 0.00 USD' in _text(b, '[data-credit-worth]')

    # The invoice owes what it owed less exactly what was applied.
    b.navigate(f'{base}/invoice/{invoice["id"]}')
    b.wait_for('!!document.querySelector(".sales-document")')
    settlement = _text(b, '[aria-label="Current invoice settlement"]')
    assert f'Applied {CREDIT}' in settlement and f'Due {STILL_OWED}' in settlement, settlement
    assert books['run']('invoice.show', {'invoice': invoice['id']})[
        'settlement_current']['due_minor_units'] == 7000

    # And taking it back off puts the whole invoice back.
    b.navigate(f'{base}/credit-memo/{credit["id"]}/apply')
    b.wait_for('!!document.querySelector("#credit-unapply-form")')
    standing = b.evaluate('document.querySelector("#credit-unapply-form tbody tr")'
                          '.getAttribute("data-application")')
    assert standing
    b.evaluate('document.querySelector(\'#credit-unapply-form input[type=checkbox]\').click()')
    b.evaluate('document.querySelector("#credit-unapply-save").click()')
    b.wait_for(f'location.pathname.endsWith("/credit-memo/{credit["id"]}")')
    b.wait_for('!!document.querySelector("[data-credit-worth]")')
    assert f'Still available {CREDIT} USD' in _text(b, '[data-credit-worth]')
    assert books['run']('invoice.show', {'invoice': invoice['id']})[
        'settlement_current']['due_minor_units'] == 10000


def test_a_return_opens_from_the_invoice_and_is_priced_by_that_invoice(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Return')
    invoice = _invoice(books)
    base = f'{env.site.base_url}/c/{env.site.company_id}'

    b.navigate(f'{base}/invoice/{invoice["id"]}')
    b.wait_for('!!document.querySelector("[data-credit-this-invoice]")')
    b.evaluate('document.querySelector("[data-credit-this-invoice]").click()')
    b.wait_for('location.pathname.endsWith("/credit-memo/post") '
               '&& !!document.querySelector("[data-generated-form]")')

    line_id = invoice['revision']['lines'][0]['line_id']
    assert _read(b, _name(b, 'lines', 0, 'source_invoice')) == invoice['id']
    assert _read(b, _name(b, 'lines', 0, 'source_line')) == line_id
    assert _read(b, _name(b, 'lines', 0, 'quantity')) == '4'
    assert 'Returning lines of invoice' in _text(b, '.workflow-note')
    # The window seeds what the invoice says and nothing the invoice cannot say: when the
    # units came back is the person's to enter, so the date opens empty.
    assert _read(b, 'f:date') == ''

    # One of the four units came back; nothing here prices it.
    _set(b, 'f:date', '2026-04-05')
    _set(b, _name(b, 'lines', 0, 'quantity'), '1')
    _preview(b)
    assert _totals(b) == {'Subtotal': UNIT, 'Tax': '0.00', 'Total': f'{UNIT} USD'}, _totals(b)

    saved = _save(b, 'credit-memo', '.credit-lines')
    written = books['run']('credit-memo.show', {'credit_memo': saved})
    assert written['origin'] == 'return'
    assert written['total'] == {'amount': UNIT, 'currency': 'USD', 'minor_units': 2500}
    assert written['revision']['lines'][0]['source_line_id'] == line_id
    shown = _text(b, '.sales-document')
    assert 'Units returned against a posted invoice' in shown, shown[:900]
    assert b.evaluate('document.querySelector("[data-credit-source]").getAttribute("href")') \
        == f'/c/{env.site.company_id}/invoice/{invoice["id"]}'


# ---------------------------------------------------------------- the phone


def _blocks(b, collection):
    """Every element of every line block fits inside its own box at this width."""
    return b.evaluate(f'''[...document.querySelectorAll({json.dumps(_rows(collection))} + " .line-cell")]
        .every(e => e.scrollWidth <= e.clientWidth + 1
                    && e.getBoundingClientRect().right <= innerWidth + 1)''')


def _grid_fits(b):
    return b.evaluate('''(() => {const g = document.querySelector(".line-grid");
        return g.scrollWidth <= g.clientWidth + 1;})()''')


def _table_fits(b, selector):
    return b.evaluate(f'''(() => {{const t = document.querySelector({json.dumps(selector)});
        const w = t.closest(".table-wrap");
        return {{scroll: t.scrollWidth, client: t.clientWidth,
                wrap_scroll: w.scrollWidth, wrap_client: w.clientWidth,
                cells: [...t.querySelectorAll("tbody td")]
                    .every(e => e.scrollWidth <= e.clientWidth + 1
                                && e.getBoundingClientRect().right <= innerWidth + 1)}};}})()''')


def test_the_credit_windows_have_nothing_to_scroll_sideways_at_phone_width(register_browser):
    """Below 700px a credit line stops being a row of a wide table and becomes its own block."""
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    books = _books(b, env.site, 'Phone')
    base = f'{env.site.base_url}/c/{env.site.company_id}'

    # The credit memo window, with a line entered.
    b.navigate(f'{base}/credit-memo/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _pick(b, 'f:customer', 'Phone homeowner')
    _set(b, 'f:date', '2026-04-02')
    _add_row(b, 'lines')
    _pick(b, _name(b, 'lines', 0, 'item'), 'Phone visit')
    _set(b, _name(b, 'lines', 0, 'quantity'), '1')
    _set(b, _name(b, 'lines', 0, 'unit_price'), CREDIT)
    _contained(b, 390)
    assert _grid_fits(b), b.evaluate('''(() => {const g=document.querySelector(".line-grid");
        return {scroll:g.scrollWidth, client:g.clientWidth};})()''')
    assert b.evaluate('getComputedStyle(document.querySelector(".line-head")).display') == 'none'
    assert b.evaluate('getComputedStyle(document.querySelector(".line-cell-label")).display') \
        == 'block'
    assert _blocks(b, 'lines')
    # The block's own reading order: what is credited and how it is identified read across
    # the block; the numbers pair up beside each other.
    boxes = b.evaluate(f'''(() => {{const row = document.querySelectorAll({json.dumps(_rows("lines"))})[0];
        const cell = name => row.querySelector(`[data-line-column="${{name}}"]`).getBoundingClientRect();
        const out = {{}};
        for (const name of ['item', 'source_invoice', 'source_line', 'description', 'quantity',
                            'unit', 'unit_price', 'class_id', '@amount'])
            out[name] = {{top: Math.round(cell(name).top), width: Math.round(cell(name).width)}};
        return out;}})()''')
    assert boxes['source_invoice']['width'] == boxes['item']['width'], boxes
    assert boxes['source_line']['top'] > boxes['source_invoice']['top'], boxes
    assert boxes['description']['top'] > boxes['source_line']['top'], boxes
    assert boxes['quantity']['top'] == boxes['unit']['top'], boxes
    assert boxes['quantity']['width'] < boxes['item']['width'], boxes
    assert boxes['@amount']['width'] == boxes['item']['width'], boxes
    _preview(b)
    _contained(b, 390)
    assert _grid_fits(b)
    saved_credit = _save(b, 'credit-memo', '.credit-lines')
    _contained(b, 390)
    fits = _table_fits(b, '.credit-lines')
    assert fits['scroll'] <= fits['client'] + 1 and fits['cells'], fits
    assert fits['wrap_scroll'] == fits['wrap_client'], fits

    # The vendor credit window and its saved document.
    b.navigate(f'{base}/vendor-credit/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _pick(b, 'f:vendor', 'Phone supply')
    _set(b, 'f:date', '2026-04-02')
    _add_row(b, 'expenses')
    _pick(b, _name(b, 'expenses', 0, 'account'), 'Phone parts')
    _set(b, _name(b, 'expenses', 0, 'amount'), '12.00')
    _contained(b, 390)
    assert _grid_fits(b) and _blocks(b, 'expenses')
    _preview(b)
    _contained(b, 390)
    _save(b, 'vendor-credit', '.vendor-credit-lines')
    _contained(b, 390)
    fits = _table_fits(b, '.vendor-credit-lines')
    assert fits['scroll'] <= fits['client'] + 1 and fits['cells'], fits

    # The refund window, opened from the credit it pays back, and its saved document.
    b.navigate(f'{base}/customer-refund/post?credit_memo={saved_credit}')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _pick(b, 'f:funding_account', 'Phone checking')
    _pick(b, 'f:method', 'Check')
    _set(b, 'f:date', '2026-04-03')
    _contained(b, 390)
    assert _grid_fits(b) and _blocks(b, 'sources')
    _preview(b)
    _contained(b, 390)
    _save(b, 'customer-refund', '.refund-sources')
    _contained(b, 390)
    fits = _table_fits(b, '.refund-sources')
    assert fits['scroll'] <= fits['client'] + 1 and fits['cells'], fits


def test_the_apply_page_fits_a_phone(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    books = _books(b, env.site, 'Pocket')
    invoice = _invoice(books)
    credit = _credit(books)
    base = f'{env.site.base_url}/c/{env.site.company_id}'

    b.navigate(f'{base}/credit-memo/{credit["id"]}/apply')
    b.wait_for('!!document.querySelector("#credit-apply-form")')
    _contained(b, 390)
    fits = _table_fits(b, '.credit-apply-open')
    assert fits['scroll'] <= fits['client'] + 1 and fits['cells'], fits
    assert fits['wrap_scroll'] == fits['wrap_client'], fits
    assert b.evaluate('''[...document.querySelectorAll("#credit-apply-form input, "
        + "#credit-apply-form label")].every(e => e.scrollWidth <= e.clientWidth + 1
            && e.getBoundingClientRect().right <= innerWidth + 1)''')

    # Applying works from the phone too, and the standing row then fits as well.
    row = f'document.querySelector(\'tr[data-invoice="{invoice["id"]}"]\')'
    b.evaluate(f'{row}.querySelector(\'input[type=checkbox]\').click()')
    b.evaluate('document.querySelector("#credit-apply-save").click()')
    b.wait_for(f'location.pathname.endsWith("/credit-memo/{credit["id"]}")')
    b.wait_for('!!document.querySelector("[data-credit-worth]")')
    _contained(b, 390)
    b.navigate(f'{base}/credit-memo/{credit["id"]}/apply')
    b.wait_for('!!document.querySelector("#credit-unapply-form")')
    _contained(b, 390)
    fits = _table_fits(b, '.credit-apply-standing')
    assert fits['scroll'] <= fits['client'] + 1 and fits['cells'], fits
    assert books['run']('invoice.show', {'invoice': invoice['id']})[
        'settlement_current']['due_minor_units'] == 7000
