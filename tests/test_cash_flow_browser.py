"""Both new reports driven in a real browser, from the home window's Reports tile.

Every figure asserted here is read off the rendered page. The two that matter
most are checked against something the page cannot invent: the cash flow
statement's closing cash against the bank accounts ``report balance-sheet``
returns for the same date, and each report's net income against what ``report
profit-and-loss`` returns for the same two dates.
"""
import base64
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")

DATE_FROM, DATE_TO = "2026-03-01", "2026-03-31"

# name, type, tax line
ACCOUNTS = (("CF checking", "bank", None),
            ("CF prepaid cover", "other_current_asset", None),
            ("CF equipment", "fixed_asset", None),
            ("CF equipment loan", "long_term_liability", None),
            ("CF service fees", "income", "Gross receipts"),
            ("CF supplies used", "expense", "Other deductions"),
            ("CF sundry", "expense", None))

# date, amount, debit account, credit account
ENTRIES = (("2026-03-02", "500.00", "CF checking", "CF service fees"),
           ("2026-03-03", "120.00", "CF supplies used", "CF checking"),
           ("2026-03-04", "80.00", "CF prepaid cover", "CF checking"),
           ("2026-03-05", "1000.00", "CF equipment", "CF equipment loan"),
           ("2026-03-06", "45.00", "CF sundry", "CF checking"))

_CELLS = """[...document.querySelectorAll('#statement-accounts tbody tr')].map(
    row => [...row.cells].map(cell => cell.textContent.trim()))"""
_TOTAL = "document.querySelector('[data-total=%s] td').textContent.trim()"


def _login(browser, site):
    browser.navigate(site.base_url + "/login")
    browser.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
        document.querySelector('[name=password]').value=%s;
        document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
        % (json.dumps(site.login), json.dumps(PASSWORD)))
    browser.wait_for("!!document.querySelector('.nav-group')")


def _fill(browser, fields):
    browser.evaluate("""(() => { const form=document.querySelector('form[data-generated-form]');
        for(const [key,value] of Object.entries(%s)) form.elements.namedItem('f:'+key).value=value;
        form.querySelector('button[value=submit]').click(); })()""" % json.dumps(fields))


def _contained(browser):
    assert browser.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"), \
        browser.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")


def _build(run):
    made = {}
    for name, kind, line in ACCOUNTS:
        body = {"name": name, "type": kind}
        if line is not None:
            body["tax_line"] = line
        made[name] = run("account.create", body)
    for index, (date, amount, debit, credit) in enumerate(ENTRIES):
        run("journal.post", {"date": date, "number": f"CF-{index}", "lines": [
            {"account": made[debit]["id"], "side": "debit", "amount": amount},
            {"account": made[credit]["id"], "side": "credit", "amount": amount}]})
    return made


