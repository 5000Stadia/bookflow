"""Every registered report answers a page a person can read.

A report satisfies four of the five parts of the availability contract by being registered,
routed, given a destination and declared cursor-free. The fifth is this: something navigates to
it and finds a usable page. Seventeen of the twenty-three had a test naming their URL and six had
none, which is not a statement about those six -- it is a statement about how the coverage was
built, one report at a time, so a report added tomorrow starts uncovered.

So this asks the registry which reports exist rather than listing them. A report declared next
week is witnessed the day it is declared, and a report that stops rendering is caught whether or
not anyone remembered to write it a test.
"""
import re

import pytest
from fastapi.testclient import TestClient

from bookflow.core import registry

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401


def _browser(hosted) -> TestClient:  # noqa: F811
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def _main(text: str) -> str:
    return text.split("<main>", 1)[-1].split("</main>", 1)[0]


def _reports() -> list[str]:
    registry.load_all()
    return sorted(c.name.split(" ", 1)[1] for c in registry.all_commands()
                  if c.name.startswith("report ") and not c.is_write)


def _usable(page) -> list[str]:
    """The same checks the sweep makes, as a list of what is wrong. Empty means usable."""
    problems = []
    if page.status_code != 200:
        problems.append(f"status {page.status_code}")
    body = _main(page.text)
    if 'name="password"' in page.text:
        problems.append("asks for a login")
    if 'class="error"' in body:
        problems.append("renders an error")
    if re.search(r"\bE_[A-Z_]+\b", body):
        problems.append("shows an error code")
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", page.text, re.S)
    if not (heading and heading.group(1).strip()):
        problems.append("has no heading")
    if not re.search(r"<(form|table|input|button|a )", body):
        problems.append("offers nothing to do")
    return problems


def test_the_checks_reject_a_page_that_is_not_a_report(hosted):  # noqa: F811
    """The sweep is only worth its runtime if these checks can fail.

    A sweep that asks the registry what to test cannot be mutation-checked by removing a report
    -- that removes its case too. So the sensitivity is proved here instead, against a name the
    registry does not know: if this comes back usable, the checks above are not checking.
    """
    browser = _browser(hosted)
    page = browser.get(f"/c/{hosted.company_id}/report/not-a-real-report", follow_redirects=False)
    assert _usable(page), "a report that does not exist rendered as a usable page"


def test_the_registry_declares_the_reports_this_witness_covers():
    """If this ever finds nothing, the sweep below is passing by covering nothing."""
    found = _reports()
    assert len(found) >= 20, (len(found), "far fewer reports than this product has")
    assert "ap-aging" in found and "trial-balance" in found


@pytest.mark.parametrize("report", _reports())
def test_every_report_answers_a_page_a_person_can_read(report, hosted):  # noqa: F811
    """Landing matters as much as status: a redirect to login answers 200 on the second hop."""
    browser = _browser(hosted)
    url = f"/c/{hosted.company_id}/report/{report}"
    page = browser.get(url, follow_redirects=False)
    assert not _usable(page), (
        report, url, _usable(page), page.headers.get("location"),
        page.text[:300])
