"""The Row 5 internal blueprint-to-witness inventory gates its storage contract."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
INVENTORY_PATH = ROOT / "design" / "specs" / "5-lists-inventory.json"

NOUNS = {
    "account", "customer", "vendor", "employee", "other-name", "item", "item-category", "class",
    "term", "payment-method", "sales-tax-code", "customer-type", "vendor-type", "job-type",
    "sales-rep", "ship-method", "customer-message", "price-level", "unit-of-measure", "custom-field",
}
SPECIAL_COMMANDS = {
    "chart.list", "chart.show", "chart.apply", "profile.list", "profile.show", "profile.apply",
    "customer.link-vendor", "customer.unlink-vendor", "other-name.convert", "undo",
}
COMPANY_ACTIONS = {
    "company.show.row5-settings", "company.update.row5-settings", "company.new.row5-rollout", "demo.reset.row5",
}
COMPANY_FIELDS = {
    "use_account_numbers", "show_lowest_subaccount_only", "required_employee_profile_fields", "use_classes",
    "prompt_for_class", "enable_price_levels", "units_of_measure_mode", "sales_tax_enabled",
    "default_sales_tax_item_id", "sales_tax_liability_basis", "sales_tax_remittance_frequency",
    "default_ship_method_id", "free_on_board", "order_printable_checks", "default_chart",
    "default_chart_version",
}
WITNESS_DIMENSIONS = {"blueprint", "schema", "model", "service", "output", "docs", "form", "test"}


def inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text())


def _witness_keys(value):
    if isinstance(value, dict):
        if isinstance(value.get("w"), str):
            yield value["w"]
        for child in value.values():
            yield from _witness_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _witness_keys(child)


def _names(entries: list[dict]) -> list[str]:
    return [entry["n"] for entry in entries]


def test_inventory_has_every_required_noun_action_and_witness_dimension():
    data = inventory()
    assertions = data["coverage_assertions"]

    assert data["schema_version"] == 1 and data["row"] == 5
    assert set(data["nouns"]) == NOUNS == set(assertions["required_nouns"])
    assert {entry["id"] for entry in data["special_commands"]} == SPECIAL_COMMANDS == set(assertions["required_special_ids"])
    assert {entry["id"] for entry in data["company_settings"]["actions"]} == COMPANY_ACTIONS == set(assertions["required_company_action_ids"])
    assert set(assertions["required_witness_dimensions"]) == WITNESS_DIMENSIONS

    templates = data["witness_templates"]
    checked = {key: value for key, value in data.items() if key not in {"notation", "witness_templates"}}
    assert set(_witness_keys(checked)) <= set(templates)
    assert all(set(template) == WITNESS_DIMENSIONS for template in templates.values())


def test_each_noun_has_unique_fields_complete_lifecycle_and_query_contract():
    data = inventory()
    ordinary = data["coverage_assertions"]["ordinary_verbs_per_noun"]

    assert ordinary == ["create", "update", "show", "list", "activate", "deactivate"]
    for noun, definition in data["nouns"].items():
        names = _names(definition["fields"])
        assert names and len(names) == len(set(names)), noun
        assert definition["actions"] and "|".join(ordinary) in definition["actions"][0]["command"], noun
        assert definition["query"]["search"] and definition["query"]["sorts"], noun
        assert definition["query"]["default_columns"], noun
        assert definition["bp"].startswith("design/blueprint.md#"), noun
        assert definition["table"] and definition["service"] and definition["selector"], noun


def test_discriminated_profiles_company_settings_and_navigation_are_closed_sets():
    data = inventory()
    item = data["nouns"]["item"]
    item_types = next(field["enum"] for field in item["fields"] if field["n"] == "type")
    assert set(item_types) == set(item["type_profiles"])
    assert len(item_types) == 12

    assert set(_names(data["company_settings"]["fields"])) == COMPANY_FIELDS
    groups = data["browser_and_documentation"]["ui_group_assignments"]
    flattened = [noun for members in groups.values() for noun in members]
    assert len(flattened) == len(set(flattened))
    assert NOUNS <= set(flattened)
    assert {"company", "chart", "profile", "audit", "undo"} <= set(flattened)


def test_deferred_references_and_exact_manifests_are_resolvable():
    data = inventory()
    deferred_ids = {entry["id"] for entry in data["deferred_entries"]}
    referenced = {
        deferred
        for noun in data["nouns"].values()
        for deferred in noun.get("deferred", [])
    }
    referenced.update(data["browser_and_documentation"]["deferred"])
    assert referenced <= deferred_ids

    charts = data["chart_manifests"]
    assert set(charts["template_ids"]) == set(charts["template_accounts"])
    assert len(charts["required_system_roles"]) == 9
    assert {row[4] for row in charts["common_accounts"] if row[4]} == set(charts["required_system_roles"])

    profiles = data["profile_manifests"]
    assert profiles["manifest_ids"] == ["standard"]
    assert set(profiles["lists"]) == set(profiles["records"])
    seed_keys = [record["seed_key"] for records in profiles["records"].values() for record in records]
    assert len(seed_keys) == len(set(seed_keys))


def test_migration_inventory_names_the_historical_convergence_witness():
    rules = {entry["id"]: entry["rule"] for entry in inventory()["migration"]["rules"]}
    assert set(rules) == {
        "migration.freeze-co0002", "migration.undo-link", "migration.chart-identity",
        "migration.convergence", "migration.failure",
    }
    assert "revision-local DDL" in rules["migration.freeze-co0002"]
    assert "identical" in rules["migration.convergence"]
