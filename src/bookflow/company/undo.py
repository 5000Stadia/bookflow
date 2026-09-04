"""Compensating undo for eligible Row 5 list audit events.

The audit log is immutable.  This module plans an inverse from its snapshots,
validates the complete inverse on a private database copy, and then applies the
same ordered operations as one new command event.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import sqlite3
from typing import Any, Literal

import sqlalchemy as sa

from bookflow.company import custom_fields, list_service, schema
from bookflow.company.lists import get_list_definition, normalize_display_name
from bookflow.core import audit
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    parse_percentage_millionths,
    parse_quantity_micro_units,
    parse_unit_factor_nano_units,
)
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched
from bookflow.core.session import Session
from bookflow.storage.engine import Database


InverseAction = Literal["update", "activate", "deactivate"]
_MISSING = object()
_LIFECYCLE_VERBS = frozenset({"create", "update", "activate", "deactivate"})
_SPECIAL_EVENT_SHAPES: dict[str, tuple[tuple[str, str], ...]] = {
    "customer link-vendor": (
        ("customer", "link"),
        ("vendor", "link"),
        ("customer_vendor_link", "link"),
    ),
    "customer unlink-vendor": (
        ("customer_vendor_link", "unlink"),
        ("customer", "unlink"),
        ("vendor", "unlink"),
    ),
}
_NONLOGICAL_TOP_LEVEL = frozenset(
    {
        "id",
        "version",
        "created_at",
        "created_by",
        "created_via",
        "updated_at",
        "updated_by",
        "updated_via",
        "seed_key",
        "name_key",
        "code_key",
        "initials_key",
        "full_name",
        "full_name_key",
        "depth",
        "path",
    }
)


def _conflict(
    *,
    record_type: str,
    record_id: str,
    field: str | None = None,
    current_version: int | None = None,
    problem: str | None = None,
    **details: Any,
) -> BookflowError:
    payload: dict[str, Any] = {"record_type": record_type, "record_id": record_id}
    if field is not None:
        payload["field"] = field
    if current_version is not None:
        payload["current_version"] = current_version
    if problem is not None:
        payload["problem"] = problem
    payload.update(details)
    return BookflowError("E_UNDO_CONFLICT", details=payload)


def _not_undoable(event_id: str, problem: str) -> BookflowError:
    return BookflowError(
        "E_NOT_UNDOABLE",
        details={"event_id": event_id, "problem": problem},
    )


@dataclass(frozen=True)
class ChildCollection:
    name: str
    table: sa.Table
    owner_column: str
    to_storage: Callable[[Mapping[str, Any]], dict[str, Any]]
    nested_owner_table: sa.Table | None = None
    nested_parent_column: str | None = None


Snapshotter = Callable[[sa.Connection, str], dict[str, Any]]


@dataclass(frozen=True)
class SnapshotHandler:
    """One record type's snapshot codec and persistence metadata."""

    noun: str
    record_type: str
    table: sa.Table
    snapshotter: Snapshotter
    collections: tuple[ChildCollection, ...] = ()

    @property
    def hierarchical(self) -> bool:
        definition = get_list_definition(self.noun)
        return bool(definition and definition.hierarchy is not None)


