"""Item catalogue profiles, aggregates, exact values, and lifecycle commands."""

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.commands import custom_field_cmds as _custom_commands  # noqa: F401
from bookflow.commands import item_cmds as _item_commands  # noqa: F401
from bookflow.commands import party_cmds as _party_commands  # noqa: F401
from bookflow.commands import unit_pricing_cmds as _unit_commands  # noqa: F401
from bookflow.company import schema
from bookflow.storage.engine import open_database


COMPANY = "Demo Plumbing Co"


def _database(client) -> Path:
    return Path(client.company.show(company=COMPANY)["path"]) / "company.db"


def _refs(client) -> dict[str, Any]:
    accounts = {item["full_name"]: item["id"] for item in client.account.list(company=COMPANY)["items"]}
    tax_code = client.run("sales-tax-code list", {}, company=COMPANY)["items"][0]["id"]
    payment_method = client.run("payment-method list", {}, company=COMPANY)["items"][0]["id"]
    tax_vendor = client.run(
        "vendor create", {"name": "State Revenue Agency", "is_tax_agency": True}, company=COMPANY,
    )["id"]
    fixed_asset = client.account.create(name="Equipment", type="fixed_asset", company=COMPANY)["id"]
    return {"accounts": accounts, "tax_code": tax_code, "payment_method": payment_method, "tax_vendor": tax_vendor, "fixed_asset": fixed_asset}


def _service(client, refs, name="Drain cleaning", **extra):
    payload = {
        "name": name,
        "type": "service",
        "sales_enabled": True,
        "description": "Service labor",
        "price": "125.50",
        "income_account_id": refs["accounts"]["Service Income"],
        "sales_tax_code_id": refs["tax_code"],
        **extra,
    }
    return client.run("item create", payload, company=COMPANY)


def _assert_error(client, command: str, payload: dict[str, Any], code: str) -> BookflowError:
    with pytest.raises(BookflowError) as caught:
        client.run(command, payload, company=COMPANY)
    assert caught.value.code == code
    return caught.value


def test_missing_service_fields_are_reported_together_then_corrected(client):
    refs = _refs(client)
    before = client.run("item list", {}, company=COMPANY)
    error = _assert_error(client, "item create", {"name": "One correction", "type": "service"}, "E_VALIDATION")
    assert {f["field"] for f in error.details["fields"]} == {
        "description", "income_account_id", "price", "sales_tax_code_id"}
    assert client.run("item list", {}, company=COMPANY) == before
    created = _service(client, refs, name="One correction", price="0")
    assert created["price"]["minor_units"] == 0


def test_missing_purchase_fields_do_not_request_disabled_sales_fields(client):
    error = _assert_error(client, "item create", {
        "name": "Purchase service", "type": "service",
        "sales_enabled": False, "purchase_enabled": True,
    }, "E_VALIDATION")
    assert {f["field"] for f in error.details["fields"]} == {
        "purchase_description", "expense_account_id", "cost"}


def test_the_item_type_resolves_the_accounts_it_already_determines(client):
    """A system role is unique in a chart, so these fields have one admissible value.

    Asking the caller for the inventory-asset or sales-tax-payable account is a required
    field with a single legal answer. Omitting it resolves; naming the wrong account is
    still refused, and naming the right one still works.
    """
    refs = _refs(client)
    account = refs["accounts"]
    common = {"type": "inventory_part", "sales_enabled": True, "purchase_enabled": True,
              "description": "Brass fitting", "price": "12.50", "purchase_description": "Brass fitting",
              "cost": "7.25", "income_account_id": account["Service Income"],
              "cogs_account_id": account["Cost of Goods Sold"], "sales_tax_code_id": refs["tax_code"]}

    resolved = client.run("item create", dict(common, name="Resolved fitting"), company=COMPANY)
    assert resolved["asset_account_id"] == account["Inventory Asset"]

    named = client.run("item create", dict(common, name="Named fitting",
                                           asset_account_id=account["Inventory Asset"]), company=COMPANY)
    assert named["asset_account_id"] == account["Inventory Asset"]

    error = _assert_error(client, "item create", dict(common, name="Wrong fitting",
                                                      asset_account_id=account["Undeposited Funds"]), "E_VALIDATION")
    assert [f["field"] for f in error.details["fields"]] == ["asset_account_id"]

    tax = client.run("item create", {"name": "County tax", "type": "sales_tax_item", "tax_percent": "2.5",
                                     "tax_agency_vendor_id": refs["tax_vendor"]}, company=COMPANY)
    assert tax["liability_account_id"] == account["Sales Tax Payable"]


