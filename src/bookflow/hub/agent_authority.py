"""Private pure administration reconciliation and safe credential projections."""
from __future__ import annotations
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Literal, Union, get_args, get_origin, get_type_hints
from types import UnionType
from . import permission_snapshot as s, permission_policy as a

MAX_INTEGER = 9223372036854775807
CATEGORIES = frozenset(('invalid_input','conflict','unavailable_target','not_administrator',
    'protected_identity','invalid_assignment','unequal_principals','confirmation_required',
    'legacy_policy_invalid','catalog_mismatch','visibility_unresolved','unsupported_schema',
    'activation_required','integer_overflow'))


class AdministrationError(ValueError):
    """Private fixed category and static location; never carries record facts."""
    def __init__(self, category: str, field: str):
        assert category in CATEGORIES
        self.category, self.field = category, field
        super().__init__(category, field)


def fail(category, field):
    raise AdministrationError(category, field)


def typed(value, kind, field='input'):
    """Strict B2 input validation, not snapshot construction or authentication."""
    origin, args = get_origin(kind), get_args(kind)
    if origin in (Union, UnionType):
        for option in args:
            try:
                typed(value, option, field)
                return
            except AdministrationError:
                pass
        fail('invalid_input',field)
    elif origin is Literal:
        if not any(type(value) is type(x) and value == x for x in args):fail('invalid_input',field)
    elif origin is tuple:
        if type(value) is not tuple:fail('invalid_input',field)
        if len(args) == 2 and args[1] is Ellipsis:
            for item in value:typed(item,args[0],field)
        else:
            if len(value) != len(args):fail('invalid_input',field)
            for item, annotation in zip(value,args):typed(item,annotation,field)
    elif is_dataclass(kind):
        if type(value) is not kind:fail('invalid_input',field)
        for name, annotation in get_type_hints(kind).items():typed(getattr(value,name),annotation,field)
    elif type(value) is not kind:fail('invalid_input',field)


def integer(value, field):
    if type(value) is not int or not 1 <= value <= MAX_INTEGER:fail('invalid_input',field)
    return value


def increment(value, field):
    integer(value,field)
    if value==MAX_INTEGER:fail('integer_overflow',field)
    return value+1


def identity(value, field):
    if type(value) is not str or not value or len(value)>26:fail('invalid_input',field)
    return value


@dataclass(frozen=True, slots=True)
class TokenRow:
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str
    user_id: str
    on_behalf_of: str | None
    kind: Literal['bearer','session']
    expires_at: str | None
    revoked_at: str | None
    authority_epoch: int | None


def read_tokens(tx) -> tuple[TokenRow, ...]:
    names=tuple(x.name for x in fields(TokenRow))
    result=[]
    for values in tx.raw.execute('SELECT '+','.join(names)+' FROM main.api_tokens ORDER BY id'):
        row=TokenRow(*values);typed(row,TokenRow,'tokens');integer(row.version,'token_version')
        result.append(row)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ReconciliationEffects:
    authorities: tuple[s.AuthorityRow, ...]
    revoke_agents: tuple[str, ...]
    comparison: a.Comparison


def reconcile(old: s.RootFacts, proposed: s.RootFacts, *, pair: s.SnapshotPair,
              actor_id: str, at: str, via: str,
              assignment_agents: tuple[str, ...] = (),
              addition_agents: tuple[str, ...] = (),
              authorize_agent: str | None = None,
              permitted_use_confirmed: bool = False,
              fresh_context_ack: bool = False) -> ReconciliationEffects:
    """Materialize once from public A results; no SQL, authorization or commits.

    Inputs are exclusively from admitted typed B2 preparation. Explicit authorize
    admission is checked against the complete proposed eligible set here; human
    administration and target/version checks belong to identity_admin.
    """
    if pair.old != old or pair.proposed != proposed:fail('invalid_input','comparison')
    comparison=a.compare(pair.comparison)
    previous={x.agent_user_id:x for x in old.authorities}
    transitions={x.agent:x for x in comparison.agents}
    for agent in addition_agents:
        if not permitted_use_confirmed:fail('confirmation_required','permitted_use')
        if not transitions[agent].proposed_set_equal:fail('unequal_principals','assignments')
    if authorize_agent is not None:
        transition=transitions[authorize_agent];before=previous[authorize_agent]
        if not transition.proposed_set_nonempty:fail('invalid_assignment','principals')
        if not transition.proposed_set_equal:fail('unequal_principals','principals')
        if not permitted_use_confirmed:fail('confirmation_required','permitted_use')
        if before.fresh_context_required and not fresh_context_ack:fail('confirmation_required','fresh_context')
    changed=[];revoked=[]
    priority=('binding_loss','principal_authority_loss','own_authority_loss','principal_set_unequal')
    for transition in comparison.agents:
        before=previous[transition.agent];after=before
        reasons=tuple(x.value for x in transition.reasons)
        if transition.needs_suspension:
            after=replace(after,epoch=increment(before.epoch,'authority_epoch'),
                suspended_at=before.suspended_at or at,
                suspension_reason=next(x for x in priority if x in reasons),
                fresh_context_required=before.fresh_context_required or any(x in reasons for x in priority[:3]))
            revoked.append(transition.agent)
        if transition.agent in addition_agents:
            after=replace(after,permitted_use_at=at)
        if transition.agent==authorize_agent:
            # Unchanged already-authorized state is a no-op; confirmations have
            # still been checked above. Ordinary restoration never enters here.
            if before.suspended_at is not None or before.fresh_context_required or before.authorized_at is None or before.permitted_use_at is None:
                after=replace(after,suspended_at=None,suspension_reason=None,fresh_context_required=False,
                    authorized_at=at,authorized_by=actor_id,permitted_use_at=at,
                    fresh_context_ack_at=at if fresh_context_ack else before.fresh_context_ack_at)
        if after != before or transition.agent in assignment_agents:
            after=replace(after,version=increment(before.version,'authority_version'),
                          updated_at=at,updated_by=actor_id,updated_via=via)
            changed.append(after)
    return ReconciliationEffects(tuple(changed),tuple(revoked),comparison)


def revoke_tokens(rows: tuple[TokenRow, ...], user_ids: tuple[str, ...], *, actor_id: str,
                  at: str, via: str) -> tuple[TokenRow, ...]:
    """All still-unrevoked credentials, regardless of expiry/principal/epoch/kind."""
    targets=set(user_ids)
    return tuple(replace(row,revoked_at=at,version=increment(row.version,'token_version'),
                         updated_at=at,updated_by=actor_id,updated_via=via)
                 if row.user_id in targets and row.revoked_at is None else row for row in rows)
