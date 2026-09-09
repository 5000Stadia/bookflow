"""Pure projections of Pydantic and SQLAlchemy contracts for documentation."""

from __future__ import annotations

import inspect
import json
import types
from dataclasses import dataclass, replace
from enum import Enum
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter
from pydantic_core import PydanticUndefined


@dataclass(frozen=True)
class FieldDoc:
    path: str
    type: str
    required: bool
    nullable: bool
    default: str
    description: str
    constraints: str
    secret: bool
    leaf: bool


def _optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _optional(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        args = get_args(annotation)
        nullable = type(None) in args
        remaining = tuple(arg for arg in args if arg is not type(None))
        if len(remaining) == 1:
            base, inner_nullable = _optional(remaining[0])
            return base, nullable or inner_nullable
        return remaining, nullable
    return annotation, False


def _base_model(annotation: Any) -> type[BaseModel] | None:
    base, _ = _optional(annotation)
    if getattr(base, "__pydantic_root_model__", False):
        return _base_model(base.model_fields["root"].annotation)
    if inspect.isclass(base) and issubclass(base, BaseModel):
        return base
    origin = get_origin(base)
    if origin in (list, tuple, set):
        args = get_args(base)
        if args and inspect.isclass(args[0]) and issubclass(args[0], BaseModel):
            return args[0]
    return None


def type_name(annotation: Any) -> str:
    base, nullable = _optional(annotation)
    if isinstance(base, tuple):
        text = " | ".join(type_name(arg).removesuffix(" | null") for arg in base)
    else:
        origin = get_origin(base)
        args = get_args(base)
        if origin is Literal:
            text = "literal[" + ", ".join(json.dumps(value) for value in args) + "]"
        elif origin in (list, tuple, set):
            text = f"array[{type_name(args[0]) if args else 'any'}]"
        elif origin is dict:
            key = type_name(args[0]) if args else "string"
            value = type_name(args[1]) if len(args) > 1 else "any"
            text = f"object[{key}, {value}]"
        elif inspect.isclass(base) and issubclass(base, Enum):
            text = "enum[" + ", ".join(json.dumps(item.value) for item in base) + "]"
        elif getattr(base, "__pydantic_root_model__", False):
            text = type_name(base.model_fields["root"].annotation)
        elif inspect.isclass(base) and issubclass(base, BaseModel):
            text = "object"
        else:
            text = {str: "string", int: "integer", float: "number", bool: "boolean", Any: "any"}.get(base, getattr(base, "__name__", str(base)))
    return text + (" | null" if nullable else "")


def _default(field: Any) -> str:
    if field.is_required():
        return "—"
    extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
    if extra.get("generated"):
        return "generated"
    if field.default is not PydanticUndefined:
        value = field.default
    elif field.default_factory is not None:
        try:
            value = field.default_factory()
        except Exception:  # pragma: no cover - current factories are inert containers
            return "factory"
    else:
        return "—"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, default=str)


def _constraints(field: Any) -> str:
    try:
        schema = TypeAdapter(field.rebuild_annotation()).json_schema()
    except Exception:  # pragma: no cover - Pydantic supports every current annotation
        return ""
    labels = {
        "minimum": "minimum",
        "exclusiveMinimum": "greater than",
        "maximum": "maximum",
        "exclusiveMaximum": "less than",
        "minLength": "minimum length",
        "maxLength": "maximum length",
        "pattern": "pattern",
        "format": "format",
    }
    parts = [f"{label} {json.dumps(schema[key], sort_keys=True)}" for key, label in labels.items() if key in schema]
    return "; ".join(parts)


def model_fields(model: type[BaseModel], *, leaves_only: bool = False, prefix: str = "",
                 _constraint_cache: dict[int, str] | None = None) -> list[FieldDoc]:
    # A traversal can visit the same captured profile through many union branches.
    # Reuse schemas within this traversal or render, never across separate renders.
    return _model_fields(model, leaves_only=leaves_only, prefix=prefix,
                         constraints={} if _constraint_cache is None else _constraint_cache)


def _model_fields(model, *, leaves_only, prefix, constraints):
    result: list[FieldDoc] = []
    for name, field in model.model_fields.items():
        path = prefix + name
        nested = _base_model(field.annotation)
        base, _ = _optional(field.annotation)
        variants = base if isinstance(base, tuple) and all(
            inspect.isclass(item) and issubclass(item, BaseModel) for item in base) else ()
        if get_origin(base) in (list, tuple, set):
            member = get_args(base)[0]
            members, _ = _optional(member)
            if isinstance(members, tuple) and all(inspect.isclass(item) and issubclass(item, BaseModel) for item in members):
                variants = members
        origin = get_origin(_optional(field.annotation)[0])
        child_prefix = path + ("[]." if origin in (list, tuple, set) else ".")
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        if id(field) not in constraints:
            constraints[id(field)] = _constraints(field)
        doc = FieldDoc(
            path=path,
            type=type_name(field.annotation),
            required=field.is_required(),
            nullable=_optional(field.annotation)[1],
            default=_default(field),
            description=(field.description or "").strip(),
            constraints=constraints[id(field)],
            secret=bool(extra.get("secret")),
            leaf=nested is None and not variants,
        )
        if not leaves_only or doc.leaf:
            result.append(doc)
        if nested is not None:
            result.extend(_model_fields(nested, leaves_only=leaves_only, prefix=child_prefix, constraints=constraints))
        elif variants:
            branches = [_model_fields(variant, leaves_only=leaves_only, prefix=child_prefix, constraints=constraints) for variant in variants]
            by_path = {}
            for variant, branch in zip(variants, branches):
                for item in branch:
                    by_path.setdefault(item.path, []).append((variant, item))
            for present in by_path.values():
                item = present[0][1]
                description = item.description
                if len(present) != len(variants):
                    description = " ".join(filter(None, (description,
                        "Present in " + ", ".join(variant.__name__ for variant, _ in present) + ".")))
                result.append(replace(item,
                    required=len(present) == len(variants) and all(entry.required for _, entry in present),
                    description=description,
                    type=" | ".join(dict.fromkeys(entry.type for _, entry in present))))
    return result


