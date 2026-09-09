"""The sales document window driven in a real browser, at desktop and phone widths.

What is checked here: the form posts the same command an agent would; a saved document
reopens with everything intact including line origins; an applied payment survives a
correction; the totals on screen are the server's own; and the page never scrolls sideways.
"""
import json
import re

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _choose, _click, _contained, _fill, _name, _value

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

# The document's own identities differ between two separately posted sales; everything
# that describes the sale itself must not.
VOLATILE = {'id', 'transaction_id', 'revision_id', 'line_id', 'document_line_id', 'created_at',
            'audit_event_id', 'number', 'current_revision_id', 'facts_fingerprint',
            'idempotency_key', 'operation_key', 'value_id'}


def _scrub(value):
    if isinstance(value, dict):
        return {key: _scrub(inner) for key, inner in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [_scrub(inner) for inner in value]
    return value


def _differences(left, right, path=''):
    """Every place two scrubbed sales differ, named by its path, for a readable failure."""
    if isinstance(left, dict) and isinstance(right, dict):
        found = []
        for key in sorted(set(left) | set(right)):
            found += _differences(left.get(key, '<missing>'), right.get(key, '<missing>'),
                                  f'{path}.{key}')
        return found
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [d for index, pair in enumerate(zip(left, right))
                for d in _differences(pair[0], pair[1], f'{path}[{index}]')]
    return [] if left == right else [f'{path}: {left!r} != {right!r}']


ROWS = '[data-collection-path=lines] > [data-collection-items] > [data-collection-item]'


def _add_line(b):
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')


def _rows(b):
    return b.evaluate(f'document.querySelectorAll("{ROWS}").length')


def _remove_line(b, index):
    b.evaluate(f'document.querySelectorAll("{ROWS}")[{index}]'
               '.querySelector("[data-collection-remove]").click()')


def _preview(b):
    b.evaluate('void (window.documentForm = document.querySelector("[data-sales-form]"))')
    _click(b, 'preview')
    b.wait_for('!window.documentForm.isConnected')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), b.evaluate('document.body.innerText')
    fingerprint = _value(b, 'f:expected_facts_fingerprint')
    assert re.fullmatch('[0-9a-f]{64}', fingerprint), b.evaluate('document.body.innerText')[:800]
    return fingerprint


def _saved(b, noun):
    b.wait_for(f'!document.querySelector("[data-generated-form]") && location.pathname.includes("/{noun}/")')
    return b.evaluate('location.pathname').rsplit('/', 1)[-1]


def _totals(b):
    return b.evaluate('''(() => {const list = document.querySelector(".totals-list");
        if (!list) return null;
        const terms = [...list.querySelectorAll("dt")].map(e => e.textContent.trim());
        const values = [...list.querySelectorAll("dd")].map(e => e.textContent.trim());
        return Object.fromEntries(terms.map((t, i) => [t, values[i]]));})()''')


def _books(b, site):
    def run(name, payload, **headers):
        return _command(b, site, name, payload, **headers)
    return run


def _fixture(run, tag):
    income = run('account.create', dict(name=f'{tag} income', type='income'))['id']
    receivable = run('account.create', dict(name=f'{tag} receivables', type='accounts_receivable'))['id']
    customer = run('customer.create', dict(name=f'{tag} customer'))['id']
    code = next(row['id'] for row in run('sales-tax-code.list', {})['items'] if not row['taxable'])
    item = run('item.create', dict(name=f'{tag} service', type='service', sales_enabled=True,
        income_account_id=income, price='12.34', description='Saved description',
        sales_tax_code_id=code))['id']
    return dict(income=income, receivable=receivable, customer=customer, item=item)