class UndoHandlerRegistry:
    """Explicit, extensible mapping from audit record/action to inverse handler."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], SnapshotHandler] = {}

    def register(
        self,
        handler: SnapshotHandler,
        *,
        actions: Sequence[str] = ("create", "update", "activate", "deactivate"),
    ) -> None:
        for action in actions:
            key = (handler.record_type, action)
            if key in self._handlers:
                raise ValueError(f"duplicate undo handler for {key!r}")
            self._handlers[key] = handler

    def get(self, record_type: str, action: str) -> SnapshotHandler | None:
        return self._handlers.get((record_type, action))

    def supports(self, record_type: str, action: str) -> bool:
        return (record_type, action) in self._handlers


HANDLERS = UndoHandlerRegistry()


@dataclass(frozen=True)
class OriginalEntry:
    id: str
    ordinal: int
    record_type: str
    record_id: str
    action: str
    version_before: int | None
    version_after: int | None
    before: dict[str, Any] | None
    after: dict[str, Any]


@dataclass(frozen=True)
class UndoRequest:
    event: dict[str, Any]
    entries: tuple[OriginalEntry, ...]
    required_role: str


@dataclass(frozen=True)
class InverseResult:
    entry: OriginalEntry
    action: InverseAction
    restored_fields: tuple[str, ...]
    touched: Touched


@dataclass(frozen=True)
class UndoPlan:
    request: UndoRequest
    event_id: str
    at: str
    results: tuple[InverseResult, ...]


def _row(connection: sa.Connection, table: sa.Table, record_id: str) -> dict[str, Any]:
    found = connection.execute(sa.select(table).where(table.c.id == record_id)).mappings().first()
    if found is None:
        raise _conflict(
            record_type=table.name,
            record_id=record_id,
            problem="the current record no longer exists",
        )
    return dict(found)


def _raw_snapshot(table: sa.Table) -> Snapshotter:
    def snapshot(connection: sa.Connection, record_id: str) -> dict[str, Any]:
        return _row(connection, table, record_id)

    return snapshot


def _account_snapshot(connection: sa.Connection, record_id: str) -> dict[str, Any]:
    from bookflow.company.accounts import logical_account_snapshot

    return logical_account_snapshot(_row(connection, schema.accounts, record_id))


def _canonical_snapshot(value: Any) -> Any:
    """Match the JSON container shapes returned by decoded audit snapshots."""

    if isinstance(value, Mapping):
        return {key: _canonical_snapshot(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_snapshot(child) for child in value]
    return value


def _party_snapshot(noun: str, record_type: str, table: sa.Table) -> Snapshotter:
    def snapshot(connection: sa.Connection, record_id: str) -> dict[str, Any]:
        from bookflow.company import parties

        db = _ConnectionDatabase(connection)
        owner = _row(connection, table, record_id)
        values = tuple(custom_fields.read_owner_values(
            connection,
            record_type=record_type,
            record_id=record_id,
        ))
        collections = parties.read_party_collections(db, noun, record_id)
        return _canonical_snapshot(
            parties._party_snapshot(db, noun, owner, values, collections)
        )

    return snapshot


def _custom_field_snapshot(connection: sa.Connection, record_id: str) -> dict[str, Any]:
    row = _row(connection, schema.custom_field_defs, record_id)
    scopes = connection.execute(
        sa.select(schema.custom_field_scopes)
        .where(schema.custom_field_scopes.c.definition_id == record_id)
        .order_by(schema.custom_field_scopes.c.position, schema.custom_field_scopes.c.id)
    ).mappings().all()
    choices = connection.execute(
        sa.select(schema.custom_field_choices)
        .where(schema.custom_field_choices.c.definition_id == record_id)
        .order_by(schema.custom_field_choices.c.position, schema.custom_field_choices.c.id)
    ).mappings().all()
    default = (
        None
        if row["default_canonical_text"] is None
        else custom_fields.typed_value_from_canonical(
            row["kind"], row["default_canonical_text"]
        )
    )
    return list_service.aggregate_snapshot(
        {**row, "default": default},
        collections={"scopes": scopes, "choices": choices},
    )


def _price_snapshot(connection: sa.Connection, record_id: str) -> dict[str, Any]:
    from bookflow.company.pricing import aggregate_snapshot

    owner = _row(connection, schema.price_levels, record_id)
    children = connection.execute(
        sa.select(schema.price_level_items)
        .where(schema.price_level_items.c.price_level_id == record_id)
        .order_by(schema.price_level_items.c.position, schema.price_level_items.c.id)
    ).mappings().all()
    return aggregate_snapshot(owner, children)


def _unit_snapshot(connection: sa.Connection, record_id: str) -> dict[str, Any]:
    from bookflow.company.units import aggregate_snapshot

    owner = _row(connection, schema.units_of_measure, record_id)
    children = connection.execute(
        sa.select(schema.unit_conversions)
        .where(schema.unit_conversions.c.unit_of_measure_id == record_id)
        .order_by(schema.unit_conversions.c.position, schema.unit_conversions.c.id)
    ).mappings().all()
    return aggregate_snapshot(owner, children)


def _item_snapshot(connection: sa.Connection, record_id: str) -> dict[str, Any]:
    from bookflow.company.items import aggregate_snapshot

    owner = _row(connection, schema.items, record_id)
    members = connection.execute(
        sa.select(schema.item_members)
        .where(schema.item_members.c.owner_item_id == record_id)
        .order_by(schema.item_members.c.position, schema.item_members.c.id)
    ).mappings().all()
    vendors = connection.execute(
        sa.select(schema.item_vendor_profiles)
        .where(schema.item_vendor_profiles.c.item_id == record_id)
        .order_by(schema.item_vendor_profiles.c.position, schema.item_vendor_profiles.c.id)
    ).mappings().all()
    values = custom_fields.read_owner_values(
        connection,
        record_type="item",
        record_id=record_id,
    )
    return aggregate_snapshot(owner, members, vendors, values)


def _raw_child(value: Mapping[str, Any]) -> dict[str, Any]:
    return dict(value)


def _price_child(value: Mapping[str, Any]) -> dict[str, Any]:
    money = value.get("price")
    percent = value.get("percent")
    return {
        "id": value["id"],
        "position": value["position"],
        "active": value.get("active", True),
        "item_id": value["item_id"],
        "price_minor_units": None if money is None else money["minor_units"],
        "price_currency": None if money is None else money["currency"],
        "percent_millionths": (
            None if percent is None else parse_percentage_millionths(percent)
        ),
        "adjustment_basis": value["adjustment_basis"],
    }


def _unit_child(value: Mapping[str, Any]) -> dict[str, Any]:
    name, name_key = normalize_display_name(value["name"], field="units.name")
    abbreviation, abbreviation_key = normalize_display_name(
        value["abbreviation"], field="units.abbreviation"
    )
    return {
        "id": value["id"],
        "position": value["position"],
        "active": value.get("active", True),
        "name": name,
        "name_key": name_key,
        "abbreviation": abbreviation,
        "abbreviation_key": abbreviation_key,
        "is_base": value["is_base"],
        "base_factor_nanounits": parse_unit_factor_nano_units(value["base_factor"]),
    }


def _item_member_child(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": value["id"],
        "position": value["position"],
        "active": value.get("active", True),
        "component_item_id": value["component_item_id"],
        "quantity_microunits": parse_quantity_micro_units(
            value["quantity"], field="members.quantity"
        ),
        "unit_id": value.get("unit_id"),
    }


def _item_vendor_child(value: Mapping[str, Any]) -> dict[str, Any]:
    money = value.get("purchase_cost")
    return {
        "id": value["id"],
        "position": value["position"],
        "active": value.get("active", True),
        "vendor_id": value["vendor_id"],
        "preferred_rank": value["preferred_rank"],
        "vendor_item_name": value.get("vendor_item_name"),
        "purchase_cost_minor_units": (
            None if money is None else money["minor_units"]
        ),
        "purchase_cost_currency": None if money is None else money["currency"],
        "minimum_quantity_microunits": (
            None
            if value.get("minimum_quantity") is None
            else parse_quantity_micro_units(
                value["minimum_quantity"],
                field="vendor_profiles.minimum_quantity",
            )
        ),
        "lead_time_days": value.get("lead_time_days"),
        "manufacturer_part_number": value.get("manufacturer_part_number"),
        "availability_notes": value.get("availability_notes"),
    }


def _register_default_handlers() -> None:
    table_by_noun: dict[str, sa.Table] = {
        "account": schema.accounts,
        "customer": schema.customers,
        "vendor": schema.vendors,
        "employee": schema.employees,
        "other-name": schema.other_names,
        "item": schema.items,
        "item-category": schema.item_categories,
        "class": schema.classes,
        "term": schema.terms,
        "payment-method": schema.payment_methods,
        "sales-tax-code": schema.sales_tax_codes,
        "customer-type": schema.customer_types,
        "vendor-type": schema.vendor_types,
        "job-type": schema.job_types,
        "sales-rep": schema.sales_reps,
        "ship-method": schema.ship_methods,
        "customer-message": schema.customer_messages,
        "price-level": schema.price_levels,
        "unit-of-measure": schema.units_of_measure,
        "custom-field": schema.custom_field_defs,
    }
    party_tables = {
        "customer": ("customer", schema.customers),
        "vendor": ("vendor", schema.vendors),
        "employee": ("employee", schema.employees),
        "other_name": ("other-name", schema.other_names),
    }
    special: dict[str, tuple[Snapshotter, tuple[ChildCollection, ...]]] = {
        "account": (_account_snapshot, ()),
        **{
            record_type: (_party_snapshot(noun, record_type, table), ())
            for record_type, (noun, table) in party_tables.items()
        },
        "item": (
            _item_snapshot,
            (
                ChildCollection(
                    "members",
                    schema.item_members,
                    "owner_item_id",
                    _item_member_child,
                ),
                ChildCollection(
                    "vendor_profiles",
                    schema.item_vendor_profiles,
                    "item_id",
                    _item_vendor_child,
                ),
            ),
        ),
        "price_level": (
            _price_snapshot,
            (
                ChildCollection(
                    "items", schema.price_level_items, "price_level_id", _price_child
                ),
            ),
        ),
        "unit_of_measure": (
            _unit_snapshot,
            (
                ChildCollection(
                    "units", schema.unit_conversions, "unit_of_measure_id", _unit_child
                ),
            ),
        ),
        "custom_field": (
            _custom_field_snapshot,
            (
                ChildCollection(
                    "scopes", schema.custom_field_scopes, "definition_id", _raw_child
                ),
                ChildCollection(
                    "choices", schema.custom_field_choices, "definition_id", _raw_child
                ),
            ),
        ),
    }
    special["customer"] = (
        _party_snapshot("customer", "customer", schema.customers),
        (
            ChildCollection(
                "shipping_addresses",
                schema.customer_addresses,
                "customer_id",
                _raw_child,
            ),
            ChildCollection(
                "contacts",
                schema.customer_contacts,
                "customer_id",
                _raw_child,
            ),
            ChildCollection(
                "contact_points",
                schema.customer_contact_points,
                "contact_id",
                _raw_child,
                nested_owner_table=schema.customer_contacts,
                nested_parent_column="customer_id",
            ),
        ),
    )
    special["vendor"] = (
        _party_snapshot("vendor", "vendor", schema.vendors),
        (
            ChildCollection(
                "contacts",
                schema.vendor_contacts,
                "vendor_id",
                _raw_child,
            ),
            ChildCollection(
                "contact_points",
                schema.vendor_contact_points,
                "contact_id",
                _raw_child,
                nested_owner_table=schema.vendor_contacts,
                nested_parent_column="vendor_id",
            ),
            ChildCollection(
                "expense_accounts",
                schema.vendor_expense_accounts,
                "vendor_id",
                _raw_child,
            ),
        ),
    )
    for noun, table in table_by_noun.items():
        definition = get_list_definition(noun)
        assert definition is not None
        snapshotter, collections = special.get(
            definition.record_type, (_raw_snapshot(table), ())
        )
        HANDLERS.register(
            SnapshotHandler(
                noun=noun,
                record_type=definition.record_type,
                table=table,
                snapshotter=snapshotter,
                collections=collections,
            )
        )

    customer_handler = HANDLERS.get("customer", "update")
    vendor_handler = HANDLERS.get("vendor", "update")
    other_name_handler = HANDLERS.get("other_name", "update")
    assert customer_handler is not None
    assert vendor_handler is not None
    assert other_name_handler is not None
    HANDLERS.register(customer_handler, actions=("link", "unlink"))
    HANDLERS.register(vendor_handler, actions=("link", "unlink"))
    HANDLERS.register(other_name_handler, actions=("convert",))
    HANDLERS.register(
        SnapshotHandler(
            noun="customer-vendor-link",
            record_type="customer_vendor_link",
            table=schema.customer_vendor_links,
            snapshotter=_raw_snapshot(schema.customer_vendor_links),
        ),
        actions=("link", "unlink"),
    )


_register_default_handlers()


def _previous_snapshot(
    connection: sa.Connection,
    *,
    record_type: str,
    record_id: str,
    version: int,
) -> dict[str, Any] | None:
    blob = connection.execute(
        sa.select(schema.audit_entries.c.after)
        .where(
            schema.audit_entries.c.record_type == record_type,
            schema.audit_entries.c.record_id == record_id,
            schema.audit_entries.c.version_after == version,
            schema.audit_entries.c.action.not_in(("migrate", "baseline")),
        )
        .order_by(schema.audit_entries.c.id.desc())
    ).scalar_one_or_none()
    return audit.decode_snapshot(blob)


def _event_required_role(command_name: str) -> str:
    try:
        from bookflow.core import registry

        command = registry.get(command_name)
    except (ImportError, ValueError):
        command = None
    if command is not None and command.required_role in {
        "member",
        "standard",
        "admin",
        "owner",
    }:
        return command.required_role
    return "admin" if command_name.startswith("custom-field ") else "standard"


def _require_original_role(s: Session, required_role: str) -> None:
    from bookflow.hub import access

    assert s.company_row is not None
    current_access, role = access.company_role(
        s, s.company_row["id"], s.company_row["organization_id"]
    )
    if not access.role_satisfies(role, current_access, required_role, s.is_hub_admin):
        raise BookflowError(
            "E_PERMISSION",
            details={
                "capability": "undo",
                "required_role": required_role,
                "role": role,
            },
        )


def load_request(s: Session, event_id: str) -> UndoRequest:
    """Load and authorize one eligible original event without mutating."""

    stable_event_id = event_id.upper()
    event_row = s.company.conn.execute(
        sa.select(schema.audit_events).where(schema.audit_events.c.id == stable_event_id)
    ).mappings().first()
    if event_row is None:
        raise BookflowError("E_EVENT_NOT_FOUND")
    event = dict(event_row)
    compensated = s.company.conn.execute(
        sa.select(schema.audit_events.c.id).where(
            schema.audit_events.c.undo_of_event_id == stable_event_id
        )
    ).scalar_one_or_none()
    if compensated is not None:
        raise BookflowError(
            "E_ALREADY_UNDONE",
            details={"event_id": stable_event_id, "undo_event_id": compensated},
        )
    if event["command"] == "undo":
        raise _not_undoable(stable_event_id, "undo events cannot themselves be undone")
    if event.get("actor_kind") == "system":
        raise _not_undoable(stable_event_id, "system events are not list writes")
    if event["command"].startswith(("chart ", "profile ")):
        raise _not_undoable(stable_event_id, "packaged-data application is not undoable")

    command_name = str(event["command"])
    special_shape = _SPECIAL_EVENT_SHAPES.get(command_name)
    definition = None
    verb = None
    if special_shape is None and command_name != "other-name convert":
        command_parts = command_name.rsplit(" ", 1)
        if len(command_parts) != 2 or command_parts[1] not in _LIFECYCLE_VERBS:
            raise _not_undoable(
                stable_event_id,
                "the event is not a supported Row 5 list write",
            )
        noun, verb = command_parts
        definition = get_list_definition(noun)
        if definition is None:
            raise _not_undoable(stable_event_id, "the event is not a Row 5 list write")

    raw_entries = s.company.conn.execute(
        sa.select(schema.audit_entries)
        .where(schema.audit_entries.c.event_id == stable_event_id)
        .order_by(schema.audit_entries.c.id)
    ).mappings().all()
    if not raw_entries:
        raise _not_undoable(stable_event_id, "the event has no list entries")
    actual_shape = tuple(
        (str(raw["record_type"]), str(raw["action"])) for raw in raw_entries
    )
    if special_shape is not None and actual_shape != special_shape:
        raise _not_undoable(
            stable_event_id,
            "the link event does not have the required complete audit shape",
        )
    if command_name == "other-name convert":
        conversion_shape_ok = (
            len(actual_shape) == 2
            and actual_shape[0] == ("other_name", "convert")
            and actual_shape[1][0] in {"customer", "vendor", "employee"}
            and actual_shape[1][1] == "create"
        )
        if not conversion_shape_ok:
            raise _not_undoable(
                stable_event_id,
                "the conversion event does not have the required complete audit shape",
            )
    entries: list[OriginalEntry] = []
    for ordinal, raw in enumerate(raw_entries):
        row = dict(raw)
        if definition is not None and (
            row["record_type"] != definition.record_type or row["action"] != verb
        ):
            raise _not_undoable(stable_event_id, "the event mixes non-list record types")
        if not HANDLERS.supports(row["record_type"], row["action"]):
            raise _not_undoable(stable_event_id, "an audit entry has no safe inverse handler")
        after = audit.decode_snapshot(row["after"])
        if after is None:
            raise _not_undoable(stable_event_id, "an audit entry has no after snapshot")
        before = audit.decode_snapshot(row["before"])
        if before is None and row["version_before"] is not None:
            before = _previous_snapshot(
                s.company.conn,
                record_type=row["record_type"],
                record_id=row["record_id"],
                version=int(row["version_before"]),
            )
        link_creation = (
            row["record_type"] == "customer_vendor_link"
            and row["action"] == "link"
            and row["version_before"] is None
        )
        if row["action"] != "create" and not link_creation and before is None:
            raise _not_undoable(stable_event_id, "the prior snapshot is unavailable")
        entries.append(
            OriginalEntry(
                id=row["id"],
                ordinal=ordinal,
                record_type=row["record_type"],
                record_id=row["record_id"],
                action=row["action"],
                version_before=row["version_before"],
                version_after=row["version_after"],
                before=before,
                after=after,
            )
        )

    required_role = _event_required_role(command_name)
    _require_original_role(s, required_role)
    return UndoRequest(event, tuple(entries), required_role)


def _is_ignored(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    first = path[0]
    return first in _NONLOGICAL_TOP_LEVEL or (
        first.startswith("_") and first.endswith("_identities")
    )


def _id_map(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, Mapping) and isinstance(item.get("id"), str) for item in value):
        return None
    return {str(item["id"]): item for item in value}


def _same_logical_value(left: Any, right: Any) -> bool:
    """Treat an omitted optional snapshot leaf as its logical null value."""

    if left is _MISSING or right is _MISSING:
        other = right if left is _MISSING else left
        return other is _MISSING or other is None
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return all(
            _same_logical_value(
                left.get(key, _MISSING), right.get(key, _MISSING)
            )
            for key in set(left) | set(right)
        )
    left_ids = _id_map(left)
    right_ids = _id_map(right)
    if left_ids is not None and right_ids is not None:
        return set(left_ids) == set(right_ids) and all(
            _same_logical_value(left_ids[key], right_ids[key])
            for key in left_ids
        )
    return left == right


def _changed_paths(
    before: Any,
    after: Any,
    path: tuple[str, ...] = (),
) -> tuple[tuple[str, ...], ...]:
    if _is_ignored(path):
        return ()
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        paths: list[tuple[str, ...]] = []
        for key in sorted(set(before) | set(after)):
            left = before.get(key, _MISSING)
            right = after.get(key, _MISSING)
            if left is _MISSING or right is _MISSING:
                candidate = path + (str(key),)
                if not _is_ignored(candidate) and not _same_logical_value(left, right):
                    paths.append(candidate)
            else:
                paths.extend(_changed_paths(left, right, path + (str(key),)))
        return tuple(paths)
    before_ids = _id_map(before)
    after_ids = _id_map(after)
    if before_ids is not None and after_ids is not None:
        paths = []
        for identifier in sorted(set(before_ids) | set(after_ids)):
            if identifier not in before_ids or identifier not in after_ids:
                paths.append(path + (identifier,))
            else:
                paths.extend(
                    _changed_paths(
                        before_ids[identifier],
                        after_ids[identifier],
                        path + (identifier,),
                    )
                )
        return tuple(paths)
    return () if _same_logical_value(before, after) else (path,)


def _path_value(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for part in path:
        if isinstance(current, Mapping):
            if part not in current:
                return _MISSING
            current = current[part]
            continue
        by_id = _id_map(current)
        if by_id is None or part not in by_id:
            return _MISSING
        current = by_id[part]
    return current


def _set_path(target: Any, path: tuple[str, ...], value: Any) -> None:
    if not path:
        raise ValueError("cannot replace the snapshot root")
    current = target
    for part in path[:-1]:
        if isinstance(current, dict):
            if part not in current:
                current[part] = {}
            current = current[part]
            continue
        by_id = _id_map(current)
        if by_id is None or part not in by_id:
            raise ValueError(f"cannot descend through snapshot path {path!r}")
        current = by_id[part]
    leaf = path[-1]
    if isinstance(current, dict):
        if value is _MISSING:
            current.pop(leaf, None)
        else:
            current[leaf] = deepcopy(value)
        return
    by_id = _id_map(current)
    if by_id is None:
        raise ValueError(f"cannot assign snapshot path {path!r}")
    existing = by_id.get(leaf)
    if value is _MISSING:
        if existing is not None:
            current.remove(existing)
    elif existing is None:
        current.append(deepcopy(value))
    else:
        index = current.index(existing)
        current[index] = deepcopy(value)
    current.sort(key=lambda item: (item.get("position", 0), str(item.get("id", ""))))


def _path_text(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _conflicting_event(
    connection: sa.Connection,
    *,
    entry: OriginalEntry,
    path: tuple[str, ...],
    current_version: int,
) -> dict[str, Any] | None:
    rows = connection.execute(
        sa.select(
            schema.audit_events.c.id,
            schema.audit_events.c.command,
            schema.audit_events.c.at,
            schema.audit_entries.c.version_before,
            schema.audit_entries.c.version_after,
            schema.audit_entries.c.before,
            schema.audit_entries.c.after,
        )
        .join(
            schema.audit_entries,
            schema.audit_entries.c.event_id == schema.audit_events.c.id,
        )
        .where(
            schema.audit_entries.c.record_type == entry.record_type,
            schema.audit_entries.c.record_id == entry.record_id,
            schema.audit_entries.c.version_after > (entry.version_after or 0),
            schema.audit_entries.c.version_after <= current_version,
        )
        .order_by(schema.audit_entries.c.version_after.desc(), schema.audit_events.c.seq.desc())
    ).mappings().all()
    for row in rows:
        before = audit.decode_snapshot(row["before"])
        if before is None and row["version_before"] is not None:
            before = _previous_snapshot(
                connection,
                record_type=entry.record_type,
                record_id=entry.record_id,
                version=int(row["version_before"]),
            )
        after = audit.decode_snapshot(row["after"])
        if (
            before is not None
            and after is not None
            and not _same_logical_value(
                _path_value(before, path), _path_value(after, path)
            )
        ):
            return {key: row[key] for key in ("id", "command", "at")}
    return None


@dataclass(frozen=True)
class HardReference:
    table: sa.Table
    foreign_key: str
    target_type_field: str | None = None
    target_type: str | None = None
    owner_table: sa.Table | None = None
    owner_foreign_key: str | None = None


_ACCOUNT_ITEM_FIELDS = (
    "income_account_id",
    "expense_account_id",
    "cogs_account_id",
    "asset_account_id",
    "deposit_account_id",
    "liability_account_id",
    "accumulated_depreciation_account_id",
    "depreciation_expense_account_id",
    "gain_loss_account_id",
)
_HARD_REFERENCES: dict[str, tuple[HardReference, ...]] = {
    "account": (
        HardReference(schema.accounts, "parent_id"),
        HardReference(schema.accounts, "reimbursable_income_account_id"),
        *(HardReference(schema.items, field) for field in _ACCOUNT_ITEM_FIELDS),
    ),
    "customer": (
        HardReference(schema.customers, "parent_id"),
        HardReference(schema.customer_vendor_links, "customer_id"),
    ),
    "vendor": (
        HardReference(schema.customer_vendor_links, "vendor_id"),
        HardReference(schema.items, "tax_agency_vendor_id"),
        HardReference(
            schema.item_vendor_profiles,
            "vendor_id",
            owner_table=schema.items,
            owner_foreign_key="item_id",
        ),
        HardReference(
            schema.sales_reps, "name_id", target_type_field="name_type", target_type="vendor"
        ),
    ),
    "employee": (
        HardReference(
            schema.sales_reps,
            "name_id",
            target_type_field="name_type",
            target_type="employee",
        ),
    ),
    "other_name": (
        HardReference(
            schema.sales_reps,
            "name_id",
            target_type_field="name_type",
            target_type="other_name",
        ),
    ),
    "item": (
        HardReference(schema.items, "parent_id"),
        HardReference(
            schema.item_members,
            "component_item_id",
            owner_table=schema.items,
            owner_foreign_key="owner_item_id",
        ),
    ),
    "unit_of_measure": (HardReference(schema.items, "unit_of_measure_set_id"),),
}


@dataclass(frozen=True)
class _ConnectionDatabase(Database):
    """Minimal database view accepted by the pure Row 5 domain planners."""

    conn: sa.Connection


def _price_target(
    current: Mapping[str, Any], desired: Mapping[str, Any]
) -> dict[str, Any]:
    target = {
        "name": desired["name"],
        "kind": desired["kind"],
        "currency": desired.get("currency"),
        "rounding_mode": desired["rounding_mode"],
        "rounding_increment": desired["rounding_increment"],
        "rounding_offset": desired["rounding_offset"],
    }
    if desired["kind"] == "fixed_percent":
        target["percent"] = desired["percent"]
    else:
        current_ids = {str(item["id"]) for item in current.get("items", [])}
        target["items"] = [
            {
                key: item[key]
                for key in ("id", "item_id", "price", "percent", "adjustment_basis")
                if key in item and (key != "id" or str(item[key]) in current_ids)
            }
            for item in desired.get("items", [])
        ]
    return target


def _unit_target(
    current: Mapping[str, Any], desired: Mapping[str, Any]
) -> dict[str, Any]:
    current_ids = {str(item["id"]) for item in current.get("units", [])}
    return {
        "name": desired["name"],
        "default_purchase_unit_id": desired.get("default_purchase_unit_id"),
        "default_sales_unit_id": desired.get("default_sales_unit_id"),
        "default_shipping_unit_id": desired.get("default_shipping_unit_id"),
        "units": [
            {
                key: item[key]
                for key in (
                    "id",
                    "name",
                    "abbreviation",
                    "is_base",
                    "base_factor",
                )
                if key in item and (key != "id" or str(item[key]) in current_ids)
            }
            for item in desired.get("units", [])
        ],
    }


def _item_target_patch(
    current: Mapping[str, Any], desired: Mapping[str, Any], roots: set[str]
) -> dict[str, Any]:
    from bookflow.company import items

    changes = {
        field: desired.get(field)
        for field in roots
        if field in items.ItemInput.model_fields
        and field not in {"members", "vendor_profiles", "custom_fields"}
    }
    if "members" in roots:
        current_ids = {
            str(row["id"]) for row in current.get("members", [])
        }
        changes["members"] = [
            {
                key: row[key]
                for key in ("id", "component_item_id", "quantity", "unit_id")
                if key in row and (key != "id" or str(row[key]) in current_ids)
            }
            for row in desired.get("members", [])
            if row.get("active", True)
        ]
    if "vendor_profiles" in roots:
        current_ids = {
            str(row["id"]) for row in current.get("vendor_profiles", [])
        }
        changes["vendor_profiles"] = [
            {
                key: row[key]
                for key in (
                    "id",
                    "vendor_id",
                    "preferred_rank",
                    "vendor_item_name",
                    "purchase_cost",
                    "minimum_quantity",
                    "lead_time_days",
                    "manufacturer_part_number",
                    "availability_notes",
                )
                if key in row and (key != "id" or str(row[key]) in current_ids)
            }
            for row in desired.get("vendor_profiles", [])
            if row.get("active", True)
        ]
    custom_patch = {
        field.removeprefix("custom_fields."): desired.get(field)
        for field in roots
        if field.startswith("custom_fields.")
    }
    if custom_patch:
        changes["custom_fields"] = custom_patch
    return changes


def _party_target_patch(
    noun: str,
    current: Mapping[str, Any],
    desired: Mapping[str, Any],
    roots: set[str],
) -> dict[str, Any]:
    from bookflow.company import parties

    model = parties.CREATE_MODELS[noun]
    changes = {
        field: desired[field]
        for field in roots
        if field in model.model_fields and field in desired
    }
    if noun == "customer" and any(field.startswith("billing_") for field in roots):
        changes["billing_address"] = {
            leaf: desired.get(f"billing_{leaf}")
            for leaf in ("line1", "line2", "city", "state", "postal_code", "country")
        }
    if noun != "customer" and any(field.startswith("address_") for field in roots):
        changes["address"] = {
            leaf: desired.get(f"address_{leaf}")
            for leaf in ("line1", "line2", "city", "state", "postal_code", "country")
        }
    if any(field.startswith("credit_limit_") for field in roots):
        changes["credit_limit"] = (
            None
            if desired.get("credit_limit_minor_units") is None
            else {
                "minor_units": desired["credit_limit_minor_units"],
                "currency": desired["credit_limit_currency"],
            }
        )

    def retained_id(
        row: Mapping[str, Any], active_ids: set[str]
    ) -> dict[str, Any]:
        return {
            key: value
            for key, value in row.items()
            if key != "id" or str(value) in active_ids
        }

    if noun == "customer" and "shipping_addresses" in roots:
        current_ids = {
            str(row["id"]) for row in current.get("shipping_addresses", [])
        }
        addresses = []
        for row in desired.get("shipping_addresses", []):
            logical = {
                "id": row["id"],
                "label": row["label"],
                "is_default": row.get("is_default", False),
                **{
                    leaf: row.get(f"address_{leaf}")
                    for leaf in ("line1", "line2", "city", "state", "postal_code", "country")
                },
            }
            addresses.append(retained_id(logical, current_ids))
        changes["shipping_addresses"] = addresses

    if "contacts" in roots or "contact_points" in roots:
        current_contact_ids = {
            str(row["id"]) for row in current.get("contacts", [])
        }
        current_point_ids = {
            str(row["id"]) for row in current.get("contact_points", [])
        }
        points_by_contact: dict[str, list[dict[str, Any]]] = {}
        for row in desired.get("contact_points", []):
            logical = {
                key: row[key]
                for key in parties.ContactPointInput.model_fields
                if key in row
            }
            points_by_contact.setdefault(str(row["contact_id"]), []).append(
                retained_id(logical, current_point_ids)
            )
        contacts = []
        for row in desired.get("contacts", []):
            logical = {
                key: row[key]
                for key in parties.ContactInput.model_fields
                if key in row and key != "points"
            }
            logical["points"] = points_by_contact.get(str(row["id"]), [])
            contacts.append(retained_id(logical, current_contact_ids))
        changes["contacts"] = contacts

    if noun == "vendor" and "expense_accounts" in roots:
        current_ids = {
            str(row["id"]) for row in current.get("expense_accounts", [])
        }
        changes["expense_accounts"] = [
            retained_id(
                {"id": row["id"], "account_id": row["account_id"]},
                current_ids,
            )
            for row in desired.get("expense_accounts", [])
        ]

    custom_patch = {
        field.removeprefix("custom_fields."): desired.get(field)
        for field in roots
        if field.startswith("custom_fields.")
    }
    if custom_patch:
        changes["custom_fields"] = custom_patch
    return changes


def _custom_definition_patch(
    desired: Mapping[str, Any], roots: set[str]
) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for field in ("name", "kind", "position", "required", "active"):
        if field in roots:
            patch[field] = desired[field]
    if "default_canonical_text" in roots:
        patch["default"] = desired.get("default")
    if "scopes" in roots:
        patch["scopes"] = tuple(
            item["record_type"] for item in desired.get("scopes", [])
        )
    if "choices" in roots:
        patch["choices"] = tuple(
            {
                "id": item["id"],
                "value": item["value"],
                "active": item.get("active", True),
            }
            for item in desired.get("choices", [])
        )
    return patch


def _validate_domain_target(
    connection: sa.Connection,
    handler: SnapshotHandler,
    current: Mapping[str, Any],
    desired: Mapping[str, Any],
    roots: set[str],
    *,
    actor_id: str,
    via: str,
    at: str,
) -> None:
    """Run the owning domain's current semantic rules against the inverse."""

    db = _ConnectionDatabase(connection)
    try:
        if handler.record_type == "account":
            from bookflow.company import accounts

            if "active" in roots:
                accounts.plan_account_active_change(
                    db,
                    str(current["id"]),
                    bool(desired["active"]),
                    actor_id=actor_id,
                    via=via,
                    cascade=False,
                    at=at,
                )
            changes = {
                field: desired.get(field)
                for field in roots
                if field in accounts.AccountUpdateInput.model_fields
                and field not in {"account", "expected_version"}
            }
            if changes:
                accounts.plan_account_update(
                    db,
                    str(current["id"]),
                    changes,
                    actor_id=actor_id,
                    via=via,
                    at=at,
                )
        elif handler.record_type in {"customer", "vendor", "employee", "other_name"}:
            from bookflow.company import parties

            if "active" in roots:
                parties.plan_party_active_change(
                    db,
                    handler.noun,
                    str(current["id"]),
                    bool(desired["active"]),
                    actor_id=actor_id,
                    via=via,
                    cascade=False,
                    at=at,
                )
            changes = _party_target_patch(
                handler.noun, current, desired, roots
            )
            if changes:
                parties.plan_party_update(
                    db,
                    handler.noun,
                    str(current["id"]),
                    changes,
                    actor_id=actor_id,
                    via=via,
                    at=at,
                )
        elif handler.record_type in {
            "item_category",
            "class",
            "term",
            "payment_method",
            "sales_tax_code",
            "customer_type",
            "vendor_type",
            "job_type",
            "sales_rep",
            "ship_method",
            "customer_message",
        }:
            from bookflow.company import profiles

            if "active" in roots:
                profiles.plan_profile_active_change(
                    db,
                    handler.noun,
                    str(current["id"]),
                    bool(desired["active"]),
                    actor_id=actor_id,
                    via=via,
                    cascade=False,
                    at=at,
                )
            payload = profiles._payload_from_row(handler.noun, desired)
            profiles.plan_profile_update(
                db,
                handler.noun,
                str(current["id"]),
                payload,
                actor_id=actor_id,
                via=via,
                at=at,
            )
        elif handler.record_type == "price_level":
            from bookflow.company import pricing

            pricing.plan_price_level_update(
                db,
                str(current["id"]),
                _price_target(current, desired),
                actor_id=actor_id,
                via=via,
                at=at,
            )
        elif handler.record_type == "unit_of_measure":
            from bookflow.company import units

            staged = "units" in roots
            if staged:
                connection.exec_driver_sql("SAVEPOINT undo_validate_unit")
            try:
                if staged:
                    _apply_collection(
                        connection, handler, handler.collections[0], desired
                    )
                units.plan_unit_update(
                    db,
                    str(current["id"]),
                    _unit_target(desired if staged else current, desired),
                    actor_id=actor_id,
                    via=via,
                    at=at,
                )
            finally:
                if staged:
                    connection.exec_driver_sql(
                        "ROLLBACK TO SAVEPOINT undo_validate_unit"
                    )
                    connection.exec_driver_sql(
                        "RELEASE SAVEPOINT undo_validate_unit"
                    )
        elif handler.record_type == "item":
            from bookflow.company import items

            if "active" in roots:
                items.plan_item_active_change(
                    db,
                    str(current["id"]),
                    bool(desired["active"]),
                    actor_id=actor_id,
                    via=via,
                    cascade=False,
                    at=at,
                )
            changes = _item_target_patch(current, desired, roots)
            if changes:
                items.plan_item_update(
                    db,
                    str(current["id"]),
                    changes,
                    actor_id=actor_id,
                    via=via,
                    at=at,
                )
        elif handler.record_type == "custom_field":
            patch = _custom_definition_patch(desired, roots)
            if patch:
                connection.exec_driver_sql("SAVEPOINT undo_validate_custom_field")
                try:
                    custom_fields.update_definition(
                        connection,
                        str(current["id"]),
                        patch,
                        actor_id=actor_id,
                        interface=via,
                        expected_version=int(current["version"]),
                        at=at,
                    )
                finally:
                    connection.exec_driver_sql(
                        "ROLLBACK TO SAVEPOINT undo_validate_custom_field"
                    )
                    connection.exec_driver_sql(
                        "RELEASE SAVEPOINT undo_validate_custom_field"
                    )
    except BookflowError as exc:
        raise _rule_conflict(handler, current, exc) from exc


