"""Row 5 list storage is complete and upgrades historical company data without loss."""

from __future__ import annotations

import json
import inspect
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from bookflow.company import schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import _config


COMMON = {
    "id", "version", "created_at", "created_by", "created_via",
    "updated_at", "updated_by", "updated_via", "active", "seed_key",
}
HIERARCHY = {"name", "name_key", "parent_id", "full_name", "full_name_key", "depth", "path"}
PERSON = {"salutation", "first_name", "middle_name", "last_name", "job_title"}
ADDRESS = {"line1", "line2", "city", "state", "postal_code", "country"}


def _address(prefix: str) -> set[str]:
    return {f"{prefix}_{field}" for field in ADDRESS}


def _money(prefix: str) -> set[str]:
    return {f"{prefix}_minor_units", f"{prefix}_currency"}


EXPECTED_COLUMNS = {
    "accounts": COMMON | HIERARCHY | {
        "number", "number_key", "type", "description", "currency", "tax_line",
        "institution_name", "institution_account_last4", "routing_number_last4",
        "provider_profile_ref", "next_check_number", "check_reorder_number",
        "order_printable_checks", "default_class_id", "track_reimbursable_expenses",
        "reimbursable_income_account_id", "note", "system_role",
    },
    "customers": COMMON | HIERARCHY | PERSON | _address("billing") | _address("payment_billing") | _money("credit_limit") | {
        "company_name", "terms_id", "sales_tax_code_id", "sales_tax_item_id", "price_level_id",
        "customer_type_id", "sales_rep_id", "preferred_payment_method_id", "preferred_ship_method_id",
        "resale_number", "preferred_delivery_method", "account_number", "payment_profile_ref",
        "payment_brand", "payment_last4", "payment_expiry_month", "payment_expiry_year", "notes",
        "default_class_id", "job_status", "job_type_id", "job_start", "job_projected_end", "job_end",
        "job_description", "job_sales_rep_id", "address_mode", "contact_mode",
    },
    "customer_addresses": {"id", "customer_id", "position", "active", "label", "label_key", "is_default"} | _address("address"),
    "customer_contacts": {"id", "customer_id", "position", "active", "role"} | PERSON | {
        "work_phone", "home_phone", "mobile_phone", "other_phone", "work_fax", "home_fax",
        "primary_email", "secondary_email", "website", "external_handle", "display_name",
    },
    "customer_contact_points": {"id", "contact_id", "position", "active", "kind", "custom_label", "value"},
    "vendors": COMMON | PERSON | _address("address") | _money("credit_limit") | {
        "name", "name_key", "company_name", "terms_id", "vendor_type_id", "default_class_id",
        "billing_rate_level_id", "account_number", "print_name_on_check_as", "eligible_1099",
        "is_tax_agency", "recall_last_transaction", "notes", "tax_id_kind", "tax_id_last4", "tax_profile_ref",
    },
    "vendor_contacts": {"id", "vendor_id", "position", "active", "role"} | PERSON | {
        "work_phone", "home_phone", "mobile_phone", "other_phone", "work_fax", "home_fax",
        "primary_email", "secondary_email", "website", "external_handle", "display_name",
    },
    "vendor_contact_points": {"id", "contact_id", "position", "active", "kind", "custom_label", "value"},
    "vendor_expense_accounts": {"id", "vendor_id", "position", "active", "account_id"},
    "customer_vendor_links": {
        "id", "version", "created_at", "created_by", "created_via", "updated_at", "updated_by",
        "updated_via", "customer_id", "vendor_id", "active",
    },
    "employees": COMMON | PERSON | _address("address") | {
        "name", "name_key", "print_name_on_check_as", "employment_type", "phone", "email", "hire_date",
        "release_date", "emergency_contact_name", "emergency_contact_relationship", "emergency_contact_phone",
        "emergency_contact_email", "default_class_id", "notes", "tax_id_last4",
    },
    "other_names": COMMON | PERSON | _address("address") | {
        "name", "name_key", "company_name", "phone", "email", "contact", "account_number",
        "default_class_id", "notes", "converted_to_type", "converted_to_id",
    },
    "items": COMMON | HIERARCHY | _money("price") | _money("cost") | _money("discount_amount") |
        _money("original_cost") | _money("disposal_proceeds") | _money("disposal_costs") |
        _money("book_basis") | _money("tax_basis") | {
            "type", "category_id", "description", "purchase_description", "sales_enabled", "purchase_enabled",
            "income_account_id", "expense_account_id", "cogs_account_id", "asset_account_id", "deposit_account_id",
            "liability_account_id", "default_class_id", "sales_tax_code_id", "manufacturer_part_number", "barcode",
            "unit_of_measure_set_id", "reorder_point_min_microunits", "reorder_point_max_microunits", "notes",
            "preferred_vendor_id", "print_members", "other_charge_percent_millionths", "assembly_build_point_microunits",
            "discount_percent_millionths", "payment_method_id", "use_undeposited_funds",
            "tax_percent_millionths", "tax_agency_vendor_id", "asset_number", "purchase_date", "vendor_id", "location",
            "serial_number", "warranty_expiration", "disposal_status", "disposal_date",
            "accumulated_depreciation_account_id", "depreciation_expense_account_id", "gain_loss_account_id",
            "depreciation_method", "useful_life_months",
        },
    "item_categories": COMMON | HIERARCHY,
    "item_members": {"id", "owner_item_id", "position", "active", "component_item_id", "quantity_microunits", "unit_id"},
    "item_vendor_profiles": {"id", "item_id", "position", "active", "vendor_id", "preferred_rank", "vendor_item_name",
        "purchase_cost_minor_units", "purchase_cost_currency", "minimum_quantity_microunits", "lead_time_days",
        "manufacturer_part_number", "availability_notes"},
    "classes": COMMON | HIERARCHY,
    "terms": COMMON | {"name", "name_key", "kind", "due_days", "discount_days", "due_day_of_month",
        "due_next_month_if_within_days", "discount_day_of_month", "discount_percent_millionths"},
    "payment_methods": COMMON | {"name", "name_key", "kind"},
    "sales_tax_codes": COMMON | {"code", "code_key", "description", "taxable"},
    "customer_types": COMMON | HIERARCHY,
    "vendor_types": COMMON | HIERARCHY,
    "job_types": COMMON | HIERARCHY,
    "sales_reps": COMMON | {"name", "name_key", "initials", "initials_key", "name_type", "name_id"},
    "ship_methods": COMMON | {"name", "name_key", "display_order"},
    "customer_messages": COMMON | {"name", "name_key", "text", "display_order"},
    "price_levels": COMMON | _money("rounding_increment") | _money("rounding_offset") | {
        "name", "name_key", "kind", "currency", "rounding_mode", "percent_millionths",
    },
    "price_level_items": {"id", "price_level_id", "position", "active", "item_id", "price_minor_units",
        "price_currency", "percent_millionths", "adjustment_basis"},
    "units_of_measure": COMMON | {"name", "name_key", "default_purchase_unit_id", "default_sales_unit_id", "default_shipping_unit_id"},
    "unit_conversions": {"id", "unit_of_measure_id", "position", "active", "name", "name_key", "abbreviation",
        "abbreviation_key", "is_base", "base_factor_nanounits"},
    "custom_field_defs": COMMON | {"name", "name_key", "kind", "position", "required", "default_canonical_text"},
    "custom_field_scopes": {"id", "definition_id", "position", "active", "record_type", "definition_name",
        "definition_name_key", "definition_active"},
    "custom_field_choices": {"id", "definition_id", "position", "active", "value", "value_key"},
    "custom_field_values": {"id", "def_id", "record_type", "record_id", "active", "canonical_text"},
}


