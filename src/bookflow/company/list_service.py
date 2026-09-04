"""Shared storage rules for Row 5 company lists.

Domain services pass their table and declarative :class:`ListDefinition` into
this module.  The module intentionally does not import any noun service or the
company schema, so using one list never loads the others.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import difflib
import unicodedata
from typing import Any, Literal

import sqlalchemy as sa

from bookflow.company.lists import ListDefinition, MAX_HIERARCHY_DEPTH, hierarchy_projection
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.registry import Touched
from bookflow.core.session import now_iso
from bookflow.core.versioning import HistoryEntry, UpdateMeta, check_update


ExpressionMap = Mapping[str, sa.ColumnElement[Any]]
ParentRule = Callable[[Mapping[str, Any], Mapping[str, Any]], None]
Invariant = Callable[[Mapping[str, Any]], None]


def _connection(db_or_connection: Any) -> sa.Connection:
    connection = getattr(db_or_connection, "conn", db_or_connection)
    if not isinstance(connection, sa.Connection):
        raise TypeError("a Bookflow Database or SQLAlchemy Connection is required")
    return connection


def _not_found(selector: Any) -> BookflowError:
    return BookflowError("E_RECORD_NOT_FOUND", details={"selector": selector, "suggestions": []})


def normalize_lookup_key(value: Any) -> str:
    """Return the trimmed NFC/case-folded form used for selector lookup.

    Unlike a leaf-name validator this accepts ``:`` because hierarchical
    selectors are complete visible names.
    """
    if not isinstance(value, str):
        raise _not_found(value)
    value = unicodedata.normalize("NFC", value.strip())
    if not value:
        raise _not_found(value)
    return unicodedata.normalize("NFC", value.casefold())


def _visible_query(table: sa.Table, visible: sa.ColumnElement[bool] | None) -> sa.Select:
    query = sa.select(table)
    return query.where(visible) if visible is not None else query


def _selector_field(definition: ListDefinition) -> str:
    fields = [field for field in definition.selector_fields if field != definition.identifier]
    if len(fields) != 1:
        raise ValueError(f"{definition.noun}: exactly one canonical visible selector is required")
    return fields[0]


def _key_expression(table: sa.Table, field: str) -> sa.ColumnElement[Any]:
    key_name = f"{field}_key"
    if key_name in table.c:
        return table.c[key_name]
    if field not in table.c:
        raise ValueError(f"{table.name}: no storage expression for {field!r}")
    return sa.func.lower(table.c[field])


def _suggest(needle: str, candidates: Sequence[str], limit: int) -> list[str]:
    folded = needle.casefold()
    contains = [candidate for candidate in candidates if folded in candidate.casefold() or candidate.casefold() in folded]
    close = difflib.get_close_matches(needle, list(candidates), n=limit, cutoff=0.5)
    result: list[str] = []
    for candidate in (*contains, *close):
        if candidate not in result:
            result.append(candidate)
        if len(result) == limit:
            break
    return result


def resolve_selector(
    db_or_connection: Any,
    table: sa.Table,
    definition: ListDefinition,
    selector: Any,
    *,
    visible: sa.ColumnElement[bool] | None = None,
    suggestion_limit: int = 3,
) -> dict[str, Any]:
    """Resolve by id first and then the canonical visible name.

    ``visible`` must contain every authorization restriction when the table is
    not already isolated by a company database.  Suggestions are selected
    from that same query, so an inaccessible and a nonexistent id have the
    same error document.
    """
    connection = _connection(db_or_connection)
    base = _visible_query(table, visible)
    trimmed = unicodedata.normalize("NFC", selector.strip()) if isinstance(selector, str) else selector
    if isinstance(trimmed, str) and is_ulid(trimmed):
        row = connection.execute(base.where(table.c[definition.identifier] == normalize_ulid(trimmed))).mappings().first()
        if row is not None:
            return dict(row)

    selector_field = _selector_field(definition)
    key = normalize_lookup_key(trimmed)
    row = connection.execute(base.where(_key_expression(table, selector_field) == key)).mappings().first()
    if row is not None:
        return dict(row)

    display = table.c[selector_field]
    candidates = [str(value) for value in connection.execute(base.with_only_columns(display).order_by(table.c[definition.identifier])).scalars()]
    error = _not_found(selector)
    error.details["suggestions"] = _suggest(str(trimmed), candidates, suggestion_limit)
    raise error


def assert_name_available(
    db_or_connection: Any,
    table: sa.Table,
    definition: ListDefinition,
    visible_name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    """Reject a canonical selector-name collision before a write."""
    connection = _connection(db_or_connection)
    selector_field = _selector_field(definition)
    query = sa.select(table.c[definition.identifier]).where(
        _key_expression(table, selector_field) == normalize_lookup_key(visible_name)
    )
    if exclude_id is not None:
        query = query.where(table.c[definition.identifier] != exclude_id)
    if connection.execute(query).first() is not None:
        raise BookflowError(
            "E_NAME_TAKEN",
            details={"field": selector_field, "value": visible_name},
        )


def _expression(
    table: sa.Table,
    field: str,
    supplied: ExpressionMap | None,
    *,
    required: bool = True,
) -> sa.ColumnElement[Any] | None:
    if supplied is not None and field in supplied:
        return supplied[field]
    if field in table.c:
        return table.c[field]
    if required:
        raise ValueError(f"{table.name}: no query expression for declared field {field!r}")
    return None


def _search_expression(table: sa.Table, field: str, supplied: ExpressionMap | None) -> sa.ColumnElement[Any] | None:
    if supplied is not None and field in supplied:
        return supplied[field]
    key_name = f"{field}_key"
    if key_name in table.c:
        return table.c[key_name]
    if field in table.c:
        return sa.func.lower(table.c[field])
    return None


def list_statement(
    table: sa.Table,
    definition: ListDefinition,
    *,
    query: str | None = None,
    filters: Sequence[str] = (),
    sort: str | None = None,
    direction: Literal["asc", "desc"] = "asc",
    include_inactive: bool = False,
    search_expressions: ExpressionMap | None = None,
    filter_expressions: ExpressionMap | None = None,
    sort_expressions: ExpressionMap | None = None,
    visible: sa.ColumnElement[bool] | None = None,
) -> sa.Select:
    """Build the declared search/filter/sort selection without materializing it."""
    if direction not in ("asc", "desc"):
        raise BookflowError(
            "E_LIST_FILTER",
            details={"problem": "sort direction accepts only asc or desc", "direction": direction},
        )
    parsed_filters = definition.parse_filters(filters)
    sort_terms = definition.resolve_sort(sort, direction)
    statement = _visible_query(table, visible)

    explicit_active = any(item.field == "active" for item in parsed_filters)
    if "active" in table.c and not include_inactive and not explicit_active:
        statement = statement.where(table.c.active.is_(True))

    for item in parsed_filters:
        expression = _expression(table, item.field, filter_expressions)
        if isinstance(item.value, bool):
            statement = statement.where(expression.is_(item.value))
        else:
            statement = statement.where(expression == item.value)

    if query is not None and query.strip():
        needle = normalize_lookup_key(query)
        expressions = [
            expression
            for field in definition.search_fields
            if (expression := _search_expression(table, field, search_expressions)) is not None
        ]
        if not expressions:
            raise ValueError(f"{definition.noun}: none of its declared search fields has a query expression")
        # autoescape makes %, _, and the escape character literal user input.
        statement = statement.where(sa.or_(*(expression.contains(needle, autoescape=True) for expression in expressions)))

    order: list[sa.ColumnElement[Any]] = []
    for term in sort_terms:
        expression = _expression(table, term.field, sort_expressions)
        expression = expression.desc() if term.direction == "desc" else expression.asc()
        if term.nulls_last:
            expression = expression.nulls_last()
        order.append(expression)
    statement = statement.order_by(*order)
    return statement


def list_rows(
    db_or_connection: Any,
    table: sa.Table,
    definition: ListDefinition,
    **options: Any,
) -> list[dict[str, Any]]:
    """Return complete compatibility enumeration using the shared selection."""
    connection = _connection(db_or_connection)
    statement = list_statement(table, definition, **options)
    return [dict(row) for row in connection.execute(statement).mappings().all()]


@dataclass(frozen=True)
class HierarchyUpdate:
    id: str
    name: str
    name_key: str
    full_name: str
    full_name_key: str
    depth: int
    path: str


@dataclass(frozen=True)
class HierarchyPlan:
    root_id: str
    updates: tuple[HierarchyUpdate, ...]

    @property
    def affected_descendant_ids(self) -> tuple[str, ...]:
        return tuple(update.id for update in self.updates[1:])


def _row_by_id(connection: sa.Connection, table: sa.Table, record_id: str) -> dict[str, Any]:
    row = connection.execute(sa.select(table).where(table.c.id == record_id)).mappings().first()
    if row is None:
        raise _not_found(record_id)
    return dict(row)


def _hierarchy_rows(connection: sa.Connection, table: sa.Table, root: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = str(root["path"])
    rows = connection.execute(
        sa.select(table)
        .where(table.c.path.like(path + "%"))
        .order_by(table.c.depth, table.c.path, table.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def plan_reparent(
    db_or_connection: Any,
    table: sa.Table,
    definition: ListDefinition,
    row: Mapping[str, Any],
    *,
    parent_id: str | None,
    name: str | None = None,
    parent_rule: ParentRule | None = None,
) -> HierarchyPlan:
    """Validate a rename/reparent and project the complete resulting subtree."""
    if definition.hierarchy is None:
        raise ValueError(f"{definition.noun} is not hierarchical")
    connection = _connection(db_or_connection)
    root = dict(row)
    subtree = _hierarchy_rows(connection, table, root)
    subtree_ids = {item["id"] for item in subtree}
    if parent_id in subtree_ids:
        raise BookflowError("E_HIERARCHY_CYCLE", details={"record_id": root["id"], "parent_id": parent_id})

    parent = None
    if parent_id is not None:
        parent = _row_by_id(connection, table, parent_id)
        if not parent.get("active", True):
            raise BookflowError(
                "E_INACTIVE_REFERENCE",
                details={"field": "parent_id", "record_type": definition.record_type, "record_id": parent_id},
            )
        if parent_rule is not None:
            parent_rule(root, parent)

    root_name = root["name"] if name is None else name
    root_depth = 1 if parent is None else int(parent["depth"]) + 1
    root_full, root_key = hierarchy_projection(
        root_name,
        None if parent is None else str(parent["full_name"]),
        depth=root_depth,
    )
    root_leaf, root_leaf_key = _leaf_name(root_name)
    root_path = f"/{root['id']}/" if parent is None else f"{parent['path']}{root['id']}/"
    projected: dict[str, HierarchyUpdate] = {
        root["id"]: HierarchyUpdate(root["id"], root_leaf, root_leaf_key, root_full, root_key, root_depth, root_path)
    }

    for child in subtree:
        if child["id"] == root["id"]:
            continue
        projected_parent = projected.get(child["parent_id"])
        if projected_parent is None:
            raise ValueError(f"{definition.noun}: subtree row {child['id']} has no projected parent")
        depth = projected_parent.depth + 1
        full_name, full_name_key = hierarchy_projection(child["name"], projected_parent.full_name, depth=depth)
        leaf, leaf_key = _leaf_name(child["name"])
        projected[child["id"]] = HierarchyUpdate(
            child["id"], leaf, leaf_key, full_name, full_name_key, depth,
            f"{projected_parent.path}{child['id']}/",
        )

    updates = tuple(projected[item["id"]] for item in subtree)
    keys = [update.full_name_key for update in updates]
    if len(keys) != len(set(keys)):
        raise BookflowError("E_NAME_TAKEN", details={"field": "full_name", "value": root_full})
    collisions = connection.execute(
        sa.select(table.c.id)
        .where(table.c.full_name_key.in_(keys), table.c.id.not_in(subtree_ids))
        .order_by(table.c.id)
    ).scalars().all()
    if collisions:
        raise BookflowError("E_NAME_TAKEN", details={"field": "full_name", "value": root_full})
    return HierarchyPlan(root_id=root["id"], updates=updates)


def _leaf_name(value: Any) -> tuple[str, str]:
    # Imported lazily from the definition module to keep one validator as truth.
    from bookflow.company.lists import normalize_display_name

    return normalize_display_name(value)


def apply_hierarchy_plan(db_or_connection: Any, table: sa.Table, plan: HierarchyPlan) -> None:
    """Write only derived hierarchy projections; caller owns root version/audit."""
    connection = _connection(db_or_connection)
    for update in plan.updates:
        values = {
            "full_name": update.full_name,
            "full_name_key": update.full_name_key,
            "depth": update.depth,
            "path": update.path,
        }
        if update.id == plan.root_id:
            values.update(name=update.name, name_key=update.name_key)
        connection.execute(table.update().where(table.c.id == update.id).values(**values))


def hierarchy_ancestors(db_or_connection: Any, table: sa.Table, row: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return nearest-to-farthest ancestors and reject corrupt cycles."""
    connection = _connection(db_or_connection)
    ancestors: list[dict[str, Any]] = []
    seen = {str(row["id"])}
    parent_id = row.get("parent_id")
    while parent_id is not None:
        if parent_id in seen:
            raise BookflowError("E_HIERARCHY_CYCLE", details={"record_id": row["id"], "parent_id": parent_id})
        seen.add(parent_id)
        parent = _row_by_id(connection, table, parent_id)
        ancestors.append(parent)
        parent_id = parent.get("parent_id")
        if len(ancestors) >= MAX_HIERARCHY_DEPTH:
            raise BookflowError("E_HIERARCHY_DEPTH", details={"record_id": row["id"], "maximum": MAX_HIERARCHY_DEPTH})
    return tuple(ancestors)


