from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import ValidationError

from bookflow.company import schema
from bookflow.company.custom_fields import (
    CustomFieldChoiceInput,
    CustomFieldDefinitionCreate,
    CustomFieldDefinitionUpdate,
    CustomFieldValuePatch,
    apply_owner_value_plan,
    create_definition,
    custom_field_logical_path,
    parse_employee_requirement_path,
    parse_typed_value,
    plan_owner_value_patch,
    read_owner_values,
    typed_value_from_canonical,
    update_definition,
    validate_employee_requirement_groups,
)
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import _config


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sa.Connection]:
    path = tmp_path / "custom-fields-co0003.db"
    with open_database(path, writable=True, create=True) as db:
        db.raw.execute("PRAGMA foreign_keys=OFF")
        command.upgrade(_config("company", db.conn), "co0003")
        db.raw.execute("PRAGMA foreign_keys=ON")
        yield db.conn


def _definition(
    conn: sa.Connection,
    *,
    name: str,
    kind: str = "text",
    scopes: tuple[str, ...] = ("customer",),
    choices: tuple[CustomFieldChoiceInput, ...] = (),
    required: bool = False,
    default=None,
    active: bool = True,
) -> dict:
    return create_definition(
        conn,
        CustomFieldDefinitionCreate(
            name=name,
            kind=kind,
            scopes=scopes,
            choices=choices,
            required=required,
            default=default,
            active=active,
        ),
        actor_id=new_id(),
        interface="python",
        at="2026-09-04T00:00:00Z",
    )


def _assert_code(code: str, call) -> BookflowError:
    with pytest.raises(BookflowError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def test_typed_values_are_exact_and_canonical() -> None:
    assert parse_typed_value("number", "00012.340000000") == ("12.34", "12.34")
    assert parse_typed_value("number", "-0.000000000") == ("0", "0")
    assert typed_value_from_canonical("number", "9.100000000") == "9.1"
    assert parse_typed_value("bool", True) == ("true", True)
    assert parse_typed_value("bool", False) == ("false", False)
    assert typed_value_from_canonical("bool", "false") is False
    assert parse_typed_value("date", "2024-02-29") == ("2024-02-29", "2024-02-29")
    assert parse_typed_value("text", "  kept verbatim  ") == ("  kept verbatim  ", "  kept verbatim  ")
    assert parse_typed_value("choice", " NORTH ", choices={"north": "North"}) == ("North", "North")


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("number", 1.25),
        ("number", True),
        ("number", "1.0000000001"),
        ("bool", "true"),
        ("bool", 1),
        ("date", "2023-02-29"),
        ("date", False),
        ("text", 1),
    ],
)
def test_typed_values_reject_floats_booleans_bad_scale_and_bad_dates(kind, value) -> None:
    _assert_code("E_VALIDATION", lambda: parse_typed_value(kind, value))


def test_definition_models_are_strict_and_forbid_secret_fields() -> None:
    with pytest.raises(ValidationError):
        CustomFieldChoiceInput(id="not-a-ulid", value="North")
    with pytest.raises(ValidationError):
        CustomFieldChoiceInput(value=" ")
    with pytest.raises(ValidationError):
        CustomFieldDefinitionCreate(name="Region", kind="text", scopes=("customer",), position=1.0)
    with pytest.raises(ValidationError):
        CustomFieldDefinitionCreate(name="Region", kind="text", scopes=("customer",), required=1)
    with pytest.raises(ValidationError):
        CustomFieldDefinitionCreate(name="Bank account number", kind="text", scopes=("vendor",))
    with pytest.raises(ValidationError):
        CustomFieldDefinitionCreate(name="Region", kind="text", scopes=("customer",), surprise=True)
    with pytest.raises(ValidationError):
        CustomFieldDefinitionCreate(name="Region", kind="text", scopes=("customer", "customer"))
    with pytest.raises(ValidationError):
        CustomFieldValuePatch.model_validate({"Region": "West"})


