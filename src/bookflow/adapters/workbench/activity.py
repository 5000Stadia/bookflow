"""What an audit event says happened, in the words a person uses, and who did it.

Presentation only. The stored summary is a fact of the audit trail and every interface
returns it unchanged; this module decides how the browser reads it aloud: "Posted bill 2"
rather than "post bill 2", "Voided invoice 7" rather than "invoice void: 7", and "Office
assistant for k" rather than two separate columns of names.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from bookflow.adapters.workbench import naming as Naming
from bookflow.core import registry

# The verbs a summary opens with, as a person says them once they are done.
PAST = {
    "post": "Posted", "pay": "Paid", "update": "Updated", "void": "Voided", "create": "Created",
    "copy": "Copied", "receive": "Received", "apply": "Applied", "unapply": "Unapplied",
    "set": "Set", "complete": "Completed", "delete": "Deleted", "add": "Added", "link": "Linked",
    "unlink": "Unlinked", "remove": "Removed", "rename": "Renamed", "activate": "Made active",
    "deactivate": "Made inactive", "reactivate": "Made active", "close": "Closed",
    "reopen": "Reopened", "begin": "Began", "seal": "Sealed", "upload": "Uploaded",
    "enter": "Entered", "skip": "Skipped", "retry": "Retried", "convert": "Converted",
    "import": "Imported", "export": "Exported", "grant": "Granted", "revoke": "Revoked",
    "issue": "Issued", "adjust": "Adjusted", "transfer": "Transferred", "deposit": "Deposited",
    "reconcile": "Reconciled", "finish": "Finished", "start": "Started", "run": "Ran",
    "record": "Recorded", "restore": "Restored", "reset": "Reset", "undo": "Undid",
    "authorize": "Authorized", "reauthorize": "Reauthorized", "assign": "Assigned",
    "invoice": "Invoiced", "bill": "Billed", "generate": "Generated", "process": "Processed",
}
# Summaries that already open with a past verb only need their capital.
DONE = {"created", "updated", "added", "linked", "renamed", "deactivated", "activated",
        "deleted", "removed", "voided", "posted", "paid", "set", "moved", "granted", "revoked"}
# Command verbs that name the document a conversion makes (estimate invoice, work-order
# sales-receipt): the event made that document from the one it was run on.
MADE = ("invoice", "sales-receipt", "work-order", "estimate")

# A bare record id; one inside a document number ("IR-01M3...") is part of that number.
_ULID = re.compile(r"(?<![\w-])[0-9A-HJKMNP-TV-Z]{26}(?![\w-])")
_FIELD = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")


def _noun_words(noun: str) -> str:
    return Naming.subject(noun, registry.noun_meta(noun)).lower() if noun else ""


def _article(words: str) -> str:
    return ("an " if words[:1] in "aeiou" else "a ") + words


def _tidy(text: str) -> str:
    """Stray ids and field names out of running text; hyphenated nouns as words."""
    text = _ULID.sub("", text)
    text = _FIELD.sub(lambda m: Naming.column_label(m.group(0)).lower(), text)
    text = re.sub(r"\b[a-z]+(?:-[a-z]+)+\b",
                  lambda m: _noun_words(m.group(0)) if registry.noun_meta(m.group(0)).get("identifier") else m.group(0),
                  text)
    return re.sub(r"\s+", " ", text).strip(" :")


def sentence(event: Mapping[str, Any]) -> str:
    """The event's summary as a sentence that starts with what was done."""
    command = str(event.get("command") or "")
    summary = str(event.get("summary") or "").strip()
    cmd = registry.get(command) if command else None
    noun, verb = (cmd.noun, cmd.verb) if cmd is not None else (command.rpartition(" ")[0], command.rpartition(" ")[2])
    # "invoice void: 7", "estimate invoice: 12" -- the command's own name, then the number.
    if command and summary.startswith(command + ":"):
        number = summary[len(command) + 1:].strip()
        if verb in MADE and noun != verb:
            return f"Created {_noun_words(verb)} {number} from {_article(_noun_words(noun))}".strip()
        if verb in PAST:
            return f"{PAST[verb]} {_noun_words(noun)} {number}".strip()
    # "work-order work order 5", "estimate estimate 3": a conversion that names what it made.
    if verb in MADE and noun != verb and summary.startswith(verb + " "):
        return f"Created {_tidy(summary[len(verb) + 1:])} from {_article(_noun_words(noun))}"
    # A summary that is only the command's name: say it as the work.
    if not summary or summary.lower() == command.lower():
        return f"{PAST.get(verb, Naming.words(verb))} {_noun_words(noun)}".strip()
    first, _, rest = summary.partition(" ")
    if noun == "bill" and verb == "pay" and first == "pay":
        return "Paid bills with " + _tidy(rest.removeprefix("bill "))
    if first in PAST:
        return f"{PAST[first]} {_tidy(rest)}".strip()
    if first in DONE:
        return f"{first.capitalize()} {_tidy(rest)}".strip()
    text = _tidy(summary)
    return text[:1].upper() + text[1:]


def who(event: Mapping[str, Any]) -> str:
    """Who did it: "Office assistant for k" when one acted for another, else the one name."""
    actor = event.get("actor_name") or event.get("actor_id") or ""
    for_whom = event.get("on_behalf_of_name") or event.get("on_behalf_of")
    return f"{actor} for {for_whom}" if for_whom and for_whom != actor else str(actor)


# The interface a write came through, as a person names it.
INTERFACES = {"http": "HTTP", "mcp": "MCP", "cli": "CLI", "python": "Python", "gui": "Browser",
              "system": "System"}


def through(interface: Any) -> str:
    return INTERFACES.get(str(interface or ""), Naming.words(interface or ""))


def attribution(event: Mapping[str, Any]) -> str:
    """Who, for whom and through what: "Office assistant, for k, via agent".

    Only an agent acts for someone else, so a write made on someone's behalf came through an
    agent; one an agent made over anything but MCP also says which interface it used.
    """
    actor = str(event.get("actor_name") or event.get("actor_id") or "")
    for_whom = event.get("on_behalf_of_name") or event.get("on_behalf_of")
    interface = str(event.get("interface") or "")
    parts = [actor]
    if for_whom and for_whom != actor:
        parts.append(f"for {for_whom}")
    if event.get("actor_kind") == "agent" or for_whom:
        parts.append("via agent" if interface in ("mcp", "") else f"via agent over {through(interface)}")
    elif interface == "gui" or event.get("client_name") == "bookflow-workbench":
        parts.append("in the browser")
    elif interface in ("cli", "python", "mcp") or interface == "http" and event.get("client_name"):
        parts.append(f"via {through(interface)}")
    return ", ".join(part for part in parts if part)


FILTERS = {"activity": sentence, "actor": who, "through": through, "attribution": attribution}
