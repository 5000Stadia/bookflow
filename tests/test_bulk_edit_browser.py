"""R107 increment 6: editing many records in real headless Chrome, at 1440 and 390 (design/bulk-import.md §7, §11).

A person reprices 20 items from the item list's "Edit many…": searches them, types one price into its cell, raises
the column 5 percent rounded to 0.05, reads the changes (each item's price before and after, the total), confirms
"Change 20 items", and sees each outcome. Undo plans first; once someone has changed an item since, the plan names
it and nothing changes, and "Leave 1, undo the other 19" restores the rest to the cent. Every post is a real click
through htmx (the page never navigates), and at 390 px nothing scrolls sideways. With BF_BULK_SHOTS set to a folder,
the grid, the changes and the reverse plan are saved there at each width.
"""
import base64
import json
import os
from pathlib import Path

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), b.evaluate(
        '[...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > innerWidth + 1)'
        '.slice(0, 5).map(e => e.tagName + "." + e.className).join(" ")')


def _shot(b, width, name):
    _fits(b)
    folder = os.environ.get('BF_BULK_SHOTS')
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    b.evaluate('(() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); })()')
    height = min(b.evaluate('Math.max(innerHeight, document.documentElement.scrollHeight)'), 6000)
    (Path(folder) / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {
        'format': 'png', 'captureBeyondViewport': True,
        'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}})['data']))


def _click(b, selector, step):
    """A real click on a submit button; the answer swaps the workspace in place (htmx), never a navigation."""
    b.evaluate('''(() => { window.__sameDocument = true; window.__failed = null;
        document.body.addEventListener('htmx:responseError', e => { window.__failed = e.detail.xhr.status; }); })()''')
    b.evaluate('document.querySelector("#bulk-workspace").dataset.before = "1"')
    b.evaluate(f'document.querySelector({json.dumps(selector)}).click()')
    b.wait_for(f'!!document.querySelector("#bulk-workspace[data-bulk-step={step}]:not([data-before])")'
               ' || !!document.querySelector("#bulk-workspace:not([data-before]) [data-bulk-error]")'
               ' || window.__failed', timeout=120)
    assert not b.evaluate('window.__failed'), 'the server failed: ' + str(b.evaluate('window.__failed'))
    assert b.evaluate('window.__sameDocument === true'), 'the form left the page instead of swapping through htmx'
    assert not b.evaluate('!!document.querySelector("[data-bulk-error]")'), b.evaluate(
        'document.querySelector("[data-bulk-error]").innerText')


def _set(b, selector, value):
    b.evaluate(f'''(() => {{ const e = document.querySelector({json.dumps(selector)});
        e.value = {json.dumps(value)}; e.dispatchEvent(new Event('input', {{bubbles: true}}));
        e.dispatchEvent(new Event('change', {{bubbles: true}})); }})()''')


def _price(b, site, name):
    shown = _command(b, site, 'item.show', {'item': name})
    return shown['price']['amount'] if shown['price'] else None


@pytest.mark.parametrize('width', (1440, 390))
def test_a_person_reprices_twenty_items_and_undoes_it(register_browser, width):
    env, b = register_browser, register_browser.browser
    site = env.site
    base = f'{site.base_url}/c/{site.company_id}'
    income = next(a['id'] for a in _command(b, site, 'account.list', {})['items'] if a['type'] == 'income')
    exempt = next(c['id'] for c in _command(b, site, 'sales-tax-code.list', {})['items'] if not c['taxable'])
    names = [f'Grid part {i:02d}' for i in range(1, 21)]
    for i, name in enumerate(names, 1):
        _command(b, site, 'item.create', {'name': name, 'type': 'service', 'sales_enabled': True,
                                          'income_account_id': income, 'sales_tax_code_id': exempt,
                                          'price': f'{i}.10'})
    before = {name: _price(b, site, name) for name in names}
    b.viewport(width, 900)

    # The item list links to the grid; a search picks the twenty.
    b.navigate(f'{base}/item')
    b.wait_for('!!document.querySelector("[data-list-bulk-edit]")', timeout=30)
    b.evaluate('document.querySelector("[data-list-bulk-edit]").click()')
    b.wait_for('!!document.querySelector("[data-bulk-step=grid]")', timeout=60)
    b.navigate(f'{base}/bulk-edit?kind=item&q=Grid+part&columns=price,description')
    b.wait_for('!!document.querySelector("[data-bulk-grid]")', timeout=60)
    assert b.evaluate('document.querySelectorAll("[data-bulk-grid] tbody tr[data-record]").length') == 20
    assert '20 items' in b.evaluate('document.querySelector("[data-bulk-count]").innerText')

    # One price typed into its cell, then the column raised 5 percent to the nearest 0.05.
    _set(b, '[data-record="Grid part 01"] input[data-name$=":price"]', '9.99')
    assert b.evaluate('document.querySelectorAll("[data-bulk-grid] td [name]").length') == 1  # only it is sent
    _set(b, '[data-bulk-op-field]', 'price')
    _set(b, '[data-bulk-op-kind]', 'percent')
    _set(b, '[data-bulk-op-by]', '5')
    _set(b, '[data-bulk-op-round]', '0.05')
    _shot(b, width, '1-grid')
    _click(b, '[data-bulk-preview]', 'changes')
    rows = b.evaluate('[...document.querySelectorAll("[data-bulk-row]")].map(r => [r.dataset.bulkRow, '
                      'r.querySelector(".bulk-before").innerText, r.querySelector(".bulk-after").innerText])')
    assert len(rows) == 20 and ['Grid part 01', '1.10', '10.50'] in rows and ['Grid part 17', '17.10', '17.95'] in rows
    assert 'Change 20 items' in b.evaluate('document.querySelector("[data-bulk-apply]").innerText')
    assert '212.00' in b.evaluate('document.querySelector("[data-bulk-totals]").innerText')  # 1.10 + ... + 20.10
    _shot(b, width, '2-changes')
    _click(b, '[data-bulk-apply]', 'done')
    assert '20 changed' in b.evaluate('document.querySelector("[data-bulk-counts]").innerText')
    assert _price(b, site, 'Grid part 01') == '10.50' and _price(b, site, 'Grid part 17') == '17.95'

    # Someone changes one item since; Undo plans first and names it, changing nothing.
    _command(b, site, 'item.update', {'item': 'Grid part 02', 'price': '5.00'})
    _click(b, '[data-bulk-undo-start]', 'done')
    blockers = b.evaluate('document.querySelector("[data-bulk-blockers]").innerText')
    assert 'Grid part 02' in blockers and _price(b, site, 'Grid part 03') != before['Grid part 03']
    assert 'Leave 1, undo the other 19' in b.evaluate('document.querySelector("[data-bulk-undo-skip]").innerText')
    _shot(b, width, '3-reverse-plan')
    _click(b, '[data-bulk-undo-skip]', 'done')
    assert 'Grid part 02' in b.evaluate('document.querySelector("[data-bulk-undone]").innerText')
    after = {name: _price(b, site, name) for name in names}
    assert after == {**before, 'Grid part 02': '5.00'}
    _fits(b)