def test_every_item_type_has_a_complete_strict_exact_profile(client):
    refs = _refs(client)
    account = refs["accounts"]
    service = _service(client, refs)
    inventory = client.run("item create", {
        "name": "Valve", "type": "inventory_part", "sales_enabled": True,
        "purchase_enabled": True, "description": "Valve", "price": "20.25",
        "purchase_description": "Valve purchase", "cost": "10.13",
        "income_account_id": account["Service Income"],
        "cogs_account_id": account["Cost of Goods Sold"],
        "asset_account_id": account["Inventory Asset"], "sales_tax_code_id": refs["tax_code"],
        "reorder_point_min": "2.500000", "reorder_point_max": "10",
    }, company=COMPANY)
    tax_item = client.run("item create", {
        "name": "Local sales tax", "type": "sales_tax_item", "description": "Local tax",
        "tax_percent": "8.250000", "tax_agency_vendor_id": refs["tax_vendor"],
        "liability_account_id": account["Sales Tax Payable"],
    }, company=COMPANY)
    payloads = [
        {"name": "Purchased service", "type": "service", "sales_enabled": False, "purchase_enabled": True, "purchase_description": "Purchased labor", "cost": "15", "expense_account_id": account["Professional Fees"]},
        {"name": "Nonstock fitting", "type": "non_inventory_part", "sales_enabled": True, "description": "Fitting", "price": "4.50", "income_account_id": account["Service Income"], "sales_tax_code_id": refs["tax_code"]},
        {"name": "Fuel surcharge", "type": "other_charge", "sales_enabled": True, "description": "Fuel", "charge_percent": "2.500000", "income_account_id": account["Service Income"], "sales_tax_code_id": refs["tax_code"]},
        {"name": "Invoice subtotal", "type": "subtotal", "description": "Subtotal"},
        {"name": "Service bundle", "type": "group", "print_members": True, "members": [{"component_item_id": service["id"], "quantity": "1"}]},
        {"name": "Preferred discount", "type": "discount", "description": "Discount", "discount_percent": "5.125000", "income_account_id": account["Service Income"], "sales_tax_code_id": refs["tax_code"]},
        {"name": "Invoice payment", "type": "payment", "description": "Payment", "payment_method_id": refs["payment_method"], "use_undeposited_funds": True},
        {"name": "Tax bundle", "type": "sales_tax_group", "members": [{"component_item_id": tax_item["id"], "quantity": "1"}]},
        {"name": "Valve assembly", "type": "inventory_assembly", "sales_enabled": True, "purchase_enabled": True, "description": "Assembled valve", "price": "40", "purchase_description": "Assembly", "cost": "11", "income_account_id": account["Service Income"], "cogs_account_id": account["Cost of Goods Sold"], "asset_account_id": account["Inventory Asset"], "sales_tax_code_id": refs["tax_code"], "assembly_build_point": "3.25", "members": [{"component_item_id": inventory["id"], "quantity": "1.5"}]},
        {"name": "Service truck", "type": "fixed_asset", "asset_number": "TRUCK-1", "description": "Service truck", "purchase_date": "2025-01-02", "original_cost": "42000.00", "asset_account_id": refs["fixed_asset"], "disposal_status": "in_service", "depreciation_method": "straight_line", "useful_life_months": 60, "book_basis": "42000"},
    ]
    made = [client.run("item create", payload, company=COMPANY) for payload in payloads]
    assert {service["type"], inventory["type"], tax_item["type"], *(item["type"] for item in made)} == {
        "service", "inventory_part", "non_inventory_part", "other_charge", "subtotal",
        "group", "discount", "payment", "sales_tax_item", "sales_tax_group",
        "inventory_assembly", "fixed_asset",
    }
    assert inventory["price"] == {"amount": "20.25", "currency": "USD", "minor_units": 2025}
    assert inventory["cost"]["amount"] == "10.13"
    assert inventory["reorder_point_min"] == "2.5"
    assembly = next(item for item in made if item["type"] == "inventory_assembly")
    assert assembly["bill_of_material_cost"]["minor_units"] == 1520
    tax_group = next(item for item in made if item["type"] == "sales_tax_group")
    assert tax_group["combined_percent"] == "8.25"
    # The flag now says the one thing it could not say before this ledger existed: whether
    # the item has stock behind it. A stock-carrying type reports real figures -- zero here,
    # because nothing has been adjusted in yet -- and every other type reports none at all.
    stocked = {"inventory_part", "inventory_assembly"}
    for row in [service, inventory, tax_item, *made]:
        assert row["inventory_values_available"] is (row["type"] in stocked), row["type"]
        assert row["quantity_on_hand"] == "0" and row["inventory_value"]["minor_units"] == 0

    foreign = _assert_error(client, "item create", {
        "name": "Bad subtotal", "type": "subtotal", "description": "Bad", "price": "1.00",
    }, "E_VALIDATION")
    assert foreign.details["fields"][0]["field"] == "price"
    _assert_error(client, "item create", {
        "name": "Float service", "type": "service", "sales_enabled": True,
        "description": "Bad", "price": 1.25, "income_account_id": account["Service Income"],
    }, "E_VALIDATION")
    missing_tax = _assert_error(client, "item create", {
        "name": "Untaxed service", "type": "service", "sales_enabled": True,
        "description": "Bad", "price": "1.25", "income_account_id": account["Service Income"],
    }, "E_VALIDATION")
    assert missing_tax.details["fields"][0]["field"] == "sales_tax_code_id"