def test_create_applies_defaults_and_requires_only_missing_create_values(conn: sa.Connection) -> None:
    required = _definition(conn, name="Service region", required=True)
    defaulted = _definition(conn, name="Crew size", kind="number", default="2.500000000")
    optional = _definition(conn, name="Gate code note")
    owner = new_id()

    missing = _assert_code(
        "E_VALIDATION",
        lambda: plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={}, creating=True),
    )
    assert missing.details["fields"][0]["field"] == f"custom_fields.{required['id']}"

    plan = plan_owner_value_patch(
        conn,
        record_type="customer",
        record_id=owner,
        patch={required["id"]: "West"},
        creating=True,
    )
    assert [mutation.definition_id for mutation in plan.mutations] == sorted([required["id"], defaulted["id"]])
    assert {mutation.canonical_text for mutation in plan.mutations} == {"West", "2.5"}
    assert optional["id"] not in {mutation.definition_id for mutation in plan.mutations}
    apply_owner_value_plan(conn, plan)

    assert {value["definition_id"]: value["value"] for value in read_owner_values(conn, record_type="customer", record_id=owner)} == {
        required["id"]: "West",
        defaulted["id"]: "2.5",
    }
    assert not plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={}, creating=False).changed


def test_patch_absent_preserves_null_clears_and_set_reuses_stable_row(conn: sa.Connection) -> None:
    definition = _definition(conn, name="Route")
    owner = new_id()
    first = plan_owner_value_patch(
        conn, record_type="customer", record_id=owner, patch={definition["id"]: "North"}, creating=True
    )
    apply_owner_value_plan(conn, first)
    row_id = first.mutations[0].row_id

    assert not plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={}).changed
    clear = plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={definition["id"]: None})
    assert clear.logical_paths == (custom_field_logical_path(definition["id"]),)
    apply_owner_value_plan(conn, clear)
    assert read_owner_values(conn, record_type="customer", record_id=owner) == []

    restore = plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={definition["id"]: "South"})
    assert restore.mutations[0].operation == "update"
    assert restore.mutations[0].row_id == row_id
    apply_owner_value_plan(conn, restore)
    assert read_owner_values(conn, record_type="customer", record_id=owner)[0]["value"] == "South"


def test_required_existing_value_cannot_be_cleared_but_unrelated_patch_does_not_retrofail(conn: sa.Connection) -> None:
    definition = _definition(conn, name="Territory", required=True)
    later_required = _definition(conn, name="Later policy", required=True)
    owner = new_id()
    plan = plan_owner_value_patch(
        conn,
        record_type="customer",
        record_id=owner,
        patch={definition["id"]: "A", later_required["id"]: "B"},
        creating=True,
    )
    apply_owner_value_plan(conn, plan)
    _assert_code(
        "E_VALIDATION",
        lambda: plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={definition["id"]: None}),
    )
    assert not plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={}).changed


def test_rename_keeps_definition_id_value_row_and_logical_path(conn: sa.Connection) -> None:
    definition = _definition(conn, name="Old label")
    owner = new_id()
    plan = plan_owner_value_patch(
        conn, record_type="customer", record_id=owner, patch={definition["id"]: "value"}, creating=True
    )
    apply_owner_value_plan(conn, plan)
    path = plan.logical_paths[0]
    value_id = read_owner_values(conn, record_type="customer", record_id=owner)[0]["id"]

    renamed = update_definition(
        conn,
        definition["id"],
        CustomFieldDefinitionUpdate(name="New label"),
        actor_id=new_id(),
        interface="python",
        expected_version=1,
        at="2026-09-04T00:01:00Z",
    )
    shown = read_owner_values(conn, record_type="customer", record_id=owner)[0]
    assert renamed["id"] == definition["id"] and renamed["version"] == 2
    assert shown["id"] == value_id and shown["definition_id"] == definition["id"]
    assert shown["name"] == "New label" and shown["value"] == "value"
    assert custom_field_logical_path(definition["id"]) == path


