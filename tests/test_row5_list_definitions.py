"""Authoritative Row 5 list metadata and dependency-light query helpers."""

from __future__ import annotations

import pytest

from bookflow.company.lists import (
    LIST_DEFINITIONS,
    MAX_DISPLAY_KEY,
    MAX_DISPLAY_NAME,
    SortTerm,
    all_list_definitions,
    hierarchy_projection,
    normalize_display_name,
    validate_list_definitions,
)
from bookflow.core.errors import BookflowError
from bookflow.core import registry


EXPECTED_ORDER = (
    "customer", "customer-type", "job-type", "sales-rep", "price-level", "ship-method",
    "customer-message", "vendor", "vendor-type", "employee", "other-name", "item",
    "item-category", "unit-of-measure", "account", "class", "term", "payment-method",
    "sales-tax-code", "custom-field",
)


def _error_code(call, *args, **kwargs) -> str:
    with pytest.raises(BookflowError) as exc:
        call(*args, **kwargs)
    return exc.value.code


def test_definitions_validate_and_have_stable_navigation_order():
    validate_list_definitions()
    assert set(LIST_DEFINITIONS) == set(EXPECTED_ORDER)
    assert tuple(item.noun for item in all_list_definitions()) == EXPECTED_ORDER

    for definition in LIST_DEFINITIONS.values():
        assert definition.route_slug == definition.noun
        assert definition.primary_collection_action == f"{definition.noun} create"
        assert definition.collection_actions == (f"{definition.noun} create", f"{definition.noun} list")
        assert definition.record_actions == (
            f"{definition.noun} show", f"{definition.noun} update",
            f"{definition.noun} activate", f"{definition.noun} deactivate",
        )
        assert definition.all_columns[:len(definition.default_columns)] == definition.default_columns
        assert len(definition.all_columns) == len(set(definition.all_columns))


def test_names_are_nfc_casefolded_and_use_validation_for_declared_bounds():
    assert normalize_display_name("  CAFE\N{COMBINING ACUTE ACCENT}  ") == ("CAFÉ", "café")
    for value in ("", "A:B", 3, "x" * (MAX_DISPLAY_NAME + 1), "İ" * (MAX_DISPLAY_KEY + 1)):
        assert _error_code(normalize_display_name, value) == "E_VALIDATION"


def test_hierarchy_projection_enforces_depth_and_full_name_bounds():
    assert hierarchy_projection("Child", "Parent", depth=2) == ("Parent:Child", "parent:child")
    assert _error_code(hierarchy_projection, "Child", "Parent", depth=6) == "E_HIERARCHY_DEPTH"
    assert _error_code(hierarchy_projection, "Child", "Parent", depth=True) == "E_HIERARCHY_DEPTH"
    assert _error_code(hierarchy_projection, "Child", "x" * 1004, depth=2) == "E_VALIDATION"


def test_filters_are_typed_strict_and_repeatable():
    definition = LIST_DEFINITIONS["account"]
    assert definition.parse_filters(("active=true", "parent_id=01JTEST", "tax_line=1040-C")) == (
        definition.parse_filters(("active=true",))[0],
        definition.parse_filters(("parent_id=01JTEST",))[0],
        definition.parse_filters(("tax_line=1040-C",))[0],
    )
    assert definition.parse_filters(("active=false",))[0].value is False
    for bad in ("active=yes", "unknown=value", "not-an-assignment", "tax_line="):
        assert _error_code(definition.parse_filters, (bad,)) == "E_LIST_FILTER"


def test_sorting_is_declared_and_always_stable():
    account = LIST_DEFINITIONS["account"]
    assert account.resolve_sort(None) == (
        SortTerm("number", "asc", True), SortTerm("full_name"), SortTerm("id"),
    )
    assert account.resolve_sort("updated_at", "desc") == (SortTerm("updated_at", "desc"), SortTerm("id"))
    assert _error_code(account.resolve_sort, "bogus") == "E_LIST_FILTER"
    assert _error_code(account.resolve_sort, "number", "sideways") == "E_LIST_FILTER"


def test_reference_descriptors_resolve_to_known_nouns():
    for definition in LIST_DEFINITIONS.values():
        for reference in definition.references:
            assert set(reference.target_nouns) <= set(LIST_DEFINITIONS)
    sales_rep = LIST_DEFINITIONS["sales-rep"]
    assert sales_rep.references[0].field == "name_id"
    assert sales_rep.references[0].target_nouns == ("employee", "vendor", "other-name")
    customer_fields = {reference.field for reference in LIST_DEFINITIONS["customer"].references}
    assert {
        "preferred_ship_method_id",
        "sales_tax_item_id",
        "job_sales_rep_id",
    } <= customer_fields
    assert "default_ship_method_id" not in customer_fields
    item_fields = {reference.field for reference in LIST_DEFINITIONS["item"].references}
    assert {"payment_method_id", "tax_agency_vendor_id", "vendor_id"} <= item_fields


def test_registry_projects_list_metadata_without_duplicating_it():
    meta = registry.noun_meta("account")
    definition = LIST_DEFINITIONS["account"]
    assert meta["definition"] is definition
    assert meta["record_type"] == definition.record_type
    assert meta["output_identifier"] == "id"
    assert meta["columns"] == definition.all_columns
    assert meta["filters"] == definition.filters
    assert meta["primary_collection_action"] == definition.primary_collection_action