def _active_dependents(
    connection: sa.Connection,
    record_type: str,
    record_id: str,
) -> list[dict[str, Any]]:
    dependents: list[dict[str, Any]] = []
    for reference in _HARD_REFERENCES.get(record_type, ()):
        conditions = [reference.table.c[reference.foreign_key] == record_id]
        selectable: sa.FromClause = reference.table
        if "active" in reference.table.c:
            conditions.append(reference.table.c.active.is_(True))
        if reference.owner_table is not None:
            assert reference.owner_foreign_key is not None
            selectable = reference.table.join(
                reference.owner_table,
                reference.owner_table.c.id
                == reference.table.c[reference.owner_foreign_key],
            )
            conditions.append(reference.owner_table.c.active.is_(True))
        if reference.target_type_field is not None:
            conditions.append(
                reference.table.c[reference.target_type_field] == reference.target_type
            )
        count = int(
            connection.execute(
                sa.select(sa.func.count()).select_from(selectable).where(*conditions)
            ).scalar_one()
        )
        if count:
            dependents.append({"record_type": reference.table.name, "count": count})
    if record_type == "custom_field":
        count = 0
        owners = {
            "customer": schema.customers,
            "vendor": schema.vendors,
            "employee": schema.employees,
            "other_name": schema.other_names,
            "item": schema.items,
        }
        for owner_type, owner_table in owners.items():
            count += int(
                connection.execute(
                    sa.select(sa.func.count())
                    .select_from(
                        schema.custom_field_values.join(
                            owner_table,
                            owner_table.c.id == schema.custom_field_values.c.record_id,
                        )
                    )
                    .where(
                        schema.custom_field_values.c.def_id == record_id,
                        schema.custom_field_values.c.record_type == owner_type,
                        schema.custom_field_values.c.active.is_(True),
                        owner_table.c.active.is_(True),
                    )
                ).scalar_one()
            )
        if count:
            dependents.append({"record_type": "custom_field_value", "count": count})
    return dependents


