"""Forms and their translation into command JSON, generated from input models (row 3 plan, The workbench)."""

from __future__ import annotations

import inspect
import json
import types
from enum import Enum
from typing import Any, Literal, Mapping, Union, get_args, get_origin

from pydantic import BaseModel

from bookflow.core import registry
from bookflow.core.errors import BookflowError


def _base(ann):
    origin = get_origin(ann)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(ann) if a is not type(None)]
        return args[0] if args else str, True
    return ann, False


_COLLECTION_ORIGINS = (list, tuple, set, frozenset)


def _collection_item(ann: Any) -> tuple[Any, bool] | None:
    """Return a collection's item annotation and nullability when it is form-shaped."""
    base, nullable = _base(ann)
    origin = get_origin(base)
    if origin not in _COLLECTION_ORIGINS:
        return None
    args = get_args(base)
    if not args:
        return Any, nullable
    item = args[0]
    # A fixed heterogeneous tuple is still JSON-shaped.  All Row 5 owned
    # collections are homogeneous (including ``tuple[T, ...]``).
    if origin is tuple and len(args) > 1 and args[1] is not Ellipsis:
        return None
    return item, nullable


def _scalar_descriptor(
    annotation: Any,
    *,
    name: str,
    description: str = "",
    required: bool = False,
) -> dict[str, Any]:
    base, nullable = _base(annotation)
    kind, choices = "text", None
    if get_origin(base) is Literal:
        kind, choices = "choice", [str(a) for a in get_args(base)]
    elif inspect.isclass(base) and issubclass(base, Enum):
        kind, choices = "choice", [str(e.value) for e in base]
    elif base is bool:
        kind = "bool"
    elif base in (int, float):
        kind = "number"
    return {
        "name": name,
        "kind": kind,
        "choices": choices,
        "description": description,
        "required": required,
        "nullable": nullable,
        "annotation": annotation,
    }


def collection_schema(annotation: Any) -> dict[str, Any] | None:
    """Describe a homogeneous collection recursively for repeated controls."""
    found = _collection_item(annotation)
    if found is None:
        return None
    item_annotation, nullable = found
    item_base, _ = _base(item_annotation)
    nested_item = collection_schema(item_annotation)
    if nested_item is not None:
        item = {"kind": "collection", "collection": nested_item}
    elif inspect.isclass(item_base) and issubclass(item_base, BaseModel):
        fields: list[dict[str, Any]] = []
        for name, field in item_base.model_fields.items():
            nested = collection_schema(field.annotation)
            if nested is not None:
                fields.append({
                    "name": name,
                    "kind": "collection",
                    "collection": nested,
                    "description": field.description or "",
                    "required": field.is_required(),
                    "nullable": _base(field.annotation)[1],
                    "annotation": field.annotation,
                })
            else:
                fields.append(_scalar_descriptor(
                    field.annotation,
                    name=name,
                    description=field.description or "",
                    required=field.is_required(),
                ))
        item = {"kind": "object", "model": item_base, "fields": fields}
    else:
        item = _scalar_descriptor(item_annotation, name="value", required=True)
    return {"kind": "collection", "nullable": nullable, "item": item}


def _project_value(annotation: Any, value: Any) -> Any:
    """Project rich show output back onto the exact command-input shape."""
    if value is None:
        return None
    found = _collection_item(annotation)
    if found is not None:
        item_annotation, _ = found
        if not isinstance(value, (list, tuple, set, frozenset)):
            return value
        return [_project_value(item_annotation, item) for item in value]
    base, _ = _base(annotation)
    if inspect.isclass(base) and issubclass(base, BaseModel) and isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for name, field in base.model_fields.items():
            if name not in value or value[name] is None:
                continue
            projected[name] = _project_value(field.annotation, value[name])
        return projected
    # Exact-money outputs are richer than the input's decimal-string leaf.
    if isinstance(value, Mapping) and "amount" in value:
        return value["amount"]
    return value


