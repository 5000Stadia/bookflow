"""R104 increment 3: the Scan field on the invoice screen, in real headless Chrome, at 1440 and 390.

A scanner in keyboard mode types the code and Enter. Scanning the Copper Coupling's 0012345678905
three times makes one line of 3; `6*` first makes it 6; an unknown code says so with "Assign to an
item" and keeps both the form and what was typed; and no Enter in the Scan field ever submits the form.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, _key, _type, register_browser  # noqa: F401
from tests.test_sales_document_browser import _preview, _saved
from tests.test_service_sales_browser import _choose, _click, _fill

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
COUPLING = '0012345678905'
LINES = """(() => [...document.querySelectorAll('[data-collection-items] > [data-collection-item]')]
  .map(r => [r.querySelector('[data-ref-value][name$=":item"]'), r.querySelector('[name$=":quantity"]')])
  .filter(([i]) => i && i.value).map(([i, q]) => [i.closest('[data-reference]').querySelector('[data-ref-search]').value, q.value]))()"""


def _open(b, base):
    b.navigate(f'{base}/invoice/post')
    b.wait_for('!!document.querySelector("[data-scan-input]") && !!window.bookflowScan')
    # Every submit the page sees is counted before anything can stop it; the body marker shows no swap.
    b.evaluate("""(() => { window.submits = 0; document.body.dataset.scanMarker = 'kept';
      addEventListener('submit', () => { window.submits++; }, true);
      document.querySelector('[data-scan-input]').focus(); return true; })()""")


def _scan(b, text):
    b.evaluate("(() => { const s = document.querySelector('[data-scan-status]'); s.textContent = ''; s.dataset.kind = '';"
               " document.querySelector('[data-scan-input]').focus(); return true; })()")
    _type(b, text)
    _key(b, 'Enter')
    b.wait_for("document.querySelector('[data-scan-status]').dataset.kind !== 'busy' && "
               "document.querySelector('[data-scan-status]').textContent !== ''")


def test_scanning_on_the_invoice_screen(register_browser):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(1440, 900)
    _open(b, base)
    for _ in range(3):
        _scan(b, COUPLING)
    assert b.evaluate(LINES) == [['Copper Coupling', '3']]
    assert b.evaluate("document.activeElement.matches('[data-scan-input]')")
    assert b.evaluate("document.querySelector('[data-scan-input]').value") == ''
    _scan(b, '6*' + COUPLING)
    assert b.evaluate(LINES) == [['Copper Coupling', '6']]
    _scan(b, '2999999999999')
    status = b.evaluate("document.querySelector('[data-scan-status]').innerText")
    assert 'No item has the code 2999999999999' in status and 'Assign to an item' in status, status
    assert b.evaluate("document.querySelector('[data-scan-input]').value") == '2999999999999'
    assert b.evaluate(LINES) == [['Copper Coupling', '6']]                     # the form is as it was
    # The guard: no Enter in the Scan field submitted anything, and the page was never swapped.
    assert b.evaluate('window.submits') == 0
    assert b.evaluate('document.body.dataset.scanMarker') == 'kept'
    assert b.evaluate('location.pathname').endswith('/invoice/post')
    # The scanned line is an ordinary line: the invoice saves through htmx with it.
    b.evaluate("document.querySelector('[data-scan-input]').value = ''")
    _fill(b, 'f:date', '2026-12-20')
    _choose(b, 'f:customer', 'Riverside Apartments')
    _preview(b)
    _click(b, 'submit')
    posted = _command(b, env.site, 'invoice.show', dict(invoice=_saved(b, 'invoice')))
    line = posted['revision']['lines'][0]
    assert line['quantity'] == '6' and 'Copper Coupling' in json.dumps(line), line
    b.viewport(390, 844)
    _open(b, base)
    _scan(b, COUPLING)
    _scan(b, COUPLING)
    assert b.evaluate(LINES) == [['Copper Coupling', '2']]
    assert b.evaluate('window.submits') == 0
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    box = json.loads(b.evaluate("JSON.stringify(document.querySelector('[data-scan-input]').getBoundingClientRect())"))
    assert box['width'] > 200 and box['right'] <= 391, box


def _settle(b):
    """Wait until every queued scan has answered."""
    b.wait_for("document.querySelector('[data-scan-status]').dataset.kind !== 'busy' && window.pending === 0", timeout=20)


def test_scans_while_a_look_up_is_in_flight(register_browser):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(1440, 900)
    _open(b, base)
    # Every look-up answers 400 ms late, as on a slow phone connection.
    b.evaluate("""(() => { const real = window.fetch; window.pending = 0;
      window.fetch = (...args) => { window.pending++;
        return new Promise(r => setTimeout(r, 400)).then(() => real(...args)).finally(() => { window.pending--; }); };
      return true; })()""")
    focus = "document.querySelector('[data-scan-input]').focus()"
    # Two different codes scanned back to back, the second while the first is still being looked up.
    b.evaluate(focus)
    _type(b, COUPLING)
    _key(b, 'Enter')
    assert b.evaluate("document.querySelector('[data-scan-input]').value") == ''   # cleared at once
    _type(b, '0036000291452')
    _key(b, 'Enter')
    _settle(b)
    assert b.evaluate(LINES) == [['Copper Coupling', '1'], ['Service Call Kit', '1']]
    # A double Enter on one scan counts once; two scans typed count twice.
    _type(b, COUPLING)
    _key(b, 'Enter')
    _key(b, 'Enter')
    _settle(b)
    assert b.evaluate(LINES) == [['Copper Coupling', '2'], ['Service Call Kit', '1']]
    _type(b, COUPLING)
    _key(b, 'Enter')
    _type(b, COUPLING)
    _key(b, 'Enter')
    _settle(b)
    assert b.evaluate(LINES) == [['Copper Coupling', '4'], ['Service Call Kit', '1']]
    # Focus moved away mid-scan stays where the person put it.
    _type(b, COUPLING)
    _key(b, 'Enter')
    b.evaluate("document.querySelector('[name=\"label:f:customer\"]').focus()")
    _settle(b)
    assert b.evaluate("document.activeElement.name") == 'label:f:customer'
    assert b.evaluate(LINES) == [['Copper Coupling', '5'], ['Service Call Kit', '1']]
    # 0* adds nothing and is refused with a message; what was typed stays.
    b.evaluate(focus)
    _type(b, '0*' + COUPLING)
    _key(b, 'Enter')
    _settle(b)
    assert 'above 0' in b.evaluate("document.querySelector('[data-scan-status]').innerText")
    assert b.evaluate("document.querySelector('[data-scan-input]').value") == '0*' + COUPLING
    assert b.evaluate(LINES) == [['Copper Coupling', '5'], ['Service Call Kit', '1']]
    assert b.evaluate('window.submits') == 0
