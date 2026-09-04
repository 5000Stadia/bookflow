"""The Row 5 workbench shell groups and queries list nouns responsively."""

import html

from fastapi.testclient import TestClient

import pytest

from bookflow.adapters.workbench import forms
from bookflow.core import registry
from bookflow.core.errors import BookflowError

from tests.test_row3_host import PASSWORD, WB, hosted


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


def test_aggregate_form_fields_use_json_and_decode_the_command_shape():
    command = registry.get("unit-of-measure create")
    assert command is not None
    descriptor = next(field for field in forms.leaves(command.input_model) if field["path"] == "units")
    assert descriptor["path_parts"] == ("units",)
    assert descriptor["kind"] == "json" and descriptor["json_shape"] == "array"

    value = '[{"name":"Each","abbreviation":"ea","is_base":true,"base_factor":"1"}]'
    raw, _, _ = forms.translate(
        command,
        {"f:name": "Count", "f:units": value, "action": "preview"},
        None,
    )
    assert raw["units"] == [
        {"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"}
    ]

    with pytest.raises(BookflowError) as caught:
        forms.translate(command, {"f:units": "not-json"}, None)
    assert caught.value.code == "E_VALIDATION"
    assert caught.value.details["fields"][0]["field"] == "units"


def test_aggregate_form_preserves_attempted_json_after_validation(hosted):
    browser = _browser(hosted)
    route = f"/c/{hosted.company_id}/unit-of-measure/create"
    page = browser.get(route)
    assert page.status_code == 200
    assert '<textarea name="f:units"' in page.text

    attempted = '[{"name":"Each"'
    failed = browser.post(
        route,
        headers=WB,
        data={"originals": "{}", "f:name": "Count", "f:units": attempted, "action": "preview"},
    )
    assert failed.status_code == 200
    assert "E_VALIDATION" in failed.text
    assert attempted in html.unescape(failed.text)


def test_list_update_form_prefills_the_declared_editable_output(hosted):
    browser = _browser(hosted)
    term = hosted.ok(
        "term list",
        {"query": "Net 30"},
        company=hosted.company_id,
    )["items"][0]
    page = browser.get(f"/c/{hosted.company_id}/term/{term['id']}/update")

    assert page.status_code == 200
    assert '<option value="standard" selected>standard</option>' in page.text
    assert 'name="f:due_days" value="30"' in page.text
    assert f'name="f:expected_version" value="{term["version"]}"' in page.text