def plan_activation(
    db_or_connection: Any,
    table: sa.Table,
    row: Mapping[str, Any],
    *,
    invariant: Invariant | None = None,
) -> tuple[dict[str, Any], ...]:
    """Validate activation, including the no-op case, without mutating."""
    if invariant is not None:
        invariant(row)
    if "parent_id" in table.c:
        inactive = [ancestor["id"] for ancestor in hierarchy_ancestors(db_or_connection, table, row) if not ancestor["active"]]
        if inactive:
            raise BookflowError("E_INACTIVE_REFERENCE", details={"field": "parent_id", "record_ids": inactive})
    return () if row.get("active") else (dict(row),)


def plan_deactivation(
    db_or_connection: Any,
    table: sa.Table,
    row: Mapping[str, Any],
    *,
    cascade: bool = False,
    invariant: Invariant | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return active rows to deactivate in deterministic parent-first order."""
    if invariant is not None:
        invariant(row)
    active_descendants: list[dict[str, Any]] = []
    if "path" in table.c:
        active_descendants = [
            item for item in _hierarchy_rows(_connection(db_or_connection), table, row)
            if item["id"] != row["id"] and item["active"]
        ]
        if active_descendants and not cascade:
            raise BookflowError(
                "E_ACTIVE_DEPENDENTS",
                details={"record_id": row["id"], "count": len(active_descendants)},
            )
    if not row.get("active"):
        if active_descendants:
            raise BookflowError(
                "E_ACTIVE_DEPENDENTS",
                details={"record_id": row["id"], "count": len(active_descendants)},
            )
        return ()
    return (dict(row), *active_descendants)


def require_active_reference(
    db_or_connection: Any,
    table: sa.Table,
    record_id: str,
    *,
    field: str,
    record_type: str,
) -> dict[str, Any]:
    """Resolve a same-company foreign reference and require active state."""
    row = _connection(db_or_connection).execute(sa.select(table).where(table.c.id == record_id)).mappings().first()
    if row is None:
        raise _not_found(record_id)
    result = dict(row)
    if not result.get("active", True):
        raise BookflowError(
            "E_INACTIVE_REFERENCE",
            details={"field": field, "record_type": record_type, "record_id": record_id},
        )
    return result


@dataclass(frozen=True)
class DependentReference:
    noun: str
    table: sa.Table
    foreign_key: str
    active_field: str = "active"
    target_type_field: str | None = None
    target_type: str | None = None


class DependencyRegistry:
    """Explicit deactivation blockers shared by list writes and undo."""

    def __init__(self) -> None:
        self._references: dict[str, list[DependentReference]] = {}

    def register(self, target_noun: str, reference: DependentReference) -> None:
        if reference.foreign_key not in reference.table.c:
            raise ValueError(f"{reference.table.name}: unknown dependent key {reference.foreign_key}")
        if reference.active_field not in reference.table.c:
            raise ValueError(f"{reference.table.name}: unknown active field {reference.active_field}")
        if reference.target_type_field is not None and reference.target_type_field not in reference.table.c:
            raise ValueError(f"{reference.table.name}: unknown target-type field {reference.target_type_field}")
        references = self._references.setdefault(target_noun, [])
        if reference in references:
            raise ValueError(f"{target_noun}: duplicate dependent reference")
        references.append(reference)

    def active_counts(self, db_or_connection: Any, target_noun: str, target_id: str) -> tuple[dict[str, Any], ...]:
        connection = _connection(db_or_connection)
        counts: dict[str, int] = {}
        for reference in self._references.get(target_noun, ()):
            table = reference.table
            where = [
                table.c[reference.foreign_key] == target_id,
                table.c[reference.active_field].is_(True),
            ]
            if reference.target_type_field is not None:
                where.append(table.c[reference.target_type_field] == reference.target_type)
            count = int(connection.execute(sa.select(sa.func.count()).select_from(table).where(*where)).scalar_one())
            if count:
                counts[reference.noun] = counts.get(reference.noun, 0) + count
        return tuple({"record_type": noun.replace("-", "_"), "count": counts[noun]} for noun in sorted(counts))

    def require_unused(self, db_or_connection: Any, target_noun: str, target_id: str) -> None:
        dependents = self.active_counts(db_or_connection, target_noun, target_id)
        if dependents:
            raise BookflowError("E_RECORD_IN_USE", details={"record_id": target_id, "dependents": list(dependents)})


def assert_no_floats(value: Any, *, path: str = "input") -> None:
    """Reject binary floating-point before it can enter storage or snapshots."""
    if isinstance(value, float):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": path, "problem": "floats are not accepted"}]})
    if isinstance(value, Mapping):
        for key, child in value.items():
            assert_no_floats(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_no_floats(child, path=f"{path}.{index}")


def create_metadata(actor_id: str, via: str, *, record_id: str | None = None, at: str | None = None) -> dict[str, Any]:
    """Construct the common version/provenance fields for a new row."""
    at = at or now_iso()
    return {
        "id": record_id or new_id(),
        "version": 1,
        "created_at": at,
        "created_by": actor_id,
        "created_via": via,
        "updated_at": at,
        "updated_by": actor_id,
        "updated_via": via,
    }


def update_metadata(row: Mapping[str, Any], actor_id: str, via: str, *, version: int | None = None, at: str | None = None) -> dict[str, Any]:
    """Construct only the common fields changed by a versioned write."""
    return {
        "version": int(row["version"]) + 1 if version is None else version,
        "updated_at": at or now_iso(),
        "updated_by": actor_id,
        "updated_via": via,
    }


def plan_update_metadata(
    row: Mapping[str, Any],
    *,
    changes: Iterable[str],
    expected_version: int | None,
    actor_id: str,
    window_seconds: int,
    history_since: Callable[[int], list[HistoryEntry]],
    current_writer: HistoryEntry | None,
) -> UpdateMeta:
    """Apply the existing optimistic/disjoint-merge rule to list changes."""
    return check_update(
        current_version=int(row["version"]),
        current_updated_at=row.get("updated_at"),
        current_writer=current_writer,
        changes=set(changes),
        expected_version=expected_version,
        history_since=history_since,
        actor_id=actor_id,
        window_seconds=window_seconds,
    )


def blind_write_warning(meta: UpdateMeta) -> str | None:
    """Return the mandatory Row 5 warning for a changed blind write."""
    if meta.previous_version is None or not meta.changed_fields:
        return None
    fields = ", ".join(meta.changed_fields)
    return f"Blind write: version {meta.previous_version} and fields {fields} were not compared."


def insert_row(db_or_connection: Any, table: sa.Table, values: Mapping[str, Any]) -> dict[str, Any]:
    """Insert a validated row and return its stored mapping."""
    assert_no_floats(values)
    unknown = set(values) - set(table.c.keys())
    if unknown:
        raise ValueError(f"{table.name}: unknown storage fields {sorted(unknown)}")
    connection = _connection(db_or_connection)
    connection.execute(table.insert().values(**dict(values)))
    return _row_by_id(connection, table, str(values["id"]))


def update_row(
    db_or_connection: Any,
    table: sa.Table,
    row: Mapping[str, Any],
    changes: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    version: int | None = None,
) -> dict[str, Any]:
    """Update logical fields plus common provenance and return the stored row."""
    assert_no_floats(changes)
    unknown = set(changes) - set(table.c.keys())
    if unknown:
        raise ValueError(f"{table.name}: unknown storage fields {sorted(unknown)}")
    values = {**dict(changes), **update_metadata(row, actor_id, via, version=version)}
    connection = _connection(db_or_connection)
    connection.execute(table.update().where(table.c.id == row["id"]).values(**values))
    return _row_by_id(connection, table, str(row["id"]))


def touched_create(record_type: str, row: Mapping[str, Any]) -> Touched:
    return Touched(record_type, str(row["id"]), "create", None, int(row["version"]), dict(row), db="company")


def touched_update(record_type: str, before: Mapping[str, Any], after: Mapping[str, Any], action: str = "update") -> Touched:
    return Touched(
        record_type,
        str(after["id"]),
        action,
        int(before["version"]),
        int(after["version"]),
        dict(after),
        before=dict(before),
        db="company",
    )


@dataclass(frozen=True)
class ChildReconciliation:
    active: tuple[dict[str, Any], ...]
    retired: tuple[dict[str, Any], ...]
    inactive: tuple[dict[str, Any], ...]
    created_ids: tuple[str, ...]
    retained_ids: tuple[str, ...]

    @property
    def all_rows(self) -> tuple[dict[str, Any], ...]:
        return (*self.active, *self.retired, *self.inactive)


def reconcile_children(
    existing: Sequence[Mapping[str, Any]],
    submitted: Sequence[Mapping[str, Any]],
    *,
    semantic_key: str | Callable[[Mapping[str, Any]], Any] | None = None,
) -> ChildReconciliation:
    """Plan complete-replacement semantics for an owned child collection."""
    if isinstance(submitted, (str, bytes)) or not isinstance(submitted, Sequence):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "collection", "problem": "must be an array"}]})
    by_id = {str(row["id"]): dict(row) for row in existing}
    seen_ids: set[str] = set()
    seen_semantic: set[Any] = set()
    active: list[dict[str, Any]] = []
    created: list[str] = []
    retained: list[str] = []

    for position, raw in enumerate(submitted):
        if not isinstance(raw, Mapping):
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": f"collection.{position}", "problem": "must be an object"}]})
        assert_no_floats(raw, path=f"collection.{position}")
        item = dict(raw)
        if "position" in item or "active" in item:
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": f"collection.{position}", "problem": "position and active are managed fields"}]})
        retained_id = item.pop("id", None)
        if retained_id is None:
            child_id = new_id()
            base: dict[str, Any] = {}
            created.append(child_id)
        else:
            if not isinstance(retained_id, str) or retained_id in seen_ids:
                raise BookflowError("E_VALIDATION", details={"fields": [{"field": f"collection.{position}.id", "problem": "duplicate or malformed retained id"}]})
            base = by_id.get(retained_id, {})
            if not base:
                raise _not_found(retained_id)
            if not base.get("active", True):
                raise BookflowError("E_VALIDATION", details={"fields": [{"field": f"collection.{position}.id", "problem": "a retired child cannot be reactivated by ordinary input"}]})
            child_id = retained_id
            retained.append(child_id)
        seen_ids.add(child_id)
        merged = {**base, **item, "id": child_id, "position": position, "active": True}
        if semantic_key is not None:
            value = semantic_key(merged) if callable(semantic_key) else merged.get(semantic_key)
            if isinstance(value, str):
                value = normalize_lookup_key(value)
            if value in seen_semantic:
                raise BookflowError("E_VALIDATION", details={"fields": [{"field": f"collection.{position}", "problem": "duplicate semantic key"}]})
            seen_semantic.add(value)
        active.append(merged)

    retired = [
        {**row, "active": False}
        for child_id, row in sorted(by_id.items(), key=lambda pair: (pair[1].get("position", 0), pair[0]))
        if row.get("active", True) and child_id not in seen_ids
    ]
    inactive = [
        row for _, row in sorted(by_id.items(), key=lambda pair: (pair[1].get("position", 0), pair[0]))
        if not row.get("active", True)
    ]
    return ChildReconciliation(
        tuple(active), tuple(retired), tuple(inactive), tuple(created), tuple(retained)
    )


def aggregate_snapshot(
    owner: Mapping[str, Any],
    *,
    collections: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    custom_values: Mapping[str, Any] | None = None,
    endpoint_values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic owner snapshot including aggregate identities."""
    snapshot = dict(owner)
    assert_no_floats(snapshot, path="snapshot")
    collections = collections or {}
    for name in sorted(collections):
        rows = [dict(row) for row in collections[name]]
        rows.sort(key=lambda row: (row.get("position", 0), str(row.get("id", ""))))
        assert_no_floats(rows, path=f"snapshot.{name}")
        snapshot[name] = [row for row in rows if row.get("active", True)]
        snapshot[f"_{name}_identities"] = [
            {"id": row.get("id"), "active": bool(row.get("active", True))} for row in rows
        ]
    if custom_values is not None:
        assert_no_floats(custom_values, path="snapshot.custom_fields")
        snapshot["custom_fields"] = {key: custom_values[key] for key in sorted(custom_values)}
    if endpoint_values is not None:
        assert_no_floats(endpoint_values, path="snapshot.endpoints")
        snapshot.update({key: endpoint_values[key] for key in sorted(endpoint_values)})
    return snapshot


__all__ = [
    "ChildReconciliation",
    "DependencyRegistry",
    "DependentReference",
    "HierarchyPlan",
    "HierarchyUpdate",
    "aggregate_snapshot",
    "apply_hierarchy_plan",
    "assert_name_available",
    "assert_no_floats",
    "blind_write_warning",
    "create_metadata",
    "hierarchy_ancestors",
    "insert_row",
    "list_rows",
    "list_statement",
    "normalize_lookup_key",
    "plan_activation",
    "plan_deactivation",
    "plan_reparent",
    "plan_update_metadata",
    "reconcile_children",
    "require_active_reference",
    "resolve_selector",
    "touched_create",
    "touched_update",
    "update_metadata",
    "update_row",
]
