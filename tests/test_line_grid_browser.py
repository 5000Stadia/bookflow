"""The line grid on a phone, measured rather than assumed.

What is checked here: at a phone width the grid has nothing to scroll sideways at all —
not merely that the page does not — because each line becomes its own block. The block
is the shape that was chosen: the prose fields full width, the small numbers paired two
to a row, the server's amount as an emphasised footer, the advanced panel collapsed and
the row's controls at the end. Every control carries a visible name of its own once the
column heads are gone. At a desktop width the same grid is still a table with heads.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_sales_document_browser import _add_line, _fixture, _preview, _saved
from tests.test_service_sales_browser import _choose, _click, _contained, _fill

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

PHONE = 390
DESKTOP = 1280
NEW = {'invoice': 'post', 'sales-receipt': 'post', 'estimate': 'create'}


def _grid(b):
    return b.evaluate('''(() => {const g = document.querySelector(".line-grid");
        const row = document.querySelector(".line-row");
        const box = e => { if (!e) return null; const r = e.getBoundingClientRect();
            return {top: r.top, left: r.left, right: r.right, bottom: r.bottom,
                    width: r.width, height: r.height}; };
        const cell = name => box(row.querySelector('[data-line-column="' + name + '"]'));
        const extras = row.querySelector(".line-extras");
        return {scroll: g.scrollWidth, client: g.clientWidth, row: box(row),
                heads: [...document.querySelectorAll(".line-head")]
                    .filter(e => e.getBoundingClientRect().height > 0).length,
                labels: [...row.querySelectorAll(".line-cell-label")]
                    .map(e => ({text: e.textContent.trim(),
                                shown: e.getBoundingClientRect().width > 4})),
                cells: Object.fromEntries(['item', 'description', 'quantity', 'unit',
                    'unit_price', 'tax_code', '@amount'].map(n => [n, cell(n)])),
                amountWeight: getComputedStyle(row.querySelector(".line-amount")).fontWeight,
                amountAlign: getComputedStyle(row.querySelector(".line-amount")).textAlign,
                extrasOpen: extras ? extras.open : null,
                actions: box(row.querySelector(".line-cell-actions")),
                extrasTop: extras ? box(extras).top : null,
                pricing: !!row.querySelector(".line-extras [name^=price-mode]")};})()''')


def _block(measured, width):
    """Every rule of the chosen block shape, read off the rendered boxes."""
    cells, row = measured['cells'], measured['row']
    for prose in ('item', 'description'):
        assert cells[prose]['width'] >= row['width'] - 25, (prose, measured)
    # Paired two to a row: same top, side by side, each about half the block.
    for left, right in (('quantity', 'unit'), ('unit_price', 'tax_code')):
        assert abs(cells[left]['top'] - cells[right]['top']) <= 2, (left, right, measured)
        assert cells[left]['left'] < cells[right]['left'], (left, right, measured)
        assert cells[left]['width'] < row['width'] * 0.75, (left, measured)
    # The amount is the block's own footer: full width, right-aligned and heavier.
    assert cells['@amount']['width'] >= row['width'] - 25, measured
    assert cells['@amount']['top'] > cells['unit_price']['top'], measured
    assert measured['amountAlign'] == 'right', measured
    assert int(measured['amountWeight']) >= 700, measured
    # Advanced controls stay shut, and Remove is at the end of the block below them.
    assert measured['extrasOpen'] is False, measured
    assert measured['actions']['top'] >= measured['extrasTop'] - 1, measured
    assert measured['actions']['bottom'] <= row['bottom'] + 1, measured


def _open(b, site, noun, verb):
    b.navigate(f'{site.base_url}/c/{site.company_id}/{noun}/{verb}')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _add_line(b)
    b.wait_for('!!document.querySelector(".line-row .line-cell")')


@pytest.mark.parametrize('noun', ('invoice', 'sales-receipt', 'estimate'))
def test_a_phone_never_scrolls_the_line_grid_sideways(register_browser, noun):
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    _fixture(run, 'Grid ' + noun)
    b.viewport(PHONE, 844)
    _open(b, env.site, noun, NEW[noun])

    measured = _grid(b)
    # The defect was here: the page did not scroll, the grid did.
    assert measured['scroll'] <= measured['client'] + 1, measured
    _contained(b, PHONE)
    # No column heads at this width, so every control says its own name.
    assert measured['heads'] == 0, measured
    assert measured['labels'] and all(row['shown'] for row in measured['labels']), measured
    assert 'Quantity' in [row['text'].split('—')[0].strip() for row in measured['labels']]
    _block(measured, PHONE)
    # The pricing rule is still reachable, in the row's own panel.
    assert measured['pricing'] is True, measured


def test_a_correction_form_is_the_same_stacked_card_on_a_phone(register_browser):
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    books = _fixture(run, 'Grid correction')
    saved = run('invoice.post', dict(date='2026-05-04', customer=books['customer'],
                ar_account=books['receivable'],
                lines=[dict(item=books['item'], quantity='2')]))['id']
    b.viewport(PHONE, 844)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/{saved}/update')
    b.wait_for('!!document.querySelector(".line-row .line-cell")')

    measured = _grid(b)
    assert measured['scroll'] <= measured['client'] + 1, measured
    assert measured['heads'] == 0, measured
    assert all(row['shown'] for row in measured['labels']), measured
    _block(measured, PHONE)
    _contained(b, PHONE)


def test_a_phone_card_still_posts_the_sale_the_command_would(register_browser):
    """A layout that cannot be filled in is not a layout."""
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    books = _fixture(run, 'Grid posts')
    b.viewport(PHONE, 844)
    _open(b, env.site, 'invoice', 'post')
    _fill(b, 'f:date', '2026-05-06')
    _choose(b, 'f:customer', 'Grid posts customer')
    _choose(b, 'f:ar_account', 'Grid posts receivables')
    _choose(b, 'c:lines:0:item', 'Grid posts service')
    _fill(b, 'c:lines:0:quantity', '3')
    _preview(b)
    _click(b, 'submit')
    posted = run('invoice.show', dict(invoice=_saved(b, 'invoice')))
    assert posted['revision']['lines'][0]['quantity'] == '3'
    _contained(b, PHONE)


def test_a_desktop_still_gets_a_table_with_heads(register_browser):
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    _fixture(run, 'Grid desktop')
    b.viewport(DESKTOP, 900)
    _open(b, env.site, 'invoice', 'post')

    measured = _grid(b)
    assert measured['heads'] == 1, measured
    # The per-cell names stay out of the way: the head is the sighted reader's version.
    assert measured['labels'] and not any(row['shown'] for row in measured['labels']), measured
    # Wide by design, and the width belongs to the grid rather than to the page.
    _contained(b, DESKTOP)
    heading = b.evaluate('''[...document.querySelectorAll(".line-head .line-cell")]
        .map(e => e.textContent.trim())''')
    assert 'Pricing' not in heading, heading
    assert any(text.startswith('Unit of measure') for text in heading), heading
    assert any('How many' in text for text in heading), heading
