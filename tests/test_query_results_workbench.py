"""A query form shows its rows on its own page, under the filters that chose them.

Submitting "Find rates" used to redirect to the Overview (rate has no list command to land on)
with the answer only as JSON in a one-time notice. Every query-style form now answers in place.
"""
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def _browser(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def _results(html):
    section = re.search(r'<section class="workspace-card" id="query-results".*?</table>', html, re.S)
    assert section, html[:2000]
    return section.group(0)


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