def project_input_values(model: type[BaseModel], originals: dict[str, Any]) -> None:
    """Project rich show values onto the command's exact typed input shape in place."""
    for name, field in model.model_fields.items():
        if name in originals:
            originals[name] = _project_value(field.annotation, originals[name])


def leaves(model: type[BaseModel], prefix: str = "") -> list[dict[str, Any]]:
    """Describe each form leaf from the command model alone."""
    out = []
    for name, f in model.model_fields.items():
        base, nullable = _base(f.annotation)
        if inspect.isclass(base) and issubclass(base, BaseModel):
            out += leaves(base, prefix + name + ".")
            continue
        kind, choices = "text", None
        json_shape = None
        if get_origin(base) is Literal:
            kind, choices = "choice", [str(a) for a in get_args(base)]
        elif inspect.isclass(base) and issubclass(base, Enum):
            kind, choices = "choice", [e.value for e in base]
        elif base is bool:
            kind = "bool"
        elif base in (int, float):
            kind = "number"
        elif get_origin(base) in _COLLECTION_ORIGINS:
            kind, json_shape = (
                ("collection", None)
                if collection_schema(f.annotation) is not None
                else ("json", "array")
            )
        elif get_origin(base) is dict:
            kind, json_shape = "json", "object"
        extra = f.json_schema_extra if isinstance(f.json_schema_extra, dict) else {}
        if extra.get("secret"):
            kind = "secret"
        default = None if f.is_required() or f.default_factory is not None else f.default
        path = prefix + name
        out.append({"path": path, "path_parts": tuple(path.split(".")), "kind": kind,
                    "json_shape": json_shape, "choices": choices,
                    "description": f.description or "", "default": default,
                    "required": f.is_required(), "nullable": nullable,
                    "annotation": f.annotation})
    return out


_ACCOUNT_TYPES = (
    "bank", "accounts_receivable", "other_current_asset", "fixed_asset", "other_asset",
    "accounts_payable", "credit_card", "other_current_liability", "long_term_liability",
    "equity", "income", "cost_of_goods_sold", "expense", "other_income", "other_expense",
    "non_posting",
)


