"""company_info and principals."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, normalize_ulid
from bookflow.core.session import Session, now_iso
from bookflow.storage.engine import Database


DEFAULT_EMPLOYEE_PROFILE_FIELDS = [
    ["first_name"], ["last_name"], ["address.line1"], ["address.city"],
    ["address.state"], ["address.postal_code"], ["phone", "email"],
]
EMPLOYEE_PROFILE_BUILTIN_PATHS = frozenset({
    "name", "salutation", "first_name", "middle_name", "last_name", "job_title",
    "print_name_on_check_as", "employment_type", "phone", "email", "hire_date",
    "release_date", "emergency_contact_name", "emergency_contact_relationship",
    "emergency_contact_phone", "emergency_contact_email", "default_class_id", "notes",
    "tax_id_last4",
    *(f"address.{leaf}" for leaf in ("line1", "line2", "city", "state", "postal_code", "country")),
})


def encode_employee_profile_fields(value: list[list[str]]) -> str:
    """Return the canonical ordered JSON storage form for employee requirements."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def logical_info_values(values: dict[str, Any]) -> dict[str, Any]:
    """Return company-info values with storage encodings decoded for outputs and audit snapshots."""
    result = dict(values)
    encoded = result.get("required_employee_profile_fields")
    if isinstance(encoded, str):
        result["required_employee_profile_fields"] = json.loads(encoded)
    return result


def validate_employee_profile_fields(value: Any, db: Database | None = None) -> list[list[str]]:
    """Validate and canonicalize the non-secret employee completeness path grammar."""
    problems: list[dict[str, str]] = []
    if not isinstance(value, list):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "required_employee_profile_fields", "problem": "must be an array"}]})
    normalized: list[list[str]] = []
    for group_index, group in enumerate(value):
        field = f"required_employee_profile_fields.{group_index}"
        if not isinstance(group, list) or not group:
            problems.append({"field": field, "problem": "must be a nonempty array of alternative paths"})
            continue
        clean: list[str] = []
        for path_index, path in enumerate(group):
            path_field = f"{field}.{path_index}"
            if not isinstance(path, str) or not path:
                problems.append({"field": path_field, "problem": "must be a nonempty registered path"})
                continue
            if path in EMPLOYEE_PROFILE_BUILTIN_PATHS:
                clean.append(path)
                continue
            prefix = "custom_fields."
            candidate = path[len(prefix):] if path.startswith(prefix) else ""
            if not candidate or not is_ulid(candidate):
                problems.append({"field": path_field, "problem": "must be a declared employee path or custom_fields.<definition-ulid>"})
                continue
            definition_id = normalize_ulid(candidate)
            if db is None:
                problems.append({"field": path_field, "problem": "custom definition does not exist in a new company"})
                continue
            exists = db.conn.execute(
                sa.select(c.custom_field_scopes.c.id)
                .select_from(c.custom_field_scopes.join(c.custom_field_defs, c.custom_field_scopes.c.definition_id == c.custom_field_defs.c.id))
                .where(
                    c.custom_field_scopes.c.definition_id == definition_id,
                    c.custom_field_scopes.c.record_type == "employee",
                    c.custom_field_scopes.c.active.is_(True),
                    c.custom_field_scopes.c.definition_active.is_(True),
                    c.custom_field_defs.c.active.is_(True),
                )
            ).first()
            if not exists:
                problems.append({"field": path_field, "problem": "must name an active employee-scoped custom definition"})
                continue
            clean.append(f"custom_fields.{definition_id}")
        if clean:
            if len(clean) != len(set(clean)):
                problems.append({"field": field, "problem": "alternative paths must be unique"})
            normalized.append(clean)
    if problems:
        raise BookflowError("E_VALIDATION", details={"fields": problems})
    return normalized


def read_info(db: Database) -> dict[str, Any]:
    row = db.conn.execute(sa.select(c.company_info)).mappings().first()
    if not row:
        return {}
    return logical_info_values(dict(row))


def upsert_principal(db: Database, *, user_id: str, username: str, display_name: str, kind: str) -> None:
    at = now_iso()
    existing = db.conn.execute(sa.select(c.principals.c.user_id).where(c.principals.c.user_id == user_id)).first()
    if existing:
        db.conn.execute(c.principals.update().where(c.principals.c.user_id == user_id).values(username=username, display_name=display_name, kind=kind, last_seen_at=at))
    else:
        db.conn.execute(c.principals.insert().values(user_id=user_id, username=username, display_name=display_name, kind=kind, first_seen_at=at, last_seen_at=at))


def upsert_actor(s: Session) -> None:
    assert s.company and s.actor
    upsert_principal(s.company, user_id=s.actor.id, username=s.actor.username, display_name=s.actor.display_name, kind=s.actor.kind)


def principal_names(db: Database, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = db.conn.execute(sa.select(c.principals.c.user_id, c.principals.c.display_name).where(c.principals.c.user_id.in_(ids))).all()
    return {r[0]: r[1] for r in rows}


def write_display_name_copy(db: Database, display_name: str) -> None:
    """Raw column write: leaves version and updated_* untouched (blueprint 3.1)."""
    db.conn.execute(c.company_info.update().values(display_name=display_name))
