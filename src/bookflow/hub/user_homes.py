"""Each person's Home (R191, design/point-of-sale.md §8) and the one question menus ask (§9).

A Home is where a person lands: on signing in and when they press Home. It never decides what they may do; their
capabilities do that at the command boundary. A Home is accepted, and later used, only while it names a workbench
page the person may open, which is asked of their **effective capabilities** in that company, the same answer
`membership effective` gives and the command boundary enforces.
"""
from __future__ import annotations

from typing import Any, Callable

from bookflow.core.errors import BookflowError

FALLBACK_LINE = "Your Home page is not available to you; showing the Overview."


def requirements(cmd) -> tuple[tuple[str, str], ...]:
    """The (capability, threshold) pairs the command boundary admits a company command on (core.dispatch.authorize)."""
    return ((cmd.capability, cmd.required_role or "authenticated"), *tuple(cmd.resource_requirements))


class Effective:
    """A member's effective capabilities in one company, from `membership effective`'s permissions.

    ``admitted`` maps each (capability, threshold) the catalog knows to whether it is admitted. ``None`` means the
    installation is not activated (legacy mode), where the role alone decides.
    """

    def __init__(self, company_id: str, permissions: list[dict[str, Any]] | None):
        self.company_id = company_id
        self.admitted = None if permissions is None else {
            (p["requirement"]["capability"], p["requirement"]["threshold"]): bool(p["admitted"]) for p in permissions}

    @classmethod
    def from_output(cls, out: dict[str, Any]) -> "Effective":
        return cls(out["company_id"], out["permissions"] if out.get("mode") == "policy_v1" else None)

    def decide(self, cmd) -> bool | None:
        """True or False when the effective capabilities answer for this company command; None when they do not
        (legacy mode, a hub command, or a requirement the catalog does not list), so the role decides."""
        if self.admitted is None or cmd is None or cmd.scope != "company":
            return None
        known = [self.admitted[pair] for pair in requirements(cmd) if pair in self.admitted]
        if not all(known):
            return False
        return True if len(known) == len(requirements(cmd)) else None

    def holds(self, capability: str) -> bool | None:
        """Whether any threshold of the capability is admitted; None in legacy mode."""
        if self.admitted is None:
            return None
        return any(ok for (cap, _), ok in self.admitted.items() if cap == capability)


def effective_for(s, user_id: str, company_id: str) -> Effective:
    """The person's effective capabilities in the company, computed as `membership effective` computes them."""
    from bookflow.hub import permission_policy as policy, permission_runtime as runtime
    from bookflow.hub.permission_access import activated
    from bookflow.hub.permission_catalog import ScopeKey
    from dataclasses import asdict
    if not activated(s):
        return Effective(company_id, None)
    raw = s.hub.raw
    opened = not raw.in_transaction  # a write's plan runs before its transaction; the observation needs one
    if opened:
        raw.execute("BEGIN")
    try:
        observed = runtime.observe_current(s.hub).snapshot
        rows = policy.admissions(observed.comparison, phase='old', subject=user_id, scope=ScopeKey('company', company_id))
    finally:
        if opened and raw.in_transaction:
            raw.execute("ROLLBACK")
    return Effective(company_id, [asdict(row) for row in rows])


def permits_for(s, user_id: str, company: dict[str, Any]) -> Callable[[Any], bool]:
    """Whether the person may run a company command there: effective capabilities, else (legacy) their role."""
    from bookflow.hub import access
    effective = effective_for(s, user_id, company["id"])
    user = s.hub.raw.execute("SELECT hub_admin, active FROM main.users WHERE id=?", (user_id,)).fetchone()
    hub_admin = bool(user and user[0])
    best = None
    for scope_type, scope_id, role in s.hub.raw.execute(
            "SELECT scope_type, scope_id, role FROM main.memberships WHERE user_id=? AND revoked_at IS NULL", (user_id,)):
        if (scope_type, scope_id) in (("company", company["id"]), ("organization", company["organization_id"])):
            if best is None or access.ROLE_RANK[role] > access.ROLE_RANK[best]:
                best = role

    def permits(cmd) -> bool:
        if cmd is None or not (user and user[1]):
            return False
        decided = effective.decide(cmd)
        if decided is not None:
            return decided
        if best is None and not hub_admin:
            return False
        return access.role_satisfies(best, "company", cmd.required_role, hub_admin)
    return permits


def read(s, user_id: str) -> dict[str, Any] | None:
    row = s.hub.raw.execute(
        "SELECT user_id, company_id, path, version, set_at, set_by, set_via FROM main.user_homes WHERE user_id=?",
        (user_id,)).fetchone()
    if row is None:
        return None
    return dict(zip(("user_id", "company_id", "path", "version", "set_at", "set_by", "set_via"), row))


def availability(s, user_id: str, home: dict[str, Any] | None) -> tuple[bool, str | None, dict[str, Any] | None]:
    """(available, why not, the company row) for a stored Home, asked now."""
    from bookflow.adapters.workbench import home as Home
    if home is None:
        return False, "no Home is set", None
    row = s.hub.raw.execute(
        "SELECT id, organization_id, display_name, pending_path FROM main.companies WHERE id=?",
        (home["company_id"],)).fetchone()
    if row is None or (row[3] or "").startswith("trash/"):
        return False, "the company is no longer here", None
    company = dict(id=row[0], organization_id=row[1], display_name=row[2])
    opened = Home.page_open(company["id"], home["path"], permits_for(s, user_id, company))
    if opened is None:
        return False, "the page is not a workbench page", company
    if not opened:
        return False, "your permissions no longer open that page", company
    return True, None, company


def refuse_page(path: str, why: str) -> BookflowError:
    return BookflowError("E_VALIDATION", message=f"A Home must be a workbench page the person may open; {why}.",
                         details={"fields": [{"field": "page", "problem": why}], "page": path})