def test_the_document_posts_the_same_invoice_the_command_does(register_browser):
    env, b = register_browser, register_browser.browser
    run = _books(b, env.site)
    b.viewport(1280, 900)
    books = _fixture(run, 'Parity')
    base = f'{env.site.base_url}/c/{env.site.company_id}/invoice'
    b.navigate(base + '/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-02-03')
    _choose(b, 'f:customer', 'Parity customer')
    _choose(b, 'f:ar_account', 'Parity receivables')
    _fill(b, 'f:memo', 'Identical either way')
    _fill(b, 'f:customer_purchase_order', 'PO-4471')
    _add_line(b)
    _choose(b, 'c:lines:0:item', 'Parity service')
    _fill(b, 'c:lines:0:quantity', '3')
    _fill(b, 'c:lines:0:unit_price', '10.00')
    _fill(b, 'c:lines:0:description', 'Entered on the form')
    _add_line(b)
    _choose(b, 'c:lines:1:item', 'Parity service')
    _fill(b, 'c:lines:1:quantity', '1.5')
    _contained(b, 1280)
    _preview(b)
    on_screen = _totals(b)
    _click(b, 'submit')
    through_form = _saved(b, 'invoice')

    # The picker sends an explicit null unit once an item is chosen, so the equivalent
    # command says the same thing. That is reference-picker behaviour older than this form.
    lines = [dict(item=books['item'], quantity='3', unit_price='10.00', unit=None,
                  description='Entered on the form'),
             dict(item=books['item'], quantity='1.5', unit=None)]
    equivalent = run('invoice.post', dict(date='2026-02-03', customer=books['customer'],
        ar_account=books['receivable'], memo='Identical either way', customer_purchase_order='PO-4471',
        lines=lines))
    from_form = run('invoice.show', dict(invoice=through_form))
    differences = _differences(_scrub(from_form['revision']), _scrub(equivalent['revision']))
    assert differences == [], differences
    assert from_form['total_minor_units'] == equivalent['total_minor_units']

    # Naming that divergence rather than hiding it: against a command that omits `unit`,
    # nothing differs except where the unit came from.
    omitting = run('invoice.post', dict(date='2026-02-03', customer=books['customer'],
        ar_account=books['receivable'], memo='Identical either way', customer_purchase_order='PO-4471',
        lines=[{key: value for key, value in line.items() if key != 'unit'} for line in lines]))
    drift = _differences(_scrub(from_form['revision']), _scrub(omitting['revision']))
    assert drift and all('origins.unit' in difference for difference in drift), drift

    # The totals shown at preview are the server's, to the cent.
    assert on_screen['Subtotal'] == from_form['revision']['subtotal']['amount']
    assert on_screen['Tax'] == from_form['revision']['tax']['amount']
    assert on_screen['Total'].split()[0] == from_form['revision']['total']['amount']


def test_a_saved_invoice_reopens_with_every_value_and_its_line_origins(register_browser):
    env, b = register_browser, register_browser.browser
    run = _books(b, env.site)
    b.viewport(1280, 900)
    books = _fixture(run, 'Reopen')
    posted = run('invoice.post', dict(date='2026-02-04', customer=books['customer'],
        ar_account=books['receivable'], memo='Keep every word', customer_purchase_order='PO-7',
        billing_address=dict(line1='9 Mill Lane', city='Rye'),
        lines=[dict(item=books['item'], quantity='2.5', description='Original line text'),
               dict(item=books['item'], quantity='1', unit_price='4.44')]))
    base = f'{env.site.base_url}/c/{env.site.company_id}/invoice'
    b.navigate(f'{base}/{posted["id"]}/update')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    assert _value(b, 'f:memo') == 'Keep every word'
    assert _value(b, 'f:customer_purchase_order') == 'PO-7'
    assert _value(b, 'f:customer') == books['customer']
    assert _value(b, 'f:billing_address.line1') == '9 Mill Lane'
    assert _value(b, 'f:billing_address.city') == 'Rye'
    # The folded address still says what it holds without being opened.
    summary = b.evaluate('document.querySelector(".address-block .address-summary").textContent')
    assert '9 Mill Lane' in summary and 'Rye' in summary
    for index, line in enumerate(posted['revision']['lines']):
        assert _value(b, f'c:lines:{index}:line_id') == line['line_id']
        assert _value(b, f'c:lines:{index}:item') == line['item_snapshot']['item']['id']
        assert _value(b, f'c:lines:{index}:quantity') == line['quantity']
    assert _value(b, 'c:lines:0:description') == 'Original line text'
    assert _value(b, 'c:lines:1:unit_price') == '4.44'
    assert _totals(b)['Total'].split()[0] == posted['revision']['total']['amount']

    # A correction that changes one quantity keeps the saved origins of both lines.
    run('item.update', dict(item=books['item'], price='99.00', description='New master text'))
    _fill(b, 'c:lines:0:quantity', '4')
    _fill(b, 'ctx:reason', 'Quantity corrected')
    _preview(b)
    _click(b, 'submit')
    assert _saved(b, 'invoice') == posted['id']
    corrected = run('invoice.show', dict(invoice=posted['id']))
    assert corrected['version'] == 2
    for before, after in zip(posted['revision']['lines'], corrected['revision']['lines']):
        assert before['line_id'] == after['line_id']
        assert before['item_snapshot'] == after['item_snapshot']
        assert before['unit_price'] == after['unit_price']
    assert 'New master text' not in json.dumps(corrected['revision'])


