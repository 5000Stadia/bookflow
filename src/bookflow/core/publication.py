"""Pure current-authority checks for already executed hosted command results.

No planner, applier, replay, migration or compensating write is invoked here.
"""

from dataclasses import dataclass, field
from contextlib import contextmanager

import sqlalchemy as sa

from bookflow.core.audit import decode_snapshot
from bookflow.core.context import Context
from bookflow.core.dispatch import _close, open_company, resolve_company, validate_input
from bookflow.core.errors import BookflowError
from bookflow.hub import access, credentials, schema as h


def _membership(row):
    return tuple(row.get(key) for key in ("id", "user_id", "scope_type", "scope_id", "role", "revoked_at"))


def _actor(s):
    return s.actor.id, s.actor.kind, s.is_hub_admin


def _deny():
    raise BookflowError("E_PERMISSION", details={"stage": "publication", "reason": "authority_changed"})


@contextmanager
def publication_reader(host, cred):
    session = host.reader_session(cred.user_id, cred.login)
    try:
        yield session
    finally:
        try:
            _close(session)
        finally:
            host.reader_done()


# Only these current lifecycle operations may account for their own membership
# row additions/removals. Each substitution must be in this request's hub audit.
MEMBERSHIP_EFFECTS = frozenset({"company new", "company attach", "company detach", "demo reset"})


