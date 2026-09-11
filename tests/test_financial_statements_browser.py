"""Actual Chrome statement navigation, current-books drill-down and bounded paging.

Every figure asserted here is the published reference-year oracle, not a literal
retyped beside it. The literals that used to sit here went stale when the seed
grew, and the walk in from the home window pinned an `/report/profit-and-loss`
link that nothing on that page has ever rendered -- so the test timed out on its
way in and never reached a single one of them. It now walks the way a person
does: the Reports tile, then the report named on the reports page.
"""
import base64
import json

import pytest

from tests.test_reference_year import EXPECTED,reference_template,reference_client  # noqa: F401
from tests.test_reference_year_browser import reference_site  # noqa: F401
from tests.test_row5_browser_acceptance import CHROME,PASSWORD,_Cdp

pytestmark=pytest.mark.skipif(not CHROME.exists(),reason="Chrome is unavailable")

# The whole year, its first half, and what the year leaves on the balance sheet.
ANNUAL_INCOME=EXPECTED["annual"]["income"]
FIRST_HALF_INCOME=EXPECTED["monthly"][5]["income"]
NET_ASSETS=EXPECTED["annual"]["net_assets"]


def money(minor):
    """The amount string the statement prints for an integer minor-unit figure."""
    sign="-" if minor<0 else ""
    whole,cents=divmod(abs(minor),100)
    return f"{sign}{whole}.{cents:02d}"


def fill(browser,fields):
    browser.evaluate("""(() => { const form=document.querySelector('form[data-generated-form]');
        for(const [key,value] of Object.entries(%s)) form.elements.namedItem('f:'+key).value=value;
        form.querySelector('button[value=submit]').click(); })()""" % json.dumps(fields))


def total(browser,key):
    """The printed amount for one whole-report total, without its own row label."""
    return browser.evaluate("document.querySelector('[data-total=%s] td').textContent.trim()" % key)


def open_report(browser,verb,heading):
    """From the home window: the Reports tile, then the report by the name it prints.

    Nothing here is addressed by URL. A tile that stops saying Reports, or a
    reports page that stops offering this report, is exactly the breakage this
    walk exists to notice -- and the destination's own address is checked once,
    on arrival, rather than used as the way in.
    """
    tile="[...document.querySelectorAll('a.flow-tile')].find(a=>a.querySelector('.flow-tile-title')?.textContent==='Reports')"
    browser.wait_for("!!"+tile)
    browser.evaluate(tile+".click()")
    link="[...document.querySelectorAll('.nav-group a')].find(a=>a.textContent===%s)" % json.dumps(verb)
    browser.wait_for("!!"+link)
    browser.evaluate(link+".click()")
    browser.wait_for("!!document.querySelector('form[data-generated-form]')")
    assert browser.evaluate("location.pathname").endswith("/report/"+verb)
    assert browser.evaluate("document.querySelector('h1').textContent").strip() == heading


@pytest.mark.timeout(300)
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

        open_report(browser,"profit-and-loss","Profit and loss")
        fill(browser,{"date_from":"2026-01-01","date_to":"2026-12-31","limit":"2"})
        browser.wait_for("document.querySelector('[data-total=net_income]')?.textContent.includes(%s)"
                         % json.dumps(money(ANNUAL_INCOME)))
        assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == "2026-01-01"
        # Account rows are paged; the whole-year total is not.
        first=browser.evaluate("document.querySelector('#statement-accounts tbody').textContent")
        browser.evaluate("document.querySelector('#statement-next-page button').click()")
        browser.wait_for("document.querySelector('#statement-accounts tbody')?.textContent!==%s" % json.dumps(first))
        assert total(browser,"net_income") == money(ANNUAL_INCOME)
        assert browser.evaluate("document.querySelectorAll('form[data-generated-form] [name=\"f:cursor\"]').length") == 0
        # The second half of the same year, from the same form.
        fill(browser,{"date_from":"2026-07-01","date_to":"2026-12-31","limit":"2"})
        browser.wait_for("document.querySelector('[data-total=net_income]')?.textContent.includes(%s)"
                         % json.dumps(money(ANNUAL_INCOME-FIRST_HALF_INCOME)))

        browser.navigate(site.base_url+company_path+"/")
        open_report(browser,"balance-sheet","Balance sheet")
        fill(browser,{"date_to":"2026-12-31","limit":"50"})
        browser.wait_for("document.querySelector('[data-total=total_equity]')?.textContent.includes(%s)"
                         % json.dumps(money(NET_ASSETS)))
        # The sheet balances on its own terms, and its equity is the year's net assets.
        assert total(browser,"difference") == money(0)
        assert total(browser,"assets") == total(browser,"liabilities_and_equity")
        assert browser.evaluate("document.documentElement.scrollWidth") <= width+1
        image=browser.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
        (tmp_path/f"balance-sheet-{width}.png").write_bytes(base64.b64decode(image['data']))

        # An amount opens that account's own current general ledger.
        browser.evaluate("[...document.querySelectorAll('#statement-accounts tr')].find(r=>r.textContent.includes('Checking')).querySelector('a').click()")
        browser.wait_for("!!document.querySelector('#report-source-state')")
        assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == "0001-01-01"
        fill(browser,{"limit":"2"})
        browser.wait_for("document.querySelector('main pre')?.textContent.includes(%s)"
                         % json.dumps(money(EXPECTED["annual"]["balances"]["Checking"])))
        assert 'books changed' not in browser.evaluate("document.querySelector('#report-source-state').textContent")
        # A source watermark from an earlier information boundary must be explicit.
        # The next-page form carries one of its own and comes first in the document,
        # so the watermark that travels with a refiltered report is named exactly.
        browser.evaluate("document.querySelector('form[data-generated-form] [name=_source_report_watermark]').value='0'")
        fill(browser,{"limit":"2"})
        browser.wait_for("document.querySelector('#report-source-state')?.textContent.includes('The books changed')")
        assert browser.evaluate("document.documentElement.scrollWidth") <= width+1
    finally: browser.close()
