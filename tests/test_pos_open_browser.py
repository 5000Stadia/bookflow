"""An abandoned open ticket remains recoverable from another browser device."""
import base64
import json
from pathlib import Path

import pytest

from tests.test_item_pictures_browser import _fits
from tests.test_pos_sale_browser import _signed_in, _pos_on, _open_pos, _confirmed, _receipts
from tests.test_pos_ticket_browser import click, lookup, hold
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def confirmations(b, accepted):
    b.evaluate(f'''window.__confirmations = []; window.confirm = message => {{
        window.__confirmations.push(message); return {json.dumps(accepted)};
    }}''')


def open_held(b):
    click(b, '[data-pos-held]')
    b.wait_for('document.querySelector("[data-pos-held-status]").textContent === "" && !!document.querySelector("[data-pos-resume]")')


@pytest.mark.parametrize('width', [390, 1440])
def test_device_loss_takeover_confirmation_stale_completion_and_open_discard(browser_site, tmp_path, width):
    site = browser_site
    first = _signed_in(tmp_path, site, width, 900)
    second = None
    try:
        _pos_on(first, site)
        _open_pos(first, site)
        before = _receipts(first, site)
        lookup(first)
        hold(first, 'Lost device sale')
        first.wait_for('window.bookflowPos.state().lines.length === 0', timeout=20)
        open_held(first)
        click(first, '[data-pos-resume]')
        first.wait_for('window.bookflowPos.state().ticket_version === 2', timeout=20)
        _confirmed(first)
        ticket = first.evaluate('window.bookflowPos.state().ticket')
        # Device 1 becomes unavailable; its local cart remains at version 2.
        second = _signed_in(tmp_path / 'replacement', site, width, 900)
        _open_pos(second, site)
        open_held(second)
        text = second.evaluate('document.querySelector("[data-pos-held-list]").textContent')
        assert 'In use on Main by ' in text and 'since ' in text and 'Take over' in text
        confirmations(second, False)
        click(second, '[data-pos-resume]')
        prompts = second.evaluate('window.__confirmations')
        assert len(prompts) == 1 and 'In use on Main by ' in prompts[0] and 'take it over?' in prompts[0]
        assert second.evaluate('window.bookflowPos.state().lines.length') == 0
        assert _command(second, site, 'pos.ticket.list', {})['items'][0]['version'] == 2
        confirmations(second, True)
        click(second, '[data-pos-resume]')
        second.wait_for('window.bookflowPos.state().ticket_version === 3', timeout=20)
        assert len(second.evaluate('window.__confirmations')) == 1
        assert second.evaluate('window.bookflowPos.state().ticket') == ticket
        _confirmed(second)
        _fits(second)
        folder = Path('/tmp/m4-shots/pos8')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'takeover-{width}.png').write_bytes(base64.b64decode(
            second.call('Page.captureScreenshot', {'format': 'png'})['data']))
        # The old device reappears and tries to charge its stale copy.
        click(first, '[data-pos-charge]')
        click(first, '[data-pos-quick-amount]')
        click(first, '[data-pos-complete]')
        first.wait_for('!!window.bookflowPos.state().stale_ticket', timeout=20)
        assert _receipts(second, site) == before
        click(second, '[data-pos-charge]')
        click(second, '[data-pos-quick-amount]')
        click(second, '[data-pos-complete]')
        second.wait_for('!document.querySelector("[data-pos-done]").hidden', timeout=20)
        assert _receipts(second, site) == before + 1
        # A second abandoned open sale can also be discarded, with one confirmation.
        held = _command(second, site, 'pos.ticket.hold', {
            'ticket': 'abandoned-discard', 'device': 'other-device',
            'label': 'Discard from another device', 'lines': [{'item': 'Pipe Sealant'}]})
        _command(second, site, 'pos.ticket.resume', {
            'ticket': held['id'], 'expected_version': held['version'], 'device': 'other-device'})
        _open_pos(second, site)
        open_held(second)
        confirmations(second, False)
        click(second, '[data-pos-discard]')
        assert len(second.evaluate('window.__confirmations')) == 1
        assert 'In use on Main by ' in second.evaluate('window.__confirmations[0]')
        assert _command(second, site, 'pos.ticket.list', {})['count'] == 1
        confirmations(second, True)
        click(second, '[data-pos-discard]')
        second.wait_for('!document.querySelector("[data-pos-discard]")', timeout=20)
        assert len(second.evaluate('window.__confirmations')) == 1
        assert _command(second, site, 'pos.ticket.list', {})['count'] == 0
        assert _receipts(second, site) == before + 1
    finally:
        if second:
            second.close()
        first.close()