@dataclass(repr=False)
class PublicationPermit:
    cmd: object
    inp: object
    ctx: Context
    actor: tuple
    memberships: frozenset
    company: tuple | None
    token: dict = field(repr=False)
    target_company: str | None = None
    dry_run: bool = False
    committed: bool = False
    own_event: str | None = None
    own_entries: tuple = ()
    execution_succeeded: bool = False
    targets: dict = field(default_factory=dict)

    @classmethod
    def capture(cls, cmd, raw, ctx, s, cred, selector, source, dry_run):
        inp = validate_input(cmd, raw)
        token = s.hub.conn.execute(sa.select(h.api_tokens).where(h.api_tokens.c.id == cred.token_id)).mappings().first()
        if token is None:
            raise BookflowError("E_UNAUTHENTICATED")
        return cls(cmd, inp, ctx, _actor(s), frozenset(_membership(row) for row in s.memberships),
                   None, dict(token), None, dry_run)

    def finish(self, s, *, succeeded=True, result=None):
        """Capture only a committed, same-request audit certificate, after execute."""
        if self.cmd.scope == "company" and s.company_row is not None:
            self.company = s.company_row["id"], s.company_row["organization_id"]
        self.execution_succeeded = succeeded
        if result is not None:
            self.targets = {key: result[key] for key in ("id", "user_id", "organization_id", "on_behalf_of", "authority_epoch") if key in result}
        if not succeeded or self.dry_run or not self.cmd.is_write:
            return
        self.committed = True
        if self.cmd.name not in MEMBERSHIP_EFFECTS | {"token revoke"}:
            return
        event = s.hub.conn.execute(sa.select(h.audit_events.c.id).where(
            h.audit_events.c.request_id == self.ctx.request_id,
            h.audit_events.c.command == self.cmd.name,
            h.audit_events.c.actor_id == self.actor[0],
        )).scalar_one_or_none()
        if event is None:
            return
        self.own_event = event
        self.own_entries = tuple(dict(row) for row in s.hub.conn.execute(sa.select(h.audit_entries).where(
            h.audit_entries.c.event_id == event,
            h.audit_entries.c.record_type.in_(["membership", "company", "api_token"]),
        )).mappings())
        if self.cmd.name == "company detach":
            removed = [entry["record_id"] for entry in self.own_entries if entry["record_type"] == "company" and entry["action"] == "delete"]
            if len(removed) == 1:
                self.target_company = removed[0]

    def _self_revoke(self, s, cred):
        if not (self.committed and self.cmd.name == "token revoke" and self.own_event
                and self.inp.token == cred.token_id):
            return False
        row = s.hub.conn.execute(sa.select(h.api_tokens).where(h.api_tokens.c.id == cred.token_id)).mappings().first()
        if row is None or self.token["revoked_at"] is not None:
            return False
        entries = [entry for entry in self.own_entries if entry["record_type"] == "api_token" and entry["record_id"] == cred.token_id]
        if len(entries) != 1:
            return False
        after = decode_snapshot(entries[0]["after"])
        if not after or not after.get("revoked_at") or after["revoked_at"] != row["revoked_at"]:
            return False
        for key in ("id", "user_id", "kind", "token_hash", "on_behalf_of", "authority_epoch", "expires_at"):
            if row[key] != self.token[key]:
                return False
        credentials.validate_binding(s.hub, row)
        return True

    def check(self, host, cred, *, original_response=False):
        with publication_reader(host, cred) as s:
            try:
                cred.revalidate(s.hub)
            except BookflowError:
                if not original_response or not self._self_revoke(s, cred):
                    raise
            if _actor(s) != self.actor:
                _deny()
            expected = set(self.memberships)
            if self.own_event and self.cmd.name in MEMBERSHIP_EFFECTS:
                for entry in self.own_entries:
                    if entry["record_type"] != "membership":
                        continue
                    before, after = decode_snapshot(entry["before"]), decode_snapshot(entry["after"])
                    if before and before.get("user_id") == self.actor[0]:
                        expected.discard(_membership(before))
                    if after and after.get("user_id") == self.actor[0] and after.get("revoked_at") is None:
                        expected.add(_membership(after))
            if expected != {_membership(row) for row in s.memberships}:
                _deny()
            if self.company is not None:
                row = resolve_company(s, self.company[0], "option")
                if (row["id"], row["organization_id"]) != self.company:
                    _deny()
                s.company_row = row
                if not self.execution_succeeded:
                    return  # unchanged authority may receive its original rejection
                acc, role = access.company_role(s, *self.company)
                if not access.role_satisfies(role, acc, self.cmd.required_role, s.is_hub_admin):
                    _deny()
                for capability, required in self.cmd.resource_requirements:
                    access.require_resource(s, capability, required)
                open_company(s, self.ctx, False)
            elif self.cmd.required_role == "hub_admin" and not s.is_hub_admin:
                if self.execution_succeeded:
                    _deny()
            if not self.execution_succeeded:
                return
            if self.cmd.authorize_input is not None:
                self.cmd.authorize_input(self.inp, self.ctx, s)
            self._additional(s)

    def _additional(self, s):
        from bookflow.commands import host_cmds, hub_cmds
        name = self.cmd.name
        if name == "company detach":
            if self.own_event:
                removed = [entry for entry in self.own_entries if entry["record_type"] == "company"
                           and entry["record_id"] == self.target_company and entry["action"] == "delete"]
                if len(removed) != 1 or s.hub.conn.execute(sa.select(h.companies.c.id).where(h.companies.c.id == self.target_company)).first():
                    _deny()
            else:
                resolve_company(s, self.inp.company, "option")
        elif name == "company new":
            hub_cmds._resolve_org_for_new(s, self.targets.get("organization_id", self.inp.organization))
        elif name == "user set-password":
            inp = self.inp.model_copy(update={"username": self.targets.get("user_id", self.inp.username)})
            host_cmds.authorize_set_password(inp, self.ctx, s)
        elif name == "token issue":
            inp = self.inp.model_copy(update={"user": self.targets.get("user_id", self.inp.user), "principal": self.targets.get("on_behalf_of", self.inp.principal)})
            _, _, epoch = host_cmds.authorize_token_issue(inp, self.ctx, s)
            if "authority_epoch" in self.targets and epoch != self.targets["authority_epoch"]:
                _deny()
        elif name == "token list" and self.inp.user is not None:
            host_cmds._target_user(s, self.inp.user)
        elif name == "token revoke":
            host_cmds.authorize_token_revoke(self.inp, self.ctx, s)
        elif name == "directive add" and s.actor.kind != "human" and not self.ctx.on_behalf_of:
            _deny()
        elif name == "undo":
            from bookflow.company import undo, schema as c
            original = s.company.conn.execute(sa.select(c.audit_events.c.command).where(
                c.audit_events.c.id == self.inp.event_id.upper())).scalar_one_or_none()
            if original is None:
                _deny()
            undo._require_original_role(s, undo._event_required_role(original))
