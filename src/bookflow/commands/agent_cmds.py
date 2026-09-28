"""Agent identities and their authority: `agent create/show/list/assign/unassign/authorize`,
`agent deactivate/activate`, and the same retirement for people: `user deactivate/activate`.

An agent is a user of kind `agent` that acts only on behalf of assigned humans (blueprint 4.3).
Every command here is an installation administrator's act (catalog `admin:agents`). Owning an
agent grants nothing; its company access is granted like anyone's, with `membership grant`.
Assignment and authorization run through the shared versioned administration owner, so the
same checks, suspension, epoch and token revocation, and one audit event apply on every surface.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.common import CommonOut, WriteOutput, common_out
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.lazy import lazy
from bookflow.core.models import ListOutput
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session, localize, now_iso

sa = lazy("sqlalchemy")
h = lazy("bookflow.hub.schema")
users = lazy("bookflow.hub.users")

VIA = lambda ctx: ctx.interface.value  # noqa: E731

NOT_YET_AUTHORIZED = "not_yet_authorized"
ADMIN_ONLY = "human installation administrator on an activated installation"
FRESH_CONTEXT = ("Acknowledge that the agent will resume in a fresh, isolated execution context: its "
                 "principals or authority were narrowed, and its old context may hold data the new set may not "
                 "share. Bookflow cannot erase what an external agent already saw.")


# ---------------------------------------------------------------- shared

def require_agent_administration(s: Session, *, activated_only: bool = True) -> None:
    """A human installation administrator; writes also need an activated installation."""
    if s.actor.kind != "human":
        raise BookflowError("E_PERMISSION", details={"capability": "user", "required_role": "human"})
    if not s.is_hub_admin:
        raise BookflowError("E_PERMISSION", details={"capability": "user", "required_role": "hub_admin"})
    if activated_only:
        require_activated(s)


def require_activated(s: Session) -> None:
    """Agent authority exists only under the current permission rules (row 7)."""
    from bookflow.hub.permission_access import activated
    if not activated(s):
        raise BookflowError("E_PERMISSION", message=(
            "Agent authority needs the current permission rules. Run `permission show`, then "
            "`permission activate` with its generation and catalog digest."),
            details={"reason": "activation_required"})


def republish(name: str, inp, s: Session) -> None:
    """The publication re-check: the caller still administers agents, and what it named still resolves."""
    require_agent_administration(s, activated_only=name not in ("agent show", "agent list"))
    if name in ("agent show", "agent assign", "agent unassign", "agent authorize"):
        _find_agent(s, inp.agent, include_inactive=name == "agent show")
    if name in ("agent deactivate", "agent activate"):
        _find_agent(s, inp.agent, include_inactive=True)
    if name in ("user deactivate", "user activate"):
        _find_person(s, inp.user)
    if name in ("agent assign", "agent unassign"):
        _find_principal(s, inp.principal)
    if name == "agent list" and inp.principal is not None:
        _find_principal(s, inp.principal)


def _find_agent(s: Session, selector: str, field: str = "agent", *, include_inactive: bool = False) -> dict[str, Any]:
    from bookflow.commands.host_cmds import _find_user
    row = _find_user(s, selector, include_inactive=include_inactive)
    if row is None or row["kind"] != "agent":
        raise BookflowError("E_USER_NOT_FOUND", details={field: selector})
    return row


def _find_principal(s: Session, selector: str) -> dict[str, Any]:
    from bookflow.commands.host_cmds import _find_user
    row = _find_user(s, selector)
    if row is None:
        raise BookflowError("E_USER_NOT_FOUND", details={"principal": selector})
    if row["kind"] != "human":
        raise BookflowError("E_VALIDATION", details={"fields": [
            {"field": "principal", "problem": "an agent acts only for a human"}]})
    return row


def translate(error) -> None:
    """Administration categories as this command family's public codes."""
    from bookflow.hub.permission_access import translate as shared
    category, field = error.args
    if category == "unequal_principals":
        raise BookflowError("E_AGENT_PRINCIPAL_MISMATCH", details={
            "suggestion": "These people do not hold identical permissions. Give each one a separate agent identity."}) from None
    if category == "confirmation_required":
        name = "acknowledge_fresh_context" if field == "fresh_context" else "confirm_permitted_use"
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": name, "problem": "required"}]}) from None
    if category == "invalid_assignment":
        raise BookflowError("E_VALIDATION", details={"fields": [
            {"field": "principal", "problem": "the agent needs at least one active human principal"}]}) from None
    shared(error)


