"""Typed runtime custom fields for Row 5 company master data.

Definitions are ordinary versioned list records.  Values are owned by their
record aggregate: this module plans value-row mutations and reports logical
paths, while the owning service remains responsible for owner concurrency and
the enclosing audit event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import re
import unicodedata
from typing import Any, Callable, Literal, Mapping

import sqlalchemy as sa
from pydantic import Field, RootModel, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from bookflow.company import schema as c
from bookflow.company.lists import normalize_display_name
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_custom_number_nano_units, parse_custom_number_nano_units
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.models import StrictModel
from bookflow.core.session import now_iso
from bookflow.storage.engine import Database


CustomFieldKind = Literal["text", "number", "date", "bool", "choice"]
CustomFieldScope = Literal[
    "customer",
    "vendor",
    "employee",
    "other_name",
    "item",
    "journal_entry",
    "invoice",
    "sales_receipt",
    "credit_memo",
    "payment",
    "deposit",
    "bill",
    "bill_payment",
    "check",
    "credit_card_charge",
    "transfer",
    "inventory_adjustment",
    "vendor_credit",
    "estimate",
    "sales_order",
    "purchase_order",
    "item_receipt",
    "statement",
]

LIST_VALUE_SCOPES = frozenset({"customer", "vendor", "employee", "other_name", "item"})
TRANSACTION_SCOPES = frozenset(
    {
        "journal_entry",
        "invoice",
        "sales_receipt",
        "credit_memo",
        "payment",
        "deposit",
        "bill",
        "bill_payment",
        "check",
        "credit_card_charge",
        "transfer",
        "inventory_adjustment",
        "vendor_credit",
        "estimate",
        "sales_order",
        "purchase_order",
        "item_receipt",
        "statement",
    }
)

EMPLOYEE_BUILTIN_REQUIREMENT_PATHS = frozenset(
    {
        "name",
        "salutation",
        "first_name",
        "middle_name",
        "last_name",
        "job_title",
        "print_name_on_check_as",
        "employment_type",
        "phone",
        "email",
        "hire_date",
        "release_date",
        "emergency_contact_name",
        "emergency_contact_relationship",
        "emergency_contact_phone",
        "emergency_contact_email",
        "default_class_id",
        "notes",
        "tax_id_last4",
        "address.line1",
        "address.line2",
        "address.city",
        "address.state",
        "address.postal_code",
        "address.country",
    }
)

_SECRET_NAME_RE = re.compile(
    r"(?:password|passcode|secret|api[ _-]?key|access[ _-]?token|routing[ _-]?number|"
    r"bank[ _-]?account|social[ _-]?security|\bssn\b|tax[ _-]?(?:id|identifier)|"
    r"direct[ _-]?deposit|payroll|withholding|medical|health)",
    re.IGNORECASE,
)


def _validation(field: str, problem: str, **details: Any) -> BookflowError:
    return BookflowError(
        "E_VALIDATION",
        details={"fields": [{"field": field, "problem": problem}], **details},
    )


def _record_not_found(definition_id: str) -> BookflowError:
    return BookflowError(
        "E_RECORD_NOT_FOUND",
        details={"record_type": "custom_field", "record_id": definition_id, "suggestions": []},
    )


def _inactive(definition_id: str) -> BookflowError:
    return BookflowError(
        "E_INACTIVE_REFERENCE",
        details={"record_type": "custom_field", "record_id": definition_id},
    )


def _in_use(definition_id: str, problem: str, **details: Any) -> BookflowError:
    return BookflowError(
        "E_RECORD_IN_USE",
        details={"record_type": "custom_field", "record_id": definition_id, "problem": problem, **details},
    )


def _conn(db: Database | sa.Connection) -> sa.Connection:
    return db.conn if isinstance(db, Database) else db


def _stable_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not is_ulid(value):
        raise _validation(field, "must be a ULID")
    return normalize_ulid(value)


def _normalize_label(value: Any, *, field: str, maximum: int = 200) -> tuple[str, str]:
    if not isinstance(value, str):
        raise _validation(field, "must be text")
    display = unicodedata.normalize("NFC", value.strip())
    if not display:
        raise _validation(field, "must not be empty")
    key = unicodedata.normalize("NFC", display.casefold())
    if len(display) > maximum or len(key) > maximum * 2:
        raise _validation(field, f"must be at most {maximum} characters")
    return display, key


def _model_stable_id(value: str, *, field: str) -> str:
    if not is_ulid(value):
        raise ValueError(f"{field} must be a ULID")
    return normalize_ulid(value)


def _model_definition_name(value: str) -> str:
    try:
        display = normalize_display_name(value)[0]
    except BookflowError as exc:
        raise ValueError(exc.details["fields"][0]["problem"]) from exc
    if _SECRET_NAME_RE.search(display):
        raise ValueError("custom fields cannot be used for credentials, tax identifiers, health, or payroll secrets")
    return display


def _model_choice_label(value: str) -> str:
    try:
        return _normalize_label(value, field="choices.value")[0]
    except BookflowError as exc:
        raise ValueError(exc.details["fields"][0]["problem"]) from exc


class CustomFieldChoiceInput(StrictModel):
    """One stable ordered choice in a choice definition."""

    id: StrictStr = Field(default_factory=new_id, json_schema_extra={"generated": True})
    value: StrictStr
    active: StrictBool = True

    @field_validator("id")
    @classmethod
    def _id_is_stable(cls, value: str) -> str:
        return _model_stable_id(value, field="choices.id")

    @field_validator("value")
    @classmethod
    def _value_is_label(cls, value: str) -> str:
        return _model_choice_label(value)


class CustomFieldDefinitionCreate(StrictModel):
    """Strict definition-create input independent of a command adapter."""

    id: StrictStr = Field(default_factory=new_id, json_schema_extra={"generated": True})
    name: StrictStr
    kind: CustomFieldKind
    scopes: tuple[CustomFieldScope, ...] = Field(min_length=1)
    choices: tuple[CustomFieldChoiceInput, ...] = ()
    position: StrictInt = Field(default=0, ge=0)
    required: StrictBool = False
    default: Any | None = None
    active: StrictBool = True

    @field_validator("id")
    @classmethod
    def _id_is_stable(cls, value: str) -> str:
        return _model_stable_id(value, field="id")

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str) -> str:
        return _model_definition_name(value)

    @field_validator("scopes")
    @classmethod
    def _scopes_are_unique(cls, values: tuple[CustomFieldScope, ...]) -> tuple[CustomFieldScope, ...]:
        if len(set(values)) != len(values):
            raise ValueError("scopes must be unique")
        return values

    @model_validator(mode="after")
    def _kind_has_valid_choices(self) -> CustomFieldDefinitionCreate:
        _validate_choice_collection(self.kind, self.choices)
        return self


class CustomFieldDefinitionUpdate(StrictModel):
    """Patch input; ``model_fields_set`` distinguishes absent from explicit null."""

    name: StrictStr | None = None
    kind: CustomFieldKind | None = None
    scopes: tuple[CustomFieldScope, ...] | None = None
    choices: tuple[CustomFieldChoiceInput, ...] | None = None
    position: StrictInt | None = Field(default=None, ge=0)
    required: StrictBool | None = None
    default: Any | None = None
    active: StrictBool | None = None

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _model_definition_name(value)

    @field_validator("scopes")
    @classmethod
    def _scopes_are_unique(cls, values: tuple[CustomFieldScope, ...] | None) -> tuple[CustomFieldScope, ...] | None:
        if values is not None and (not values or len(set(values)) != len(values)):
            raise ValueError("scopes must be a nonempty unique sequence")
        return values


class CustomFieldValuePatch(RootModel[dict[str, Any | None]]):
    """Owner-value patch keyed only by stable definition ids."""

    @model_validator(mode="before")
    @classmethod
    def _keys_are_stable_ids(cls, value: Any) -> dict[str, Any | None]:
        if not isinstance(value, dict):
            raise ValueError("custom_fields must be an object")
        normalized: dict[str, Any | None] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not is_ulid(raw_key):
                raise ValueError("custom_fields keys must be definition ULIDs")
            key = normalize_ulid(raw_key)
            if key in normalized:
                raise ValueError("custom_fields contains the same definition id more than once")
            normalized[key] = raw_value
        return normalized


def _validate_choice_collection(
    kind: CustomFieldKind,
    choices: tuple[CustomFieldChoiceInput, ...],
) -> None:
    if kind != "choice" and choices:
        raise ValueError("choices are accepted only for choice fields")
    if kind == "choice" and not any(choice.active for choice in choices):
        raise ValueError("a choice field needs at least one active choice")
    ids = [choice.id for choice in choices]
    keys = [_normalize_label(choice.value, field="choices.value")[1] for choice in choices if choice.active]
    if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
        raise ValueError("choice ids and active values must be unique")


def custom_field_logical_path(definition_id: str) -> tuple[str, str]:
    """Return the structured owner/audit path for one stable definition id."""

    return ("custom_fields", _stable_id(definition_id, field="definition_id"))


def parse_typed_value(
    kind: CustomFieldKind,
    value: Any,
    *,
    field: str = "value",
    choices: Mapping[str, str] | None = None,
) -> tuple[str, Any]:
    """Validate a typed wire value and return canonical storage and output."""

    if kind == "number":
        coefficient = parse_custom_number_nano_units(value, field=field)
        canonical = format_custom_number_nano_units(coefficient, field=field)
        return canonical, canonical
    if kind == "bool":
        if type(value) is not bool:
            raise _validation(field, "must be a JSON boolean")
        canonical = "true" if value else "false"
        return canonical, value
    if kind == "date":
        if not isinstance(value, str):
            raise _validation(field, "must be an ISO date string")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise _validation(field, "must be an ISO date in YYYY-MM-DD form") from exc
        canonical = parsed.isoformat()
        if canonical != value:
            raise _validation(field, "must be a canonical ISO date in YYYY-MM-DD form")
        return canonical, canonical
    if kind == "text":
        if not isinstance(value, str):
            raise _validation(field, "must be text")
        return value, value
    if kind == "choice":
        if not isinstance(value, str):
            raise _validation(field, "must be a choice label")
        _display, key = _normalize_label(value, field=field)
        available = choices or {}
        if key not in available:
            raise _validation(field, "must name an active declared choice")
        canonical = available[key]
        return canonical, canonical
    raise _validation(field, "has an unsupported custom-field kind")


def typed_value_from_canonical(kind: CustomFieldKind, canonical_text: str) -> Any:
    """Decode canonical database text without accepting a looser wire form."""

    if kind == "number":
        coefficient = parse_custom_number_nano_units(canonical_text)
        return format_custom_number_nano_units(coefficient)
    if kind == "bool":
        if canonical_text == "true":
            return True
        if canonical_text == "false":
            return False
        raise _validation("canonical_text", "contains a noncanonical boolean")
    if kind == "date":
        return parse_typed_value("date", canonical_text, field="canonical_text")[1]
    if kind in {"text", "choice"}:
        return canonical_text
    raise _validation("kind", "has an unsupported custom-field kind")


def _definition_row(connection: sa.Connection, definition_id: str) -> dict[str, Any]:
    row = connection.execute(
        sa.select(c.custom_field_defs).where(c.custom_field_defs.c.id == definition_id)
    ).mappings().first()
    if row is None:
        raise _record_not_found(definition_id)
    return dict(row)


def _scope_rows(connection: sa.Connection, definition_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        sa.select(c.custom_field_scopes)
        .where(c.custom_field_scopes.c.definition_id == definition_id)
        .order_by(c.custom_field_scopes.c.position, c.custom_field_scopes.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def _choice_rows(connection: sa.Connection, definition_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        sa.select(c.custom_field_choices)
        .where(c.custom_field_choices.c.definition_id == definition_id)
        .order_by(c.custom_field_choices.c.position, c.custom_field_choices.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def read_definition(db: Database | sa.Connection, definition_id: str) -> dict[str, Any]:
    """Return one definition with deterministic scope and choice order."""

    connection = _conn(db)
    stable_id = _stable_id(definition_id, field="definition_id")
    row = _definition_row(connection, stable_id)
    scopes = _scope_rows(connection, stable_id)
    choices = _choice_rows(connection, stable_id)
    result = dict(row)
    result["scopes"] = [
        {"id": item["id"], "record_type": item["record_type"], "position": item["position"], "active": bool(item["active"])}
        for item in scopes
        if item["active"]
    ]
    result["choices"] = [
        {"id": item["id"], "value": item["value"], "position": item["position"], "active": bool(item["active"])}
        for item in choices
        if item["active"]
    ]
    result["default"] = (
        None
        if row["default_canonical_text"] is None
        else typed_value_from_canonical(row["kind"], row["default_canonical_text"])
    )
    return result


def _active_choice_map(connection: sa.Connection, definition_id: str) -> dict[str, str]:
    rows = connection.execute(
        sa.select(c.custom_field_choices.c.value, c.custom_field_choices.c.value_key).where(
            c.custom_field_choices.c.definition_id == definition_id,
            c.custom_field_choices.c.active.is_(True),
        )
    ).all()
    return {row.value_key: row.value for row in rows}


def _check_name_collision(
    connection: sa.Connection,
    *,
    name_key: str,
    scopes: tuple[str, ...],
    excluding: str | None = None,
) -> None:
    query = sa.select(c.custom_field_scopes.c.definition_id, c.custom_field_scopes.c.record_type).where(
        c.custom_field_scopes.c.definition_name_key == name_key,
        c.custom_field_scopes.c.record_type.in_(scopes),
        c.custom_field_scopes.c.active.is_(True),
        c.custom_field_scopes.c.definition_active.is_(True),
    )
    if excluding is not None:
        query = query.where(c.custom_field_scopes.c.definition_id != excluding)
    collision = connection.execute(query.order_by(c.custom_field_scopes.c.record_type)).first()
    if collision:
        raise BookflowError(
            "E_NAME_TAKEN",
            details={"record_type": collision.record_type, "name_key": name_key},
        )
    global_query = sa.select(c.custom_field_defs.c.id).where(c.custom_field_defs.c.name_key == name_key)
    if excluding is not None:
        global_query = global_query.where(c.custom_field_defs.c.id != excluding)
    if connection.execute(global_query).first():
        raise BookflowError("E_NAME_TAKEN", details={"record_type": "custom_field", "name_key": name_key})


def _canonical_default(
    connection: sa.Connection,
    *,
    definition_id: str,
    kind: CustomFieldKind,
    value: Any | None,
    pending_choices: tuple[CustomFieldChoiceInput, ...] | None = None,
) -> str | None:
    if value is None:
        return None
    if pending_choices is None:
        choices = _active_choice_map(connection, definition_id) if kind == "choice" else None
    else:
        choices = {
            _normalize_label(choice.value, field="choices.value")[1]: choice.value
            for choice in pending_choices
            if choice.active
        }
    return parse_typed_value(kind, value, field="default", choices=choices)[0]


def create_definition(
    db: Database | sa.Connection,
    definition: CustomFieldDefinitionCreate | Mapping[str, Any],
    *,
    actor_id: str,
    interface: str,
    at: str | None = None,
    id_factory: Callable[[], str] = new_id,
) -> dict[str, Any]:
    """Validate and insert one definition plus its owned scope/choice rows."""

    model = definition if isinstance(definition, CustomFieldDefinitionCreate) else CustomFieldDefinitionCreate.model_validate(definition)
    connection = _conn(db)
    timestamp = at or now_iso()
    name, name_key = normalize_display_name(model.name)
    _check_name_collision(connection, name_key=name_key, scopes=model.scopes)
    default = _canonical_default(
        connection,
        definition_id=model.id,
        kind=model.kind,
        value=model.default,
        pending_choices=model.choices,
    )
    row = {
        "id": model.id,
        "version": 1,
        "created_at": timestamp,
        "created_by": actor_id,
        "created_via": interface,
        "updated_at": timestamp,
        "updated_by": actor_id,
        "updated_via": interface,
        "active": model.active,
        "seed_key": None,
        "name": name,
        "name_key": name_key,
        "kind": model.kind,
        "position": model.position,
        "required": model.required,
        "default_canonical_text": default,
    }
    connection.execute(c.custom_field_defs.insert().values(**row))
    for position, record_type in enumerate(model.scopes):
        connection.execute(
            c.custom_field_scopes.insert().values(
                id=_stable_id(id_factory(), field="scopes.id"),
                definition_id=model.id,
                position=position,
                active=True,
                record_type=record_type,
                definition_name=name,
                definition_name_key=name_key,
                definition_active=model.active,
            )
        )
    for position, choice in enumerate(model.choices):
        value, value_key = _normalize_label(choice.value, field="choices.value")
        connection.execute(
            c.custom_field_choices.insert().values(
                id=choice.id,
                definition_id=model.id,
                position=position,
                active=choice.active,
                value=value,
                value_key=value_key,
            )
        )
    return read_definition(connection, model.id)


def _referenced_by_employee_requirements(connection: sa.Connection, definition_id: str) -> bool:
    row = connection.execute(sa.select(c.company_info.c.required_employee_profile_fields)).first()
    if not row or not row[0]:
        return False
    try:
        requirements = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return False
    target = f"custom_fields.{definition_id}"
    return any(target in group for group in requirements if isinstance(group, list))


def _has_any_value(connection: sa.Connection, definition_id: str) -> bool:
    return connection.execute(
        sa.select(c.custom_field_values.c.id).where(c.custom_field_values.c.def_id == definition_id).limit(1)
    ).first() is not None


def update_definition(
    db: Database | sa.Connection,
    definition_id: str,
    patch: CustomFieldDefinitionUpdate | Mapping[str, Any],
    *,
    actor_id: str,
    interface: str,
    expected_version: int | None = None,
    at: str | None = None,
    id_factory: Callable[[], str] = new_id,
) -> dict[str, Any]:
    """Apply one definition patch while preserving stable children and values."""

    model = patch if isinstance(patch, CustomFieldDefinitionUpdate) else CustomFieldDefinitionUpdate.model_validate(patch)
    connection = _conn(db)
    stable_id = _stable_id(definition_id, field="definition_id")
    current = _definition_row(connection, stable_id)
    if expected_version is not None and expected_version != current["version"]:
        raise BookflowError(
            "E_VERSION_CONFLICT",
            details={"record_type": "custom_field", "record_id": stable_id, "expected_version": expected_version, "current_version": current["version"]},
        )

    fields_set = model.model_fields_set
    current_scopes = _scope_rows(connection, stable_id)
    old_scope_types = tuple(row["record_type"] for row in current_scopes if row["active"])
    new_scopes = model.scopes if "scopes" in fields_set else old_scope_types
    if new_scopes is None:
        raise _validation("scopes", "must not be null")
    new_name = model.name if "name" in fields_set else current["name"]
    if new_name is None:
        raise _validation("name", "must not be null")
    name, name_key = normalize_display_name(new_name)
    new_kind = model.kind if "kind" in fields_set else current["kind"]
    if new_kind is None:
        raise _validation("kind", "must not be null")
    has_values = _has_any_value(connection, stable_id)
    if has_values and (new_kind != current["kind"] or set(new_scopes) != set(old_scope_types)):
        raise _in_use(stable_id, "kind and scopes are immutable after the first value")
    _check_name_collision(connection, name_key=name_key, scopes=new_scopes, excluding=stable_id)

    old_choices = _choice_rows(connection, stable_id)
    if "choices" in fields_set and model.choices is None:
        raise _validation("choices", "must not be null")
    choice_models = model.choices if "choices" in fields_set else tuple(
        CustomFieldChoiceInput(id=row["id"], value=row["value"], active=bool(row["active"]))
        for row in old_choices
    )
    assert choice_models is not None
    try:
        _validate_choice_collection(new_kind, choice_models)
    except ValueError as exc:
        raise _validation("choices", str(exc)) from exc
    by_id = {row["id"]: row for row in old_choices}
    submitted_ids = {choice.id for choice in choice_models}
    for choice in choice_models:
        old = by_id.get(choice.id)
        if old is None:
            other = connection.execute(
                sa.select(c.custom_field_choices.c.definition_id).where(c.custom_field_choices.c.id == choice.id)
            ).first()
            if other is not None:
                raise _validation("choices.id", "belongs to another definition")
            continue
        old_is_retired = not choice.active or _normalize_label(choice.value, field="choices.value")[1] != old["value_key"]
        if old_is_retired and connection.execute(
            sa.select(c.custom_field_values.c.id).where(
                c.custom_field_values.c.def_id == stable_id,
                c.custom_field_values.c.active.is_(True),
                c.custom_field_values.c.canonical_text == old["value"],
            ).limit(1)
        ).first():
            raise _in_use(stable_id, "an active value uses the choice", choice_id=old["id"], choice=old["value"])
    for old in old_choices:
        if old["active"] and old["id"] not in submitted_ids and connection.execute(
            sa.select(c.custom_field_values.c.id).where(
                c.custom_field_values.c.def_id == stable_id,
                c.custom_field_values.c.active.is_(True),
                c.custom_field_values.c.canonical_text == old["value"],
            ).limit(1)
        ).first():
            raise _in_use(stable_id, "an active value uses the choice", choice_id=old["id"], choice=old["value"])

    new_active = model.active if "active" in fields_set else bool(current["active"])
    if new_active is None:
        raise _validation("active", "must not be null")
    requirement_would_break = not new_active or ("employee" in old_scope_types and "employee" not in new_scopes)
    if requirement_would_break and _referenced_by_employee_requirements(connection, stable_id):
        raise _in_use(stable_id, "the employee profile requirements reference this definition")
    new_position = model.position if "position" in fields_set else current["position"]
    new_required = model.required if "required" in fields_set else bool(current["required"])
    if new_position is None or new_required is None:
        raise _validation("definition", "position and required must not be null")

    if "default" in fields_set:
        default = _canonical_default(
            connection,
            definition_id=stable_id,
            kind=new_kind,
            value=model.default,
            pending_choices=choice_models,
        )
    elif new_kind != current["kind"]:
        default = None
    else:
        default = current["default_canonical_text"]

    desired = {
        "name": name,
        "name_key": name_key,
        "kind": new_kind,
        "position": new_position,
        "required": new_required,
        "default_canonical_text": default,
        "active": new_active,
    }
    scopes_changed = tuple(new_scopes) != old_scope_types or name != current["name"] or bool(new_active) != bool(current["active"])
    choices_changed = [
        (choice.id, choice.value, choice.active, position)
        for position, choice in enumerate(choice_models)
    ] != [
        (row["id"], row["value"], bool(row["active"]), row["position"])
        for row in old_choices
    ]
    scalar_changed = any(current[key] != value for key, value in desired.items())
    if not (scopes_changed or choices_changed or scalar_changed):
        return read_definition(connection, stable_id)

    timestamp = at or now_iso()
    connection.execute(
        c.custom_field_defs.update().where(c.custom_field_defs.c.id == stable_id).values(
            **desired,
            version=current["version"] + 1,
            updated_at=timestamp,
            updated_by=actor_id,
            updated_via=interface,
        )
    )
    existing_scope_by_type = {row["record_type"]: row for row in current_scopes}
    for row in current_scopes:
        if row["record_type"] not in new_scopes and row["active"]:
            connection.execute(c.custom_field_scopes.update().where(c.custom_field_scopes.c.id == row["id"]).values(active=False))
    for position, record_type in enumerate(new_scopes):
        old = existing_scope_by_type.get(record_type)
        values = {
            "position": position,
            "active": True,
            "definition_name": name,
            "definition_name_key": name_key,
            "definition_active": new_active,
        }
        if old:
            connection.execute(c.custom_field_scopes.update().where(c.custom_field_scopes.c.id == old["id"]).values(**values))
        else:
            connection.execute(
                c.custom_field_scopes.insert().values(
                    id=_stable_id(id_factory(), field="scopes.id"),
                    definition_id=stable_id,
                    record_type=record_type,
                    **values,
                )
            )
    # Inactive scope projections still track a rename/definition deactivation.
    connection.execute(
        c.custom_field_scopes.update().where(c.custom_field_scopes.c.definition_id == stable_id).values(
            definition_name=name,
            definition_name_key=name_key,
            definition_active=new_active,
        )
    )

    for old in old_choices:
        if old["id"] not in submitted_ids:
            connection.execute(c.custom_field_choices.update().where(c.custom_field_choices.c.id == old["id"]).values(active=False))
    for position, choice in enumerate(choice_models):
        value, value_key = _normalize_label(choice.value, field="choices.value")
        if choice.id in by_id:
            connection.execute(
                c.custom_field_choices.update().where(c.custom_field_choices.c.id == choice.id).values(
                    position=position, active=choice.active, value=value, value_key=value_key
                )
            )
        else:
            connection.execute(
                c.custom_field_choices.insert().values(
                    id=choice.id,
                    definition_id=stable_id,
                    position=position,
                    active=choice.active,
                    value=value,
                    value_key=value_key,
                )
            )
    return read_definition(connection, stable_id)


@dataclass(frozen=True)
class CustomFieldValueMutation:
    operation: Literal["insert", "update"]
    row_id: str
    definition_id: str
    record_type: str
    record_id: str
    active: bool
    canonical_text: str
    before_active: bool | None
    before_canonical_text: str | None
    logical_path: tuple[str, str]


@dataclass(frozen=True)
class OwnerCustomFieldPlan:
    record_type: str
    record_id: str
    creating: bool
    mutations: tuple[CustomFieldValueMutation, ...]
    logical_paths: tuple[tuple[str, str], ...]

    @property
    def changed(self) -> bool:
        return bool(self.mutations)


def _applicable_definitions(connection: sa.Connection, record_type: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        sa.select(
            c.custom_field_defs,
            c.custom_field_scopes.c.active.label("scope_active"),
        )
        .select_from(
            c.custom_field_defs.join(
                c.custom_field_scopes,
                c.custom_field_scopes.c.definition_id == c.custom_field_defs.c.id,
            )
        )
        .where(
            c.custom_field_scopes.c.record_type == record_type,
            c.custom_field_scopes.c.active.is_(True),
        )
        .order_by(c.custom_field_defs.c.position, c.custom_field_defs.c.name_key, c.custom_field_defs.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def plan_owner_value_patch(
    db: Database | sa.Connection,
    *,
    record_type: str,
    record_id: str,
    patch: CustomFieldValuePatch | Mapping[str, Any | None] | None,
    creating: bool = False,
    id_factory: Callable[[], str] = new_id,
) -> OwnerCustomFieldPlan:
    """Plan owner-value inserts/updates; absent keys preserve and null clears."""

    if record_type not in LIST_VALUE_SCOPES:
        if record_type in TRANSACTION_SCOPES:
            raise _validation("record_type", "custom-field values for this transaction type are not available yet")
        raise _validation("record_type", "does not accept custom fields")
    stable_record_id = _stable_id(record_id, field="record_id")
    mapping = (
        {}
        if patch is None
        else patch.root
        if isinstance(patch, CustomFieldValuePatch)
        else CustomFieldValuePatch.model_validate(patch).root
    )
    connection = _conn(db)
    applicable = _applicable_definitions(connection, record_type)
    by_id = {row["id"]: row for row in applicable}
    for definition_id in mapping:
        if definition_id not in by_id:
            # Existing but not applicable remains indistinguishable from unknown at this boundary.
            if connection.execute(sa.select(c.custom_field_defs.c.id).where(c.custom_field_defs.c.id == definition_id)).first() is None:
                raise _record_not_found(definition_id)
            raise _record_not_found(definition_id)

    value_rows = connection.execute(
        sa.select(c.custom_field_values).where(
            c.custom_field_values.c.record_type == record_type,
            c.custom_field_values.c.record_id == stable_record_id,
        )
    ).mappings().all()
    existing = {row["def_id"]: dict(row) for row in value_rows}
    mutations: list[CustomFieldValueMutation] = []

    for definition in applicable:
        definition_id = definition["id"]
        supplied = definition_id in mapping
        requested = mapping.get(definition_id)
        old = existing.get(definition_id)
        if not supplied:
            if not creating:
                continue
            if not definition["active"]:
                continue
            if definition["default_canonical_text"] is not None:
                requested_canonical = definition["default_canonical_text"]
            elif definition["required"] and definition["active"]:
                raise _validation(f"custom_fields.{definition_id}", "is required for a new record")
            else:
                continue
        elif requested is None:
            if old is None or not old["active"]:
                continue
            if not definition["active"]:
                raise _inactive(definition_id)
            if definition["required"]:
                raise _validation(f"custom_fields.{definition_id}", "a populated required value cannot be cleared")
            requested_canonical = None
        else:
            choices = _active_choice_map(connection, definition_id) if definition["kind"] == "choice" else None
            requested_canonical = parse_typed_value(
                definition["kind"],
                requested,
                field=f"custom_fields.{definition_id}",
                choices=choices,
            )[0]
            if old is not None and old["active"] and old["canonical_text"] == requested_canonical:
                continue
            if not definition["active"]:
                raise _inactive(definition_id)

        if requested_canonical is None:
            assert old is not None
            mutation = CustomFieldValueMutation(
                operation="update",
                row_id=old["id"],
                definition_id=definition_id,
                record_type=record_type,
                record_id=stable_record_id,
                active=False,
                canonical_text=old["canonical_text"],
                before_active=bool(old["active"]),
                before_canonical_text=old["canonical_text"],
                logical_path=custom_field_logical_path(definition_id),
            )
        elif old is None:
            mutation = CustomFieldValueMutation(
                operation="insert",
                row_id=_stable_id(id_factory(), field="custom_field_values.id"),
                definition_id=definition_id,
                record_type=record_type,
                record_id=stable_record_id,
                active=True,
                canonical_text=requested_canonical,
                before_active=None,
                before_canonical_text=None,
                logical_path=custom_field_logical_path(definition_id),
            )
        else:
            mutation = CustomFieldValueMutation(
                operation="update",
                row_id=old["id"],
                definition_id=definition_id,
                record_type=record_type,
                record_id=stable_record_id,
                active=True,
                canonical_text=requested_canonical,
                before_active=bool(old["active"]),
                before_canonical_text=old["canonical_text"],
                logical_path=custom_field_logical_path(definition_id),
            )
        mutations.append(mutation)

    mutations.sort(key=lambda item: item.definition_id)
    paths = tuple(item.logical_path for item in mutations)
    return OwnerCustomFieldPlan(record_type, stable_record_id, creating, tuple(mutations), paths)


def apply_owner_value_plan(db: Database | sa.Connection, plan: OwnerCustomFieldPlan) -> None:
    """Persist a validated value plan inside the caller's aggregate transaction."""

    connection = _conn(db)
    for mutation in plan.mutations:
        if mutation.operation == "insert":
            connection.execute(
                c.custom_field_values.insert().values(
                    id=mutation.row_id,
                    def_id=mutation.definition_id,
                    record_type=mutation.record_type,
                    record_id=mutation.record_id,
                    active=mutation.active,
                    canonical_text=mutation.canonical_text,
                )
            )
        else:
            connection.execute(
                c.custom_field_values.update().where(c.custom_field_values.c.id == mutation.row_id).values(
                    active=mutation.active,
                    canonical_text=mutation.canonical_text,
                )
            )


