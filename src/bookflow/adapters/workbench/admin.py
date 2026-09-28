"""Installation administration in the words the rest of the product uses.

Presentation only: users, memberships, tokens and agents as the commands return them, read with
names instead of ids, dates through the display filters and codes as sentences. Every value
shown comes from a command's own output; the forms still send exactly what the commands take.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable, Mapping

from bookflow.adapters.workbench import display as Display
from bookflow.adapters.workbench import naming as Naming
from bookflow.core.errors import BookflowError

KINDS = {"human": "Person", "agent": "Agent", "system": "System"}
ROLES = {"readonly": "Read only", "standard": "Standard", "admin": "Admin", "owner": "Owner"}
THRESHOLDS = {"member": "a membership", "standard": "Standard", "admin": "Admin", "owner": "Owner",
              "hub_admin": "an installation administrator"}
# Why an agent is suspended, as its administrator reads it.
SUSPENSIONS = {
    "not_yet_authorized": "Not yet authorized",
    "binding_loss": "Suspended: a person it acts for lost access",
    "principal_authority_loss": "Suspended: a person it acts for has fewer permissions",
    "own_authority_loss": "Suspended: its own permissions were reduced",
    "principal_set_unequal": "Suspended: the people it acts for no longer hold the same permissions",
}
# Why a permission does not apply, after "no — ".
REASONS = {
    "subject_absent": "no such user",
    "subject_inactive": "the account is deactivated",
    "scope_absent": "the company is not available",
    "scope_hidden": "no access to this company",
    "no_grant": "needs an explicit grant",
    "explicit_deny": "denied for this user",
    "human_required": "only a person may do this",
    "agent_suspended": "the agent is suspended",
}
PERMITTED_USE = ("I confirm the people this agent acts for allow it to act for them, with their full "
                 "permissions in every company it reaches.")
FRESH_CONTEXT = ("I confirm the agent starts again in a fresh, isolated context. Its people or their "
                 "permissions were narrowed, and Bookflow cannot erase what it already saw.")
CHECKBOXES = {
    "confirm_permitted_use": ("Confirm permitted use", PERMITTED_USE),
    "acknowledge_fresh_context": ("Acknowledge a fresh context", FRESH_CONTEXT),
}
# What an optional person picker leaves the command to decide.
PLACEHOLDERS = {("token", "user"): "Yourself", ("token", "token"): "Choose a token", ("token", "principal"): "Nobody: not an agent's token",
                ("agent", "owner"): "Yourself"}
FIELD_LABELS = {"token": "Token", "agent": "Agent", "principal": "Acts for", "user": "Person", "owner": "Owner",
                "expected_version": "Version", **{k: v[0] for k, v in CHECKBOXES.items()}}


def when(value: Any) -> str:
    """A recorded instant: "12 min ago" today, else its date."""
    return Display.ago(value) if value else ""


def agent_status(authority: Mapping[str, Any] | None, active: bool = True) -> str:
    if not active:
        return "Deactivated"
    if not authority:
        return ""
    if not authority.get("suspended"):
        return "Authorized"
    reason = authority.get("suspension_reason") or ""
    return SUSPENSIONS.get(reason, "Suspended: " + Naming.words(reason).lower() if reason else "Suspended")


@lru_cache(maxsize=1)
def _deletions() -> dict[str, str]:
    """Each Delete capability as the plural of the documents it deletes: "invoices"."""
    from bookflow.adapters.workbench.permissions import CAPS, NOUNS
    from bookflow.core import registry
    return {cap: Naming.list_heading(noun, registry.noun_meta(noun)).lower() for cap, noun in zip(CAPS, NOUNS)}


def capability_words(capability: str) -> str:
    """What a capability lets someone do: "Can post, edit and void"."""
    if capability == "ledger.read":
        return "Can read the books"
    if capability == "ledger.post":
        return "Can post, edit and void"
    if capability in _deletions():
        return "Can delete " + _deletions()[capability]
    return "Can use " + Naming.words(capability.replace(".", " ")).lower()


def restriction(capability: str, denied: bool) -> str:
    words = capability_words(capability)
    return "Cannot" + words[3:] if denied else words


def permission_words(row: Mapping[str, Any]) -> str:
    """One effective permission: "Can post, edit and void: no — needs Standard"."""
    requirement = row.get("requirement") or {}
    text = capability_words(requirement.get("capability", "")) + ": "
    if row.get("admitted"):
        return text + "yes"
    why = []
    for reason in row.get("reasons") or ():
        if reason == "role_floor":
            why.append("needs " + THRESHOLDS.get(requirement.get("threshold"), str(requirement.get("threshold"))))
        else:
            why.append(REASONS.get(reason, Naming.words(reason).lower()))
    return text + "no" + (" — " + "; ".join(why) if why else "")


def users(run: Callable[..., dict[str, Any]], **filters: Any) -> list[dict[str, Any]]:
    """Every `user list` row this viewer may see, read a page at a time; none when it may list none."""
    rows: list[dict[str, Any]] = []
    cursor = None
    try:
        while True:
            page = run("user list", {**filters, "limit": 200, **({"cursor": cursor} if cursor else {})})
            rows += page["items"]
            cursor = page.get("next_cursor")
            if not cursor:
                return rows
    except BookflowError as exc:
        if exc.code != "E_PERMISSION":
            raise
        return rows


def names(run: Callable[..., dict[str, Any]]) -> dict[str, str]:
    """Display names by user id, for every person and agent this viewer may list."""
    return {row["user_id"]: row["display_name"] or row["username"] for row in users(run, include_inactive=True)}


# ---------------------------------------------------------------- lists

COLUMNS = {
    "user": ("name", "username", "kind", "acts_for", "administrator", "status", "added", "added_by"),
    "membership": ("name", "company_or_organization", "role", "restrictions", "status", "granted", "granted_by"),
    "token": ("label", "person", "acting_for", "kind", "created", "last_used", "expires", "status"),
    "agent": ("name", "username", "acts_for", "status", "owner", "created"),
    "organization": ("name", "your_role"),
}


def list_rows(noun: str, items: list[dict[str, Any]], known: Mapping[str, str]) -> list[dict[str, Any]]:
    """The command's rows as a person reads them; `id` keeps each row's own link."""
    rows = []
    for it in items:
        if noun == "user":
            rows.append(dict(id=it["user_id"], name=it["display_name"], username=it["username"],
                             kind=KINDS.get(it["kind"], it["kind"]), acts_for=known.get(it.get("acts_for_user_id") or "", it.get("acts_for") or ""),
                             administrator="Installation administrator" if it.get("hub_admin") else "",
                             status="Active" if it.get("active") else "Deactivated",
                             added=Display.longday(it.get("added_at")) if it.get("added_at") else "",
                             added_by=it.get("added_by_name") or ""))
        elif noun == "membership":
            limits = [restriction(x, True) for x in it.get("denies") or ()]
            limits += [restriction(x, False) for x in it.get("grants") or ()]
            rows.append(dict(id=it["membership_id"], name=it["display_name"] + (f" ({KINDS['agent'].lower()})" if it.get("kind") == "agent" else ""),
                             company_or_organization=it["scope_name"] + (" (organization)" if it["scope_type"] == "organization" else ""),
                             role=ROLES.get(it["role"], it["role"]), restrictions="; ".join(limits),
                             status="Deactivated account" if it.get("account_active") is False else ("Active" if it.get("active") else "Revoked"),
                             granted=Display.longday(it.get("granted_at")) if it.get("granted_at") else "",
                             granted_by=it.get("granted_by_name") or ""))
        elif noun == "token":
            rows.append(dict(id=it["token_id"], label=it.get("label") or "", person=known.get(it["user_id"], it.get("username") or ""),
                             acting_for=known.get(it.get("on_behalf_of") or "", "") if it.get("on_behalf_of") else "",
                             kind={"session": "Browser session", "bearer": "Access token"}.get(it.get("kind"), Naming.words(it.get("kind") or "")),
                             created=when(it.get("created_at")), last_used=when(it.get("last_used_at")),
                             expires=Display.longday(it["expires_at"]) if it.get("expires_at") else "Never",
                             status="Revoked" if it.get("revoked_at") else "Active"))
        elif noun == "agent":
            rows.append(dict(id=it["agent_id"], name=it["display_name"], username=it["username"],
                             acts_for=", ".join(p["display_name"] for p in it.get("principals") or ()),
                             status=agent_status(it.get("authority"), it.get("active", True)),
                             owner=known.get(it.get("owner_user_id") or "", it.get("owner_username") or ""),
                             created=Display.longday(it.get("created_at")) if it.get("created_at") else ""))
        elif noun == "organization":
            rows.append(dict(id=it["organization_id"], name=it["display_name"],
                             your_role=ROLES.get(it.get("role") or "", "Installation administrator" if it.get("access") == "hub_admin" else "")))
    return rows


# ---------------------------------------------------------------- the agent page

def agent_view(agent: Mapping[str, Any], known: Mapping[str, str]) -> dict[str, Any]:
    """An agent's page: who it is, whom it acts for, and whether it may act, in sentences."""
    authority = agent.get("authority") or {}
    name = lambda user_id: known.get(user_id or "", "") or ("an administrator" if user_id else "")  # noqa: E731
    facts = [("Username", agent.get("username")),
             ("Status", agent_status(authority, agent.get("active", True))),
             ("Owner", known.get(agent.get("owner_user_id") or "", agent.get("owner_username") or "") or "—"),
             ("Created", f"{Display.longday(agent.get('created_at'))} by {name(agent.get('created_by'))}")]
    if authority.get("authorized_at"):
        facts.append(("Authorized", f"{when(authority['authorized_at'])} by {name(authority.get('authorized_by'))}"))
    if authority.get("fresh_context_required"):
        facts.append(("Before it acts again", "Authorize it again and acknowledge a fresh context: the people it "
                      "acts for, or their permissions, were narrowed, and Bookflow cannot erase what it already saw."))
    principals = [dict(name=p["display_name"], username=p["username"],
                       assigned=f"{when(p.get('assigned_at'))} by {name(p.get('assigned_by'))}" if p.get("assigned_at") else "")
                  for p in agent.get("principals") or ()]
    return dict(facts=facts, principals=principals)


