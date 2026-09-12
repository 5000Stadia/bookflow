"""The packaged demo is a production-command witness for every Row 5 table."""

from pathlib import Path

import sqlalchemy as sa

from bookflow.company import schema
from bookflow.storage.engine import open_database


COMPANY = "Demo Plumbing Co"

PRIMARY_TABLES = (
    "accounts",
    "customers",
    "vendors",
    "employees",
    "other_names",
    "items",
    "item_categories",
    "classes",
    "terms",
    "payment_methods",
    "sales_tax_codes",
    "customer_types",
    "vendor_types",
    "job_types",
    "sales_reps",
    "ship_methods",
    "customer_messages",
    "price_levels",
    "units_of_measure",
    "custom_field_defs",
)

CHILD_TABLES = (
    "customer_addresses",
    "customer_contacts",
    "customer_contact_points",
    "vendor_contacts",
    "vendor_contact_points",
    "vendor_expense_accounts",
    "customer_vendor_links",
    "item_members",
    "item_vendor_profiles",
    "price_level_items",
    "unit_conversions",
    "custom_field_scopes",
    "custom_field_choices",
    "custom_field_values",
)

ITEM_TYPES = {
    "service",
    "inventory_part",
    "non_inventory_part",
    "other_charge",
    "subtotal",
    "group",
    "discount",
    "payment",
    "sales_tax_item",
    "sales_tax_group",
    "inventory_assembly",
    "fixed_asset",
}


def _database(client) -> Path:
    return Path(client.company.show(company=COMPANY)["path"]) / "company.db"


def test_demo_covers_every_row5_table_item_type_and_lifecycle_state(client):
    with open_database(_database(client), writable=False) as db:
        for table_name in (*PRIMARY_TABLES, *CHILD_TABLES):
            table = getattr(schema, table_name)
            states = set(db.conn.execute(sa.select(table.c.active).distinct()).scalars())
            assert states == {False, True}, table_name

        item_types = set(db.conn.execute(sa.select(schema.items.c.type)).scalars())
        assert ITEM_TYPES <= item_types

        commands = set(db.conn.execute(sa.select(schema.audit_events.c.command)).scalars())
        assert {
            "account create",
            "customer create",
            "vendor create",
            "employee create",
            "other-name create",
            "item create",
            "customer link-vendor",
            "customer unlink-vendor",
            "price-level create",
            "unit-of-measure create",
            "custom-field create",
        } <= commands


def test_demo_public_records_exercise_hierarchy_aggregates_and_custom_kinds(client):
    parent = client.customer.show(customer="Riverside Apartments", company=COMPANY)
    inherited = client.customer.show(
        customer="Riverside Apartments:Building A", company=COMPANY,
    )
    owned = client.customer.show(
        customer="Riverside Apartments:Building A:Unit 301", company=COMPANY,
    )
    vendor = client.vendor.show(vendor="Central Supply", company=COMPANY)
    candidate = client.run(
        "other-name show", {"other_name": "Alex Community"}, company=COMPANY,
    )

    assert (parent["depth"], inherited["depth"], owned["depth"]) == (1, 2, 3)
    assert inherited["address_mode"] == inherited["contact_mode"] == "inherit"
    assert inherited["billing_address_source_id"] == parent["id"]
    assert owned["address_mode"] == owned["contact_mode"] == "own"
    assert owned["billing_address_source_id"] == owned["id"]
    assert parent["linked_vendor_id"] == vendor["id"]
    assert vendor["linked_customer_id"] == parent["id"]
    assert candidate["converted"] is False and candidate["conversion_target"] is None

    assert {field["kind"] for field in parent["custom_fields"]} == {
        "text", "number", "date", "bool", "choice",
    }
    assert {field["kind"] for field in vendor["custom_fields"]} == {
        "text", "number", "date", "bool", "choice",
    }

    units = client.run(
        "unit-of-measure show", {"unit_of_measure": "Inventory Count"}, company=COMPANY,
    )
    assert units["base_unit"]["name"] == "Each"
    assert units["default_purchase_unit"]["name"] == "Box"
    assert {unit["active"] for unit in units["units"]} == {False, True}

    price = client.run(
        "price-level show", {"price_level": "Contract Pricing"}, company=COMPANY,
    )
    assert price["kind"] == "per_item" and price["rounding_increment"]["minor_units"] == 5
    assert {item["active"] for item in price["items"]} == {False, True}


def test_demo_reset_recreates_the_complete_seed(client):
    before = {
        noun: client.run(f"{noun} list", {"include_inactive": True}, company=COMPANY)["count"]
        for noun in ("customer", "vendor", "employee", "other-name", "item")
    }
    reset = client.demo.reset()
    after = {
        noun: client.run(f"{noun} list", {"include_inactive": True}, company=COMPANY)["count"]
        for noun in ("customer", "vendor", "employee", "other-name", "item")
    }
    assert reset["trashed_path"] is not None
    assert before == after


def test_the_demo_banks_its_takings_so_the_deposit_window_opens_on_something(client):
    """Deposit is the one noun with a home-window tile, and the demo used to bank nothing.

    Every other seeded receipt goes straight to Checking, which is not what happens when the
    money is in the till: it sits in Undeposited Funds until somebody takes it to the bank. So
    a person opening the demo and following the Make deposit tile met an empty list, and the
    one command with a tile of its own was the one the demo never exercised.
    """
    deposits = client.run("deposit query", {}, company=COMPANY)["items"]
    assert deposits, "the demo banks nothing, so the deposit window opens on an empty list"
    banked = deposits[0]
    assert banked["totals"]["bank_total"]["minor_units"] > 0
    assert banked["current"]["status"] == "posted"
    assert banked["current"]["active_source_count"] >= 2, (
        "one trip to the bank with more than one receipt in it")
