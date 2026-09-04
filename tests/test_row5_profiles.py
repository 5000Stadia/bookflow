"""Row 5 supporting profile domain rules and standard manifest."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.profiles import (
    HIERARCHICAL_NOUNS,
    apply_standard_profile,
    compute_term_dates,
    load_standard_profile,
    parse_profile_input,
    persist_profile_active_change,
    persist_profile_mutation,
    persist_profile_update,
    plan_profile_active_change,
    plan_profile_create,
    plan_profile_update,
    project_profile_record,
)
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head


ACTOR_ID = "01J00000000000000000000000"
AT = "2026-09-04T08:00:00.000Z"


@pytest.fixture
def company_db(tmp_path: Path):
    path = tmp_path / "company.db"
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, "company", None)
        yield db


def _insert_source(db, table: sa.Table, *, name: str, active: bool = True) -> str:
    identifier = new_id()
    db.conn.execute(
        table.insert().values(
            id=identifier,
            version=1,
            created_at=AT,
            created_by=ACTOR_ID,
            created_via="python",
            updated_at=AT,
            updated_by=ACTOR_ID,
            updated_via="python",
            active=active,
            seed_key=None,
            name=name,
            name_key=name.casefold(),
        )
    )
    return identifier


def _create(db, noun: str, payload: dict) -> dict:
    mutation = plan_profile_create(
        db,
        noun,
        payload,
        actor_id=ACTOR_ID,
        via="python",
        at=AT,
    )
    persist_profile_mutation(db, mutation)
    return dict(mutation.after)


def test_standard_manifest_has_the_exact_versioned_seed_key_inventory():
    manifest = load_standard_profile()
    assert manifest.manifest_id == "standard"
    assert manifest.version == 1
    assert manifest.lists == [
        "term",
        "payment-method",
        "sales-tax-code",
        "ship-method",
        "customer-message",
    ]
    assert {
        noun: [record["seed_key"] for record in records]
        for noun, records in manifest.records.items()
    } == {
        "term": [
            "term.due-on-receipt",
            "term.net-15",
            "term.net-30",
            "term.net-60",
            "term.1-10-net-30",
            "term.2-10-net-30",
        ],
        "payment-method": [
            "payment.cash",
            "payment.check",
            "payment.visa",
            "payment.mastercard",
            "payment.american-express",
            "payment.discover",
            "payment.debit-card",
            "payment.gift-card",
            "payment.electronic-check",
            "payment.bank-transfer",
            "payment.other",
        ],
        "sales-tax-code": ["tax-code.tax", "tax-code.non"],
        "ship-method": [
            "ship.delivery",
            "ship.federal-express",
            "ship.ups",
            "ship.usps",
            "ship.other",
        ],
        "customer-message": [
            "message.thank-you",
            "message.prompt-payment",
            "message.remit",
        ],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Missing due", "kind": "standard"},
        {"name": "Too far", "kind": "standard", "due_days": 366},
        {"name": "Boolean", "kind": "standard", "due_days": True},
        {
            "name": "Mixed",
            "kind": "standard",
            "due_days": 30,
            "due_day_of_month": 15,
        },
        {
            "name": "Unpaired",
            "kind": "standard",
            "due_days": 30,
            "discount_days": 10,
        },
        {
            "name": "Missing threshold",
            "kind": "date_driven",
            "due_day_of_month": 31,
        },
        {
            "name": "Percent overflow",
            "kind": "standard",
            "due_days": 30,
            "discount_days": 10,
            "discount_percent": "100.000001",
        },
    ],
)
def test_term_discriminator_pairs_and_bounds_are_strict(payload):
    with pytest.raises(BookflowError) as exc:
        parse_profile_input("term", payload)
    assert exc.value.code == "E_VALIDATION"


def test_term_dates_use_exact_days_and_end_of_month_clamping():
    standard = {
        "name": "Net 30",
        "kind": "standard",
        "due_days": 30,
        "discount_days": 10,
        "discount_percent": "2",
    }
    assert compute_term_dates(standard, date(2024, 1, 31)).model_dump() == {
        "due_date": date(2024, 3, 1),
        "discount_date": date(2024, 2, 10),
    }

    date_driven = {
        "name": "Month end",
        "kind": "date_driven",
        "due_day_of_month": 31,
        "due_next_month_if_within_days": 2,
        "discount_day_of_month": 31,
        "discount_percent": "1",
    }
    # Two days from January's due date moves the deadline into February;
    # both configured day 31 values clamp to leap-year month end.
    assert compute_term_dates(date_driven, date(2024, 1, 29)).model_dump() == {
        "due_date": date(2024, 2, 29),
        "discount_date": date(2024, 2, 29),
    }
    assert compute_term_dates(date_driven, date(2024, 1, 28)).due_date == date(2024, 1, 31)


def test_stored_term_discriminator_is_pinned(company_db):
    term = _create(
        company_db,
        "term",
        {"name": "Net 30", "kind": "standard", "due_days": 30},
    )
    with pytest.raises(BookflowError) as changed:
        plan_profile_update(
            company_db,
            "term",
            term["id"],
            {
                "kind": "date_driven",
                "due_day_of_month": 15,
                "due_next_month_if_within_days": 5,
            },
            actor_id=ACTOR_ID,
            via="python",
        )
    assert changed.value.code == "E_TYPE_CHANGE"


def test_sales_rep_polymorphic_source_is_active_typed_and_live(company_db):
    employee_id = _insert_source(company_db, c.employees, name="Avery Reed")

    with pytest.raises(BookflowError) as mismatch:
        plan_profile_create(
            company_db,
            "sales-rep",
            {
                "name": "Avery",
                "initials": "AR",
                "name_type": "vendor",
                "name_id": employee_id,
            },
            actor_id=ACTOR_ID,
            via="python",
        )
    assert mismatch.value.code == "E_RECORD_NOT_FOUND"

    created = _create(
        company_db,
        "sales-rep",
        {
            "name": "Avery",
            "initials": "AR",
            "name_type": "employee",
            "name_id": employee_id,
        },
    )
    projection = project_profile_record(company_db, "sales-rep", created)
    assert projection["source_type"] == "employee"
    assert projection["source_name"] == "Avery Reed"

    company_db.conn.execute(
        c.employees.update()
        .where(c.employees.c.id == employee_id)
        .values(name="Avery Rowan", active=False)
    )
    # Existing aliases remain readable and follow later source renames even
    # when the source is no longer offered for a new alias.
    assert project_profile_record(company_db, "sales-rep", created)["source_name"] == "Avery Rowan"

    inactive_id = _insert_source(company_db, c.employees, name="Inactive", active=False)
    with pytest.raises(BookflowError) as inactive:
        plan_profile_create(
            company_db,
            "sales-rep",
            {
                "name": "Inactive",
                "initials": "IN",
                "name_type": "employee",
                "name_id": inactive_id,
            },
            actor_id=ACTOR_ID,
            via="python",
        )
    assert inactive.value.code == "E_INACTIVE_REFERENCE"


def test_normalized_names_and_sales_rep_initials_are_unique(company_db):
    first = _create(
        company_db,
        "payment-method",
        {"name": " Caf\u00e9 ", "kind": "other"},
    )
    assert first["name"] == "Caf\u00e9"
    with pytest.raises(BookflowError) as duplicate:
        plan_profile_create(
            company_db,
            "payment-method",
            {"name": "CAFE\u0301", "kind": "cash"},
            actor_id=ACTOR_ID,
            via="python",
        )
    assert duplicate.value.code == "E_NAME_TAKEN"

    employee_id = _insert_source(company_db, c.employees, name="One")
    employee_two_id = _insert_source(company_db, c.employees, name="Two")
    _create(
        company_db,
        "sales-rep",
        {"name": "One", "initials": "xy", "name_type": "employee", "name_id": employee_id},
    )
    with pytest.raises(BookflowError) as initials:
        plan_profile_create(
            company_db,
            "sales-rep",
            {"name": "Two", "initials": "XY", "name_type": "employee", "name_id": employee_two_id},
            actor_id=ACTOR_ID,
            via="python",
        )
    assert initials.value.code == "E_NAME_TAKEN"


def test_manifest_reapply_preserves_edits_and_inactive_state_and_inserts_only_missing(company_db):
    first = apply_standard_profile(company_db, actor_id=ACTOR_ID, via="python")
    assert first.inserted_by_list == {
        "term": 6,
        "payment-method": 11,
        "sales-tax-code": 2,
        "ship-method": 5,
        "customer-message": 3,
    }
    assert first.preserved_by_list == {noun: 0 for noun in first.inserted_by_list}

    company_db.conn.execute(
        c.terms.update()
        .where(c.terms.c.seed_key == "term.net-30")
        .values(name="My edited term", name_key="my edited term", active=False)
    )
    missing_id = company_db.conn.execute(
        sa.select(c.payment_methods.c.id).where(c.payment_methods.c.seed_key == "payment.other")
    ).scalar_one()
    company_db.conn.execute(c.payment_methods.delete().where(c.payment_methods.c.id == missing_id))

    second = apply_standard_profile(company_db, actor_id=ACTOR_ID, via="python")
    assert second.inserted_by_list == {
        "term": 0,
        "payment-method": 1,
        "sales-tax-code": 0,
        "ship-method": 0,
        "customer-message": 0,
    }
    assert second.preserved_by_list == {
        "term": 6,
        "payment-method": 10,
        "sales-tax-code": 2,
        "ship-method": 5,
        "customer-message": 3,
    }
    edited = company_db.conn.execute(
        sa.select(c.terms).where(c.terms.c.seed_key == "term.net-30")
    ).mappings().one()
    assert edited["name"] == "My edited term"
    assert edited["active"] is False
    assert company_db.conn.execute(
        sa.select(sa.func.count()).select_from(c.payment_methods)
    ).scalar_one() == 11

    third = apply_standard_profile(company_db, actor_id=ACTOR_ID, via="python")
    assert set(third.inserted_by_list.values()) == {0}
    assert third.preserved_by_list == {
        "term": 6,
        "payment-method": 11,
        "sales-tax-code": 2,
        "ship-method": 5,
        "customer-message": 3,
    }


def test_manifest_dry_run_writes_nothing(company_db):
    result = apply_standard_profile(
        company_db,
        actor_id=ACTOR_ID,
        via="python",
        dry_run=True,
    )
    assert result.dry_run is True
    assert sum(result.inserted_by_list.values()) == 27
    assert company_db.conn.execute(sa.select(sa.func.count()).select_from(c.terms)).scalar_one() == 0


def test_manifest_validates_every_missing_insert_before_writing(company_db):
    _create(company_db, "payment-method", {"name": "Cash", "kind": "cash"})
    with pytest.raises(BookflowError) as collision:
        apply_standard_profile(company_db, actor_id=ACTOR_ID, via="python")
    assert collision.value.code == "E_NAME_TAKEN"
    # Terms precede payment methods in the manifest but are only planned, so
    # the later collision cannot leave an early list partially installed.
    assert company_db.conn.execute(sa.select(sa.func.count()).select_from(c.terms)).scalar_one() == 0
    assert company_db.raw.in_transaction is False


def test_all_five_hierarchical_profiles_share_depth_cycle_and_active_rules(company_db):
    assert HIERARCHICAL_NOUNS == {
        "item-category",
        "class",
        "customer-type",
        "vendor-type",
        "job-type",
    }
    rows = []
    parent_id = None
    for level in range(1, 6):
        row = _create(
            company_db,
            "customer-type",
            {"name": f"Level {level}", "parent_id": parent_id},
        )
        rows.append(row)
        parent_id = row["id"]
    assert rows[-1]["full_name"] == "Level 1:Level 2:Level 3:Level 4:Level 5"

    with pytest.raises(BookflowError) as depth:
        plan_profile_create(
            company_db,
            "customer-type",
            {"name": "Level 6", "parent_id": rows[-1]["id"]},
            actor_id=ACTOR_ID,
            via="python",
        )
    assert depth.value.code == "E_HIERARCHY_DEPTH"

    with pytest.raises(BookflowError) as cycle:
        plan_profile_update(
            company_db,
            "customer-type",
            rows[0]["id"],
            {"parent_id": rows[-1]["id"]},
            actor_id=ACTOR_ID,
            via="python",
        )
    assert cycle.value.code == "E_HIERARCHY_CYCLE"

    with pytest.raises(BookflowError) as children:
        plan_profile_active_change(
            company_db,
            "customer-type",
            rows[0]["id"],
            False,
            actor_id=ACTOR_ID,
            via="python",
        )
    assert children.value.code == "E_ACTIVE_DEPENDENTS"

    cascade = plan_profile_active_change(
        company_db,
        "customer-type",
        rows[0]["id"],
        False,
        actor_id=ACTOR_ID,
        via="python",
        cascade=True,
        at=AT,
    )
    assert cascade.affected_ids == tuple(row["id"] for row in rows)
    persist_profile_active_change(company_db, cascade)
    with pytest.raises(BookflowError) as ancestor:
        plan_profile_active_change(
            company_db,
            "customer-type",
            rows[-1]["id"],
            True,
            actor_id=ACTOR_ID,
            via="python",
        )
    assert ancestor.value.code == "E_INACTIVE_REFERENCE"


def test_hierarchy_rename_reprojects_descendants_without_versioning_them(company_db):
    root = _create(company_db, "class", {"name": "Old", "parent_id": None})
    child = _create(company_db, "class", {"name": "Child", "parent_id": root["id"]})
    plan = plan_profile_update(
        company_db,
        "class",
        root["id"],
        {"name": "New"},
        actor_id=ACTOR_ID,
        via="python",
        at=AT,
    )
    assert plan.mutation is not None
    assert len(plan.projections) == 1
    persist_profile_update(company_db, plan)
    updated_child = company_db.conn.execute(
        sa.select(c.classes).where(c.classes.c.id == child["id"])
    ).mappings().one()
    assert updated_child["full_name"] == "New:Child"
    assert updated_child["version"] == 1
