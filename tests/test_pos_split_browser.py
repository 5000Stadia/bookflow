"""Split payments, reload/retry and all tenders on the thermal receipt in real Chrome."""
import base64
import json
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_item_pictures_browser import _fits
from tests.test_pos_sale_browser import _signed_in, _pos_on, _open_pos, _confirmed
from tests.test_pos_ticket_browser import click, lookup
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _amount(b, value):
    b.evaluate(f'''(() => {{const input = document.querySelector('[data-pos-tendered]');
      input.value = {json.dumps(value)}; input.dispatchEvent(new Event('input', {{bubbles: true}}));}})()''')


def _method(b, name):
    b.evaluate(f'[...document.querySelectorAll("[data-pos-method]")].find(x => x.textContent === {json.dumps(name)}).click()')


def _press(b, selector):
    """Press the visible control, including its native submitter and focus behavior."""
    position = b.evaluate(f'''(() => {{const button = document.querySelector({json.dumps(selector)});
      button.scrollIntoView({{block:'center'}}); const r = button.getBoundingClientRect();
      return {{x:r.x + r.width / 2, y:r.y + r.height / 2}};}})()''')
    b.call('Input.dispatchMouseEvent', dict(type='mousePressed', button='left', clickCount=1, **position))
    b.call('Input.dispatchMouseEvent', dict(type='mouseReleased', button='left', clickCount=1, **position))