def _edit(s: Session, ctx: Context, intent, *, preview: bool):
    from bookflow.hub import identity_admin as b, permission_runtime as runtime
    from bookflow.hub.permission_setup import audit_context
    from bookflow.core.identity_admin_binding import session_operation
    try:
        with session_operation(s, ctx, purpose="preview" if preview else "apply") as operation:
            binding = operation._binding("preview" if preview else "apply")
            bundle = runtime.catalog_for_root(s.hub)
            if preview:
                return b.preview_edit(s.hub, binding=binding, intent=intent, catalog=bundle,
                                      visibility=runtime.VISIBILITY, request_id=ctx.request_id)
            return b.apply_edit(s.hub, binding=binding, intent=intent, catalog=bundle, visibility=runtime.VISIBILITY,
                                audit=audit_context(ctx, getattr(s, "_permission_command", None))).private
    except b.AdministrationError as exc:
        translate(exc)


# ---------------------------------------------------------------- output

class AgentAuthority(BaseModel):
    epoch: int = Field(description="Authority epoch; every suspension raises it, and tokens bind to it")
    version: int = Field(description="Observed version; pass it as expected_version to assign, unassign or authorize")
    suspended: bool = Field(description="True while the agent may not act; new agents start suspended until authorized")
    suspension_reason: str | None = Field(description="Why it is suspended: not_yet_authorized, binding_loss, principal_authority_loss, own_authority_loss, principal_set_unequal or a migration reason")
    fresh_context_required: bool = Field(description="Authorizing needs acknowledge_fresh_context, because its principals or authority were narrowed")
    authorized_at: str | None
    authorized_by: str | None
    permitted_use_at: str | None
    fresh_context_ack_at: str | None


class AgentPrincipal(BaseModel):
    user_id: str
    username: str
    display_name: str
    assigned_at: str | None
    assigned_by: str


class AgentOut(CommonOut):
    agent_id: str
    username: str
    display_name: str
    owner_user_id: str | None
    owner_username: str | None
    active: bool
    authority: AgentAuthority
    principals: list[AgentPrincipal]


class AgentWriteOutput(WriteOutput):
    agent: AgentOut
    changed: bool
    revoked_token_count: int = Field(0, description="Tokens this change revoked; restoring access never revives them")
    message: str


def _when(s: Session, value: str | None) -> str | None:
    """A stored time in the viewer's zone; a preview's not-yet-assigned time is null."""
    return None if value in (None, "prospective") else localize(s, value)


def _authority_out(s: Session, row: dict[str, Any]) -> AgentAuthority:
    return AgentAuthority(epoch=row["epoch"], version=row["version"], suspended=row["suspended_at"] is not None,
                          suspension_reason=row["suspension_reason"], fresh_context_required=bool(row["fresh_context_required"]),
                          authorized_at=_when(s, row["authorized_at"]), authorized_by=row["authorized_by"],
                          permitted_use_at=_when(s, row["permitted_use_at"]),
                          fresh_context_ack_at=_when(s, row["fresh_context_ack_at"]))


def _agent_out(s: Session, agent: dict[str, Any], authority: dict[str, Any], assignments: list[dict[str, Any]]) -> AgentOut:
    ids = {x["principal_user_id"] for x in assignments if x["revoked_at"] is None} | ({agent["owner_user_id"]} - {None})
    people = {r["id"]: dict(r) for r in s.hub.conn.execute(sa.select(h.users).where(h.users.c.id.in_(ids))).mappings()} if ids else {}
    owner = people.get(agent["owner_user_id"])
    principals = [AgentPrincipal(user_id=x["principal_user_id"], username=people[x["principal_user_id"]]["username"],
                                 display_name=people[x["principal_user_id"]]["display_name"],
                                 assigned_at=_when(s, x["assigned_at"]),
                                 assigned_by=x["assigned_by"])
                  for x in sorted(assignments, key=lambda x: x["principal_user_id"]) if x["revoked_at"] is None]
    return AgentOut(**common_out(s, agent), agent_id=agent["id"], username=agent["username"], display_name=agent["display_name"],
                    owner_user_id=agent["owner_user_id"], owner_username=owner["username"] if owner else None,
                    active=bool(agent["active"]), authority=_authority_out(s, authority), principals=principals)


