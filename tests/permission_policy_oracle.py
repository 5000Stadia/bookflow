"""Test-only oracle: permission_policy's scanning implementation from before R180 increment 2.

The production module indexes the phase facts once per comparison. This file keeps the
original rescanning code, verbatim, so tests/test_permission_policy_index.py can check on
random policies that both answer every question identically. It reuses the production
value types, so results compare with ==. Never import it from bookflow.
"""
from bookflow.hub.permission_policy import *  # noqa: F401,F403  (the value types)
from bookflow.hub.permission_policy import (_select, _floor, _reasons, _company_pairs, _payload, _loss,
    _check, _fail, _scope_key, ROLES, DELETE_NAMES, PURCHASE_DELETE_FAMILIES, Requirement, ScopeKey,
    ValidatedComparison, RoleFacts, Admission, ScopeSignature, AuthoritySignature, ActionResult,
    ExecutionResult, AgentTransition, Comparison, Reason)
from typing import Literal


def _present(p, scope):
    if scope.kind == 'hub':
        return scope.id == 'root'
    if scope.kind in ('organization', 'future_company'):
        return next(x.present for x in p.organizations if x.id == scope.id)
    return next(x.present for x in p.companies if x.id == scope.id)


def _members(p, subject, scope):
    targets = []
    if scope.kind in ('organization', 'future_company'):
        targets.append(ScopeKey('organization', scope.id))
    elif scope.kind == 'company':
        row = next(x for x in p.companies if x.id == scope.id)
        if row.present:
            targets.extend((ScopeKey('organization',row.organization), scope))
    return tuple((m.scope,m.value) for m in p.memberships
                 if m.subject == subject and m.scope in targets and m.value is not None)


def _role(p,identifier,subject,scope):
    members=_members(p,identifier,scope)
    role=max((m.role for _,m in members),key=ROLES.index,default=None)
    admin=bool(subject and subject.hub_admin)
    if p.semantics == 'scoped_v1' and scope.kind != 'hub':
        admin=False
    return RoleFacts(role,tuple(sorted((s for s,m in members if m.role==role),key=_scope_key)),admin,'hub_admin' if admin else role)


def _base_reasons(p,identifier,subject,scope):
    result=[]
    if subject is None:result.append(Reason.subject_absent)
    elif not subject.active:result.append(Reason.subject_inactive)
    if not _present(p,scope):result.append(Reason.scope_absent)
    if not next(v.visible for v in p.visibility if v.subject==identifier and v.scope==scope):result.append(Reason.scope_hidden)
    return result


def admissions(value: ValidatedComparison, *, phase: Literal['old', 'new'],
               subject: str, scope: ScopeKey) -> tuple[Admission, ...]:
    _check(scope, ScopeKey, 'scope')
    p,s=_select(value,phase,subject,scope)
    if scope.kind not in ('company','future_company'):_fail('invalid_reference','scope')
    role=_role(p,subject,s,scope);members=_members(p,subject,scope)
    current={Requirement(c.name,t) for c in p.catalog.capabilities for t in c.company_thresholds}
    defaults={(d.role,d.requirement) for d in p.catalog.defaults}
    result=[]
    for requirement in _company_pairs(value):
        present=requirement in current
        grants=tuple(k for k,m in members if requirement.capability in m.policy.grants) if present else ()
        denies=tuple(k for k,m in members if requirement.capability in m.policy.denies) if present else ()
        default=present and (role.default_role,requirement) in defaults
        if p.semantics == 'legacy_actual':
            # Public access.require_resource ignores override/default rows. Its
            # explicit-only gate always refuses Delete, even for installation admins.
            default=present and requirement.capability not in (*DELETE_NAMES,
                *('transaction.'+family+'.delete' for family in PURCHASE_DELETE_FAMILIES))
            grants=denies=()
        reasons=_base_reasons(p,subject,s,scope)
        if not _floor(role,requirement.threshold):reasons.append(Reason.role_floor)
        if not (default or grants):reasons.append(Reason.no_grant)
        if denies:reasons.append(Reason.explicit_deny)
        result.append(Admission(scope,requirement,not reasons,role,default,tuple(sorted(grants,key=_scope_key)),tuple(sorted(denies,key=_scope_key)),_reasons(reasons)))
    return tuple(result)


