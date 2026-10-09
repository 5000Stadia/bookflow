"""The MCP result size budget: a result too large for the agent's client arrives compact, never cut silently.

Claude's MCP client refuses a tool result above about 25k tokens, and a result dense with ULIDs,
amounts and minor units runs near two characters a token: the R70 payment receipt (54,495
characters) and the R80 register page (53,833) were both refused, so the agent never saw its own
write or its warnings. A result whose rendered JSON is within BUDGET passes unchanged. A larger one
keeps its warnings and errors first and every scalar (identity and headline figures: id, number,
status, totals, counts); it leaves out its largest nested lists, largest first, until it fits, and a
query page keeps the leading rows that fit. A `blocking` list (one compact line per problem that stops
the command, as `cutover plan` gives it) leads the result and is never left out, and an `exceptions`
list is left out only after every other list: the cutover trial's full plan said "14 blocking
exceptions" and its list was the first thing cut. `result_compacted` names each list left out with its
item count and says how to read the full record. Python, CLI and HTTP results are unchanged: only
the MCP adapter carries this budget, because only its client refuses large results.
"""

import copy
import json

BUDGET = 20_000       # characters of rendered result JSON the agent receives
NOTE_ROOM = 1_000     # reserved for result_compacted itself
FIRST = ("blocking", "code", "message", "warnings", "errors", "error", "details")
LAST = ("exceptions",)  # left out only once every other list is gone


