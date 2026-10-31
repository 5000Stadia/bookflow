"""R114 increment 5: the POS screen in real headless Chrome, at a phone's 390 px and a desktop's 1440 px.

At 390 px a clerk rings up by tapping a group then an item, by looking an item up, and by scanning (a keyboard-mode
scanner's "2*" and Enter, and a code handed to the Scan field the way the camera hands one); the sale sheet slides up
from the bar at the foot. A reload mid-sale keeps the cart. The customer chip adds and chooses a customer; Charge
takes cash from a quick amount, shows the change due, and Complete posts one sales receipt, then the change screen.

At 1440 px the sale stands beside the boxes. A Complete whose answer is lost leaves the sale kept on the device with
"Finish the sale"; after a reload, finishing it returns the sale already posted, and only one receipt exists.
Screenshots of the screen, the cart and the change screen go to BF_PICTURE_SHOTS when it is set.
"""
import base64
import json
import os
import time
from pathlib import Path

import pytest

from tests.test_item_pictures_browser import _fits
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

SEALANT = '4006381334020'
COUPLING = '0012345678905'


def _shot(b, width, name):
    """The screen as the clerk sees it (the viewport: the sale sheet and the change screen are fixed layers)."""
    folder = os.environ.get('BF_PICTURE_SHOTS')
    if not folder:
        return
    time.sleep(0.4)   # the sheet's slide
    Path(folder).mkdir(parents=True, exist_ok=True)
    b.evaluate('document.activeElement && document.activeElement.blur()')
    (Path(folder) / f'{name}-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'format': 'png'})['data']))


def _signed_in(tmp_path, site, width, height):
    b = _Cdp(tmp_path / f'pos-chrome-{width}')
    b.viewport(width, height)
    b.navigate(site.base_url + '/login')
    b.evaluate(f"""(() => {{
      document.querySelector('[name="username"]').value = {json.dumps(site.login)};
      document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
      document.querySelector('form[hx-post="/login"]').requestSubmit(); }})()""")
    b.wait_for("location.pathname.startsWith('/c/')", timeout=30)
    return b


def _pos_on(b, site):
    info = _command(b, site, 'company.show', {})
    _command(b, site, 'company.update', {'pos_enabled': True, 'expected_version': info['info_version']},
             **{'X-Bookflow-Reason': 'POS test'})


def _open_pos(b, site):
    b.navigate(f'{site.base_url}/c/{site.company_id}/pos')
    b.wait_for('!!window.bookflowPos && !!window.bookflowScan && !!document.querySelector("[data-pos-boxes] .pos-box")',
               timeout=30)


def _lines(b):
    return b.evaluate('window.bookflowPos.state().lines.map(l => [l.name, l.quantity])')


def _confirmed(b):
    b.wait_for('document.querySelector("[data-pos-total]").dataset.state === "confirmed"', timeout=20)
    return b.evaluate('window.bookflowPos.server().total.amount')


def _receipts(b, site):
    return len(_command(b, site, 'sales-receipt.query', {'limit': 200})['items'])


def test_ring_up_by_tap_lookup_and_scan_then_cash_and_the_change_screen_at_390(browser_site, tmp_path):
    site = browser_site
    b = _signed_in(tmp_path, site, 390, 844)
    try:
        _pos_on(b, site)
        before = _receipts(b, site)
        _open_pos(b, site)
        _fits(b)
        assert b.evaluate('getComputedStyle(document.querySelector("[data-pos-bar]")).position') == 'fixed'
        # Block style: two boxes a row on a phone. The home is the starting layout: a group per top category.
        assert b.evaluate('getComputedStyle(document.querySelector("[data-pos-boxes]")).gridTemplateColumns.split(" ").length') == 2
        assert b.evaluate('!!document.querySelector("[data-pos-box-group=\\"Plumbing Parts\\"]")')
        _shot(b, 390, 'pos-screen')
        # By tap: into the group, then the valve; the line shows at once, then the host's total.
        b.evaluate('document.querySelector("[data-pos-box-group=\\"Plumbing Parts\\"]").click()')
        b.wait_for('document.querySelector("[data-pos-crumbs]").textContent.includes("Plumbing Parts")')
        valve = b.evaluate('[...document.querySelectorAll("[data-pos-box-item]")].find(x => x.textContent.includes("Brass Shutoff Valve")).dataset.posBoxItem')
        b.evaluate(f'document.querySelector("[data-pos-box-item=\\"{valve}\\"]").click()')
        assert _lines(b) == [['Brass Shutoff Valve', '1']]
        b.evaluate(f'document.querySelector("[data-pos-box-item=\\"{valve}\\"]").click()')
        assert _lines(b) == [['Brass Shutoff Valve', '2']]   # one more on its line
        # By lookup: part of a name lists matches; tapping one rings it up.
        b.evaluate("""(() => { const i = document.querySelector('[data-scan-input]'); i.value = 'clearing';
          i.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        b.wait_for('!document.querySelector("[data-pos-results]").hidden')
        b.evaluate('[...document.querySelectorAll("[data-pos-result]")].find(x => x.textContent.includes("Mainline Clearing")).click()')
        assert _lines(b)[-1] == ['Mainline Clearing', '1']
        assert b.evaluate('document.querySelector("[data-pos-results]").hidden')
        # By scan: a keyboard-mode scanner types "2*" and the code, then Enter.
        b.evaluate(f"""(() => {{ const i = document.querySelector('[data-scan-input]'); i.focus(); i.value = '2*{SEALANT}';
          i.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); }})()""")
        b.wait_for('window.bookflowPos.state().lines.length === 3', timeout=15)
        assert _lines(b)[-1] == ['Pipe Sealant', '2']
        # And a code handed over as the camera hands one (camera-scan.js -> bookflowScan.scan).
        b.evaluate(f'window.bookflowScan.scan(document.querySelector("[data-scan][data-scan-mode=pos]"), "{COUPLING}")')
        b.wait_for('window.bookflowPos.state().lines.length === 4', timeout=15)
        assert _lines(b)[-1] == ['Copper Coupling', '1']
        total = _confirmed(b)
        # §12's timings, as this headless desktop Chrome sees them (a phone on Wi-Fi is Kabe's trial): a tap's line,
        # and the host's total for this cart.
        tap_ms = b.evaluate(f'(() => {{ const t = performance.now(); document.querySelector("[data-pos-box-item=\\"{valve}\\"]").click(); '
                            'const n = document.querySelectorAll("[data-pos-line]").length; return [performance.now() - t, n]; })()')
        preview_ms = b.evaluate('''(async () => { const body = JSON.stringify({lines: window.bookflowPos.state().lines.map(l => ({item: l.item_id, quantity: l.quantity}))});
          const times = []; for (let i = 0; i < 5; i++) { const t = performance.now();
            await fetch(location.pathname.replace(/^\\/c\\/([^/]+)\\/pos$/, '/companies/$1/commands/pos.sale.preview'), {method: 'POST', credentials: 'same-origin',
              headers: {'Content-Type': 'application/json', 'X-Bookflow-Workbench': '1'}, body}).then(r => r.json());
            times.push(performance.now() - t); } return times.sort((a, b) => a - b)[2]; })()''', await_promise=True)
        b.evaluate('document.querySelector("[data-pos-line]").click()')
        b.wait_for('document.querySelector("[data-pos-line-dialog]").open')
        b.evaluate('document.querySelector("[data-pos-line-qty]").value = "2"; document.querySelector("[data-pos-line-done]").click()')
        assert _lines(b)[0] == ['Brass Shutoff Valve', '2']
        total = _confirmed(b)
        shown = _command(b, site, 'pos.sale.preview', {'lines': [
            {'item': valve, 'quantity': '2'}, {'item': 'Mainline Clearing', 'quantity': '1'},
            {'item': 'Pipe Sealant', 'quantity': '2'}, {'item': 'Copper Coupling', 'quantity': '1'}]})
        assert shown['total']['amount'] == total
        assert b.evaluate('document.querySelector("[data-pos-bar-count]").textContent') == '6 items'
        # A reload mid-sale keeps the cart, ticket and all.
        ticket = b.evaluate('window.bookflowPos.state().ticket')
        _open_pos(b, site)
        assert len(_lines(b)) == 4 and b.evaluate('window.bookflowPos.state().ticket') == ticket
        _confirmed(b)
        # The sale sheet slides up from the bar.
        b.evaluate('document.querySelector("[data-pos-bar]").click()')
        b.wait_for('document.querySelector("[data-pos]").classList.contains("pos-sheet-open")')
        b.wait_for('document.querySelector("[data-pos-cart]").getBoundingClientRect().bottom <= innerHeight + 1')
        # The customer chip: add one, chosen at once; the total is asked for again.
        b.evaluate('document.querySelector("[data-pos-customer]").click()')
        b.wait_for('document.querySelector("[data-pos-customer-dialog]").open')
        b.evaluate("""(() => { document.querySelector('[data-pos-customer-new]').open = true;
          document.querySelector('[data-pos-new-name]').value = 'Casey Counter';
          document.querySelector('[data-pos-new-phone]').value = '555-0142';
          document.querySelector('[data-pos-customer-add]').click(); })()""")
        b.wait_for('!document.querySelector("[data-pos-customer-dialog]").open')
        assert b.evaluate('document.querySelector("[data-pos-customer]").textContent') == 'Casey Counter'
        total = _confirmed(b)
        _shot(b, 390, 'pos-cart')
        _fits(b)
        # Charge: cash, a quick amount above the total, the change due, Complete.
        b.evaluate('document.querySelector("[data-pos-charge]").click()')
        b.wait_for('document.querySelector("[data-pos-tender]").open')
        assert b.evaluate('document.querySelector("[data-pos-method][aria-pressed=true]").textContent') == 'Cash'
        assert b.evaluate('document.querySelector("[data-pos-complete]").disabled')
        quick = b.evaluate('[...document.querySelectorAll("[data-pos-quick-amount]")].map(x => x.dataset.posQuickAmount)')
        assert quick[0] == total and all(float(q) > float(total) for q in quick[1:])
        pay = quick[-1]
        b.evaluate(f'document.querySelector("[data-pos-quick-amount=\\"{pay}\\"]").click()')
        change = f'{float(pay) - float(total):.2f}'
        assert change.split('.')[0] in b.evaluate('document.querySelector("[data-pos-change-due]").textContent')
        assert b.evaluate('[...document.querySelectorAll("[data-pos-keypad] button")].every(k => k.getBoundingClientRect().height >= 64)')
        b.evaluate('window.__t0 = performance.now(); document.querySelector("[data-pos-complete]").click()')
        b.wait_for('!document.querySelector("[data-pos-done]").hidden', timeout=20)
        complete_ms = b.evaluate('performance.now() - window.__t0')
        number = b.evaluate('document.querySelector("[data-pos-done-number]").textContent')
        assert change.split('.')[0] in b.evaluate('document.querySelector("[data-pos-done-change]").textContent')
        _shot(b, 390, 'pos-change')
        receipt = _command(b, site, 'sales-receipt.show', {'sales_receipt': number})
        assert receipt['total']['amount'] == total and receipt['customer_name'] == 'Casey Counter'
        assert _receipts(b, site) == before + 1
        print(f'POS timings (headless desktop Chrome, loopback): tap to line {tap_ms[0]:.1f} ms, '
              f'server total {preview_ms:.0f} ms (median of 5), Complete to change screen {complete_ms:.0f} ms')
        assert tap_ms[0] < 100
        # New sale: an empty cart and a new ticket.
        b.evaluate('document.querySelector("[data-pos-new-sale]").click()')
        assert _lines(b) == [] and b.evaluate('window.bookflowPos.state().ticket') != ticket
        _fits(b)
    finally:
        b.close()


def test_a_lost_answer_is_finished_after_a_reload_and_posts_once_at_1440(browser_site, tmp_path):
    site = browser_site
    b = _signed_in(tmp_path, site, 1440, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        # The sale stands beside the boxes.
        assert b.evaluate('getComputedStyle(document.querySelector("[data-pos-bar]")).display') == 'none'
        cart = b.evaluate('document.querySelector("[data-pos-cart]").getBoundingClientRect().left')
        assert cart > b.evaluate('document.querySelector("[data-pos-boxes]").getBoundingClientRect().right') - 1
        _shot(b, 1440, 'pos-screen')
        b.evaluate('[...document.querySelectorAll("[data-pos-box-item]")][0].click()')
        b.evaluate("""(() => { const i = document.querySelector('[data-scan-input]'); i.value = 'valve';
          i.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        b.wait_for('!document.querySelector("[data-pos-results]").hidden')
        b.evaluate('document.querySelector("[data-pos-result]").click()')
        _confirmed(b)
        _shot(b, 1440, 'pos-cart')
        before = _receipts(b, site)
        # The host completes the sale, but its answer never arrives.
        b.evaluate("""(() => { const real = window.fetch; window.fetch = async (url, init) => {
          const response = await real(url, init);
          if (String(url).endsWith('/pos.sale.complete')) { window.__posted = true; throw new TypeError('Failed to fetch'); }
          return response; }; })()""")
        b.evaluate('document.querySelector("[data-pos-charge]").click()')
        b.wait_for('document.querySelector("[data-pos-tender]").open')
        visa = b.evaluate('[...document.querySelectorAll("[data-pos-method]")].find(x => x.textContent === "Visa").dataset.posMethod')
        b.evaluate(f'document.querySelector("[data-pos-method=\\"{visa}\\"]").click()')
        assert not b.evaluate('document.querySelector("[data-pos-reference-row]").hidden')
        b.evaluate('document.querySelector("[data-pos-reference]").value = "4242"')
        b.evaluate('document.querySelector("[data-pos-complete]").click()')
        b.wait_for('window.__posted === true && document.querySelector("[data-pos-tender-status]").textContent.includes("No connection")',
                   timeout=20)
        assert _receipts(b, site) == before + 1
        # Reload: the sale is kept here, locked, with Finish the sale.
        _open_pos(b, site)
        b.wait_for('!document.querySelector("[data-pos-pending]").hidden')
        assert b.evaluate('document.querySelector("[data-pos-charge]").disabled')
        b.evaluate('document.querySelector("[data-pos-retry]").click()')
        b.wait_for('!document.querySelector("[data-pos-done]").hidden', timeout=20)
        assert 'already completed' in b.evaluate('document.querySelector("[data-pos-done-summary]").textContent')
        assert b.evaluate('document.querySelector("[data-pos-done-change]").textContent').endswith('0.00')
        _shot(b, 1440, 'pos-change')
        assert _receipts(b, site) == before + 1
        assert b.evaluate('window.bookflowPos.state().lines.length') == 0
        assert b.evaluate('document.querySelector("[data-pos-pending]").hidden')
    finally:
        b.close()
