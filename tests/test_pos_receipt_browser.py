"""Receipt scan, reprint, thermal layout and the change screen in real Chrome."""
import base64
import json
from pathlib import Path
from io import BytesIO
from pypdf import PdfReader
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_pos_sale_browser import _signed_in, _pos_on, _open_pos, _confirmed
from tests.test_pos_ticket_browser import click, lookup
from tests.test_row8_register_browser import _command
from tests.test_item_pictures_browser import _fits

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def shot(b, name, width):
    path = Path('/tmp/m4-shots/pos8')
    path.mkdir(parents=True, exist_ok=True)
    (path / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format': 'png'})['data']))


@pytest.mark.parametrize('width', [390, 1440])
def test_change_receipt_barcode_reprint_and_void(browser_site, tmp_path, width):
    site = browser_site
    b = _signed_in(tmp_path, site, width, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        lookup(b)
        if width == 390: click(b, '[data-pos-bar]')
        click(b, '[data-pos-charge]')
        click(b, '[data-pos-quick-amount]')
        click(b, '[data-pos-complete]')
        b.wait_for('!document.querySelector("[data-pos-done]").hidden', timeout=30)
        _fits(b)
        shot(b, 'change', width)
        number = b.evaluate('document.querySelector("[data-pos-done-number]").textContent')
        href = b.evaluate('document.querySelector("[data-pos-print]").href')
        b.navigate(href)
        b.wait_for('!!document.querySelector(".barcode svg")')
        _fits(b)
        shot(b, 'receipt', width)
        assert b.evaluate('document.querySelectorAll(".item").length') == 1
        printed = PdfReader(BytesIO(base64.b64decode(b.call('Page.printToPDF', {'preferCSSPageSize': True})['data'])))
        assert len(printed.pages) == 1
        assert 'Mainline Clearing' in printed.pages[0].extract_text()
        original = b.evaluate('document.querySelector(".totals").textContent')
        # Rasterize the actual receipt SVG and decode with the same vendored engine used by camera Scan.
        decoded = b.evaluate('''(async () => {
          const script = document.createElement('script'); script.src = '/static/vendor/zxing-wasm/3.1.5/zxing-reader.js';
          await new Promise((ok, fail) => { script.onload = ok; script.onerror = fail; document.head.appendChild(script); });
          await ZXingWASM.prepareZXingModule({overrides: {locateFile: p => '/static/vendor/zxing-wasm/3.1.5/' + p}, fireImmediately: true});
          const svg = document.querySelector('.barcode svg');
          const image = new Image(); const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], {type:'image/svg+xml'}));
          await new Promise((ok, fail) => {image.onload=ok; image.onerror=fail; image.src=url;});
          const canvas = document.createElement('canvas'); canvas.width = image.width * 3; canvas.height = image.height * 3;
          const ctx = canvas.getContext('2d'); ctx.fillStyle = 'white'; ctx.fillRect(0,0,canvas.width,canvas.height); ctx.drawImage(image,0,0,canvas.width,canvas.height);
          const found = await ZXingWASM.readBarcodesFromImageData(ctx.getImageData(0,0,canvas.width,canvas.height), {formats:['Code128']});
          URL.revokeObjectURL(url); return found.map(x => x.text);
        })()''', await_promise=True)
        assert decoded == [number]
        _open_pos(b, site)
        click(b, '[data-pos-reprint-open]')
        b.evaluate(f'window.bookflowScan.scan(document.querySelector("[data-pos-reprint-dialog] [data-scan]"), {json.dumps(number)})')
        b.wait_for(f'document.querySelector("[data-pos-reprint-sale]").value === {json.dumps(number)}')
        click(b, '[data-pos-reprint-form] button[type=submit]')
        b.wait_for('location.pathname.includes("/pos/receipt/") && document.readyState === "complete" && !!document.querySelector(".receipt .totals")')
        assert 'Reprint' in b.evaluate('document.body.textContent')
        assert b.evaluate('document.querySelector(".totals").textContent') == original
        _open_pos(b, site)
        click(b, '[data-pos-void-open]')
        b.evaluate(f'window.bookflowScan.scan(document.querySelector("[data-pos-void-dialog] [data-scan]"), {json.dumps(decoded[0])})')
        b.wait_for(f'document.querySelector("[data-pos-void-sale]").value === {json.dumps(number)}')
        b.evaluate('document.querySelector("[data-pos-void-reason]").value = "Receipt scanned for void"; document.querySelector("[data-pos-void-form] button[type=submit]").click()')
        b.wait_for('document.querySelector("[data-pos-void-status]").textContent.includes("voided")', timeout=20)
        b.navigate(href + '?reprint=true')
        assert 'VOID' in b.evaluate('document.body.textContent')
        assert b.evaluate('document.querySelector(".totals").textContent') == original
    finally:
        b.close()


def test_sixty_lines_and_58mm_long_name_wrap_without_losing_amounts(browser_site, tmp_path):
    site = browser_site
    b = _signed_in(tmp_path, site, 390, 900)
    try:
        _pos_on(b, site)
        item = _command(b, site, 'item.show', {'item': 'Mainline Clearing'})
        name = 'Very long service name with industrial inspection and clearing details'
        _command(b, site, 'item.update', {'item': item['id'], 'expected_version': item['version'], 'name': name},
                 **{'X-Bookflow-Reason': 'thermal wrapping'})
        cart = {'lines': [{'item': item['id']} for _ in range(60)]}
        total = _command(b, site, 'pos.sale.preview', cart)['total']['amount']
        sold = _command(b, site, 'pos.sale.complete', dict(cart, ticket='sixty-line-receipt', expected_total=total,
            tender={'payment_method': 'Cash'}), **{'X-Bookflow-Reason': 'receipt length'})
        url = f'{site.base_url}/c/{site.company_id}/pos/receipt/{sold["id"]}'
        b.navigate(url)
        b.wait_for('document.querySelectorAll(".item").length === 60')
        assert b.evaluate('document.querySelector(".totals").getBoundingClientRect().top > [...document.querySelectorAll(".item")].at(-1).getBoundingClientRect().bottom')
        printed = PdfReader(BytesIO(base64.b64decode(b.call('Page.printToPDF', {'preferCSSPageSize': True, 'printBackground': True})['data'])))
        assert len(printed.pages) == 1
        text = ' '.join(printed.pages[0].extract_text().split())
        assert text.count(name) == 60
        assert text.rfind(name) < text.rfind('Total')
        assert abs(float(printed.pages[0].mediabox.width) - 80 * 72 / 25.4) < 1

        register = _command(b, site, 'pos-register.show', {'pos_register': 'Main'})
        _command(b, site, 'pos-register.update', {'pos_register':'Main', 'expected_version':register['version'], 'receipt_width':58},
                 **{'X-Bookflow-Reason': 'narrow thermal roll'})
        b.navigate(url)
        _fits(b)
        assert b.evaluate('[...document.querySelectorAll(".name")].every(x => x.getBoundingClientRect().height > 25)')
        assert b.evaluate('[...document.querySelectorAll(".item .amount")].every(x => x.getBoundingClientRect().right <= document.querySelector(".receipt").getBoundingClientRect().right)')
        assert b.evaluate('document.querySelector(".totals").textContent').find(total) >= 0
        shot(b, 'receipt-58mm', 390)
    finally:
        b.close()