def _stored(s: Session, agent: dict[str, Any]) -> AgentOut:
    authority = s.hub.conn.execute(sa.select(h.agent_authority).where(
        h.agent_authority.c.agent_user_id == agent["id"])).mappings().first()
    assignments = [dict(r) for r in s.hub.conn.execute(sa.select(h.agent_principals).where(
        h.agent_principals.c.agent_user_id == agent["id"])).mappings()]
    return _agent_out(s, agent, dict(authority), assignments)


def _prepared(s: Session, agent: dict[str, Any], prepared) -> AgentOut:
    root = prepared.final.root
    authority = asdict(next(x for x in root.authorities if x.agent_user_id == agent["id"]))
    assignments = [asdict(x) for x in root.assignments if x.agent_user_id == agent["id"]]
    return _agent_out(s, agent, authority, assignments)


def _revoked(prepared, agent_id: str) -> int:
    before = {x.id: x for x in prepared.old_tokens}
    return sum(1 for x in prepared.final_tokens if x.user_id == agent_id and x.revoked_at is not None
               and before[x.id].revoked_at is None)


def _current_authority(s: Session, agent: dict[str, Any], expected_version: int | None) -> dict[str, Any]:
    row = dict(s.hub.conn.execute(sa.select(h.agent_authority).where(
        h.agent_authority.c.agent_user_id == agent["id"])).mappings().first())
    if expected_version is not None and expected_version != row["version"]:
        raise BookflowError("E_VERSION_CONFLICT", details={"field": "expected_version"})
    return row


# ---------------------------------------------------------------- agent create

class AgentCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    username: str = Field(description="Handle for the new agent; unique among all users, ignoring case", min_length=1, max_length=64)
    display_name: str | None = Field(None, description="Name shown in lists and the audit trail; defaults to the username", max_length=128)
    owner: str | None = Field(None, description="Human who owns this agent, for the record only; defaults to you. Ownership grants no access")


class AgentCreateOutput(WriteOutput):
    agent: AgentOut
    message: str


agent_create = command("agent create", scope="hub", capability="user", required_role="hub_admin",
                       description=("Create an agent identity. It starts suspended with no principals and no access: "
                                    "assign the humans it acts for, grant its memberships, authorize it, then issue a token."),
                       input_model=AgentCreateInput, output_model=AgentCreateOutput, writes={"hub"}, positional=["username"],
                       error_codes=["E_VALIDATION", "E_PERMISSION", "E_USER_NOT_FOUND"], authorization=ADMIN_ONLY)


@agent_create
def plan_agent_create(inp: AgentCreateInput, ctx: Context, s: Session) -> Plan:
    require_agent_administration(s)
    if users.username_key(inp.username) == "system" or users.username_matches(s.hub, inp.username):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "username", "problem": "already in use"}]})
    owner = _find_principal(s, inp.owner).get("id") if inp.owner is not None else s.actor.id
    at = now_iso()
    agent = {"id": "", "kind": "agent", "username": inp.username, "display_name": inp.display_name or inp.username,
             "owner_user_id": owner, "active": True, **users.common(s.actor.id, VIA(ctx), at)}
    authority = {"agent_user_id": "", "epoch": 1, "suspended_at": at, "suspension_reason": NOT_YET_AUTHORIZED,
                 "version": 1, "updated_at": at, "updated_by": s.actor.id, "updated_via": VIA(ctx),
                 "authorized_at": None, "authorized_by": None, "permitted_use_at": None, "fresh_context_ack_at": None,
                 "fresh_context_required": False}
    preview = AgentCreateOutput(agent=_agent_out(s, agent, authority, []), message="A dry run creates no agent.")
    return Plan(preview=preview, data={"agent": agent, "authority": authority})


