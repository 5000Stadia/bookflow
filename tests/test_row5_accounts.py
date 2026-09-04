"""Account domain invariants and its complete shared-command lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

# The package owns registration; the captain adds it to the lazy noun index.
from bookflow.commands.account_cmds import ACCOUNT_COMMANDS  # noqa: F401
from bookflow.company import accounts, schema
from bookflow.company.list_service import create_metadata
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database


def _chartless(client, name: str = "Account Test Books LLC") -> dict:
    return client.company.new(
        legal_name=name,
        home_currency="USD",
        organization="Demo Holdings LLC",
        timezone="UTC",
        chart="none",
    )


def _error(call, *args, **kwargs) -> BookflowError:
    with pytest.raises(BookflowError) as caught:
        call(*args, **kwargs)
    return caught.value


def test_account_complete_lifecycle_idempotency_outputs_and_audit(client):
    company = _chartless(client)["company_id"]
    created = client.account.create(
        name="Operating Checking",
        number="1001",
        type="bank",
        institution_name="Community Bank",
        institution_account_last4="A123",
        routing_number_last4="0123",
        next_check_number="100",
        check_reorder_number="R-1",
        order_printable_checks=True,
        currency="USD",
        company=company,
        idempotency_key="account-operating-checking",
    )
    replay = client.account.create(
        name="Operating Checking",
        number="1001",
        type="bank",
        institution_name="Community Bank",
        institution_account_last4="A123",
        routing_number_last4="0123",
        next_check_number="100",
        check_reorder_number="R-1",
        order_printable_checks=True,
        currency="USD",
        company=company,
        idempotency_key="account-operating-checking",
    )
    assert replay == {**created, "idempotent_replay": True}

    shown = client.account.show(account="operating checking", company=company)
    assert shown["id"] == created["id"]
    assert shown["balance"] == {
        "amount": "0.00",
        "currency": "USD",
        "minor_units": 0,
    }
    assert shown["available_balance"] is None
    assert shown["normal_balance"] == "debit"
    assert shown["statement_family"] == "balance_sheet"
    assert shown["has_transactions"] is False
    assert shown["provider_profile_ref"] is None

    listed = client.account.list(
        query="community", filter=["active=true", "type=bank"], company=company
    )
    assert [row["id"] for row in listed["items"]] == [created["id"]]

    updated = client.account.update(
        account=created["id"], description="Main operating account", company=company
    )
    assert updated["version"] == 2
    assert updated["changed_fields"] == ["description"]
    assert updated["warnings"] == [
        "Blind write: version 1 and fields description were not compared."
    ]
    no_change = client.account.update(
        account=created["id"],
        expected_version=2,
        description="Main operating account",
        company=company,
    )
    assert no_change["version"] == 2 and no_change["changed_fields"] == []

    deactivated = client.account.deactivate(
        account=created["id"], expected_version=2, company=company
    )
    assert deactivated["changed"] is True
    assert deactivated["active"] is False
    assert deactivated["affected_ids"] == [created["id"]]
    assert client.account.list(query="Operating", company=company)["items"] == []
    assert [
        row["id"]
        for row in client.account.list(
            query="Operating", include_inactive=True, company=company
        )["items"]
    ] == [created["id"]]
    no_change = client.account.deactivate(
        account=created["id"], expected_version=1, company=company
    )
    assert no_change["changed"] is False and no_change["version"] == 3

    activated = client.account.activate(
        account=created["id"], company=company
    )
    assert activated["changed"] is True and activated["active"] is True
    assert activated["warnings"] == [
        "Blind write: version 3 and fields active were not compared."
    ]

    events = client.audit.list(command="account deactivate", company=company)
    assert events["count"] == 1
    assert events["items"][0]["entry_count"] == 1


def test_account_number_currency_type_profiles_and_reimbursable_rules(client):
    company = _chartless(client)["company_id"]
    income = client.account.create(
        name="Reimbursed Expense Income",
        number="4000",
        type="income",
        company=company,
    )
    expense = client.account.create(
        name="Travel",
        number="6000",
        type="expense",
        track_reimbursable_expenses=True,
        reimbursable_income_account_id=income["id"],
        company=company,
    )
    assert expense["track_reimbursable_expenses"] is True

    cases = [
        ({"name": "Unicode number", "number": "１２", "type": "expense"}, "number"),
        ({"name": "Long number", "number": "12345678", "type": "expense"}, "number"),
        ({"name": "Lower currency", "currency": "usd", "type": "expense"}, "currency"),
        (
            {
                "name": "Provider",
                "type": "bank",
                "provider_profile_ref": "opaque-provider-id",
            },
            "provider_profile_ref",
        ),
        (
            {"name": "Income with routing", "type": "income", "routing_number_last4": "1234"},
            "routing_number_last4",
        ),
        (
            {
                "name": "Bad reimbursable",
                "type": "income",
                "track_reimbursable_expenses": True,
                "reimbursable_income_account_id": income["id"],
            },
            "track_reimbursable_expenses",
        ),
        (
            {
                "name": "Missing income",
                "type": "expense",
                "track_reimbursable_expenses": True,
            },
            "reimbursable_income_account_id",
        ),
        ({"name": "Nonposting tax", "type": "non_posting", "tax_line": "1040-C"}, "tax_line"),
    ]
    for payload, field in cases:
        error = _error(client.account.create, **payload, company=company)
        assert error.code == "E_VALIDATION"
        assert error.details["fields"][0]["field"].split(".")[0] == field

    duplicate = _error(
        client.account.create,
        name="Duplicate Number",
        number="6000",
        type="expense",
        company=company,
    )
    assert duplicate.code == "E_NAME_TAKEN"

    wrong_parent = _error(
        client.account.create,
        name="Wrong Parent",
        type="expense",
        parent_id=income["id"],
        company=company,
    )
    assert wrong_parent.code == "E_VALIDATION"

    class_record = client.run(
        "class create", {"name": "Field work"}, company=company
    )
    disabled_class = _error(
        client.account.create,
        name="Classed too early",
        type="expense",
        default_class_id=class_record["id"],
        company=company,
    )
    assert disabled_class.code == "E_VALIDATION"
    client.company.update(use_classes=True, company=company)
    assigned = client.account.create(
        name="Classed travel",
        type="expense",
        default_class_id=class_record["id"],
        company=company,
    )
    assert assigned["default_class_id"] == class_record["id"]


def test_hierarchy_rename_depth_and_cascade_are_deterministic(client):
    company = _chartless(client)["company_id"]
    root = client.account.create(name="Operations", type="expense", company=company)
    child = client.account.create(
        name="Facilities", type="expense", parent_id=root["id"], company=company
    )
    leaf = client.account.create(
        name="Repairs", type="expense", parent_id=child["id"], company=company
    )
    renamed = client.account.update(
        account=root["id"],
        expected_version=1,
        name="Operating Costs",
        company=company,
    )
    assert renamed["affected_descendant_ids"] == [child["id"], leaf["id"]]
    assert client.account.show(account=leaf["id"], company=company)["full_name"] == (
        "Operating Costs:Facilities:Repairs"
    )

    blocked = _error(
        client.account.deactivate,
        account=root["id"],
        expected_version=2,
        company=company,
    )
    assert blocked.code == "E_ACTIVE_DEPENDENTS"
    cascade = client.account.deactivate(
        account=root["id"], expected_version=2, cascade=True, company=company
    )
    assert cascade["affected_ids"] == [root["id"], child["id"], leaf["id"]]
    event = client.audit.list(command="account deactivate", company=company)["items"][0]
    assert event["entry_count"] == 3


def test_system_accounts_and_hard_dependencies_cannot_be_deactivated(client):
    system = client.account.show(
        account="Accounts Receivable", company="Demo Plumbing Co"
    )
    assert system["is_system"] is True
    protected = _error(
        client.account.update,
        account=system["id"],
        expected_version=system["version"],
        name="Receivables",
        company="Demo Plumbing Co",
    )
    assert protected.code == "E_SYSTEM_RECORD"
    assert protected.details["fields"] == ["name"]
    protected = _error(
        client.account.deactivate,
        account=system["id"],
        expected_version=system["version"],
        company="Demo Plumbing Co",
    )
    assert protected.code == "E_SYSTEM_RECORD"

    company = _chartless(client, "Account Dependency Test LLC")["company_id"]
    income = client.account.create(name="Recovery Income", type="income", company=company)
    client.account.create(
        name="Billable Travel",
        type="expense",
        track_reimbursable_expenses=True,
        reimbursable_income_account_id=income["id"],
        company=company,
    )
    used = _error(
        client.account.deactivate,
        account=income["id"],
        expected_version=1,
        company=company,
    )
    assert used.code == "E_RECORD_IN_USE"
    assert used.details["dependents"] == [
        {"record_type": "reimbursable_income_account_id", "count": 1}
    ]


def test_account_update_merges_disjoint_versions_and_conflicts_on_overlap(client):
    company = _chartless(client)["company_id"]
    account = client.account.create(name="Travel", type="expense", company=company)
    first = client.account.update(
        account=account["id"],
        expected_version=1,
        description="Travel costs",
        company=company,
    )
    assert first["version"] == 2
    merged = client.account.update(
        account=account["id"],
        expected_version=1,
        note="Receipts required",
        company=company,
    )
    assert merged["version"] == 3
    assert merged["merged_over_versions"] == [2]
    assert merged["description"] == "Travel costs"

    conflict = _error(
        client.account.update,
        account=account["id"],
        expected_version=1,
        description="Different description",
        company=company,
    )
    assert conflict.code == "E_VERSION_CONFLICT"
    assert conflict.details["changed_fields"] == ["description", "note"]
    shown = client.account.show(account=account["id"], company=company)
    assert shown["description"] == "Travel costs"


def test_account_selector_is_cross_company_secret_and_default_sort_is_lexical(client):
    first_company = _chartless(client, "First Account Books LLC")["company_id"]
    second_company = _chartless(client, "Second Account Books LLC")["company_id"]
    hidden = client.account.create(
        name="Hidden Across Companies", type="expense", company=first_company
    )
    hidden_error = _error(
        client.account.show, account=hidden["id"], company=second_company
    )
    missing_error = _error(
        client.account.show, account=new_id(), company=second_company
    )
    assert hidden_error.to_dict() == missing_error.to_dict()

    left = client.account.create(
        name="Long Left", number="4100020", type="income", company=second_company
    )
    right = client.account.create(
        name="Short Right", number="4101", type="income", company=second_company
    )
    last = client.account.create(
        name="Unnumbered", type="income", company=second_company
    )
    listed = client.account.list(company=second_company)
    assert [row["id"] for row in listed["items"]] == [
        left["id"],
        right["id"],
        last["id"],
    ]


def test_active_item_account_reference_blocks_deactivation(client):
    made = _chartless(client)
    company = made["company_id"]
    account = client.account.create(
        name="Sales Income", type="income", company=company
    )
    database = Path(made["path"]) / "company.db"
    with open_database(database, writable=True) as db:
        info = db.conn.execute(sa.select(schema.company_info)).mappings().one()
        item_id = new_id()
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(
            schema.items.insert().values(
                **create_metadata(info["created_by"], "system", record_id=item_id),
                active=True,
                seed_key=None,
                name="Consulting",
                name_key="consulting",
                parent_id=None,
                full_name="Consulting",
                full_name_key="consulting",
                depth=1,
                path=f"/{item_id}/",
                type="service",
                description="Consulting",
                sales_enabled=True,
                purchase_enabled=False,
                income_account_id=account["id"],
            )
        )
        db.raw.execute("COMMIT")

    error = _error(
        client.account.deactivate,
        account=account["id"],
        expected_version=1,
        company=company,
    )
    assert error.code == "E_RECORD_IN_USE"
    assert error.details["dependents"] == [
        {"record_type": "income_account_id", "count": 1}
    ]
