"""Real desktop and phone browsers post a journal and display its historical lines."""
import base64
import json
from pathlib import Path

import pytest
from tests.test_row5_browser_acceptance import browser_site, _Cdp, _assert_rendered_page, CHROME, PASSWORD  # noqa: F401


@pytest.mark.skipif(not CHROME.exists(), reason='Chrome is unavailable')
@pytest.mark.parametrize('width,height', [(1280, 900), (390, 844)])
def test_journal_generated_post_and_revision_views(browser_site, tmp_path, width, height):
    browser = _Cdp(tmp_path / 'chrome')
    try:
        browser.viewport(width, height)
        browser.navigate(browser_site.base_url + '/login')
        browser.evaluate(f'''(() => {{
          document.querySelector('[name="username"]').value = {json.dumps(browser_site.login)};
          document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
          document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()''')
        home = f'{browser_site.base_url}/c/{browser_site.company_id}/'
        browser.wait_for(f'location.href === {json.dumps(home)} && !!document.querySelector(".group-grid")')
        browser.navigate(home + 'journal/post')
        browser.wait_for('!!document.querySelector("[data-collection-add]")')
        _assert_rendered_page(browser, 'journal post', viewport=(width, height))
        browser.evaluate('''(() => {
          const form = document.querySelector('[data-generated-form]');
          form.querySelector('[name="f:date"]').value = '2026-04-05';
          form.querySelector('[name="f:number"]').value = 'BROWSER-REAL';
          const collection = form.querySelector('[data-collection-path="lines"]');
          collection.querySelector('[data-collection-add]').click();
          collection.querySelector('[data-collection-add]').click();
          const values = [ ['Checking', 'debit'], ['Service Income', 'credit'] ];
          [...collection.querySelectorAll('[data-collection-item]')].forEach((row, i) => {
            row.querySelector('[name$=":account"]').value = values[i][0];
            row.querySelector('[name$=":side"]').value = values[i][1];
            row.querySelector('[name$=":amount"]').value = '48.25';
          });
          form.querySelector('button[value="submit"]').click();
        })()''')
        browser.wait_for('document.body.innerText.includes("Journal BROWSER-REAL") && !!document.querySelector("[aria-label=\\"Journal entry\\"]")')
        _assert_rendered_page(browser, 'posted journal', viewport=(width, height))
        assert browser.evaluate('document.querySelector("[aria-label=\\"Journal entry\\"]").innerText.includes("48.25")')
        assert browser.evaluate('document.querySelector("[aria-label=\\"Accounting history\\"]").innerText.includes("Original")')
        result = browser.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
        (tmp_path / f'journal-{width}.png').write_bytes(base64.b64decode(result['data']))
        browser.navigate(home + 'journal')
        browser.wait_for('!!document.querySelector(".table-wrap table a")')
        _assert_rendered_page(browser, 'journal list', viewport=(width, height))
        browser.evaluate('''[...document.querySelectorAll('.table-wrap table a')].find(a => a.textContent.trim() === 'DEMO-SERVICE').click()''')
        browser.wait_for('document.body.innerText.includes("Previous revision")')
        browser.evaluate('''[...document.querySelectorAll('a')].find(a => a.textContent === 'Previous revision').click()''')
        browser.wait_for('location.search.includes("revision_number=1") && document.body.innerText.includes("Current revision")')
        assert browser.evaluate('document.querySelector("[aria-label=\\"Journal entry\\"]").innerText.includes("1200.00")')
        _assert_rendered_page(browser, 'historical journal', viewport=(width, height))
    finally:
        browser.close()