@agent_create.applier
def apply_agent_create(plan: Plan, ctx: Context, s: Session) -> Applied:
    agent_id = new_id()
    agent = {**plan.data["agent"], "id": agent_id, "password_hash": None, "hub_admin": False, "timezone": None}
    authority = {**plan.data["authority"], "agent_user_id": agent_id}
    s.hub.conn.execute(h.users.insert().values(**agent))
    s.hub.conn.execute(h.agent_authority.insert().values(**authority))
    out = AgentCreateOutput(agent=_stored(s, agent), message=(
        f"{agent['username']} exists and is suspended until authorized. Next: `agent assign {agent['username']} "
        f"--principal <person> --confirm-permitted-use`, `membership grant {agent['username']} --company <company>`, "
        f"then `agent authorize {agent['username']} --confirm-permitted-use`."))
    return Applied(out, [Touched("user", agent_id, "create", None, 1, agent),
                         Touched("agent_authority", agent_id, "create", None, 1, authority)],
                   f"created the agent {agent['username']}")


# ---------------------------------------------------------------- agent show / list

class AgentSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    agent: str = Field(description="Agent username or id", max_length=64)


agent_show = command("agent show", scope="hub", capability="user", required_role="hub_admin",
                     description="Show an agent's authority: epoch, suspension and its reason, whether a fresh context is required, and the humans it acts for.",
                     input_model=AgentSelector, output_model=AgentOut, positional=["agent"],
                     error_codes=["E_PERMISSION", "E_USER_NOT_FOUND"], authorization=ADMIN_ONLY)


@agent_show
def plan_agent_show(inp: AgentSelector, ctx: Context, s: Session) -> Plan:
    require_agent_administration(s, activated_only=False)
    return Plan(_stored(s, _find_agent(s, inp.agent, include_inactive=True)))


class AgentListInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    principal: str | None = Field(None, description="Only agents assigned to act for this person; username or id", max_length=64)
    include_inactive: bool = Field(False, description="Also list deactivated agents")


agent_list = command("agent list", scope="hub", capability="user", required_role="hub_admin",
                     description="List the agents on this installation with their authority state and principals.",
                     input_model=AgentListInput, output_model=ListOutput[AgentOut],
                     error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION"], authorization=ADMIN_ONLY)


@agent_list
def plan_agent_list(inp: AgentListInput, ctx: Context, s: Session) -> Plan:
    require_agent_administration(s, activated_only=False)
    q = sa.select(h.users).where(h.users.c.kind == "agent").order_by(h.users.c.username)
    if not inp.include_inactive:
        q = q.where(h.users.c.active.is_(True))
    if inp.principal is not None:
        person = _find_principal(s, inp.principal)
        q = q.where(h.users.c.id.in_(sa.select(h.agent_principals.c.agent_user_id).where(
            h.agent_principals.c.principal_user_id == person["id"], h.agent_principals.c.revoked_at.is_(None))))
    items = [_stored(s, dict(r)) for r in s.hub.conn.execute(q).mappings()]
    return Plan(ListOutput[AgentOut](items=items, count=len(items)))


# ---------------------------------------------------------------- agent assign / unassign

class AgentAssignInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    agent: str = Field(description="Agent username or id", max_length=64)
    principal: str = Field(description="Human the agent will act on behalf of; username or id", max_length=64)
    confirm_permitted_use: bool = Field(False, description="Confirm this person permits the agent to act for them with their full permissions. Required")
    expected_version: int | None = Field(None, ge=1, description="Observed authority version from `agent show`; stale requests refuse")


class AgentUnassignInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    agent: str = Field(description="Agent username or id", max_length=64)
    principal: str = Field(description="Human the agent will no longer act for; username or id", max_length=64)
    expected_version: int | None = Field(None, ge=1, description="Observed authority version from `agent show`; stale requests refuse")


def _principals(s: Session, agent_id: str) -> tuple[str, ...]:
    return tuple(s.hub.conn.execute(sa.select(h.agent_principals.c.principal_user_id).where(
        h.agent_principals.c.agent_user_id == agent_id, h.agent_principals.c.revoked_at.is_(None))).scalars())


agent_assign = command("agent assign", scope="hub", capability="user", required_role="hub_admin",
                       description=("Let an agent act on behalf of one more human. Everyone an agent acts for must hold identical "
                                    "permissions; adding someone never lifts a suspension or revives a token."),
                       input_model=AgentAssignInput, output_model=AgentWriteOutput, writes={"hub"}, positional=["agent"],
                       error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION", "E_VERSION_CONFLICT",
                                    "E_AGENT_PRINCIPAL_MISMATCH"], authorization=ADMIN_ONLY)


