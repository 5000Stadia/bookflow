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


@lru_cache(maxsize=None)
def _model_schema_text(model):
    # A command's models are fixed for the life of the process, and their JSON schema is the
    # expensive part of every help answer; each call still gets its own fresh copy.
    return json.dumps(model.model_json_schema())


def model_schema(model):
    return json.loads(_model_schema_text(model))


def _commands():
    registry.load_all()
    return registry.all_commands(include_standalone=True)


def doc_sections(text):
    """A command reference's `###` sections by heading, in order."""
    found, heading, lines = {}, None, []
    for line in text.splitlines():
        if line.startswith("### "):
            if heading is not None:
                found[heading] = "\n".join(lines).strip()
            heading, lines = line[4:].strip(), []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        found[heading] = "\n".join(lines).strip()
    return found


def help_section(name, section):
    """One part of a command's help: a schema definition by name, or a reference section by heading.

    A help view too large for the agent's client arrives compacted (budget.fit_help) and lists
    these names, so every part of it stays reachable one piece at a time.
    """
    whole = command_help(name, "full")
    parts = {}
    for key in ("input_schema", "output_schema"):
        schema = whole[key]
        parts[key] = {k: v for k, v in schema.items() if k != "$defs"}
        for ref, definition in schema.get("$defs", {}).items():
            parts.setdefault(ref, definition)
    for heading, text in doc_sections(whole["documentation"]).items():
        parts.setdefault(heading, text)
    if section not in parts:
        raise BookflowError("E_VALIDATION", message="No help section has that name.",
                            details={"field": "section", "allowed": sorted(parts)})
    return {"name": whole["name"], "section": section, "content": parts[section],
            "bridge_version": BRIDGE_VERSION}


def command_help(name, view="usage", section=None):
    if section is not None:
        return help_section(name, section)
    _commands()
    cmd = registry.get(name)
    if cmd is None:
        raise registry.unknown_command(name)
    if view not in HELP_VIEWS:
        raise BookflowError("E_VALIDATION", details={"field": "view", "allowed": HELP_VIEWS})
    from bookflow.documentation.examples import EXAMPLES
    from bookflow.documentation.generate import command_document, command_usage, run_example
    from .envelopes import RunArguments
    row = descriptor(cmd)
    context_fields = model_schema(RunArguments)["properties"]
    context_properties = {key: context_fields[key] if key in row["context"] else
                          {"const": False if key == "dry_run" else None,
                           "description": "Only this inactive value or omission applies to this command."}
                          for key in ("company", "dry_run", "reason", "source_ref", "directive", "idempotency_key")}
    result = {
        **row,
        "context_schema": {"type": "object", "description": "Schema fragment for top-level bookflow_run execution fields, not a nested context argument.", "additionalProperties": False,
            "properties": context_properties},
        "context_usage": "Omit optional context or use null; dry_run omitted/false is inactive and null is invalid. Active context applies only where listed. Use a short audit reason naming the trigger (at most 140 characters), not a narrative. Agent writes require reason or an active directive, and so do their dry_run previews: a preview runs the same checks as the save. Company selection: explicit non-null company, then calling-machine environment, then calling-machine configuration; null behaves as omitted.",
        "error_codes": sorted(set(cmd.error_codes) | set(INFRASTRUCTURE_CODES)),
        "bridge_version": BRIDGE_VERSION, "view": view, "available_views": list(HELP_VIEWS),
    }
    if view in {"usage", "input_schema", "full"}:
        # One worked example in every view that shows input, so a caller reading only the
        # schema still sees a complete call it can copy and edit.
        result["example"] = run_example(cmd)
        result["cli_example"] = EXAMPLES[cmd.name].invocation
        result["input_schema"] = model_schema(cmd.input_model)
    if view in {"output_schema", "full"}:
        result["output_schema"] = model_schema(cmd.output_model)
    if view in {"usage", "full"}:
        result["documentation"] = command_usage(cmd) if view == "usage" else command_document(cmd)
    return result


