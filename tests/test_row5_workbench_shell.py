"""The Row 5 workbench shell groups and queries list nouns responsively."""

import html
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from bookflow.adapters.workbench import forms
from bookflow.core import registry

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
    assert f'href="/c/{hosted.company_id}/undo"' in page.text

    undo = _browser(hosted).get(f"/c/{hosted.company_id}/undo")
    assert undo.status_code == 200
    assert 'name="f:event_id"' in undo.text


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


def test_list_columns_are_url_only_selectable_and_ordered(hosted):
    browser = _browser(hosted)
    page = browser.get(
        f"/c/{hosted.company_id}/term",
        params={"query": "Net 30", "columns": "active,name,kind"},
    )
    assert page.status_code == 200
    assert page.text.index("<th>active</th>") < page.text.index("<th>name</th>")
    assert page.text.index("<th>name</th>") < page.text.index("<th>kind</th>")
    assert 'name="columns" value="active,name,kind"' in page.text

    invalid = browser.get(
        f"/c/{hosted.company_id}/term",
        params={"columns": "name,not-a-column"},
    )
    assert invalid.status_code == 422
    assert "E_LIST_FILTER" in invalid.text


def test_list_page_preserves_repeated_filters_and_state_when_toggling_inactive(hosted):
    browser = _browser(hosted)
    page = browser.get(
        f"/c/{hosted.company_id}/term",
        params=[
            ("query", "Net"),
            ("filter", "active=true"),
            ("filter", "kind=standard"),
            ("sort", "name"),
            ("direction", "desc"),
            ("columns", "name,active"),
        ],
    )
    assert page.status_code == 200
    assert page.text.count('name="filter"') == 3
    assert 'value="active=true"' in page.text
    assert 'value="kind=standard"' in page.text

    marker = 'href="/c/' + hosted.company_id + '/term?'
    start = page.text.index(marker) + len('href="')
    end = page.text.index('"', start)
    query = parse_qs(urlsplit(html.unescape(page.text[start:end])).query)
    assert query == {
        "query": ["Net"],
        "filter": ["active=true", "kind=standard"],
        "sort": ["name"],
        "direction": ["desc"],
        "columns": ["name,active"],
        "include_inactive": ["1"],
    }


def test_native_list_search_ignores_the_spare_blank_filter_control(hosted):
    created = hosted.ok(
        "customer create",
        {"name": "Workbench Blank Filter Search"},
        company=hosted.company_id,
    )
    page = _browser(hosted).get(
        f"/c/{hosted.company_id}/customer",
        params=[("query", "Workbench Blank Filter Search"), ("filter", "")],
    )
    assert page.status_code == 200
    assert "E_LIST_FILTER" not in page.text
    assert created["id"] in page.text


def test_record_heading_uses_the_declared_authoritative_display_field(hosted):
    created = hosted.ok(
        "customer list",
        {"query": "Riverside Apartments"},
        company=hosted.company_id,
    )["items"][0]
    page = _browser(hosted).get(
        f"/c/{hosted.company_id}/customer/{created['id']}"
    )
    assert page.status_code == 200
    assert "<h1>Riverside Apartments</h1>" in page.text
    assert f"<h1>{created['id']}</h1>" not in page.text


def test_shell_declares_mobile_viewport_and_scopes_horizontal_scroll_to_tables(hosted):
    page = _browser(hosted).get(f"/c/{hosted.company_id}/term")
    audit = _browser(hosted).get(f"/c/{hosted.company_id}/audit")
    css = _browser(hosted).get("/static/style.css")
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page.text
    assert 'class="table-wrap"' in page.text
    assert '<div class="table-wrap"><table>' in audit.text
    assert "@media (max-width:700px)" in css.text
    assert ".table-wrap{max-width:100%;overflow-x:auto}" in css.text


def test_aggregate_form_fields_use_repeated_typed_controls_and_decode_the_command_shape():
    command = registry.get("unit-of-measure create")
    assert command is not None
    descriptor = next(field for field in forms.leaves(command.input_model) if field["path"] == "units")
    assert descriptor["path_parts"] == ("units",)
    assert descriptor["kind"] == "collection" and descriptor["json_shape"] is None

    raw, _, _ = forms.translate(
        command,
        {
            "f:name": "Count",
            "collection:units": "1",
            "c:units:row-a:name": "Each",
            "c:units:row-a:abbreviation": "ea",
            "c:units:row-a:is_base": "true",
            "c:units:row-a:base_factor": "1",
            "action": "preview",
        },
        None,
    )
    assert raw["units"] == [
        {"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"}
    ]


def test_aggregate_form_preserves_attempted_repeated_values_after_validation(hosted):
    browser = _browser(hosted)
    route = f"/c/{hosted.company_id}/unit-of-measure/create"
    page = browser.get(route)
    assert page.status_code == 200
    assert 'name="collection:units"' in page.text
    assert 'data-collection-add' in page.text
    assert '<textarea name="f:units"' not in page.text

    attempted = "Each kept after error"
    failed = browser.post(
        route,
        headers=WB,
        data={
            "originals": "{}",
            "f:name": "Count",
            "collection:units": "1",
            "c:units:row-a:name": attempted,
            "c:units:row-a:abbreviation": "ea",
            "c:units:row-a:is_base": "true",
            "c:units:row-a:base_factor": "not-exact",
            "action": "preview",
        },
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
