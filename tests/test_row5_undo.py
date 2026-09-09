"""Compensating Row 5 list-event undo is atomic, field-aware, and audited."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from bookflow.commands.undo_cmds import UNDO_COMMAND  # noqa: F401
from bookflow.commands import item_cmds as _item_commands  # noqa: F401
from bookflow.commands import party_cmds as _party_commands  # noqa: F401
from bookflow.commands import unit_pricing_cmds as _unit_pricing_commands  # noqa: F401
from bookflow.company import schema
from bookflow.core import audit
from bookflow.core.errors import BookflowError
from bookflow.storage.engine import open_database
from tests.conftest import as_user, make_actor


def _event(client, company: str, command: str, record_id: str | None = None) -> str:
    payload = {"command": command}
    if record_id is not None:
        payload["record_id"] = record_id
    events = client.audit.list(company=company, **payload)["items"]
    assert events
    return events[0]["id"]


def _error(call, *args, **kwargs) -> BookflowError:
    with pytest.raises(BookflowError) as caught:
        call(*args, **kwargs)
    return caught.value


def _company_db(client, company: str) -> Path:
    return Path(client.company.show(company=company)["path"]) / "company.db"


def test_undo_create_is_preview_safe_audited_and_idempotent(client):
    company = "Demo Plumbing Co"
    created = client.account.create(name="Undo create", type="expense", company=company)
    original = _event(client, company, "account create", created["id"])

    preview = client.run("undo", {"event_id": original}, company=company, dry_run=True)
    assert preview["dry_run"] is True
    assert preview["original_event_id"] == original
    assert preview["affected_records"][0]["action"] == "deactivate"
    assert client.account.show(account=created["id"], company=company)["active"] is True

    result = client.run(
        "undo",
        {"event_id": original},
        company=company,
        idempotency_key="undo-created-account",
        reason="created in error",
    )
    replay = client.run(
        "undo",
        {"event_id": original},
        company=company,
        idempotency_key="undo-created-account",
        reason="created in error",
    )
    assert replay == {**result, "idempotent_replay": True}
    current = client.account.show(account=created["id"], company=company)
    assert current["active"] is False and current["version"] == 2

    with open_database(_company_db(client, company), writable=False) as db:
        event = db.conn.execute(
            sa.select(schema.audit_events).where(
                schema.audit_events.c.id == result["undo_event_id"]
            )
        ).mappings().one()
        assert event["command"] == "undo"
        assert event["undo_of_event_id"] == original
        assert event["reason"] == "created in error"
        assert original in event["summary"]
        entries = db.conn.execute(
            sa.select(schema.audit_entries).where(
                schema.audit_entries.c.event_id == result["undo_event_id"]
            )
        ).mappings().all()
        assert len(entries) == 1 and entries[0]["action"] == "deactivate"
        assert audit.decode_snapshot(entries[0]["after"])["active"] is False

    duplicate = _error(
        client.run,
        "undo",
        {"event_id": original},
        company=company,
        idempotency_key="a-new-attempt",
    )
    assert duplicate.code == "E_ALREADY_UNDONE"
    assert duplicate.details["undo_event_id"] == result["undo_event_id"]
    inverse_of_inverse = _error(
        client.run,
        "undo",
        {"event_id": result["undo_event_id"]},
        company=company,
    )
    assert inverse_of_inverse.code == "E_NOT_UNDOABLE"


def test_undo_update_preserves_disjoint_edits_detects_overlap_and_uses_current_value(client):
    company = "Demo Plumbing Co"
    first = client.account.create(name="Undo update", type="expense", company=company)
    client.account.update(
        account=first["id"], description="original change", company=company
    )
    original = _event(client, company, "account update", first["id"])
    client.account.update(account=first["id"], note="later note", company=company)

    result = client.run("undo", {"event_id": original}, company=company)
    shown = client.account.show(account=first["id"], company=company)
    assert shown["description"] is None
    assert shown["note"] == "later note"
    assert shown["version"] == 4
    assert result["affected_records"][0]["restored_fields"] == ["description"]

    returned = client.account.create(name="Changed back", type="expense", company=company)
    client.account.update(
        account=returned["id"], description="after", company=company
    )
    returned_event = _event(client, company, "account update", returned["id"])
    client.account.update(account=returned["id"], description="away", company=company)
    client.account.update(account=returned["id"], description="after", company=company)
    client.run("undo", {"event_id": returned_event}, company=company)
    assert client.account.show(account=returned["id"], company=company)["description"] is None

    collided = client.account.create(name="Overlap", type="expense", company=company)
    client.account.update(
        account=collided["id"], description="first", company=company
    )
    collided_event = _event(client, company, "account update", collided["id"])
    client.account.update(
        account=collided["id"], description="second", company=company
    )
    overlapping_event = _event(client, company, "account update", collided["id"])
    client.account.update(account=collided["id"], note="after overlap", company=company)
    error = _error(
        client.run, "undo", {"event_id": collided_event}, company=company
    )
    assert error.code == "E_UNDO_CONFLICT"
    assert error.details["record_id"] == collided["id"]
    assert error.details["field"] == "description"
    assert error.details["conflicting_event"]["command"] == "account update"
    assert error.details["conflicting_event"]["id"] == overlapping_event
    assert client.account.show(account=collided["id"], company=company)["description"] == "second"


def test_undo_cascade_orders_parent_before_child_and_dependency_blocks_create(client):
    company = "Demo Plumbing Co"
    parent = client.account.create(name="Undo tree", type="expense", company=company)
    parent_create = _event(client, company, "account create", parent["id"])
    child = client.account.create(
        name="Undo child", type="expense", parent_id=parent["id"], company=company
    )

    blocked = _error(
        client.run, "undo", {"event_id": parent_create}, company=company
    )
    assert blocked.code == "E_UNDO_CONFLICT"
    assert blocked.details["field"] == "active"

    client.account.deactivate(
        account=parent["id"], expected_version=1, cascade=True, company=company
    )
    cascade = _event(client, company, "account deactivate", parent["id"])
    undone = client.run("undo", {"event_id": cascade}, company=company)
    assert [item["record_id"] for item in undone["affected_records"]] == [
        parent["id"],
        child["id"],
    ]
    assert all(item["action"] == "activate" for item in undone["affected_records"])
    assert client.account.show(account=parent["id"], company=company)["active"] is True
    assert client.account.show(account=child["id"], company=company)["active"] is True


def test_undo_hierarchy_reprojects_disjoint_descendant_edits_and_rechecks_collisions(client):
    company = "Demo Plumbing Co"
    parent = client.account.create(name="Undo old root", type="expense", company=company)
    child = client.account.create(
        name="Original leaf", type="expense", parent_id=parent["id"], company=company
    )
    client.account.update(account=parent["id"], name="Undo new root", company=company)
    original = _event(client, company, "account update", parent["id"])
    client.account.update(account=child["id"], name="Later leaf", company=company)
    client.run("undo", {"event_id": original}, company=company)
    assert client.account.show(account=child["id"], company=company)["full_name"] == (
        "Undo old root:Later leaf"
    )

    target = client.account.create(name="Undo freed name", type="expense", company=company)
    client.account.update(account=target["id"], name="Undo renamed", company=company)
    rename_event = _event(client, company, "account update", target["id"])
    client.account.create(name="Undo freed name", type="expense", company=company)
    conflict = _error(
        client.run, "undo", {"event_id": rename_event}, company=company
    )
    assert conflict.code == "E_UNDO_CONFLICT"
    assert conflict.details["cause"] == "E_NAME_TAKEN"
    assert client.account.show(account=target["id"], company=company)["name"] == "Undo renamed"


def test_undo_custom_field_aggregate_restores_children_and_name(client):
    company = "Demo Plumbing Co"
    north = "01ARZ3NDEKTSV4RRFFQ69G5FBA"
    east = "01ARZ3NDEKTSV4RRFFQ69G5FBB"
    created = client.run(
        "custom-field create",
        {
            "name": "Undo zone",
            "kind": "choice",
            "scopes": ["customer"],
            "choices": [{"id": north, "value": "North"}],
            "default": "North",
        },
        company=company,
    )
    client.run(
        "custom-field update",
        {
            "custom_field": created["id"],
            "expected_version": 1,
            "name": "Changed zone",
            "default": "Northern",
            "choices": [
                {"id": north, "value": "Northern"},
                {"id": east, "value": "East"},
            ],
        },
        company=company,
    )
    original = _event(client, company, "custom-field update", created["id"])
    client.run("undo", {"event_id": original}, company=company)
    shown = client.run(
        "custom-field show", {"custom_field": created["id"]}, company=company
    )
    assert shown["name"] == "Undo zone"
    assert shown["default"] == "North"
    assert [(item["id"], item["value"]) for item in shown["choices"]] == [
        (north, "North")
    ]


def test_undo_custom_definition_create_observes_active_value_owner(client):
    company = "Demo Plumbing Co"
    definition = client.run(
        "custom-field create",
        {"name": "Undo dependency", "kind": "text", "scopes": ["customer"]},
        company=company,
    )
    original = _event(client, company, "custom-field create", definition["id"])
    customer = client.customer.create(
        name="Undo custom owner",
        custom_fields={definition["id"]: "in use"},
        company=company,
    )
    blocked = _error(client.run, "undo", {"event_id": original}, company=company)
    assert blocked.code == "E_UNDO_CONFLICT"
    assert blocked.details["dependents"] == [
        {"record_type": "custom_field_value", "count": 1}
    ]

    client.customer.deactivate(customer=customer["id"], company=company)
    client.run("undo", {"event_id": original}, company=company)
    shown = client.run(
        "custom-field show", {"custom_field": definition["id"]}, company=company
    )
    assert shown["active"] is False


def test_undo_unit_aggregate_retires_only_the_child_added_by_original_event(client):
    company = "Demo Plumbing Co"
    created = client.run(
        "unit-of-measure create",
        {
            "name": "Undo quantities",
            "units": [
                {
                    "name": "Each",
                    "abbreviation": "ea",
                    "is_base": True,
                    "base_factor": "1",
                }
            ],
        },
        company=company,
    )
    base = created["base_unit"]
    updated = client.run(
        "unit-of-measure update",
        {
            "unit_of_measure": created["id"],
            "expected_version": 1,
            "units": [
                {
                    "id": base["id"],
                    "name": "Each",
                    "abbreviation": "ea",
                    "is_base": True,
                    "base_factor": "1",
                },
                {
                    "name": "Case",
                    "abbreviation": "cs",
                    "is_base": False,
                    "base_factor": "12",
                },
            ],
        },
        company=company,
    )
    added = next(item for item in updated["units"] if item["abbreviation"] == "cs")
    original = _event(client, company, "unit-of-measure update", created["id"])
    client.run("undo", {"event_id": original}, company=company)
    shown = client.run(
        "unit-of-measure show",
        {"unit_of_measure": created["id"]},
        company=company,
    )
    assert [item["id"] for item in shown["units"] if item["active"]] == [base["id"]]
    assert next(item for item in shown["units"] if item["id"] == added["id"])["active"] is False

    with_dozen = client.run(
        "unit-of-measure update",
        {
            "unit_of_measure": created["id"],
            "units": [
                {
                    "id": base["id"],
                    "name": "Each",
                    "abbreviation": "ea",
                    "is_base": True,
                    "base_factor": "1",
                },
                {
                    "name": "Dozen",
                    "abbreviation": "dz",
                    "is_base": False,
                    "base_factor": "12",
                },
            ],
        },
        company=company,
    )
    dozen_id = next(
        row["id"] for row in with_dozen["units"] if row["abbreviation"] == "dz"
    )
    client.run(
        "unit-of-measure update",
        {
            "unit_of_measure": created["id"],
            "default_sales_unit_id": dozen_id,
            "units": [
                {
                    "id": base["id"],
                    "name": "Each",
                    "abbreviation": "ea",
                    "is_base": True,
                    "base_factor": "1",
                },
                {
                    "id": dozen_id,
                    "name": "Dozen",
                    "abbreviation": "dz",
                    "is_base": False,
                    "base_factor": "12",
                },
            ],
        },
        company=company,
    )
    client.run(
        "unit-of-measure update",
        {
            "unit_of_measure": created["id"],
            "default_sales_unit_id": base["id"],
            "units": [
                {
                    "id": base["id"],
                    "name": "Each",
                    "abbreviation": "ea",
                    "is_base": True,
                    "base_factor": "1",
                },
                {
                    "name": "Case",
                    "abbreviation": "cs",
                    "is_base": False,
                    "base_factor": "24",
                },
            ],
        },
        company=company,
    )
    replacement_event = _event(
        client, company, "unit-of-measure update", created["id"]
    )
    client.run("undo", {"event_id": replacement_event}, company=company)
    restored = client.run(
        "unit-of-measure show",
        {"unit_of_measure": created["id"]},
        company=company,
    )
    assert restored["default_sales_unit_id"] == dozen_id
    assert [row["id"] for row in restored["units"] if row["active"]] == [
        base["id"],
        dozen_id,
    ]


def test_undo_item_snapshot_restores_money_custom_value_and_member_identity(client):
    company = "Demo Plumbing Co"
    income = next(
        row["id"]
        for row in client.account.list(company=company)["items"]
        if row["full_name"] == "Service Income"
    )
    tax_code = client.run("sales-tax-code list", {}, company=company)["items"][0][
        "id"
    ]
    definition = client.run(
        "custom-field create",
        {"name": "Undo item bin", "kind": "text", "scopes": ["item"]},
        company=company,
    )

    def service(name: str):
        return client.run(
            "item create",
            {
                "name": name,
                "type": "service",
                "sales_enabled": True,
                "description": name,
                "price": "10.00",
                "income_account_id": income,
                "sales_tax_code_id": tax_code,
            },
            company=company,
        )

    first = service("Undo first component")
    first_create_event = _event(client, company, "item create", first["id"])
    second = service("Undo second component")
    changed = client.run(
        "item update",
        {
            "item": first["id"],
            "price": "12.25",
            "custom_fields": {definition["id"]: "B-2"},
        },
        company=company,
    )
    original = _event(client, company, "item update", first["id"])
    client.run(
        "item update",
        {"item": first["id"], "notes": "keep this later note"},
        company=company,
    )
    client.run("undo", {"event_id": original}, company=company)
    restored = client.run("item show", {"item": first["id"]}, company=company)
    assert restored["price"] == {"amount": "10.00", "currency": "USD", "minor_units": 1000}
    assert restored["custom_fields"] == []
    assert restored["notes"] == "keep this later note"
    assert changed["custom_fields"][0]["value"] == "B-2"

    group = client.run(
        "item create",
        {
            "name": "Undo member group",
            "type": "group",
            "print_members": True,
            "members": [{"component_item_id": first["id"], "quantity": "1"}],
        },
        company=company,
    )
    first_member_id = group["members"][0]["id"]
    client.run(
        "item update",
        {
            "item": group["id"],
            "members": [{"component_item_id": second["id"], "quantity": "2.5"}],
        },
        company=company,
    )
    members_event = _event(client, company, "item update", group["id"])
    client.run("undo", {"event_id": members_event}, company=company)
    restored_group = client.run(
        "item show", {"item": group["id"]}, company=company
    )
    active = [row for row in restored_group["members"] if row["active"]]
    assert [(row["id"], row["component_item_id"], row["quantity"]) for row in active] == [
        (first_member_id, first["id"], "1")
    ]

    client.run("item deactivate", {"item": group["id"]}, company=company)
    client.run("undo", {"event_id": first_create_event}, company=company)
    inactive_component = client.run(
        "item show", {"item": first["id"]}, company=company
    )
    assert inactive_component["active"] is False


def test_undo_item_vendor_profile_restores_exact_child_values(client):
    company = "Demo Plumbing Co"
    expense = next(
        row["id"]
        for row in client.account.list(company=company)["items"]
        if row["full_name"] == "Professional Fees"
    )
    first_vendor = client.run(
        "vendor create", {"name": "Undo first supplier"}, company=company
    )
    second_vendor = client.run(
        "vendor create", {"name": "Undo second supplier"}, company=company
    )
    item = client.run(
        "item create",
        {
            "name": "Undo purchased service",
            "type": "service",
            "sales_enabled": False,
            "purchase_enabled": True,
            "purchase_description": "External work",
            "cost": "50.00",
            "expense_account_id": expense,
            "vendor_profiles": [
                {
                    "vendor_id": first_vendor["id"],
                    "preferred_rank": 1,
                    "purchase_cost": "48.75",
                    "minimum_quantity": "1.5",
                    "lead_time_days": 3,
                }
            ],
        },
        company=company,
    )
    first_profile_id = item["vendor_profiles"][0]["id"]
    client.run(
        "item update",
        {
            "item": item["id"],
            "vendor_profiles": [
                {"vendor_id": second_vendor["id"], "preferred_rank": 1}
            ],
        },
        company=company,
    )
    original = _event(client, company, "item update", item["id"])
    client.run("undo", {"event_id": original}, company=company)
    restored = client.run("item show", {"item": item["id"]}, company=company)
    active_profiles = [row for row in restored["vendor_profiles"] if row["active"]]
    assert len(active_profiles) == 1
    assert active_profiles[0]["id"] == first_profile_id
    assert active_profiles[0]["vendor_id"] == first_vendor["id"]
    assert active_profiles[0]["preferred_rank"] == 1
    assert active_profiles[0]["purchase_cost"] == {
        "amount": "48.75",
        "currency": "USD",
        "minor_units": 4875,
    }
    assert active_profiles[0]["minimum_quantity"] == "1.5"
    assert active_profiles[0]["lead_time_days"] == 3


def test_undo_party_aggregate_restores_nested_contact_and_address_identities(client):
    company = "Demo Plumbing Co"
    customer = client.customer.create(
        name="Undo aggregate customer",
        billing_address={"line1": "10 Billing Road"},
        credit_limit="100.00",
        shipping_addresses=[
            {"label": "Office", "is_default": True, "line1": "1 First Ave"}
        ],
        contacts=[
            {
                "role": "primary",
                "display_name": "Original Contact",
                "points": [
                    {
                        "kind": "linked_in",
                        "custom_label": "Profile",
                        "value": "original-contact",
                    }
                ],
            }
        ],
        company=company,
    )
    address_id = customer["shipping_addresses"][0]["id"]
    contact_id = customer["contacts"][0]["id"]
    point_id = customer["contacts"][0]["points"][0]["id"]
    client.customer.update(
        customer=customer["id"],
        billing_address={"line1": "20 Changed Road"},
        credit_limit="250.00",
        shipping_addresses=[
            {"label": "Site", "is_default": True, "line1": "2 Second Ave"}
        ],
        contacts=[
            {
                "role": "primary",
                "display_name": "Replacement Contact",
                "points": [{"kind": "main_email", "value": "new@example.test"}],
            }
        ],
        company=company,
    )
    original = _event(client, company, "customer update", customer["id"])
    client.customer.update(
        customer=customer["id"], notes="keep later note", company=company
    )
    client.run("undo", {"event_id": original}, company=company)
    restored = client.customer.show(customer=customer["id"], company=company)
    assert restored["notes"] == "keep later note"
    assert restored["billing_address"]["line1"] == "10 Billing Road"
    assert restored["credit_limit"] == {
        "amount": "100.00",
        "currency": "USD",
        "minor_units": 10000,
    }
    assert [
        (row["id"], row["label"], row["line1"])
        for row in restored["shipping_addresses"]
    ] == [(address_id, "Office", "1 First Ave")]
    contacts = restored["contacts"]
    assert [(row["id"], row["display_name"]) for row in contacts] == [
        (contact_id, "Original Contact")
    ]
    points = contacts[0]["points"]
    assert [(row["id"], row["kind"], row["value"]) for row in points] == [
        (point_id, "linked_in", "original-contact")
    ]

    with_alternate = client.customer.update(
        customer=customer["id"],
        contacts=[
            {
                "id": contact_id,
                "role": "primary",
                "display_name": "Original Contact",
                "points": [
                    {
                        "id": point_id,
                        "kind": "linked_in",
                        "custom_label": "Profile",
                        "value": "original-contact",
                    }
                ],
            },
            {"role": "alternate", "display_name": "Alternate Contact"},
        ],
        company=company,
    )
    alternate_id = next(
        row["id"] for row in with_alternate["contacts"] if row["role"] == "alternate"
    )
    client.customer.update(
        customer=customer["id"],
        contacts=[
            {
                "id": contact_id,
                "role": "alternate",
                "display_name": "Original Contact",
                "points": [
                    {
                        "id": point_id,
                        "kind": "linked_in",
                        "custom_label": "Profile",
                        "value": "original-contact",
                    }
                ],
            },
            {
                "id": alternate_id,
                "role": "primary",
                "display_name": "Alternate Contact",
            },
        ],
        company=company,
    )
    swap_event = _event(client, company, "customer update", customer["id"])
    client.run("undo", {"event_id": swap_event}, company=company)
    roles = {
        row["id"]: row["role"]
        for row in client.customer.show(
            customer=customer["id"], company=company
        )["contacts"]
    }
    assert roles == {contact_id: "primary", alternate_id: "alternate"}


def test_undo_vendor_expense_collection_restores_stable_child_identity(client):
    company = "Demo Plumbing Co"
    expense_account = client.account.list(
        filter=["type=expense"], company=company
    )["items"][0]["id"]
    vendor = client.vendor.create(
        name="Undo expense defaults",
        expense_accounts=[{"account_id": expense_account}],
        company=company,
    )
    original_child_id = vendor["expense_accounts"][0]["id"]
    replaced = client.vendor.update(
        vendor=vendor["id"],
        expense_accounts=[{"account_id": expense_account}],
        company=company,
    )
    assert replaced["expense_accounts"][0]["id"] != original_child_id
    original = _event(client, company, "vendor update", vendor["id"])
    client.run("undo", {"event_id": original}, company=company)
    restored = client.vendor.show(vendor=vendor["id"], company=company)
    assert [row["id"] for row in restored["expense_accounts"]] == [
        original_child_id
    ]


def test_undo_customer_vendor_link_is_ordered_and_preserves_disjoint_edits(client):
    company = "Demo Plumbing Co"
    customer = client.customer.create(name="Undo linked customer", company=company)
    vendor = client.vendor.create(name="Undo linked vendor", company=company)
    linked = client.run(
        "customer link-vendor",
        {
            "customer": customer["id"],
            "vendor": vendor["id"],
            "expected_customer_version": 1,
            "expected_vendor_version": 1,
        },
        company=company,
    )
    original = _event(client, company, "customer link-vendor")
    client.customer.update(
        customer=customer["id"], notes="later customer note", company=company
    )

    result = client.run("undo", {"event_id": original}, company=company)
    assert [
        (row["record_type"], row["action"])
        for row in result["affected_records"]
    ] == [
        ("customer_vendor_link", "deactivate"),
        ("vendor", "update"),
        ("customer", "update"),
    ]
    shown_customer = client.customer.show(customer=customer["id"], company=company)
    shown_vendor = client.vendor.show(vendor=vendor["id"], company=company)
    assert shown_customer["linked_vendor_id"] is None
    assert shown_customer["notes"] == "later customer note"
    assert shown_vendor["linked_customer_id"] is None
    with open_database(_company_db(client, company), writable=False) as db:
        link = db.conn.execute(
            sa.select(schema.customer_vendor_links).where(
                schema.customer_vendor_links.c.id == linked["link_id"]
            )
        ).mappings().one()
        assert link["active"] is False
        entries = db.conn.execute(
            sa.select(schema.audit_entries)
            .where(schema.audit_entries.c.event_id == result["undo_event_id"])
            .order_by(schema.audit_entries.c.id)
        ).mappings().all()
        assert [row["record_type"] for row in entries] == [
            "customer_vendor_link",
            "vendor",
            "customer",
        ]
        assert audit.decode_snapshot(entries[-1]["before"])["notes"] == (
            "later customer note"
        )

    # Intervening unlink/relink is eligible once every current logical value is
    # back at the original event's after-value.
    customer_2 = client.customer.create(name="Undo relink customer", company=company)
    vendor_2 = client.vendor.create(name="Undo relink vendor", company=company)
    first_link = client.run(
        "customer link-vendor",
        {
            "customer": customer_2["id"],
            "vendor": vendor_2["id"],
            "expected_customer_version": 1,
            "expected_vendor_version": 1,
        },
        company=company,
    )
    first_event = _event(client, company, "customer link-vendor")
    client.run(
        "customer unlink-vendor",
        {
            "customer": customer_2["id"],
            "expected_customer_version": 2,
            "expected_vendor_version": 2,
            "expected_link_version": first_link["link_version"],
        },
        company=company,
    )
    reactivated = client.run(
        "customer link-vendor",
        {
            "customer": customer_2["id"],
            "vendor": vendor_2["id"],
            "expected_customer_version": 3,
            "expected_vendor_version": 3,
            "expected_link_version": 2,
        },
        company=company,
    )
    reactivated_event = _event(client, company, "customer link-vendor")
    reactivated_undo = client.run(
        "undo", {"event_id": reactivated_event}, company=company
    )
    assert reactivated_undo["affected_records"][0]["action"] == "update"
    client.run(
        "customer link-vendor",
        {
            "customer": customer_2["id"],
            "vendor": vendor_2["id"],
            "expected_customer_version": 5,
            "expected_vendor_version": 5,
            "expected_link_version": reactivated["link_version"] + 1,
        },
        company=company,
    )
    client.run("undo", {"event_id": first_event}, company=company)
    assert client.customer.show(
        customer=customer_2["id"], company=company
    )["linked_vendor_id"] is None

    overlap_customer = client.customer.create(
        name="Undo link overlap customer", company=company
    )
    overlap_vendor = client.vendor.create(
        name="Undo link overlap vendor", company=company
    )
    overlap_link = client.run(
        "customer link-vendor",
        {
            "customer": overlap_customer["id"],
            "vendor": overlap_vendor["id"],
            "expected_customer_version": 1,
            "expected_vendor_version": 1,
        },
        company=company,
    )
    overlap_event = _event(client, company, "customer link-vendor")
    client.run(
        "customer unlink-vendor",
        {
            "customer": overlap_customer["id"],
            "expected_customer_version": 2,
            "expected_vendor_version": 2,
            "expected_link_version": overlap_link["link_version"],
        },
        company=company,
    )
    overlap = _error(
        client.run, "undo", {"event_id": overlap_event}, company=company
    )
    assert overlap.code == "E_UNDO_CONFLICT"
    assert overlap.details["record_type"] == "customer_vendor_link"
    assert overlap.details["field"] == "active"
    assert overlap.details["conflicting_event"]["command"] == (
        "customer unlink-vendor"
    )


def test_undo_customer_vendor_unlink_rechecks_endpoints_and_is_atomic(client):
    company = "Demo Plumbing Co"
    customer = client.customer.create(name="Undo unlinked customer", company=company)
    vendor = client.vendor.create(name="Undo unlinked vendor", company=company)
    linked = client.run(
        "customer link-vendor",
        {
            "customer": customer["id"],
            "vendor": vendor["id"],
            "expected_customer_version": 1,
            "expected_vendor_version": 1,
        },
        company=company,
    )
    client.run(
        "customer unlink-vendor",
        {
            "customer": customer["id"],
            "expected_customer_version": 2,
            "expected_vendor_version": 2,
            "expected_link_version": linked["link_version"],
        },
        company=company,
    )
    original = _event(client, company, "customer unlink-vendor")
    client.vendor.update(
        vendor=vendor["id"], notes="later vendor note", company=company
    )
    result = client.run("undo", {"event_id": original}, company=company)
    assert [row["record_type"] for row in result["affected_records"]] == [
        "vendor",
        "customer",
        "customer_vendor_link",
    ]
    assert client.customer.show(
        customer=customer["id"], company=company
    )["linked_vendor_id"] == vendor["id"]
    restored_vendor = client.vendor.show(vendor=vendor["id"], company=company)
    assert restored_vendor["linked_customer_id"] == customer["id"]
    assert restored_vendor["notes"] == "later vendor note"

    blocked_customer = client.customer.create(
        name="Undo blocked unlink customer", company=company
    )
    blocked_vendor = client.vendor.create(
        name="Undo blocked unlink vendor", company=company
    )
    blocked_link = client.run(
        "customer link-vendor",
        {
            "customer": blocked_customer["id"],
            "vendor": blocked_vendor["id"],
            "expected_customer_version": 1,
            "expected_vendor_version": 1,
        },
        company=company,
    )
    client.run(
        "customer unlink-vendor",
        {
            "customer": blocked_customer["id"],
            "expected_customer_version": 2,
            "expected_vendor_version": 2,
            "expected_link_version": blocked_link["link_version"],
        },
        company=company,
    )
    blocked_event = _event(client, company, "customer unlink-vendor")
    client.vendor.deactivate(vendor=blocked_vendor["id"], company=company)
    before_customer = client.customer.show(
        customer=blocked_customer["id"], company=company
    )
    before_vendor = client.vendor.show(vendor=blocked_vendor["id"], company=company)
    conflict = _error(
        client.run, "undo", {"event_id": blocked_event}, company=company
    )
    assert conflict.code == "E_UNDO_CONFLICT"
    assert conflict.details["problem"] == "a link endpoint is not active"
    assert client.customer.show(
        customer=blocked_customer["id"], company=company
    )["version"] == before_customer["version"]
    assert client.vendor.show(
        vendor=blocked_vendor["id"], company=company
    )["version"] == before_vendor["version"]


def test_undo_other_name_conversion_restores_source_and_checks_target_dependencies(client):
    company = "Demo Plumbing Co"
    source = client.run(
        "other-name create",
        {"name": "Undo converted source", "notes": "source note"},
        company=company,
    )
    converted = client.run(
        "other-name convert",
        {"other_name": source["id"], "to": "vendor", "expected_version": 1},
        company=company,
    )
    original = _event(client, company, "other-name convert")
    client.vendor.update(
        vendor=converted["target_id"], notes="later target note", company=company
    )
    result = client.run("undo", {"event_id": original}, company=company)
    assert [
        (row["record_type"], row["action"])
        for row in result["affected_records"]
    ] == [("vendor", "deactivate"), ("other_name", "update")]
    restored = client.run(
        "other-name show", {"other_name": source["id"]}, company=company
    )
    assert restored["active"] is True
    assert restored["converted_to_type"] is None
    assert restored["converted_to_id"] is None
    target = client.vendor.show(vendor=converted["target_id"], company=company)
    assert target["active"] is False
    assert target["notes"] == "later target note"

    overlap_source = client.run(
        "other-name create", {"name": "Undo conversion overlap"}, company=company
    )
    overlap_conversion = client.run(
        "other-name convert",
        {
            "other_name": overlap_source["id"],
            "to": "vendor",
            "expected_version": 1,
        },
        company=company,
    )
    overlap_event = _event(client, company, "other-name convert")
    client.vendor.deactivate(
        vendor=overlap_conversion["target_id"], company=company
    )
    overlap = _error(
        client.run, "undo", {"event_id": overlap_event}, company=company
    )
    assert overlap.code == "E_UNDO_CONFLICT"
    assert overlap.details["record_type"] == "vendor"
    assert overlap.details["field"] == "active"
    assert overlap.details["conflicting_event"]["command"] == "vendor deactivate"

    blocked_source = client.run(
        "other-name create", {"name": "Undo conversion dependency"}, company=company
    )
    blocked_conversion = client.run(
        "other-name convert",
        {
            "other_name": blocked_source["id"],
            "to": "vendor",
            "expected_version": 1,
        },
        company=company,
    )
    blocked_event = _event(client, company, "other-name convert")
    endpoint = client.customer.create(
        name="Undo conversion link endpoint", company=company
    )
    client.run(
        "customer link-vendor",
        {
            "customer": endpoint["id"],
            "vendor": blocked_conversion["target_id"],
            "expected_customer_version": 1,
            "expected_vendor_version": 1,
        },
        company=company,
    )
    source_before = client.run(
        "other-name show", {"other_name": blocked_source["id"]}, company=company
    )
    target_before = client.vendor.show(
        vendor=blocked_conversion["target_id"], company=company
    )
    conflict = _error(
        client.run, "undo", {"event_id": blocked_event}, company=company
    )
    assert conflict.code == "E_UNDO_CONFLICT"
    assert conflict.details["problem"] == "active dependent records still use this record"
    assert client.run(
        "other-name show", {"other_name": blocked_source["id"]}, company=company
    )["version"] == source_before["version"]
    assert client.vendor.show(
        vendor=blocked_conversion["target_id"], company=company
    )["version"] == target_before["version"]


def test_undo_rejects_packaged_events_and_requires_admin(client, root):
    company_row = client.company.new(
        legal_name="Undo Role Books LLC",
        home_currency="USD",
        organization="Demo Holdings LLC",
        timezone="UTC",
        chart="none",
    )
    company = company_row["company_id"]
    client.chart.apply(template_id="general", company=company)
    chart_event = _event(client, company, "chart apply")
    error = _error(client.run, "undo", {"event_id": chart_event}, company=company)
    assert error.code == "E_NOT_UNDOABLE"

    account = client.account.create(name="Role target", type="expense", company=company)
    account_event = _event(client, company, "account create", account["id"])
    make_actor(root, "standard-undo", company_role=(company, "standard"))
    denied = _error(
        as_user(root, "standard-undo").run,
        "undo",
        {"event_id": account_event},
        company=company,
    )
    assert denied.code == "E_PERMISSION"
    assert denied.details["required_role"] == "admin"