def signature(value: ValidatedComparison, *, phase: Literal['old', 'new'],
              subject: str) -> AuthoritySignature:
    p,s=_select(value,phase,subject)
    current={a.key:a for a in p.catalog.admin_actions}
    all_admin={a.key:a for pp in (value.old,value.new) for a in pp.catalog.admin_actions}
    scopes=[]
    for scope in value.scopes:
        role=_role(p,subject,s,scope)
        bits=[]
        # Both old and new domains are included if an action's domain changes.
        for key in sorted(all_admin):
            domains={a.domain for pp in (value.old,value.new) for a in pp.catalog.admin_actions if a.key==key}
            if scope.kind not in domains:continue
            action=current.get(key)
            admitted=bool(action and action.domain==scope.kind and not _base_reasons(p,subject,s,scope) and _floor(role,action.threshold) and (not action.human_only or (s and s.kind=='human')))
            bits.append((key,admitted))
        company=tuple((a.requirement,a.admitted) for a in admissions(value,phase=phase,subject=subject,scope=scope)) if scope.kind in ('company','future_company') else ()
        scopes.append(ScopeSignature(scope,_present(p,scope),not _base_reasons(p,subject,s,scope),role.membership_role,
            role.hub_admin if scope.kind == 'hub' or p.semantics == 'prepared_v1' else False,company,tuple(bits)))
    return AuthoritySignature(subject,s is not None,bool(s and s.active),tuple(scopes))


def company_action(value: ValidatedComparison, *, phase: Literal['old', 'new'],
                   subject: str, scope: ScopeKey, action: str) -> ActionResult:
    _check(action, str, 'action')
    atoms={a.requirement:a for a in admissions(value,phase=phase,subject=subject,scope=scope)}
    known={a.key for p in (value.old,value.new) for a in p.catalog.company_actions}
    if action not in known:_fail('unexpected_key','action')
    selected=next((a for a in getattr(value,phase).catalog.company_actions if a.key==action),None)
    if selected is None:return ActionResult(False,False,(),(Reason.action_unavailable,))
    reasons=[r for req in selected.requirements for r in atoms[req].reasons]
    admitted=not reasons
    if not selected.available:reasons.append(Reason.action_unavailable)
    if selected.remaining_graph_owners:reasons.append(Reason.graph_checks_remaining)
    return ActionResult(admitted,selected.available,selected.remaining_graph_owners,_reasons(reasons))


def execution(value: ValidatedComparison, *, phase: Literal['old', 'new'],
              actor: str, bound_human: str | None, scope: ScopeKey,
              requirement: Requirement) -> ExecutionResult:
    _check(bound_human, str | None, 'bound_human')
    _check(requirement, Requirement, 'requirement')
    p,s=_select(value,phase,actor,scope)
    atoms={a.requirement:a for a in admissions(value,phase=phase,subject=actor,scope=scope)}
    if requirement not in atoms:_fail('unexpected_key','requirement')
    own=atoms[requirement].admitted;other=None;eligible=True;unsuspended=True;reasons=[]
    if not own:reasons.append(Reason.actor_denied)
    if actor in value.agents:
        state=next(a.value for a in p.agents if a.id==actor)
        unsuspended=bool(state and not state.suspended)
        if bound_human is not None and bound_human not in value.subjects:_fail('unexpected_key','bound_human')
        eligible=bool(state and bound_human in state.eligible_humans)
        other=False
        if bound_human is not None:
            person=next(x.value for x in p.subjects if x.id==bound_human)
            if person and person.kind=='human' and person.active:
                other=next(a.admitted for a in admissions(value,phase=phase,subject=bound_human,scope=scope) if a.requirement==requirement)
        if not eligible:reasons.append(Reason.binding_ineligible)
        if not unsuspended:reasons.append(Reason.agent_suspended)
        if not other:reasons.append(Reason.principal_denied)
    elif bound_human is not None:_fail('inconsistent_fact','bound_human')
    return ExecutionResult(own,other,eligible,unsuspended,own and other is not False and eligible and unsuspended,_reasons(reasons))


def compare(value: ValidatedComparison) -> Comparison:
    if type(value) is not ValidatedComparison:_fail('invalid_type','comparison')
    old={i:signature(value,phase='old',subject=i) for i in value.subjects}
    new={i:signature(value,phase='new',subject=i) for i in value.subjects}
    left={a.id:a.value for a in value.old.agents};right={a.id:a.value for a in value.new.agents};result=[]
    for agent in value.agents:
        a,b=left[agent],right[agent];before=set(a.eligible_humans if a else ());after=set(b.eligible_humans if b else ())
        people=before|after
        equal=len({_payload(new[i]) for i in after})<=1
        noop=_payload(old[agent])==_payload(new[agent]) and before==after and all(_payload(old[i])==_payload(new[i]) for i in people)
        own_loss=_loss(old[agent],new[agent]);human_loss=any(_loss(old[i],new[i]) for i in people);binding_loss=bool(before-after)
        needed=not noop and (own_loss or human_loss or binding_loss or not equal)
        suspended=bool((a and a.suspended) or (b and b.suspended))
        reasons=[]
        for condition,reason in ((own_loss,Reason.own_authority_loss),(human_loss,Reason.principal_authority_loss),(binding_loss,Reason.binding_loss),(not equal,Reason.principal_set_unequal),(not after,Reason.empty_principal_set),(suspended,Reason.already_suspended)):
            if condition:reasons.append(reason)
        result.append(AgentTransition(agent,needed,suspended or needed,equal,bool(after),_reasons(reasons)))
    return Comparison(tuple(old.values()),tuple(new.values()),tuple(result))
