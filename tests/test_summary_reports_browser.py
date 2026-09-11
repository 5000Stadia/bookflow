"""The four period summaries driven in a real browser, at desktop and phone widths.

The journey a small-business owner actually takes to answer "where did the money come
from and where did it go": the home window, the Reports tile, each summary in turn, and
the drill-down each one offers. Every figure asserted here is read off the rendered page,
not off the command's JSON.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command
from tests.test_summary_reports import BY_CUSTOMER, BY_ITEM, BY_REP, BY_VENDOR, FROM, TO, build

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")

# Exactly what each table has to read, as a person sees it. The percentages are the
# report's exact coefficients shown to two places, which is what the page prints.
CUSTOMER_ROWS = [
    ["Summary Coastal Cafe", "", "115.00", "18.40%"],
    ["Summary Ridge Builders", "", "350.00", "56.00%"],
    ["Summary Ridge Builders:Tower Job", "Summary Ridge Builders", "100.00", "16.00%"],
    ["No name", "", "60.00", "9.60%"],
]
ITEM_ROWS = [
    ["Summary Drain Service", "4", "400.00", "100.00", "64.00%"],
    ["Summary Valve Fitting", "5", "125.00", "25.00", "20.00%"],
    ["No item", "0", "100.00", "", "16.00%"],
]
REP_ROWS = [
    ["Summary Rep North", "SNO", "425.00", "68.00%"],
    ["Summary Rep South", "SSO", "100.00", "16.00%"],
    ["Unassigned", "", "100.00", "16.00%"],
]
VENDOR_ROWS = [
    ["Summary Fuel Depot", "130.00", "37.14%"],
    ["Summary Supply Co", "205.00", "58.57%"],
    ["No name", "15.00", "4.29%"],
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


def _login(browser, site):
    browser.navigate(site.base_url + "/login")
    browser.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
        document.querySelector('[name=password]').value=%s;
        document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
        % (json.dumps(site.login), json.dumps(PASSWORD)))
    browser.wait_for("!!document.querySelector('.nav-group')")


def _open_report(browser, site, verb, heading):
    browser.navigate(f"{site.base_url}/c/{site.company_id}/report/{verb}")
    browser.wait_for("!!document.querySelector('[name=\"f:date_from\"]')")
    assert browser.evaluate("document.querySelector('h1').textContent") == heading
    # The filter form restarts the report; it never carries a continuation.
    assert browser.evaluate("!document.querySelector('form[data-generated-form] [name=\"f:cursor\"]')")
    _fill(browser, {"date_from": FROM, "date_to": TO, "limit": "200"})
    browser.wait_for("!!document.querySelector('#summary-rows')")
    assert not browser.evaluate("document.querySelector('.error')?.textContent"), \
        browser.evaluate("document.body.innerText")


@pytest.mark.timeout(300)
@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_an_owner_reaches_all_four_summaries_and_reads_the_money(browser_site, tmp_path, width, height):  # noqa: F811
    site = browser_site
    browser = _Cdp(tmp_path / f"summaries-chrome-{width}")
    try:
        browser.viewport(width, height)
        _login(browser, site)

        def run(command, body, *, reason=None):
            headers = {"X-Bookflow-Reason": reason} if reason else {}
            return _command(browser, site, command.replace(" ", "."), body, **headers)

        build(run)

        # From the home window's Reports tile, not from a URL nobody would type.
        browser.navigate(f"{site.base_url}/c/{site.company_id}/")
        browser.wait_for("!!document.querySelector('a[href$=\"/_group/reports\"]')")
        browser.evaluate("document.querySelector('a[href$=\"/_group/reports\"]').click()")
        browser.wait_for("!!document.querySelector('a[href$=\"/report/sales-by-customer\"]')")
        for verb in ("sales-by-item", "sales-by-rep", "expenses-by-vendor"):
            assert browser.evaluate(f"!!document.querySelector('a[href$=\"/report/{verb}\"]')"), verb
        browser.evaluate("document.querySelector('a[href$=\"/report/sales-by-customer\"]').click()")
        browser.wait_for("!!document.querySelector('[name=\"f:date_from\"]')")
        assert browser.evaluate("document.querySelector('h1').textContent") == "Sales by customer"

        _fill(browser, {"date_from": FROM, "date_to": TO, "limit": "200"})
        browser.wait_for("!!document.querySelector('#summary-rows')")
        assert browser.evaluate(_CELLS % "#summary-rows") == CUSTOMER_ROWS
        assert browser.evaluate(_CELLS % "#summary-totals") == [["625.00"]]
        assert "4 customers on this page" in browser.evaluate(
            "document.querySelector('#summary-page-count').textContent")
        assert browser.evaluate("!document.querySelector('#summary-next-page')")
        _no_sideways_scroll(browser)

        # The customer's own statement for the same period, opened from the row. The
        # link carries the filter into the next report's form rather than running it.
        browser.evaluate("""[...document.querySelectorAll('#summary-rows a')]
            .find(a => a.textContent.trim() === 'Summary Ridge Builders').click()""")
        browser.wait_for("!!document.querySelector('[name=\"f:customer\"]')")
        assert browser.evaluate("document.querySelector('h1').textContent") == "Customer statement"
        assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == FROM
        assert browser.evaluate("document.querySelector('[name=\"f:date_to\"]').value") == TO
        assert browser.evaluate("document.querySelector('[name=\"f:customer\"]').value").startswith("01")

        _open_report(browser, site, "sales-by-item", "Sales by item")
        assert browser.evaluate(_CELLS % "#summary-rows") == ITEM_ROWS
        # The report states what had no item rather than dropping it: item income and
        # no-item income sit beside the total a reader compares with the profit and loss.
        assert browser.evaluate(_CELLS % "#summary-totals") == [["525.00", "100.00", "625.00"]]
        assert "No item" in browser.evaluate("document.querySelector('#summary-rows').innerText")
        _no_sideways_scroll(browser)

        _open_report(browser, site, "sales-by-rep", "Sales by rep")
        assert browser.evaluate(_CELLS % "#summary-rows") == REP_ROWS
        assert browser.evaluate(_CELLS % "#summary-totals") == [["625.00"]]
        _no_sideways_scroll(browser)

        _open_report(browser, site, "expenses-by-vendor", "Expenses by vendor")
        assert browser.evaluate(_CELLS % "#summary-rows") == VENDOR_ROWS
        assert browser.evaluate(_CELLS % "#summary-totals") == [["350.00"]]
        _no_sideways_scroll(browser)

        # A vendor's own open bills, opened from that vendor's row.
        browser.evaluate("""[...document.querySelectorAll('#summary-rows a')]
            .find(a => a.textContent.trim() === 'Summary Supply Co').click()""")
        browser.wait_for("!!document.querySelector('[name=\"f:vendor\"]')")
        assert browser.evaluate("document.querySelector('h1').textContent") == "Unpaid bills"
        assert browser.evaluate("document.querySelector('[name=\"f:as_of\"]').value") == TO
    finally:
        browser.close()


@pytest.mark.timeout(300)
def test_a_summary_page_walks_and_a_stale_continuation_restarts_in_the_browser(browser_site, tmp_path):  # noqa: F811
    site = browser_site
    browser = _Cdp(tmp_path / "summaries-chrome-paging")
    try:
        browser.viewport(1280, 900)
        _login(browser, site)

        def run(command, body, *, reason=None):
            headers = {"X-Bookflow-Reason": reason} if reason else {}
            return _command(browser, site, command.replace(" ", "."), body, **headers)

        build(run)
        browser.navigate(f"{site.base_url}/c/{site.company_id}/report/sales-by-customer")
        browser.wait_for("!!document.querySelector('[name=\"f:date_from\"]')")
        _fill(browser, {"date_from": FROM, "date_to": TO, "limit": "1"})
        browser.wait_for("!!document.querySelector('#summary-rows')")
        assert browser.evaluate(_CELLS % "#summary-rows") == CUSTOMER_ROWS[:1]
        # Whole-report totals on every page, not this page's own arithmetic.
        assert browser.evaluate(_CELLS % "#summary-totals") == [["625.00"]]
        assert browser.evaluate("!!document.querySelector('#summary-next-page')")
        browser.evaluate("document.querySelector('#summary-next-page button').click()")
        browser.wait_for("""document.querySelector('#summary-rows')
            ?.innerText.includes('Summary Ridge Builders')""")
        assert browser.evaluate(_CELLS % "#summary-rows") == CUSTOMER_ROWS[1:2]
        assert browser.evaluate(_CELLS % "#summary-totals") == [["625.00"]]

        # A company write while the reader is mid-report stales the continuation.
        run("customer create", {"name": "Summary browser witness"})
        browser.evaluate("document.querySelector('#summary-next-page button').click()")
        browser.wait_for("!!document.querySelector('#report-restart')")
        assert "E_QUERY_STALE" in browser.evaluate("document.querySelector('.error').textContent")
        assert not browser.evaluate("!!document.querySelector('#summary-rows')")
        # And the visible filter form still restarts a fresh report from here.
        assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == FROM
        assert browser.evaluate("!document.querySelector('form[data-generated-form] [name=\"f:cursor\"]')")
        _fill(browser, {"date_from": FROM, "date_to": TO, "limit": "200"})
        browser.wait_for("!!document.querySelector('#summary-rows')")
        assert browser.evaluate(_CELLS % "#summary-rows") == CUSTOMER_ROWS
        assert browser.evaluate(_CELLS % "#summary-totals") == [["625.00"]]
    finally:
        browser.close()