def read_owner_values(
    db: Database | sa.Connection,
    *,
    record_type: str,
    record_id: str,
) -> list[dict[str, Any]]:
    """Project active values, including values preserved under inactive definitions."""

    connection = _conn(db)
    stable_record_id = _stable_id(record_id, field="record_id")
    rows = connection.execute(
        sa.select(
            c.custom_field_values.c.id,
            c.custom_field_values.c.def_id,
            c.custom_field_values.c.canonical_text,
            c.custom_field_defs.c.name,
            c.custom_field_defs.c.kind,
            c.custom_field_defs.c.active.label("definition_active"),
            c.custom_field_defs.c.position,
        )
        .select_from(c.custom_field_values.join(c.custom_field_defs, c.custom_field_defs.c.id == c.custom_field_values.c.def_id))
        .where(
            c.custom_field_values.c.record_type == record_type,
            c.custom_field_values.c.record_id == stable_record_id,
            c.custom_field_values.c.active.is_(True),
        )
        .order_by(c.custom_field_defs.c.position, c.custom_field_defs.c.name_key, c.custom_field_defs.c.id)
    ).mappings().all()
    return [
        {
            "id": row["id"],
            "definition_id": row["def_id"],
            "name": row["name"],
            "kind": row["kind"],
            "definition_active": bool(row["definition_active"]),
            "value": typed_value_from_canonical(row["kind"], row["canonical_text"]),
        }
        for row in rows
    ]


