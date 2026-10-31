"""Hold on one device, resume on another, and recover uncertain ticket changes in real Chrome."""
import base64
import json
from pathlib import Path

import pytest

from tests.test_item_pictures_browser import _fits
from tests.test_pos_sale_browser import _signed_in, _pos_on, _open_pos, _confirmed, _lines, _receipts
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def click(b, selector):
    b.evaluate(f'document.querySelector({json.dumps(selector)}).click()')


def lookup(b, name='Mainline Clearing'):
    b.evaluate(f'''(() => {{const input = document.querySelector('[data-scan-input]'); input.value = {json.dumps(name)};
        input.dispatchEvent(new Event('input', {{bubbles: true}}));}})()''')
    b.wait_for('!!document.querySelector("[data-pos-result]")')
    click(b, '[data-pos-result]')
    return _confirmed(b)


def hold(b, label):
    click(b, '[data-pos-hold]')
    b.evaluate(f'document.querySelector("[data-pos-hold-label]").value = {json.dumps(label)}')
    b.evaluate('document.querySelector("[data-pos-hold-form]").requestSubmit()')


def shot(b, name, width):
    folder = Path('/tmp/m4-shots/pos7')
    folder.mkdir(parents=True, exist_ok=True)
    b.evaluate('document.activeElement && document.activeElement.blur()')
    (folder / f'{name}-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'format': 'png'})['data']))


@pytest.mark.parametrize('width', [390, 1440])
def test_hold_reload_resume_on_another_device_reprice_complete_and_void(browser_site, tmp_path, width):
    site = browser_site
    first = _signed_in(tmp_path, site, width, 900)
    second = None
    try:
        _pos_on(first, site)
        _open_pos(first, site)
        before = _receipts(first, site)
        old_total = lookup(first)
        ticket = first.evaluate('window.bookflowPos.state().ticket')
        if width == 390:
            click(first, '[data-pos-bar]')
        hold(first, 'Alex — back after lunch')
        first.wait_for('window.bookflowPos.state().lines.length === 0', timeout=20)
        assert _receipts(first, site) == before
        _open_pos(first, site)
        click(first, '[data-pos-held]')
        first.wait_for('!!document.querySelector("[data-pos-resume]")', timeout=20)
        assert 'Alex — back after lunch' in first.evaluate('document.querySelector("[data-pos-held-list]").textContent')
        assert 'Main' in first.evaluate('document.querySelector("[data-pos-held-list]").textContent')
        _fits(first)
        first.wait_for('document.querySelector("[data-pos-held-status]").textContent === ""')
        shot(first, 'held', width)
        # A later price is applied when another device resumes, not stored as a typed override.
        item = _command(first, site, 'item.show', {'item': 'Mainline Clearing'})
        _command(first, site, 'item.update', {'item': item['id'], 'expected_version': item['version'], 'price': '217.00'},
                 **{'X-Bookflow-Reason': 'price changed while customer away'})
        second = _signed_in(tmp_path / 'second-device', site, width, 900)
        _open_pos(second, site)
        click(second, '[data-pos-held]')
        second.wait_for('!!document.querySelector("[data-pos-resume]")')
        click(second, '[data-pos-resume]')
        second.wait_for('window.bookflowPos.state().lines.length === 1', timeout=20)
        assert second.evaluate('window.bookflowPos.state().ticket') == ticket
        assert second.evaluate('window.bookflowPos.state().ticket_version') == 2
        assert _confirmed(second) != old_total
        assert _lines(second) == [['Mainline Clearing', '1']]
        _fits(second)
        shot(second, 'resumed', width)
        # The first device's stale list cannot take the same held ticket a second time.
        click(first, '[data-pos-resume]')
        first.wait_for('document.querySelector("[data-pos-held-status]").textContent.length > 0 && !window.bookflowPos.state().mutation', timeout=20)
        assert first.evaluate('window.bookflowPos.state().lines.length') == 0
        # Reload keeps the resumed ticket's claim/version and the cart, then payment consumes it once.
        _open_pos(second, site)
        assert second.evaluate('window.bookflowPos.state().ticket_version') == 2
        _confirmed(second)
        click(second, '[data-pos-charge]')
        click(second, '[data-pos-quick-amount]')
        click(second, '[data-pos-complete]')
        second.wait_for('!document.querySelector("[data-pos-done]").hidden', timeout=20)
        assert _receipts(second, site) == before + 1
        receipt_number = second.evaluate('document.querySelector("[data-pos-done-number]").textContent')
        click(second, '[data-pos-void-last]')
        reason = '客户取消 — customer’s literal %20 reference'
        second.evaluate(f'document.querySelector("[data-pos-void-reason]").value = {json.dumps(reason)}')
        click(second, '[data-pos-void-submit]')
        second.wait_for('document.querySelector("[data-pos-void-status]").textContent.includes("voided")', timeout=20)
        _fits(second)
        shot(second, 'voided', width)
        assert _command(second, site, 'sales-receipt.show', {'sales_receipt': receipt_number})['status'] == 'voided'
        events = _command(second, site, 'audit.list', {'command': 'pos sale void', 'limit': 20})['items']
        assert events and events[0]['reason'] == reason
    finally:
        if second:
            second.close()
        first.close()


@pytest.mark.parametrize('lost_action', ['hold', 'resume'])
def test_ticket_write_lost_answer_recovers_after_reload_without_losing_cart(browser_site, tmp_path, lost_action):
    site = browser_site
    b = _signed_in(tmp_path, site, 390, 844)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        lookup(b)
        ticket = b.evaluate('window.bookflowPos.state().ticket')
        if lost_action == 'resume':
            hold(b, 'Keep this cart')
            b.wait_for('window.bookflowPos.state().lines.length === 0', timeout=20)
            click(b, '[data-pos-held]')
            b.wait_for('!!document.querySelector("[data-pos-resume]")')
        b.evaluate(f'''(() => {{const real = window.fetch; window.fetch = async (url, init) => {{
            const answer = await real(url, init);
            if (String(url).endsWith('/pos.ticket.{lost_action}')) throw new TypeError('Lost answer');
            return answer;
        }};}})()''')
        if lost_action == 'hold':
            hold(b, 'Keep this cart')
        else:
            click(b, '[data-pos-resume]')
        b.wait_for('!!window.bookflowPos.state().mutation && document.querySelector("[data-pos-connection]").textContent.includes("No confirmation")', timeout=20)
        assert b.evaluate('document.querySelector("[data-pos-charge]").disabled')
        _open_pos(b, site)
        click(b, '[data-pos-ticket-retry]')
        b.wait_for('!window.bookflowPos.state().mutation', timeout=20)
        if lost_action == 'hold':
            assert b.evaluate('window.bookflowPos.state().lines.length') == 0
            held = _command(b, site, 'pos.ticket.list', {})['items']
            assert len(held) == 1 and held[0]['id'] == ticket
        else:
            assert b.evaluate('window.bookflowPos.state().ticket') == ticket
            assert _lines(b) == [['Mainline Clearing', '1']]
            _confirmed(b)
            opened = _command(b, site, 'pos.ticket.list', {})['items']
            assert len(opened) == 1 and opened[0]['id'] == ticket and opened[0]['status'] == 'open'
    finally:
        b.close()


def test_resume_preserves_current_cart_and_discard_requires_confirmation(browser_site, tmp_path):
    site = browser_site
    b = _signed_in(tmp_path, site, 1440, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        lookup(b)
        hold(b, 'Waiting customer')
        b.wait_for('window.bookflowPos.state().lines.length === 0', timeout=20)
        lookup(b, 'Pipe Sealant')
        current = b.evaluate('window.bookflowPos.state().ticket')
        click(b, '[data-pos-held]')
        b.wait_for('!!document.querySelector("[data-pos-resume]")')
        click(b, '[data-pos-resume]')
        assert b.evaluate('window.bookflowPos.state().ticket') == current
        assert 'Hold or clear' in b.evaluate('document.querySelector("[data-pos-held-status]").textContent')
        b.evaluate('window.confirm = () => {window.__confirmations = (window.__confirmations || 0) + 1; return false;}')
        click(b, '[data-pos-discard]')
        assert b.evaluate('window.__confirmations') == 1
        assert len(_command(b, site, 'pos.ticket.list', {})['items']) == 1
        b.evaluate('window.confirm = () => {window.__confirmations++; return true;}')
        click(b, '[data-pos-discard]')
        b.wait_for('!document.querySelector("[data-pos-discard]")', timeout=20)
        assert b.evaluate('window.__confirmations') == 2
        assert b.evaluate('window.bookflowPos.state().ticket') == current
        assert _lines(b) == [['Pipe Sealant', '1']]
    finally:
        b.close()


def test_a_changed_resumed_ticket_can_be_forgotten_without_changing_host(browser_site, tmp_path):
    site = browser_site
    b = _signed_in(tmp_path, site, 1440, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        lookup(b)
        hold(b, 'Changed elsewhere')
        b.wait_for('window.bookflowPos.state().lines.length === 0', timeout=20)
        click(b, '[data-pos-held]')
        b.wait_for('!!document.querySelector("[data-pos-resume]")')
        click(b, '[data-pos-resume]')
        b.wait_for('window.bookflowPos.state().ticket_version === 2', timeout=20)
        state = b.evaluate('window.bookflowPos.state()')
        _command(b, site, 'pos.ticket.discard', {'ticket': state['ticket'], 'expected_version': 2})
        b.evaluate('window.confirm = () => true')
        click(b, '[data-pos-clear]')
        b.wait_for('!!document.querySelector("[data-pos-forget]")', timeout=20)
        _open_pos(b, site)
        b.evaluate('window.confirm = () => true')
        click(b, '[data-pos-forget]')
        b.wait_for('window.bookflowPos.state().lines.length === 0')
        assert _command(b, site, 'pos.ticket.list', {})['items'] == []
    finally:
        b.close()