def _set_intent(s: Session, inp, principals: tuple[str, ...], confirmed: bool):
    from bookflow.hub import identity_admin as b
    agent = _find_agent(s, inp.agent)
    authority = _current_authority(s, agent, inp.expected_version)
    return agent, b.SetAssignments(agent["id"], authority["version"], authority["epoch"], principals, confirmed)


@agent_assign
def plan_agent_assign(inp: AgentAssignInput, ctx: Context, s: Session) -> Plan:
    require_agent_administration(s)
    if not inp.confirm_permitted_use:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "confirm_permitted_use", "problem": "required"}]})
    agent = _find_agent(s, inp.agent)
    person = _find_principal(s, inp.principal)
    current = _principals(s, agent["id"])
    wanted = tuple(sorted(set(current) | {person["id"]}))
    agent, intent = _set_intent(s, inp, wanted, True)
    prepared = _edit(s, ctx, intent, preview=True)
    return Plan(_write_out(s, agent, prepared, f"{agent['username']} can act for {person['username']}."),
                data={"agent": agent, "intent": intent, "message": f"{agent['username']} can act for {person['username']}."})


agent_unassign = command("agent unassign", scope="hub", capability="user", required_role="hub_admin",
                         description=("Stop an agent acting for a human. Always allowed; the agent is suspended and every token "
                                      "it holds is revoked, including tokens for its other principals, until it is authorized again."),
                         input_model=AgentUnassignInput, output_model=AgentWriteOutput, writes={"hub"}, positional=["agent"],
                         error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_RECORD_NOT_FOUND", "E_VALIDATION", "E_VERSION_CONFLICT"],
                         authorization=ADMIN_ONLY)


@agent_unassign
def plan_agent_unassign(inp: AgentUnassignInput, ctx: Context, s: Session) -> Plan:
    require_agent_administration(s)
    agent = _find_agent(s, inp.agent)
    person = _find_principal(s, inp.principal)
    current = _principals(s, agent["id"])
    if person["id"] not in current:
        raise BookflowError("E_RECORD_NOT_FOUND", details={"agent": inp.agent, "principal": inp.principal})
    agent, intent = _set_intent(s, inp, tuple(x for x in current if x != person["id"]), False)
    prepared = _edit(s, ctx, intent, preview=True)
    message = (f"{agent['username']} no longer acts for {person['username']}. It is suspended and its tokens are revoked; "
               f"authorize it again, with a fresh context, before issuing new tokens.")
    return Plan(_write_out(s, agent, prepared, message), data={"agent": agent, "intent": intent, "message": message})


def _write_out(s: Session, agent: dict[str, Any], prepared, message: str) -> AgentWriteOutput:
    changed = prepared.visible.changed
    final = next((x for x in prepared.final_users if x.id == agent["id"]), None)
    agent = {**agent, "active": final.active} if final is not None else agent
    return AgentWriteOutput(agent=_prepared(s, agent, prepared), changed=changed,
                            revoked_token_count=_revoked(prepared, agent["id"]),
                            message=message if changed else f"{agent['username']} was already in that state.")


