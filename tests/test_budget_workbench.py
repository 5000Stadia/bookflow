"""R109 in the workbench: the Set Up Budgets grid, the budget and comparison report pages, Memorize on a
report page, and the memorized report list that opens a report on today's dates."""
import re
from datetime import date

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def test_budget_grid_reports_and_memorized_reports(hosted):  # noqa: F811
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    company = hosted.company_id
    base = f"/companies/{company}/commands"

    def run(name, body):
        from bookflow.core import registry
        reason = {"X-Bookflow-Reason": "budget witness"} if registry.get(name).is_write else {}
        answered = browser.post(f"{base}/{name.replace(' ', '.')}", json=body, headers={**HEADERS, **reason})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    income = run("account create", {"name": "BW Sales", "type": "income"})["id"]
    budget = run("budget create", {"name": "BW Plan", "fiscal_year": 2026})["budget"]

    # The grid: accounts down, months across; typing a row and saving writes it with budget set.
    grid = browser.get(f"/c/{company}/budget/{budget['id']}", headers=HEADERS)
    assert grid.status_code == 200 and "data-budget-table" in grid.text
    assert f'name="m:{income}:0"' in grid.text and f'name="m:{income}:11"' in grid.text
    assert ">Jan 26<" in grid.text and ">Dec 26<" in grid.text
    form = {f"m:{income}:{i}": "" for i in range(12)}
    form.update({f"m:{income}:0": "1,200.00", f"m:{income}:1": "1300", "version": str(budget["version"]),
                 "action": "save", "ctx:reason": "first pass"})
    saved = browser.post(f"/c/{company}/budget/{budget['id']}", data=form, headers=HEADERS)
    assert saved.status_code == 200 and "Saved 2 month amounts." in saved.text
    row = next(r for r in run("budget show", {"budget": "BW Plan"})["rows"] if r["account_id"] == income)
    assert [cell["amount"] for cell in row["amounts"]][:3] == ["1200.00", "1300.00", "0.00"]
    filled = browser.post(f"/c/{company}/budget/{budget['id']}", headers=HEADERS, data={
        "action": "fill", "fill_account": income, "fill_start_month": "2026-03", "fill_amount": "1000.00",
        "fill_change_percent": "10"})
    assert filled.status_code == 200 and "Filled the row" in filled.text
    row = next(r for r in run("budget show", {"budget": "BW Plan"})["rows"] if r["account_id"] == income)
    assert [cell["amount"] for cell in row["amounts"]][2:5] == ["1000.00", "1100.00", "1210.00"]
    stale = browser.post(f"/c/{company}/budget/{budget['id']}", headers=HEADERS,
                         data={**form, f"m:{income}:0": "5.00", "version": "1"})
    assert stale.status_code == 400 and "E_VERSION_CONFLICT" in stale.text

    # The reports: a column group per quarter over Actual / Budget / $ Over / % of Budget, with Memorize.
    report = browser.get(f"/c/{company}/report/budget-vs-actual?f:budget=BW Plan&f:columns=quarter", headers=HEADERS)
    assert report.status_code == 200 and 'id="comparison-lines"' in report.text
    assert 'scope="colgroup" colspan="4" class="num">Jan – Mar 2026' in report.text
    assert "% of Budget" in report.text and 'id="report-memorize"' in report.text
    for path in ("/report/budget-overview?f:budget=BW Plan", "/report/profit-and-loss-comparison",
                 "/report/balance-sheet-comparison"):
        page = browser.get(f"/c/{company}{path}", headers=HEADERS)
        assert page.status_code == 200 and 'id="comparison-lines"' in page.text, path
    centre = browser.get(f"/c/{company}/_group/reports", headers=HEADERS).text
    for verb in ("budget-vs-actual", "budget-overview", "profit-and-loss-comparison", "balance-sheet-comparison"):
        assert f"/report/{verb}" in centre, verb

    # Memorize the profit and loss for this calendar year: the matching named range is offered.
    today = date.today()
    first, last = f"{today.year}-01-01", f"{today.year}-12-31"
    memorize = browser.get(f"/c/{company}/report/profit-and-loss/memorize?f:date_from={first}&f:date_to={last}",
                           headers=HEADERS)
    assert memorize.status_code == 200
    assert re.search(r'<option value="this_calendar_year" selected>', memorize.text) or \
        re.search(r'<option value="this_fiscal_year" selected>', memorize.text)
    done = browser.post(f"/c/{company}/report/profit-and-loss/memorize", headers=HEADERS, follow_redirects=False,
                        data={"f:date_from": first, "f:date_to": last, "name": "BW Year P&L", "group": "Company",
                              "date_range": "this_calendar_year"})
    assert done.status_code == 303 and done.headers["location"] == f"/c/{company}/memorized-report"
    listing = browser.get(f"/c/{company}/memorized-report", headers=HEADERS)
    assert "BW Year P&amp;L" in listing.text and 'data-memorized-group="Company"' in listing.text
    kept = run("memorized-report list", {})["items"][0]
    assert kept["filters"] == {} and kept["date_range"] == "this_calendar_year"
    opened = browser.get(f"/c/{company}/memorized-report/{kept['id']}/open", headers=HEADERS, follow_redirects=False)
    assert opened.status_code == 303
    assert f"f%3Adate_from={first}" in opened.headers["location"] and f"f%3Adate_to={last}" in opened.headers["location"]
