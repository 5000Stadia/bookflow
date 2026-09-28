"""A read-only command's form shows its answer on its own page, under the inputs that asked.

Submitting "Find rates" used to redirect to the Overview (rate has no list command to land on)
with the answer only as JSON in a one-time notice; so did every read-only command with no record
or list page to land on. A `list` still opens its list page and a `show` its record page.
"""
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def _browser(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def _results(html, end='</table>'):
    section = re.search(r'<section class="workspace-card" id="command-result".*?' + end, html, re.S)
    assert section, html[:2000]
    return section.group(0)


def _submit(browser, path, data):
    page = browser.post(path, data=data, headers=HEADERS, follow_redirects=False)
    assert page.status_code == 200 and "location" not in page.headers, (page.status_code, page.headers.get("location"))
    assert "save-feedback" not in page.text  # no one-time notice carrying the answer
    return page


def test_find_rates_shows_the_matching_rates_under_the_form(hosted):
    browser = _browser(hosted)
    base = f"/companies/{hosted.company_id}/commands"
    for currency, rate in (("EUR", "1.08"), ("GBP", "1.27")):
        answered = browser.post(f"{base}/rate.set", json={"date": "2026-03-11", "from_currency": currency, "rate": rate},
                                headers=HEADERS)
        assert answered.status_code == 200, answered.text[:400]
    page = browser.post(f"/c/{hosted.company_id}/rate/query", data={"f:from_currency": "EUR"},
                        headers=HEADERS, follow_redirects=False)
    assert page.status_code == 200 and "location" not in page.headers
    table = _results(page.text)
    assert "EUR" in table and "1.08" in table and "GBP" not in table
    assert "save-feedback" not in page.text  # no one-time notice carrying the answer
    # The filter stays entered, and the row opens the rate it names.
    assert re.search(r'name="f:from_currency"[^>]*value="EUR"', page.text)
    assert re.search(rf'href="/c/{hosted.company_id}/rate/\w+"', table)


def test_another_query_form_answers_in_place_with_a_next_page(hosted):
    browser = _browser(hosted)
    page = browser.post(f"/c/{hosted.company_id}/customer/query", data={"f:limit": "1"},
                        headers=HEADERS, follow_redirects=False)
    assert page.status_code == 200 and "location" not in page.headers
    _results(page.text)
    assert "data-query-next=" in page.text


def test_calculations_and_lookups_answer_in_place_with_readable_money(hosted):
    browser = _browser(hosted)
    company = f"/c/{hosted.company_id}"
    sources = _submit(browser, f"{company}/deposit/sources", {"f:date": "2026-09-27"})
    shown = _results(sources.text, end='</details>')
    assert "Subtotal" in shown and "$" in shown and "Technical result (JSON)" in shown
    assert re.search(r'name="f:date"[^>]*value="2026-09-27"', sources.text)
    liability = _submit(browser, f"{company}/sales-tax/liability", {"f:as_of": "2026-09-27"})
    assert "Technical result (JSON)" in _results(liability.text, end='</details>')


def test_a_list_and_a_show_still_open_their_own_pages(hosted):
    browser = _browser(hosted)
    company = f"/c/{hosted.company_id}"
    customer = browser.post(f"/companies/{hosted.company_id}/commands/customer.create", json={"name": "Show landing"},
                            headers=HEADERS).json()["id"]
    shown = browser.post(f"{company}/customer/show", data={"f:customer": customer}, headers=HEADERS, follow_redirects=False)
    assert shown.status_code == 303 and shown.headers["location"].startswith(f"{company}/customer/{customer}")
    listed = browser.post(f"{company}/customer/list", data={}, headers=HEADERS, follow_redirects=False)
    assert listed.status_code == 303 and listed.headers["location"].startswith(f"{company}/customer?")


def test_result_tables_name_the_records_a_row_points_at(hosted):
    """A customer or payment method in a result row reads as its name and opens its record."""
    browser = _browser(hosted)
    company = f"/c/{hosted.company_id}"
    page = _submit(browser, f"{company}/payment/query", {})
    table = _results(page.text)
    customer = re.search(rf'<a href="{company}/customer/(\w+)">([^<]+)</a>', table)
    assert customer and customer.group(2) != customer.group(1)
    assert re.search(rf'<a href="{company}/payment-method/\w+">[^<]*[a-z][^<]*</a>', table)
    # An id nobody can open (a revision) is left to the technical result, not a column.
    orders = _results(_submit(browser, f"{company}/purchase-order/query", {}).text)
    assert re.search(rf'<a href="{company}/vendor/\w+">[^<]*[a-z][^<]*</a>', orders)
    assert "Current revision" not in orders
    assert not re.search(r'<td[^>]*>[0-9A-Z]{26}</td>', orders)


def test_sales_tax_owed_reads_a_period_as_the_anchor_report_does(hosted):
    """The Sales tax owed page opens on a from/to range, not only an as-of date (R72)."""
    browser = _browser(hosted)
    company = f"/c/{hosted.company_id}"
    opened = browser.get(f"{company}/sales-tax/liability", headers=HEADERS)
    assert opened.status_code == 200
    assert re.search(r'name="f:date_from"[^>]*value="\d{4}-01-01"', opened.text)
    assert re.search(r'name="f:date_to"[^>]*value="\d{4}-\d{2}-\d{2}"', opened.text)
    month = _submit(browser, f"{company}/sales-tax/liability",
                    {"f:date_from": "2026-09-01", "f:date_to": "2026-09-30"})
    assert "Technical result (JSON)" in _results(month.text, end='</details>')
    assert "2026-09-01" in _results(month.text, end='</details>')
