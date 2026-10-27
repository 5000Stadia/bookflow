"""R112 increment 1: the Email panel, the Outbox and a message's history in real headless Chrome, at 1440 and 390.

A person opens a demo invoice, clicks Email…, sees the panel filled from the invoice template with the customer's
address and the no-connection note, changes the subject and taps Send (through htmx): the message is released with
the person's go and waits in the outbox, since nothing leaves the machine. The Outbox lists it under Ready; its page
shows the frozen PDF and its history. At a phone's width nothing scrolls sideways. With BF_DELIVERY_SHOTS set to a
folder, the screens are saved there.
"""
import base64
import os
from pathlib import Path

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), b.evaluate(
        '[...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > innerWidth + 1)'
        '.slice(0, 5).map(e => e.tagName + "." + e.className).join(" ")')


def _shot(b, width, name):
    folder = os.environ.get('BF_DELIVERY_SHOTS')
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    b.evaluate('(() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); })()')
    height = b.evaluate('Math.max(innerHeight, document.documentElement.scrollHeight)')
    (Path(folder) / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {
        'format': 'png', 'captureBeyondViewport': True,
        'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}})['data']))


@pytest.mark.parametrize('width', (1440, 390))
def test_email_an_invoice_from_its_page_and_find_it_waiting_in_the_outbox(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    invoice = run('invoice.show', {'invoice': 'DEMO-SALE-INV-ACTIVE'})
    write('customer.update', {'customer': invoice['customer_id'], 'email': 'ap@example-customer.test'})

    b.navigate(f"{base}/invoice/{invoice['id']}")
    b.wait_for('!!document.querySelector("[data-email-document]")', timeout=30)
    b.evaluate('document.querySelector("[data-email-document]").click()')
    b.wait_for('!!document.querySelector("[data-email-panel]")', timeout=30)
    text = _text(b)
    assert 'No sending connection is set up' in text and 'DEMO-SALE-INV-ACTIVE' in text, text[-1500:]
    assert b.evaluate('document.querySelector("[data-to]").value') == 'ap@example-customer.test'
    assert b.evaluate('document.querySelector("[data-subject]").value').startswith('Invoice DEMO-SALE-INV-ACTIVE')
    _shot(b, width, 'email-panel')
    if width == 390:
        _fits(b)
    # A real submit: the Send button posts the panel through htmx, and the answer replaces the page in place.
    b.evaluate('''(() => { window.__sameDocument = true;
        document.querySelector("[data-subject]").value = "Invoice from Demo Plumbing, as promised";
        document.querySelector("[data-send]").click(); })()''')
    b.wait_for('!!document.querySelector("[data-message-result]") || !!document.querySelector("[role=alert]")',
               timeout=30)
    assert b.evaluate('!!document.querySelector("[data-message-result]")'), _text(b)[-1500:]
    assert b.evaluate('window.__sameDocument === true'), 'the form left the page instead of swapping through htmx'
    [row] = run('delivery.query', {'status': 'all'})['rows']
    assert row['subject'] == 'Invoice from Demo Plumbing, as promised' and row['status'] == 'ready'
    message = run('delivery.show', {'message': row['number']})
    assert message['go_by'] and not message['waiting_for_go'] and message['connection'] == 'none'
    assert 'Ready to send' in _text(b)
    _shot(b, width, 'message-history')
    if width == 390:
        _fits(b)
    assert [e['event'] for e in run('delivery.history', {'message': row['number']})['events']] == [
        'drafted', 'released']

    b.navigate(f'{base}/delivery?status=ready')
    b.wait_for(f'!!document.querySelector("[data-message=\\"{row["number"]}\\"]")', timeout=30)
    assert 'Nothing leaves this machine' in _text(b)
    _shot(b, width, 'outbox')
    if width == 390:
        _fits(b)
    b.navigate(f"{base}/delivery/{row['number']}")
    b.wait_for('!!document.querySelector("[data-history]")', timeout=30)
    assert b.evaluate('document.querySelectorAll("[data-history] tbody tr").length') == 2
    b.navigate(f'{base}/delivery-settings')
    b.wait_for('!!document.querySelector("[data-connections]")', timeout=30)
    _shot(b, width, 'settings')
    if width == 390:
        _fits(b)


def _downloaded(b, folder, name):
    import time
    for _ in range(200):
        path = folder / name
        if path.exists() and not (folder / (name + '.crdownload')).exists() and path.stat().st_size:
            return path.read_bytes()
        time.sleep(0.05)
    raise AssertionError(f'{name} not downloaded: {sorted(p.name for p in folder.iterdir())}; ' + _text(b)[-800:])


def test_export_downloads_the_eml_from_the_outbox_and_the_email_panel(register_browser, tmp_path):
    """A real click on Export .eml: the post carries the workbench header through htmx and the file arrives."""
    import email
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(1440, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    invoice = run('invoice.show', {'invoice': 'DEMO-SALE-INV-ACTIVE'})
    write('customer.update', {'customer': invoice['customer_id'], 'email': 'ap@example-customer.test'})
    message = write('delivery.draft', {'kind': 'invoice', 'document': invoice['id']})
    name = f"message-{message['number']}.eml"

    outbox_dir, panel_dir = tmp_path / 'outbox', tmp_path / 'panel'
    outbox_dir.mkdir(), panel_dir.mkdir()
    b.call('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(outbox_dir)})
    b.navigate(f'{base}/delivery?status=draft')
    b.wait_for(f'!!document.querySelector("[data-message=\\"{message["number"]}\\"]")', timeout=30)
    b.evaluate(f'''(() => {{ document.querySelector("[data-message=\\"{message['number']}\\"] input").click();
        document.querySelector("[data-outbox-export]").click(); }})()''')
    b.wait_for('!!document.querySelector("[data-export-file]") || !!document.querySelector("[role=alert]")',
               timeout=30)
    parsed = email.message_from_bytes(_downloaded(b, outbox_dir, name))
    assert parsed['X-Unsent'] == '1' and parsed['To'] == 'ap@example-customer.test'

    b.call('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(panel_dir)})
    b.navigate(f"{base}/delivery/compose?kind=invoice&document={invoice['id']}")
    b.wait_for('!!document.querySelector("[data-email-panel]")', timeout=30)
    b.evaluate('document.querySelector("[data-export]").click()')
    b.wait_for('!!document.querySelector("[data-export-file]") || !!document.querySelector("[role=alert]")',
               timeout=30)
    assert email.message_from_bytes(_downloaded(b, panel_dir, name))['Subject'] == message['subject']
    assert [e['event'] for e in run('delivery.history', {'message': message['number']})['events']] == [
        'drafted', 'exported', 'exported']
