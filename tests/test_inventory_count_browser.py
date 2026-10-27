"""R104 increment 2: the count screen in real headless Chrome, at 1440 and at 390 pixels.

A person starts a count of the van's valves, scans the valve's barcode into the pinned item field
and types what is on the shelf (the phone's number keypad for the quantity), opens Post to see the preview, and records the count --
every form submitted through htmx, as the server requires of a cookie session. The same screen at a
phone's width fits without sideways scrolling, with the item field still on screen.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _submit(b, form_selector, values, button):
    b.evaluate("""(() => {
        for (const old of document.querySelectorAll('[data-count-result], [role=alert], [data-count-preview]')) old.remove();
        const f = document.querySelector(%r).closest('form') || document.querySelector(%r);
        const values = %s;
        for (const [name, value] of Object.entries(values)) {
            const field = f.querySelector('[name="' + name + '"]');
            if (field.type === 'checkbox') field.checked = !!value; else field.value = value;
        }
        f.querySelector(%r).click();
        return true; })()""" % (form_selector, form_selector, __import__('json').dumps(values), button))


def _notice(b):
    b.wait_for('!!document.querySelector("[data-count-result]") || !!document.querySelector("[role=alert]") '
               '|| !!document.querySelector("[data-count-preview]")')
    return b.evaluate('(document.querySelector("[role=alert]") || document.querySelector("[data-count-result]") '
                      '|| document.querySelector("[data-count-preview]")).innerText')


@pytest.mark.parametrize('width', (1440, 390))
def test_a_count_entered_and_posted_from_the_count_screen(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    b.navigate(f'{base}/inventory/counts')
    b.wait_for('!!document.querySelector("[data-count-start]")')
    # A refused start shows its own reason through htmx (answered 200 to htmx, as the pick screen does), never the
    # generic "could not be confirmed" box.
    _submit(b, '[data-count-start]', {'site': 'Van 1', 'date': '2026-12-20', 'items': 'No Such Valve',
                                      'ctx:reason': 'Van count'}, '[data-count-create]')
    text = _notice(b)
    assert 'E_' in text and 'could not be confirmed' not in text, text
    _submit(b, '[data-count-start]', {'site': 'Van 1', 'date': '2026-12-20', 'items': 'Brass Shutoff Valve',
                                      'ctx:reason': 'Van count'}, '[data-count-create]')
    text = _notice(b)
    assert 'started' in text, text
    assert b.evaluate('!!document.querySelector("[data-count-entry] [name=scan]")')
    # The demo's van count left three valves on the van. The scanner types the valve's barcode and Enter,
    # twice, faster than the page answers: the second scan waits for the first entry's page and adds to it.
    b.wait_for('!!window.bookflowScan && !!document.querySelector("[data-scan-mode=count] [data-scan-input]")')
    b.evaluate('''(() => {
        for (const old of document.querySelectorAll('[data-count-result], [role=alert]')) old.remove();
        for (let n = 0; n < 2; n++) {
            const input = document.querySelector('[data-scan-mode=count] [data-scan-input]');
            input.focus();
            input.value = '100000000050';
            input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
        }
        return true; })()''')
    b.wait_for('!!document.querySelector("[data-count-result]") && '
               'document.querySelector("[data-count-result]").innerText.includes("counted 2")', timeout=60)
    text = _notice(b)
    assert 'Brass Shutoff Valve: counted 2, expected 3, difference -1' in text, text
    assert b.evaluate('document.querySelectorAll("[data-line]").length') == 1
    if width == 390:
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        top = b.evaluate('document.querySelector("[data-count-entry] [name=scan]").getBoundingClientRect().right')
        assert top <= 390, top
    _submit(b, '[data-count-post]', {'adjustment_account': 'CDP supplies', 'ctx:reason': 'Van count'},
            '[data-count-preview-open]')
    text = _notice(b)
    assert 'Post preview' in text and 'Brass Shutoff Valve' in text and '-1' in text, text
    b.evaluate('document.querySelector("[data-count-confirm]").click()')
    b.wait_for('!!document.querySelector("[data-count-result]") || !!document.querySelector("[role=alert]")')
    text = b.evaluate('(document.querySelector("[role=alert]") || document.querySelector("[data-count-result]")).innerText')
    assert 'recorded' in text and 'Brass Shutoff Valve -1' in text, text
    assert b.evaluate('document.querySelector("[data-count]").dataset.countStatus') == 'recorded'
    if width == 390:
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
