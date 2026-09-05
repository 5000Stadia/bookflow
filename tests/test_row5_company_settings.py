"""Row 5 company settings are typed, persisted, and cross-field validated."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import schema
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database


DEFAULT_REQUIREMENTS = [
    ["first_name"], ["last_name"], ["address.line1"], ["address.city"],
    ["address.state"], ["address.postal_code"], ["phone", "email"],
]


def _company_db(client) -> Path:
    return Path(client.company.show(company="Demo Plumbing Co")["path"]) / "company.db"


def _common(actor: str) -> dict:
    return {
        "id": new_id(), "version": 1, "created_at": "2026-09-04T00:00:00.000+00:00",
        "created_by": actor, "created_via": "system", "updated_at": "2026-09-04T00:00:00.000+00:00",
        "updated_by": actor, "updated_via": "system", "active": True, "seed_key": None,
    }


def _insert_reference_fixture(client) -> tuple[str, str, str, str]:
    shown = client.company.show(company="Demo Plumbing Co")
    actor = shown["info"]["created_by"]
    ship_id = new_id()
    unit_id = new_id()
    item_id = new_id()
    tax_item_id = new_id()
    with open_database(_company_db(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(schema.ship_methods.insert().values(
            **{**_common(actor), "id": ship_id}, name="Delivery Van", name_key="delivery van", display_order=0,
        ))
        db.conn.execute(schema.units_of_measure.insert().values(
            **{**_common(actor), "id": unit_id}, name="Each", name_key="each",
            default_purchase_unit_id=None, default_sales_unit_id=None, default_shipping_unit_id=None,
        ))
        for current_id, name, item_type, unit_set in (
            (item_id, "Measured service", "service", unit_id),
            (tax_item_id, "Local tax", "sales_tax_item", None),
        ):
            db.conn.execute(schema.items.insert().values(
                **{**_common(actor), "id": current_id}, name=name, name_key=name.casefold(),
                parent_id=None, full_name=name, full_name_key=name.casefold(), depth=1,
                path=f"/{current_id}/", type=item_type, sales_enabled=False, purchase_enabled=False,
                unit_of_measure_set_id=unit_set,
            ))
        db.raw.execute("COMMIT")
    return ship_id, unit_id, item_id, tax_item_id


def test_row5_defaults_are_visible_as_typed_company_info(client):
    client.organization.new(name="Default Settings Org")
    made = client.company.new(
        legal_name="Default Settings Co", home_currency="USD",
        organization="Default Settings Org", timezone="UTC",
    )
    info = client.company.show(company=made["company_id"])["info"]
    assert info["use_account_numbers"] is True
    assert info["show_lowest_subaccount_only"] is False
    assert info["required_employee_profile_fields"] == DEFAULT_REQUIREMENTS
    assert info["use_classes"] is False and info["prompt_for_class"] is False
    assert info["enable_price_levels"] is False
    assert info["units_of_measure_mode"] == "disabled"
    assert info["sales_tax_enabled"] is False and info["default_sales_tax_item_id"] is None
    assert info["sales_tax_liability_basis"] == "invoice_date"
    assert info["sales_tax_remittance_frequency"] == "quarterly"
    assert info["default_ship_method_id"] is None and info["free_on_board"] is None
    assert info["order_printable_checks"] is False


def test_company_update_round_trips_all_nonreference_settings(client):
    # The sales demo enables tax; start from rollout settings so enabling it
    # remains a real changed field in the update receipt.
    client.company.update(
        sales_tax_enabled=False, default_sales_tax_item_id=None, company="Demo Plumbing Co",
    )
    out = client.company.update(
        use_account_numbers=False,
        show_lowest_subaccount_only=True,
        required_employee_profile_fields=[["name"], ["phone", "email"]],
        use_classes=True,
        prompt_for_class=True,
        enable_price_levels=True,
        units_of_measure_mode="multiple_related_units",
        sales_tax_enabled=True,
        sales_tax_liability_basis="payment_receipt",
        sales_tax_remittance_frequency="monthly",
        free_on_board="Origin",
        order_printable_checks=True,
        company="Demo Plumbing Co",
    )
    assert set(out["changed_fields"]) >= {
        "use_account_numbers", "show_lowest_subaccount_only", "required_employee_profile_fields",
        "use_classes", "prompt_for_class", "enable_price_levels", "units_of_measure_mode",
        "sales_tax_enabled", "sales_tax_liability_basis", "sales_tax_remittance_frequency",
        "free_on_board", "order_printable_checks",
    }
    info = client.company.show(company="Demo Plumbing Co")["info"]
    assert info["required_employee_profile_fields"] == [["name"], ["phone", "email"]]
    assert info["sales_tax_enabled"] is True
    assert info["sales_tax_liability_basis"] == "payment_receipt"
    assert info["free_on_board"] == "Origin"


def test_company_setting_cross_field_and_reference_rules(client):
    with pytest.raises(BookflowError) as exc:
        client.company.update(prompt_for_class=True, company="Demo Plumbing Co")
    assert exc.value.code == "E_VALIDATION"

    ship_id, _unit_id, item_id, tax_item_id = _insert_reference_fixture(client)
    client.company.update(default_ship_method_id=ship_id, company="Demo Plumbing Co")
    client.company.update(
        sales_tax_enabled=True, default_sales_tax_item_id=tax_item_id, company="Demo Plumbing Co",
    )
    with pytest.raises(BookflowError) as exc:
        client.company.update(sales_tax_enabled=False, company="Demo Plumbing Co")
    assert exc.value.code == "E_VALIDATION"
    client.company.update(
        sales_tax_enabled=False, default_sales_tax_item_id=None, company="Demo Plumbing Co",
    )

    client.company.update(units_of_measure_mode="single_unit_per_item", company="Demo Plumbing Co")
    with pytest.raises(BookflowError) as exc:
        client.company.update(units_of_measure_mode="disabled", company="Demo Plumbing Co")
    assert exc.value.code == "E_ACTIVE_DEPENDENTS"
    assert exc.value.details["records"] == [{"id": item_id, "full_name": "Measured service"}]

    with open_database(_company_db(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(schema.ship_methods.update().where(schema.ship_methods.c.id == ship_id).values(active=False))
        db.raw.execute("COMMIT")
    client.company.update(default_ship_method_id=None, company="Demo Plumbing Co")
    with pytest.raises(BookflowError) as exc:
        client.company.update(default_ship_method_id=ship_id, company="Demo Plumbing Co")
    assert exc.value.code == "E_INACTIVE_REFERENCE"
    with pytest.raises(BookflowError) as exc:
        client.company.update(default_ship_method_id=new_id(), company="Demo Plumbing Co")
    assert exc.value.code == "E_RECORD_NOT_FOUND"


def test_employee_requirement_paths_use_registered_stable_custom_ids(client):
    for bad in ([[]], [["address.bogus"]], [["custom_fields.not-an-id"]], [["phone", "phone"]]):
        with pytest.raises(BookflowError) as exc:
            client.company.update(required_employee_profile_fields=bad, company="Demo Plumbing Co")
        assert exc.value.code == "E_VALIDATION"

    shown = client.company.show(company="Demo Plumbing Co")
    actor = shown["info"]["created_by"]
    definition_id = new_id()
    with open_database(_company_db(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(schema.custom_field_defs.insert().values(
            **{**_common(actor), "id": definition_id}, name="Crew code", name_key="crew code",
            kind="text", position=0, required=False, default_canonical_text=None,
        ))
        db.conn.execute(schema.custom_field_scopes.insert().values(
            id=new_id(), definition_id=definition_id, position=0, active=True, record_type="employee",
            definition_name="Crew code", definition_name_key="crew code", definition_active=True,
        ))
        db.raw.execute("COMMIT")
    client.company.update(
        required_employee_profile_fields=[[f"custom_fields.{definition_id.lower()}", "phone"]],
        company="Demo Plumbing Co",
    )
    assert client.company.show(company="Demo Plumbing Co")["info"]["required_employee_profile_fields"] == [
        [f"custom_fields.{definition_id}", "phone"],
    ]


def test_new_company_persists_nonreference_row5_settings(client):
    client.organization.new(name="Settings Org")
    made = client.company.new(
        legal_name="Settings Co", home_currency="USD", organization="Settings Org", timezone="UTC",
        use_account_numbers=False, use_classes=True, prompt_for_class=True,
        required_employee_profile_fields=[["name"]], sales_tax_enabled=True,
        sales_tax_remittance_frequency="annually", free_on_board="Destination",
    )
    info = client.company.show(company=made["company_id"])["info"]
    assert info["use_account_numbers"] is False
    assert info["use_classes"] is True and info["prompt_for_class"] is True
    assert info["required_employee_profile_fields"] == [["name"]]
    assert info["sales_tax_enabled"] is True
    assert info["sales_tax_remittance_frequency"] == "annually"
    assert info["free_on_board"] == "Destination"