def test_an_applied_payment_survives_a_correction_made_in_the_document(register_browser):
    env, b = register_browser, register_browser.browser
    run = _books(b, env.site)
    b.viewport(1280, 900)
    books = _fixture(run, 'Applied')
    method = run('payment-method.create', dict(name='Applied cheque', kind='check'))['id']
    # Both sides name the same receivable so the application is compatible.
    posted = run('invoice.post', dict(date='2026-02-05', customer=books['customer'],
        ar_account=books['receivable'], memo='Half paid',
        lines=[dict(item=books['item'], quantity='1', unit_price='100.00')]))
    payment = run('payment.receive', dict(customer=books['customer'], date='2026-02-06',
        amount='50.00', payment_method=method, deposit_to=env.bank['id'],
        ar_account=books['receivable'],
        operation_key='document-form-applied', applications=dict(mode='inline', items=[
            dict(invoice=posted['id'], expected_version=1, amount='50.00')])))
    base = f'{env.site.base_url}/c/{env.site.company_id}/invoice'
    b.navigate(f'{base}/{posted["id"]}/update')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    before = _totals(b)
    assert before['Payments Applied'] == '50.00'
    assert before['Balance Due'].split()[0] == '50.00'
    _fill(b, 'c:lines:0:quantity', '2')
    _fill(b, 'ctx:reason', 'Second visit added')
    _preview(b)
    _click(b, 'submit')
    assert _saved(b, 'invoice') == posted['id']
    corrected = run('invoice.show', dict(invoice=posted['id']))
    assert corrected['total_minor_units'] == 20000
    settlement = run('invoice.settlement', dict(invoice=posted['id']))
    assert settlement['applied_minor_units'] == 5000
    assert settlement['due_minor_units'] == 15000
    assert run('payment.show', dict(payment=payment['id']))['id'] == payment['id']
    # Reopening shows the retained application against the corrected total.
    b.navigate(f'{base}/{posted["id"]}/update')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    after = _totals(b)
    assert after['Payments Applied'] == '50.00' and after['Balance Due'].split()[0] == '150.00'
    assert after['Total'].split()[0] == corrected['revision']['total']['amount']


def test_adding_and_removing_lines_keeps_the_values_already_entered(register_browser):
    env, b = register_browser, register_browser.browser
    run = _books(b, env.site)
    b.viewport(1280, 900)
    books = _fixture(run, 'Rows')
    base = f'{env.site.base_url}/c/{env.site.company_id}/invoice'
    b.navigate(base + '/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-02-07')
    _choose(b, 'f:customer', 'Rows customer')
    _choose(b, 'f:ar_account', 'Rows receivables')
    for index, quantity in enumerate(('1', '2', '3')):
        _add_line(b)
        _choose(b, f'c:lines:{index}:item', 'Rows service')
        _fill(b, f'c:lines:{index}:quantity', quantity)
        _fill(b, f'c:lines:{index}:description', f'Row {quantity}')
    assert _rows(b) == 3
    _remove_line(b, 1)
    assert _rows(b) == 2
    assert [_value(b, f'c:lines:{i}:quantity') for i in (0, 1)] == ['1', '3']
    assert [_value(b, f'c:lines:{i}:description') for i in (0, 1)] == ['Row 1', 'Row 3']
    _add_line(b)
    _choose(b, 'c:lines:2:item', 'Rows service')
    _fill(b, 'c:lines:2:quantity', '4')
    assert [_value(b, f'c:lines:{i}:quantity') for i in (0, 1, 2)] == ['1', '3', '4']
    assert _value(b, 'c:lines:0:description') == 'Row 1'
    _preview(b)
    _click(b, 'submit')
    saved = run('invoice.show', dict(invoice=_saved(b, 'invoice')))
    assert [line['quantity'] for line in saved['revision']['lines']] == ['1', '3', '4']
    assert [line['description'] for line in saved['revision']['lines']][:2] == ['Row 1', 'Row 3']


@pytest.mark.parametrize('noun', ['invoice', 'estimate'])
def test_the_line_grid_has_nothing_to_scroll_sideways_at_phone_width(register_browser, noun):
    """A phone gets a block per line, not a wide table dragged through a narrow window.

    This used to assert the opposite — that the grid scrolled inside itself while the
    page did not. The page was fine and the grid was 1032px inside a 340px window, which
    is the thing a person complained about, so the contract is now the grid's own.
    """
    env, b = register_browser, register_browser.browser
    run = _books(b, env.site)
    b.viewport(390, 844)
    books = _fixture(run, 'Phone ' + noun)
    verb = 'post' if noun == 'invoice' else 'create'
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/{noun}/{verb}')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-02-08')
    _choose(b, 'f:customer', f'Phone {noun} customer')
    if noun == 'estimate':
        _fill(b, 'f:title', 'Phone job')
    else:
        _choose(b, 'f:ar_account', f'Phone {noun} receivables')
    _add_line(b)
    _choose(b, 'c:lines:0:item', f'Phone {noun} service')
    _fill(b, 'c:lines:0:quantity', '2')
    _contained(b, 390)
    grid = b.evaluate('''(() => {const g = document.querySelector(".line-grid");
        return {scroll: g.scrollWidth, client: g.clientWidth,
                overflow: getComputedStyle(g).overflowX};})()''')
    assert grid['scroll'] <= grid['client'] + 1, grid
    _preview(b)
    _contained(b, 390)