def test_item_lifecycle_preview_idempotency_hierarchy_version_and_audit(client):
    refs = _refs(client)
    payload = {
        "name": "Installation", "type": "service", "sales_enabled": True,
        "description": "Install", "price": "100", "income_account_id": refs["accounts"]["Service Income"],
        "sales_tax_code_id": refs["tax_code"],
    }
    preview = client.run("item create", payload, company=COMPANY, dry_run=True)
    assert preview["dry_run"] is True
    assert client.run("item list", {"query": "Installation"}, company=COMPANY)["count"] == 0
    created = client.run("item create", payload, company=COMPANY, idempotency_key="installation-v1")
    replay = client.run("item create", payload, company=COMPANY, idempotency_key="installation-v1")
    assert replay == {**created, "idempotent_replay": True}
    child = _service(client, refs, name="Rough-in", parent_id=created["id"])
    renamed = client.run("item update", {
        "item": created["id"], "expected_version": 1, "name": "Field installation",
    }, company=COMPANY)
    assert renamed["affected_descendant_ids"] == [child["id"]]
    assert client.run("item show", {"item": child["id"]}, company=COMPANY)["full_name"] == "Field installation:Rough-in"
    first = client.run("item update", {"item": created["id"], "expected_version": 2, "notes": "Crew A"}, company=COMPANY)
    merged = client.run("item update", {"item": created["id"], "expected_version": 2, "barcode": None}, company=COMPANY)
    assert merged["version"] == first["version"]
    conflict = _assert_error(client, "item update", {
        "item": created["id"], "expected_version": 2, "notes": "Crew B",
    }, "E_VERSION_CONFLICT")
    assert "notes" in conflict.details["changed_fields"]

    blocked = _assert_error(client, "item deactivate", {
        "item": created["id"], "expected_version": first["version"],
    }, "E_ACTIVE_DEPENDENTS")
    assert blocked.details["count"] == 1
    off = client.run("item deactivate", {
        "item": created["id"], "expected_version": first["version"], "cascade": True,
    }, company=COMPANY)
    assert off["affected_ids"] == [created["id"], child["id"]]
    assert client.run("item list", {"query": "installation"}, company=COMPANY)["count"] == 0
    assert client.run("item list", {"query": "installation", "include_inactive": True}, company=COMPANY)["count"] == 2
    event = client.audit.list(command="item deactivate", company=COMPANY)["items"][0]
    assert event["entry_count"] == 2


