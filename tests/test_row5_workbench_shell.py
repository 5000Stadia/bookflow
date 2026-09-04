"""The Row 5 workbench shell groups and queries list nouns responsively."""

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted


def _browser(hosted) -> TestClient:
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def test_company_home_uses_the_fixed_accounting_navigation_groups(hosted):
    page = _browser(hosted).get(f"/c/{hosted.company_id}/")
    assert page.status_code == 200
    headings = (
        "Company",
        "Customers and sales",
        "Vendors and purchases",
        "Employees",
        "Items",
        "Accounting",
        "Settings",
        "Audit",
    )
    positions = [page.text.find(f"<h2>{heading}</h2>") for heading in headings]
    present = [position for position in positions if position >= 0]
    assert present == sorted(present)
    assert f'href="/c/{hosted.company_id}/term"' in page.text
    assert f'href="/c/{hosted.company_id}/profile/apply"' in page.text
    assert f'href="/c/{hosted.company_id}/chart/apply"' in page.text


def test_list_page_uses_declared_columns_and_command_backed_query_controls(hosted):
    browser = _browser(hosted)
    page = browser.get(
        f"/c/{hosted.company_id}/term",
        params={"query": "Net 30", "sort": "name", "direction": "desc"},
    )
    assert page.status_code == 200
    assert "Net 30" in page.text and "Net 15" not in page.text
    for column in ("name", "kind", "due_rule_summary", "discount_rule_summary", "active"):
        assert f"<th>{column}</th>" in page.text
    assert 'name="query" value="Net 30"' in page.text
    assert '<option value="name" selected>name</option>' in page.text
    assert '<option value="desc" selected>Descending</option>' in page.text


def test_shell_declares_mobile_viewport_and_scopes_horizontal_scroll_to_tables(hosted):
    page = _browser(hosted).get(f"/c/{hosted.company_id}/term")
    css = _browser(hosted).get("/static/style.css")
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page.text
    assert 'class="table-wrap"' in page.text
    assert "@media (max-width:700px)" in css.text
    assert ".table-wrap{max-width:100%;overflow-x:auto}" in css.text
