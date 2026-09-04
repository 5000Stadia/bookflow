"""Exact price-level and unit-of-measure aggregates and lifecycle commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.commands import unit_pricing_cmds as _registered_commands  # noqa: F401
from bookflow.company import pricing, schema, units
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database


def _company_db(client) -> Path:
    return Path(client.company.show(company="Demo Plumbing Co")["path"]) / "company.db"


def _common(actor: str, *, active: bool = True) -> dict[str, Any]:
    at = "2026-09-04T08:00:00.000Z"
    return {
        "id": new_id(),
        "version": 1,
        "created_at": at,
        "created_by": actor,
        "created_via": "python",
        "updated_at": at,
        "updated_by": actor,
        "updated_via": "python",
        "active": active,
        "seed_key": None,
    }


def _insert_item(client, name: str, *, active: bool = True, unit_set_id: str | None = None) -> str:
    shown = client.company.show(company="Demo Plumbing Co")
    actor = shown["info"]["created_by"]
    record = _common(actor, active=active)
    item_id = record["id"]
    with open_database(_company_db(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(
            schema.items.insert().values(
                **record,
                name=name,
                name_key=name.casefold(),
                parent_id=None,
                full_name=name,
                full_name_key=name.casefold(),
                depth=1,
                path=f"/{item_id}/",
                type="service",
                sales_enabled=False,
                purchase_enabled=False,
                unit_of_measure_set_id=unit_set_id,
            )
        )
        db.raw.execute("COMMIT")
    return item_id


def _has_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_has_float(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_float(child) for child in value)
    return False


def test_fixed_price_level_lifecycle_is_exact_versioned_and_idempotent(client):
    # Price-level definitions remain manageable while later-form selection is disabled.
    assert client.company.show(company="Demo Plumbing Co")["info"]["enable_price_levels"] is False
    created = client.run(
        "price-level create",
        {
            "name": "Preferred",
            "kind": "fixed_percent",
            "percent": "-12.500000",
        },
        company="Demo Plumbing Co",
        idempotency_key="preferred-v1",
    )
    assert created["percent"] == "-12.5"
    assert created["resolved_currency"] == "USD"
    assert created["rounding_increment"] == {
        "amount": "0.01", "currency": "USD", "minor_units": 1,
    }
    assert created["rounding_offset"]["minor_units"] == 0
    assert not _has_float(created)

    replay = client.run(
        "price-level create",
        {
            "name": "Preferred",
            "kind": "fixed_percent",
            "percent": "-12.500000",
        },
        company="Demo Plumbing Co",
        idempotency_key="preferred-v1",
    )
    assert replay["id"] == created["id"]
    assert replay["idempotent_replay"] is True

    updated = client.run(
        "price-level update",
        {
            "price_level": created["id"],
            "expected_version": 1,
            "percent": "5.125",
            "rounding_mode": "up",
        },
        company="Demo Plumbing Co",
    )
    assert updated["version"] == 2
    assert updated["percent"] == "5.125"
    assert updated["changed_fields"] == ["percent", "rounding_mode"]

    with pytest.raises(BookflowError) as exc:
        client.run(
            "price-level update",
            {"price_level": created["id"], "expected_version": 1, "percent": "9"},
            company="Demo Plumbing Co",
        )
    assert exc.value.code == "E_VERSION_CONFLICT"

    off = client.run(
        "price-level deactivate",
        {"price_level": created["id"], "expected_version": 2},
        company="Demo Plumbing Co",
    )
    assert off["changed"] is True and off["version"] == 3
    active_ids = {
        item["id"]
        for item in client.run("price-level list", {}, company="Demo Plumbing Co")["items"]
    }
    assert created["id"] not in active_ids
    all_ids = {
        item["id"]
        for item in client.run(
            "price-level list", {"include_inactive": True}, company="Demo Plumbing Co"
        )["items"]
    }
    assert created["id"] in all_ids
    on = client.run(
        "price-level activate",
        {"price_level": created["id"], "expected_version": 3},
        company="Demo Plumbing Co",
    )
    assert on["changed"] is True and on["version"] == 4


def test_price_discriminator_money_and_ordered_child_reconciliation(client):
    first = _insert_item(client, "Drain cleaning")
    second = _insert_item(client, "Pipe fitting")
    inactive = _insert_item(client, "Retired service", active=False)

    with pytest.raises(BookflowError) as exc:
        client.run(
            "price-level create",
            {"name": "Mixed", "kind": "fixed_percent", "percent": "1", "items": []},
            company="Demo Plumbing Co",
        )
    assert exc.value.code == "E_VALIDATION"
    with pytest.raises(BookflowError) as exc:
        client.run(
            "price-level create",
            {"name": "Out of range", "kind": "fixed_percent", "percent": "1000000.000001"},
            company="Demo Plumbing Co",
        )
    assert exc.value.code == "E_VALUE_RANGE"
    with pytest.raises(BookflowError) as exc:
        client.run(
            "price-level create",
            {"name": "Float", "kind": "fixed_percent", "percent": 1.5},
            company="Demo Plumbing Co",
        )
    assert exc.value.code == "E_VALIDATION"
    with pytest.raises(BookflowError) as exc:
        client.run(
            "price-level create",
            {
                "name": "Wrong currency",
                "kind": "fixed_percent",
                "percent": "1",
                "rounding_increment": "0.01 EUR",
            },
            company="Demo Plumbing Co",
        )
    assert exc.value.code == "E_VALIDATION"

    made = client.run(
        "price-level create",
        {
            "name": "Job prices",
            "kind": "per_item",
            "currency": "USD",
            "items": [
                {"item_id": first, "price": "125.50", "adjustment_basis": "standard_price"},
                {"item_id": second, "percent": "10.25", "adjustment_basis": "cost"},
            ],
        },
        company="Demo Plumbing Co",
    )
    assert made["item_count"] == 2
    assert [item["position"] for item in made["items"]] == [0, 1]
    assert made["items"][0]["price"]["minor_units"] == 12_550
    first_child, second_child = made["items"]

    changed = client.run(
        "price-level update",
        {
            "price_level": made["id"],
            "expected_version": 1,
            "items": [
                {
                    "id": second_child["id"],
                    "item_id": second,
                    "percent": "11",
                    "adjustment_basis": "current_custom_price",
                }
            ],
        },
        company="Demo Plumbing Co",
    )
    assert changed["version"] == 2 and changed["changed_fields"] == ["items"]
    assert changed["item_count"] == 1
    assert changed["items"][0]["id"] == second_child["id"]
    retired = next(item for item in changed["items"] if item["id"] == first_child["id"])
    assert retired["active"] is False

    with pytest.raises(BookflowError) as exc:
        client.run(
            "price-level update",
            {
                "price_level": made["id"],
                "expected_version": 2,
                "items": [{"item_id": inactive, "price": "2.00", "adjustment_basis": "cost"}],
            },
            company="Demo Plumbing Co",
        )
    assert exc.value.code == "E_INACTIVE_REFERENCE"

    found = client.run(
        "price-level list",
        {"query": "Pipe", "filter": ["currency=USD"], "sort": "item_count"},
        company="Demo Plumbing Co",
    )
    assert found["count"] == 1 and found["items"][0]["id"] == made["id"]

    with open_database(_company_db(client), writable=False) as db:
        events = db.conn.execute(
            sa.select(schema.audit_events.c.id)
            .join(
                schema.audit_entries,
                schema.audit_entries.c.event_id == schema.audit_events.c.id,
            )
            .where(
                schema.audit_events.c.command == "price-level update",
                schema.audit_entries.c.record_type == "price_level",
                schema.audit_entries.c.record_id == made["id"],
            )
        ).all()
        entries = db.conn.execute(
            sa.select(schema.audit_entries).where(
                schema.audit_entries.c.event_id == events[0][0]
            )
        ).mappings().all()
    assert len(events) == 1 and len(entries) == 1
    assert entries[0]["record_type"] == "price_level"


def test_unit_modes_same_set_defaults_conversion_and_dependency_block(client):
    payload = {
        "name": "Count",
        "units": [{"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"}],
    }
    # Definitions remain manageable while item assignment/use is disabled.
    count = client.run(
        "unit-of-measure create",
        payload,
        company="Demo Plumbing Co",
        idempotency_key="count-set",
    )
    each_id = count["base_unit"]["id"]
    with open_database(_company_db(client), writable=False) as db:
        with pytest.raises(BookflowError) as exc:
            units.convert_quantity(db, count["id"], each_id, each_id, "1")
    assert exc.value.code == "E_FEATURE_DISABLED"

    client.company.update(
        units_of_measure_mode="single_unit_per_item", company="Demo Plumbing Co"
    )
    assert count["base_unit"]["base_factor"] == "1"
    assert count["related_unit_count"] == 1
    with open_database(_company_db(client), writable=False) as db:
        units.validate_unit_assignment(db, count["id"])

    client.company.update(
        units_of_measure_mode="multiple_related_units", company="Demo Plumbing Co"
    )
    expanded = client.run(
        "unit-of-measure update",
        {
            "unit_of_measure": count["id"],
            "expected_version": 1,
            "units": [
                {"id": each_id, "name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"},
                {"name": "Pack", "abbreviation": "pk", "is_base": False, "base_factor": "3"},
            ],
        },
        company="Demo Plumbing Co",
    )
    pack_id = next(unit["id"] for unit in expanded["units"] if unit["active"] and not unit["is_base"])
    defaulted = client.run(
        "unit-of-measure update",
        {
            "unit_of_measure": count["id"],
            "expected_version": 2,
            "default_purchase_unit_id": pack_id,
            "default_sales_unit_id": each_id,
            "default_shipping_unit_id": pack_id,
        },
        company="Demo Plumbing Co",
    )
    assert defaulted["default_purchase_unit"]["abbreviation"] == "pk"
    assert defaulted["default_sales_unit"]["abbreviation"] == "ea"

    # A mode change preserves the related set, but single-unit assignment and
    # conversion cannot consume that multi-unit definition.
    client.company.update(
        units_of_measure_mode="single_unit_per_item", company="Demo Plumbing Co"
    )
    preserved = client.run(
        "unit-of-measure show", {"unit_of_measure": count["id"]}, company="Demo Plumbing Co"
    )
    assert preserved["related_unit_count"] == 2
    with open_database(_company_db(client), writable=False) as db:
        with pytest.raises(BookflowError) as exc:
            units.validate_unit_assignment(db, count["id"])
        assert exc.value.code == "E_VALIDATION"
        with pytest.raises(BookflowError) as exc:
            units.convert_quantity(db, count["id"], each_id, pack_id, "1")
        assert exc.value.code == "E_FEATURE_DISABLED"
    client.company.update(
        units_of_measure_mode="multiple_related_units", company="Demo Plumbing Co"
    )

    with open_database(_company_db(client), writable=False) as db:
        converted = units.convert_quantity(db, count["id"], each_id, pack_id, "1")
    assert converted.target_quantity == "0.333333"
    assert converted.source_quantity == "1"

    other = client.run(
        "unit-of-measure create",
        {"name": "Length", "units": [{"name": "Foot", "abbreviation": "ft", "is_base": True, "base_factor": "1"}]},
        company="Demo Plumbing Co",
    )
    with pytest.raises(BookflowError) as exc:
        client.run(
            "unit-of-measure update",
            {
                "unit_of_measure": count["id"],
                "expected_version": 3,
                "default_sales_unit_id": other["base_unit"]["id"],
            },
            company="Demo Plumbing Co",
        )
    assert exc.value.code == "E_VALIDATION"

    _insert_item(client, "Measured labor", unit_set_id=count["id"])
    with pytest.raises(BookflowError) as exc:
        client.run(
            "unit-of-measure deactivate",
            {"unit_of_measure": count["id"], "expected_version": 3},
            company="Demo Plumbing Co",
        )
    assert exc.value.code == "E_RECORD_IN_USE"

    listed = client.run(
        "unit-of-measure list",
        {"query": "pk", "filter": ["base_unit=true"], "sort": "related_unit_count", "direction": "desc"},
        company="Demo Plumbing Co",
    )
    assert listed["count"] == 1 and listed["items"][0]["id"] == count["id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "No base", "units": [{"name": "Each", "abbreviation": "ea", "is_base": False, "base_factor": "1"}]},
        {"name": "Bad base", "units": [{"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1.000000001"}]},
        {"name": "Float factor", "units": [{"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": 1.0}]},
        {"name": "Duplicate", "units": [
            {"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"},
            {"name": " each ", "abbreviation": "box", "is_base": False, "base_factor": "2"},
        ]},
    ],
)
def test_unit_collection_rejects_invalid_exact_or_normalized_profiles(client, payload):
    client.company.update(
        units_of_measure_mode="multiple_related_units", company="Demo Plumbing Co"
    )
    with pytest.raises(BookflowError) as exc:
        client.run("unit-of-measure create", payload, company="Demo Plumbing Co")
    assert exc.value.code == "E_VALIDATION"


def test_nonpositive_unit_factor_uses_the_range_error(client):
    with pytest.raises(BookflowError) as exc:
        client.run(
            "unit-of-measure create",
            {
                "name": "Broken",
                "units": [
                    {"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "0"}
                ],
            },
            company="Demo Plumbing Co",
        )
    assert exc.value.code == "E_VALUE_RANGE"
