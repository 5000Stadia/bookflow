"""A report opens with its numbers (R57).

Opening a report runs it on the company-calendar defaults, so the first screen is the
statement and its headline figure rather than an empty filter form. The filters fold into
one summary line with date chips that run the report again; Print and CSV stay the whole
filtered report; what the report is and when it ran sits under About.
"""
import re
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from bookflow.adapters.workbench import date_defaults as DateDefaults

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401


def _browser(hosted) -> TestClient:  # noqa: F811
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def _main(text: str) -> str:
    return text.split("<main", 1)[-1].split("</main>", 1)[0]


def _company(hosted, browser) -> str:  # noqa: F811
    return hosted.company_id if hasattr(hosted, "company_id") else re.search(
        r"/c/([^/\"]+)/", browser.get("/companies").text).group(1)


def test_profit_and_loss_shows_figures_without_pressing_run(hosted):  # noqa: F811
    browser = _browser(hosted)
    company = _company(hosted, browser)
    page = browser.get(f"/c/{company}/report/profit-and-loss")
    assert page.status_code == 200
    body = _main(page.text)
    # The statement and its headline are on the first screen, taken from the command's totals.
    assert 'id="statement-accounts"' in body and 'data-total="net_income"' in body
    headline = re.search(r'<h2 id="report-result"[^>]*>([^<]*)</h2>\s*<p class="report-figure" data-headline="([^"]*)">([^<]*)</p>', body)
    assert headline and headline.group(1) == "Net income", body[:3000]
    final = re.search(r'<tr class="statement-final" data-total="net_income"><th scope="row">Net income</th><td class="num">([^<]*)</td>', body)
    assert final, body
    # The filters are folded, not open, because there is a result to read.
    assert re.search(r'<details class="form-section report-filters" id="report-filters" aria-label="Report filters">', body)
    assert "Jan 1 – " in body and "· Accrual" in body and '<span class="report-edit">Edit</span>' in body
    assert "Whole-statement totals" not in body.split('id="report-about"')[0]


def test_date_from_is_asked_before_date_to(hosted):  # noqa: F811
    browser = _browser(hosted)
    company = _company(hosted, browser)
    for verb in ("profit-and-loss", "cash-flows", "general-ledger", "sales-by-customer"):
        body = _main(browser.get(f"/c/{company}/report/{verb}").text)
        assert body.index('name="f:date_from"') < body.index('name="f:date_to"'), verb


def test_date_chips_run_the_report_again_with_their_range(hosted):  # noqa: F811
    browser = _browser(hosted)
    company = _company(hosted, browser)
    body = _main(browser.get(f"/c/{company}/report/profit-and-loss").text)
    chips = dict((label, href.replace("&amp;", "&")) for href, label in
                 re.findall(r'<a href="([^"]+)"(?: aria-current="true")?>([^<]+)</a>',
                            body.split('class="report-presets"', 1)[1].split("</nav>", 1)[0]))
    assert list(chips) == ["This month", "This quarter", "Year to date", "Last year"]
    query = parse_qs(urlsplit(chips["Last year"]).query)
    year = int(query["f:date_from"][0][:4])
    assert query["f:date_from"] == [f"{year}-01-01"] and query["f:date_to"] == [f"{year}-12-31"]
    rerun = _main(browser.get(chips["Last year"]).text)
    assert 'data-headline="' in rerun and f"Jan 1, {year} – Dec 31, {year}" in rerun
    assert re.search(r'aria-current="true">Last year</a>', rerun)


def test_as_of_reports_offer_end_dates(hosted):  # noqa: F811
    presets = DateDefaults.presets("report balance-sheet", "2026-05-14")
    assert presets == [("Today", {"date_to": "2026-05-14"}),
                       ("End of last month", {"date_to": "2026-04-30"}),
                       ("End of last quarter", {"date_to": "2026-03-31"}),
                       ("End of last year", {"date_to": "2025-12-31"})]
    assert DateDefaults.presets("report cash-flows", "2026-11-03")[1] == (
        "This quarter", {"date_from": "2026-10-01", "date_to": "2026-12-31"})


def test_a_report_without_its_required_inputs_opens_on_its_form(hosted):  # noqa: F811
    browser = _browser(hosted)
    company = _company(hosted, browser)
    # A link that names an account but no dates cannot be run: the form opens, with no error.
    body = _main(browser.get(f"/c/{company}/report/general-ledger?f:account=nothing-yet").text)
    assert 'id="report-result"' not in body and 'class="error"' not in body
    assert re.search(r'id="report-filters" aria-label="Report filters" open>', body)


def test_print_and_csv_are_small_buttons_for_the_whole_filtered_report(hosted):  # noqa: F811
    browser = _browser(hosted)
    company = _company(hosted, browser)
    body = _main(browser.get(f"/c/{company}/report/ar-aging").text)
    actions = body.split('<p class="report-actions">', 1)[1].split("</p>", 1)[0]
    assert 'id="report-print-all"' in actions and '/report/ar-aging/print-all?' in actions
    assert 'id="report-export"' in actions and '/report/ar-aging/export.csv?' in actions
    assert '>Print</a>' in actions and '>CSV</a>' in actions


def test_generated_just_now_with_the_watermark_under_about(hosted):  # noqa: F811
    browser = _browser(hosted)
    company = _company(hosted, browser)
    body = _main(browser.get(f"/c/{company}/report/balance-sheet").text)
    assert re.search(r'Generated <time datetime="[^"]+">just now</time>', body)
    before, about = body.split('id="report-about"', 1)
    # The words on screen above About name no watermark (the print-only heading and the
    # links behind the figures may still carry one).
    on_screen = before.split('class="report-bar', 1)[1]
    assert "watermark" not in re.sub(r"<[^>]+>", " ", on_screen).lower()
    assert re.search(r"audit watermark \d+", about) and "stops and asks you to run the report again" in about


def test_running_from_the_form_marks_the_result_heading_for_focus(hosted):  # noqa: F811
    browser = _browser(hosted)
    company = _company(hosted, browser)
    opened = _main(browser.get(f"/c/{company}/report/profit-and-loss").text)
    assert "data-report-focus" not in opened
    ran = _main(browser.post(f"/c/{company}/report/profit-and-loss", data={
        "f:date_from": "2026-01-01", "f:date_to": "2026-12-31", "action": "submit"},
        headers={"X-Bookflow-Workbench": "1"}).text)
    assert re.search(r'<h2 id="report-result" tabindex="-1" data-report-focus>Net income</h2>', ran)