def _validate_deactivation(
    connection: sa.Connection,
    handler: SnapshotHandler,
    current: Mapping[str, Any],
) -> None:
    if handler.record_type == "account" and current.get("system_role") is not None:
        raise _conflict(
            record_type=handler.record_type,
            record_id=str(current["id"]),
            field="active",
            current_version=int(current["version"]),
            problem="a protected system record cannot be deactivated",
        )
    try:
        list_service.plan_deactivation(
            connection, handler.table, current, cascade=False
        )
    except BookflowError as exc:
        raise _rule_conflict(handler, current, exc, field="active") from exc
    dependents = _active_dependents(
        connection, handler.record_type, str(current["id"])
    )
    if dependents:
        raise _conflict(
            record_type=handler.record_type,
            record_id=str(current["id"]),
            field="active",
            current_version=int(current["version"]),
            problem="active dependent records still use this record",
            dependents=dependents,
        )


def _rule_conflict(
    handler: SnapshotHandler,
    current: Mapping[str, Any],
    exc: BookflowError,
    *,
    field: str | None = None,
) -> BookflowError:
    return _conflict(
        record_type=handler.record_type,
        record_id=str(current["id"]),
        field=field,
        current_version=int(current["version"]),
        problem="a current list rule rejects the inverse",
        cause=exc.code,
        cause_details=exc.details,
    )


