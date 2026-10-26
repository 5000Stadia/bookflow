"""R104 increment 5: bins and cycle counts in real headless Chrome, at 1440 and at 390 pixels.

A person starts a count of bin A2 from the Counts page, scans A2's bin label (the Bin field takes it) and then
an item's code, and the entry lands in A2; the lines are grouped by bin. The Counts page lists what is due for a
cycle count with "Count the next N", which starts a cycle count. On the Sites page the Bins card lists the
warehouse's bins and the transfer form moves stock from one bin to another of the same site. Every form submits
through htmx, as the server requires of a cookie session, and at a phone's width nothing scrolls sideways.
"""
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, _key, _type, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
COUPLING = '0012345678905'
A2_LABEL = '2000000001012'


def _text(b):
    return b.evaluate('document.body.innerText')


def _set(b, selector, value):
    b.evaluate(f'''(() => {{ const e = document.querySelector({selector!r}); e.value = {value!r};
        e.dispatchEvent(new Event("input", {{bubbles: true}})); e.dispatchEvent(new Event("change", {{bubbles: true}})); }})()''')


def _scan(b, text):
    b.evaluate("(() => { const s = document.querySelector('[data-scan-status]'); s.textContent = ''; s.dataset.kind = '';"
               " document.querySelector('[data-scan-input]').focus(); return true; })()")
    _type(b, text)
    _key(b, 'Enter')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')


@pytest.mark.parametrize('width', (1440, 390))
def test_count_a_bin_by_scanning_its_label_and_count_the_next_due(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    run('inventory-transfer.post', {'date': DEMO_AS_OF, 'from_site': 'Warehouse', 'to_site': 'Warehouse',
                                    'lines': [{'item': 'Copper Coupling', 'quantity': '1', 'to_bin': 'A2'}]})

    b.navigate(f'{base}/inventory/counts')
    b.wait_for('!!document.querySelector("[data-count-start]")')
    assert b.evaluate('!!document.querySelector("[data-cycle-due]")'), _text(b)[-1500:]
    if width == 390:
        _fits(b)
    _set(b, '[data-count-start] [name=site]', 'Warehouse')
    _set(b, '[data-count-start] [name=date]', DEMO_AS_OF)
    _set(b, '[data-count-bins]', 'A2')
    _set(b, '[data-count-start] [name="ctx:reason"]', 'Count bin A2')
    b.evaluate('document.querySelector("[data-count-create]").click()')
    b.wait_for('!!document.querySelector("#inventory-count [data-count-entry]") && !!window.bookflowScan', timeout=30)
    number = b.evaluate('document.querySelector("#inventory-count").dataset.count')
    assert run('inventory-count.show', {'count': number})['selection'] == {'kind': 'bins', 'bins': [
        run('bin.show', {'bin': 'A2'})['id']]}

    _scan(b, A2_LABEL)
    b.wait_for("document.querySelector('[data-scan-status]').dataset.kind === 'ok'")
    assert b.evaluate('document.querySelector("[data-scan-bin]").value') == 'A2'
    assert 'Current bin: A2' in b.evaluate("document.querySelector('[data-scan-status]').textContent")
    _scan(b, COUPLING)
    b.wait_for('!!document.querySelector("[data-count-result]")', timeout=30)
    assert 'in bin A2' in b.evaluate('document.querySelector("[data-count-result]").textContent')
    lines = run('inventory-count.show', {'count': number})['lines']
    assert ('Copper Coupling', 'A2', '1') in {(l['item_name'], l['bin'], l['counted']) for l in lines}
    assert 'Bin A2' in b.evaluate('[...document.querySelectorAll("[data-count-group]")].map(h => h.textContent).join("|")') \
        or len(set(l['bin'] for l in lines)) == 1
    assert b.evaluate('document.querySelector("[data-scan-bin]").value') == 'A2'   # the bin stays for the next scan
    if width == 390:
        _fits(b)

    b.navigate(f'{base}/inventory/counts')
    b.wait_for('!!document.querySelector("[data-count-next]")')
    b.evaluate('document.querySelector("[data-count-next]").click()')
    b.wait_for('!!document.querySelector("#inventory-count")', timeout=30)
    started = b.evaluate('document.querySelector("#inventory-count").dataset.count')
    assert run('inventory-count.show', {'count': started})['selection']['kind'] == 'cycle'


@pytest.mark.parametrize('width', (1440, 390))
def test_move_stock_between_two_bins_of_the_warehouse(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    b.navigate(f'{base}/inventory/sites')
    b.wait_for('!!document.querySelector("[data-bin-card]") && !!document.querySelector("[data-transfer-form]")')
    bins = b.evaluate('[...document.querySelectorAll("[data-bin-list] tbody tr")].map(r => r.cells[1].textContent)')
    assert bins == ['A1', 'A2', 'A3']
    if width == 390:
        _fits(b)
    _set(b, '[data-transfer-form] [name=from_site]', 'Warehouse')
    _set(b, '[data-transfer-form] [name=to_site]', 'Warehouse')
    _set(b, '[data-transfer-form] [name=date]', DEMO_AS_OF)
    _set(b, '[name=item_0]', 'Pipe Sealant')
    _set(b, '[name=quantity_0]', '1')
    _set(b, '[name=from_bin_0]', 'A2')
    _set(b, '[name=to_bin_0]', 'A3')
    _set(b, '[data-transfer-form] [name="ctx:reason"]', 'Restock the kit bay')
    b.evaluate('document.querySelector("[data-transfer-save]").click()')
    b.wait_for('!!document.querySelector("[data-site-result]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-site-result]")'), _text(b)[-1500:]
    held = run('bin.show', {'bin': 'A3', 'as_of': DEMO_AS_OF})['stock']
    assert ('Pipe Sealant', '1') in {(row['item_name'], row['quantity_on_hand']) for row in held}
    if width == 390:
        _fits(b)
