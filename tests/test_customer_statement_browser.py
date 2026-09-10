"""The statement in a real browser, on a real phone width, from the flow board.

A statement is the receivables page a customer reads, so the measurement that
matters is not that the page did not scroll: it is that the table itself fits
the width it was given. A table wider than its own box scrolls sideways inside a
page that looks fine, and the reader never finds the balance.
"""
import base64
import json

import pytest

from tests.test_customer_statement import (
    EXPECTED_AGING, EXPECTED_ROWS, EXPECTED_TOTALS, FROM, TO, build,
)
from tests.test_financial_statements_browser import fill
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")


def cells(browser, selector):
    return browser.evaluate("""[...document.querySelectorAll(%s)].map(row => ({
        kind: row.dataset.kind, entry: row.dataset.entry,
        values: [...row.querySelectorAll('[data-value]')].map(cell => {
            const copy = cell.cloneNode(true);
            copy.querySelectorAll('.document-cell-label').forEach(e => e.remove());
            return copy.textContent.trim();})}))""" % json.dumps(selector))


def fits(browser, selector):
    """The element's own content against its own box, which is the real question."""
    return browser.evaluate("""(() => {const e = document.querySelector(%s);
        return e === null ? null : {scroll: e.scrollWidth, client: e.clientWidth};})()""" % json.dumps(selector))


@pytest.mark.timeout(180)
def test_a_statement_reads_as_a_document_at_both_widths_and_pages_without_restarting(browser_site, tmp_path):  # noqa: F811
    site = browser_site
    browser = _Cdp(tmp_path / "statement-chrome")
    try:
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

        # The flow board's Statement tile is the way in, and it lands on a form.
        browser.navigate(f"{site.base_url}/c/{site.company_id}/")
        browser.wait_for("!!document.querySelector('a[href$=\"/report/statement\"]')")
        assert browser.evaluate("document.querySelector('a[href$=\"/report/statement\"]').textContent").strip()
        browser.evaluate("document.querySelector('a[href$=\"/report/statement\"]').click()")
        browser.wait_for("!!document.querySelector('[name=\"f:date_from\"]')")
        assert browser.evaluate("document.querySelector('h1').textContent") == "Customer statement"
        fill(browser, {"date_from": FROM, "date_to": TO, "limit": "200"})
        browser.wait_for("!!document.querySelector('#statement-lines tbody tr')")

        assert not browser.evaluate("document.querySelector('.error')?.textContent"), \
            browser.evaluate("document.body.innerText")
        assert cells(browser, "#statement-lines tbody tr") == [
            {"kind": kind, "entry": entry,
             "values": [f"{amount / 100:.2f}", f"{balance / 100:.2f}"]}
            for _customer, kind, entry, _date, _number, amount, balance in EXPECTED_ROWS]
        for key, minor in EXPECTED_TOTALS.items():
            assert browser.evaluate(f"document.querySelector('[data-total={key}] dd').textContent") \
                == f"{minor / 100:.2f} USD", key
        for key, minor in EXPECTED_AGING.items():
            assert browser.evaluate(f"document.querySelector('[data-aging={key}] dd').textContent") \
                == f"{minor / 100:.2f} USD", key
        # The document a row names opens where it was written.
        assert browser.evaluate("""!!document.querySelector('#statement-lines a[href*="/invoice/"]')""")
        assert browser.evaluate("""!!document.querySelector('#statement-lines a[href*="/payment/"]')""")
        # Nothing on the page claims it can be delivered; this product cannot.
        assert browser.evaluate("""[...document.querySelectorAll('#customer-statement a,#customer-statement button')]
            .filter(e => /send|e-?mail|print|deliver|mail/i.test(e.textContent)).length""") == 0

        for width, height in ((1280, 900), (390, 844)):
            browser.viewport(width, height)
            page = fits(browser, "html")
            assert page["scroll"] == page["client"], (width, page)
            for selector in ("#statement-lines", ".report-lines-wrap", "#statement-totals", "#statement-aging"):
                box = fits(browser, selector)
                assert box["scroll"] == box["client"], (width, selector, box)
            # The measurement above can also pass because nothing is there, so
            # check the layout the width is supposed to have actually arrived.
            row = browser.evaluate("getComputedStyle(document.querySelector('#statement-lines tbody tr')).display")
            assert row == ("grid" if width == 390 else "table-row"), (width, row)
            if width == 390:
                caption = browser.evaluate("""({caption: document.querySelector('#statement-lines caption').clientWidth,
                    table: document.querySelector('#statement-lines').clientWidth})""")
                assert caption["caption"] >= caption["table"] - 2, caption
                # Every card names its own columns, or a phone reader gets bare numbers.
                assert browser.evaluate("""[...document.querySelectorAll('#statement-lines tbody tr')]
                    .every(r => r.querySelectorAll('.document-cell-label').length === 5)""")
                assert browser.evaluate("""getComputedStyle(
                    document.querySelector('#statement-lines .document-cell-label')).display""") == "block"
            (tmp_path / f"customer-statement-{width}.png").write_bytes(base64.b64decode(
                browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})["data"]))

        # A page boundary must not restart the running balance or the totals.
        browser.viewport(390, 844)
        fill(browser, {"date_from": FROM, "date_to": TO, "limit": "4"})
        browser.wait_for("document.querySelectorAll('#statement-lines tbody tr').length === 4")
        first = cells(browser, "#statement-lines tbody tr")
        assert [row["values"][1] for row in first] == ["250.00", "250.00", "500.00", "900.00"]
        assert browser.evaluate("document.querySelector('[data-total=closing] dd').textContent") == "630.00 USD"
        browser.evaluate("document.querySelector('#customer-statement-next-page button').click()")
        browser.wait_for("document.querySelector('#statement-lines tbody tr')?.dataset.entry === 'payment'")
        second = cells(browser, "#statement-lines tbody tr")
        assert [row["values"][1] for row in second] == ["600.00", "450.00", "380.00", "380.00"]
        assert browser.evaluate("document.querySelector('[data-total=closing] dd').textContent") == "630.00 USD"
        assert browser.evaluate("document.querySelectorAll('form[data-generated-form] [name=\"f:cursor\"]').length") == 0
        box = fits(browser, "#statement-lines")
        assert box["scroll"] == box["client"], box

        # One customer, from the customer's own row, is a statement of its own.
        browser.viewport(1280, 900)
        fill(browser, {"date_from": FROM, "date_to": TO, "limit": "200"})
        browser.wait_for("document.querySelectorAll('#statement-lines tbody tr').length === %d" % len(EXPECTED_ROWS))
        browser.evaluate("""[...document.querySelectorAll('#statement-lines a')]
            .find(a => a.textContent.trim() === 'Quill Signage').click()""")
        browser.wait_for("!!document.querySelector('#report-source-state')")
        assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == FROM
        assert browser.evaluate("document.querySelector('[name=\"f:customer\"]').value")
        fill(browser, {"limit": "200"})
        browser.wait_for("document.querySelector('[data-total=closing] dd')?.textContent === '250.00 USD'")
        assert browser.evaluate("""[...new Set([...document.querySelectorAll('#statement-lines tbody tr')]
            .map(r => r.dataset.customer))].length""") == 1
        assert browser.evaluate("document.querySelectorAll('#statement-lines tbody tr').length") == 2
        assert "books changed" not in browser.evaluate("document.querySelector('#report-source-state').textContent")
    finally:
        browser.close()