@pytest.mark.timeout(300)
@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_a_bookkeeper_reaches_both_new_reports_and_reads_money_that_reconciles(browser_site, tmp_path, width, height):  # noqa: F811
    site = browser_site
    browser = _Cdp(tmp_path / f"cash-flows-chrome-{width}")
    try:
        browser.viewport(width, height)
        _login(browser, site)
        run = lambda command, body: _command(browser, site, command, body)
        _build(run)

        # From the home window's Reports tile, not from a URL nobody would type.
        browser.navigate(f"{site.base_url}/c/{site.company_id}/")
        browser.wait_for("!!document.querySelector('a[href$=\"/_group/reports\"]')")
        browser.evaluate("document.querySelector('a[href$=\"/_group/reports\"]').click()")
        browser.wait_for("!!document.querySelector('a[href$=\"/report/cash-flows\"]')")
        assert browser.evaluate("!!document.querySelector('a[href$=\"/report/income-tax-summary\"]')")

        # --- The statement of cash flows -------------------------------------
        browser.evaluate("document.querySelector('a[href$=\"/report/cash-flows\"]').click()")
        browser.wait_for("!!document.querySelector('[name=\"f:date_from\"]')")
        assert browser.evaluate("document.querySelector('h1').textContent") == "Statement of cash flows"
        # The visible filter form restarts the report; it never carries a continuation.
        assert browser.evaluate("!document.querySelector('form[data-generated-form] [name=\"f:cursor\"]')")
        _fill(browser, {"date_from": DATE_FROM, "date_to": DATE_TO, "limit": "200"})
        browser.wait_for("!!document.querySelector('#statement-accounts')")
        assert not browser.evaluate("document.querySelector('.error')?.textContent")

        money = lambda key: browser.evaluate(_TOTAL % key)
        # The report's own reconciliation, read off the page.
        assert money("difference") == "0.00"
        value = lambda key: int(round(float(money(key).replace(",", "")) * 100))
        assert value("opening_cash") + value("net_change_in_cash") == value("closing_cash")
        assert (value("net_income") + value("operating_adjustments") + value("investing")
                + value("financing")) == value("closing_cash") - value("opening_cash")

        # And the same figures against the two statements that already exist.
        sheet = run("report.balance-sheet", {"date_to": DATE_TO, "limit": 200})
        assert sheet["next_cursor"] is None, "the demo chart outgrew one balance sheet page"
        banks = sum(row["amount"]["minor_units"] for row in sheet["rows"] if row["account_type"] == "bank")
        assert value("closing_cash") == banks
        profit = run("report.profit-and-loss", {"date_from": DATE_FROM, "date_to": DATE_TO, "limit": 200})
        assert value("net_income") == profit["totals"]["net_income"]["minor_units"]

        # Each account lands in the section its type declares, with its own sign.
        # Section, account, opening, closing, cash effect. Opening and closing
        # read on the account's own normal side, so the loan reads as what is owed.
        rows = {row[1]: row for row in browser.evaluate(_CELLS)}
        assert rows["CF prepaid cover"] == ["Operating", "CF prepaid cover", "0.00", "80.00", "-80.00"]
        assert rows["CF equipment"] == ["Investing", "CF equipment", "0.00", "1000.00", "-1000.00"]
        assert rows["CF equipment loan"] == ["Financing", "CF equipment loan", "0.00", "1000.00", "1000.00"]
        assert "CF checking" not in rows, "cash is what the statement explains, not a row in it"
        sections = [row[0] for row in browser.evaluate(_CELLS)]
        assert sections == sorted(sections, key=["Operating", "Investing", "Financing"].index)
        _contained(browser)
        (tmp_path / f"cash-flows-{width}.png").write_bytes(base64.b64decode(
            browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})["data"]))

        # An amount opens that account's own current general ledger.
        browser.evaluate("""[...document.querySelectorAll('#statement-accounts tr')]
            .find(r => r.textContent.includes('CF equipment loan')).querySelector('a').click()""")
        browser.wait_for("!!document.querySelector('#report-source-state')")
        assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == DATE_FROM

        # --- The income tax summary ------------------------------------------
        browser.navigate(f"{site.base_url}/c/{site.company_id}/report/income-tax-summary")
        browser.wait_for("!!document.querySelector('[name=\"f:date_from\"]')")
        assert browser.evaluate("document.querySelector('h1').textContent") == "Income tax summary"
        _fill(browser, {"date_from": DATE_FROM, "date_to": DATE_TO, "limit": "200"})
        browser.wait_for("!!document.querySelector('#statement-accounts')")
        assert not browser.evaluate("document.querySelector('.error')?.textContent")
        assert browser.evaluate(_TOTAL % "net_income") == profit["totals"]["net_income"]["amount"]

        listed = browser.evaluate("""[...document.querySelectorAll('#statement-accounts tbody tr')].map(
            row => [row.dataset.kind, row.dataset.taxLine, ...[...row.cells].map(c => c.textContent.trim())])""")
        groups = {row[1]: row for row in listed if row[0] == "tax_line"}
        assert set(groups) >= {"Gross receipts", "Other deductions", "Unassigned"}
        assert groups["Gross receipts"][2:] == ["Gross receipts", "Total of 1 account", "500.00"]
        assert groups["Other deductions"][2:] == ["Other deductions", "Total of 1 account", "120.00"]
        accounts = {row[3]: row for row in listed if row[0] == "account"}
        assert accounts["CF service fees"][1] == "Gross receipts" and accounts["CF service fees"][4] == "500.00"
        assert accounts["CF supplies used"][1] == "Other deductions" and accounts["CF supplies used"][4] == "120.00"
        assert accounts["CF sundry"][1] == "Unassigned" and accounts["CF sundry"][4] == "45.00"
        # Every group total is the accounts printed under it.
        for line, group in groups.items():
            members = [float(row[4]) for row in listed if row[0] == "account" and row[1] == line]
            assert round(sum(members), 2) == float(group[4]), line
        _contained(browser)
        (tmp_path / f"income-tax-summary-{width}.png").write_bytes(base64.b64decode(
            browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})["data"]))
    finally:
        browser.close()


@pytest.mark.timeout(300)
def test_both_reports_page_and_restart_a_stale_continuation_in_the_browser(browser_site, tmp_path):  # noqa: F811
    site = browser_site
    browser = _Cdp(tmp_path / "cash-flows-chrome-paging")
    try:
        browser.viewport(1280, 900)
        _login(browser, site)
        run = lambda command, body: _command(browser, site, command, body)
        _build(run)
        for report, totals in (("cash-flows", "difference"), ("income-tax-summary", "net_income")):
            browser.navigate(f"{site.base_url}/c/{site.company_id}/report/{report}")
            browser.wait_for("!!document.querySelector('[name=\"f:date_from\"]')")
            _fill(browser, {"date_from": DATE_FROM, "date_to": DATE_TO, "limit": "1"})
            browser.wait_for("!!document.querySelector('#statement-accounts')")
            first = browser.evaluate(_CELLS)
            printed = browser.evaluate("document.querySelector('#statement-accounts tbody').textContent")
            whole = browser.evaluate(_TOTAL % totals)
            assert len(first) == 1
            assert browser.evaluate("!!document.querySelector('#statement-next-page')")
            browser.evaluate("document.querySelector('#statement-next-page button').click()")
            browser.wait_for("document.querySelector('#statement-accounts tbody')?.textContent!==%s"
                             % json.dumps(printed))
            # Whole-report totals on page two, not page two's own arithmetic.
            assert browser.evaluate(_TOTAL % totals) == whole
            assert browser.evaluate(_CELLS) != first

            # A company write while the reader is mid-report stales the continuation.
            run("account.create", {"name": f"CF witness {report}", "type": "expense"})
            browser.evaluate("document.querySelector('#statement-next-page button').click()")
            browser.wait_for("!!document.querySelector('#report-restart')")
            assert "E_QUERY_STALE" in browser.evaluate("document.querySelector('.error').textContent")
            # And the visible filter form still restarts a fresh report from here.
            assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == DATE_FROM
            assert browser.evaluate("!document.querySelector('form[data-generated-form] [name=\"f:cursor\"]')")
            _fill(browser, {"date_from": DATE_FROM, "date_to": DATE_TO, "limit": "200"})
            browser.wait_for("!!document.querySelector('#statement-accounts')")
            assert browser.evaluate(_TOTAL % totals) == whole
    finally:
        browser.close()
