"""The ten everyday reports opened in a real browser: each page arrives with its figures.

The owner's path is the Reports tile, then a report. Each report is opened the way a
drill-down or a date chip opens it -- its filters in the address -- and the headline figure
read off the rendered page is the hand-computed figure `test_everyday_reports` checks the
command for. At phone width no report pushes the page sideways.
"""
from urllib.parse import urlencode

import pytest

from tests.test_everyday_reports import PAGES
from tests.test_row5_browser_acceptance import CHROME, _Cdp, browser_site  # noqa: F401
from tests.test_summary_reports_browser import _login, _no_sideways_scroll

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")

HEADINGS = {
    "customer-balance-summary": "Customer balance summary",
    "customer-balance-detail": "Customer balance detail",
    "vendor-balance-summary": "Vendor balance summary",
    "vendor-balance-detail": "Vendor balance detail",
    "open-purchase-orders": "Open purchase orders",
    "purchases-by-vendor": "Purchases by vendor summary",
    "purchases-by-item": "Purchases by item summary",
    "deposit-detail": "Deposit detail",
    "transaction-list-by-date": "Transaction list by date",
    "vendor-1099-summary": "1099 summary",
}


@pytest.mark.timeout(600)
def test_every_everyday_report_opens_with_its_figures(browser_site, tmp_path):  # noqa: F811
    site = browser_site
    browser = _Cdp(tmp_path / "everyday-chrome")
    try:
        browser.viewport(390, 844)
        _login(browser, site)
        browser.navigate(f"{site.base_url}/c/{site.company_id}/_group/reports")
        browser.wait_for("!!document.querySelector('a[href$=\"/report/vendor-1099-summary\"]')")
        for verb in HEADINGS:
            assert browser.evaluate(f"!!document.querySelector('a[href$=\"/report/{verb}\"]')"), verb
        for verb, heading in HEADINGS.items():
            query, figure = PAGES[verb]
            browser.navigate(f"{site.base_url}/c/{site.company_id}/report/{verb}?" + urlencode(query))
            browser.wait_for("!!document.querySelector('#everyday-rows') || !!document.querySelector('.error')")
            assert not browser.evaluate("document.querySelector('.error')?.textContent"), \
                (verb, browser.evaluate("document.body.innerText"))
            assert browser.evaluate("document.querySelector('h1').textContent") == heading
            shown = browser.evaluate("document.querySelector('#everyday-report').innerText")
            assert figure in shown, (verb, shown[:600])
            rows = browser.evaluate("document.querySelectorAll('#everyday-rows tbody tr').length")
            assert rows >= 1, verb
            assert browser.evaluate("!!document.querySelector('#report-print-all')"), verb
            assert browser.evaluate("!!document.querySelector('#report-export')"), verb
            _no_sideways_scroll(browser)
    finally:
        browser.close()
