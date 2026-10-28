"""R112 increment 3: Email later and the batch preview in real headless Chrome, at 1440 and 390.

On a demo invoice a person ticks Email later (an htmx post on change) and the queue lists it. The batch page previews
every unsent invoice with a checkbox per customer and the skipped with why; the person leaves one out and taps Draft,
and the page loops through the chunks with a progress bar until done; then cancels the batch as one unit. At a
phone's width nothing scrolls sideways. With BF_DELIVERY_SHOTS set to a folder, the screens are saved there.
"""
import pytest

from tests.test_delivery_browser import _fits, _shot, _text
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.mark.parametrize('width', (1440, 390))
def test_email_later_and_a_batch_previewed_drafted_in_chunks_and_cancelled(register_browser, width, monkeypatch):
    from bookflow.company import delivery_batches
    monkeypatch.setattr(delivery_batches, 'CHUNK', 1)  # so the page loops through its chunks
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    for name, address, method in (('Lakeside Dental', 'ap@lakeside.example', 'email'),
                                  ('Riverside Apartments', 'office@riverside.example', 'email'),
                                  ('Commercial Example Customer', 'ap@commercial.example', 'mail')):
        write('customer.update', {'customer': name, 'email': address, 'preferred_delivery_method': method})
    invoice = run('invoice.show', {'invoice': 'DEMO-SALE-INV-ACTIVE'})

    # Email later, ticked on the invoice's page: a real htmx post on change.
    b.navigate(f"{base}/invoice/{invoice['id']}")
    b.wait_for('!!document.querySelector("[data-email-later-box]")', timeout=30)
    b.evaluate('(() => { window.__sameDocument = true; document.querySelector("[data-email-later-box]").click(); })()')
    b.wait_for('!!document.querySelector("[data-email-later-why]") || !!document.querySelector("[data-email-later-'
               'error]")', timeout=30)
    assert b.evaluate('window.__sameDocument === true')
    assert 'marked to email' in _text(b)
    [row] = run('delivery.queue', {})['rows']
    assert row['number'] == 'DEMO-SALE-INV-ACTIVE' and row['why'] == 'flagged'
    _shot(b, width, 'email-later')
    if width == 390:
        _fits(b)

    # The batch preview: who gets what, and who is skipped with why.
    b.navigate(f'{base}/delivery/batch?selection=unsent&kind=invoice')
    b.wait_for('!!document.querySelector("[data-batch-count]")', timeout=60)
    text = _text(b)
    assert '3 emails to draft' in text and 'Skipped' in text and 'the customer has no email address' in text, text
    assert 'Commercial Example Customer' in text  # flagged, so it goes despite preferring mail
    _shot(b, width, 'batch-preview')
    if width == 390:
        _fits(b)
    # Leave Riverside out, then Draft: the page loops through its chunks until done.
    b.evaluate('''(() => { window.__sameDocument = true;
        const box = [...document.querySelectorAll("[data-batch-items] input[name=keep]")]
            .find(x => x.closest("tr").innerText.includes("Riverside"));
        box.click(); document.querySelector("[data-batch-draft]").click(); })()''')
    b.wait_for('!!document.querySelector("[data-batch-number]") && !document.querySelector("[data-batch-continue]")'
               ' || !!document.querySelector("[data-batch-error]")', timeout=90)
    assert b.evaluate('window.__sameDocument === true')
    text = _text(b)
    assert 'Done: 2 drafted' in text and 'left out of this batch' in text, text
    number = b.evaluate('document.querySelector("[data-batch-number]").innerText').split()[-1]
    rows = run('delivery.query', {'batch': number, 'status': 'all'})['rows']
    assert {r['customer_name'] for r in rows} == {'Commercial Example Customer', 'Lakeside Dental'}
    _shot(b, width, 'batch-done')
    if width == 390:
        _fits(b)
    # Cancelled as one unit: no drafted message stands.
    b.evaluate('''(() => { document.querySelector("[name='ctx:reason']").value = "Wrong month";
        document.querySelector("[data-batch-cancel]").click(); })()''')
    b.wait_for('!!document.querySelector("[data-batch-result]") || !!document.querySelector("[data-batch-error]")',
               timeout=30)
    assert 'Cancelled batch' in _text(b)
    assert {r['status'] for r in run('delivery.query', {'batch': number, 'status': 'all'})['rows']} == {'cancelled'}