def test_members_cycles_dependencies_and_type_transitions(client):
    refs = _refs(client)
    left = _service(client, refs, name="Left")
    right = _service(client, refs, name="Right")
    left = client.run("item update", {"item": left["id"], "type": "other_charge", "charge_percent": "1", "price": None}, company=COMPANY)
    assert left["type"] == "other_charge" and left["charge_percent"] == "1"
    _assert_error(client, "item update", {"item": left["id"], "type": "inventory_part"}, "E_TYPE_CHANGE")

    group_a = client.run("item create", {
        "name": "Group A", "type": "group", "print_members": True,
        "members": [{"component_item_id": right["id"], "quantity": "1"}],
    }, company=COMPANY)
    group_b = client.run("item create", {
        "name": "Group B", "type": "group", "print_members": False,
        "members": [{"component_item_id": group_a["id"], "quantity": "1"}],
    }, company=COMPANY)
    _assert_error(client, "item update", {
        "item": group_a["id"], "members": [{"component_item_id": group_b["id"], "quantity": "1"}],
    }, "E_HIERARCHY_CYCLE")
    used = _assert_error(client, "item deactivate", {"item": group_a["id"]}, "E_RECORD_IN_USE")
    assert used.details["dependents"] == [{"record_type": "item_member", "count": 1}]