def _validate_changed_references(
    connection: sa.Connection,
    handler: SnapshotHandler,
    desired: Mapping[str, Any],
    roots: set[str],
) -> None:
    for foreign_key in handler.table.foreign_keys:
        field = foreign_key.parent.name
        if field not in roots or field == "parent_id":
            continue
        target_id = desired.get(field)
        target = foreign_key.column.table
        if target_id is None or "active" not in target.c:
            continue
        active = connection.execute(
            sa.select(target.c.active).where(target.c.id == target_id)
        ).scalar_one_or_none()
        restored_child = any(
            collection.table is target
            and any(
                str(row.get("id")) == str(target_id)
                for row in desired.get(collection.name, [])
            )
            for collection in handler.collections
        )
        if active is not True and not restored_child:
            raise _conflict(
                record_type=handler.record_type,
                record_id=str(desired["id"]),
                field=field,
                current_version=int(desired["version"]),
                problem="the restored reference is not active",
                referenced_record_type=target.name,
                referenced_record_id=target_id,
            )


def _validate_link_target(
    connection: sa.Connection,
    current: Mapping[str, Any],
    desired: Mapping[str, Any],
) -> None:
    """Recheck endpoint activity and one-to-one slots before restoring a link."""

    if not desired.get("active"):
        return
    record_id = str(current["id"])
    for record_type, table, field in (
        ("customer", schema.customers, "customer_id"),
        ("vendor", schema.vendors, "vendor_id"),
    ):
        endpoint_id = str(desired[field])
        active = connection.execute(
            sa.select(table.c.active).where(table.c.id == endpoint_id)
        ).scalar_one_or_none()
        if active is not True:
            raise _conflict(
                record_type="customer_vendor_link",
                record_id=record_id,
                field="active",
                current_version=int(current["version"]),
                problem="a link endpoint is not active",
                referenced_record_type=record_type,
                referenced_record_id=endpoint_id,
            )
        occupied = connection.execute(
            sa.select(schema.customer_vendor_links.c.id).where(
                schema.customer_vendor_links.c[field] == endpoint_id,
                schema.customer_vendor_links.c.active.is_(True),
                schema.customer_vendor_links.c.id != record_id,
            )
        ).scalar_one_or_none()
        if occupied is not None:
            raise _conflict(
                record_type="customer_vendor_link",
                record_id=record_id,
                field=field,
                current_version=int(current["version"]),
                problem="a link endpoint already has another active relationship",
                conflicting_link_id=str(occupied),
            )