def _apply(plan: Plan, ctx: Context, s: Session) -> Applied:
    agent = plan.data["agent"]
    prepared = _edit(s, ctx, plan.data["intent"], preview=False)
    out = _write_out(s, agent, prepared, plan.data["message"])
    out = out.model_copy(update={"agent": _stored(s, _find_agent(s, agent["id"], include_inactive=True))})
    return Applied(out, [], "Updated agent authority.", audited=out.changed)


agent_assign.applier(_apply)
agent_unassign.applier(_apply)


# ---------------------------------------------------------------- agent authorize

class AgentAuthorizeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    agent: str = Field(description="Agent username or id", max_length=64)
    confirm_permitted_use: bool = Field(False, description="Confirm every assigned person permits this agent to act for them with their full permissions. Required")
    acknowledge_fresh_context: bool = Field(False, description=FRESH_CONTEXT + " Required when the agent's fresh_context_required is true")
    expected_version: int | None = Field(None, ge=1, description="Observed authority version from `agent show`; stale requests refuse")


agent_authorize = command("agent authorize", scope="hub", capability="user", required_role="hub_admin",
                          description=("Authorize an agent, the first time or after a suspension: its assigned humans must hold identical "
                                       "permissions and permit its use. Old tokens stay revoked; issue new ones with `token issue`."),
                          input_model=AgentAuthorizeInput, output_model=AgentWriteOutput, writes={"hub"}, positional=["agent"],
                          error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION", "E_VERSION_CONFLICT",
                                       "E_AGENT_PRINCIPAL_MISMATCH"], authorization=ADMIN_ONLY)


@agent_authorize
def plan_agent_authorize(inp: AgentAuthorizeInput, ctx: Context, s: Session) -> Plan:
    from bookflow.hub import identity_admin as b
    require_agent_administration(s)
    if not inp.confirm_permitted_use:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "confirm_permitted_use", "problem": "required"}]})
    agent = _find_agent(s, inp.agent)
    authority = _current_authority(s, agent, inp.expected_version)
    intent = b.AuthorizeAgent(agent["id"], authority["version"], authority["epoch"], True, inp.acknowledge_fresh_context)
    prepared = _edit(s, ctx, intent, preview=True)
    principal = next(iter(_principals(s, agent["id"])), None)
    who = users.find_user(s, id=principal)["username"] if principal else "<person>"
    message = (f"{agent['username']} is authorized at epoch {authority['epoch']}. Issue it a token with "
               f"`token issue --user {agent['username']} --principal {who}`.")
    return Plan(_write_out(s, agent, prepared, message), data={"agent": agent, "intent": intent, "message": message})


agent_authorize.applier(_apply)


# ---------------------------------------------------------------- deactivate / activate

class AgentStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    agent: str = Field(description="Agent username or id", max_length=64)
    expected_version: int | None = Field(None, ge=1, description="Observed record version (`version` from `agent show`); stale requests refuse")


def _status_intent(s: Session, row: dict[str, Any], expected_version: int | None, active: bool):
    from bookflow.hub import identity_admin as b
    if expected_version is not None and expected_version != row["version"]:
        raise BookflowError("E_VERSION_CONFLICT", details={"field": "expected_version"})
    return b.SetUserActive(row["id"], row["version"], active)


def _status_edit(s: Session, ctx: Context, intent, *, preview: bool):
    """One owner for both nouns: the last active installation administrator is kept."""
    from bookflow.hub import identity_admin as b
    try:
        return _edit(s, ctx, intent, preview=preview)
    except BookflowError as exc:
        if (exc.details or {}).get("field") == "last_administrator":
            raise BookflowError("E_PERMISSION", message=(
                "This is the last active installation administrator, so it cannot be deactivated. "
                "Add another installation administrator first."),
                details={"reason": "protected_identity", "field": "last_administrator"}) from None
        raise


agent_deactivate = command("agent deactivate", scope="hub", capability="user", required_role="hub_admin",
                           description=("Retire an agent: it stops acting at once, every token it holds is revoked in the same "
                                        "audited change, and it leaves the default lists. Its history stays. Reactivating it "
                                        "revives no token; it must be authorized again."),
                           input_model=AgentStatusInput, output_model=AgentWriteOutput, writes={"hub"}, positional=["agent"],
                           error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION", "E_VERSION_CONFLICT"],
                           authorization=ADMIN_ONLY)