# ---------------------------------------------------------------- hub forms

def decorate_form(noun: str, verb: str, leaves: list[dict[str, Any]], record_id: str | None,
                  run: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """The installation forms: the agent by name, people chosen from a list, plain checkboxes."""
    if noun not in ("agent", "token"):
        return leaves
    paths = {leaf["path"] for leaf in leaves}
    agent = None
    if noun == "agent" and record_id:
        try:
            agent = run("agent show", {"agent": record_id})
        except BookflowError:
            agent = None
    choosers: dict[str, list[tuple[str, str]]] = {}
    if paths & {"principal", "owner", "user"}:
        humans = users(run, kind="human")
        assigned = {p["username"] for p in (agent or {}).get("principals") or ()}
        if noun == "agent" and verb == "unassign":
            choosers["principal"] = [(p["username"], p["display_name"]) for p in (agent or {}).get("principals") or ()]
        elif "principal" in paths:
            choosers["principal"] = [(p["username"], p["display_name"]) for p in humans if p["username"] not in assigned]
        if "owner" in paths:
            choosers["owner"] = [(p["username"], p["display_name"]) for p in humans]
        if noun == "token" and "user" in paths:
            choosers["user"] = [(u["username"], u["display_name"] + (" (agent)" if u["kind"] == "agent" else ""))
                                for u in users(run)]
    if noun == "token" and verb == "revoke" and "token" in paths:
        known = names(run)
        choosers["token"] = [(t["token_id"], f"{t.get('label') or 'Token'} · {known.get(t['user_id'], t.get('username') or '')}"
                              f" · created {when(t.get('created_at'))}") for t in run("token list", {})["items"]]
    for leaf in leaves:
        path = leaf["path"]
        if path in FIELD_LABELS:
            leaf["label"] = FIELD_LABELS[path]
        if path == "agent" and leaf.get("pinned") and agent:
            leaf["reference"] = {"current": {"label": agent["display_name"]}}
        elif path in choosers:
            leaf["kind"] = "choice"
            leaf["placeholder"] = PLACEHOLDERS.get((noun, path), "Choose a person" if choosers[path] else "Nobody to choose")
            leaf["choices"] = [value for value, _ in choosers[path]]
            leaf["choice_labels"] = {value: f"{label} ({value})" if path != "token" and label != value else label for value, label in choosers[path]}
            leaf.pop("reference", None)
        elif path == "acknowledge_fresh_context" and agent and not (agent.get("authority") or {}).get("fresh_context_required"):
            leaf["hidden"] = True
        elif path in CHECKBOXES:
            leaf["kind"] = "checkbox"
            leaf["sentence"] = CHECKBOXES[path][1]
            leaf["description"] = None
    return [leaf for leaf in leaves if not leaf.get("hidden")]


# ---------------------------------------------------------------- the finder

# Installation pages the header finder offers from inside a company: (command, label, href, words).
# Each is offered only when the viewer may run its command, and none names an organization,
# company or person, so the finder reveals nothing beyond what the viewer may already open.
FINDER = (
    ("user list", "Users", "/hub/user", ("people", "logins", "accounts")),
    ("user add", "New user", "/hub/user/add", ("add a person", "login")),
    ("membership list", "Memberships", "/hub/membership", ("access", "roles", "who can reach")),
    ("token list", "Tokens", "/hub/token", ("access tokens", "api", "credentials")),
    ("token issue", "New token", "/hub/token/issue", ("issue a token", "api key", "credential")),
    ("agent list", "Agents", "/hub/agent", ("assistants", "ai")),
    ("agent create", "New agent", "/hub/agent/create", ("assistant", "ai")),
    ("organization list", "Organizations", "/hub/organization", ("organisations", "companies")),
)


def finder_entries(permits: Callable[[Any], bool]) -> list[dict[str, Any]]:
    from bookflow.core import registry
    entries = []
    for name, label, href, words in FINDER:
        cmd = registry.get(name)
        if cmd is not None and not cmd.local_only and permits(cmd):
            entries.append(dict(label=label, href=href, section="Installation", kind="page",
                                keywords=[name, *words]))
    return entries