def parse_employee_requirement_path(
    db: Database | sa.Connection,
    path: Any,
) -> tuple[str, ...]:
    """Validate one nonsecret employee profile path and return path segments."""

    if not isinstance(path, str):
        raise _validation("required_employee_profile_fields", "each path must be text")
    if path in EMPLOYEE_BUILTIN_REQUIREMENT_PATHS:
        return tuple(path.split("."))
    prefix = "custom_fields."
    if not path.startswith(prefix):
        raise _validation("required_employee_profile_fields", "path is not a registered employee output")
    raw_id = path[len(prefix) :]
    definition_id = _stable_id(raw_id, field="required_employee_profile_fields")
    connection = _conn(db)
    found = connection.execute(
        sa.select(c.custom_field_defs.c.id)
        .select_from(
            c.custom_field_defs.join(
                c.custom_field_scopes,
                c.custom_field_scopes.c.definition_id == c.custom_field_defs.c.id,
            )
        )
        .where(
            c.custom_field_defs.c.id == definition_id,
            c.custom_field_defs.c.active.is_(True),
            c.custom_field_scopes.c.record_type == "employee",
            c.custom_field_scopes.c.active.is_(True),
            c.custom_field_scopes.c.definition_active.is_(True),
        )
    ).first()
    if found is None:
        raise _record_not_found(definition_id)
    return ("custom_fields", definition_id)