def size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _lists(value, path=()):
    """Every list held by an object member, with its path, outermost first."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, list):
                yield path + (key,), item
            yield from _lists(item, path + (key,))


def _page_field(document):
    """The top-level list of a query page: the largest top-level list of objects."""
    lists = [(size(value), key) for key, value in document.items()
             if key not in FIRST and isinstance(value, list) and value and all(isinstance(row, dict) for row in value)]
    return max(lists)[1] if lists else None


_AUDIT = ("created_at", "created_by", "created_via", "updated_at", "updated_by", "updated_via")


def _slim(row):
    return {key: value for key, value in row.items()
            if key not in _AUDIT and value is not None and value != [] and value != {}}


def fit(document, *, show=None, paging=None, budget=BUDGET, result_files=True):
    """Return the document unchanged when it fits; otherwise its compact form, which says so."""
    if not isinstance(document, dict):
        return document
    full = size(document)
    if full <= budget:
        return document
    result = {key: copy.deepcopy(document[key]) for key in FIRST if key in document}
    result.update((key, copy.deepcopy(value)) for key, value in document.items() if key not in result)
    page = _page_field(result)
    rows = result.pop(page) if page is not None else None
    omitted = []
    showing = None
    target = budget - NOTE_ROOM
    # Leave out the largest nested lists first; warnings and errors are never left out.
    candidates = sorted(((size(items), path, len(items)) for path, items in _lists(result)
                         if path[0] not in FIRST), key=lambda entry: (entry[1][0] in LAST, -entry[0]))
    current = size(result)
    for cost, path, count in candidates:
        if current <= target:
            break
        holder = result
        for key in path[:-1]:
            holder = holder.get(key) if isinstance(holder, dict) else None
        if not isinstance(holder, dict) or path[-1] not in holder:
            continue  # already gone with an enclosing list
        del holder[path[-1]]
        omitted.append({"field": ".".join(path), "items": count})
        current = size(result)
    how = []
    slimmed = False
    if page is not None and sum(size(row) + 2 for row in rows) > target - current:
        # Before any row is left out, each row sheds what carries nothing: null or empty fields
        # and its audit stamps. A chart of accounts then arrives whole far more often (R166).
        rows = [_slim(row) for row in rows]
        slimmed = True
    if page is not None:
        kept, room = [], target - current
        for row in rows:
            cost = size(row) + 2
            if cost > room:
                break
            kept.append(row)
            room -= cost
        result[page] = kept
        if slimmed:
            how.append(f"Each of the {page} leaves out its null or empty fields and its created_/updated_ audit "
                       "stamps; a show command reads one whole.")
        if len(kept) < len(rows):
            omitted.insert(0, {"field": page, "items": len(rows), "kept": len(kept)})
            showing = f"showing {len(kept)} of {len(rows)} {page}"
            if result.get("next_cursor") is not None:
                # It continues after the last row the full page held, so it would skip the rows
                # left out here; a rerun with the smaller limit gets a cursor that does not.
                result["next_cursor"] = None
                how.append("next_cursor is left out because it would skip the rows not shown.")
            limit = max(len(kept), 1)
            if paging is None or paging.get("paged"):
                how.append(f"This page kept its first {len(kept)} of {len(rows)} {page}. Rerun with input.limit "
                           f"{limit} and follow next_cursor to page through them all.")
            else:
                ways = []
                if paging.get("alternative"):
                    ways.append(f"run {paging['alternative']} with input.limit {limit} and follow next_cursor")
                if paging.get("narrow"):
                    ways.append("narrow this command with input." + " or input.".join(paging["narrow"]))
                how.append(f"This list kept its first {len(kept)} of {len(rows)} {page}; {paging['command']} returns "
                           f"them all at once and takes no limit." + (f" For the rest, {' or '.join(ways)}." if ways else ""))
    if show:
        how.append(f"Read the full record with bookflow_run {show}.")
    if result_files:
        how.append("Or add transport.result_file (an absolute path) to receive the complete JSON as a file, "
                   "and page it with bookflow_run action inspect.")
    else:
        how.append("transport.result_file cannot save the complete JSON as a file: no output directory is configured. "
                   "The MCP server saves files only when started with --output-dir DIR.")
    note = {"reason": "size_budget", "full_characters": full, "budget_characters": budget,
            "omitted": omitted, "full_result": " ".join(how)}
    if showing:
        note = {"showing": showing, **note}
        note["full_result"] = f"Not every row is here: {showing}. " + note["full_result"]
    # The note leads, right after warnings and errors, so a cut list is never read as complete.
    lead = {key: result.pop(key) for key in FIRST if key in result}
    result = {**lead, "result_compacted": note, **result}
    if size(result) > budget:
        # Still too large (large scalars or strings): keep warnings, errors and identity only.
        keep = {key: result[key] for key in FIRST if key in result}
        keep.update((key, result[key]) for key in ("id", "number", "status", "version", "count", "next_cursor",
                                                   "dry_run", "changed", "ready", "summary") if key in result and size(result[key]) <= 200)
        omitted.append({"field": "*", "reason": "every other field left out to fit"})
        result = {**keep, "result_compacted": note}
    return result


# Help leads with what a caller acts on; schemas and the reference follow and compact first.
HELP_FIRST = ("name", "description", "view", "example", "cli_example", "context_usage", "documentation")
HELP_KEEP = frozenset(FIRST) | {"name", "description", "view", "example", "cli_example", "bridge_version"}


def _schema_outline(schema):
    """A schema's shape without its definitions: each property's type or definition name."""
    def kind(value):
        if "$ref" in value:
            return value["$ref"].rsplit("/", 1)[-1]
        if "anyOf" in value:
            return " | ".join(kind(v) for v in value["anyOf"])
        if value.get("type") == "array" and isinstance(value.get("items"), dict):
            return "array of " + kind(value["items"])
        return str(value.get("type", "any"))
    return {"required": schema.get("required", []),
            "properties": {k: kind(v) for k, v in schema.get("properties", {}).items() if isinstance(v, dict)},
            "sections": sorted(schema.get("$defs", {}))}


def _outline(key, value):
    if isinstance(value, dict) and ("properties" in value or "$defs" in value):
        return _schema_outline(value)
    if key == "documentation" and isinstance(value, str):
        from .catalog import doc_sections
        return {"sections": list(doc_sections(value))}
    return None


def fit_help(document, *, budget=BUDGET):
    """`bookflow_help` within the budget: usage and example first, the rest outlined by section.

    A full view reached 202,778 characters for `invoice post` (R72), past what the client takes.
    Each large part in turn, largest first, becomes an outline naming its sections, and every
    section can be read alone with bookflow_help section=<name>.
    """
    if not isinstance(document, dict) or size(document) <= budget:
        return document
    full = size(document)
    if isinstance(document.get("content"), str):
        # One reference section: its text is kept up to the budget, cut at a line.
        room = budget - NOTE_ROOM - size({k: v for k, v in document.items() if k != "content"})
        text = document["content"]
        while size(text) > room:
            text = text[:max(0, min(len(text) - 1, text.rfind("\n", 0, int(len(text) * 0.9))))]
        return {**document, "content": text, "result_compacted": {
            "reason": "size_budget", "full_characters": full, "budget_characters": budget,
            "kept_characters": len(text), "full_result": "The Input and Output headings describe input_schema "
            "and output_schema; read those schemas and their definitions as sections, one at a time."}}
    result = {key: document[key] for key in (*FIRST, *HELP_FIRST) if key in document}
    result.update((key, value) for key, value in document.items() if key not in result)
    target = budget - NOTE_ROOM
    outlined, dropped = [], []
    for cost, key in sorted(((size(v), k) for k, v in result.items() if k not in HELP_KEEP), reverse=True):
        if size(result) <= target:
            break
        outline = _outline(key, result[key])
        if outline is not None and size(outline) < cost:
            result[key] = outline
            outlined.append(key)
    for cost, key in sorted(((size(v), k) for k, v in result.items() if k not in HELP_KEEP), reverse=True):
        if size(result) <= target:
            break
        del result[key]
        dropped.append(key)
    name = document.get("name", "<command>")
    result["result_compacted"] = {
        "reason": "size_budget", "full_characters": full, "budget_characters": budget,
        "outlined": outlined, "omitted": dropped,
        "full_result": f"Each outlined part lists its sections. Read one with bookflow_help command={name!r} "
                       f"section=<name> (a schema definition such as a name under sections, input_schema or "
                       f"output_schema for the top level, or a reference heading such as 'Output'). "
                       f"view=usage is the concise guide."}
    return result