ROW5_COMPANY_SETTINGS = {
    "default_chart_version", "use_account_numbers", "show_lowest_subaccount_only",
    "required_employee_profile_fields", "use_classes", "prompt_for_class", "enable_price_levels",
    "units_of_measure_mode", "sales_tax_enabled", "default_sales_tax_item_id",
    "sales_tax_liability_basis", "sales_tax_remittance_frequency", "default_ship_method_id",
    "free_on_board", "order_printable_checks",
}


def _upgrade(path: Path, revision: str) -> None:
    with open_database(path, writable=True, create=True) as db:
        db.raw.execute("PRAGMA foreign_keys=OFF")
        command.upgrade(_config("company", db.conn), revision)
        db.raw.execute("PRAGMA foreign_keys=ON")


def _normalized_shape(conn: sqlite3.Connection) -> tuple:
    tables = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        tables.append((
            name,
            tuple(conn.execute(f'PRAGMA table_info("{name}")')),
            tuple(conn.execute(f'PRAGMA foreign_key_list("{name}")')),
            tuple(conn.execute(
                "SELECT name, \"unique\", partial FROM pragma_index_list(?) ORDER BY name",
                (name,),
            )),
        ))
    return tuple(tables)


def test_row5_metadata_has_exact_primary_and_child_column_inventory():
    assert set(EXPECTED_COLUMNS) == {table.name for table in schema.ROW5_TABLES}
    for table_name, expected in EXPECTED_COLUMNS.items():
        assert set(schema.metadata.tables[table_name].c.keys()) == expected, table_name
    assert ROW5_COMPANY_SETTINGS <= set(schema.company_info.c.keys())
    assert "undo_of_event_id" in schema.audit_events.c
    assert schema.customers.c.preferred_delivery_method.nullable


