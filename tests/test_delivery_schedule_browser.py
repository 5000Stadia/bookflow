"""R112 increment 4: the Schedules page and the make-live check in real headless Chrome, at 1440 and 390.

A person saves a send schedule from the form (an htmx post), sees it listed as a draft whose messages wait for a go,
opens Turn on, reads the one check of its exact details (what it sends, when, the first run, who it would reach
today) and turns it on; the page then says it is live under them. At a phone's width nothing scrolls sideways. With
BF_DELIVERY_SHOTS set to a folder, the screens are saved there.
"""
import pytest

from tests.test_delivery_browser import _fits, _shot, _text
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.mark.parametrize('width', (1440, 390))
def test_a_send_schedule_saved_checked_and_turned_on(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    for name, address in (('Lakeside Dental', 'ap@lakeside.example'), ('Riverside Apartments',
                                                                        'office@riverside.example')):
        write('customer.update', {'customer': name, 'email': address, 'preferred_delivery_method': 'email'})

    b.navigate(f'{base}/delivery-settings')
    b.wait_for('!!document.querySelector("[data-schedules-link] a")', timeout=30)
    b.evaluate('document.querySelector("[data-schedules-link] a").click()')
    b.wait_for('!!document.querySelector("[data-schedule-form]")', timeout=30)
    assert 'No schedules yet' in _text(b)
    # The form, filled and saved: a real htmx submit.
    b.evaluate('''(() => { window.__sameDocument = true;
        document.querySelector("#schedule-form").open = true;
        const f = document.querySelector("[data-schedule-form]");
        f.querySelector("[name=name]").value = "Daily invoices";
        f.querySelector("[name=kind]").value = "invoice";
        f.querySelector("[name=what]").value = "unsent";
        f.querySelector("[name=frequency]").value = "daily";
        f.querySelector("[name=start_date]").value = "2026-11-02";
        f.querySelector("[name=mode]").value = "send";
        f.querySelector("[data-schedule-save]").click(); })()''')
    b.wait_for('!!document.querySelector("[data-schedules-result]") || !!document.querySelector('
               '"[data-schedules-error]")', timeout=30)
    assert b.evaluate('window.__sameDocument === true')
    text = _text(b)
    assert 'Schedule Daily invoices saved' in text and 'wait for a go until a person makes it live' in text, text
    _shot(b, width, 'schedules')
    if width == 390:
        _fits(b)

    # Turn on: the one check of its exact details.
    b.evaluate('document.querySelector("[data-schedule=\\"Daily invoices\\"] [data-schedule-turn-on]").click()')
    b.wait_for('!!document.querySelector("[data-live-check]")', timeout=60)
    text = _text(b)
    assert 'Every invoice with no live or sent message' in text and 'Every day from 2026-11-02' in text, text
    assert 'If it ran today' in text and 'Lakeside Dental' in text and 'ap@lakeside.example' in text, text
    _shot(b, width, 'schedule-live')
    if width == 390:
        _fits(b)
    b.evaluate('(() => { window.__sameDocument = true; document.querySelector("[data-live-confirm]").click(); })()')
    b.wait_for('!!document.querySelector("[data-schedules-result]") || !!document.querySelector("[data-live-error]")',
               timeout=60)
    assert b.evaluate('window.__sameDocument === true')
    assert 'Daily invoices is live' in _text(b)
    shown = run('delivery-schedule.show', {'schedule': 'Daily invoices'})
    assert shown['state'] == 'live' and shown['mode'] == 'send' and shown['established_by']
    if width == 390:
        _fits(b)