def _owner_storage_values(
    handler: SnapshotHandler,
    desired: Mapping[str, Any],
    roots: set[str],
) -> dict[str, Any]:
    values = {
        field: desired.get(field)
        for field in roots
        if field in handler.table.c
        and field
        not in {
            "id",
            "version",
            "created_at",
            "created_by",
            "created_via",
            "updated_at",
            "updated_by",
            "updated_via",
        }
    }
    if "name" in roots and "name_key" in handler.table.c:
        name, name_key = normalize_display_name(desired["name"])
        values.update(name=name, name_key=name_key)
    if "code" in roots and "code_key" in handler.table.c:
        code, code_key = normalize_display_name(desired["code"], field="code")
        values.update(code=code, code_key=code_key)
    if "initials" in roots and "initials_key" in handler.table.c:
        initials, initials_key = normalize_display_name(
            desired["initials"], field="initials"
        )
        values.update(initials=initials, initials_key=initials_key)
    if handler.record_type == "price_level":
        if "percent" in roots:
            values["percent_millionths"] = (
                None
                if desired.get("percent") is None
                else parse_percentage_millionths(desired["percent"])
            )
        for prefix in ("rounding_increment", "rounding_offset"):
            if prefix in roots:
                money = desired[prefix]
                values[f"{prefix}_minor_units"] = money["minor_units"]
                values[f"{prefix}_currency"] = money["currency"]
    if handler.record_type in {"customer", "vendor", "employee", "other_name"}:
        for logical_field, storage_prefix in (
            ("billing_address", "billing"),
            ("address", "address"),
        ):
            if logical_field not in roots:
                continue
            address = desired.get(logical_field) or {}
            for leaf in ("line1", "line2", "city", "state", "postal_code", "country"):
                values[f"{storage_prefix}_{leaf}"] = address.get(leaf)
        if "credit_limit" in roots:
            money = desired.get("credit_limit")
            values["credit_limit_minor_units"] = (
                None if money is None else money["minor_units"]
            )
            values["credit_limit_currency"] = (
                None if money is None else money["currency"]
            )
    if handler.record_type == "item":
        for field in (
            "price",
            "cost",
            "discount_amount",
            "original_cost",
            "disposal_proceeds",
            "disposal_costs",
            "book_basis",
            "tax_basis",
        ):
            if field not in roots:
                continue
            money = desired.get(field)
            values[f"{field}_minor_units"] = (
                None if money is None else money["minor_units"]
            )
            values[f"{field}_currency"] = (
                None if money is None else money["currency"]
            )
        for field, column in {
            "charge_percent": "other_charge_percent_millionths",
            "discount_percent": "discount_percent_millionths",
            "tax_percent": "tax_percent_millionths",
        }.items():
            if field in roots:
                values[column] = (
                    None
                    if desired.get(field) is None
                    else parse_percentage_millionths(desired[field], field=field)
                )
        for field, column in {
            "reorder_point_min": "reorder_point_min_microunits",
            "reorder_point_max": "reorder_point_max_microunits",
            "assembly_build_point": "assembly_build_point_microunits",
        }.items():
            if field in roots:
                values[column] = (
                    None
                    if desired.get(field) is None
                    else parse_quantity_micro_units(desired[field], field=field)
                )
    return values


