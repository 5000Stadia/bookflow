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
    # Riverside's one invoice is the demo's own DEMO-PIPE-INV-1, already sent by hand (M-1001), so the batch skips it
    # as sent; Tax Work Example Customer's unsent invoice stands in.
    for name, address, method in (('Lakeside Dental', 'ap@lakeside.example', 'email'),
                                  ('Tax Work Example Customer', 'ap@taxwork.example', 'email'),
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
    # Leave Tax Work out, then Draft: the page loops through its chunks until done.
    b.evaluate('''(() => { window.__sameDocument = true;
        const box = [...document.querySelectorAll("[data-batch-items] input[name=keep]")]
            .find(x => x.closest("tr").innerText.includes("Tax Work"));
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


def test_a_batch_left_unfinished_is_found_again_and_refusals_keep_the_batch_in_place(register_browser, monkeypatch):
    """The review's findings: unticking Email later returns to neutral; a batch left at 1 of 2 is listed in the
    Outbox with Continue and Cancel, and a rerun of its selection offers it; a refused cancel or continue keeps the
    batch view with the refusal and a Continue button; Continue then finishes it."""
    from bookflow.company import delivery_batches
    monkeypatch.setattr(delivery_batches, 'CHUNK', 1)
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(1440, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    # Riverside's one invoice is already sent in the demo (M-1001); Tax Work Example Customer's stands in.
    for name, address in (('Lakeside Dental', 'ap@lakeside.example'), ('Tax Work Example Customer',
                                                                        'ap@taxwork.example')):
        write('customer.update', {'customer': name, 'email': address, 'preferred_delivery_method': 'email'})
    invoice = run('invoice.show', {'invoice': 'DEMO-SALE-INV-ACTIVE'})

    # Tick, then untick: the flag is cleared, not turned into a lasting opt-out.
    b.navigate(f"{base}/invoice/{invoice['id']}")
    b.wait_for('!!document.querySelector("[data-email-later-box]")', timeout=30)
    b.evaluate('document.querySelector("[data-email-later-box]").click()')
    b.wait_for('!!document.querySelector("[data-email-later-why]")', timeout=30)
    b.evaluate('document.querySelector("[data-email-later-box]").click()')
    b.wait_for('!document.querySelector("[data-email-later-why]") && '
               '!!document.querySelector("[data-email-later-never-button]")', timeout=30)
    assert run('delivery.queue', {'kind': 'invoice', 'document': invoice['id']})['flag'] is None

    # A batch started and left at 1 of 2 (a closed tab): the Outbox lists it with Continue and Cancel.
    started = write('delivery.batch', {'kind': 'invoice', 'unsent': True})
    number = started['number']
    assert started['state'] == 'drafting' and started['planned'] == 2
    b.navigate(f'{base}/delivery/batch?selection=unsent&kind=invoice')
    b.wait_for('!!document.querySelector("[data-batch-count]")', timeout=60)
    assert b.evaluate(f'!!document.querySelector("[data-batch-open=\\"{number}\\"] [data-batch-open-link]")')
    b.navigate(f'{base}/delivery')
    b.wait_for(f'!!document.querySelector("[data-unfinished-batch=\\"{number}\\"] [data-batch-cancel-link]")',
               timeout=30)
    b.evaluate(f'document.querySelector("[data-unfinished-batch=\\"{number}\\"] [data-batch-continue-link]").click()')
    b.wait_for('!!document.querySelector("[data-batch-continue-button]")', timeout=30)
    assert not b.evaluate('!!document.querySelector("[data-batch-continue]")')  # waits for the person

    # Cancel with no reason: the refusal shows in place and the batch view stays.
    b.evaluate('(() => { window.__sameDocument = true; document.querySelector("[data-batch-cancel]").click(); })()')
    b.wait_for('!!document.querySelector("[data-batch-error]")', timeout=30)
    assert b.evaluate('window.__sameDocument === true')
    assert b.evaluate('!!document.querySelector("[data-batch-number]") && '
                      '!!document.querySelector("[data-batch-continue-button]") && '
                      '!document.querySelector("[data-batch-preview]")')
    assert 'E_REASON_REQUIRED' in _text(b)
    # A refused continue (another fingerprint) stops the loop in place, leaving Continue.
    b.evaluate('''(() => { document.querySelector("[data-batch-error]").remove();
        document.querySelector("[data-batch-continue-button]").closest("form")
        .querySelector("[name=fingerprint]").value = "0".repeat(64);
        document.querySelector("[data-batch-continue-button]").click(); })()''')
    b.wait_for('!!document.querySelector("[data-batch-error]") && '
               '!!document.querySelector("[data-batch-continue-button]")', timeout=30)
    assert 'E_WORK_DEPENDENCY' in _text(b) and b.evaluate('!document.querySelector("[data-batch-continue]")')
    assert run('delivery.batch-show', {'batch': number})['drafted'] == 1
    # Continue finishes it.
    b.evaluate('document.querySelector("[data-batch-continue-button]").click()')
    b.wait_for('!!document.querySelector("[data-batch-number]") && !document.querySelector("[data-batch-continue]") '
               '&& !document.querySelector("[data-batch-continue-button]")', timeout=60)
    assert 'Done: 2 drafted' in _text(b)
    assert run('delivery.batch-show', {'batch': number})['state'] == 'done'
