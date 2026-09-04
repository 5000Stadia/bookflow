"""Forms and their translation into command JSON, generated from input models (row 3 plan, The workbench)."""

from __future__ import annotations

import inspect
import json
import types
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from bookflow.core import registry
from bookflow.core.errors import BookflowError


def _base(ann):
    origin = get_origin(ann)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(ann) if a is not type(None)]
        return args[0] if args else str, True
    return ann, False


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
        elif get_origin(base) in (list, tuple, set, frozenset):
            kind, json_shape = "json", "array"
        elif get_origin(base) is dict:
            kind, json_shape = "json", "object"
        extra = f.json_schema_extra if isinstance(f.json_schema_extra, dict) else {}
        if extra.get("secret"):
            kind = "secret"
        default = None if f.is_required() else f.default
        path = prefix + name
        out.append({"path": path, "path_parts": tuple(path.split(".")), "kind": kind,
                    "json_shape": json_shape, "choices": choices,
                    "description": f.description or "", "default": default,
                    "required": f.is_required(), "nullable": nullable})
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
    key = f"f:{leaf['path']}"
    if attempted is not None and key in attempted:
        return attempted[key]
    value = get_path(originals, leaf["path"])
    if leaf["kind"] == "json" and value is not None:
        return json.dumps(value, indent=2, default=str)
    return value


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
        if originals is not None and original is not None and original == v:
            continue
        set_path(raw, path, v)
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