def _apply_collection(
    connection: sa.Connection,
    handler: SnapshotHandler,
    collection: ChildCollection,
    desired: Mapping[str, Any],
) -> None:
    desired_rows = {
        str(item["id"]): item for item in desired.get(collection.name, [])
    }
    if collection.nested_owner_table is None:
        owner_condition = (
            collection.table.c[collection.owner_column] == desired["id"]
        )
    else:
        assert collection.nested_parent_column is not None
        owner_ids = set(
            connection.execute(
                sa.select(collection.nested_owner_table.c.id).where(
                    collection.nested_owner_table.c[
                        collection.nested_parent_column
                    ]
                    == desired["id"]
                )
            ).scalars()
        )
        owner_ids.update(
            str(row[collection.owner_column])
            for row in desired_rows.values()
            if row.get(collection.owner_column) is not None
        )
        owner_condition = collection.table.c[collection.owner_column].in_(owner_ids)
    stored = connection.execute(
        sa.select(collection.table).where(owner_condition)
    ).mappings().all()
    by_id = {str(row["id"]): dict(row) for row in stored}
    if "active" in collection.table.c:
        # Release partial-unique slots before role/rank/default/name swaps.
        connection.execute(
            collection.table.update()
            .where(owner_condition, collection.table.c.active.is_(True))
            .values(active=False)
        )
    for identifier, logical in desired_rows.items():
        values = collection.to_storage(logical)
        if collection.nested_owner_table is None:
            values[collection.owner_column] = desired["id"]
        values = {
            key: value for key, value in values.items() if key in collection.table.c
        }
        if identifier in by_id:
            connection.execute(
                collection.table.update()
                .where(collection.table.c.id == identifier)
                .values(**values)
            )
        else:
            connection.execute(collection.table.insert().values(**values))


def _apply_custom_values(
    connection: sa.Connection,
    handler: SnapshotHandler,
    desired: Mapping[str, Any],
    roots: set[str],
) -> None:
    if handler.record_type not in custom_fields.LIST_VALUE_SCOPES:
        return
    prefix = "custom_fields."
    patch = {
        field.removeprefix(prefix): desired.get(field)
        for field in roots
        if field.startswith(prefix)
    }
    if not patch:
        return
    try:
        custom_plan = custom_fields.plan_owner_value_patch(
            connection,
            record_type=handler.record_type,
            record_id=str(desired["id"]),
            patch=patch,
            creating=False,
        )
        custom_fields.apply_owner_value_plan(connection, custom_plan)
    except BookflowError as exc:
        raise _rule_conflict(handler, desired, exc) from exc


def _validate_custom_definition(
    connection: sa.Connection,
    current: Mapping[str, Any],
    desired: Mapping[str, Any],
    roots: set[str],
) -> None:
    record_id = str(current["id"])
    has_values = connection.execute(
        sa.select(schema.custom_field_values.c.id)
        .where(schema.custom_field_values.c.def_id == record_id)
        .limit(1)
    ).first() is not None
    if has_values and "kind" in roots and desired.get("kind") != current.get("kind"):
        raise _conflict(
            record_type="custom_field",
            record_id=record_id,
            field="kind",
            current_version=int(current["version"]),
            problem="definitions with values cannot change kind",
        )
    if "choices" in roots:
        active_values = set(
            connection.execute(
                sa.select(schema.custom_field_values.c.canonical_text).where(
                    schema.custom_field_values.c.def_id == record_id,
                    schema.custom_field_values.c.active.is_(True),
                )
            ).scalars()
        )
        desired_choices = {
            item["value"] for item in desired.get("choices", []) if item.get("active", True)
        }
        used_missing = sorted(active_values - desired_choices)
        if used_missing:
            raise _conflict(
                record_type="custom_field",
                record_id=record_id,
                field="choices",
                current_version=int(current["version"]),
                problem="active values use a choice the inverse would retire",
                values=used_missing,
            )


def _is_link_creation(entry: OriginalEntry) -> bool:
    return (
        entry.record_type == "customer_vendor_link"
        and entry.action == "link"
        and entry.before is None
        and entry.version_before is None
    )


def _restored_paths(entry: OriginalEntry) -> tuple[tuple[str, ...], ...]:
    if entry.action == "create" or _is_link_creation(entry):
        return (("active",),)
    assert entry.before is not None
    paths = _changed_paths(entry.before, entry.after)
    if entry.action in {"activate", "deactivate"}:
        paths = tuple(path for path in paths if path == ("active",))
    return paths


def _restored_value(entry: OriginalEntry, path: tuple[str, ...]) -> Any:
    if entry.action == "create" or _is_link_creation(entry):
        return False
    assert entry.before is not None
    return _path_value(entry.before, path)


def _inverse_action(entry: OriginalEntry) -> InverseAction:
    if entry.action in {"create", "activate"} or _is_link_creation(entry):
        return "deactivate"
    if entry.action == "deactivate":
        return "activate"
    return "update"


def _entry_order(entry: OriginalEntry) -> tuple[int, int, int]:
    """Put structural prerequisites first and destructive inverses last."""

    if entry.action == "link":
        # Release the relationship before endpoint snapshots are restored.
        return (0 if entry.record_type == "customer_vendor_link" else 1, 0, -entry.ordinal)
    if entry.action == "unlink":
        # Restore endpoint versions before reserving their one-to-one slots.
        return (1 if entry.record_type == "customer_vendor_link" else 0, 0, -entry.ordinal)
    if entry.action == "convert":
        # The created target must pass deactivation checks before the source is restored.
        return (3, 0, entry.ordinal)
    depth = int(entry.after.get("depth", 1))
    action = _inverse_action(entry)
    if action == "activate":
        return (0, depth, entry.ordinal)
    if action == "update":
        return (1, 0, -entry.ordinal)
    return (2, -depth, -entry.ordinal)


