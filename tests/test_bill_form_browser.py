"""The bill window, list and detail page, driven in a real browser.

Five things are checked here that nothing else checks. That the Enter bill tile on the home
board is live in the only sense the availability contract accepts — a person follows the
tile's own link and enters a bill through the page it lands on. That the window posts the
same command an agent sends over MCP, field for field. That the Items tab is a way of
entering a bill rather than a control that exists: a person switches to it, buys something
and the saved bill says what they bought. That a correction made through the browser leaves
both grids exactly as they were captured — the regression that arrives the moment the
correction form baselines the Items grid as well as the Expenses one, because each grid is
replaced as a whole and a baseline that does not match resubmits it. And that at 390px
neither grid nor the saved bill has anything to scroll sideways: the element's own
scrollWidth against its own clientWidth, not merely a page that does not move.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _click, _contained, _fill, _value

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

FIRST = '184.60'
SECOND = '100.00'
TOTAL = '284.60'

ROWS = '[data-collection-path=expenses] > [data-collection-items] > [data-collection-item]'
ITEM_ROWS = '[data-collection-path=items] > [data-collection-items] > [data-collection-item]'

# One item line, bought twelve at a time. Written out so a reader can add it up.
VALVE_COST = '12.35'
VALVES = '148.20'        # 12 x 12.35
BOTH_TABS = '332.80'     # 184.60 on the Expenses tab + 148.20 on the Items tab

VOLATILE = {'id', 'transaction_id', 'revision_id', 'line_id', 'document_line_id', 'created_at',
            'audit_event_id', 'number', 'current_revision_id', 'idempotency_key', 'position',
            'updated_at', 'version', 'supplier_reference', 'supplier_reference_key',
            'key_id', 'posting_source_id', 'ordinal'}


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _cell(b, rows, index, field):
    return b.evaluate(f'''document.querySelectorAll({json.dumps(rows)})[{index}]
        .querySelector('[name^="c:"][name$=":{field}"]').name''')


def _line(b, index, field):
    return _cell(b, ROWS, index, field)


def _item(b, index, field):
    return _cell(b, ITEM_ROWS, index, field)


def _count(b, rows):
    return b.evaluate(f'document.querySelectorAll({json.dumps(rows)}).length')


def _add_line(b, path='expenses'):
    b.evaluate(f'document.querySelector("[data-collection-path={path}] > '
               '[data-collection-add]").click()')


def _tabs(b):
    """The tab strip as a person sees it: its labels, and which one is showing."""
    return b.evaluate('''(() => {const tabs = [...document.querySelectorAll("[data-line-tab]")];
        return {labels: tabs.map(t => t.textContent.trim()),
                selected: tabs.filter(t => t.getAttribute("aria-selected") === "true")
                              .map(t => t.dataset.lineTab),
                shown: [...document.querySelectorAll("[data-line-panel]")]
                       .filter(p => !p.hidden).map(p => p.dataset.linePanel)};})()''')


def _open_tab(b, key):
    b.evaluate(f'document.querySelector("[data-line-tab={key}]").click()')
    b.wait_for(f'document.querySelector("[data-line-panel={key}]").hidden === false')


def _pick(b, name, label):
    """Choose a record in a reference picker by typing its name and clicking the match."""
    _fill(b, 'label:' + name, label)
    picker = f'document.getElementsByName({json.dumps(name)})[0].closest("[data-reference]")'
    b.wait_for(f'{picker}.querySelectorAll("[role=option]").length > 0')
    b.evaluate(f'''(() => {{const options=[...{picker}.querySelectorAll('[role=option]')];
        const option=options.find(e=>e.textContent.includes({json.dumps(label)}));
        if(!option) throw new Error('Missing choice'); option.click();}})()''')
    assert _value(b, name)


def _act(b, action):
    """Preview swaps the form in place; the page that comes back is the one to read."""
    b.evaluate('void (window.billForm = document.querySelector("[data-generated-form]"))')
    _click(b, action)
    b.wait_for('!window.billForm.isConnected')


def _save(b):
    """Saving navigates to the saved bill; the form's own window is gone by then.

    The saved page is the signal, not one of its tables: a bill bought wholly on the Items
    tab has no Expenses table to wait for.
    """
    _click(b, 'submit')
    b.wait_for('!document.querySelector("[data-generated-form]") '
               '&& !!document.querySelector(".sales-document")')
    return b.evaluate('location.pathname').rsplit('/', 1)[-1]


def _totals(b):
    return b.evaluate('''(() => {const list = document.querySelector(".totals-list");
        if (!list) return null;
        const terms = [...list.querySelectorAll("dt")].map(e => e.textContent.trim());
        const values = [...list.querySelectorAll("dd")].map(e => e.textContent.trim());
        return Object.fromEntries(terms.map((t, i) => [t, values[i]]));})()''')


def _text(b, selector):
    return b.evaluate(f'document.querySelector({json.dumps(selector)})?.innerText')


def _books(b, site, tag, *, due_days=None):
    run = lambda name, payload: _command(b, site, name, payload)
    terms = None
    if due_days is not None:
        terms = run('term.create', {'name': f'{tag} net {due_days}', 'kind': 'standard',
                                    'due_days': due_days})['id']
    return dict(
        first=run('account.create', {'name': f'{tag} parts', 'type': 'expense'})['id'],
        second=run('account.create', {'name': f'{tag} fuel', 'type': 'expense'})['id'],
        vendor=run('vendor.create', {'name': f'{tag} supply',
                                     **({'terms_id': terms} if terms else {})})['id'],
        customer=run('customer.create', {'name': f'{tag} homeowner'})['id'],
        job=run('class.create', {'name': f'{tag} job'})['id'],
        terms=terms, run=run, tag=tag)


def _enter_bill(b, env, books, *, amounts=(FIRST, SECOND), reference='SUP-77'):
    """Fill the open bill window with two expense lines, the second billable to a customer."""
    _pick(b, 'f:vendor', f'{books["tag"]} supply')
    _fill(b, 'f:date', '2026-03-04')
    _fill(b, 'f:supplier_reference', reference)
    _fill(b, 'f:memo', 'March supplies')
    for index, (name, value) in enumerate(zip((f'{books["tag"]} parts', f'{books["tag"]} fuel'),
                                              amounts)):
        _add_line(b)
        _pick(b, _line(b, index, 'account'), name)
        _fill(b, _line(b, index, 'amount'), value)
        _fill(b, _line(b, index, 'memo'), 'Line ' + str(index + 1))
    _pick(b, _line(b, 0, 'class_id'), f'{books["tag"]} job')
    _pick(b, _line(b, 1, 'customer'), f'{books["tag"]} homeowner')
    _fill(b, _line(b, 1, 'billable'), 'true')


def test_the_enter_bill_tile_opens_a_window_that_posts_what_the_command_posts(register_browser):
    """The tile is live: a person follows its own link and a bill comes out the other end."""
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Parity', due_days=30)

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/')
    b.wait_for('!!document.querySelector("a.flow-tile")')
    tile = '''[...document.querySelectorAll("a.flow-tile")].find(
        a => a.querySelector(".flow-tile-title")?.textContent.trim() === "Enter bill")'''
    assert b.evaluate(f'!!{tile}'), 'the Enter bill tile is not a link on the home board'
    assert b.evaluate(f'{tile}.getAttribute("href")') == \
        f'/c/{env.site.company_id}/bill/post'
    b.evaluate(f'{tile}.click()')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'New bill'

    _enter_bill(b, env, books)
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    assert _totals(b) == {'Expenses': f'{TOTAL} USD', 'Amount due': f'{TOTAL} USD'}
    # The vendor's terms derived the due date, and the footer says which date and which rule.
    said = b.evaluate('document.querySelector("[data-reconciliation]").textContent')
    assert 'Due 2026-04-03' in said and 'Parity net 30' in said, said

    saved = _save(b)
    written = books['run']('bill.show', {'bill': saved})

    # The same bill, sent the way an agent sends it.
    sent = books['run']('bill.post', {
        'vendor': books['vendor'], 'date': '2026-03-04', 'supplier_reference': 'SUP-agent',
        'memo': 'March supplies',
        'expenses': [{'account': books['first'], 'amount': FIRST, 'memo': 'Line 1',
                      'class_id': books['job']},
                     {'account': books['second'], 'amount': SECOND, 'memo': 'Line 2',
                      'customer': books['customer'], 'billable': True}]})
    assert _scrub(written['revision'])['expenses'] == _scrub(sent['revision'])['expenses']
    assert _scrub(written['revision'])['profile'] == _scrub(sent['revision'])['profile']
    assert written['total'] == {'amount': TOTAL, 'currency': 'USD', 'minor_units': 28460}
    assert written['due_date'] == '2026-04-03'
    assert written['settlement_current']['status'] == 'unpaid'
    assert written['supplier_reference'] == 'SUP-77'

    # The page the save landed on is the bill itself, showing what was entered.
    detail = _text(b, '.sales-document')
    for part in ('Parity supply', 'SUP-77', '2026-04-03', FIRST, SECOND, TOTAL,
                 'Parity parts', 'Parity fuel', 'Parity homeowner', 'Parity job'):
        assert part in detail, (part, detail[:1200])
    billable = b.evaluate('''[...document.querySelectorAll(".bill-lines tbody tr")]
        .map(r => r.querySelector('[data-label="Billable"]').innerText.trim())''')
    assert billable == ['No', 'Yes'], billable
    assert 'Unpaid' in _text(b, '[data-bill-settlement]')

    # And the bill entered through the window is in the list of bills, at its own total.
    # Wait for the list page itself, not merely for a table cell: the detail page being
    # left has its own line table, so `table td` is already true before the navigation
    # starts and the read races it. This is a full page load, so the path is the signal.
    b.evaluate('document.querySelector(".document-nav-find").click()')
    b.wait_for('location.pathname.endsWith("/bill") && !!document.querySelector("table td")')
    entered = b.evaluate(f'''[...document.querySelectorAll("table tr")].slice(1)
        .map(r => [...r.querySelectorAll("td")].map(c => c.innerText.trim()))
        .find(row => row[0] === {json.dumps(written["number"])})''')
    assert entered == [written['number'], '2026-03-04', 'Parity supply', '2026-04-03',
                       f'{TOTAL} USD', 'posted'], entered


def test_the_bill_list_shows_the_bills_and_the_arrows_step_between_them(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Listing')
    run = books['run']
    first = run('bill.post', {'vendor': books['vendor'], 'date': '2026-02-01',
                              'supplier_reference': 'LIST-1',
                              'expenses': [{'account': books['first'], 'amount': FIRST}]})
    second = run('bill.post', {'vendor': books['vendor'], 'date': '2026-02-09',
                               'supplier_reference': 'LIST-2',
                               'expenses': [{'account': books['second'], 'amount': SECOND}]})

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill')
    b.wait_for('!!document.querySelector("table")')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'Bills'
    heads = b.evaluate('[...document.querySelectorAll("table th")].map(e => e.textContent.trim())')
    assert heads == ['Number', 'Date', 'Vendor', 'Due date', 'Total', 'Status'], heads
    rows = b.evaluate('''[...document.querySelectorAll("table tr")].slice(1)
        .map(r => [...r.querySelectorAll("td")].map(c => c.innerText.trim()))''')
    assert [row[0] for row in rows] == [second['number'], first['number']], rows
    assert rows[0] == [second['number'], '2026-02-09', 'Listing supply', '2026-02-09',
                       f'{SECOND} USD', 'posted'], rows[0]

    # The number is the way into the bill, the way an invoice number is.
    b.evaluate(f'''document.querySelector('a[href$="/bill/{second["id"]}"]').click()''')
    b.wait_for(f'location.pathname.endsWith("/bill/{second["id"]}")')
    b.wait_for('!!document.querySelector(".document-nav")')
    assert 'LIST-2' in _text(b, '.sales-document')
    nav = _text(b, '.document-nav')
    assert 'Find a bill' in nav and 'This is the latest bill' in nav, nav
    assert '2 of 2' in nav, nav

    # The arrow walks to the bill written before it, in the list's own order.
    b.evaluate('document.querySelector(".document-step[rel=prev]").click()')
    b.wait_for(f'location.pathname.endsWith("/bill/{first["id"]}") '
               '&& !!document.querySelector(".bill-lines")')
    assert 'LIST-1' in _text(b, '.sales-document')
    assert 'This is the earliest bill' in _text(b, '.document-nav')

    # The new-bill window offers the last few bills, as every other document window does.
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill/post')
    b.wait_for('!!document.querySelector(".document-recent-list")')
    recent = _text(b, '.document-recent')
    assert 'Recent bills' in recent, recent
    assert second['number'] in recent and first['number'] in recent, recent
    assert 'Listing supply' in recent, recent


def test_a_correction_replaces_the_lines_and_the_saved_bill_shows_the_new_total(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Correct')
    posted = books['run']('bill.post', {
        'vendor': books['vendor'], 'date': '2026-04-01', 'supplier_reference': 'FIX-1',
        'expenses': [{'account': books['first'], 'amount': FIRST, 'memo': 'As invoiced'}]})

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill/{posted["id"]}/update')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert 'correction' in b.evaluate('document.querySelector("h1").textContent')
    # The window opens on what the revision captured, not on an empty form.
    assert _value(b, 'f:supplier_reference') == 'FIX-1'
    assert _value(b, _line(b, 0, 'amount')) == FIRST
    assert _value(b, _line(b, 0, 'memo')) == 'As invoiced'

    _fill(b, _line(b, 0, 'amount'), '200.00')
    _fill(b, _line(b, 0, 'memo'), 'Reissued at the corrected price')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    assert _totals(b) == {'Expenses': '200.00 USD', 'Amount due': '200.00 USD'}
    _save(b)

    after = books['run']('bill.show', {'bill': posted['id']})
    assert after['total']['amount'] == '200.00'
    assert after['revision']['revision_number'] == 2
    assert [line['memo'] for line in after['revision']['expenses']] == \
        ['Reissued at the corrected price']
    shown = _text(b, '.sales-document')
    assert '200.00' in shown and 'Revision 2' in shown, shown[:800]
    # The earlier revision is still readable from the bill it was corrected into.
    b.evaluate('''[...document.querySelectorAll("a")]
        .find(a => a.textContent.trim() === "Previous revision").click()''')
    b.wait_for('location.search.includes("revision_number=1") '
               '&& !!document.querySelector(".bill-lines")')
    assert 'As invoiced' in _text(b, '.sales-document')

    # A correction that touches only the header keeps the saved lines exactly as captured.
    # The grid equals its own baseline, so the form does not submit it at all, and a renamed
    # account is not silently re-captured on to the corrected revision.
    books['run']('account.update', {'account': books['first'], 'name': 'Correct renamed'})
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill/{posted["id"]}/update')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _fill(b, 'f:memo', 'Header only')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    _save(b)
    final = books['run']('bill.show', {'bill': posted['id']})
    assert final['revision']['revision_number'] == 3
    assert final['revision']['memo'] == 'Header only'
    assert final['revision']['expenses'][0]['line_snapshot']['account']['name'] \
        == 'Correct parts', final['revision']['expenses'][0]['line_snapshot']['account']


def test_the_expenses_grid_has_nothing_to_scroll_sideways_at_phone_width(register_browser):
    """Below 700px a bill line stops being a row of a wide table and becomes its own block."""
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    books = _books(b, env.site, 'Phone')

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _enter_bill(b, env, books)

    _contained(b, 390)
    grid = b.evaluate('''(() => {const g = document.querySelector(".line-grid");
        return {scroll: g.scrollWidth, client: g.clientWidth,
                overflow: getComputedStyle(g).overflowX};})()''')
    assert grid['scroll'] == grid['client'], grid
    # Each block carries its own visible field names, because the column heads are gone.
    assert b.evaluate('getComputedStyle(document.querySelector(".line-head")).display') == 'none'
    assert b.evaluate('getComputedStyle(document.querySelector(".line-cell-label")).display') \
        == 'block'
    assert b.evaluate(f'''[...document.querySelectorAll({json.dumps(ROWS)} + " .line-cell")]
        .every(e => e.scrollWidth <= e.clientWidth + 1
                    && e.getBoundingClientRect().right <= innerWidth + 1)''')
    # The block's own reading order: what the money went to, who it was for and what it was
    # for each run the whole width; the amount and the class are the pair beside each other.
    boxes = b.evaluate(f'''(() => {{const row = document.querySelectorAll({json.dumps(ROWS)})[1];
        const cell = name => row.querySelector(`[data-line-column="${{name}}"]`).getBoundingClientRect();
        const out = {{}};
        for (const name of ['account', 'amount', 'class_id', 'customer', 'billable', 'memo'])
            out[name] = {{top: Math.round(cell(name).top), width: Math.round(cell(name).width)}};
        return out;}})()''')
    assert boxes['amount']['top'] == boxes['class_id']['top'], boxes
    assert boxes['account']['width'] > boxes['amount']['width'] * 1.8, boxes
    assert boxes['customer']['width'] == boxes['account']['width'], boxes
    assert boxes['memo']['width'] == boxes['account']['width'], boxes
    assert boxes['customer']['top'] > boxes['amount']['top'], boxes
    assert boxes['billable']['top'] > boxes['customer']['top'], boxes
    assert boxes['memo']['top'] > boxes['billable']['top'], boxes

    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    _contained(b, 390)
    assert b.evaluate('''(() => {const g = document.querySelector(".line-grid");
        return g.scrollWidth === g.clientWidth;})()''')
    _save(b)
    _contained(b, 390)


def test_a_saved_bill_reads_as_line_cards_at_phone_width(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    books = _books(b, env.site, 'Card')
    posted = books['run']('bill.post', {
        'vendor': books['vendor'], 'date': '2026-05-02', 'supplier_reference': 'PHONE-1',
        'expenses': [{'account': books['first'], 'amount': FIRST,
                      'memo': 'A memo long enough to wrap onto a second line',
                      'customer': books['customer'], 'billable': True},
                     {'account': books['second'], 'amount': SECOND, 'memo': 'Line 2'}]})

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill/{posted["id"]}')
    b.wait_for('!!document.querySelector(".bill-lines")')
    _contained(b, 390)
    table = b.evaluate('''(() => {const t = document.querySelector(".bill-lines");
        return {scroll: t.scrollWidth, client: t.clientWidth,
                head: getComputedStyle(t.querySelector("thead")).position};})()''')
    assert table['scroll'] <= table['client'] + 1, table
    wrap = b.evaluate('''(() => {const w = document.querySelector(".bill-lines").closest(".table-wrap");
        return {scroll: w.scrollWidth, client: w.clientWidth};})()''')
    assert wrap['scroll'] == wrap['client'], wrap
    # Every cell names itself, because the column heads are not on screen.
    assert b.evaluate('''[...document.querySelectorAll(".bill-lines tbody td")]
        .every(td => getComputedStyle(td.querySelector(".sales-cell-label")).display === "block")''')
    assert b.evaluate('''[...document.querySelectorAll(".bill-lines tbody td")]
        .every(td => td.getBoundingClientRect().right <= innerWidth + 1)''')
    # One card per line: what it was spent on, what it was for and who it is for run the
    # whole card; the amount, Billable and the class are the short values beside each other.
    boxes = b.evaluate('''(() => {const row = document.querySelector(".bill-lines tbody tr");
        const cell = label => row.querySelector(`[data-label="${label}"]`).getBoundingClientRect();
        const out = {};
        for (const label of ['Account', 'Amount', 'Memo', 'Customer:Job', 'Billable', 'Class'])
            out[label] = {top: Math.round(cell(label).top), width: Math.round(cell(label).width)};
        return out;})()''')
    assert boxes['Account']['width'] > boxes['Amount']['width'] * 1.8, boxes
    assert boxes['Memo']['width'] == boxes['Account']['width'], boxes
    assert boxes['Customer:Job']['width'] == boxes['Account']['width'], boxes
    assert boxes['Billable']['top'] == boxes['Class']['top'], boxes
    assert boxes['Billable']['top'] > boxes['Customer:Job']['top'], boxes
    shown = _text(b, '.sales-document')
    assert 'PHONE-1' in shown and TOTAL in shown and 'Card homeowner' in shown, shown[:900]


def _bought_item(books):
    """A non-inventory part with a purchase side, and the account it puts its cost in.

    The account is the item's, not the row's: an item line names no account, which is the
    whole difference between the two grids.
    """
    account = books['run']('account.create',
                           {'name': f'{books["tag"]} valves', 'type': 'expense'})['id']
    item = books['run']('item.create', {
        'name': f'{books["tag"]} valve', 'type': 'non_inventory_part',
        'sales_enabled': False, 'purchase_enabled': True,
        'purchase_description': '1in brass ball valve', 'cost': VALVE_COST,
        'expense_account_id': account})['id']
    return item, account


def test_the_items_tab_buys_something_and_the_saved_bill_says_what_was_bought(register_browser):
    """The Items tab is a way in, not a control that exists.

    A person opens the bill window, switches to Items, buys twelve valves and saves; the bill
    that comes out says what was bought, out of the account named on the item, and matches
    the same purchase sent as an agent sends it.
    """
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Bought')
    item, account = _bought_item(books)

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    b.wait_for('!!document.querySelector("[data-line-tab]")')

    # Two tabs over one band, and a bill opens on the accounts a person types.
    assert _tabs(b) == {'labels': ['Expenses', 'Items'], 'selected': ['expenses'],
                        'shown': ['expenses']}, _tabs(b)
    _open_tab(b, 'items')
    assert _tabs(b) == {'labels': ['Expenses', 'Items'], 'selected': ['items'],
                        'shown': ['items']}, _tabs(b)

    _pick(b, 'f:vendor', 'Bought supply')
    _fill(b, 'f:date', '2026-06-08')
    _fill(b, 'f:supplier_reference', 'ITEM-1')
    _add_line(b, 'items')
    _pick(b, _item(b, 0, 'item'), 'Bought valve')
    _fill(b, _item(b, 0, 'quantity'), '12')
    _fill(b, _item(b, 0, 'unit_cost'), VALVE_COST)
    _pick(b, _item(b, 0, 'customer'), 'Bought homeowner')
    _fill(b, _item(b, 0, 'billable'), 'true')
    _pick(b, _item(b, 0, 'class_id'), 'Bought job')

    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    # The footer names the grid the lines are on. A bill bought wholly on the Items tab is
    # not told that it spent nothing on expenses.
    assert _totals(b) == {'Items': f'{VALVES} USD', 'Amount due': f'{VALVES} USD'}
    # A preview swaps the whole window in place; it comes back on the tab being worked on.
    assert _tabs(b)['shown'] == ['items'], _tabs(b)

    saved = _save(b)
    written = books['run']('bill.show', {'bill': saved})
    assert written['expense_total']['minor_units'] == 0
    assert written['item_total'] == {'amount': VALVES, 'currency': 'USD', 'minor_units': 14820}
    assert written['total']['amount'] == VALVES
    assert written['revision']['expenses'] == []
    line = written['revision']['items'][0]
    assert len(written['revision']['items']) == 1
    assert line['item_id'] == item
    # The account came off the item; nothing on the row named one.
    assert line['account_id'] == account
    assert line['quantity'] == '12'
    assert line['unit_cost']['amount'] == VALVE_COST
    assert line['amount']['amount'] == VALVES
    assert line['billable'] is True
    assert line['description'] == '1in brass ball valve'

    # The same purchase, sent the way an agent sends it.
    sent = books['run']('bill.post', {
        'vendor': books['vendor'], 'date': '2026-06-08', 'supplier_reference': 'ITEM-agent',
        'items': [{'item': item, 'quantity': '12', 'unit_cost': VALVE_COST,
                   'customer': books['customer'], 'billable': True, 'class_id': books['job']}]})
    assert _scrub(written['revision'])['items'] == _scrub(sent['revision'])['items']

    # The page the save landed on is the bill itself, showing what was bought.
    detail = _text(b, '.sales-document')
    for part in ('Bought valve', '1in brass ball valve', VALVE_COST, VALVES,
                 'Bought homeowner', 'Bought job'):
        assert part in detail, (part, detail[:1200])
    assert b.evaluate('!!document.querySelector(".bill-item-lines")')
    # Nothing was entered on the Expenses tab, so there is no empty Expenses table.
    assert b.evaluate('!document.querySelector(".bill-lines")')


def test_a_browser_correction_leaves_both_grids_exactly_as_they_were_captured(register_browser):
    """The regression the Items baseline can introduce, and the one it must not.

    Each grid is replaced as a whole: supplying one replaces it and leaves the other exactly
    as captured. The correction form submits a grid only when it differs from the baseline it
    opened with, so a baseline that does not match what the window renders would resubmit
    that grid on every correction and silently re-resolve it against today's records. Both
    records are renamed before any correction, so a grid that was resubmitted says so.
    """
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Keep')
    item, account = _bought_item(books)
    posted = books['run']('bill.post', {
        'vendor': books['vendor'], 'date': '2026-07-01', 'supplier_reference': 'KEEP-1',
        'expenses': [{'account': books['first'], 'amount': FIRST, 'memo': 'Parts'},
                     {'account': books['second'], 'amount': SECOND, 'memo': 'Fuel'}],
        'items': [{'item': item, 'quantity': '12', 'unit_cost': VALVE_COST},
                  {'item': item, 'quantity': '1', 'amount': '40.00',
                   'description': 'One spare'}]})

    def grids():
        record = books['run']('bill.show', {'bill': posted['id']})
        return record['revision']['revision_number'], {
            'expenses': [(line['line_id'], line['amount']['amount'], line['memo'],
                          line['line_snapshot']['account']['full_name'])
                         for line in record['revision']['expenses']],
            'items': [(line['line_id'], line['amount']['amount'], line['description'],
                       line['line_snapshot']['item']['label'])
                      for line in record['revision']['items']]}

    revision, before = grids()
    assert revision == 1
    assert len(before['expenses']) == 2 and len(before['items']) == 2

    # Rename both records. A grid that is silently resubmitted recaptures the new name.
    books['run']('account.update', {'account': books['first'], 'name': 'Keep renamed account'})
    books['run']('item.update', {'item': item, 'name': 'Keep renamed item'})

    def correct():
        b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill/{posted["id"]}/update')
        b.wait_for('!!document.querySelector("[data-generated-form]")')

    # The window opens on both grids, as they were captured, in the order they were written.
    correct()
    assert _count(b, ROWS) == 2 and _count(b, ITEM_ROWS) == 2
    assert _value(b, _line(b, 0, 'amount')) == FIRST
    assert _value(b, _item(b, 0, 'unit_cost')) == VALVE_COST
    # The line entered as a whole-line amount reopens on the amount, not on a cost nobody gave.
    assert _value(b, _item(b, 1, 'amount')) == '40.00'
    assert _value(b, _item(b, 1, 'unit_cost')) == ''

    # A correction that touches only the header submits neither grid, so neither moves.
    _fill(b, 'f:memo', 'Header only')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    _save(b)
    revision, after = grids()
    assert revision == 2
    assert after == before, (after, before)

    # A correction on the Expenses tab replaces that grid alone. The item lines keep their
    # identities, their order and the item name they were captured under.
    correct()
    _fill(b, _line(b, 0, 'amount'), '200.00')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    _save(b)
    revision, after = grids()
    assert revision == 3
    assert after['items'] == before['items'], (after['items'], before['items'])
    assert [row[0] for row in after['expenses']] == [row[0] for row in before['expenses']]
    assert [row[1] for row in after['expenses']] == ['200.00', SECOND]
    # The grid that was submitted recaptured the renamed account; the grid that was not
    # kept the name it was written under. That is the same fact from both sides.
    assert after['expenses'][0][3] == 'Keep renamed account'
    assert after['items'][0][3] == f'{books["tag"]} valve'

    # And a correction made on the Items tab replaces that grid alone, the same way.
    correct()
    _open_tab(b, 'items')
    _fill(b, _item(b, 0, 'quantity'), '15')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    assert _totals(b)['Items'] == '225.25 USD', _totals(b)   # 15 x 12.35 + 40.00
    _save(b)
    revision, final = grids()
    assert revision == 4
    assert final['expenses'] == after['expenses'], (final['expenses'], after['expenses'])
    assert [row[0] for row in final['items']] == [row[0] for row in before['items']]
    assert [row[1] for row in final['items']] == ['185.25', '40.00']
    assert [row[2] for row in final['items']] == [row[2] for row in before['items']]
    assert final['items'][0][3] == 'Keep renamed item'


def test_the_items_grid_and_its_tabs_have_nothing_to_scroll_sideways_at_phone_width(register_browser):
    """The Items grid becomes one block per line too, in its own reading order."""
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    books = _books(b, env.site, 'Pocket')
    _bought_item(books)

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _open_tab(b, 'items')
    _pick(b, 'f:vendor', 'Pocket supply')
    _fill(b, 'f:date', '2026-08-10')
    _add_line(b, 'items')
    _pick(b, _item(b, 0, 'item'), 'Pocket valve')
    _fill(b, _item(b, 0, 'quantity'), '12')
    _fill(b, _item(b, 0, 'unit_cost'), VALVE_COST)
    _pick(b, _item(b, 0, 'customer'), 'Pocket homeowner')
    _fill(b, _item(b, 0, 'billable'), 'true')

    _contained(b, 390)
    grid = b.evaluate('''(() => {const g = document.querySelector("[data-collection-path=items]");
        return {scroll: g.scrollWidth, client: g.clientWidth};})()''')
    assert grid['scroll'] == grid['client'], grid
    # Each tab is a finger-wide target of its own, and the pair fills the window.
    widths = b.evaluate('''[...document.querySelectorAll("[data-line-tab]")]
        .map(t => Math.round(t.getBoundingClientRect().width))''')
    assert len(widths) == 2 and abs(widths[0] - widths[1]) <= 1, widths

    # The block's own reading order: what was bought across the block, then how many and
    # what one costs beside each other, then the amount and the class, then who it is for.
    boxes = b.evaluate(f'''(() => {{const row = document.querySelectorAll({json.dumps(ITEM_ROWS)})[0];
        const cell = name => row.querySelector(`[data-line-column="${{name}}"]`).getBoundingClientRect();
        const out = {{}};
        for (const name of ['item', 'description', 'quantity', 'unit_cost', 'amount',
                            'class_id', 'customer', 'billable'])
            out[name] = {{top: Math.round(cell(name).top), width: Math.round(cell(name).width)}};
        return out;}})()''')
    assert boxes['item']['width'] > boxes['quantity']['width'] * 1.8, boxes
    assert boxes['description']['width'] == boxes['item']['width'], boxes
    assert boxes['customer']['width'] == boxes['item']['width'], boxes
    assert boxes['quantity']['top'] == boxes['unit_cost']['top'], boxes
    assert boxes['amount']['top'] == boxes['class_id']['top'], boxes
    assert boxes['amount']['top'] > boxes['quantity']['top'], boxes
    assert boxes['customer']['top'] > boxes['amount']['top'], boxes
    assert boxes['billable']['top'] > boxes['customer']['top'], boxes
    assert b.evaluate(f'''[...document.querySelectorAll({json.dumps(ITEM_ROWS)} + " .line-cell")]
        .every(e => e.scrollWidth <= e.clientWidth + 1
                    && e.getBoundingClientRect().right <= innerWidth + 1)''')

    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    _contained(b, 390)
    _save(b)
    _contained(b, 390)


def test_one_window_enters_both_tabs_and_saves_both_grids(register_browser):
    """The tab that is not showing is hidden, not absent: its lines still reach the command.

    Both grids are entered in one window and only one of them can be on screen when Save is
    pressed. If hiding a panel took its controls out of the submission, a bill entered this
    way would silently lose whichever grid the person was not looking at.
    """
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Both')
    item, account = _bought_item(books)

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/bill/post')
    b.wait_for('!!document.querySelector("[data-line-tab]")')
    _pick(b, 'f:vendor', 'Both supply')
    _fill(b, 'f:date', '2026-09-02')
    _fill(b, 'f:supplier_reference', 'BOTH-1')
    _add_line(b)
    _pick(b, _line(b, 0, 'account'), 'Both parts')
    _fill(b, _line(b, 0, 'amount'), FIRST)
    _fill(b, _line(b, 0, 'memo'), 'Fittings')

    _open_tab(b, 'items')
    _add_line(b, 'items')
    _pick(b, _item(b, 0, 'item'), 'Both valve')
    _fill(b, _item(b, 0, 'quantity'), '12')
    _fill(b, _item(b, 0, 'unit_cost'), VALVE_COST)

    # The Expenses panel is off the screen at this point, and its line is still in the form.
    assert _tabs(b)['shown'] == ['items'], _tabs(b)
    assert b.evaluate('document.querySelector("[data-line-panel=expenses]").hidden') is True
    assert _count(b, ROWS) == 1 and _count(b, ITEM_ROWS) == 1

    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    assert _totals(b) == {'Expenses': f'{FIRST} USD', 'Items': f'{VALVES} USD',
                          'Amount due': f'{BOTH_TABS} USD'}

    saved = _save(b)
    written = books['run']('bill.show', {'bill': saved})
    assert [line['amount']['amount'] for line in written['revision']['expenses']] == [FIRST]
    assert [line['amount']['amount'] for line in written['revision']['items']] == [VALVES]
    assert written['revision']['items'][0]['account_id'] == account
    assert written['total']['amount'] == BOTH_TABS
    # Both tables are on the saved bill, each with the line it owns.
    detail = _text(b, '.sales-document')
    for part in ('Both parts', 'Fittings', 'Both valve', FIRST, VALVES, BOTH_TABS):
        assert part in detail, (part, detail[:1200])
