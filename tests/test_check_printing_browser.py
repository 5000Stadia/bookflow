"""R152 in a real browser: a person with a cookie session prints checks from the Print Checks page.

Three checks are saved with Print later (the check form offers it), the page lists them ticked, Print
submits through htmx and offers the PDF, and "Did checks print OK?" is answered with the first check
that misprinted, which goes back to the queue. Set BF_CHECK_SHOTS=<dir> to keep 1440 and 390 wide
screenshots of each step.
"""
import base64
import json
import os
from pathlib import Path

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

WHY = {'X-Bookflow-Reason': 'print checks witness'}


def _shot(b, name):
    shots = os.environ.get('BF_CHECK_SHOTS')
    if not shots:
        return
    out = Path(shots)
    out.mkdir(parents=True, exist_ok=True)
    for width in (1440, 390):
        b.viewport(width, 900)
        height = b.evaluate('Math.ceil(document.documentElement.scrollHeight)')
        b.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': min(int(height), 8000),
                                                      'deviceScaleFactor': 1, 'mobile': width <= 700})
        data = b.call('Page.captureScreenshot', {'format': 'png'}, timeout=30)['data']
        (out / f'{name}-{width}.png').write_bytes(base64.b64decode(data))
    b.viewport(1280, 900)


def _click_and_swap(b, button):
    b.evaluate('''void (window.__st = [], window.__watching || (window.__watching = true,
      document.addEventListener('htmx:afterRequest', e => window.__st.push(e.detail.xhr.status))),
      window.__old = document.querySelector('#print-checks'))''')
    b.evaluate(f'document.querySelector({json.dumps(button)}).click()')
    b.wait_for("!window.__old.isConnected && !!document.querySelector('#print-checks')", timeout=30)
    assert b.evaluate('window.__st') == [200], b.evaluate('(document.querySelector("[role=alert]") || {}).innerText')
    text = b.evaluate('document.body.innerText')
    assert 'E_WORKBENCH_HEADER' not in text and not b.evaluate('!!document.querySelector("[role=alert]")'), text[:900]
    return text


def test_a_person_prints_checks_and_answers_did_they_print_ok(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    company = env.site.company_id
    base = f'{env.site.base_url}/c/{company}'

    def run(name, payload):
        return _command(b, env.site, name, payload, **WHY)

    bank = env.bank['id']
    expense = run('account.create', {'name': 'BC Supplies', 'type': 'expense'})['id']
    vendor = run('vendor.create', {'name': 'BC Paper Co', 'address': {'line1': '9 Mill Road', 'city': 'Dayton',
                                                                       'state': 'OH', 'postal_code': '45402'}})['id']
    ids = []
    for amount in ('125.00', '64.10', '1000.00'):
        ids.append(run('check.post', {'account': bank, 'pay_to': {'name_type': 'vendor', 'name_id': vendor},
                                      'date': '2026-10-20', 'amount': amount, 'memo': 'October supplies',
                                      'to_be_printed': True, 'expenses': [{'account': expense, 'amount': amount}]})['id'])

    b.navigate(f'{base}/check/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert 'Print later' in b.evaluate('document.body.innerText')

    b.navigate(f'{base}/print-checks?account={bank}')
    b.wait_for('!!document.querySelector("[data-print-queue]")')
    assert b.evaluate('document.querySelectorAll("[data-queued] input:checked").length') == 3
    assert b.evaluate('document.querySelector("[name=account]").value') == bank
    b.evaluate('document.querySelector("[data-first-number]").value = "2201"')
    _shot(b, 'print-checks-queue')
    text = _click_and_swap(b, '[data-print]')
    assert 'numbers 2201 to 2203' in text and 'Did checks print OK?' in text
    pdf = b.evaluate('document.querySelector("[data-print-pdf]").getAttribute("href")')
    fetched = b.evaluate(f"fetch({json.dumps(pdf)}, {{credentials: 'same-origin'}}).then(r => r.headers.get('content-type'))",
                         await_promise=True)
    assert fetched == 'application/pdf'
    _shot(b, 'print-checks-confirm')

    run_id = b.evaluate('document.querySelector("[data-confirm-run]").dataset.confirmRun')
    b.evaluate(f'''(() => {{ document.querySelector('[name="ok:{run_id}"][value=no]').checked = true;
      document.querySelector('[name="failed_from:{run_id}"]').value = "2203"; }})()''')
    text = _click_and_swap(b, '[data-confirm]')
    assert '2 check(s) printed. Spoiled: 2203' in text, text[:600]
    assert b.evaluate('document.querySelectorAll("[data-queued]").length') == 1
    assert b.evaluate(f'!!document.querySelector(\'[data-queued="{ids[2]}"]\')')
    _shot(b, 'print-checks-after')
