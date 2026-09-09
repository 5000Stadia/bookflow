"""Party master data: scalar profiles, aggregates, links, and conversion."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import parties, schema
from bookflow.core import registry
from bookflow.storage.engine import open_database
from tests.conftest import as_user, make_actor

# This bounded package registers itself; the captain adds its lazy registry
# index entry during Row 5 assembly.
import bookflow.commands.party_cmds  # noqa: F401, E402
import bookflow.commands.item_cmds  # noqa: F401, E402


COMPANY = "Demo Plumbing Co"


def _run(client, noun: str, verb: str, payload: dict):
    return client.run(f"{noun} {verb}", payload, company=COMPANY)


def _database(client) -> Path:
    return Path(client.company.show(company=COMPANY)["path"]) / "company.db"


def test_every_party_has_idempotent_create_and_the_six_lifecycle_commands(client):
    payloads = {
        "customer": {"name": "Lifecycle Customer", "company_name": "Lifecycle Co"},
        "vendor": {"name": "Lifecycle Vendor", "eligible_1099": True},
        "employee": {"name": "Lifecycle Employee", "employment_type": "part_time"},
        "other-name": {"name": "Lifecycle Owner", "contact": "Owner Contact"},
    }
    updates = {
        "customer": {"company_name": "Edited Lifecycle Co"},
        "vendor": {"account_number": "V-200"},
        "employee": {"phone": "555-0101"},
        "other-name": {"email": "owner@example.test"},
    }
    for noun, payload in payloads.items():
        created = client.run(
            f"{noun} create",
            payload,
            company=COMPANY,
            idempotency_key=f"party-{noun}",
        )
        replay = client.run(
            f"{noun} create",
            payload,
            company=COMPANY,
            idempotency_key=f"party-{noun}",
        )
        assert replay == {**created, "idempotent_replay": True}

        selector = noun.replace("-", "_")
        shown = _run(client, noun, "show", {selector: created["id"]})
        assert shown["id"] == created["id"] and shown["active"] is True
        listed = _run(client, noun, "list", {"query": created["name"]})
        assert [item["id"] for item in listed["items"]] == [created["id"]]

        updated = _run(
            client,
            noun,
            "update",
            {selector: created["id"], "expected_version": 1, **updates[noun]},
        )
        assert updated["version"] == 2
        assert updated["changed_fields"] == sorted(updates[noun])

        deactivated = _run(
            client,
            noun,
            "deactivate",
            {selector: created["id"], "expected_version": 2},
        )
        assert deactivated["changed"] is True and deactivated["active"] is False
        assert _run(client, noun, "list", {"query": created["name"]})["items"] == []
        visible = _run(
            client,
            noun,
            "list",
            {"query": created["name"], "include_inactive": True},
        )
        assert [item["id"] for item in visible["items"]] == [created["id"]]

        activated = _run(
            client,
            noun,
            "activate",
            {selector: created["id"], "expected_version": 3},
        )
        assert activated["changed"] is True and activated["active"] is True


def test_customer_jobs_inherit_live_and_hierarchy_updates_project_descendants(client):
    term = client.term.list(company=COMPANY)["items"][0]
    parent = client.customer.create(
        name="Inheritance Parent",
        terms_id=term["id"],
        billing_address={"line1": "1 First Ave", "city": "Northport"},
        shipping_addresses=[
            {"label": "Main", "is_default": True, "line1": "1 First Ave"}
        ],
        contacts=[
            {
                "role": "primary",
                "display_name": "Pat Parent",
                "work_phone": "555-0100",
            }
        ],
        company=COMPANY,
    )
    job = client.customer.create(
        name="Kitchen",
        parent_id=parent["id"],
        company=COMPANY,
    )
    phase = client.customer.create(
        name="Phase One",
        parent_id=job["id"],
        company=COMPANY,
    )
    assert job["contact_mode"] == job["address_mode"] == "inherit"
    assert phase["effective_terms_id"] == term["id"]
    assert phase["terms_source_id"] == parent["id"]
    assert phase["effective_billing_address"]["line1"] == "1 First Ave"
    assert phase["shipping_addresses"][0]["line1"] == "1 First Ave"
    assert phase["shipping_addresses_source_id"] == parent["id"]
    assert phase["phone"] == "555-0100"
    assert phase["contacts_source_id"] == parent["id"]

    with pytest.raises(BookflowError) as inherited_shortcut:
        client.customer.update(
            customer=job["id"],
            expected_version=1,
            phone="555-9999",
            company=COMPANY,
        )
    assert inherited_shortcut.value.code == "E_VALIDATION"
    owned = client.customer.update(
        customer=job["id"],
        expected_version=1,
        contact_mode="own",
        contacts=[{"role": "primary", "display_name": "Job Lead", "work_phone": "555-0200"}],
        company=COMPANY,
    )
    assert owned["phone"] == "555-0200"
    assert owned["contacts_source_id"] == job["id"]

    parent = client.customer.update(
        customer=parent["id"],
        expected_version=1,
        name="Renamed Parent",
        billing_address={"line1": "2 Second Ave", "city": "Northport"},
        company=COMPANY,
    )
    assert parent["affected_descendant_ids"] == [job["id"], phase["id"]]
    fresh = client.customer.show(customer=phase["id"], company=COMPANY)
    assert fresh["full_name"] == "Renamed Parent:Kitchen:Phase One"
    assert fresh["effective_billing_address"]["line1"] == "2 Second Ave"
    assert fresh["phone"] == "555-0200"

    with pytest.raises(BookflowError) as blocked:
        client.customer.deactivate(
            customer=parent["id"],
            expected_version=parent["version"],
            company=COMPANY,
        )
    assert blocked.value.code == "E_ACTIVE_DEPENDENTS"
    cascaded = client.customer.deactivate(
        customer=parent["id"],
        expected_version=parent["version"],
        cascade=True,
        company=COMPANY,
    )
    assert cascaded["affected_ids"] == [parent["id"], job["id"], phase["id"]]


def test_party_references_are_active_and_money_is_exact(client):
    vendor_type = client.run(
        "vendor-type create",
        {"name": "Parts House"},
        company=COMPANY,
    )
    vendor = client.vendor.create(
        name="Exact Supply",
        vendor_type_id=vendor_type["id"],
        credit_limit="123.45 USD",
        company=COMPANY,
    )
    assert vendor["credit_limit"] == {
        "amount": "123.45",
        "currency": "USD",
        "minor_units": 12345,
    }
    client.run(
        "vendor-type deactivate",
        {"vendor_type": vendor_type["id"], "expected_version": vendor_type["version"]},
        company=COMPANY,
    )
    with pytest.raises(BookflowError) as inactive:
        client.vendor.create(
            name="Blocked Vendor",
            vendor_type_id=vendor_type["id"],
            company=COMPANY,
        )
    assert inactive.value.code == "E_INACTIVE_REFERENCE"

    with pytest.raises(BookflowError) as floating:
        client.vendor.create(name="Float Vendor", credit_limit=1.25, company=COMPANY)
    assert floating.value.code == "E_VALIDATION"
    with pytest.raises(BookflowError) as currency:
        client.vendor.create(name="EUR Vendor", credit_limit="1.00 EUR", company=COMPANY)
    assert currency.value.code == "E_VALIDATION"
    with pytest.raises(BookflowError) as overflow:
        client.vendor.create(
            name="Overflow Vendor",
            credit_limit="92233720368547758.08",
            company=COMPANY,
        )
    assert overflow.value.code == "E_VALUE_RANGE"


def test_staged_protected_fields_are_not_accepted_by_party_commands(client):
    cases = (
        ("customer create", {"name": "Protected Customer", "payment_profile_ref": "opaque"}),
        ("vendor create", {"name": "Protected Vendor", "tax_profile_ref": "opaque"}),
        ("vendor create", {"name": "Tax Vendor", "tax_id_last4": "1234"}),
        ("employee create", {"name": "Protected Employee", "tax_id_last4": "1234"}),
    )
    for command, payload in cases:
        with pytest.raises(BookflowError) as denied:
            client.run(command, payload, company=COMPANY)
        assert denied.value.code == "E_VALIDATION"


def test_employee_completeness_uses_company_requirements_and_custom_values(client):
    complete = client.employee.create(
        name="Complete Employee",
        first_name="Casey",
        last_name="Worker",
        address={
            "line1": "10 Main St",
            "city": "Portland",
            "state": "OR",
            "postal_code": "97201",
        },
        email="casey@example.test",
        company=COMPANY,
    )
    assert complete["profile_complete"] is True
    assert complete["missing_profile_fields"] == []

    incomplete = client.employee.create(
        name="Incomplete Employee",
        first_name="Only",
        company=COMPANY,
    )
    assert incomplete["profile_complete"] is False
    assert ["last_name"] in incomplete["missing_profile_fields"]
    filtered = client.employee.list(
        filter=["profile_complete=false"],
        company=COMPANY,
    )
    assert incomplete["id"] in {item["id"] for item in filtered["items"]}
    assert complete["id"] not in {item["id"] for item in filtered["items"]}

    definition = client.run(
        "custom-field create",
        {
            "name": "Employee region",
            "kind": "text",
            "scopes": ["employee"],
        },
        company=COMPANY,
    )
    client.company.update(
        required_employee_profile_fields=[[f"custom_fields.{definition['id']}"]],
        company=COMPANY,
    )
    customized = client.employee.create(
        name="Custom Complete",
        custom_fields={definition["id"]: "North"},
        company=COMPANY,
    )
    assert customized["profile_complete"] is True
    assert customized["custom_fields"][0]["definition_id"] == definition["id"]
    assert customized["custom_fields"][0]["value"] == "North"
    cleared = client.employee.update(
        employee=customized["id"],
        expected_version=1,
        custom_fields={definition["id"]: None},
        company=COMPANY,
    )
    assert cleared["custom_fields"] == []
    assert cleared["profile_complete"] is False
    assert cleared["changed_fields"] == [f"custom_fields.{definition['id']}"]


def test_party_updates_support_blind_warnings_disjoint_merges_and_audit(client):
    vendor = client.vendor.create(name="Concurrent Vendor", company=COMPANY)
    first = client.vendor.update(
        vendor=vendor["id"],
        expected_version=1,
        notes="first edit",
        company=COMPANY,
    )
    merged = client.vendor.update(
        vendor=vendor["id"],
        expected_version=1,
        account_number="A-10",
        company=COMPANY,
    )
    assert merged["version"] == 3
    assert merged["merged_over_versions"] == [2]

    with pytest.raises(BookflowError) as conflict:
        client.vendor.update(
            vendor=vendor["id"],
            expected_version=1,
            notes="stale overlap",
            company=COMPANY,
        )
    assert conflict.value.code == "E_VERSION_CONFLICT"

    blind = client.vendor.update(
        vendor=vendor["id"],
        company_name="Blind LLC",
        company=COMPANY,
    )
    assert blind["warnings"] == [
        "Blind write: version 3 and fields company_name were not compared."
    ]
    events = client.audit.list(
        company=COMPANY,
        record_type="vendor",
        record_id=vendor["id"],
    )["items"]
    assert [event["command"] for event in events] == [
        "vendor update",
        "vendor update",
        "vendor update",
        "vendor create",
    ]


def test_normalized_selectors_and_party_list_filters(client):
    vendor = client.vendor.create(name="  Café Supply  ", eligible_1099=True, company=COMPANY)
    assert client.vendor.show(vendor="CAFÉ SUPPLY", company=COMPANY)["id"] == vendor["id"]
    assert client.vendor.list(
        query="café",
        filter=["eligible_1099=true"],
        company=COMPANY,
    )["count"] == 1

    owner = client.run(
        "other-name create",
        {"name": "Unconverted Owner"},
        company=COMPANY,
    )
    result = client.run(
        "other-name list",
        {"filter": ["unconverted=true"]},
        company=COMPANY,
    )
    assert owner["id"] in {item["id"] for item in result["items"]}


def test_owned_contacts_addresses_and_expense_accounts_reconcile_stable_identity(client):
    customer = client.customer.create(
        name="Aggregate Customer",
        shipping_addresses=[
            {"label": "Office", "is_default": True, "line1": "100 Office Way"},
            {"label": "Site", "line1": "200 Site Way"},
        ],
        contacts=[
            {
                "role": "primary",
                "display_name": "Alex Aggregate",
                "work_phone": "555-1000",
                "points": [
                    {"kind": "linked_in", "custom_label": "Work profile", "value": "alex-a"}
                ],
            }
        ],
        phone="555-1000",
        company=COMPANY,
    )
    contact_id = customer["contacts"][0]["id"]
    point_id = customer["contacts"][0]["points"][0]["id"]
    address_id = customer["shipping_addresses"][0]["id"]

    shortcut = client.customer.update(
        customer=customer["id"],
        expected_version=1,
        phone="555-1001",
        company=COMPANY,
    )
    assert shortcut["changed_fields"] == ["contacts"]
    assert shortcut["contacts"][0]["id"] == contact_id
    assert shortcut["contacts"][0]["points"][0]["id"] == point_id
    assert shortcut["phone"] == "555-1001"

    with pytest.raises(BookflowError) as conflicting_shortcut:
        client.customer.update(
            customer=customer["id"],
            expected_version=2,
            contacts=[
                {
                    "id": contact_id,
                    "role": "primary",
                    "display_name": "Alex Aggregate",
                    "work_phone": "555-2000",
                    "points": [
                        {
                            "id": point_id,
                            "kind": "linked_in",
                            "custom_label": "Work profile",
                            "value": "alex-a",
                        }
                    ],
                }
            ],
            phone="555-9999",
            company=COMPANY,
        )
    assert conflicting_shortcut.value.code == "E_VALIDATION"

    reidentified = client.customer.update(
        customer=customer["id"],
        expected_version=2,
        shipping_addresses=[
            {"label": "Office", "is_default": True, "line1": "100 New Office Way"}
        ],
        contacts=[
            {
                "role": "primary",
                "display_name": "New Primary",
                "work_phone": "555-2100",
            }
        ],
        company=COMPANY,
    )
    replacement_address_id = reidentified["shipping_addresses"][0]["id"]
    assert replacement_address_id != address_id
    assert reidentified["contacts"][0]["id"] != contact_id

    replaced = client.customer.update(
        customer=customer["id"],
        expected_version=3,
        shipping_addresses=[
            {
                "id": replacement_address_id,
                "label": "Head office",
                "is_default": True,
                "line1": "101 Office Way",
            }
        ],
        contacts=[],
        company=COMPANY,
    )
    assert replaced["shipping_addresses"][0]["id"] == replacement_address_id
    assert replaced["shipping_addresses"][0]["label"] == "Head office"
    assert replaced["contacts"] == []
    with open_database(_database(client), writable=False) as db:
        contact = db.conn.execute(
            sa.select(schema.customer_contacts).where(schema.customer_contacts.c.id == contact_id)
        ).mappings().one()
        point = db.conn.execute(
            sa.select(schema.customer_contact_points).where(
                schema.customer_contact_points.c.id == point_id
            )
        ).mappings().one()
        assert contact["active"] is False
        assert point["active"] is False
    event = client.audit.list(
        company=COMPANY,
        command="customer update",
        record_type="customer",
        record_id=customer["id"],
    )["items"][0]
    entry = client.audit.show(event=event["id"], company=COMPANY)["entries"][0]
    assert {"id": point_id, "active": False} in entry["after"][
        "_contact_points_identities"
    ]

    account = client.account.list(filter=["type=expense"], company=COMPANY)["items"][0]
    vendor = client.vendor.create(
        name="Aggregate Vendor",
        contact="Vera Vendor",
        alt_contact="Victor Alternate",
        phone="555-3000",
        expense_accounts=[{"account_id": account["id"]}],
        company=COMPANY,
    )
    expense_id = vendor["expense_accounts"][0]["id"]
    assert vendor["contact"] == "Vera Vendor"
    assert vendor["phone"] == "555-3000"
    updated = client.vendor.update(
        vendor=vendor["id"],
        expected_version=1,
        expense_accounts=[{"id": expense_id, "account_id": account["id"]}],
        email="vera@example.test",
        company=COMPANY,
    )
    assert updated["expense_accounts"][0]["id"] == expense_id
    assert updated["email"] == "vera@example.test"
    primary_id = next(row["id"] for row in updated["contacts"] if row["role"] == "primary")
    alternate_id = next(row["id"] for row in updated["contacts"] if row["role"] == "alternate")
    swapped = client.vendor.update(
        vendor=vendor["id"],
        expected_version=2,
        contacts=[
            {"id": primary_id, "role": "alternate", "display_name": "Vera Vendor"},
            {"id": alternate_id, "role": "primary", "display_name": "Victor Alternate"},
        ],
        expense_accounts=[{"account_id": account["id"]}],
        company=COMPANY,
    )
    assert [row["id"] for row in swapped["contacts"]] == [primary_id, alternate_id]
    assert [row["role"] for row in swapped["contacts"]] == ["alternate", "primary"]
    assert swapped["expense_accounts"][0]["id"] != expense_id


def test_active_item_purchase_profiles_block_vendor_deactivation(client):
    accounts = {
        item["full_name"]: item["id"]
        for item in client.account.list(company=COMPANY)["items"]
    }
    vendor = client.vendor.create(name="Required Purchase Vendor", company=COMPANY)
    item = client.run(
        "item create",
        {
            "name": "Vendor-backed service",
            "type": "service",
            "sales_enabled": False,
            "purchase_enabled": True,
            "purchase_description": "Externally supplied service",
            "cost": "25.00",
            "expense_account_id": accounts["Professional Fees"],
            "preferred_vendor_id": vendor["id"],
        },
        company=COMPANY,
    )
    profile = client.vendor.show(vendor=vendor["id"], company=COMPANY)[
        "item_vendor_profiles"
    ][0]
    assert profile["item_id"] == item["id"]
    assert profile["item_name"] == item["full_name"]
    assert profile["vendor_id"] == vendor["id"]
    assert profile["preferred_rank"] == 1
    assert profile["purchase_cost"] is None
    assert profile["minimum_quantity"] is None
    with pytest.raises(BookflowError) as blocked:
        client.vendor.deactivate(
            vendor=vendor["id"],
            expected_version=1,
            company=COMPANY,
        )
    assert blocked.value.code == "E_RECORD_IN_USE"
    assert blocked.value.details["dependents"] == [
        {"record_type": "item_preferred_vendor", "count": 1},
        {"record_type": "item_vendor_profile", "count": 1},
    ]

    client.run(
        "item deactivate",
        {"item": item["id"], "expected_version": 1},
        company=COMPANY,
    )
    retired = client.vendor.deactivate(
        vendor=vendor["id"],
        expected_version=1,
        company=COMPANY,
    )
    assert retired["active"] is False


def test_customer_vendor_link_unlink_reactivation_versions_and_audit_order(client):
    customer = client.customer.create(name="Linked Customer", company=COMPANY)
    vendor = client.vendor.create(name="Linked Vendor", company=COMPANY)
    linked = client.run(
        "customer link-vendor",
        {
            "customer": customer["id"],
            "vendor": vendor["id"],
            "expected_customer_version": 1,
            "expected_vendor_version": 1,
        },
        company=COMPANY,
        idempotency_key="link-customer-vendor",
    )
    replay = client.run(
        "customer link-vendor",
        {
            "customer": customer["id"],
            "vendor": vendor["id"],
            "expected_customer_version": 1,
            "expected_vendor_version": 1,
        },
        company=COMPANY,
        idempotency_key="link-customer-vendor",
    )
    assert replay == {**linked, "idempotent_replay": True}
    shown_customer = client.customer.show(customer=customer["id"], company=COMPANY)
    shown_vendor = client.vendor.show(vendor=vendor["id"], company=COMPANY)
    assert shown_customer["linked_vendor_id"] == vendor["id"]
    assert shown_vendor["linked_customer_id"] == customer["id"]
    expected_link_state = [{
        "id": linked["link_id"],
        "version": 1,
        "customer_id": customer["id"],
        "vendor_id": vendor["id"],
        "active": True,
    }]
    assert shown_customer["vendor_links"] == expected_link_state
    assert shown_vendor["customer_links"] == expected_link_state

    event = client.audit.list(company=COMPANY, command="customer link-vendor")["items"][0]
    details = client.audit.show(event=event["id"], company=COMPANY)
    assert [entry["record_type"] for entry in details["entries"]] == [
        "customer",
        "vendor",
        "customer_vendor_link",
    ]
    with pytest.raises(BookflowError) as blocked:
        client.vendor.deactivate(vendor=vendor["id"], expected_version=2, company=COMPANY)
    assert blocked.value.code == "E_RECORD_IN_USE"

    unlinked = client.run(
        "customer unlink-vendor",
        {
            "customer": customer["id"],
            "expected_customer_version": 2,
            "expected_vendor_version": 2,
            "expected_link_version": 1,
        },
        company=COMPANY,
        idempotency_key="unlink-customer-vendor",
    )
    assert unlinked["active"] is False
    unlinked_customer = client.customer.show(customer=customer["id"], company=COMPANY)
    unlinked_vendor = client.vendor.show(vendor=vendor["id"], company=COMPANY)
    assert unlinked_customer["linked_vendor_id"] is None
    assert unlinked_vendor["linked_customer_id"] is None
    assert unlinked_customer["vendor_links"] == [{**expected_link_state[0], "version": 2, "active": False}]
    assert unlinked_vendor["customer_links"] == [{**expected_link_state[0], "version": 2, "active": False}]
    event = client.audit.list(company=COMPANY, command="customer unlink-vendor")["items"][0]
    details = client.audit.show(event=event["id"], company=COMPANY)
    assert [entry["record_type"] for entry in details["entries"]] == [
        "customer_vendor_link",
        "customer",
        "vendor",
    ]

    relinked = client.run(
        "customer link-vendor",
        {
            "customer": customer["id"],
            "vendor": vendor["id"],
            "expected_customer_version": 3,
            "expected_vendor_version": 3,
            "expected_link_version": 2,
        },
        company=COMPANY,
    )
    assert relinked["link_id"] == linked["link_id"]
    assert relinked["link_version"] == 3


def test_other_name_conversion_is_atomic_idempotent_and_preserves_source(client):
    definition = client.run(
        "custom-field create",
        {
            "name": "Party marker",
            "kind": "text",
            "scopes": ["other_name", "vendor"],
        },
        company=COMPANY,
    )
    source = client.run(
        "other-name create",
        {
            "name": "Convertible Owner",
            "company_name": "Convertible LLC",
            "contact": "Connie Contact",
            "phone": "555-4000",
            "email": "connie@example.test",
            "address": {"line1": "400 Convert Rd"},
            "account_number": "OWN-4",
            "custom_fields": {definition["id"]: "founder"},
        },
        company=COMPANY,
    )
    converted = client.run(
        "other-name convert",
        {"other_name": source["id"], "to": "vendor", "expected_version": 1},
        company=COMPANY,
        idempotency_key="convert-owner-vendor",
    )
    replay = client.run(
        "other-name convert",
        {"other_name": source["id"], "to": "vendor", "expected_version": 1},
        company=COMPANY,
        idempotency_key="convert-owner-vendor",
    )
    assert replay == {**converted, "idempotent_replay": True}
    target = client.vendor.show(vendor=converted["target_id"], company=COMPANY)
    assert target["company_name"] == "Convertible LLC"
    assert target["address"]["line1"] == "400 Convert Rd"
    assert target["contact"] == "Connie Contact"
    assert target["phone"] == "555-4000"
    assert target["account_number"] == "OWN-4"
    assert target["custom_fields"][0]["value"] == "founder"

    retired = client.run(
        "other-name show",
        {"other_name": source["id"]},
        company=COMPANY,
    )
    assert retired["active"] is False
    assert retired["converted_to_type"] == "vendor"
    assert retired["converted_to_id"] == target["id"]
    assert retired["phone"] == "555-4000"
    with pytest.raises(BookflowError) as second_conversion:
        client.run(
            "other-name convert",
            {"other_name": source["id"], "to": "employee", "expected_version": 2},
            company=COMPANY,
        )
    assert second_conversion.value.code == "E_RECORD_IN_USE"
    with pytest.raises(BookflowError) as ordinary_reactivation:
        client.run(
            "other-name activate",
            {"other_name": source["id"], "expected_version": 2},
            company=COMPANY,
        )
    assert ordinary_reactivation.value.code == "E_RECORD_IN_USE"

    event = client.audit.list(company=COMPANY, command="other-name convert")["items"][0]
    details = client.audit.show(event=event["id"], company=COMPANY)
    assert [entry["record_type"] for entry in details["entries"]] == [
        "other_name",
        "vendor",
    ]


def test_party_update_schema_retains_create_constraints_and_contacts_never_go_blank(client):
    create = registry.get("customer create").input_model.model_json_schema()["properties"]
    update = registry.get("customer update").input_model.model_json_schema()["properties"]
    assert update["name"]["anyOf"][0]["minLength"] == create["name"]["minLength"] == 1
    assert update["name"]["anyOf"][0]["maxLength"] == create["name"]["maxLength"] == 200
    assert update["parent_id"]["anyOf"][0]["minLength"] == 26
    assert update["parent_id"]["anyOf"][0]["maxLength"] == 26

    derived = client.vendor.create(
        name="Derived Contact Vendor",
        phone="555-8100",
        company=COMPANY,
    )
    assert derived["contacts"][0]["display_name"] == "Derived Contact Vendor"
    for display_name in (None, "   "):
        with pytest.raises(BookflowError) as blank:
            client.vendor.create(
                name=f"Blank Contact {display_name!r}",
                contacts=[{"role": "primary", "display_name": display_name}],
                company=COMPANY,
            )
        assert blank.value.code == "E_VALIDATION"


def test_explicit_retained_contact_distinguishes_omitted_points_from_empty(client):
    created = client.customer.create(
        name="Nested Point Preservation",
        contacts=[
            {
                "role": "primary",
                "display_name": "Point Keeper",
                "points": [{"kind": "linked_in", "value": "keeper-profile"}],
            }
        ],
        company=COMPANY,
    )
    contact = created["contacts"][0]
    preserved = client.customer.update(
        customer=created["id"],
        expected_version=1,
        contacts=[
            {
                "id": contact["id"],
                "role": "primary",
                "display_name": "Point Keeper Renamed",
            }
        ],
        company=COMPANY,
    )
    assert preserved["contacts"][0]["points"] == contact["points"]
    cleared = client.customer.update(
        customer=created["id"],
        expected_version=2,
        contacts=[
            {
                "id": contact["id"],
                "role": "primary",
                "display_name": "Point Keeper Renamed",
                "points": [],
            }
        ],
        company=COMPANY,
    )
    assert cleared["contacts"][0]["points"] == []


def test_customer_list_uses_effective_values_and_real_child_and_custom_data(client):
    customer_type = client.run(
        "customer-type create", {"name": "Inherited Search Type"}, company=COMPANY
    )
    custom = client.run(
        "custom-field create",
        {"name": "Search marker", "kind": "text", "scopes": ["customer"]},
        company=COMPANY,
    )
    parent = client.customer.create(
        name="Effective Search Parent",
        customer_type_id=customer_type["id"],
        preferred_delivery_method="mail",
        billing_address={"line1": "801 Billing Blvd"},
        shipping_addresses=[
            {"label": "Warehouse", "is_default": True, "line1": "802 Shipping Shore"}
        ],
        contacts=[
            {
                "role": "primary",
                "display_name": "Zulu Parent Contact",
                "work_phone": "555-8101",
                "points": [{"kind": "linked_in", "value": "zulu-profile"}],
            }
        ],
        custom_fields={custom["id"]: "searchable-purple"},
        company=COMPANY,
    )
    job = client.customer.create(
        name="Inherited Search Job",
        parent_id=parent["id"],
        company=COMPANY,
    )
    assert job["preferred_delivery_method"] is None
    assert job["effective_preferred_delivery_method"] == "mail"
    assert job["preferred_delivery_method_source_id"] == parent["id"]

    filtered = client.customer.list(
        filter=[f"customer_type_id={customer_type['id']}"], company=COMPANY
    )
    assert {parent["id"], job["id"]} <= {item["id"] for item in filtered["items"]}
    for query in ("555-8101", "zulu-profile", "Shipping Shore", "Billing Blvd"):
        result = client.customer.list(query=query, company=COMPANY)
        assert {parent["id"], job["id"]} <= {item["id"] for item in result["items"]}
    custom_result = client.customer.list(query="searchable-purple", company=COMPANY)
    assert parent["id"] in {item["id"] for item in custom_result["items"]}

    alpha = client.customer.create(
        name="Contact Sort Alpha Record",
        contacts=[{"role": "primary", "display_name": "Alpha Contact"}],
        company=COMPANY,
    )
    ordered = client.customer.list(sort="primary_contact", company=COMPANY)["items"]
    positions = {item["id"]: index for index, item in enumerate(ordered)}
    assert positions[alpha["id"]] < positions[parent["id"]]

    vendor = client.vendor.create(
        name="Child Search Vendor",
        contacts=[
            {
                "role": "primary",
                "display_name": "Vendor Contact",
                "points": [{"kind": "skype_id", "value": "vendor-skype-8102"}],
            }
        ],
        company=COMPANY,
    )
    assert vendor["id"] in {
        item["id"]
        for item in client.vendor.list(query="vendor-skype-8102", company=COMPANY)["items"]
    }


def test_structured_and_money_snapshots_conflict_on_their_logical_fields(client):
    customer = client.customer.create(
        name="Logical Snapshot Customer",
        billing_address={"line1": "One Logical Way"},
        company=COMPANY,
    )
    client.customer.update(
        customer=customer["id"],
        expected_version=1,
        billing_address={"line1": "Two Logical Way"},
        company=COMPANY,
    )
    with pytest.raises(BookflowError) as address_conflict:
        client.customer.update(
            customer=customer["id"],
            expected_version=1,
            billing_address={"line1": "Stale Logical Way"},
            company=COMPANY,
        )
    assert address_conflict.value.code == "E_VERSION_CONFLICT"
    assert address_conflict.value.details["changed_fields"] == ["billing_address"]

    vendor = client.vendor.create(
        name="Logical Snapshot Vendor", credit_limit="10.01", company=COMPANY
    )
    client.vendor.update(
        vendor=vendor["id"], expected_version=1, credit_limit="20.02", company=COMPANY
    )
    with pytest.raises(BookflowError) as money_conflict:
        client.vendor.update(
            vendor=vendor["id"], expected_version=1, credit_limit="30.03", company=COMPANY
        )
    assert money_conflict.value.code == "E_VERSION_CONFLICT"
    assert money_conflict.value.details["changed_fields"] == ["credit_limit"]

    customer_event = client.audit.list(
        company=COMPANY,
        command="customer update",
        record_id=customer["id"],
    )["items"][0]
    customer_after = client.audit.show(
        event=customer_event["id"], company=COMPANY
    )["entries"][0]["after"]
    assert customer_after["billing_address"]["line1"] == "Two Logical Way"
    assert "billing_line1" not in customer_after
    vendor_event = client.audit.list(
        company=COMPANY,
        command="vendor update",
        record_id=vendor["id"],
    )["items"][0]
    vendor_after = client.audit.show(
        event=vendor_event["id"], company=COMPANY
    )["entries"][0]["after"]
    assert vendor_after["credit_limit"]["amount"] == "20.02"
    assert "credit_limit_minor_units" not in vendor_after


def test_composites_require_exact_versions_and_report_all_stale_records(client):
    customer = client.customer.create(name="Composite Customer", company=COMPANY)
    vendor = client.vendor.create(name="Composite Vendor", company=COMPANY)
    linked = client.run(
        "customer link-vendor",
        {
            "customer": customer["id"],
            "vendor": vendor["id"],
            "expected_customer_version": 1,
            "expected_vendor_version": 1,
        },
        company=COMPANY,
    )
    client.run(
        "customer unlink-vendor",
        {
            "customer": customer["id"],
            "expected_customer_version": 2,
            "expected_vendor_version": 2,
            "expected_link_version": linked["link_version"],
        },
        company=COMPANY,
    )
    client.customer.update(
        customer=customer["id"], expected_version=3, notes="new endpoint", company=COMPANY
    )
    client.vendor.update(
        vendor=vendor["id"], expected_version=3, account_number="new endpoint", company=COMPANY
    )
    with pytest.raises(BookflowError) as conflicts:
        client.run(
            "customer link-vendor",
            {
                "customer": customer["id"],
                "vendor": vendor["id"],
                "expected_customer_version": 3,
                "expected_vendor_version": 3,
                "expected_link_version": 1,
            },
            company=COMPANY,
        )
    assert conflicts.value.code == "E_VERSION_CONFLICT"
    records = conflicts.value.details["records"]
    assert [item["record_type"] for item in records] == [
        "customer",
        "vendor",
        "customer_vendor_link",
    ]
    assert all(item["updated_by"] and item["updated_by_name"] for item in records)
    assert all(item["updated_via"] == "python" for item in records)
    assert all(item["seconds_since_update"] >= 0 for item in records)
    assert records[0]["changed_fields"] == ["notes"]
    assert records[1]["changed_fields"] == ["account_number"]
    assert records[2]["changed_fields"] == ["active"]

    source = client.run(
        "other-name create", {"name": "Stale Conversion Source"}, company=COMPANY
    )
    client.run(
        "other-name update",
        {"other_name": source["id"], "expected_version": 1, "notes": "new note"},
        company=COMPANY,
    )
    with pytest.raises(BookflowError) as conversion_conflict:
        client.run(
            "other-name convert",
            {"other_name": source["id"], "to": "vendor", "expected_version": 1},
            company=COMPANY,
        )
    assert conversion_conflict.value.code == "E_VERSION_CONFLICT"
    assert conversion_conflict.value.details["records"][0]["changed_fields"] == ["notes"]
    assert client.run(
        "other-name show", {"other_name": source["id"]}, company=COMPANY
    )["active"] is True


def test_party_composites_roll_back_after_injected_apply_failure(client, monkeypatch):
    customer = client.customer.create(name="Rollback Link Customer", company=COMPANY)
    vendor = client.vendor.create(name="Rollback Link Vendor", company=COMPANY)
    link_events_before = client.audit.list(
        company=COMPANY, command="customer link-vendor"
    )["count"]
    original_link = parties.persist_customer_vendor_link

    def fail_after_link(db, plan):
        original_link(db, plan)
        raise RuntimeError("injected after complete link storage")

    monkeypatch.setattr(parties, "persist_customer_vendor_link", fail_after_link)
    with pytest.raises(RuntimeError, match="injected"):
        client.run(
            "customer link-vendor",
            {
                "customer": customer["id"],
                "vendor": vendor["id"],
                "expected_customer_version": 1,
                "expected_vendor_version": 1,
            },
            company=COMPANY,
        )
    assert client.customer.show(customer=customer["id"], company=COMPANY)["version"] == 1
    assert client.vendor.show(vendor=vendor["id"], company=COMPANY)["version"] == 1
    assert (
        client.audit.list(company=COMPANY, command="customer link-vendor")["count"]
        == link_events_before
    )

    monkeypatch.setattr(parties, "persist_customer_vendor_link", original_link)
    source = client.run(
        "other-name create", {"name": "Rollback Conversion Source"}, company=COMPANY
    )
    conversion_events_before = client.audit.list(
        company=COMPANY, command="other-name convert"
    )["count"]
    original_mutation = parties.persist_party_mutation

    def fail_after_target(db, mutation):
        original_mutation(db, mutation)
        raise RuntimeError("injected after conversion target")

    monkeypatch.setattr(parties, "persist_party_mutation", fail_after_target)
    with pytest.raises(RuntimeError, match="injected"):
        client.run(
            "other-name convert",
            {"other_name": source["id"], "to": "vendor", "expected_version": 1},
            company=COMPANY,
        )
    assert client.run(
        "other-name show", {"other_name": source["id"]}, company=COMPANY
    )["active"] is True
    assert client.vendor.list(query="Rollback Conversion Source", company=COMPANY)["count"] == 0
    assert (
        client.audit.list(company=COMPANY, command="other-name convert")["count"]
        == conversion_events_before
    )


def test_party_roles_and_audit_snapshots_do_not_expose_tax_suffix(client, root):
    company_id = client.company.list()["items"][0]["company_id"]
    make_actor(root, "readonly-party", company_role=(company_id, "readonly"))
    make_actor(root, "standard-party", company_role=(company_id, "standard"))
    readonly = as_user(root, "readonly-party")
    standard = as_user(root, "standard-party")
    vendor = client.vendor.create(name="Tax Suffix Vendor", company=COMPANY)
    with open_database(_database(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(
            schema.vendors.update()
            .where(schema.vendors.c.id == vendor["id"])
            .values(tax_id_kind="ein", tax_id_last4="6789", tax_profile_ref="opaque-ref")
        )
        db.raw.execute("COMMIT")

    assert readonly.vendor.show(vendor=vendor["id"], company=COMPANY)["tax_id_last4"] is None
    assert readonly.vendor.list(query="Tax Suffix", company=COMPANY)["count"] == 1
    with pytest.raises(BookflowError) as denied:
        readonly.vendor.create(name="Denied Party", company=COMPANY)
    assert denied.value.code == "E_PERMISSION"
    assert standard.vendor.show(vendor=vendor["id"], company=COMPANY)["tax_id_last4"] is None
    assert client.vendor.show(vendor=vendor["id"], company=COMPANY)["tax_id_last4"] == "6789"

    updated = standard.vendor.update(
        vendor=vendor["id"], expected_version=1, notes="ordinary edit", company=COMPANY
    )
    assert updated["version"] == 2
    event = client.audit.list(
        company=COMPANY, command="vendor update", record_id=vendor["id"]
    )["items"][0]
    entry = client.audit.show(event=event["id"], company=COMPANY)["entries"][0]
    for snapshot in (entry["before"], entry["after"]):
        assert "tax_id_kind" not in snapshot
        assert "tax_id_last4" not in snapshot
        assert "tax_profile_ref" not in snapshot