def test_co0003_contains_no_live_company_schema_import():
    migration = __import__(
        "bookflow.storage.company_migrations.versions.0003_lists",
        fromlist=["upgrade"],
    )
    source = inspect.getsource(migration)
    assert "bookflow.company.schema" not in source
    assert "ROW5_TABLES" not in source


def test_row5_foreign_keys_are_restrictive_and_indexed():
    for table in (*schema.ROW5_TABLES, schema.company_info, schema.audit_events):
        for foreign_key in table.foreign_keys:
            assert foreign_key.ondelete == "RESTRICT", (table.name, foreign_key.parent.name)
            assert any(
                tuple(index.columns)[0] is foreign_key.parent
                for index in table.indexes
                if tuple(index.columns)
            ), (table.name, foreign_key.parent.name)


def test_row5_partial_unique_structural_indexes_are_present():
    expected = {
        "ux_accounts_root_name", "ux_accounts_sibling_name",
        "ux_customer_contacts_primary", "ux_customer_contacts_alternate",
        "ux_vendor_contacts_primary", "ux_vendor_contacts_alternate",
        "ux_customer_vendor_links_customer", "ux_customer_vendor_links_vendor",
        "ux_unit_conversions_base", "ux_custom_field_scopes_record_name",
        "ux_co_audit_events_undo",
    }
    indexes = {
        index.name: index
        for table in schema.metadata.tables.values()
        for index in table.indexes
    }
    assert expected <= set(indexes)
    for name in expected:
        assert indexes[name].unique
        assert indexes[name].dialect_options["sqlite"].get("where") is not None