@agent_deactivate
def plan_agent_deactivate(inp: AgentStatusInput, ctx: Context, s: Session) -> Plan:
    require_agent_administration(s)
    agent = _find_agent(s, inp.agent, include_inactive=True)
    intent = _status_intent(s, agent, inp.expected_version, False)
    prepared = _status_edit(s, ctx, intent, preview=True)
    message = (f"{agent['username']} is deactivated: it cannot act, its tokens are revoked and it is hidden from lists "
               f"(`agent list --include-inactive` shows it). Its history stays. `agent activate {agent['username']}` "
               f"brings it back suspended, to be authorized again.")
    out = _write_out(s, agent, prepared, message)
    if not out.changed:
        out = out.model_copy(update={"message": f"{agent['username']} was already deactivated."})
    return Plan(out, data={"agent": agent, "intent": intent, "message": message})


agent_activate = command("agent activate", scope="hub", capability="user", required_role="hub_admin",
                         description=("Bring a deactivated agent back. It returns suspended with no tokens: authorize it with "
                                      "`agent authorize`, then issue a new token."),
                         input_model=AgentStatusInput, output_model=AgentWriteOutput, writes={"hub"}, positional=["agent"],
                         error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION", "E_VERSION_CONFLICT"],
                         authorization=ADMIN_ONLY)


@agent_activate
def plan_agent_activate(inp: AgentStatusInput, ctx: Context, s: Session) -> Plan:
    require_agent_administration(s)
    agent = _find_agent(s, inp.agent, include_inactive=True)
    intent = _status_intent(s, agent, inp.expected_version, True)
    prepared = _status_edit(s, ctx, intent, preview=True)
    message = (f"{agent['username']} is active again and stays suspended; no old token works. Authorize it with "
               f"`agent authorize {agent['username']} --confirm-permitted-use`, then issue a new token.")
    out = _write_out(s, agent, prepared, message)
    if not out.changed:
        out = out.model_copy(update={"message": f"{agent['username']} was already active."})
    return Plan(out, data={"agent": agent, "intent": intent, "message": message})


def _apply_status(plan: Plan, ctx: Context, s: Session) -> Applied:
    agent = plan.data["agent"]
    prepared = _status_edit(s, ctx, plan.data["intent"], preview=False)
    out = _write_out(s, agent, prepared, plan.data["message"])
    out = out.model_copy(update={"agent": _stored(s, _find_agent(s, agent["id"], include_inactive=True))})
    return Applied(out, [], "Updated agent status.", audited=out.changed)


agent_deactivate.applier(_apply_status)
agent_activate.applier(_apply_status)


# ---------------------------------------------------------------- user deactivate / activate

class UserStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user: str = Field(description="Username or id of the person", max_length=64)
    expected_version: int | None = Field(None, ge=1, description="Observed record version (`version` from `user list`); stale requests refuse")


class UserStatusOutput(WriteOutput):
    user_id: str
    username: str
    display_name: str
    active: bool
    changed: bool
    revoked_token_count: int = Field(0, description="Their tokens and sessions this change revoked; reactivating never revives them")
    suspended_agents: list[str] = Field(default_factory=list, description="Usernames of agents acting for them that this change suspended")
    message: str


def _find_person(s: Session, selector: str) -> dict[str, Any]:
    from bookflow.commands.host_cmds import _find_user
    row = _find_user(s, selector, include_inactive=True)
    if row is None or row["kind"] == "system":
        raise BookflowError("E_USER_NOT_FOUND", details={"user": selector})
    if row["kind"] != "human":
        raise BookflowError("E_VALIDATION", details={"fields": [
            {"field": "user", "problem": "this is an agent; use `agent deactivate` or `agent activate`"}]})
    return row