def _shot(b, name, width):
    folder = Path('/tmp/m4-shots/pos11')
    folder.mkdir(parents=True, exist_ok=True)
    b.evaluate('document.activeElement && document.activeElement.blur()')
    (folder / f'{name}-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'format': 'png'})['data']))


@pytest.mark.parametrize('width', [390, 1440])
@pytest.mark.parametrize('parts', [2, 3])
def test_split_change_reload_retry_and_receipt(browser_site, tmp_path, width, parts):
    site = browser_site
    b = _signed_in(tmp_path, site, width, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        total = Decimal(lookup(b))
        ticket = b.evaluate('window.bookflowPos.state().ticket')
        if width == 390:
            click(b, '[data-pos-bar]')
        click(b, '[data-pos-charge]')
        _method(b, 'Visa')
        # Card cannot create cash change, and malformed/negative amounts cannot advance.
        for invalid in [str(total + 1), '-1', 'not money', '1.234']:
            _amount(b, invalid)
            assert b.evaluate('document.querySelector("[data-pos-complete]").disabled')
            assert b.evaluate('document.querySelector("[data-pos-add-payment]").disabled')
        _amount(b, '10.00')
        b.evaluate('document.querySelector("[data-pos-reference]").value = "4242"')
        assert b.evaluate('document.querySelector("[data-pos-complete]").disabled')
        _press(b, '[data-pos-add-payment]')
        assert b.evaluate('document.querySelectorAll("[data-pos-payments] li").length') == 1
        assert '4242' in b.evaluate('document.querySelector("[data-pos-payments]").textContent')
        assert b.evaluate('[...document.querySelectorAll("[data-pos-method]")].find(x => x.textContent === "Visa").disabled')
        # Partial tender drafts survive a reload, and resuming the same cart keeps its key.
        _open_pos(b, site)
        _confirmed(b)
        if width == 390:
            click(b, '[data-pos-bar]')
        click(b, '[data-pos-charge]')
        assert b.evaluate('window.bookflowPos.state().ticket') == ticket
        assert b.evaluate('document.querySelectorAll("[data-pos-payments] li").length') == 1
        assert '4242' in b.evaluate('document.querySelector("[data-pos-payments]").textContent')
        if parts == 3:
            name = b.evaluate('''JSON.parse(document.querySelector('#pos-data').textContent).registers[0].methods
              .find(m => m.kind !== 'cash' && m.name !== 'Visa').name''')
            _method(b, name)
            _amount(b, '5.00')
            _press(b, '[data-pos-add-payment]')
        _method(b, 'Cash')
        remaining = total - Decimal(10 + (5 if parts == 3 else 0))
        assert b.evaluate('document.querySelector("[data-pos-quick-amount]").dataset.posQuickAmount') == f'{remaining:.2f}'
        _amount(b, f'{remaining + 2:.2f}')
        assert b.evaluate('document.querySelector("[data-pos-change-due]").textContent').endswith('2.00')
        assert not b.evaluate('document.querySelector("[data-pos-complete]").disabled')
        _fits(b)
        _shot(b, f'{parts}-tenders', width)
        # Record the posted response, and lose it in the three-tender case to exercise permanent retry.
        b.evaluate('''(() => {const real = window.fetch; window.fetch = async (url, init) => {
          const response = await real(url, init);
          if (String(url).endsWith('/pos.sale.complete')) {
            window.__splitPosted = await response.clone().json();
            if (window.__loseSplitAnswer) throw new TypeError('Lost answer');
          } return response;
        };})()''')
        b.evaluate(f'window.__loseSplitAnswer = {json.dumps(parts == 3)}')
        _press(b, '[data-pos-complete]')
        b.wait_for('!!window.__splitPosted', timeout=30)
        assert 'id' in b.evaluate('window.__splitPosted'), b.evaluate('window.__splitPosted')
        if parts == 3:
            b.wait_for('document.querySelector("[data-pos-tender-status]").textContent.includes("No connection")', timeout=30)
            posted = b.evaluate('window.__splitPosted')
            assert b.evaluate('document.querySelector("[data-pos-tendered]").disabled && document.querySelector("[data-pos-complete]").disabled')
            _open_pos(b, site)
            click(b, '[data-pos-retry]')
        b.wait_for('!document.querySelector("[data-pos-done]").hidden', timeout=30)
        if parts == 2:
            posted = b.evaluate('window.__splitPosted')
        else:
            assert 'already completed' in b.evaluate('document.querySelector("[data-pos-done-summary]").textContent')
        assert len(posted['tenders']) == parts
        assert posted['change']['amount'] == '2.00'
        href = b.evaluate('document.querySelector("[data-pos-print]").href')
        assert posted['id'] in href
        assert b.evaluate('document.querySelector("[data-pos-done-change]").textContent').endswith('2.00')
        invoice = _command(b, site, 'invoice.show', {'invoice': posted['sales_receipt']['id']})
        assert invoice['total']['amount'] == str(total)
        if parts == 3:
            click(b, '[data-pos-void-last]')
            b.evaluate('document.querySelector("[data-pos-void-reason]").value = "Split payment void"')
            _press(b, '[data-pos-void-submit]')
            b.wait_for('document.querySelector("[data-pos-void-status]").textContent.includes("voided")', timeout=30)
        b.navigate(href)
        b.wait_for('!!document.querySelector(".receipt .totals")')
        rows = b.evaluate('[...document.querySelectorAll(".totals .amounts")].map(x => x.textContent)')
        for tender in posted['tenders']:
            assert any(tender['payment_method']['name'] in r and tender['amount']['amount'] in r for r in rows)
        assert any('Change due' in r and '2.00' in r for r in rows)
        _fits(b)
        _shot(b, f'{parts}-tenders-receipt', width)
        if parts == 2:
            _open_pos(b, site)
            click(b, '[data-pos-refund-open]')
            b.evaluate(f'document.querySelector("[data-pos-refund-sale]").value = {json.dumps(posted["id"])}')
            _press(b, '[data-pos-refund-find] button[type=submit]')
            b.wait_for('!!document.querySelector("[data-refund-quantity]")', timeout=30)
            b.evaluate('''(() => {const q = document.querySelector('[data-refund-quantity]'); q.value = '1';
              q.dispatchEvent(new Event('input', {bubbles:true}));
              const method = document.querySelector('[data-pos-refund-settlement]'); method.value = 'original';
              method.dispatchEvent(new Event('input', {bubbles:true}));})()''')
            _press(b, '[data-pos-refund-preview]')
            b.wait_for('!document.querySelector("[data-pos-refund-complete]").disabled', timeout=30)
            assert b.evaluate('document.querySelectorAll("[data-pos-refund-tenders] li").length') == 2
            review = b.evaluate('document.querySelector("[data-pos-refund-tenders]").textContent')
            assert 'Visa' in review and 'Cash' in review
            _shot(b, 'split-refund-review', width)
            _press(b, '[data-pos-refund-complete]')
            b.wait_for('!document.querySelector("[data-pos-refund-done]").hidden', timeout=30)
            summary = b.evaluate('document.querySelector("[data-pos-refund-done-summary]").textContent')
            assert 'Visa' in summary and 'Cash' in summary
    finally:
        b.close()
