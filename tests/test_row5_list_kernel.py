"""Shared Row 5 list storage and lifecycle registration."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Literal

import pytest
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, ValidationError

from bookflow.commands.list_factory import (
    LIFECYCLE_VERBS,
    LifecycleCallbacks,
    LifecycleModels,
    ModelPair,
    register_lifecycle,
)
from bookflow.company import schema as c
from bookflow.company.list_service import (
    DependencyRegistry,
    DependentReference,
    aggregate_snapshot,
    assert_name_available,
    assert_no_floats,
    create_metadata,
    insert_row,
    list_rows,
    plan_activation,
    plan_deactivation,
    plan_reparent,
    reconcile_children,
    require_active_reference,
    resolve_selector,
    update_metadata,
)
from bookflow.company.lists import LIST_DEFINITIONS, hierarchy_projection, normalize_display_name
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, REGISTRY
from bookflow.core.session import Session
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import current_revision, migrate_to_head


ACTOR = "01J00000000000000000000000"


@pytest.fixture
def company_db(tmp_path: Path):
    path = tmp_path / "company.db"
    with open_database(path, writable=True, create=True) as db:
        assert migrate_to_head(db, "company", None) == (None, "co0012")
        assert current_revision(db) == "co0012"
        yield db


def _term(db, name: str, *, active: bool = True, due_days: int = 30) -> dict:
    display, key = normalize_display_name(name)
    values = {
        **create_metadata(ACTOR, "python"),
        "active": active,
        "seed_key": None,
        "name": display,
        "name_key": key,
        "kind": "standard",
        "due_days": due_days,
        "discount_days": None,
        "due_day_of_month": None,
        "due_next_month_if_within_days": None,
        "discount_day_of_month": None,
        "discount_percent_millionths": None,
    }
    return insert_row(db, c.terms, values)


def _class(db, name: str, parent: dict | None = None, *, active: bool = True) -> dict:
    display, key = normalize_display_name(name)
    record_id = new_id()
    depth = 1 if parent is None else parent["depth"] + 1
    full_name, full_name_key = hierarchy_projection(display, parent["full_name"] if parent else None, depth=depth)
    path = f"/{record_id}/" if parent is None else f"{parent['path']}{record_id}/"
    values = {
        **create_metadata(ACTOR, "python", record_id=record_id),
        "active": active,
        "seed_key": None,
        "name": display,
        "name_key": key,
        "parent_id": parent["id"] if parent else None,
        "full_name": full_name,
        "full_name_key": full_name_key,
        "depth": depth,
        "path": path,
    }
    return insert_row(db, c.classes, values)


def _error(call, *args, **kwargs) -> BookflowError:
    with pytest.raises(BookflowError) as caught:
        call(*args, **kwargs)
    return caught.value


def test_selector_is_id_first_then_canonical_name_and_keeps_hidden_ids_secret(company_db):
    visible = _term(company_db, "CAFÉ")
    secret = _term(company_db, "Hidden term")
    definition = LIST_DEFINITIONS["term"]

    assert resolve_selector(company_db, c.terms, definition, visible["id"])["id"] == visible["id"]
    assert resolve_selector(company_db, c.terms, definition, "  cafe\N{COMBINING ACUTE ACCENT} ")["id"] == visible["id"]

    hidden_clause = c.terms.c.id != secret["id"]
    hidden = _error(resolve_selector, company_db, c.terms, definition, secret["id"], visible=hidden_clause)
    absent = _error(resolve_selector, company_db, c.terms, definition, secret["id"], visible=sa.false())
    assert hidden.to_dict() == absent.to_dict() == {
        "code": "E_RECORD_NOT_FOUND",
        "message": "No such record.",
        "details": {"selector": secret["id"], "suggestions": []},
    }


def test_canonical_collision_is_rejected_before_storage(company_db):
    row = _term(company_db, "CAFÉ")
    error = _error(assert_name_available, company_db, c.terms, LIST_DEFINITIONS["term"], " cafe\N{COMBINING ACUTE ACCENT} ")
    assert error.code == "E_NAME_TAKEN"
    assert_name_available(company_db, c.terms, LIST_DEFINITIONS["term"], "CAFÉ", exclude_id=row["id"])


def test_list_search_escapes_wildcards_filters_strictly_and_orders_by_id(company_db):
    first = _term(company_db, "100%_safe", due_days=9)
    _term(company_db, "100XXsafe", due_days=9)
    _term(company_db, "Inactive", active=False, due_days=9)
    definition = LIST_DEFINITIONS["term"]

    literal = list_rows(company_db, c.terms, definition, query="%_")
    assert [row["id"] for row in literal] == [first["id"]]
    assert all(row["active"] for row in list_rows(company_db, c.terms, definition))
    assert {row["active"] for row in list_rows(company_db, c.terms, definition, include_inactive=True)} == {True, False}
    assert [row["name"] for row in list_rows(company_db, c.terms, definition, filters=("active=false",))] == ["Inactive"]
    ids = [row["id"] for row in list_rows(company_db, c.terms, definition, sort="kind")]
    assert ids == sorted(ids)
    assert list_rows(company_db, c.terms, definition, query="   ") == list_rows(company_db, c.terms, definition)

    assert _error(list_rows, company_db, c.terms, definition, filters=("active=yes",)).code == "E_LIST_FILTER"
    assert _error(list_rows, company_db, c.terms, definition, sort="not-a-sort").code == "E_LIST_FILTER"
    assert _error(list_rows, company_db, c.terms, definition, direction="sideways").code == "E_LIST_FILTER"


def test_reparent_projects_complete_subtree_and_rejects_cycle_and_depth_six(company_db):
    root = _class(company_db, "Root")
    child = _class(company_db, "Child", root)
    destination = _class(company_db, "Destination")
    definition = LIST_DEFINITIONS["class"]

    plan = plan_reparent(company_db, c.classes, definition, root, parent_id=destination["id"], name="Renamed")
    assert [(item.full_name, item.depth) for item in plan.updates] == [
        ("Destination:Renamed", 2),
        ("Destination:Renamed:Child", 3),
    ]
    assert plan.affected_descendant_ids == (child["id"],)
    assert _error(plan_reparent, company_db, c.classes, definition, root, parent_id=child["id"]).code == "E_HIERARCHY_CYCLE"

    deep_root = _class(company_db, "One")
    level2 = _class(company_db, "Two", deep_root)
    level3 = _class(company_db, "Three", level2)
    level4 = _class(company_db, "Four", level3)
    moving = _class(company_db, "Moving")
    _class(company_db, "Moving child", moving)
    assert _error(plan_reparent, company_db, c.classes, definition, moving, parent_id=level4["id"]).code == "E_HIERARCHY_DEPTH"


def test_activation_deactivation_and_dependencies_are_planned_without_cross_list_cascade(company_db):
    root = _class(company_db, "Root")
    child = _class(company_db, "Child", root)
    inactive = _class(company_db, "Inactive", active=False)

    assert _error(plan_deactivation, company_db, c.classes, root).code == "E_ACTIVE_DEPENDENTS"
    assert [row["id"] for row in plan_deactivation(company_db, c.classes, root, cascade=True)] == [root["id"], child["id"]]
    assert plan_activation(company_db, c.classes, inactive) == (inactive,)

    dependencies = DependencyRegistry()
    dependencies.register("class", DependentReference("class", c.classes, "parent_id"))
    in_use = _error(dependencies.require_unused, company_db, "class", root["id"])
    assert in_use.code == "E_RECORD_IN_USE"
    assert in_use.details["dependents"] == [{"record_type": "class", "count": 1}]


def test_reference_validation_distinguishes_missing_and_inactive_without_values(company_db):
    active = _term(company_db, "Active")
    inactive = _term(company_db, "Inactive", active=False)
    assert require_active_reference(company_db, c.terms, active["id"], field="terms_id", record_type="term") == active
    missing = _error(require_active_reference, company_db, c.terms, new_id(), field="terms_id", record_type="term")
    assert missing.code == "E_RECORD_NOT_FOUND" and missing.details["suggestions"] == []
    hidden = _error(require_active_reference, company_db, c.terms, inactive["id"], field="terms_id", record_type="term")
    assert hidden.code == "E_INACTIVE_REFERENCE"
    assert "name" not in hidden.details


def test_provenance_helpers_and_float_guard_do_not_coerce_exact_values(company_db):
    row = _term(company_db, "Net 15")
    created = create_metadata(ACTOR, "cli", record_id=row["id"], at="2026-09-04T00:00:00.000Z")
    assert created["version"] == 1 and created["created_at"] == created["updated_at"]
    updated = update_metadata(row, ACTOR, "http", version=7, at="2026-09-04T01:00:00.000Z")
    assert updated == {
        "version": 7,
        "updated_at": "2026-09-04T01:00:00.000Z",
        "updated_by": ACTOR,
        "updated_via": "http",
    }
    assert_no_floats({"minor_units": 105, "active": True})
    error = _error(assert_no_floats, {"money": {"amount": 1.05}})
    assert error.code == "E_VALIDATION"
    assert error.details["fields"][0]["field"] == "input.money.amount"


def test_child_reconciliation_preserves_ids_retires_omissions_and_snapshots_deterministically():
    kept_id, retired_id = new_id(), new_id()
    existing = [
        {"id": kept_id, "position": 0, "active": True, "label": "Work"},
        {"id": retired_id, "position": 1, "active": True, "label": "Home"},
    ]
    result = reconcile_children(existing, [{"id": kept_id, "label": "Office"}, {"label": "Mobile"}], semantic_key="label")
    assert result.retained_ids == (kept_id,)
    assert len(result.created_ids) == 1
    assert [(row["label"], row["position"]) for row in result.active] == [("Office", 0), ("Mobile", 1)]
    assert result.retired == ({"id": retired_id, "position": 1, "active": False, "label": "Home"},)
    assert result.inactive == ()
    snapshot = aggregate_snapshot({"id": "owner"}, collections={"contacts": result.all_rows})
    assert [row["label"] for row in snapshot["contacts"]] == ["Office", "Mobile"]
    assert {"id": retired_id, "active": False} in snapshot["_contacts_identities"]
    assert _error(reconcile_children, result.all_rows, [{"id": retired_id, "label": "Home"}]).code == "E_VALIDATION"
    next_result = reconcile_children(result.all_rows, [{"id": kept_id, "label": "Office"}])
    assert any(row["id"] == retired_id for row in next_result.inactive)
    assert _error(reconcile_children, existing, [{"label": "x"}, {"label": "X"}], semantic_key="label").code == "E_VALIDATION"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Create(_Strict):
    name: str


class _Select(_Strict):
    term: str


class _Update(_Select):
    expected_version: int | None = None
    name: str | None = None


class _List(_Strict):
    query: str | None = None
    include_inactive: bool = False
    filter: tuple[str, ...] = ()
    sort: str | None = None
    direction: Literal["asc", "desc"] = "asc"


class _Lifecycle(_Select):
    expected_version: int | None = None


class _Output(_Strict):
    version: int = 1
    changed: bool = True


def _plan(inp: BaseModel, ctx: Context, session: Session) -> Plan:
    return Plan(preview=_Output())


def _apply(plan: Plan, ctx: Context, session: Session) -> Applied:
    return Applied(output=plan.preview, touched=[], summary="fixture")


def test_factory_registers_exact_lifecycle_with_domain_callbacks_and_pipeline_metadata():
    names = {f"term {verb}" for verb in LIFECYCLE_VERBS}
    original = {name: REGISTRY.pop(name) for name in names if name in REGISTRY}
    models = LifecycleModels(
        create=ModelPair(_Create, _Output),
        update=ModelPair(_Update, _Output),
        show=ModelPair(_Select, _Output),
        list=ModelPair(_List, _Output),
        activate=ModelPair(_Lifecycle, _Output),
        deactivate=ModelPair(_Lifecycle, _Output),
    )
    callbacks = LifecycleCallbacks(
        create_plan=_plan,
        create_apply=_apply,
        update_plan=_plan,
        update_apply=_apply,
        show_plan=_plan,
        list_plan=_plan,
        activate_plan=_plan,
        activate_apply=_apply,
        deactivate_plan=_plan,
        deactivate_apply=_apply,
    )
    try:
        commands = register_lifecycle("term", models=models, callbacks=callbacks, selector_field="term")
        assert {command.name for command in commands.as_tuple()} == names
        assert commands.create.accepts_idempotency_key
        assert commands.create.required_role == "standard"
        assert commands.show.required_role == commands.list.required_role == "member"
        assert commands.update.clearable
        assert commands.update.version_source == ("term show", "term", "version")
        assert commands.show.plan is _plan and commands.update.apply is _apply
        assert commands.show.writes == frozenset()
        assert commands.deactivate.writes == frozenset({"company"})
        with pytest.raises(ValidationError):
            commands.update.input_model.model_validate({"term": "Net 30", "expected_version": 1.0})
    finally:
        for name in names:
            REGISTRY.pop(name, None)
        REGISTRY.update(original)


def test_factory_import_does_not_eagerly_import_any_noun_service():
    noun_modules = [
        "bookflow.company.accounts",
        "bookflow.company.parties",
        "bookflow.company.items",
        "bookflow.company.profiles",
        "bookflow.company.custom_fields",
    ]
    program = (
        "import json,sys; import bookflow.commands.list_factory; "
        f"print(json.dumps([name for name in {noun_modules!r} if name in sys.modules]))"
    )
    result = subprocess.run([sys.executable, "-c", program], check=True, text=True, capture_output=True)
    assert json.loads(result.stdout) == []
