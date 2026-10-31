"""Return picker, receipt Scan, uncertain-write recovery and thermal output in real Chrome."""
import base64
import json
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_pos_sale_browser import _signed_in, _pos_on, _open_pos
from tests.test_pos_ticket_browser import click, lookup
from tests.test_row8_register_browser import _command
from tests.test_item_pictures_browser import _fits

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def fill(b, selector, value):
    b.evaluate(f'''(() => {{const input=document.querySelector({json.dumps(selector)}); input.value={json.dumps(value)};
      input.dispatchEvent(new Event('input',{{bubbles:true}}));}})()''')


def shot(b, name, width):
    folder = Path('/tmp/m4-shots/pos9')
    folder.mkdir(parents=True, exist_ok=True)
    b.evaluate('document.activeElement && document.activeElement.blur()')
    (folder / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'format':'png'})['data']))


def review(b):
    click(b, '[data-pos-refund-preview]')
    b.wait_for('!document.querySelector("[data-pos-refund-complete]").disabled',timeout=30)


@pytest.mark.parametrize('width', [390, 1440])
def test_refund_from_completed_sale_scan_partial_retry_and_receipt(browser_site, tmp_path, width):
    site = browser_site
    b = _signed_in(tmp_path, site, width, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        lookup(b)
        lookup(b)
        if width == 390: click(b, '[data-pos-bar]')
        click(b, '[data-pos-charge]')
        click(b, '[data-pos-quick-amount]')
        click(b, '[data-pos-complete]')
        b.wait_for('!document.querySelector("[data-pos-done]").hidden',timeout=30)
        number = b.evaluate('document.querySelector("[data-pos-done-number]").textContent')
        price = _command(b, site, 'pos.refund.lines', {'sale': number})['lines'][0]['unit_price']['amount']
        click(b, '[data-pos-refund-last]')
        b.wait_for('!!document.querySelector("[data-refund-quantity]")',timeout=30)
        assert 'Available to return 2' in b.evaluate('document.querySelector("[data-pos-refund-lines]").textContent')
        fill(b, '[data-refund-quantity]', '1')
        review(b)
        _fits(b)
        shot(b, 'partial-return', width)
        # The host commits, but its response is lost. A reload retries the saved request exactly.
        b.evaluate(f'window.malformedRefundAnswer = {json.dumps(width == 1440)}')
        b.evaluate('''window.refundFetch = window.fetch; window.fetch = async function(url, options) {
          const response = await window.refundFetch(url, options);
          if (String(url).endsWith('/pos.refund')) {
            if (window.malformedRefundAnswer) return new Response('null', {status:200,headers:{'Content-Type':'application/json'}});
            throw new TypeError('lost refund response');
          }
          return response;
        };''')
        click(b, '[data-pos-refund-complete]')
        b.wait_for('/No confirmation|did not confirm/.test(document.querySelector("[data-pos-refund-status]").textContent)',timeout=30)
        _open_pos(b, site)
        b.wait_for('document.querySelector("[data-pos-refund-dialog]").open')
        assert b.evaluate('document.querySelector("[data-pos-refund-editor]").disabled')
        click(b, '[data-pos-refund-complete]')
        b.wait_for('!document.querySelector("[data-pos-refund-done]").hidden',timeout=30)
        _fits(b)
        shot(b, 'refund-paid', width)
        href = b.evaluate('document.querySelector("[data-pos-refund-print]").href')
        b.navigate(href)
        b.wait_for('!!document.querySelector(".receipt")')
        assert 'REFUND' in b.evaluate('document.body.textContent')
        assert not b.evaluate('!!document.querySelector(".barcode")')
        assert 'Type the number to look up the sale' not in b.evaluate('document.body.textContent')
        assert 'Refund paid' in b.evaluate('document.body.textContent')
        assert 'Walk-in' not in b.evaluate('document.body.textContent')
        assert '× ' + price in b.evaluate('document.querySelector(".item .amounts").textContent')
        _fits(b)
        shot(b, 'refund-receipt', width)
        printed = PdfReader(BytesIO(base64.b64decode(b.call('Page.printToPDF',{'preferCSSPageSize':True})['data'])))
        assert len(printed.pages) == 1
        assert 'REFUND' in printed.pages[0].extract_text()
        assert 'refund=true' in b.evaluate('document.querySelector("nav a:last-child").href')
        saved_pdf = b.evaluate("""(async () => {
          const response = await fetch(document.querySelector('nav a:last-child').href);
          if (!response.ok) throw new Error('Refund PDF refused');
          const bytes = new Uint8Array(await response.arrayBuffer());
          return btoa(Array.from(bytes, x => String.fromCharCode(x)).join(''));
        })()""", await_promise=True)
        assert 'REFUND' in PdfReader(BytesIO(base64.b64decode(saved_pdf))).pages[0].extract_text()
        _open_pos(b, site)
        click(b, '[data-pos-refund-open]')
        b.evaluate(f'window.bookflowScan.scan(document.querySelector("[data-pos-refund-dialog] [data-scan]"),{json.dumps(number)})')
        b.wait_for(f'document.querySelector("[data-pos-refund-sale]").value === {json.dumps(number)}')
        click(b, '[data-pos-refund-find] button[type=submit]')
        b.wait_for('document.querySelector("[data-pos-refund-lines]").textContent.includes("Available to return 1")',timeout=30)
        fill(b, '[data-refund-quantity]', '1')
        review(b)
        # A new camera/scanner value must invalidate the prior sale and its confirmed total.
        b.evaluate('window.bookflowScan.scan(document.querySelector("[data-pos-refund-dialog] [data-scan]"), "another-receipt")')
        b.wait_for('document.querySelector("[data-pos-refund-sale]").value === "another-receipt"')
        assert not b.evaluate('!!document.querySelector("[data-refund-quantity]")')
        assert b.evaluate('document.querySelector("[data-pos-refund-complete]").disabled')
        b.evaluate(f'window.bookflowScan.scan(document.querySelector("[data-pos-refund-dialog] [data-scan]"), {json.dumps(number)})')
        b.wait_for(f'document.querySelector("[data-pos-refund-sale]").value === {json.dumps(number)}')
        click(b, '[data-pos-refund-find] button[type=submit]')
        b.wait_for('document.querySelector("[data-pos-refund-lines]").textContent.includes("Available to return 1")',timeout=30)
        fill(b, '[data-refund-quantity]', '1')
        review(b)
        click(b, '[data-pos-refund-complete]')
        b.wait_for('!document.querySelector("[data-pos-refund-done]").hidden',timeout=30)
        assert _command(b, site, 'pos.refund.lines', {'sale':number})['lines'][0]['available_quantity'] == '0'
    finally:
        b.close()


@pytest.mark.parametrize('width', [390, 1440])
def test_no_receipt_entered_price_store_credit_named_customer(browser_site, tmp_path, width):
    site = browser_site
    b = _signed_in(tmp_path, site, width, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        click(b, '[data-pos-refund-open]')
        click(b, '[data-pos-refund-no-receipt]')
        fill(b, '[data-pos-refund-item-search]', 'Water Heater')
        b.wait_for('!!document.querySelector("[data-refund-add]")')
        click(b, '[data-refund-add]')
        serial = f'POS-REFUND-SERIAL-{width}'
        fill(b, '[data-refund-serial]', serial)
        fill(b, '[data-pos-refund-item-search]', 'Mainline Clearing')
        b.wait_for('!!document.querySelector("[data-refund-add]")')
        click(b, '[data-refund-add]')
        assert b.evaluate('document.querySelector("[data-refund-serial]").value') == serial
        fill(b, '[data-refund-price="1"]', '12.34')
        fill(b, '[data-pos-refund-settlement]', 'store_credit')
        click(b, '[data-pos-refund-preview]')
        b.wait_for('!document.querySelector("[data-pos-refund-preview]").disabled',timeout=30)
        assert b.evaluate('document.querySelector("[data-pos-refund-complete]").disabled')
        assert 'customer' in b.evaluate('document.querySelector("[data-pos-refund-status]").textContent').lower()
        customer = _command(b, site, 'pos.customer.add', {'name':f'Refund customer {width}'}, **{'X-Bookflow-Reason':'refund customer'})
        fill(b, '[data-pos-refund-customer-search]', customer['name'])
        b.wait_for('!!document.querySelector("[data-refund-customer]")',timeout=20)
        click(b, '[data-refund-customer]')
        assert b.evaluate('document.querySelector("[data-refund-serial]").value') == serial
        review(b)
        _fits(b)
        shot(b, 'no-receipt-store-credit', width)
        click(b, '[data-pos-refund-complete]')
        b.wait_for('!document.querySelector("[data-pos-refund-done]").hidden',timeout=30)
        assert b.evaluate('document.querySelector("[data-pos-refund-done-label]").textContent') == 'Store credit issued'
        b.navigate(b.evaluate('document.querySelector("[data-pos-refund-print]").href'))
        b.wait_for('!!document.querySelector(".receipt")')
        assert 'Store credit issued' in b.evaluate('document.body.textContent')
        assert customer['name'] in b.evaluate('document.body.textContent')
        _fits(b)
    finally:
        b.close()