def _visibility_rules(noun: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Presentation-only discriminator rules, with domain services remaining authoritative."""
    if noun == "item":
        from bookflow.company.items import ITEM_PROFILES

        fields = {
            field
            for profile in ITEM_PROFILES.values()
            for field in profile.allowed
        }
        return {
            field: (
                "type",
                tuple(kind for kind, profile in ITEM_PROFILES.items() if field in profile.allowed),
            )
            for field in fields - {"name", "type", "custom_fields"}
        }
    if noun == "term":
        return {
            "due_days": ("kind", ("standard",)),
            "discount_days": ("kind", ("standard",)),
            "due_day_of_month": ("kind", ("date_driven",)),
            "due_next_month_if_within_days": ("kind", ("date_driven",)),
            "discount_day_of_month": ("kind", ("date_driven",)),
        }
    if noun == "price-level":
        return {
            "percent": ("kind", ("fixed_percent",)),
            "items": ("kind", ("per_item",)),
        }
    if noun == "custom-field":
        return {"choices": ("kind", ("choice",))}
    if noun == "account":
        institution = _ACCOUNT_TYPES[:9]
        return {
            "institution_name": ("type", institution),
            "institution_account_last4": ("type", institution),
            "routing_number_last4": ("type", ("bank",)),
            "next_check_number": ("type", ("bank",)),
            "check_reorder_number": ("type", ("bank",)),
            "order_printable_checks": ("type", ("bank",)),
            "track_reimbursable_expenses": ("type", ("expense", "cost_of_goods_sold")),
            "reimbursable_income_account_id": ("type", ("expense", "cost_of_goods_sold")),
            "tax_line": ("type", tuple(kind for kind in _ACCOUNT_TYPES if kind != "non_posting")),
        }
    return {}


def selected_value(
    path: str,
    originals: Mapping[str, Any] | None,
    attempted: Mapping[str, str] | None,
) -> Any:
    """Return the attempted discriminator/reference before the stored original."""
    key = f"f:{path}"
    if attempted is not None and key in attempted:
        return attempted[key]
    return get_path(dict(originals) if originals is not None else None, path)


def describe_fields(
    noun: str,
    verb: str,
    model: type[BaseModel],
    originals: Mapping[str, Any] | None,
    attempted: Mapping[str, str] | None,
) -> list[dict[str, Any]]:
    """Decorate typed leaves with discriminator visibility and immutable update selectors."""
    described = leaves(model)
    rules = _visibility_rules(noun)
    pinned = {
        "term": "kind",
        "price-level": "kind",
        "sales-rep": "name_type",
    }
    for leaf in described:
        if leaf["kind"] == "collection":
            schema = collection_schema(leaf["annotation"])
            assert schema is not None
            leaf["collection"] = schema
            leaf["collection"]["values"] = collection_form_value(
                leaf["annotation"], leaf["path"], originals, attempted
            )
        rule = rules.get(leaf["path"])
        if rule is not None:
            discriminator, values = rule
            selected = selected_value(discriminator, originals, attempted)
            leaf["visible_when"] = {"field": discriminator, "values": values}
            leaf["visible"] = selected in values
        leaf["pinned"] = (
            verb == "update"
            and pinned.get(noun) == leaf["path"]
            and get_path(dict(originals) if originals is not None else None, leaf["path"]) is not None
        )
    return described


def reference_for_path(definition: Any, path: str) -> Any | None:
    """Return a direct scalar reference declared by a list definition."""
    if definition is None or "." in path:
        return None
    for reference in definition.references:
        if not reference.many and reference.field == path:
            return reference
        # The item definition deliberately groups its many account columns as
        # one logical reference family while forms still render scalar ids.
        if reference.field == "account_ids" and path.endswith("_account_id"):
            return reference
    return None


def custom_field_descriptors(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project current definition records into stable-id form controls."""
    out: list[dict[str, Any]] = []
    for definition in definitions:
        identifier = str(definition["id"])
        kind = str(definition["kind"])
        choices = [
            str(choice["value"])
            for choice in definition.get("choices", [])
            if choice.get("active", True)
        ]
        out.append({
            "path": f"custom_fields.{identifier}",
            "path_parts": ("custom_fields", identifier),
            "form_key": f"cf:{identifier}",
            "definition_id": identifier,
            "label": str(definition["name"]),
            "kind": f"custom-{kind}",
            "choices": choices if kind == "choice" else None,
            "description": "Company custom field; its stable definition id is submitted.",
            "default": definition.get("default"),
            "required": bool(definition.get("required", False)),
            "nullable": True,
            "runtime": True,
            "pinned": False,
        })
    return out


def set_path(d: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def get_path(d: dict[str, Any] | None, path: str) -> Any:
    cur: Any = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def form_value(
    leaf: dict[str, Any],
    originals: dict[str, Any] | None,
    attempted: dict[str, str] | None,
) -> Any:
    """Return attempted text separately from the authoritative original."""
    if leaf["kind"] == "secret":
        return ""
    key = leaf.get("form_key", f"f:{leaf['path']}")
    if attempted is not None and key in attempted:
        return attempted[key]
    value = get_path(originals, leaf["path"])
    if value is None and leaf.get("runtime"):
        value = leaf.get("default")
    if leaf["kind"] == "collection":
        return value
    if leaf["kind"] == "json" and value is not None:
        return json.dumps(value, indent=2, default=str)
    return value


def _collection_prefix(path: tuple[str, ...]) -> str:
    return "c:" + ":".join(path) + ":"


def _coerce_scalar(annotation: Any, value: str) -> Any:
    base, _ = _base(annotation)
    if base is bool:
        if value == "unset":
            return None
        return value == "true"
    if base is int:
        try:
            return int(value)
        except ValueError:
            return value
    if base is float:
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _read_collection(
    annotation: Any,
    path: tuple[str, ...],
    form: Mapping[str, str],
    *,
    coerce: bool,
) -> list[Any]:
    """Read rows in browser order; indexes are stable DOM identities, not ordering."""
    found = _collection_item(annotation)
    if found is None:
        return []
    item_annotation, _ = found
    prefix = _collection_prefix(path)
    marker_prefix = "collection:" + ":".join(path) + ":"
    indexes: list[str] = []
    for key in form:
        matching_prefix = prefix if key.startswith(prefix) else marker_prefix if key.startswith(marker_prefix) else None
        if matching_prefix is None:
            continue
        remainder = key[len(matching_prefix):]
        index = remainder.split(":", 1)[0]
        if index and index not in indexes:
            indexes.append(index)
    item_base, _ = _base(item_annotation)
    rows: list[Any] = []
    for index in indexes:
        item_path = (*path, index)
        if _collection_item(item_annotation) is not None:
            rows.append(_read_collection(item_annotation, item_path, form, coerce=coerce))
        elif inspect.isclass(item_base) and issubclass(item_base, BaseModel):
            row: dict[str, Any] = {}
            for name, field in item_base.model_fields.items():
                nested = _collection_item(field.annotation)
                if nested is not None:
                    marker = "collection:" + ":".join((*item_path, name))
                    nested_prefix = _collection_prefix((*item_path, name))
                    if marker in form or any(key.startswith(nested_prefix) for key in form):
                        row[name] = _read_collection(
                            field.annotation, (*item_path, name), form, coerce=coerce
                        )
                    continue
                key = "c:" + ":".join((*item_path, name))
                if key not in form:
                    continue
                value = form[key]
                if value in ("", "unset"):
                    continue
                row[name] = _coerce_scalar(field.annotation, value) if coerce else value
            rows.append(row)
        else:
            key = "c:" + ":".join((*item_path, "value"))
            if key not in form:
                rows.append("")
            else:
                value = form[key]
                rows.append(_coerce_scalar(item_annotation, value) if coerce else value)
    return rows


def collection_form_value(
    annotation: Any,
    path: str,
    originals: Mapping[str, Any] | None,
    attempted: Mapping[str, str] | None,
) -> list[Any]:
    """Return attempted repeated rows separately from the stored projection."""
    marker = f"collection:{path}"
    prefix = f"c:{path}:"
    if attempted is not None and (
        marker in attempted or any(key.startswith(prefix) for key in attempted)
    ):
        return _read_collection(annotation, (path,), attempted, coerce=False)
    value = get_path(dict(originals) if originals is not None else None, path)
    projected = _project_value(annotation, value)
    return list(projected) if isinstance(projected, list) else []


def collection_attempt(
    annotation: Any,
    path: str,
    value: Any,
    *,
    omit_stable_ids: bool = False,
) -> dict[str, str]:
    """Encode a trusted projection as attempted repeated controls."""
    projected = _project_value(annotation, value)
    encoded: dict[str, str] = {}

    def add(collection_annotation: Any, parts: tuple[str, ...], rows: Any) -> None:
        encoded["collection:" + ":".join(parts)] = "1"
        found = _collection_item(collection_annotation)
        if found is None or not isinstance(rows, list):
            return
        item_annotation, _ = found
        item_base, _ = _base(item_annotation)
        for index, item in enumerate(rows):
            item_parts = (*parts, str(index))
            if _collection_item(item_annotation) is not None:
                add(item_annotation, item_parts, item)
            elif inspect.isclass(item_base) and issubclass(item_base, BaseModel):
                if not isinstance(item, Mapping):
                    continue
                for name, field in item_base.model_fields.items():
                    if omit_stable_ids and name == "id":
                        continue
                    if name not in item or item[name] is None:
                        continue
                    if _collection_item(field.annotation) is not None:
                        add(field.annotation, (*item_parts, name), item[name])
                        continue
                    scalar = item[name]
                    encoded["c:" + ":".join((*item_parts, name))] = (
                        "true" if scalar is True else "false" if scalar is False else str(scalar)
                    )
            else:
                encoded["c:" + ":".join((*item_parts, "value"))] = str(item)

    add(annotation, (path,), projected)
    return encoded


def translate(cmd: registry.Command, form: dict[str, str], originals: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, str], bool]:
    """Form encoding -> (command JSON, context headers, dry_run).

    A leaf is sent only when it differs from its original (or has no original), or when its clear box is ticked, which sends null.
    Booleans are tri-state: unset, true, false. Context fields become headers.
    """
    raw: dict[str, Any] = {}
    headers: dict[str, str] = {}
    for leaf in leaves(cmd.input_model):
        path = leaf["path"]
        clear = form.get(f"clear:{path}") == "1"
        value = form.get(f"f:{path}")
        original = get_path(originals, path) if originals is not None else None
        if clear:
            set_path(raw, path, None)
            continue
        if leaf["kind"] == "collection":
            marker = f"collection:{path}"
            prefix = f"c:{path}:"
            if marker not in form and not any(key.startswith(prefix) for key in form):
                continue
            v = _read_collection(
                leaf["annotation"], (path,), form, coerce=True
            )
            projected_original = _project_value(leaf["annotation"], original)
            if (
                originals is not None
                and projected_original == v
                and not leaf["required"]
                and not path.startswith("expected_")
            ):
                continue
            set_path(raw, path, v)
            continue
        if value is None or value == "":
            continue
        if leaf["kind"] == "bool":
            if value == "unset":
                continue
            v: Any = value == "true"
        elif leaf["kind"] == "json":
            try:
                v = json.loads(value)
            except json.JSONDecodeError as exc:
                raise BookflowError(
                    "E_VALIDATION",
                    details={"fields": [{
                        "field": path,
                        "problem": f"must be a JSON {leaf['json_shape']} ({exc.msg})",
                    }]},
                ) from None
            expected = list if leaf["json_shape"] == "array" else dict
            if not isinstance(v, expected):
                raise BookflowError(
                    "E_VALIDATION",
                    details={"fields": [{
                        "field": path,
                        "problem": f"must be a JSON {leaf['json_shape']}",
                    }]},
                )
        elif leaf["kind"] == "number":
            try:
                v = int(value) if value.lstrip("-").isdigit() else float(value)
            except ValueError:
                v = value
        else:
            v = value
        if (
            originals is not None
            and original is not None
            and original == v
            and not leaf["required"]
            and not path.startswith("expected_")
        ):
            continue
        set_path(raw, path, v)
    custom_patch: dict[str, Any | None] = {}
    original_custom = get_path(originals, "custom_fields") if originals is not None else None
    original_custom = original_custom if isinstance(original_custom, dict) else {}
    for key, value in form.items():
        if not key.startswith("cf:"):
            continue
        definition_id = key.removeprefix("cf:")
        if form.get(f"clear:custom_fields.{definition_id}") == "1":
            custom_patch[definition_id] = None
            continue
        if value in ("", "unset"):
            continue
        kind = form.get(f"cf-kind:{definition_id}")
        parsed: Any = value == "true" if kind == "bool" else value
        if original_custom.get(definition_id) != parsed:
            custom_patch[definition_id] = parsed
    if custom_patch:
        raw["custom_fields"] = custom_patch
    for name, header in (("reason", "X-Bookflow-Reason"), ("source_ref", "X-Bookflow-Source-Ref"), ("directive", "X-Bookflow-Directive"), ("idempotency_key", "Idempotency-Key")):
        v = form.get(f"ctx:{name}")
        if v:
            headers[header] = v
    return raw, headers, form.get("action") == "preview"


def context_fields(cmd: registry.Command) -> list[str]:
    out = []
    if cmd.is_write:
        out += ["reason", "source_ref"]
        if cmd.scope == "company":
            out.append("directive")
    if cmd.accepts_idempotency_key:
        out.append("idempotency_key")
    return out