def test_row5_account_item_and_contact_checks_are_frozen():
    assert str(schema.accounts.c.number.type) == "VARCHAR(7)"
    assert str(schema.accounts.c.number_key.type) == "VARCHAR(7)"
    checks = {
        constraint.name: str(constraint.sqltext)
        for table in schema.ROW5_TABLES
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert "number NOT GLOB '*[^0-9]*'" in checks["ck_accounts_number"]
    assert "other_charge_percent_millionths BETWEEN 0 AND 100000000" in checks["ck_items_other_charge_percent"]
    assert "assembly_build_point_microunits >= 0" in checks["ck_items_assembly_build_point"]
    assert set(schema.CONTACT_POINT_KINDS) >= {
        "main_phone", "home_phone", "mobile_phone", "main_email", "website",
        "linked_in", "facebook", "twitter", "skype_id", "other_4",
    }
    assert schema.items.c.preferred_vendor_id.references(schema.vendors.c.id)


def test_co0003_frozen_ddl_has_exact_row5_table_and_column_inventory(tmp_path: Path):
    path = tmp_path / "co0003-shape.db"
    _upgrade(path, "co0003")
    with sqlite3.connect(path) as conn:
        table_names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert set(EXPECTED_COLUMNS) <= table_names
        for table_name, expected in EXPECTED_COLUMNS.items():
            assert {row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")')} == expected
        assert ROW5_COMPANY_SETTINGS <= {
            row[1] for row in conn.execute("PRAGMA table_info(company_info)")
        }
        account_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='accounts'"
        ).fetchone()[0]
        item_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='items'"
        ).fetchone()[0]
        assert "number NOT GLOB '*[^0-9]*'" in account_sql
        assert "other_charge_percent_millionths BETWEEN 0 AND 100000000" in item_sql
        assert "assembly_build_point_microunits >= 0" in item_sql
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_co0003_direct_upgrade_preserves_historical_audit_rows_and_defaults(tmp_path: Path):
    path = tmp_path / "historical-company.db"
    _upgrade(path, "co0002")
    event_rows = (
        ("E1", 3, "2026-01-01T00:00:00Z", "company update", "U1", "human", None, "cli", "client", "1", "host", "S1", "R1", None, "because", None, None, "paper:1", "first"),
        ("E2", 9, "2026-01-02T00:00:00Z", "directive add", "U2", "agent", "U1", "http", "client", "2", "host", "S2", "R2", "retry", None, "D1", "SI-1", None, "second"),
    )
    entry_rows = (
        ("N1", "E1", "company_info", "C1", "update", 1, 2, b"\x00after\xff", b"\x00before\x80"),
        ("N2", "E2", "directive", "D1", "create", None, 1, b"\x01compressed\x00", None),
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO company_info (id,version,created_at,created_by,created_via,updated_at,updated_by,updated_via,"
            "legal_name,display_name,tax_id_kind,entity_type,income_tax_form,fiscal_year_start_month,tax_year_start_month,"
            "report_basis,home_currency,timezone,recent_activity_window_seconds,default_chart) "
            "VALUES ('C1',2,'t','U1','cli','t','U1','cli','Legal','Display','ein','other','other',1,1,'accrual','USD','UTC',60,'general')"
        )
        conn.executemany(
            "INSERT INTO audit_events (id,seq,at,command,actor_id,actor_kind,on_behalf_of,interface,client_name,client_version,"
            "client_host,session_id,request_id,idempotency_key,reason,directive_id,directive_code,source_ref,summary) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            event_rows,
        )
        conn.executemany(
            "INSERT INTO audit_entries (id,event_id,record_type,record_id,action,version_before,version_after,after,before) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            entry_rows,
        )

    _upgrade(path, "co0003")

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("co0003",)
        migrated_events = tuple(conn.execute(
            "SELECT id,seq,at,command,actor_id,actor_kind,on_behalf_of,interface,client_name,client_version,client_host,"
            "session_id,request_id,idempotency_key,reason,directive_id,directive_code,source_ref,summary "
            "FROM audit_events ORDER BY seq"
        ))
        migrated_entries = tuple(conn.execute(
            "SELECT id,event_id,record_type,record_id,action,version_before,version_after,after,before "
            "FROM audit_entries ORDER BY id"
        ))
        assert migrated_events == event_rows
        assert migrated_entries == entry_rows
        info = conn.execute(
            "SELECT default_chart,default_chart_version,use_account_numbers,show_lowest_subaccount_only,"
            "required_employee_profile_fields,use_classes,prompt_for_class,enable_price_levels,units_of_measure_mode,"
            "sales_tax_enabled,default_sales_tax_item_id,sales_tax_liability_basis,sales_tax_remittance_frequency,"
            "default_ship_method_id,free_on_board,order_printable_checks FROM company_info"
        ).fetchone()
        assert info[:4] == (None, None, 1, 0)
        assert json.loads(info[4]) == [
            ["first_name"], ["last_name"], ["address.line1"], ["address.city"],
            ["address.state"], ["address.postal_code"], ["phone", "email"],
        ]
        assert info[5:] == (0, 0, 0, "disabled", 0, None, "invoice_date", "quarterly", None, None, 0)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {row[2] for row in conn.execute("PRAGMA foreign_key_list(audit_entries)")} == {"audit_events"}


def test_fresh_and_populated_co0002_paths_converge_at_co0003(tmp_path: Path):
    fresh = tmp_path / "fresh.db"
    upgraded = tmp_path / "upgraded.db"
    _upgrade(fresh, "co0003")
    _upgrade(upgraded, "co0002")
    with sqlite3.connect(upgraded) as conn:
        conn.execute(
            "INSERT INTO company_info (id,version,created_at,created_by,created_via,updated_at,updated_by,updated_via,"
            "legal_name,display_name,tax_id_kind,entity_type,income_tax_form,fiscal_year_start_month,tax_year_start_month,"
            "report_basis,home_currency,timezone,recent_activity_window_seconds) "
            "VALUES ('C1',1,'t','U1','cli','t','U1','cli','L','D','ein','other','other',1,1,'accrual','USD','UTC',60)"
        )
    _upgrade(upgraded, "co0003")
    with sqlite3.connect(fresh) as left, sqlite3.connect(upgraded) as right:
        assert _normalized_shape(left) == _normalized_shape(right)


def test_audit_undo_link_is_unique_and_self_referential(tmp_path: Path):
    path = tmp_path / "undo.db"
    _upgrade(path, "co0003")
    base = ("t", "undo", "cli", "c", "1", "h", "S", "R", "summary")
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO audit_events (id,at,command,interface,client_name,client_version,client_host,session_id,request_id,summary) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("E1", *base),
        )
        conn.execute(
            "INSERT INTO audit_events (id,at,command,interface,client_name,client_version,client_host,session_id,request_id,undo_of_event_id,summary) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("E2", *base[:-1], "E1", base[-1]),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audit_events (id,at,command,interface,client_name,client_version,client_host,session_id,request_id,undo_of_event_id,summary) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("E3", *base[:-1], "E1", base[-1]),
            )
