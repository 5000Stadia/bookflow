"""Authenticated hosts call these pure registry projections, never permission verdicts."""

import base64
import hashlib
import json
from functools import lru_cache

from bookflow.core import registry
from bookflow.core.errors import BookflowError, INFRASTRUCTURE_CODES

BRIDGE_VERSION = 2
HELP_VIEWS = ["usage", "input_schema", "output_schema", "full"]


def descriptor(cmd):
    context = []
    if cmd.scope == "company":
        context.append("company")
    if cmd.is_write:
        context.extend(["dry_run", "reason", "source_ref"])
        if cmd.scope == "company":
            context.append("directive")
    if cmd.accepts_idempotency_key:
        context.append("idempotency_key")
    return {
        "name": cmd.name, "description": cmd.description, "scope": cmd.scope,
        "kind": cmd.kind, "context": context, "local_only": cmd.local_only,
        "standalone": cmd.standalone, "protocol_stdout": cmd.protocol_stdout,
        "follow": cmd.streams, "clearable": cmd.clearable,
        "transfer": cmd.transfer.direction if cmd.transfer else None,
        "authorization": cmd.authorization_requirement,
    }


def _commands():
    registry.load_all()
    return registry.all_commands(include_standalone=True)


def command_help(name, view="usage"):
    _commands()
    cmd = registry.get(name)
    if cmd is None:
        raise BookflowError("E_USAGE", details={"command": name})
    if view not in HELP_VIEWS:
        raise BookflowError("E_VALIDATION", details={"field": "view", "allowed": HELP_VIEWS})
    from bookflow.documentation.generate import command_document, command_usage
    from .envelopes import RunArguments
    row = descriptor(cmd)
    context_fields = RunArguments.model_json_schema()["properties"]
    result = {
        **row,
        "context_schema": {"type": "object", "additionalProperties": False,
            "properties": {key: context_fields[key] for key in row["context"]}},
        "context_usage": "Omit optional context or use null; dry_run omitted/false is inactive and null is invalid. Active context applies only where listed. Use a short audit reason naming the trigger (at most 140 characters), not a narrative. Agent writes require reason or an active directive. Company selection: explicit non-null company, then calling-machine environment, then calling-machine configuration; null behaves as omitted.",
        "error_codes": sorted(set(cmd.error_codes) | set(INFRASTRUCTURE_CODES)),
        "bridge_version": BRIDGE_VERSION, "view": view, "available_views": list(HELP_VIEWS),
    }
    if view in {"usage", "input_schema", "full"}:
        result["input_schema"] = cmd.input_model.model_json_schema()
    if view in {"output_schema", "full"}:
        result["output_schema"] = cmd.output_model.model_json_schema()
    if view in {"usage", "full"}:
        result["documentation"] = command_usage(cmd) if view == "usage" else command_document(cmd)
    return result


@lru_cache(maxsize=8)
def _registry_digest(contract):
    schemas = [(row, inp.model_json_schema(), out.model_json_schema()) for row, inp, out in contract]
    return hashlib.sha256(json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def list_commands(*, prefix=None, limit=20, cursor=None):
    commands = _commands()
    rows = [descriptor(cmd) for cmd in commands]
    # Schemas are part of cursor identity: a field change invalidates old pages.
    contract = tuple((json.dumps(row, sort_keys=True), cmd.input_model, cmd.output_model)
                     for row, cmd in zip(rows, commands))
    digest = _registry_digest(contract)
    offset = 0
    if cursor is not None:
        try:
            decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if not isinstance(decoded, dict) or set(decoded) != {"digest", "prefix", "offset"}:
                raise ValueError
            if type(decoded["offset"]) is not int or decoded["offset"] < 0 or decoded["prefix"] != prefix:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise BookflowError("E_VALIDATION", details={"reason": "malformed_cursor"}) from None
        if decoded["digest"] != digest:
            raise BookflowError("E_QUERY_STALE", details={"scope": "command_catalog", "reason": "registry_changed", "restart": True, "current_registry_digest": digest})
        offset = decoded["offset"]
    rows = [row for row in rows if prefix is None or row["name"].startswith(prefix)]
    if offset > len(rows):
        raise BookflowError("E_VALIDATION", details={"reason": "malformed_cursor"})
    end = offset + limit
    next_cursor = None
    if end < len(rows):
        next_cursor = base64.urlsafe_b64encode(json.dumps({"digest": digest, "prefix": prefix, "offset": end}).encode()).decode()
    return {"commands": rows[offset:end], "next_cursor": next_cursor,
            "registry_digest": digest, "bridge_version": BRIDGE_VERSION}
