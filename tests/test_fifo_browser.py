"""R104 increment 7: Settings > Costing method in real headless Chrome, at 1440 and at 390 pixels.

A person opens the page (average today, no switches), picks FIFO from the demo's as-of date, previews (per item
and in total, the change in stock value and cost of goods sold, and the correction entries), switches, then reads
Inventory layers. Every form submits through htmx, as the server requires of a cookie session, and at a phone's
width nothing scrolls sideways.
"""
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_landed_cost_browser import _fits, _set, _text

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.mark.parametrize('width', (1440, 390))
def test_switch_to_fifo_from_settings_and_read_the_layers(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)

    b.navigate(f'{base}/inventory/costing-method')
    b.wait_for('!!document.querySelector("[data-costing-form]")')
    assert 'Average cost' in b.evaluate('document.querySelector("[data-costing-today]").innerText')
    if width == 390:
        _fits(b)
    _set(b, '[data-costing-form] [name=method]', 'fifo')
    _set(b, '[data-costing-form] [name=effective_from]', DEMO_AS_OF)
    _set(b, '[data-costing-form] [name="ctx:reason"]', "FIFO on our accountant's advice")
    b.evaluate('document.querySelector("[data-costing-preview]").click()')
    b.wait_for('!!document.querySelector("[data-costing-preview-result]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-costing-preview-result]")'), _text(b)[-1500:]
    assert run('costing-method.show', {})['switches'] == []                  # the preview saved nothing
    if width == 390:
        _fits(b)
    b.evaluate('document.querySelector("[data-costing-set]").click()')
    b.wait_for('!!document.querySelector("[data-costing-result]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-costing-result]")'), _text(b)[-1500:]
    switches = run('costing-method.show', {})['switches']
    assert [(s['method'], s['effective_from']) for s in switches] == [('fifo', DEMO_AS_OF)]
    assert b.evaluate('document.querySelectorAll("[data-costing-switches] tbody tr").length') == 1

    b.navigate(f'{base}/report/inventory-layers?as_of={DEMO_AS_OF}')
    b.wait_for('document.body.innerText.includes("Opening (FIFO from")', timeout=30)
    text = _text(b)
    assert 'Copper Pipe' in text, text[-1500:]
    if width == 390:
        _fits(b)