@lru_cache(maxsize=8)
def _registry_digest(contract):
    schemas = [(row, inp.model_json_schema(), out.model_json_schema()) for row, inp, out in contract]
    return hashlib.sha256(json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Everyday words for command nouns whose registered names do not contain them. Each phrase lists
# the commands it means, most useful first; they lead a search page ahead of plain word matches.
_MOVE_IN = ("cutover plan", "cutover apply", "cutover tie-out")
KEYWORDS = {
    "credit card": ("card-charge", "card-credit"), "cc": ("card-charge", "card-credit"),
    "tax": ("sales-tax", "sales-tax-code", "report income-tax-summary"),
    "vat": ("sales-tax", "sales-tax-code"), "gst": ("sales-tax", "sales-tax-code"),
    **dict.fromkeys(("import", "migrate", "migration", "move in", "move-in", "moving in", "switch from",
                     "convert", "conversion", "iif", "quickbooks", "quickbooks desktop", "qb", "qbd",
                     "opening balance", "opening balances", "old books", "set up from", "setup from",
                     "bring over"), _MOVE_IN),
}


def _words(text):
    return " ".join(text.replace("-", " ").replace("_", " ").lower().split())


def _keyword_rank(query, name):
    """The position of name among the commands a keyword in the query means, or None.

    A keyword counts when it is the whole query, appears in it as whole words ("import from
    quickbooks"), or the query is the start of a keyword of four or more letters ("migrat").
    """
    wanted = _words(query)
    if not wanted:
        return None
    padded = f" {wanted} "
    best = None
    for phrase, nouns in KEYWORDS.items():
        words = _words(phrase)
        if not (wanted == words or f" {words} " in padded or (len(wanted) >= 4 and words.startswith(wanted))):
            continue
        for rank, noun in enumerate(nouns):
            if name == noun or name.startswith(noun + " "):
                best = rank if best is None else min(best, rank)
    return best


def keyword_match(query, name):
    """True when every word of the query starts a word of the name, or a keyword names its noun."""
    wanted = _words(query)
    if not wanted:
        return False
    if _keyword_rank(query, name) is not None:
        return True
    parts = name.replace("-", " ").split(" ")
    return all(any(part.startswith(word) for part in parts) for word in wanted.split(" "))


# A listed description is its opening, cut at a sentence; bookflow_help has the whole text.
LISTED_DESCRIPTION = 600
_DEFAULTS = {"local_only": False, "standalone": False, "protocol_stdout": False, "follow": False,
             "clearable": False, "transfer": None}


def listed_row(row):
    """A catalog row as a list page carries it: the opening of its description and only the flags that
    differ from the usual; `context` and the rest are in bookflow_help."""
    text = row["description"]
    if len(text) > LISTED_DESCRIPTION:
        cut = max(text.rfind(". ", 0, LISTED_DESCRIPTION), text.rfind("; ", 0, LISTED_DESCRIPTION))
        if cut < LISTED_DESCRIPTION // 3:
            cut = text.rfind(" ", 0, LISTED_DESCRIPTION)
        text = text[:cut + 1].rstrip() + " …"
    out = {"name": row["name"], "description": text, "scope": row["scope"], "kind": row["kind"],
           "authorization": row["authorization"]}
    out.update((key, row[key]) for key, usual in _DEFAULTS.items() if row[key] != usual)
    return out


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
    matched = [row for row in rows if prefix is None or row["name"].startswith(prefix)]
    searched = False
    if prefix and not matched:
        # A prefix that names no command is read as words: "tax" finds the sales-tax nouns,
        # "credit card" or "cc" finds card charges, "import" or "quickbooks" the move-in. Commands a
        # keyword means come first, in its order. Deterministic, so cursors still hold.
        found = [row for row in rows if keyword_match(prefix, row["name"])]
        ranks = {row["name"]: _keyword_rank(prefix, row["name"]) for row in found}
        matched = sorted(found, key=lambda row: (ranks[row["name"]] is None, ranks[row["name"]] or 0))
        searched = bool(matched)
    rows = matched
    if offset > len(rows):
        raise BookflowError("E_VALIDATION", details={"reason": "malformed_cursor"})

    def cursor_at(position):
        if position >= len(rows):
            return None
        return base64.urlsafe_b64encode(json.dumps({"digest": digest, "prefix": prefix, "offset": position}).encode()).decode()

    from .budget import BUDGET, NOTE_ROOM, size
    listed = [listed_row(row) for row in rows[offset:offset + limit]]
    page = {"commands": listed, "total": len(rows), "next_cursor": cursor_at(offset + limit),
            "registry_digest": digest, "bridge_version": BRIDGE_VERSION}
    if searched:
        page["match"] = "keywords"
    # A page always fits the MCP result budget: it ends at the last command that fits, and its
    # cursor continues from there, so paging reaches every command.
    room = BUDGET - NOTE_ROOM - size({**page, "commands": [], "next_cursor": "x" * 200})
    kept = 0
    for row in listed:
        room -= size(row) + 2
        if room < 0:
            break
        kept += 1
    if kept < len(listed):
        kept = max(kept, 1)
        page["commands"] = listed[:kept]
        page["next_cursor"] = cursor_at(offset + kept)
        page["page_note"] = (f"This page shows {kept} of the {len(listed)} commands asked for (commands "
                             f"{offset + 1}-{offset + kept} of {len(rows)}) to stay within the result size budget. "
                             "Follow next_cursor with the same prefix for the rest, or narrow with a prefix.")
    if prefix and not rows:
        # A prefix that names nothing (a synonym, a plural, a typo) is answered with the
        # nearest real command names rather than a bare empty page.
        page["suggestions"] = registry.similar_commands(prefix)
    return page
