"""R82: a company can show negative amounts in parentheses; data and exports keep the minus."""
from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def test_parentheses_setting_changes_what_pages_show_and_nothing_else(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    company = f"/c/{hosted.company_id}"
    shown = hosted.ok("company.show", {}, company=hosted.company_id)
    assert shown["info"]["negative_number_style"] == "minus"
    # Job B holds ten dollars of its own credit, a negative balance on the customer list.
    report = f"{company}/customer?query=Job+B"
    before = browser.get(report).text
    assert "-10.00" in before and "(10.00)" not in before
    form = browser.get(f"{company}/company/self/update").text
    assert 'name="f:negative_number_style"' in form and "parentheses" in form and "Show negative amounts as" in form
    hosted.ok("company.update", {"negative_number_style": "parentheses"}, company=hosted.company_id)
    after = browser.get(report).text
    assert "(10.00)" in after and "-10.00" not in after.split("<main", 1)[1].split("</main>")[0]
    # The data keeps its minus sign: only what a person reads changes.
    shown = hosted.ok("company.show", {}, company=hosted.company_id)
    assert shown["info"]["negative_number_style"] == "parentheses"
    hosted.ok("company.update", {"negative_number_style": "minus"}, company=hosted.company_id)
    assert "-10.00" in browser.get(report).text
