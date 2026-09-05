"""Actual Chrome statement navigation, current-books drill-down and bounded paging."""
import base64
import json

import pytest

from tests.test_reference_year import reference_template,reference_client  # noqa: F401
from tests.test_reference_year_browser import reference_site  # noqa: F401
from tests.test_row5_browser_acceptance import CHROME,PASSWORD,_Cdp

pytestmark=pytest.mark.skipif(not CHROME.exists(),reason="Chrome is unavailable")


def fill(browser,fields):
    browser.evaluate("""(() => { const form=document.querySelector('form[data-generated-form]');
        for(const [key,value] of Object.entries(%s)) form.elements.namedItem('f:'+key).value=value;
        form.querySelector('button[value=submit]').click(); })()""" % json.dumps(fields))


@pytest.mark.parametrize("width,height",[(1280,900),(390,844)])
def test_statements_from_navigation_paging_and_current_ledger(reference_site,tmp_path,width,height):
    site=reference_site;browser=_Cdp(tmp_path/"chrome-profile")
    try:
        browser.viewport(width,height);browser.navigate(site.base_url+"/login")
        browser.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()""" % (json.dumps(site.login),json.dumps(PASSWORD)))
        browser.wait_for("[...document.querySelectorAll('main a')].some(a=>a.textContent==='Reference Plumbing Co')")
        browser.evaluate("[...document.querySelectorAll('main a')].find(a=>a.textContent==='Reference Plumbing Co').click()")
        browser.wait_for("document.querySelector('h1')?.textContent==='Reference Plumbing Co'")
        company_path=browser.evaluate("location.pathname").rstrip("/")
        browser.wait_for("!!document.querySelector('a[href$=\"/report/profit-and-loss\"]')")
        browser.evaluate("document.querySelector('a[href$=\"/report/profit-and-loss\"]').click()")
        browser.wait_for("!!document.querySelector('[name=\"f:date_from\"]')")
        # September adds two $120 net sales, $16 tax, $128 each in bank/AR.
        # Annual/H2 profit and equity rise $240; assets rise $256.
        fill(browser,{"date_from":"2026-01-01","date_to":"2026-12-31","limit":"2"})
        browser.wait_for("document.querySelector('[data-total=net_income]')?.textContent.includes('64390.00')")
        assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == "2026-01-01"
        first=browser.evaluate("document.querySelector('#statement-accounts tbody').textContent")
        browser.evaluate("document.querySelector('#statement-next-page button').click()")
        browser.wait_for("document.querySelector('#statement-accounts tbody')?.textContent!==%s" % json.dumps(first))
        assert '64390.00' in browser.evaluate("document.querySelector('[data-total=net_income]').textContent")
        assert browser.evaluate("document.querySelectorAll('form[data-generated-form] [name=\"f:cursor\"]').length") == 0
        fill(browser,{"date_from":"2026-07-01","date_to":"2026-12-31","limit":"2"})
        browser.wait_for("document.querySelector('[data-total=net_income]')?.textContent.includes('41340.00')")
        browser.navigate(site.base_url+company_path+"/report/balance-sheet")
        browser.wait_for("!!document.querySelector('[name=\"f:date_to\"]')")
        fill(browser,{"date_to":"2026-12-31","limit":"50"})
        browser.wait_for("document.querySelector('[data-total=total_equity]')?.textContent.includes('74390.00')")
        assert '74606.00' in browser.evaluate("document.querySelector('[data-total=assets]').textContent")
        assert browser.evaluate("document.documentElement.scrollWidth") <= width+1
        image=browser.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
        (tmp_path/f"balance-sheet-{width}.png").write_bytes(base64.b64decode(image['data']))
        browser.evaluate("[...document.querySelectorAll('#statement-accounts tr')].find(r=>r.textContent.includes('Checking')).querySelector('a').click()")
        browser.wait_for("!!document.querySelector('#report-source-state')")
        assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == "0001-01-01"
        fill(browser,{"limit":"2"})
        browser.wait_for("document.querySelector('main pre')?.textContent.includes('72678.00')")
        assert 'books changed' not in browser.evaluate("document.querySelector('#report-source-state').textContent")
        # A source watermark from an earlier information boundary must be explicit.
        browser.evaluate("document.querySelector('[name=_source_report_watermark]').value='0'")
        fill(browser,{"limit":"2"})
        browser.wait_for("document.querySelector('#report-source-state')?.textContent.includes('The books changed')")
        assert browser.evaluate("document.documentElement.scrollWidth") <= width+1
    finally: browser.close()
