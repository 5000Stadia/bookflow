"""The two payables reports driven in a real browser, at desktop and phone widths.

The journey a bookkeeper actually takes: the home window, the report picker, the
A/P aging summary, one vendor's own open bills, and the bill itself. Every figure
asserted here is read off the rendered page, not off the command's JSON.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command
from tests.test_payable_reports import AS_OF, build

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")

AGING_TOTALS = ["925.00", "515.50", "310.00", "615.00", "1100.00", "3465.50"]
AGING_ROWS = [
    ["Aging Freight LLC", "0.00", "275.00", "310.00", "0.00", "0.00", "585.00"],
    ["Aging Parts Inc", "0.00", "0.00", "0.00", "0.00", "1100.00", "1100.00"],
    ["Aging Supply Co", "925.00", "240.50", "0.00", "615.00", "0.00", "1780.50"],
]
# due, days past due, column, bill, vendor, bill date, reference, amount, applied, open
SUPPLY_BILLS = [
    ["2026-04-01", "90", "61-90", "AP-90", "Aging Supply Co", "2026-03-15", "", "615.00", "0.00", "615.00"],
    ["2026-05-31", "30", "1-30", "AP-30", "Aging Supply Co", "2026-05-01", "SUP-51", "240.50", "0.00", "240.50"],
    ["2026-06-30", "0", "Current", "AP-TODAY", "Aging Supply Co", "2026-06-02", "", "800.00", "0.00", "800.00"],
    ["2026-07-15", "-15", "Current", "AP-CURRENT", "Aging Supply Co", "2026-06-01", "SUP-77", "125.00", "0.00", "125.00"],
]

_CELLS = """[...document.querySelectorAll('%s tbody tr')].map(
    row => [...row.cells].map(cell => cell.textContent.trim()))"""


def _fill(browser, fields):
    browser.evaluate("""(() => { const form=document.querySelector('form[data-generated-form]');
        for(const [key,value] of Object.entries(%s)) form.elements.namedItem('f:'+key).value=value;
        form.querySelector('button[value=submit]').click(); })()""" % json.dumps(fields))


def _no_sideways_scroll(browser):
    assert browser.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"), \
        browser.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")


@pytest.mark.timeout(240)
@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_a_bookkeeper_reaches_both_payables_reports_and_reads_the_money(browser_site, tmp_path, width, height):  # noqa: F811
    site = browser_site
    browser = _Cdp(tmp_path / f"payables-chrome-{width}")
    try:
        browser.viewport(width, height)
        browser.navigate(site.base_url + "/login")
        browser.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
            % (json.dumps(site.login), json.dumps(PASSWORD)))
        browser.wait_for("!!document.querySelector('.nav-group')")

        def run(command, body, *, reason=None):
            headers = {"X-Bookflow-Reason": reason} if reason else {}
            return _command(browser, site, command.replace(" ", "."), body, **headers)

        build(run)

        # From the home window's Reports tile, not from a URL nobody would type.
        browser.navigate(f"{site.base_url}/c/{site.company_id}/")
        browser.wait_for("!!document.querySelector('a[href$=\"/_group/reports\"]')")
        browser.evaluate("document.querySelector('a[href$=\"/_group/reports\"]').click()")
        browser.wait_for("!!document.querySelector('a[href$=\"/report/ap-aging\"]')")
        assert browser.evaluate("!!document.querySelector('a[href$=\"/report/unpaid-bills\"]')")
        browser.evaluate("document.querySelector('a[href$=\"/report/ap-aging\"]').click()")
        browser.wait_for("!!document.querySelector('[name=\"f:as_of\"]')")
        assert browser.evaluate("document.querySelector('h1').textContent") == "A/P aging summary"
        # The filter form restarts the report; it never carries a continuation.
        assert browser.evaluate("!document.querySelector('form[data-generated-form] [name=\"f:cursor\"]')")

        _fill(browser, {"as_of": AS_OF, "limit": "200"})
        browser.wait_for("!!document.querySelector('#payables-aging')")
        assert browser.evaluate(_CELLS % "#payables-totals") == [AGING_TOTALS]
        assert browser.evaluate(_CELLS % "#payables-aging") == AGING_ROWS
        _no_sideways_scroll(browser)
        assert browser.evaluate("!document.querySelector('#payables-next-page')")

        # One vendor's own open bills, opened from that vendor's aging row. The
        # link carries the filter into the next report's form rather than
        # running it, so the reader sees what they are about to run.
        browser.evaluate("""[...document.querySelectorAll('#payables-aging a')]
            .find(a => a.textContent.trim() === 'Aging Supply Co').click()""")
        browser.wait_for("!!document.querySelector('[name=\"f:vendor\"]')")
        assert browser.evaluate("document.querySelector('h1').textContent") == "Unpaid bills"
        assert browser.evaluate("document.querySelector('[name=\"f:as_of\"]').value") == AS_OF
        assert browser.evaluate("document.querySelector('[name=\"f:vendor\"]').value").startswith("01")
        _fill(browser, {"limit": "200"})
        browser.wait_for("!!document.querySelector('#payables-unpaid')")
        assert browser.evaluate(_CELLS % "#payables-unpaid") == SUPPLY_BILLS
        assert browser.evaluate(_CELLS % "#payables-totals") == [["1780.50", "0.00", "1780.50"]]
        _no_sideways_scroll(browser)

        # The bill itself, opened from its number.
        browser.evaluate("""[...document.querySelectorAll('#payables-unpaid a')]
            .find(a => a.textContent.trim() === 'AP-30').click()""")
        # Opening a bill is a full page load, so wait on the navigation itself.
        # The report page this leaves already prints AP-30 in the row that was
        # clicked, so waiting on that text is satisfied before the click has
        # gone anywhere and the read below races a document with no body yet.
        browser.wait_for(f'location.pathname.startsWith("/c/{site.company_id}/bill/")'
                         ' && document.readyState !== "loading"')
        assert "AP-30" in browser.evaluate("document.body.innerText")
        assert "SUP-51" in browser.evaluate("document.body.innerText")

        # Unfiltered, the open bills add to the aging total the same page showed.
        browser.navigate(f"{site.base_url}/c/{site.company_id}/report/unpaid-bills")
        browser.wait_for("!!document.querySelector('[name=\"f:as_of\"]')")
        _fill(browser, {"as_of": AS_OF, "limit": "200"})
        browser.wait_for("!!document.querySelector('#payables-unpaid')")
        assert browser.evaluate(_CELLS % "#payables-totals") == [["3465.50", "0.00", "3465.50"]]
        assert browser.evaluate("""[...document.querySelectorAll('#payables-unpaid tbody tr')]
            .map(row => row.cells[3].textContent.trim())""") == [
            "AP-OLD", "AP-91", "AP-90", "AP-31", "AP-30", "AP-FIXED", "AP-TODAY", "AP-CURRENT"]
        assert "AP-VOID" not in browser.evaluate("document.querySelector('#payables-unpaid').innerText")
        _no_sideways_scroll(browser)
    finally:
        browser.close()


@pytest.mark.timeout(240)
def test_a_payables_page_walks_and_a_stale_continuation_restarts_in_the_browser(browser_site, tmp_path):  # noqa: F811
    site = browser_site
    browser = _Cdp(tmp_path / "payables-chrome-paging")
    try:
        browser.viewport(1280, 900)
        browser.navigate(site.base_url + "/login")
        browser.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
            % (json.dumps(site.login), json.dumps(PASSWORD)))
        browser.wait_for("!!document.querySelector('.nav-group')")

        def run(command, body, *, reason=None):
            headers = {"X-Bookflow-Reason": reason} if reason else {}
            return _command(browser, site, command.replace(" ", "."), body, **headers)

        build(run)
        browser.navigate(f"{site.base_url}/c/{site.company_id}/report/ap-aging")
        browser.wait_for("!!document.querySelector('[name=\"f:as_of\"]')")
        _fill(browser, {"as_of": AS_OF, "limit": "1"})
        browser.wait_for("!!document.querySelector('#payables-aging')")
        assert browser.evaluate(_CELLS % "#payables-aging") == AGING_ROWS[:1]
        # Whole-report totals on every page, not this page's own arithmetic.
        assert browser.evaluate(_CELLS % "#payables-totals") == [AGING_TOTALS]
        assert browser.evaluate("!!document.querySelector('#payables-next-page')")
        browser.evaluate("document.querySelector('#payables-next-page button').click()")
        browser.wait_for("""document.querySelector('#payables-aging')
            ?.innerText.includes('Aging Parts Inc')""")
        assert browser.evaluate(_CELLS % "#payables-aging") == AGING_ROWS[1:2]
        assert browser.evaluate(_CELLS % "#payables-totals") == [AGING_TOTALS]

        # A company write while the reader is mid-report stales the continuation.
        run("vendor create", {"name": "Aging browser witness"})
        browser.evaluate("document.querySelector('#payables-next-page button').click()")
        browser.wait_for("!!document.querySelector('#report-restart')")
        assert "E_QUERY_STALE" in browser.evaluate("document.querySelector('.error').textContent")
        assert not browser.evaluate("!!document.querySelector('#payables-aging')")
        # And the visible filter form still restarts a fresh report from here.
        assert browser.evaluate("document.querySelector('[name=\"f:as_of\"]').value") == AS_OF
        assert browser.evaluate("!document.querySelector('form[data-generated-form] [name=\"f:cursor\"]')")
        _fill(browser, {"as_of": AS_OF, "limit": "200"})
        browser.wait_for("!!document.querySelector('#payables-aging')")
        assert browser.evaluate(_CELLS % "#payables-totals") == [AGING_TOTALS]
    finally:
        browser.close()