def test_vendor_rank_one_projection_and_custom_values_reconcile_atomically(client):
    refs = _refs(client)
    vendor_one = client.run("vendor create", {"name": "Supply One"}, company=COMPANY)
    vendor_two = client.run("vendor create", {"name": "Supply Two"}, company=COMPANY)
    definition = client.run("custom-field create", {
        "name": "Bin", "kind": "text", "scopes": ["item"],
    }, company=COMPANY)
    item = client.run("item create", {
        "name": "Purchased labor", "type": "service", "sales_enabled": False, "purchase_enabled": True,
        "purchase_description": "Subcontracted labor", "cost": "50",
        "expense_account_id": refs["accounts"]["Professional Fees"],
        "vendor_profiles": [
            {"vendor_id": vendor_one["id"], "preferred_rank": 2, "purchase_cost": "49.25", "minimum_quantity": "1.5"},
            {"vendor_id": vendor_two["id"], "preferred_rank": 1},
        ],
        "custom_fields": {definition["id"]: "A-12"},
    }, company=COMPANY)
    assert item["preferred_vendor_id"] == vendor_two["id"]
    assert item["custom_fields"][0]["value"] == "A-12"
    changed = client.run("item update", {
        "item": item["id"], "expected_version": 1, "preferred_vendor_id": vendor_one["id"],
        "custom_fields": {definition["id"]: "B-08"},
    }, company=COMPANY)
    active_profiles = [row for row in changed["vendor_profiles"] if row["active"]]
    assert [(row["vendor_id"], row["preferred_rank"]) for row in active_profiles] == [
        (vendor_one["id"], 1), (vendor_two["id"], 2),
    ]
    assert changed["custom_fields"][0]["value"] == "B-08"
    assert "custom_fields." + definition["id"] in changed["changed_fields"]
    cleared_preference = client.run("item update", {
        "item": item["id"], "expected_version": 2, "preferred_vendor_id": None,
    }, company=COMPANY)
    assert cleared_preference["preferred_vendor_id"] is None
    assert all(row["preferred_rank"] != 1 for row in cleared_preference["vendor_profiles"] if row["active"])
    second_definition = client.run("custom-field create", {
        "name": "Shelf", "kind": "text", "scopes": ["item"],
    }, company=COMPANY)
    first_custom = client.run("item update", {
        "item": item["id"], "expected_version": 3,
        "custom_fields": {definition["id"]: "C-04"},
    }, company=COMPANY)
    merged = client.run("item update", {
        "item": item["id"], "expected_version": 3,
        "custom_fields": {second_definition["id"]: "Upper"},
    }, company=COMPANY)
    assert merged["version"] == 5 and merged["merged_over_versions"] == [4]
    assert first_custom["version"] == 4
    filtered = client.run("item list", {
        "filter": [f"custom_fields.{second_definition['id']}=Upper"],
    }, company=COMPANY)
    assert [row["id"] for row in filtered["items"]] == [item["id"]]
    assert {row["value"] for row in filtered["items"][0]["custom_fields"]} == {"C-04", "Upper"}
    assert {row["vendor_name"] for row in filtered["items"][0]["vendor_profiles"] if row["active"]} == {
        "Supply One", "Supply Two",
    }
    _assert_error(client, "item update", {
        "item": item["id"], "expected_version": 3,
        "custom_fields": {definition["id"]: "D-01"},
    }, "E_VERSION_CONFLICT")
    with open_database(_database(client), writable=False) as db:
        event_id = db.conn.execute(
            sa.select(schema.audit_events.c.id)
            .where(schema.audit_events.c.command == "item update")
            .order_by(schema.audit_events.c.seq)
            .limit(1)
        ).scalar_one()
        entries = db.conn.execute(sa.select(schema.audit_entries).where(schema.audit_entries.c.event_id == event_id)).mappings().all()
    assert len(entries) == 1 and entries[0]["record_type"] == "item"


def test_item_filter_sort_and_cross_company_selector_are_deterministic(client):
    refs = _refs(client)
    one = _service(client, refs, name="Alpha service")
    two = _service(client, refs, name="Beta service", price="200")
    listed = client.run("item list", {
        "query": "service", "filter": ["type=service", "sales_enabled=true"],
        "sort": "price", "direction": "desc",
    }, company=COMPANY)
    assert [row["id"] for row in listed["items"]][:2] == [two["id"], one["id"]]
    other = client.company.new(
        legal_name="Other Item Books LLC", home_currency="USD",
        organization="Demo Holdings LLC", timezone="UTC", chart="general",
    )["company_id"]
    with pytest.raises(BookflowError) as across:
        client.run("item show", {"item": one["id"]}, company=other)
    with pytest.raises(BookflowError) as missing:
        client.run("item show", {"item": "01ARZ3NDEKTSV4RRFFQ69G5FAA"}, company=other)
    assert across.value.to_dict() == missing.value.to_dict()


