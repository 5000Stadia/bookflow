"""Transaction-owned value slots and immutable revision custom-field facts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

import sqlalchemy as sa
from pydantic import ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from bookflow.company import custom_fields as cf, schema as c
from bookflow.company.custom_fields import CustomFieldKind, CustomFieldValuePatch
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, normalize_ulid
from bookflow.core.models import StrictModel
from bookflow.core.registry import Touched
from bookflow.storage.engine import Database


class SnapshotField(StrictModel):
    """Captured typed facts, independent of current definition display text."""

    model_config = ConfigDict(strict=True, str_strip_whitespace=False)

    definition_id: StrictStr
    value_id: StrictStr
    name: StrictStr
    kind: CustomFieldKind
    value: StrictStr | StrictBool
    canonical_text: StrictStr
    definition_version: StrictInt = Field(ge=1)
    position: StrictInt = Field(ge=0)
    choice_id: StrictStr | None = None
    choice_label: StrictStr | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SnapshotField:
        for identifier in (self.definition_id, self.value_id, self.choice_id):
            if identifier is not None and (not is_ulid(identifier) or normalize_ulid(identifier) != identifier):
                raise ValueError("snapshot identifiers must be canonical ULIDs")
        try:
            decoded = cf.typed_value_from_canonical(self.kind, self.canonical_text)
            if type(decoded) is not type(self.value) or decoded != self.value:
                raise ValueError("value must decode canonical_text exactly")
            if self.kind != "bool" and decoded != self.canonical_text:
                raise ValueError("canonical_text must be canonical")
            if self.kind == "choice":
                if self.choice_id is None or self.choice_label is None:
                    raise ValueError("choice facts must include identity and label")
                canonical, key = cf._normalize_label(self.canonical_text, field="canonical_text")
                label, label_key = cf._normalize_label(self.choice_label, field="choice_label")
                if canonical != self.canonical_text or label != self.choice_label or key != label_key:
                    raise ValueError("choice label must identify the canonical value")
            elif self.choice_id is not None or self.choice_label is not None:
                raise ValueError("non-choice fields cannot carry choice facts")
        except BookflowError as exc:
            raise ValueError("invalid canonical snapshot value") from exc
        return self


@dataclass(frozen=True)
class JournalCustomFieldPlan:
    owner_plan: cf.OwnerCustomFieldPlan
    snapshot: dict[str, dict[str, Any]]
    # Serialized source facts do not alias the caller's mutable dictionaries.
    previous_json: str
    patch_json: str
    refresh: bool

    @property
    def changed(self) -> bool:
        return self.owner_plan.changed or self.snapshot != json.loads(self.previous_json)


def _slots(connection: sa.Connection, record_id: str, *, record_type: str) -> dict[str, dict[str, Any]]:
    return {row["def_id"]: dict(row) for row in connection.execute(
        sa.select(c.custom_field_values).where(
            c.custom_field_values.c.record_type == record_type,
            c.custom_field_values.c.record_id == record_id,
        )
    ).mappings()}


def _choice(connection: sa.Connection, definition_id: str, canonical: str, choice_id: str | None = None) -> dict:
    key = cf._normalize_label(canonical, field="canonical_text")[1]
    matches = [row for row in cf._choice_rows(connection, definition_id)
               if row["active"] and (row["id"] == choice_id if choice_id is not None else row["value_key"] == key)]
    if len(matches) != 1 or matches[0]["value_key"] != key:
        raise ValueError("snapshot choice no longer identifies the populated slot")
    return matches[0]


def _capture(connection: sa.Connection, slot: dict, previous: dict | None = None) -> dict:
    definition = cf._definition_row(connection, slot["def_id"])
    canonical = slot["canonical_text"]
    choice = _choice(connection, slot["def_id"], canonical,
                     previous["choice_id"] if previous and definition["kind"] == "choice" else None) if definition["kind"] == "choice" else None
    return SnapshotField(
        definition_id=slot["def_id"], value_id=slot["id"], name=definition["name"],
        kind=definition["kind"], value=cf.typed_value_from_canonical(definition["kind"], canonical),
        canonical_text=canonical, definition_version=definition["version"], position=definition["position"],
        choice_id=choice["id"] if choice else None, choice_label=choice["value"] if choice else None,
    ).model_dump(mode="json")


def _after(mutation: cf.CustomFieldValueMutation) -> dict:
    return dict(id=mutation.row_id, def_id=mutation.definition_id,
                record_type=mutation.record_type, record_id=mutation.record_id,
                active=mutation.active, canonical_text=mutation.canonical_text)



def validate_kinds(db, patch, expected, *, record_type: str = "journal_entry") -> None:
    """Check captured caller kinds against current company definitions."""
    if not expected.root:
        return
    definitions = {d["id"]: d for d in cf._applicable_definitions(cf._conn(db), record_type)}
    for key, kind in expected.root.items():
        if key not in patch.root or patch.root[key] is None:
            raise cf._validation(f"custom_field_kinds.{key}", "must accompany a supplied non-null custom value")
        if key not in definitions:
            raise cf._record_not_found(key)
        if definitions[key]["kind"] != kind:
            raise cf._validation(f"custom_fields.{key}", "field type changed; review the original attempt and explicitly use the current type or omit it")


def prepare(db: Database | sa.Connection, record_id: str, patch: CustomFieldValuePatch,
            previous_snapshot: dict, *, creating: bool, refresh: bool = False,
            record_type: str = "journal_entry") -> JournalCustomFieldPlan:
    """Plan stable slots and a complete revision snapshot without writing."""

    connection = cf._conn(db)
    owner = cf.plan_owner_value_patch(connection, record_type=record_type, record_id=record_id,
                                     patch=patch, creating=creating)
    try:
        previous_json = json.dumps(previous_snapshot, sort_keys=True, allow_nan=False)
        slots = _slots(connection, owner.record_id, record_type=record_type)
        changed = {mutation.definition_id for mutation in owner.mutations}
        for mutation in owner.mutations:
            slots[mutation.definition_id] = _after(mutation)
        snapshot = {}
        for definition_id, slot in slots.items():
            if not slot["active"]:
                continue
            previous = previous_snapshot.get(definition_id)
            if definition_id not in changed and previous is not None:
                snapshot[definition_id] = _capture(connection, slot, previous) if refresh else deepcopy(previous)
            else:
                snapshot[definition_id] = _capture(connection, slot)
        plan = JournalCustomFieldPlan(owner, snapshot, previous_json,
                                      json.dumps(patch.root, sort_keys=True, allow_nan=False), refresh)
        validate(connection, plan, owner.record_id, snapshot, record_type=record_type)
        return plan
    except (ValueError, TypeError, KeyError) as exc:
        raise BookflowError("E_INTERNAL", details={"problem": "invalid journal custom-field source facts"}) from exc


def validate(db: Database | sa.Connection, plan: JournalCustomFieldPlan,
             record_id: str, snapshot: dict, *, record_type: str = "journal_entry") -> None:
    """Check pending revision facts against stored slots and proposed changes."""

    try:
        _validate(cf._conn(db), plan, record_id, snapshot, record_type=record_type)
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError, BookflowError) as exc:
        raise BookflowError("E_INTERNAL", details={"problem": "invalid journal custom-field plan or snapshot"}) from exc


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("journal custom-field facts do not agree")


def _validate(connection: sa.Connection, plan: JournalCustomFieldPlan, record_id: str, snapshot: dict, *, record_type: str) -> None:
    owner = plan.owner_plan
    _require(type(owner.creating) is bool and type(plan.refresh) is bool)
    _require(record_type in {"journal_entry", "invoice", "sales_receipt", "payment", "proposal", "estimate", "work_order", "deposit", "bill", "credit_memo"})
    _require(owner.record_type == record_type and owner.record_id == record_id)
    _require(is_ulid(record_id) and normalize_ulid(record_id) == record_id)
    previous = json.loads(plan.previous_json)
    _require(isinstance(previous, dict) and isinstance(snapshot, dict) and snapshot == plan.snapshot)
    slots = _slots(connection, record_id, record_type=record_type)
    active_before = {key for key, slot in slots.items() if slot["active"]}
    _require(set(previous) == active_before)
    if owner.creating:
        _require(not slots and not previous)
    # When an aggregate exists, its selected immutable revision is the authority
    # for preserved labels, rather than the supplied preparation dictionary.
    if record_type in {"proposal", "estimate", "work_order"}:
        header_type = connection.execute(sa.select(c.work_documents.c.kind).where(
            c.work_documents.c.id == record_id)).scalar_one_or_none()
        if header_type is not None:
            _require(header_type == record_type)
        source = connection.execute(sa.select(c.work_revisions.c.custom_fields_snapshot).select_from(
            c.work_documents.join(c.work_revisions,
                                  c.work_documents.c.current_revision_id == c.work_revisions.c.id)
        ).where(c.work_documents.c.id == record_id)).scalar_one_or_none()
        if source is not None:
            _require(not owner.creating and json.loads(source) == previous)
    elif sa.inspect(connection).has_table("transactions"):
        header_type = connection.execute(sa.select(c.transactions.c.type).where(
            c.transactions.c.id == record_id)).scalar_one_or_none()
        if header_type is not None:
            _require(header_type == record_type)
        source = connection.execute(sa.select(c.transaction_revisions.c.custom_fields_snapshot).select_from(
            c.transactions.join(c.transaction_revisions,
                                c.transactions.c.current_revision_id == c.transaction_revisions.c.id)
        ).where(c.transactions.c.id == record_id)).scalar_one_or_none()
        if source is not None:
            _require(not owner.creating and json.loads(source) == previous)

    definitions = {row["id"]: row for row in cf._applicable_definitions(connection, record_type)}
    for key, raw in previous.items():
        field = SnapshotField.model_validate(raw)
        slot = slots[key]
        _require(key == field.definition_id and field.value_id == slot["id"]
                 and field.canonical_text == slot["canonical_text"])
        _require(key in definitions and definitions[key]["kind"] == field.kind)
        if field.kind == "choice":
            _choice(connection, key, field.canonical_text, field.choice_id)

    # Recheck the actual patch semantics, including omitted defaults/required,
    # inactive equality and stable reuse. Only new slot ids are supplied here.
    insert_by_definition = {m.definition_id: m.row_id for m in owner.mutations if m.operation == "insert"}
    insert_ids = iter(insert_by_definition[key] for key in definitions if key in insert_by_definition)
    expected = cf.plan_owner_value_patch(connection, record_type=record_type, record_id=record_id,
        patch=CustomFieldValuePatch.model_validate(json.loads(plan.patch_json)), creating=owner.creating,
        id_factory=lambda: next(insert_ids))
    _require(owner == expected)
    changed = set()
    used_ids = set()
    for mutation in owner.mutations:
        _require(type(mutation.active) is bool and type(mutation.canonical_text) is str)
        _require(mutation.before_active is None or type(mutation.before_active) is bool)
        _require(mutation.before_canonical_text is None or type(mutation.before_canonical_text) is str)
        _require(mutation.definition_id not in changed and mutation.row_id not in used_ids)
        changed.add(mutation.definition_id)
        used_ids.add(mutation.row_id)
        if mutation.operation == "insert":
            _require(connection.execute(sa.select(c.custom_field_values.c.id).where(
                c.custom_field_values.c.id == mutation.row_id)).first() is None)
        slots[mutation.definition_id] = _after(mutation)
    _require(set(snapshot) == {key for key, slot in slots.items() if slot["active"]})
    for key, raw in snapshot.items():
        field = SnapshotField.model_validate(raw)
        _require(set(raw) == set(SnapshotField.model_fields))
        slot = slots[key]
        _require(field.definition_id == key and field.value_id == slot["id"]
                 and field.canonical_text == slot["canonical_text"])
        definition = definitions[key]
        _require(field.kind == definition["kind"])
        if key not in changed and not plan.refresh:
            _require(raw == previous[key])
        else:
            _require(field.name == definition["name"] and field.position == definition["position"]
                     and field.definition_version == definition["version"])
            if key in changed:
                _require(bool(definition["active"]))
            elif key in previous:
                _require(field.choice_id == previous[key]["choice_id"])
            if field.kind == "choice":
                choice = _choice(connection, key, field.canonical_text, field.choice_id)
                _require(field.choice_label == choice["value"])


def touches(plan: JournalCustomFieldPlan) -> list[Touched]:
    """Safe nonversioned value-slot audit entries for the enclosing event."""

    result = []
    for mutation in plan.owner_plan.mutations:
        after = _after(mutation)
        before = None if mutation.operation == "insert" else {
            **after, "active": mutation.before_active, "canonical_text": mutation.before_canonical_text,
        }
        result.append(Touched("custom_field_value", mutation.row_id,
                              "create" if before is None else "update", None, None,
                              after, before, db="company"))
    return result


def apply(db: Database | sa.Connection, plan: JournalCustomFieldPlan) -> None:
    """Apply slots in the caller's existing company transaction."""

    cf.apply_owner_value_plan(db, plan.owner_plan)


def project(snapshot: dict) -> list[dict]:
    """Return typed captured facts in deterministic display order, without IO."""

    fields = [SnapshotField.model_validate(raw).model_dump(mode="json") for raw in snapshot.values()]
    return sorted(fields, key=lambda field: (field["position"], field["name"], field["definition_id"]))
