"""One translation of "clear these fields" into the JSON null shape, for the CLI and the client (row 2 plan, Null)."""

from __future__ import annotations

import types
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from bookflow.core.errors import BookflowError


def _nullable(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return (origin is Union or origin is types.UnionType) and type(None) in get_args(annotation)


def _leaves(model: type[BaseModel], prefix: str = "") -> dict[str, tuple[bool, bool]]:
    """dotted path -> (nullable, is_nested_object)."""
    out: dict[str, tuple[bool, bool]] = {}
    for name, f in model.model_fields.items():
        ann = f.annotation
        base = ann
        origin = get_origin(ann)
        if origin is Union or origin is types.UnionType:
            args = [a for a in get_args(ann) if a is not type(None)]
            base = args[0] if args else str
        if isinstance(base, type) and issubclass(base, BaseModel):
            out[prefix + name] = (_nullable(ann), True)
            out.update(_leaves(base, prefix + name + "."))
        else:
            out[prefix + name] = (_nullable(ann), False)
    return out


def apply_clears(cmd, raw: dict[str, Any], names: list[str]) -> None:
    """Set each named field to null in ``raw``; reject unknown, non-nullable, or value-plus-clear names with E_VALIDATION."""
    leaves = _leaves(cmd.input_model)
    problems = []
    for name in names:
        dotted = name.replace("-", "_")
        if dotted not in leaves:
            candidates = [p for p in leaves if p.replace(".", "_") == dotted]
            dotted = candidates[0] if candidates else dotted
        if dotted not in leaves:
            problems.append({"field": name, "problem": "not a field of this command"}); continue
        nullable, nested = leaves[dotted]
        if not nullable and not nested:
            problems.append({"field": name, "problem": "cannot be cleared; it always has a value"}); continue
        parts = dotted.split(".")
        node = raw
        present = True
        for p in parts[:-1]:
            if p in node and node[p] is None:
                problems.append({"field": name, "problem": f"{p} is given as null, which already clears it"}); present = None
                break
            if not isinstance(node.get(p), dict):
                present = False
                break
            node = node[p]
        if present is None:
            continue
        if present and node.get(parts[-1]) is not None and (not nested or parts[-1] in node):
            problems.append({"field": name, "problem": "given both a value and a clear"}); continue
        node = raw
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = None
    if problems:
        raise BookflowError("E_VALIDATION", details={"fields": problems})
