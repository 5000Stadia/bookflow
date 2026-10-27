"""R104 increment 6: landed cost in real headless Chrome, at 1440 and at 390 pixels.

A person enters a freight bill on Landed Cost Clearing, opens Inventory > Landed cost, ticks the charge, names the
two copper pipe receipts, previews (each target's share split into what stays in stock and what was issued before
the allocation date), then allocates. The bill of the first receipt then shows its lines' landed cost and landed
unit cost. Every form submits through htmx, as the server requires of a cookie session, and at a phone's width
nothing scrolls sideways.
"""
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


def _set(b, selector, value):
    b.evaluate(f'''(() => {{ const e = document.querySelector({selector!r}); e.value = {value!r};
        e.dispatchEvent(new Event("input", {{bubbles: true}})); e.dispatchEvent(new Event("change", {{bubbles: true}})); }})()''')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')


@pytest.mark.parametrize('width', (1440, 390))
def test_spread_a_freight_bill_over_two_receipts_and_see_it_on_the_bill(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    clearing = run('landed-cost.setup', {})['account_id']
    vendor = run('vendor.query', {'limit': 1})['items'][0]['id']
    run('bill.post', {'vendor': vendor, 'date': DEMO_AS_OF, 'number': 'FRT-88',
                      'expenses': [{'account': clearing, 'amount': '12.00'}]})
    charge = run('landed-cost.charges', {'as_of': DEMO_AS_OF})['rows'][0]['charge']

    b.navigate(f'{base}/inventory/landed-cost')
    b.wait_for('!!document.querySelector("[data-landed-charges]")')
    if width == 390:
        _fits(b)
    b.evaluate(f'document.querySelector("[name=\\"charge:{charge}\\"]").checked = true')
    _set(b, '[name=target_0]', 'DEMO-PIPE-1')
    _set(b, '[name=target_1]', 'DEMO-PIPE-2')
    _set(b, '[data-landed-form] [name="ctx:reason"]', "Freight for last week's pipe")
    b.evaluate('document.querySelector("[data-landed-preview]").click()')
    b.wait_for('!!document.querySelector("[data-landed-preview-result]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-landed-preview-result]")'), _text(b)[-1500:]
    shares = b.evaluate('[...document.querySelectorAll("[data-landed-preview-result] tbody tr")].map(r => r.cells[2].textContent)')
    assert len(shares) == 2 and '4.00' in shares[0] and '8.00' in shares[1], shares
    assert run('landed-cost.query', {})['rows'][0]['number'] == 'DEMO-LC-1'        # the preview saved nothing
    if width == 390:
        _fits(b)
    b.evaluate('document.querySelector("[data-landed-post]").click()')
    b.wait_for('!!document.querySelector("[data-landed-result]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-landed-result]")'), _text(b)[-1500:]
    assert run('landed-cost.charges', {'as_of': DEMO_AS_OF})['rows'] == []

    bill = run('bill.show', {'bill': 'DEMO-PIPE-1'})
    b.navigate(f'{base}/bill/{bill["id"]}')
    b.wait_for('!!document.querySelector("[data-landed-lines]")', timeout=30)
    text = b.evaluate('document.querySelector("[data-landed-lines]").innerText')
    assert 'Copper Pipe' in text and '14.00' in text and '1.4000' in text, text    # 10.00 + 4.00 over 10 lengths
    if width == 390:
        _fits(b)
