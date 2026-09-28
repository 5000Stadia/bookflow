"""Plain `E_VALIDATION` field problems from an input model's validation failure.

Every adapter reports a rejected input through `dispatch.validate_input`, and this is the one place
that turns the validator's report into what a caller can act on: the field path the caller wrote
(never a validator's internal branch name), what shape the field accepts, and for an unknown field
the names this part of the input does accept.
"""

from __future__ import annotations

import types
import typing
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

DATE_PATTERN = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
ULID_PATTERN = "^[0-9A-HJKMNP-TV-Z]{26}$"
MONEY_SHAPE = ('a decimal string like "22.80" in the company currency (never a number); '
               'an object {"minor_units": 2280, "currency": "USD"} is also accepted')
_PRIMITIVE_TAGS = {"str", "int", "bool", "float", "none", "dict", "list", "tuple", "decimal", "date"}


def _union_args(annotation: Any) -> tuple[Any, ...] | None:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        return tuple(arg for arg in typing.get_args(annotation) if arg is not type(None))
    return None


def _unwrap(annotation: Any) -> Any:
    """Strip Annotated and a lone Optional; a real union stays a union."""
    while True:
        if typing.get_origin(annotation) is typing.Annotated:
            annotation = typing.get_args(annotation)[0]
            continue
        branches = _union_args(annotation)
        if branches is not None and len(branches) == 1:
            annotation = branches[0]
            continue
        return annotation


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _model_root(annotation: Any) -> Any:
    if _is_model(annotation) and "root" in annotation.model_fields and len(annotation.model_fields) == 1:
        return _unwrap(annotation.model_fields["root"].annotation)
    return annotation


def _branch_for(branches: tuple[Any, ...], segment: str) -> Any | None:
    for branch in branches:
        bare = _unwrap(branch)
        name = getattr(bare, "__name__", "")
        if name and (segment == name or segment.endswith(", " + name + "]") or segment.endswith("[" + name + "]")):
            return branch
        if segment == name.lower() or (segment in _PRIMITIVE_TAGS and name.lower() == segment):
            return branch
        if segment.startswith("literal[") and typing.get_origin(bare) is typing.Literal:
            return branch
    for branch in branches:
        # A discriminated union names the branch by its tag value.
        bare = _unwrap(branch)
        if _is_model(bare):
            for field in bare.model_fields.values():
                if typing.get_origin(_unwrap(field.annotation)) is typing.Literal and segment in typing.get_args(_unwrap(field.annotation)):
                    return branch
    return None


def _looks_internal(segment: Any) -> bool:
    return isinstance(segment, str) and ("[" in segment or "(" in segment or segment in _PRIMITIVE_TAGS)


class _Resolved:
    __slots__ = ("path", "annotation", "parent", "leaf", "union_path", "branch", "complete")

    def __init__(self) -> None:
        self.path: list[Any] = []
        self.annotation: Any = None
        self.parent: Any = None
        self.leaf: tuple[Any, str, list[int]] | None = None
        self.union_path: tuple[Any, ...] | None = None
        self.branch: Any = None
        self.complete = True


def resolve(model: type[BaseModel], loc: tuple[Any, ...]) -> _Resolved:
    """Walk a validator location through the model, keeping only the caller's own field names."""
    out = _Resolved()
    current: Any = model
    out.annotation = model
    for segment in loc:
        current = _model_root(_unwrap(current))
        branches = _union_args(current)
        if branches is not None:
            branch = _branch_for(branches, segment) if isinstance(segment, str) else None
            if branch is not None:
                out.union_path, out.branch = tuple(out.path), segment
                current = branch
                continue
            out.complete = False
        if isinstance(segment, int):
            origin = typing.get_origin(current)
            if origin in (list, tuple, set, frozenset) or (origin is not None and str(origin).startswith("<class 'collections.abc")):
                args = typing.get_args(current)
                current = args[0] if args else Any
            else:
                out.complete = False
            out.path.append(segment)
            if out.leaf is not None:
                out.leaf[2].append(segment)
            continue
        if _is_model(current) and segment in current.model_fields:
            out.parent = current
            out.leaf = (current, segment, [])
            out.path.append(segment)
            current = current.model_fields[segment].annotation
            out.annotation = current
            continue
        if typing.get_origin(current) in (dict, typing.Mapping) or str(typing.get_origin(current)).endswith("Mapping'>"):
            args = typing.get_args(current)
            out.path.append(segment)
            out.leaf = None
            current = args[1] if len(args) == 2 else Any
            continue
        if _looks_internal(segment):
            out.complete = False
            continue
        # An unknown key: its parent is the model it was sent to.
        if _is_model(current):
            out.parent = current
        out.path.append(segment)
        out.complete = False
        current = Any
    if out.complete:
        out.annotation = current
    return out


def _leaf_schema(where: _Resolved) -> tuple[dict[str, Any], dict[str, Any]]:
    """The field's own JSON schema, constraints included (a bare annotation has lost them)."""
    if where.leaf is None:
        return {}, {}
    model, name, indices = where.leaf
    try:
        schema = model.model_json_schema()
    except Exception:  # noqa: BLE001 - a description is a courtesy, never a failure
        return {}, {}
    defs = schema.get("$defs", {})
    field = model.model_fields[name]
    prop = schema.get("properties", {}).get(field.alias or name, {})
    for _ in indices:
        prop = _deref(prop, defs)
        options = prop.get("anyOf") or [prop]
        prop = next((option.get("items") for option in options if option.get("type") == "array"), {}) or {}
    return prop, defs


