"""High-value Row 5 generated-form contracts in the browser workbench."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from bookflow.adapters.workbench import forms
from bookflow.core import registry
from tests.conftest import make_actor
from tests.test_row3_host import GHOST, PASSWORD, WB, hosted


def _browser(hosted, *, follow_redirects: bool = False) -> TestClient:
    browser = TestClient(hosted.handle.app, follow_redirects=follow_redirects)
    assert browser.post(
        "/login",
        json={"username": hosted.login, "password": PASSWORD},
    ).status_code == 200
    return browser


def _page_originals(page) -> dict:
    matched = re.search(r'name="originals" value=\'([^\']*)\'', page.text)
    assert matched is not None
    return json.loads(html.unescape(matched.group(1)))


def _encode_collection(path: str, rows: list[dict]) -> dict[str, str]:
    encoded = {f"collection:{path}": "1"}
    for index, row in enumerate(rows):
        for field, value in row.items():
            child_path = f"{path}:{index}:{field}"
            if isinstance(value, list):
                encoded.update(_encode_collection(child_path, value))
            elif value is not None:
                encoded[f"c:{child_path}"] = (
                    "true" if value is True else "false" if value is False else str(value)
                )
    return encoded


def test_typed_descriptors_expose_variant_fields_and_pin_immutable_kinds():
    term_create = registry.get("term create")
    assert term_create is not None
    fields = {
        leaf["path"]: leaf
        for leaf in forms.describe_fields("term", "create", term_create.input_model, {}, {})
    }
    assert fields["due_days"]["visible_when"] == {
        "field": "kind",
        "values": ("standard",),
    }
    assert fields["due_day_of_month"]["visible_when"]["values"] == ("date_driven",)

    term_update = registry.get("term update")
    assert term_update is not None
    update_fields = {
        leaf["path"]: leaf
        for leaf in forms.describe_fields(
            "term",
            "update",
            term_update.input_model,
            {"kind": "standard"},
            {},
        )
    }
    assert update_fields["kind"]["pinned"] is True
    assert update_fields["due_days"]["visible"] is True
    assert update_fields["due_day_of_month"]["visible"] is False

    item_create = registry.get("item create")
    assert item_create is not None
    item_fields = {
        leaf["path"]: leaf
        for leaf in forms.describe_fields(
            "item",
            "create",
            item_create.input_model,
            {"type": "subtotal"},
            {},
        )
    }
    assert item_fields["description"]["visible"] is True
    assert item_fields["income_account_id"]["visible"] is False
    assert item_fields["members"]["visible_when"]["values"] == (
        "inventory_assembly",
        "group",
        "sales_tax_group",
    )

    price_create = registry.get("price-level create")
    custom_create = registry.get("custom-field create")
    account_create = registry.get("account create")
    sales_rep_update = registry.get("sales-rep update")
    assert all((price_create, custom_create, account_create, sales_rep_update))
    price_fields = {
        leaf["path"]: leaf
        for leaf in forms.describe_fields(
            "price-level", "create", price_create.input_model, {"kind": "per_item"}, {}
        )
    }
    assert price_fields["items"]["visible"] is True
    assert price_fields["percent"]["visible"] is False
    custom_fields = {
        leaf["path"]: leaf
        for leaf in forms.describe_fields(
            "custom-field", "create", custom_create.input_model, {"kind": "choice"}, {}
        )
    }
    assert custom_fields["choices"]["visible"] is True
    account_fields = {
        leaf["path"]: leaf
        for leaf in forms.describe_fields(
            "account", "create", account_create.input_model, {"type": "bank"}, {}
        )
    }
    assert account_fields["routing_number_last4"]["visible"] is True
    assert account_fields["track_reimbursable_expenses"]["visible"] is False
    sales_fields = {
        leaf["path"]: leaf
        for leaf in forms.describe_fields(
            "sales-rep",
            "update",
            sales_rep_update.input_model,
            {"name_type": "employee"},
            {},
        )
    }
    assert sales_fields["name_type"]["pinned"] is True


def test_runtime_custom_controls_translate_one_stable_id_patch():
    command = registry.get("customer update")
    assert command is not None
    definition_id = "01ARZ3NDEKTSV4RRFFQ69G5FAA"
    other_id = "01ARZ3NDEKTSV4RRFFQ69G5FAB"
    raw, _, _ = forms.translate(
        command,
        {
            f"cf:{definition_id}": "true",
            f"cf-kind:{definition_id}": "bool",
            f"cf:{other_id}": "kept",
            f"cf-kind:{other_id}": "text",
            "action": "preview",
        },
        {"custom_fields": {definition_id: False, other_id: "kept"}},
    )
    assert raw == {"custom_fields": {definition_id: True}}

    cleared, _, _ = forms.translate(
        command,
        {
            f"cf:{definition_id}": "false",
            f"cf-kind:{definition_id}": "bool",
            f"clear:custom_fields.{definition_id}": "1",
        },
        {"custom_fields": {definition_id: True}},
    )
    assert cleared == {"custom_fields": {definition_id: None}}


def test_reference_picker_is_bounded_active_company_scoped_and_retains_inactive_current(hosted, root):
    current = hosted.ok(
        "term create",
        {"name": "Workbench inactive term", "kind": "standard", "due_days": 7},
        company=hosted.company_id,
    )
    customer = hosted.ok(
        "customer create",
        {"name": "Workbench reference customer", "terms_id": current["id"]},
        company=hosted.company_id,
    )
    hosted.ok(
        "term deactivate",
        {"term": current["id"], "expected_version": current["version"]},
        company=hosted.company_id,
    )

    hosted.ok("organization.new", {"name": "Workbench Foreign Org"})
    foreign_company = hosted.ok(
        "company.new",
        {
            "legal_name": "Workbench Foreign Company LLC",
            "home_currency": "USD",
            "organization": "Workbench Foreign Org",
            "timezone": "UTC",
        },
    )["company_id"]
    foreign = hosted.ok(
        "term create",
        {"name": "Foreign-only reference term", "kind": "standard", "due_days": 9},
        company=foreign_company,
    )

    browser = _browser(hosted)
    page = browser.get(f"/c/{hosted.company_id}/customer/{customer['id']}/update")
    assert page.status_code == 200
    assert f'name="f:terms_id" value="{current["id"]}"' in page.text
    assert "Workbench inactive term" in page.text
    assert "(inactive; readable only)" in page.text
    assert f'href="/c/{hosted.company_id}/term/create?' in page.text
    assert 'target="bookflow-add-' in page.text

    inactive = browser.get(
        f"/c/{hosted.company_id}/_references/customer/terms_id",
        params={"q": "Workbench inactive term"},
    )
    assert inactive.status_code == 200
    assert current["id"] not in inactive.text

    active = browser.get(
        f"/c/{hosted.company_id}/_references/customer/terms_id",
        params={"q": "Net"},
    )
    assert active.status_code == 200
    assert active.text.count("<option") <= 25
    assert "Net 30" in active.text
    assert "Cache-Control" in active.headers

    isolated = browser.get(
        f"/c/{hosted.company_id}/_references/customer/terms_id",
        params={"q": "Foreign-only reference term"},
    )
    assert isolated.status_code == 200
    assert foreign["id"] not in isolated.text
    assert "Foreign-only reference term" not in isolated.text

    employee = hosted.ok(
        "employee create",
        {
            "name": "Workbench Picker Employee",
            "first_name": "Workbench",
            "last_name": "Picker",
        },
        company=hosted.company_id,
    )
    polymorphic = browser.get(
        f"/c/{hosted.company_id}/_references/sales-rep/name_id",
        params=[
            ("f:name_type", "employee"),
            ("f:name_id", "Workbench Picker Employee"),
        ],
    )
    assert polymorphic.status_code == 200
    assert employee["id"] in polymorphic.text

    make_actor(root, "workbench-reference-reader", company_role=(hosted.company_id, "readonly"))
    token = hosted.ok(
        "token.issue",
        {"user": "workbench-reference-reader", "label": "workbench-reference-reader"},
    )
    reader = TestClient(hosted.handle.app)
    headers = {"Authorization": f"Bearer {token['secret']}"}
    hidden = reader.get(
        f"/c/{foreign_company}/_references/customer/terms_id?q=Foreign-only",
        headers=headers,
    )
    missing = reader.get(
        f"/c/{GHOST}/_references/customer/terms_id?q=Foreign-only",
        headers=headers,
    )
    assert hidden.status_code == missing.status_code == 404
    assert hidden.text == missing.text
    assert "Foreign-only reference term" not in hidden.text


def test_runtime_custom_fields_render_by_stable_id_and_preserve_attempt(hosted):
    definitions = {}
    for kind, extra in (
        ("text", {}),
        ("number", {}),
        ("date", {}),
        ("bool", {}),
        ("choice", {"choices": [{"value": "North"}, {"value": "South"}]}),
    ):
        definitions[kind] = hosted.ok(
            "custom-field create",
            {
                "name": f"Workbench {kind} field",
                "kind": kind,
                "scopes": ["customer"],
                **extra,
            },
            company=hosted.company_id,
        )
    customer = hosted.ok(
        "customer create",
        {
            "name": "Workbench custom customer",
            "custom_fields": {
                definitions["text"]["id"]: "stored text",
                definitions["number"]["id"]: "1.25",
                definitions["date"]["id"]: "2026-09-04",
                definitions["bool"]["id"]: True,
                definitions["choice"]["id"]: "North",
            },
        },
        company=hosted.company_id,
    )

    browser = _browser(hosted)
    route = f"/c/{hosted.company_id}/customer/{customer['id']}/update"
    page = browser.get(route)
    assert page.status_code == 200
    assert 'name="f:custom_fields"' not in page.text
    for kind, definition in definitions.items():
        assert f'name="cf:{definition["id"]}"' in page.text
        assert f"Workbench {kind} field" in page.text
    assert 'type="date"' in page.text
    assert 'inputmode="decimal"' in page.text
    assert '<option value="North" selected>North</option>' in page.text

    attempted = "value kept after error"
    failure = browser.post(
        f"/c/{hosted.company_id}/customer/create",
        headers=WB,
        data={
            "originals": "{}",
            f"cf:{definitions['text']['id']}": attempted,
            f"cf-kind:{definitions['text']['id']}": "text",
            "action": "preview",
        },
    )
    assert failure.status_code == 200
    assert "E_VALIDATION" in failure.text
    assert attempted in html.unescape(failure.text)


def test_audit_detail_offers_authorized_dry_run_checked_undo_and_company_route_submits(hosted, root):
    created = hosted.ok(
        "ship-method create",
        {"name": "Workbench Undo Courier", "display_order": 91},
        company=hosted.company_id,
    )
    event = hosted.ok(
        "audit list",
        {"record_type": "ship_method", "record_id": created["id"], "limit": 1},
        company=hosted.company_id,
    )["items"][0]

    owner = _browser(hosted)
    detail = owner.get(f"/c/{hosted.company_id}/audit/{event['id']}")
    assert detail.status_code == 200
    assert 'action="/c/' + hosted.company_id + '/undo"' in detail.text
    assert f'name="f:event_id" value="{event["id"]}"' in detail.text
    assert "Undo this event" in detail.text

    make_actor(root, "workbench-audit-reader", company_role=(hosted.company_id, "readonly"))
    token = hosted.ok(
        "token.issue",
        {"user": "workbench-audit-reader", "label": "workbench-audit-reader"},
    )
    readonly = TestClient(hosted.handle.app)
    readonly_detail = readonly.get(
        f"/c/{hosted.company_id}/audit/{event['id']}",
        headers={"Authorization": f"Bearer {token['secret']}"},
    )
    assert readonly_detail.status_code == 200
    assert "Undo this event" not in readonly_detail.text

    response = owner.post(
        f"/c/{hosted.company_id}/undo",
        headers=WB,
        data={
            "originals": json.dumps({}),
            "f:event_id": event["id"],
            "action": "submit",
        },
    )
    assert response.status_code == 303
    undo_event = hosted.ok(
        "audit list",
        {"command": "undo", "limit": 1},
        company=hosted.company_id,
    )["items"][0]

    compensated = owner.get(f"/c/{hosted.company_id}/audit/{event['id']}")
    assert f'/audit/{undo_event["id"]}' in compensated.text
    inverse = owner.get(f"/c/{hosted.company_id}/audit/{undo_event['id']}")
    assert f'/audit/{event["id"]}' in inverse.text
    assert "Undo this event" not in inverse.text


def test_add_new_reference_preserves_caller_and_returns_created_stable_id(hosted):
    browser = _browser(hosted)
    caller = browser.get(f"/c/{hosted.company_id}/customer/create")
    assert caller.status_code == 200
    matched = re.search(
        rf'href="([^"]*/c/{hosted.company_id}/term/create\?[^"]+)"[^>]*target="bookflow-add-([^"]+)"',
        caller.text,
    )
    assert matched is not None
    child_url = html.unescape(matched.group(1))
    query = parse_qs(urlsplit(child_url).query)
    token = query["return_token"][0]
    assert query["return_target"] == ["term"]
    assert matched.group(2) == token
    assert '/static/workflow.js' in caller.text
    script = browser.get('/static/workflow.js').text
    assert "bookflow-reference-created" in script
    assert 'data-ref-value' in caller.text and 'data-ref-search' in caller.text

    child = browser.get(child_url)
    assert child.status_code == 200
    assert f'name="_return_token" value="{token}"' in child.text
    assert 'name="_return_target" value="term"' in child.text
    # Callback forms use the authenticated workbench transport in the child;
    # the returned script executes there without replacing the caller's draft.
    form_tag = re.search(r'<form method="post"[^>]+data-generated-form>', child.text)
    assert form_tag is not None and "hx-post" in form_tag.group(0)

    created = browser.post(
        urlsplit(child_url).path,
        headers=WB,
        data={
            "originals": "{}",
            "_return_token": token,
            "_return_target": "term",
            "f:name": "Callback Net 12",
            "f:kind": "standard",
            "f:due_days": "12",
            "action": "submit",
        },
    )
    assert created.status_code == 200
    assert f'"token": "{token}"' in created.text
    assert '"version": 1' in created.text
    assert 'window.opener.postMessage(message,location.origin)' in created.text
    stable_id = re.search(r'"id": "([0-9A-Z]{26})"', created.text)
    assert stable_id is not None
    listed = hosted.ok(
        "term list", {"query": "Callback Net 12"}, company=hosted.company_id
    )["items"]
    assert [row["id"] for row in listed] == [stable_id.group(1)]


def test_nested_collection_references_are_scoped_search_controls_including_component_units(hosted):
    unit_set = hosted.ok(
        "unit-of-measure create",
        {
            "name": "Workbench Length",
            "units": [
                {"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"},
                {"name": "Pair", "abbreviation": "pr", "is_base": False, "base_factor": "2"},
            ],
        },
        company=hosted.company_id,
    )
    hosted.ok(
        "company update",
        {"units_of_measure_mode": "multiple_related_units"},
        company=hosted.company_id,
    )
    account = hosted.ok(
        "account create",
        {"name": "Workbench Nested Expense", "type": "expense"},
        company=hosted.company_id,
    )
    purchased = {
        "type": "service",
        "purchase_enabled": True,
        "purchase_description": "Nested reference service",
        "cost": "1",
        "expense_account_id": account["id"],
    }
    component = hosted.ok(
        "item create",
        {
            "name": "Workbench Unit Component",
            **purchased,
            "unit_of_measure_set_id": unit_set["id"],
        },
        company=hosted.company_id,
    )
    no_set = hosted.ok(
        "item create",
        {"name": "Workbench No Unit Component", **purchased},
        company=hosted.company_id,
    )
    vendor = hosted.ok(
        "vendor create", {"name": "Workbench Nested Vendor"}, company=hosted.company_id
    )
    browser = _browser(hosted)

    item_form = browser.get(f"/c/{hosted.company_id}/item/create")
    assert item_form.status_code == 200
    for path in (
        "members.component_item_id",
        "members.unit_id",
        "vendor_profiles.vendor_id",
    ):
        assert f"/_references/item/{path}?target=" in item_form.text
    assert 'data-ref-component="1"' in item_form.text
    assert 'name="c:members:__INDEX0__:component_item_id"' in item_form.text
    assert 'name="c:members:__INDEX0__:unit_id"' in item_form.text

    price_form = browser.get(f"/c/{hosted.company_id}/price-level/create")
    expense_form = browser.get(f"/c/{hosted.company_id}/vendor/{vendor['id']}/update")
    assert "/_references/price-level/items.item_id?target=item" in price_form.text
    assert "/_references/vendor/expense_accounts.account_id?target=account" in expense_form.text

    item_choices = browser.get(
        f"/c/{hosted.company_id}/_references/price-level/items.item_id",
        params={"target": "item", "c:items:0:item_id": "Workbench Unit Component"},
    )
    vendor_choices = browser.get(
        f"/c/{hosted.company_id}/_references/item/vendor_profiles.vendor_id",
        params={"target": "vendor", "c:vendor_profiles:0:vendor_id": "Workbench Nested Vendor"},
    )
    account_choices = browser.get(
        f"/c/{hosted.company_id}/_references/vendor/expense_accounts.account_id",
        params={"target": "account", "c:expense_accounts:0:account_id": "Workbench Nested Expense"},
    )
    assert component["id"] in item_choices.text
    assert vendor["id"] in vendor_choices.text
    assert account["id"] in account_choices.text
    assert all(response.text.count("<option") <= 25 for response in (
        item_choices, vendor_choices, account_choices,
    ))

    unit_choices = browser.get(
        f"/c/{hosted.company_id}/_references/item/members.unit_id",
        params=[
            ("target", "unit-of-measure"),
            ("c:members:0:component_item_id", component["id"]),
            ("c:members:0:unit_id", "Pair"),
        ],
    )
    pair = next(unit for unit in unit_set["units"] if unit["name"] == "Pair")
    each = next(unit for unit in unit_set["units"] if unit["name"] == "Each")
    assert unit_choices.status_code == 200
    assert pair["id"] in unit_choices.text
    assert each["id"] not in unit_choices.text
    assert "Workbench Length · Pair (pr)" in unit_choices.text

    no_set_choices = browser.get(
        f"/c/{hosted.company_id}/_references/item/members.unit_id",
        params=[
            ("target", "unit-of-measure"),
            ("c:members:0:component_item_id", no_set["id"]),
            ("c:members:0:unit_id", "Each"),
        ],
    )
    unresolved_choices = browser.get(
        f"/c/{hosted.company_id}/_references/item/members.unit_id",
        params=[
            ("target", "unit-of-measure"),
            ("c:members:0:component_item_id", "01ARZ3NDEKTSV4RRFFQ69G5FAZ"),
            ("c:members:0:unit_id", "Each"),
        ],
    )
    assert no_set_choices.status_code == unresolved_choices.status_code == 200
    assert no_set_choices.text == unresolved_choices.text == ""


def test_add_new_vendor_callback_populates_link_endpoint_version_protocol(hosted):
    customer = hosted.ok(
        "customer create", {"name": "Callback Link Customer"}, company=hosted.company_id
    )
    browser = _browser(hosted)
    caller = browser.get(
        f"/c/{hosted.company_id}/customer/{customer['id']}/link-vendor"
    )
    assert caller.status_code == 200
    assert 'data-ref-version-field="expected_vendor_version"' in caller.text
    assert "option.dataset.version = String(event.data.version)" in browser.get('/static/workflow.js').text
    matched = re.search(
        rf'href="([^"]*/c/{hosted.company_id}/vendor/create\?[^"]+)"', caller.text
    )
    assert matched is not None
    child_url = html.unescape(matched.group(1))
    query = parse_qs(urlsplit(child_url).query)
    token = query["return_token"][0]
    callback = browser.post(
        urlsplit(child_url).path,
        headers=WB,
        data={
            "originals": "{}",
            "_return_token": token,
            "_return_target": "vendor",
            "f:name": "Callback Created Vendor",
            "action": "submit",
        },
    )
    assert callback.status_code == 200
    assert '"target": "vendor"' in callback.text
    assert '"version": 1' in callback.text
    assert re.search(r'"id": "[0-9A-Z]{26}"', callback.text)


def test_nested_collections_render_typed_controls_and_round_trip_stable_order(hosted):
    customer = hosted.ok(
        "customer create",
        {
            "name": "Workbench nested contacts",
            "contacts": [
                {
                    "role": "primary",
                    "display_name": "First Contact",
                    "points": [{"kind": "work_phone", "value": "555-0101"}],
                },
                {
                    "role": "alternate",
                    "display_name": "Second Contact",
                    "points": [{"kind": "main_email", "value": "second@example.test"}],
                },
            ],
        },
        company=hosted.company_id,
    )
    shown = hosted.ok(
        "customer show", {"customer": customer["id"]}, company=hosted.company_id
    )
    first_id, second_id = [contact["id"] for contact in shown["contacts"]]
    first_point_id = shown["contacts"][0]["points"][0]["id"]

    browser = _browser(hosted)
    route = f"/c/{hosted.company_id}/customer/{customer['id']}/update"
    page = browser.get(route)
    assert page.status_code == 200
    assert '<textarea name="f:contacts"' not in page.text
    assert 'name="collection:contacts"' in page.text
    assert 'name="c:contacts:0:id"' in page.text
    assert 'name="collection:contacts:0:points"' in page.text
    assert 'name="c:contacts:0:points:0:id"' in page.text
    assert first_id in page.text and first_point_id in page.text
    assert "data-collection-add" in page.text
    assert "data-collection-remove" in page.text
    assert "data-collection-up" in page.text and "data-collection-down" in page.text

    originals = _page_originals(page)
    update = registry.get("customer update")
    assert update is not None
    unchanged_form = _encode_collection("contacts", originals["contacts"])
    unchanged, _, _ = forms.translate(update, unchanged_form, originals)
    assert unchanged == {}

    reordered = list(reversed(originals["contacts"]))
    reordered_form = _encode_collection("contacts", reordered)
    translated, _, _ = forms.translate(update, reordered_form, originals)
    assert [row["id"] for row in translated["contacts"]] == [second_id, first_id]
    assert translated["contacts"][1]["points"][0]["id"] == first_point_id
    removed, _, _ = forms.translate(
        update, _encode_collection("contacts", [originals["contacts"][1]]), originals
    )
    assert [row["id"] for row in removed["contacts"]] == [second_id]
    added_rows = [
        *originals["contacts"],
        {"role": "additional", "display_name": "New Contact", "points": []},
    ]
    added, _, _ = forms.translate(
        update, _encode_collection("contacts", added_rows), originals
    )
    assert added["contacts"][-1] == {
        "role": "additional",
        "display_name": "New Contact",
        "points": [],
    }

    submitted = browser.post(
        route,
        headers=WB,
        data={
            "originals": json.dumps(originals),
            "f:customer": customer["id"],
            "f:expected_version": str(shown["version"]),
            **reordered_form,
            "action": "submit",
        },
    )
    assert submitted.status_code == 303
    after = hosted.ok(
        "customer show", {"customer": customer["id"]}, company=hosted.company_id
    )
    assert [contact["id"] for contact in after["contacts"]] == [second_id, first_id]
    assert after["contacts"][1]["points"][0]["id"] == first_point_id


def test_money_outputs_prefill_as_exact_amounts_and_untouched_preview(hosted):
    created = hosted.ok(
        "customer create",
        {"name": "Workbench Exact Money", "credit_limit": "123.45"},
        company=hosted.company_id,
    )
    shown = hosted.ok(
        "customer show", {"customer": created["id"]}, company=hosted.company_id
    )
    assert shown["credit_limit"]["currency"] == "USD"
    browser = _browser(hosted)
    route = f"/c/{hosted.company_id}/customer/{created['id']}/update"
    page = browser.get(route)
    assert page.status_code == 200
    assert 'name="f:credit_limit" value="123.45"' in page.text
    assert "{'amount':" not in page.text
    originals = _page_originals(page)
    assert originals["credit_limit"] == "123.45"
    preview = browser.post(
        route,
        headers=WB,
        data={
            "originals": json.dumps(originals),
            "f:customer": created["id"],
            "f:expected_version": str(shown["version"]),
            "f:credit_limit": "123.45",
            "action": "preview",
        },
    )
    assert preview.status_code == 200
    assert "Preview (nothing written)" in preview.text
    assert "not an amount" not in preview.text
    unchanged = hosted.ok(
        "customer show", {"customer": created["id"]}, company=hosted.company_id
    )
    assert unchanged["credit_limit"] == shown["credit_limit"]
    assert unchanged["version"] == shown["version"]


def test_linked_contact_copy_previews_and_submits_ordinary_versioned_update(hosted):
    customer = hosted.ok(
        "customer create",
        {
            "name": "Contact Copy Customer",
            "contact": "Primary Contact",
            "phone": "555-0199",
            "email": "copy@example.test",
        },
        company=hosted.company_id,
    )
    vendor = hosted.ok(
        "vendor create",
        {"name": "Contact Copy Vendor", "phone": "555-0000"},
        company=hosted.company_id,
    )
    linked = hosted.ok(
        "customer link-vendor",
        {
            "customer": customer["id"],
            "vendor": vendor["id"],
            "expected_customer_version": customer["version"],
            "expected_vendor_version": vendor["version"],
        },
        company=hosted.company_id,
    )
    browser = _browser(hosted)
    detail = browser.get(f"/c/{hosted.company_id}/customer/{customer['id']}")
    assert detail.status_code == 200
    copy_url = f"/c/{hosted.company_id}/customer/{customer['id']}/copy-contact"
    assert f'href="{copy_url}"' in detail.text

    copy = browser.get(copy_url)
    assert copy.status_code == 200
    target_route = f"/c/{hosted.company_id}/vendor/{vendor['id']}/update"
    assert f'action="{target_route}"' in copy.text
    assert "ordinary versioned vendor update command" in copy.text
    assert 'name="f:phone" value="555-0199"' in copy.text
    assert 'name="f:email" value="copy@example.test"' in copy.text
    assert 'name="c:contacts:0:role"' in copy.text
    assert 'name="c:contacts:0:id" value=""' in copy.text
    assert f'name="f:expected_version" value="{linked["vendor_version"]}"' in copy.text
    originals = _page_originals(copy)
    source = hosted.ok(
        "customer show", {"customer": customer["id"]}, company=hosted.company_id
    )
    assert source["contacts"][0]["id"] not in copy.text
    vendor_update = registry.get("vendor update")
    assert vendor_update is not None
    copied_contacts = forms.collection_attempt(
        vendor_update.input_model.model_fields["contacts"].annotation,
        "contacts",
        source["contacts"],
        omit_stable_ids=True,
    )
    form = {
        "originals": json.dumps(originals),
        "f:vendor": vendor["id"],
        "f:expected_version": str(linked["vendor_version"]),
        "f:contact": "Primary Contact",
        "f:phone": "555-0199",
        "f:email": "copy@example.test",
        **copied_contacts,
    }
    preview = browser.post(target_route, headers=WB, data={**form, "action": "preview"})
    assert preview.status_code == 200
    assert "Preview (nothing written)" in preview.text
    unchanged = hosted.ok(
        "vendor show", {"vendor": vendor["id"]}, company=hosted.company_id
    )
    assert unchanged["phone"] == "555-0000"
    assert unchanged["version"] == linked["vendor_version"]

    submitted = browser.post(target_route, headers=WB, data={**form, "action": "submit"})
    assert submitted.status_code == 303
    copied = hosted.ok(
        "vendor show", {"vendor": vendor["id"]}, company=hosted.company_id
    )
    assert copied["contact"] == "Primary Contact"
    assert copied["phone"] == "555-0199"
    assert copied["email"] == "copy@example.test"
    assert copied["contacts"][0]["id"] != source["contacts"][0]["id"]


def test_vendor_contact_copy_to_inheriting_job_explicitly_owns_complete_contacts(hosted):
    parent = hosted.ok(
        "customer create",
        {"name": "Contact Copy Parent", "contact": "Inherited Parent"},
        company=hosted.company_id,
    )
    job = hosted.ok(
        "customer create",
        {
            "name": "Contact Copy Job",
            "parent_id": parent["id"],
            "address_mode": "inherit",
            "contact_mode": "inherit",
        },
        company=hosted.company_id,
    )
    vendor = hosted.ok(
        "vendor create",
        {
            "name": "Job Contact Source Vendor",
            "contact": "Vendor Contact",
            "phone": "555-0188",
            "email": "vendor-to-job@example.test",
        },
        company=hosted.company_id,
    )
    linked = hosted.ok(
        "customer link-vendor",
        {
            "customer": job["id"],
            "vendor": vendor["id"],
            "expected_customer_version": job["version"],
            "expected_vendor_version": vendor["version"],
        },
        company=hosted.company_id,
    )
    browser = _browser(hosted)
    copy_url = f"/c/{hosted.company_id}/vendor/{vendor['id']}/copy-contact"
    copy = browser.get(copy_url)
    assert copy.status_code == 200
    target_route = f"/c/{hosted.company_id}/customer/{job['id']}/update"
    assert f'action="{target_route}"' in copy.text
    assert '<option value="own" selected>own</option>' in copy.text
    assert 'name="c:contacts:0:role"' in copy.text

    source = hosted.ok(
        "vendor show", {"vendor": vendor["id"]}, company=hosted.company_id
    )
    customer_update = registry.get("customer update")
    assert customer_update is not None
    contacts = forms.collection_attempt(
        customer_update.input_model.model_fields["contacts"].annotation,
        "contacts",
        source["contacts"],
        omit_stable_ids=True,
    )
    post = {
        "originals": json.dumps(_page_originals(copy)),
        "f:customer": job["id"],
        "f:expected_version": str(linked["customer_version"]),
        "f:contact_mode": "own",
        "f:contact": "Vendor Contact",
        "f:phone": "555-0188",
        "f:email": "vendor-to-job@example.test",
        **contacts,
    }
    preview = browser.post(target_route, headers=WB, data={**post, "action": "preview"})
    assert preview.status_code == 200
    assert "Preview (nothing written)" in preview.text
    assert "E_VALIDATION" not in preview.text
    submitted = browser.post(target_route, headers=WB, data={**post, "action": "submit"})
    assert submitted.status_code == 303
    copied = hosted.ok(
        "customer show", {"customer": job["id"]}, company=hosted.company_id
    )
    assert copied["contact_mode"] == "own"
    assert copied["contact"] == "Vendor Contact"
    assert copied["phone"] == "555-0188"
    assert copied["contacts"][0]["id"] != source["contacts"][0]["id"]


def test_relationship_and_conversion_forms_prefill_versions_and_reject_stale_writes(hosted):
    customer = hosted.ok(
        "customer create", {"name": "Version Form Customer"}, company=hosted.company_id
    )
    vendor = hosted.ok(
        "vendor create", {"name": "Version Form Vendor"}, company=hosted.company_id
    )
    browser = _browser(hosted)
    link_route = f"/c/{hosted.company_id}/customer/{customer['id']}/link-vendor"
    link_form = browser.get(link_route, params={"vendor": vendor["id"]})
    assert link_form.status_code == 200
    assert f'name="f:customer" value="{customer["id"]}"' in link_form.text
    assert f'name="f:vendor" value="{vendor["id"]}"' in link_form.text
    assert f'name="f:expected_customer_version" value="{customer["version"]}"' in link_form.text
    assert f'name="f:expected_vendor_version" value="{vendor["version"]}"' in link_form.text

    unknown_vendor = "01ARZ3NDEKTSV4RRFFQ69G5FAA"
    invalid_link = browser.post(
        link_route,
        headers=WB,
        data={
            "originals": json.dumps(_page_originals(link_form)),
            "f:customer": customer["id"],
            "f:vendor": unknown_vendor,
            "f:expected_customer_version": str(customer["version"]),
            "f:expected_vendor_version": "1",
            "action": "preview",
        },
    )
    assert invalid_link.status_code == 200
    assert "E_RECORD_NOT_FOUND" in invalid_link.text
    assert f'name="f:vendor" value="{unknown_vendor}"' in invalid_link.text

    hosted.ok(
        "customer update",
        {
            "customer": customer["id"],
            "expected_version": customer["version"],
            "notes": "concurrent edit",
        },
        company=hosted.company_id,
    )
    stale_link = browser.post(
        link_route,
        headers=WB,
        data={
            "originals": json.dumps(_page_originals(link_form)),
            "f:customer": customer["id"],
            "f:vendor": vendor["id"],
            "f:expected_customer_version": str(customer["version"]),
            "f:expected_vendor_version": str(vendor["version"]),
            "action": "submit",
        },
    )
    assert stale_link.status_code == 200 and "E_VERSION_CONFLICT" in stale_link.text
    still_unlinked = hosted.ok(
        "customer show", {"customer": customer["id"]}, company=hosted.company_id
    )
    assert still_unlinked["linked_vendor_id"] is None

    linked = hosted.ok(
        "customer link-vendor",
        {
            "customer": customer["id"],
            "vendor": vendor["id"],
            "expected_customer_version": still_unlinked["version"],
            "expected_vendor_version": vendor["version"],
        },
        company=hosted.company_id,
    )
    unlink_route = f"/c/{hosted.company_id}/customer/{customer['id']}/unlink-vendor"
    unlink_form = browser.get(unlink_route)
    assert unlink_form.status_code == 200
    assert f'name="f:expected_customer_version" value="{linked["customer_version"]}"' in unlink_form.text
    assert f'name="f:expected_vendor_version" value="{linked["vendor_version"]}"' in unlink_form.text
    assert f'name="f:expected_link_version" value="{linked["link_version"]}"' in unlink_form.text
    hosted.ok(
        "vendor update",
        {
            "vendor": vendor["id"],
            "expected_version": linked["vendor_version"],
            "notes": "concurrent vendor edit",
        },
        company=hosted.company_id,
    )
    stale_unlink = browser.post(
        unlink_route,
        headers=WB,
        data={
            "originals": json.dumps(_page_originals(unlink_form)),
            "f:customer": customer["id"],
            "f:expected_customer_version": str(linked["customer_version"]),
            "f:expected_vendor_version": str(linked["vendor_version"]),
            "f:expected_link_version": str(linked["link_version"]),
            "action": "submit",
        },
    )
    assert stale_unlink.status_code == 200 and "E_VERSION_CONFLICT" in stale_unlink.text
    assert hosted.ok(
        "customer show", {"customer": customer["id"]}, company=hosted.company_id
    )["linked_vendor_id"] == vendor["id"]

    current_customer = hosted.ok(
        "customer show", {"customer": customer["id"]}, company=hosted.company_id
    )
    current_vendor = hosted.ok(
        "vendor show", {"vendor": vendor["id"]}, company=hosted.company_id
    )
    unlinked = hosted.ok(
        "customer unlink-vendor",
        {
            "customer": customer["id"],
            "expected_customer_version": current_customer["version"],
            "expected_vendor_version": current_vendor["version"],
            "expected_link_version": linked["link_version"],
        },
        company=hosted.company_id,
    )
    reactivate_form = browser.get(link_route, params={"vendor": vendor["id"]})
    assert reactivate_form.status_code == 200
    assert f'name="f:expected_link_version" value="{unlinked["link_version"]}"' in reactivate_form.text
    suggestions = browser.get(
        f"/c/{hosted.company_id}/_references/customer/vendor",
        params={"q": "Version Form Vendor", "f:customer": customer["id"]},
    )
    assert suggestions.status_code == 200
    assert vendor["id"] in suggestions.text
    assert f'data-link-version="{unlinked["link_version"]}"' in suggestions.text
    reactivation_preview = browser.post(
        link_route,
        headers=WB,
        data={
            "originals": json.dumps(_page_originals(reactivate_form)),
            "f:customer": customer["id"],
            "f:vendor": vendor["id"],
            "f:expected_customer_version": str(unlinked["customer_version"]),
            "f:expected_vendor_version": str(unlinked["vendor_version"]),
            "f:expected_link_version": str(unlinked["link_version"]),
            "action": "preview",
        },
    )
    assert reactivation_preview.status_code == 200
    assert "Preview (nothing written)" in reactivation_preview.text

    other = hosted.ok(
        "other-name create", {"name": "Version Form Other"}, company=hosted.company_id
    )
    convert_route = f"/c/{hosted.company_id}/other-name/{other['id']}/convert"
    convert_form = browser.get(convert_route)
    assert convert_form.status_code == 200
    assert f'name="f:other_name" value="{other["id"]}"' in convert_form.text
    assert f'name="f:expected_version" value="{other["version"]}"' in convert_form.text
    hosted.ok(
        "other-name update",
        {
            "other_name": other["id"],
            "expected_version": other["version"],
            "notes": "concurrent other edit",
        },
        company=hosted.company_id,
    )
    stale_convert = browser.post(
        convert_route,
        headers=WB,
        data={
            "originals": json.dumps(_page_originals(convert_form)),
            "f:other_name": other["id"],
            "f:to": "customer",
            "f:expected_version": str(other["version"]),
            "action": "submit",
        },
    )
    assert stale_convert.status_code == 200 and "E_VERSION_CONFLICT" in stale_convert.text
    source = hosted.ok(
        "other-name show", {"other_name": other["id"]}, company=hosted.company_id
    )
    assert source["converted_to_type"] is None
