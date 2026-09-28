"""The MCP result size budget: a result too large for the agent's client arrives compact, never cut silently.

Claude's MCP client refuses a tool result above about 25k tokens, and a result dense with ULIDs,
amounts and minor units runs near two characters a token: the R70 payment receipt (54,495
characters) and the R80 register page (53,833) were both refused, so the agent never saw its own
write or its warnings. A result whose rendered JSON is within BUDGET passes unchanged. A larger one
keeps its warnings and errors first and every scalar (identity and headline figures: id, number,
status, totals, counts); it leaves out its largest nested lists, largest first, until it fits, and a
query page keeps the leading rows that fit. `result_compacted` names each list left out with its
item count and says how to read the full record. Python, CLI and HTTP results are unchanged: only
the MCP adapter carries this budget, because only its client refuses large results.
"""

import copy
import json

BUDGET = 20_000       # characters of rendered result JSON the agent receives
NOTE_ROOM = 1_000     # reserved for result_compacted itself
FIRST = ("code", "message", "warnings", "errors", "error", "details")


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


def fit(document, *, show=None, budget=BUDGET):
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
    target = budget - NOTE_ROOM
    # Leave out the largest nested lists first; warnings and errors are never left out.
    candidates = sorted(((size(items), path, len(items)) for path, items in _lists(result)
                         if path[0] not in FIRST), key=lambda entry: -entry[0])
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
    if page is not None:
        kept, room = [], target - current
        for row in rows:
            cost = size(row) + 2
            if cost > room:
                break
            kept.append(row)
            room -= cost
        result[page] = kept
        if len(kept) < len(rows):
            omitted.insert(0, {"field": page, "items": len(rows), "kept": len(kept)})
            how.append(f"This page kept its first {len(kept)} of {len(rows)} {page}. Rerun with input.limit "
                       f"{max(len(kept), 1)} and follow next_cursor to page through them all.")
    if show:
        how.append(f"Read the full record with bookflow_run {show}.")
    how.append("Or add transport.result_file (an absolute path) to receive the complete JSON as a file, "
               "and page it with bookflow_run action inspect.")
    note = {"reason": "size_budget", "full_characters": full, "budget_characters": budget,
            "omitted": omitted, "full_result": " ".join(how)}
    result["result_compacted"] = note
    if size(result) > budget:
        # Still too large (large scalars or strings): keep warnings, errors and identity only.
        keep = {key: result[key] for key in FIRST if key in result}
        keep.update((key, result[key]) for key in ("id", "number", "status", "version", "count", "next_cursor",
                                                   "dry_run", "changed") if key in result and size(result[key]) <= 200)
        omitted.append({"field": "*", "reason": "every other field left out to fit"})
        result = {**keep, "result_compacted": note}
    return result