def validate_employee_requirement_groups(
    db: Database | sa.Connection,
    groups: Any,
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """Validate ordered nonempty alternative-path groups without inventing fields."""

    if not isinstance(groups, list) or not groups:
        raise _validation("required_employee_profile_fields", "must be a nonempty array")
    parsed: list[tuple[tuple[str, ...], ...]] = []
    for group in groups:
        if not isinstance(group, list) or not group:
            raise _validation("required_employee_profile_fields", "each requirement must have at least one alternative")
        parsed.append(tuple(parse_employee_requirement_path(db, path) for path in group))
    return tuple(parsed)


__all__ = [
    "CustomFieldChoiceInput",
    "CustomFieldDefinitionCreate",
    "CustomFieldDefinitionUpdate",
    "CustomFieldValueMutation",
    "CustomFieldValuePatch",
    "EMPLOYEE_BUILTIN_REQUIREMENT_PATHS",
    "LIST_VALUE_SCOPES",
    "OwnerCustomFieldPlan",
    "TRANSACTION_SCOPES",
    "apply_owner_value_plan",
    "create_definition",
    "custom_field_logical_path",
    "parse_employee_requirement_path",
    "parse_typed_value",
    "plan_owner_value_patch",
    "read_definition",
    "read_owner_values",
    "typed_value_from_canonical",
    "update_definition",
    "validate_employee_requirement_groups",
]