def _schema(annotation: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        schema = TypeAdapter(annotation).json_schema()
    except Exception:  # noqa: BLE001 - a description is a courtesy, never a failure
        return {}, {}
    return schema, schema.get("$defs", {})


def _deref(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return defs.get(ref.rsplit("/", 1)[1], {})
    return schema


def _is_money(options: list[dict[str, Any]], defs: dict[str, Any]) -> bool:
    has_string = any(option.get("type") == "string" for option in options)
    has_money = any({"minor_units", "currency"} <= set(_deref(option, defs).get("properties", {})) for option in options)
    return has_string and has_money


def describe(schema: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> str:
    """One plain phrase naming the shape a JSON schema accepts."""
    schema = _deref(schema, defs)
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        options = [_deref(option, defs) for option in options if option.get("type") != "null"]
        if _is_money(options, defs):
            return MONEY_SHAPE
        phrases = []
        for option in options:
            phrase = describe(option, defs, depth + 1)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
        return " or ".join(phrases)
    if "enum" in schema:
        return "one of " + ", ".join(repr(value) for value in schema["enum"])
    if "const" in schema:
        return repr(schema["const"])
    kind = schema.get("type")
    if kind == "string":
        pattern = schema.get("pattern")
        text = (schema.get("description") or "") + " " + (schema.get("title") or "")
        if pattern == DATE_PATTERN or (schema.get("maxLength") == 10 and ("YYYY-MM-DD" in text or "date" in text.lower())):
            return 'a date like "2026-09-27" (YYYY-MM-DD)'
        if pattern == ULID_PATTERN:
            return "a record ID (26 characters, as a query or show command returns it)"
        description = (schema.get("description") or "").strip().rstrip(".")
        if description and len(description) <= 160:
            return "text: " + description
        return "text"
    if kind == "integer":
        return "a whole number"
    if kind == "number":
        return "a number"
    if kind == "boolean":
        return "true or false"
    if kind == "array":
        inner = describe(schema.get("items", {}), defs, depth + 1) if depth < 2 else ""
        return "a list" + (" of " + inner if inner else "")
    if kind == "object":
        names = list(schema.get("properties", {}))
        return "an object" + (" with fields " + ", ".join(names) if names and depth < 2 else "")
    return ""


def _accepted_fields(model: Any) -> list[str]:
    model = _model_root(_unwrap(model))
    if not _is_model(model):
        return []
    return [field.alias or name for name, field in model.model_fields.items()]


def _plain(message: str) -> str:
    for prefix in ("Value error, ", "Assertion failed, "):
        if message.startswith(prefix):
            return message[len(prefix):]
    return message


def _dotted(path: list[Any] | tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in path) or "input"


def fields(error: ValidationError, model: type[BaseModel] | None) -> list[dict[str, Any]]:
    """The `details.fields` list for a model validation failure."""
    raw = error.errors(include_url=False)
    if model is None:
        return [{"field": _dotted(item["loc"]), "problem": _plain(item["msg"])} for item in raw]
    resolved = [(item, resolve(model, tuple(item["loc"]))) for item in raw]
    # A plain value that matched no branch of a union fails once per branch; the caller wrote one
    # field, so it gets one problem saying what that field accepts.
    branch_sets: dict[tuple[Any, ...], set[str]] = {}
    for item, where in resolved:
        if where.union_path is not None:
            branch_sets.setdefault(where.union_path, set()).add(str(where.branch))
    collapsed = {path for path, names in branch_sets.items() if len(names) > 1}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item, where in resolved:
        if where.union_path is not None and where.union_path in collapsed:
            path = list(where.union_path)
            union_where = resolve(model, tuple(path))
            schema, defs = _leaf_schema(union_where)
            if not schema:
                schema, defs = _schema(_field_annotation(model, path))
            name = _dotted(path)
            if name in seen:
                continue
            seen.add(name)
            out.append({"field": name, "problem": "must be " + (describe(schema, defs) or "a valid value")})
            continue
        name = _dotted(where.path)
        entry: dict[str, Any] = {"field": name, "problem": _plain(item["msg"])}
        kind = item.get("type")
        if kind == "extra_forbidden":
            accepted = _accepted_fields(where.parent) if where.parent is not None else []
            entry["problem"] = "not a field here" + ("; accepted fields: " + ", ".join(accepted) if accepted else "")
            if accepted:
                entry["accepted_fields"] = accepted
        elif kind in ("missing", "string_pattern_mismatch", "string_type", "int_type", "bool_type",
                      "model_type", "dict_type", "list_type") and where.complete:
            schema, defs = _leaf_schema(where)
            shape = describe(schema, defs)
            if shape:
                entry["problem"] = ("required: " if kind == "missing" else "must be ") + shape
        key = name + "\0" + entry["problem"]
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def _field_annotation(model: type[BaseModel], path: list[Any]) -> Any:
    current: Any = model
    for segment in path:
        current = _model_root(_unwrap(current))
        if isinstance(segment, int):
            args = typing.get_args(current)
            current = args[0] if args else Any
        elif _is_model(current) and segment in current.model_fields:
            current = current.model_fields[segment].annotation
        else:
            args = typing.get_args(current)
            current = args[1] if len(args) == 2 else Any
    return current