def test_inactive_definition_preserves_readable_value_and_rejects_changes(conn: sa.Connection) -> None:
    definition = _definition(conn, name="District")
    owner = new_id()
    set_plan = plan_owner_value_patch(
        conn, record_type="customer", record_id=owner, patch={definition["id"]: "One"}, creating=True
    )
    apply_owner_value_plan(conn, set_plan)
    update_definition(
        conn,
        definition["id"],
        {"active": False},
        actor_id=new_id(),
        interface="python",
        at="2026-09-04T00:02:00Z",
    )

    preserved = read_owner_values(conn, record_type="customer", record_id=owner)[0]
    assert preserved["value"] == "One" and preserved["definition_active"] is False
    assert not plan_owner_value_patch(
        conn, record_type="customer", record_id=owner, patch={definition["id"]: "One"}
    ).changed
    _assert_code(
        "E_INACTIVE_REFERENCE",
        lambda: plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={definition["id"]: "Two"}),
    )
    _assert_code(
        "E_INACTIVE_REFERENCE",
        lambda: plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={definition["id"]: None}),
    )


def test_scope_name_collisions_and_post_value_kind_scope_freeze(conn: sa.Connection) -> None:
    definition = _definition(conn, name="Region", scopes=("customer", "vendor"))
    _assert_code("E_NAME_TAKEN", lambda: _definition(conn, name=" region ", scopes=("customer",)))
    owner = new_id()
    plan = plan_owner_value_patch(
        conn, record_type="customer", record_id=owner, patch={definition["id"]: "North"}, creating=True
    )
    apply_owner_value_plan(conn, plan)
    _assert_code(
        "E_RECORD_IN_USE",
        lambda: update_definition(
            conn, definition["id"], {"kind": "number"}, actor_id=new_id(), interface="python"
        ),
    )
    _assert_code(
        "E_RECORD_IN_USE",
        lambda: update_definition(
            conn, definition["id"], {"scopes": ["customer"]}, actor_id=new_id(), interface="python"
        ),
    )


def test_choice_values_are_canonical_and_in_use_choice_cannot_be_removed(conn: sa.Connection) -> None:
    north = CustomFieldChoiceInput(value="North")
    south = CustomFieldChoiceInput(value="South")
    definition = _definition(conn, name="Area", kind="choice", choices=(north, south))
    owner = new_id()
    plan = plan_owner_value_patch(
        conn, record_type="customer", record_id=owner, patch={definition["id"]: " north "}, creating=True
    )
    assert plan.mutations[0].canonical_text == "North"
    apply_owner_value_plan(conn, plan)

    _assert_code(
        "E_RECORD_IN_USE",
        lambda: update_definition(
            conn,
            definition["id"],
            {"choices": [{"id": south.id, "value": "South"}]},
            actor_id=new_id(),
            interface="python",
        ),
    )
    _assert_code(
        "E_VALIDATION",
        lambda: plan_owner_value_patch(conn, record_type="customer", record_id=owner, patch={definition["id"]: "East"}),
    )


def test_more_than_45_active_definitions_per_scope_are_supported(conn: sa.Connection) -> None:
    created = [_definition(conn, name=f"Employee field {index}", scopes=("employee",)) for index in range(46)]
    assert len(created) == 46
    assert conn.execute(
        sa.select(sa.func.count()).select_from(schema.custom_field_scopes).where(
            schema.custom_field_scopes.c.record_type == "employee",
            schema.custom_field_scopes.c.active.is_(True),
        )
    ).scalar_one() == 46