def test_assembly_cost_converts_member_units_to_the_purchase_unit(client):
    refs = _refs(client)
    client.company.update(units_of_measure_mode="multiple_related_units", company=COMPANY)
    unit_set = client.run("unit-of-measure create", {
        "name": "Count",
        "units": [
            {"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"},
            {"name": "Pack", "abbreviation": "pk", "is_base": False, "base_factor": "3"},
        ],
    }, company=COMPANY)
    each_id = unit_set["units"][0]["id"]
    pack_id = unit_set["units"][1]["id"]
    unit_set = client.run("unit-of-measure update", {
        "unit_of_measure": unit_set["id"], "expected_version": 1,
        "default_purchase_unit_id": each_id,
    }, company=COMPANY)
    account = refs["accounts"]
    component = client.run("item create", {
        "name": "Unit component", "type": "inventory_part", "description": "Component",
        "price": "15", "purchase_description": "Component purchase", "cost": "10",
        "income_account_id": account["Service Income"],
        "cogs_account_id": account["Cost of Goods Sold"],
        "asset_account_id": account["Inventory Asset"],
        "sales_tax_code_id": refs["tax_code"],
        "unit_of_measure_set_id": unit_set["id"],
    }, company=COMPANY)
    assembly = client.run("item create", {
        "name": "Unit assembly", "type": "inventory_assembly", "description": "Assembly",
        "price": "35", "purchase_description": "Assembly purchase", "cost": "30",
        "income_account_id": account["Service Income"],
        "cogs_account_id": account["Cost of Goods Sold"],
        "asset_account_id": account["Inventory Asset"],
        "sales_tax_code_id": refs["tax_code"],
        "members": [{"component_item_id": component["id"], "quantity": "1", "unit_id": pack_id}],
    }, company=COMPANY)
    assert assembly["bill_of_material_cost"] == {
        "amount": "30.00", "currency": "USD", "minor_units": 3000,
    }
    listed = client.run("item list", {"query": "Unit assembly"}, company=COMPANY)
    assert listed["items"][0]["bill_of_material_cost"] == assembly["bill_of_material_cost"]
    assert listed["items"][0]["members"][0]["unit_name"] == "Pack"


# The types whose sales and purchase profiles are neither fixed by the type nor
# both required: what a bookkeeper gets when they name a price and an income
# account and say nothing about buying.
_SALES_DEFAULT_TYPES = ("service", "non_inventory_part", "other_charge")


def _sales_side(refs, item_type, name):
    payload = {
        "name": name, "type": item_type, "description": "Billed to the customer",
        "price": "100.00", "income_account_id": refs["accounts"]["Service Income"],
    }
    if item_type == "service":
        payload["sales_tax_code_id"] = refs["tax_code"]
    return payload


@pytest.mark.parametrize("item_type", _SALES_DEFAULT_TYPES)
def test_sales_side_item_creates_without_naming_a_profile(client, item_type):
    refs = _refs(client)
    created = client.run("item create", _sales_side(refs, item_type, f"Default {item_type}"), company=COMPANY)
    assert (created["sales_enabled"], created["purchase_enabled"]) == (True, False)
    assert created["price"]["minor_units"] == 10000
    assert client.run("item show", {"item": created["id"]}, company=COMPANY)["sales_enabled"] is True


@pytest.mark.parametrize("item_type", _SALES_DEFAULT_TYPES)
def test_explicit_profile_flags_are_honoured_over_the_default(client, item_type):
    refs = _refs(client)
    account = refs["accounts"]
    purchase_only = client.run("item create", {
        "name": f"Bought {item_type}", "type": item_type, "sales_enabled": False,
        "purchase_enabled": True, "purchase_description": "Bought in",
        "cost": "60.00", "expense_account_id": account["Subcontractors"],
    }, company=COMPANY)
    assert (purchase_only["sales_enabled"], purchase_only["purchase_enabled"]) == (False, True)
    both = client.run("item create", {
        **_sales_side(refs, item_type, f"Both {item_type}"), "purchase_enabled": True,
        "purchase_description": "Bought in", "cost": "60.00",
        "expense_account_id": account["Subcontractors"],
    }, company=COMPANY)
    assert (both["sales_enabled"], both["purchase_enabled"]) == (True, True)
    # An explicit False is not quietly restored by the default, so an item with
    # no side at all is still refused by the unchanged capacity rule.
    error = _assert_error(client, "item create", {
        **_sales_side(refs, item_type, f"Idle {item_type}"), "sales_enabled": False,
    }, "E_VALIDATION")
    assert error.details["fields"] == [
        {"field": "sales_enabled", "problem": "at least one of sales or purchase must be enabled"}]
