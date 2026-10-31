"""R107 increment 7: guided opening balances in real headless Chrome, at 1440 and 390 (design/bulk-import.md §6, §9).

A person opens Opening balances, gives the date and the receivables total they know, pastes the balances they know,
three open invoices, two unpaid bills and three stock items (one not an item yet, confirmed to be made), checks the
plan (the given and derived checks listed apart), posts it, and reads the tie-out: tied, the given figures proved,
stock and equity taken from the rows. Every post is a real click through htmx (the page never navigates), and at
390 px nothing scrolls sideways at any step. The demo company's books start in 2025, so an opening as of 2024-12-31
has nothing before it. With BF_OPENING_SHOTS set to a folder, each step is saved there at each width.
"""
import base64
import json
import os
from pathlib import Path

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

AS_OF = '2024-12-31'
BALANCES = 'Account\tBalance\nChecking\t4,250.00\n'
INVOICES = ('Customer\tNumber\tDate\tTerms\tOpen amount\n'
            'Opening Cafe\tOB-101\t12/02/2024\tNet 30\t300.00\n'
            'Opening Cafe\tOB-102\t12/20/2024\tNet 30\t125.50\n'
            'Opening Dental\tOB-103\t11/15/2024\tNet 30\t74.50\n')
BILLS = ('Vendor\tReference\tDate\tTerms\tOpen amount\n'
         'Opening Supply\tOS-1\t12/10/2024\tNet 30\t180.00\n'
         'Opening Supply\tOS-2\t12/28/2024\tNet 30\t20.00\n')
STOCK = ('Item\tQuantity\tUnit cost\n'
         'Opening widget\t4\t12.50\n'
         'Opening gadget\t10\t3.00\n'
         'Opening sprocket\t2\t7.25\n')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), b.evaluate(
        '[...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > innerWidth + 1)'
        '.slice(0, 5).map(e => e.tagName + "." + e.className).join(" ")')


def _shot(b, width, name):
    _fits(b)
    folder = os.environ.get('BF_OPENING_SHOTS')
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
    b.evaluate('document.querySelector("#opening-workspace").dataset.before = "1"')
    b.evaluate(f'document.querySelector({json.dumps(selector)}).click()')
    b.wait_for(f'!!document.querySelector("#opening-workspace[data-opening-step={step}]:not([data-before])")'
               ' || !!document.querySelector("#opening-workspace:not([data-before]) [data-opening-error]")'
               ' || window.__failed', timeout=120)
    assert not b.evaluate('window.__failed'), 'the server failed: ' + str(b.evaluate('window.__failed'))
    assert b.evaluate('window.__sameDocument === true'), 'the form left the page instead of swapping through htmx'
    assert not b.evaluate('!!document.querySelector("[data-opening-error]")'), b.evaluate(
        'document.querySelector("[data-opening-error]").innerText')


def _set(b, selector, value):
    b.evaluate(f'''(() => {{ const e = document.querySelector({json.dumps(selector)});
        e.value = {json.dumps(value)}; e.dispatchEvent(new Event('input', {{bubbles: true}})); }})()''')


@pytest.mark.parametrize('width', (1440, 390))
def test_a_person_pastes_an_opening_posts_it_and_reads_the_tie_out(register_browser, width):
    env, b = register_browser, register_browser.browser
    site = env.site
    income = next(a['id'] for a in _command(b, site, 'account.list', {})['items'] if a['full_name'] == 'Sales Income'
                  or a['type'] == 'income')
    accounts = {a['full_name']: a['id'] for a in _command(b, site, 'account.list', {})['items']}
    for name, cost in (('Opening widget', '12.50'), ('Opening gadget', '3.00')):
        _command(b, site, 'item.create', {'name': name, 'type': 'inventory_part', 'price': '20.00', 'cost': cost,
                                          'description': name, 'purchase_description': name,
                                          'income_account_id': income,
                                          'cogs_account_id': accounts['Cost of Goods Sold'],
                                          'asset_account_id': accounts['Inventory Asset']})
    b.viewport(width, 900)
    b.navigate(f'{site.base_url}/c/{site.company_id}/opening')
    b.wait_for('!!document.querySelector("[data-opening-start]")')
    _shot(b, width, 'start')
    _set(b, '[data-opening-as-of]', AS_OF)
    _set(b, '[name=given_receivables]', '500.00')
    _click(b, '[data-opening-begin]', 'balances')
    for step, text, after in (('balances', BALANCES, 'open_invoices'), ('open_invoices', INVOICES, 'unpaid_bills'),
                              ('unpaid_bills', BILLS, 'stock')):
        _set(b, '[data-opening-grid]', text)
        _click(b, '[data-opening-save]', step)
        assert not b.evaluate('!!document.querySelector("[data-opening-problems]")'), b.evaluate(
            'document.querySelector("[data-opening-problems]").innerText')
        _shot(b, width, step)
        _click(b, '[data-opening-next]', after)
    _set(b, '[data-opening-grid]', STOCK)
    _set(b, '[data-opening-make]', 'Opening sprocket')
    _click(b, '[data-opening-next]', 'post')
    _shot(b, width, 'stock-saved')
    _click(b, '[data-opening-plan]', 'post')
    assert b.evaluate('document.querySelector("[data-opening-plan-result]").dataset.ready') == 'yes', b.evaluate(
        'document.querySelector("[data-opening-plan-result]").innerText')
    given = b.evaluate('document.querySelector("[data-opening-given]").innerText')
    derived = b.evaluate('document.querySelector("[data-opening-derived]").innerText')
    assert 'receivables' in given and 'inventory' in derived
    _shot(b, width, 'plan')
    _click(b, '[data-opening-apply]', 'post')
    assert b.evaluate('document.querySelector("[data-opening-tie]").dataset.tied') == 'yes', b.evaluate(
        'document.querySelector("[data-opening-tie]").innerText')
    proved = b.evaluate('document.querySelector("[data-opening-tie-given]").innerText')
    taken = b.evaluate('document.querySelector("[data-opening-tie-derived]").innerText')
    assert 'tied to the 4,250.00 you gave' in proved and 'tied to the 500.00 you gave' in proved
    assert 'taken from your stock list; not independently checked' in taken
    assert 'worked out as assets less liabilities' in taken
    _shot(b, width, 'tied')
    # The books say the same: 14.50 + 30.00 + 50.00 = 94.50 of stock; 500.00 owed to the company, 200.00 by it.
    stock = {r['item_name']: r['asset_value']['amount'] for r in _command(
        b, site, 'report.inventory-valuation', {'as_of': AS_OF})['rows'] if r['item_name'].startswith('Opening ')}
    assert stock == {'Opening widget': '50.00', 'Opening gadget': '30.00', 'Opening sprocket': '14.50'}
    # A reload comes back to the same draft and step.
    b.evaluate('location.reload()')
    b.wait_for('!!document.querySelector("#opening-workspace[data-opening-step=post] [data-opening-tie]")')
    _fits(b)
