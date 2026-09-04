"""High-value Row 5 generated-form contracts in the browser workbench."""

from __future__ import annotations

import html
import json

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
    assert f'href="/c/{hosted.company_id}/term/create"' in page.text
    assert 'target="_blank"' in page.text

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