def test_employee_requirement_paths_accept_only_declared_or_active_employee_definition(conn: sa.Connection) -> None:
    employee = _definition(conn, name="License", scopes=("employee",))
    vendor = _definition(conn, name="Vendor zone", scopes=("vendor",))
    assert parse_employee_requirement_path(conn, "address.postal_code") == ("address", "postal_code")
    assert parse_employee_requirement_path(conn, f"custom_fields.{employee['id'].lower()}") == (
        "custom_fields",
        employee["id"],
    )
    assert validate_employee_requirement_groups(
        conn,
        [["first_name"], ["phone", "email"], [f"custom_fields.{employee['id']}"]],
    )[-1] == (("custom_fields", employee["id"]),)
    _assert_code(
        "E_RECORD_NOT_FOUND",
        lambda: parse_employee_requirement_path(conn, f"custom_fields.{vendor['id']}"),
    )
    _assert_code("E_VALIDATION", lambda: parse_employee_requirement_path(conn, "birth_date"))
    _assert_code("E_VALIDATION", lambda: parse_employee_requirement_path(conn, "custom_fields.not-an-id"))

    actor = new_id()
    conn.execute(
        schema.company_info.insert().values(
            id=new_id(),
            version=1,
            created_at="2026-09-04T00:00:00Z",
            created_by=actor,
            created_via="python",
            updated_at="2026-09-04T00:00:00Z",
            updated_by=actor,
            updated_via="python",
            legal_name="Example",
            display_name="Example",
            home_currency="USD",
            timezone="UTC",
            required_employee_profile_fields=json.dumps([[f"custom_fields.{employee['id']}"]]),
        )
    )
    _assert_code(
        "E_RECORD_IN_USE",
        lambda: update_definition(
            conn,
            employee["id"],
            {"scopes": ["vendor"]},
            actor_id=new_id(),
            interface="python",
        ),
    )
    _assert_code(
        "E_RECORD_IN_USE",
        lambda: update_definition(
            conn,
            employee["id"],
            {"active": False},
            actor_id=new_id(),
            interface="python",
        ),
    )
    conn.execute(schema.company_info.update().values(required_employee_profile_fields="[]"))
    update_definition(
        conn,
        employee["id"],
        {"active": False},
        actor_id=new_id(),
        interface="python",
    )
    _assert_code(
        "E_RECORD_NOT_FOUND",
        lambda: parse_employee_requirement_path(conn, f"custom_fields.{employee['id']}"),
    )


def test_unknown_definition_and_unsupported_transaction_values_are_rejected(conn: sa.Connection) -> None:
    _assert_code(
        "E_RECORD_NOT_FOUND",
        lambda: plan_owner_value_patch(
            conn, record_type="customer", record_id=new_id(), patch={new_id(): "x"}, creating=True
        ),
    )
    definition = _definition(conn, name="Estimate note", scopes=("estimate",))
    _assert_code(
        "E_VALIDATION",
        lambda: plan_owner_value_patch(
            conn, record_type="estimate", record_id=new_id(), patch={definition["id"]: "x"}, creating=True
        ),
    )


@pytest.mark.parametrize("record_type", ["invoice", "sales_receipt"])
def test_supported_sales_custom_values_round_trip(conn: sa.Connection, record_type: str) -> None:
    definition = _definition(conn, name="Sale note", scopes=(record_type,))
    owner = new_id()
    plan = plan_owner_value_patch(
        conn, record_type=record_type, record_id=owner,
        patch={definition["id"]: "x"}, creating=True,
    )
    assert plan.logical_paths == (custom_field_logical_path(definition["id"]),)
    apply_owner_value_plan(conn, plan)
    shown, = read_owner_values(conn, record_type=record_type, record_id=owner)
    assert shown["value"] == "x"
    other_type = "sales_receipt" if record_type == "invoice" else "invoice"
    _assert_code(
        "E_RECORD_NOT_FOUND",
        lambda: plan_owner_value_patch(
            conn, record_type=other_type, record_id=new_id(),
            patch={definition["id"]: "x"}, creating=True,
        ),
    )