def _user_status_out(s: Session, person: dict[str, Any], prepared, message: str) -> UserStatusOutput:
    final = next(x for x in prepared.final_users if x.id == person["id"])
    before = {x.id: x for x in prepared.old_tokens}
    revoked = sum(1 for x in prepared.final_tokens if x.user_id == person["id"] and x.revoked_at is not None
                  and before[x.id].revoked_at is None)
    was = {x.agent_user_id: x for x in prepared.old.authorities}
    newly = {x.agent_user_id for x in prepared.final.root.authorities
             if x.suspended_at is not None and was[x.agent_user_id].suspended_at is None}
    names = sorted(r["username"] for r in s.hub.conn.execute(sa.select(h.users.c.username).where(
        h.users.c.id.in_(newly))).mappings()) if newly else []
    changed = prepared.visible.changed
    return UserStatusOutput(user_id=person["id"], username=person["username"], display_name=person["display_name"],
                            active=bool(final.active), changed=changed, revoked_token_count=revoked,
                            suspended_agents=names, message=message)


user_deactivate = command("user deactivate", scope="hub", capability="user", required_role="hub_admin",
                          description=("Retire a person's account: they can no longer log in or act, their sessions and tokens "
                                       "are revoked, every agent acting for them is suspended, all in one audited change, and "
                                       "they leave the default lists. Their history stays. The last active installation "
                                       "administrator cannot be deactivated."),
                          input_model=UserStatusInput, output_model=UserStatusOutput, writes={"hub"}, positional=["user"],
                          error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION", "E_VERSION_CONFLICT"],
                          authorization=ADMIN_ONLY)


@user_deactivate
def plan_user_deactivate(inp: UserStatusInput, ctx: Context, s: Session) -> Plan:
    require_agent_administration(s)
    person = _find_person(s, inp.user)
    intent = _status_intent(s, person, inp.expected_version, False)
    prepared = _status_edit(s, ctx, intent, preview=True)
    if person["id"] == s.actor.id:
        # Checked after the owner's own last-administrator guard, which answers first.
        raise BookflowError("E_VALIDATION", details={"fields": [
            {"field": "user", "problem": "you cannot deactivate yourself; ask another installation administrator"}]})
    message = (f"{person['username']} is deactivated: they cannot log in or act, their sessions and tokens are revoked, "
               f"and agents acting for them are suspended. Their history stays. `user activate {person['username']}` "
               f"brings the account back; agents must be authorized again.")
    out = _user_status_out(s, person, prepared, message)
    if not out.changed:
        out = out.model_copy(update={"message": f"{person['username']} was already deactivated."})
    return Plan(out, data={"person": person, "intent": intent, "message": message})


user_activate = command("user activate", scope="hub", capability="user", required_role="hub_admin",
                        description=("Bring a deactivated person back: they can log in again with their password. Old sessions "
                                     "and tokens stay revoked, and agents suspended by the deactivation stay suspended until authorized."),
                        input_model=UserStatusInput, output_model=UserStatusOutput, writes={"hub"}, positional=["user"],
                        error_codes=["E_PERMISSION", "E_USER_NOT_FOUND", "E_VALIDATION", "E_VERSION_CONFLICT"],
                        authorization=ADMIN_ONLY)


@user_activate
def plan_user_activate(inp: UserStatusInput, ctx: Context, s: Session) -> Plan:
    require_agent_administration(s)
    person = _find_person(s, inp.user)
    intent = _status_intent(s, person, inp.expected_version, True)
    prepared = _status_edit(s, ctx, intent, preview=True)
    message = (f"{person['username']} is active again and can log in. Old sessions and tokens stay revoked; "
               f"authorize any suspended agent again with `agent authorize`.")
    out = _user_status_out(s, person, prepared, message)
    if not out.changed:
        out = out.model_copy(update={"message": f"{person['username']} was already active."})
    return Plan(out, data={"person": person, "intent": intent, "message": message})


def _apply_user_status(plan: Plan, ctx: Context, s: Session) -> Applied:
    prepared = _status_edit(s, ctx, plan.data["intent"], preview=False)
    out = _user_status_out(s, plan.data["person"], prepared, plan.data["message"])
    return Applied(out, [], "Updated account status.", audited=out.changed)


user_deactivate.applier(_apply_user_status)
user_activate.applier(_apply_user_status)