def _apply_entry(
    connection: sa.Connection,
    entry: OriginalEntry,
    *,
    actor_id: str,
    via: str,
    at: str,
    comparison_snapshot: Mapping[str, Any] | None = None,
) -> InverseResult:
    handler = HANDLERS.get(entry.record_type, entry.action)
    if handler is None:
        raise _not_undoable("unknown", "an audit entry has no inverse handler")
    current_row = _row(connection, handler.table, entry.record_id)
    current = handler.snapshotter(connection, entry.record_id)
    comparison = current if comparison_snapshot is None else comparison_snapshot
    action = _inverse_action(entry)

    paths = _restored_paths(entry)
    if entry.action == "create" or _is_link_creation(entry):
        if not _same_logical_value(
            _path_value(comparison, ("active",)),
            _path_value(entry.after, ("active",)),
        ):
            raise _conflict(
                record_type=entry.record_type,
                record_id=entry.record_id,
                field="active",
                current_version=int(current_row["version"]),
                problem="current state no longer matches the created state",
                conflicting_event=_conflicting_event(
                    connection,
                    entry=entry,
                    path=("active",),
                    current_version=int(current_row["version"]),
                ),
            )
        desired = deepcopy(comparison)
        desired["active"] = False
    else:
        assert entry.before is not None
        if not paths:
            raise _not_undoable("unknown", "the audit entry has no logical field change")
        for path in paths:
            current_value = _path_value(comparison, path)
            after_value = _path_value(entry.after, path)
            if not _same_logical_value(current_value, after_value):
                raise _conflict(
                    record_type=entry.record_type,
                    record_id=entry.record_id,
                    field=_path_text(path),
                    current_version=int(current_row["version"]),
                    problem="current state does not equal the original after-value",
                    conflicting_event=_conflicting_event(
                        connection,
                        entry=entry,
                        path=path,
                        current_version=int(current_row["version"]),
                    ),
                )
        desired = deepcopy(comparison)
        for path in paths:
            _set_path(desired, path, _restored_value(entry, path))

    roots = {path[0] for path in paths}
    if action == "deactivate":
        _validate_deactivation(connection, handler, current_row)
    elif action == "activate":
        try:
            list_service.plan_activation(connection, handler.table, current_row)
        except BookflowError as exc:
            raise _rule_conflict(handler, current_row, exc, field="active") from exc
    domain_roots = roots
    if entry.action == "convert":
        # Ordinary other-name activation rejects a still-linked conversion source.
        # Validate the target state that this same inverse will persist instead.
        activation_target = {
            **current_row,
            "converted_to_type": desired.get("converted_to_type"),
            "converted_to_id": desired.get("converted_to_id"),
        }
        try:
            list_service.plan_activation(connection, handler.table, activation_target)
        except BookflowError as exc:
            raise _rule_conflict(handler, current_row, exc, field="active") from exc
        domain_roots = roots - {"active", "converted_to_type", "converted_to_id"}
    _validate_changed_references(connection, handler, desired, roots)
    if handler.record_type == "customer_vendor_link":
        _validate_link_target(connection, current_row, desired)
    if handler.record_type == "custom_field":
        _validate_custom_definition(connection, comparison, desired, roots)
    _validate_domain_target(
        connection,
        handler,
        comparison,
        desired,
        domain_roots,
        actor_id=actor_id,
        via=via,
        at=at,
    )

    collection_roots = {collection.name for collection in handler.collections}
    for collection in handler.collections:
        if collection.name in roots:
            _apply_collection(connection, handler, collection, desired)

    _apply_custom_values(connection, handler, desired, roots)
    owner_roots = roots - collection_roots
    values = _owner_storage_values(handler, desired, owner_roots)

    if handler.hierarchical and {"name", "parent_id"} & owner_roots:
        definition = get_list_definition(handler.noun)
        assert definition is not None
        try:
            hierarchy = list_service.plan_reparent(
                connection,
                handler.table,
                definition,
                current_row,
                parent_id=desired.get("parent_id"),
                name=desired.get("name"),
            )
            list_service.apply_hierarchy_plan(connection, handler.table, hierarchy)
        except BookflowError as exc:
            raise _rule_conflict(handler, current_row, exc) from exc
        values.pop("name", None)
        values.pop("name_key", None)

    values.update(
        version=int(current_row["version"]) + 1,
        updated_at=at,
        updated_by=actor_id,
        updated_via=via,
    )
    try:
        connection.execute(
            handler.table.update()
            .where(handler.table.c.id == entry.record_id)
            .values(**values)
        )
        if handler.record_type == "custom_field" and "active" in owner_roots:
            connection.execute(
                schema.custom_field_scopes.update()
                .where(schema.custom_field_scopes.c.definition_id == entry.record_id)
                .values(definition_active=desired["active"])
            )
    except sa.exc.IntegrityError as exc:
        raise _conflict(
            record_type=entry.record_type,
            record_id=entry.record_id,
            current_version=int(current_row["version"]),
            problem="a current uniqueness or reference constraint rejects the inverse",
            constraint="storage",
        ) from exc

    after = handler.snapshotter(connection, entry.record_id)
    touched = Touched(
        entry.record_type,
        entry.record_id,
        action,
        int(current_row["version"]),
        int(current_row["version"]) + 1,
        after,
        current,
        db="company",
    )
    return InverseResult(
        entry=entry,
        action=action,
        restored_fields=tuple(_path_text(path) for path in paths),
        touched=touched,
    )


@contextmanager
def _planning_connection(s: Session):
    raw = sqlite3.connect(":memory:", isolation_level=None)
    engine = None
    connection = None
    try:
        s.company.raw.backup(raw)
        raw.execute("PRAGMA foreign_keys=ON")
        engine = sa.create_engine(
            "sqlite://",
            creator=lambda: raw,
            poolclass=sa.pool.StaticPool,
        )
        connection = engine.connect()
        yield connection
    finally:
        if connection is not None:
            connection.close()
        if engine is not None:
            engine.dispose()
        try:
            raw.close()
        except sqlite3.ProgrammingError:
            pass


def apply_inverse(
    connection: sa.Connection,
    request: UndoRequest,
    *,
    actor_id: str,
    via: str,
    at: str,
) -> tuple[InverseResult, ...]:
    """Validate and apply every inverse in deterministic dependency-safe order."""

    ordered = sorted(request.entries, key=_entry_order)
    starting_snapshots: dict[str, dict[str, Any]] = {}
    for entry in ordered:
        handler = HANDLERS.get(entry.record_type, entry.action)
        if handler is None:
            raise _not_undoable(
                request.event["id"], "an audit entry has no inverse handler"
            )
        starting_snapshots[entry.id] = handler.snapshotter(
            connection, entry.record_id
        )

    interim = []
    for entry in ordered:
        interim.append(
            _apply_entry(
                connection,
                entry,
                actor_id=actor_id,
                via=via,
                at=at,
                comparison_snapshot=starting_snapshots[entry.id],
            )
        )

    results = []
    for result in interim:
        entry = result.entry
        handler = HANDLERS.get(entry.record_type, entry.action)
        assert handler is not None
        final_snapshot = handler.snapshotter(connection, entry.record_id)
        for path in _restored_paths(entry):
            if not _same_logical_value(
                _path_value(final_snapshot, path), _restored_value(entry, path)
            ):
                raise _conflict(
                    record_type=entry.record_type,
                    record_id=entry.record_id,
                    field=_path_text(path),
                    current_version=result.touched.version_after,
                    problem="the assembled inverse could not restore the requested field",
                )
        results.append(
            InverseResult(
                entry=entry,
                action=result.action,
                restored_fields=result.restored_fields,
                touched=Touched(
                    entry.record_type,
                    entry.record_id,
                    result.action,
                    result.touched.version_before,
                    result.touched.version_after,
                    final_snapshot,
                    starting_snapshots[entry.id],
                    db="company",
                ),
            )
        )
    return tuple(results)


def plan_undo(s: Session, event_id: str, *, actor_id: str, via: str, at: str) -> UndoPlan:
    """Prepare an undo and prove the complete inverse on a private database copy."""

    request = load_request(s, event_id)
    with _planning_connection(s) as connection:
        try:
            results = apply_inverse(
                connection, request, actor_id=actor_id, via=via, at=at
            )
        except sa.exc.IntegrityError as exc:
            raise BookflowError(
                "E_UNDO_CONFLICT",
                details={
                    "event_id": request.event["id"],
                    "problem": "the assembled inverse violates a storage constraint",
                },
            ) from exc
    return UndoPlan(request=request, event_id=new_id(), at=at, results=results)


def assert_not_compensated(connection: sa.Connection, original_event_id: str) -> None:
    existing = connection.execute(
        sa.select(schema.audit_events.c.id).where(
            schema.audit_events.c.undo_of_event_id == original_event_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise BookflowError(
            "E_ALREADY_UNDONE",
            details={"event_id": original_event_id, "undo_event_id": existing},
        )


def write_undo_event(
    s: Session,
    ctx: Context,
    plan: UndoPlan,
    results: Sequence[InverseResult],
) -> None:
    """Append the compensating event and its ordered entries inside the caller's transaction."""

    assert_not_compensated(s.company.conn, plan.request.event["id"])
    summary = (
        f"undid event {plan.request.event['id']} "
        f"({plan.request.event['command']})"
    )
    s.company.conn.execute(
        schema.audit_events.insert().values(
            id=plan.event_id,
            seq=audit.next_seq(s.company, schema.audit_events),
            at=plan.at,
            command="undo",
            actor_id=s.actor.id,
            actor_kind=s.actor.kind,
            on_behalf_of=ctx.on_behalf_of,
            interface=ctx.interface.value,
            client_name=ctx.client_name,
            client_version=ctx.client_version,
            client_host=ctx.client_host,
            session_id=ctx.session_id,
            request_id=ctx.request_id,
            idempotency_key=ctx.idempotency_key,
            reason=ctx.reason,
            directive_id=ctx.directive_id,
            directive_code=s.directive_code,
            source_ref=ctx.source_ref,
            undo_of_event_id=plan.request.event["id"],
            summary=summary,
        )
    )
    for result in results:
        touched = result.touched
        s.company.conn.execute(
            schema.audit_entries.insert().values(
                id=new_id(),
                event_id=plan.event_id,
                record_type=touched.record_type,
                record_id=touched.record_id,
                action=touched.action,
                version_before=touched.version_before,
                version_after=touched.version_after,
                after=audit.encode_snapshot(touched.after),
                before=audit.encode_snapshot(touched.before),
            )
        )


__all__ = [
    "HANDLERS",
    "InverseResult",
    "OriginalEntry",
    "SnapshotHandler",
    "UndoHandlerRegistry",
    "UndoPlan",
    "UndoRequest",
    "apply_inverse",
    "assert_not_compensated",
    "load_request",
    "plan_undo",
    "write_undo_event",
]