def _sample_string(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith("_id") or lowered in {"id", "event", "token", "directive"}:
        return "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    if "currency" in lowered:
        return "USD"
    if lowered.endswith("_at") or lowered in {"since", "until"}:
        return "2026-01-01T00:00:00Z"
    if lowered.endswith("date"):
        return "2026-01-01"
    if lowered in {"path", "data_root", "socket", "output"}:
        return "/example"
    if lowered in {"interface", "created_via", "updated_via", "via"}:
        return "cli"
    if lowered in {"home_currency", "original_currency"}:
        return "USD"
    if lowered == "code" or lowered.endswith("_code"):
        return "SI-1"
    if lowered in {"role", "access", "required_role"}:
        return "owner"
    if lowered == "kind" or lowered.endswith("_kind"):
        return "human"
    if lowered == "schema_revision":
        return "current"
    return "value"


def sample_value(annotation: Any, name: str) -> Any:
    base, nullable = _optional(annotation)
    if nullable:
        return None
    if isinstance(base, tuple):
        return sample_value(base[0], name)
    origin = get_origin(base)
    args = get_args(base)
    if origin is Literal:
        return args[0]
    if origin in (list, tuple, set):
        # A strict wire model rejects a list supplied for a tuple field, so sample
        # the declared container rather than always a list.
        return () if origin is tuple else []
    if origin is dict:
        return {}
    if inspect.isclass(base) and issubclass(base, Enum):
        return next(iter(base)).value
    if getattr(base, "__pydantic_root_model__", False):
        return sample_value(base.model_fields["root"].annotation, name)
    if inspect.isclass(base) and issubclass(base, BaseModel):
        # Python mode keeps a declared tuple a tuple, which a strict parent
        # requires; the top-level caller serializes to JSON once, at the end.
        return sample_instance(base).model_dump()
    if base is str:
        return _sample_string(name)
    if base is int:
        return 0 if name == "count" else 1
    if base is float:
        return 1.0
    if base is bool:
        return False
    return None


def sample_instance(model: type[BaseModel]) -> BaseModel:
    """A validated sample of one model, before any JSON serialization.

    Nested samples are dumped in Python mode, so a declared tuple is still a
    tuple when its parent validates it. A strict wire model rejects the list
    a JSON dump would have left there. Serialization happens once, at the top.
    """
    values: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        if "sample" in extra:
            value = extra["sample"]
        elif not field.is_required():
            if field.default is not PydanticUndefined:
                value = field.default
            elif field.default_factory is not None:
                value = sample_value(field.annotation, name) if extra.get("generated") else field.default_factory()
            else:
                value = sample_value(field.annotation, name)
        else:
            value = sample_value(field.annotation, name)
        values[name] = value
    return model.model_validate(values)


def sample_model(model: type[BaseModel]) -> dict[str, Any]:
    return sample_instance(model).model_dump(mode="json")


@dataclass(frozen=True)
class ColumnDoc:
    name: str
    type: str
    nullable: bool
    default: str
    key: str
    indexes: str
    references: str
    description: str


def table_columns(table: Any) -> list[ColumnDoc]:
    unique_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    index_names: dict[str, list[str]] = {column.name: [] for column in table.columns}
    for index in sorted(table.indexes, key=lambda item: item.name or ""):
        for column in index.columns:
            index_names[column.name].append(index.name or "unnamed")
    result = []
    primary_positions = {column.name: position for position, column in enumerate(table.primary_key.columns, 1)}
    for column in table.columns:
        default = "—"
        if column.default is not None:
            arg = column.default.arg
            default = json.dumps(arg, sort_keys=True, default=str) if not callable(arg) else getattr(arg, "__name__", "callable")
        keys = []
        position = primary_positions.get(column.name)
        if position is not None:
            keys.append(f"primary key {position}")
        if column.unique or (column.name,) in unique_sets:
            keys.append("unique")
        composite = [" + ".join(names) for names in sorted(unique_sets) if len(names) > 1 and column.name in names]
        keys.extend(f"unique with {names}" for names in composite)
        result.append(ColumnDoc(
            name=column.name,
            type=str(column.type),
            nullable=bool(column.nullable),
            default=default,
            key=", ".join(keys) or "—",
            indexes=", ".join(index_names[column.name]) or "—",
            references=", ".join(sorted(str(foreign.column) for foreign in column.foreign_keys)) or "—",
            description=str(column.info.get("description", "")).strip(),
        ))
    return result
