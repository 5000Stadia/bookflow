"""Pure policy facts and comparisons, not authentication or runtime authorization."""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Literal
from enum import Enum
from .permission_catalog import (ScopeKey, Requirement, SubjectKind, Role, DefaultRole,
    ScopeKind, Threshold, CommandDescriptor, CapabilitySpec, DefaultEntry, CompanyAction,
    AdminAction, ResourceSource, Catalog, CatalogManifest, PolicyInputError,
    _check, _fail, _unique, _same_keys, _scope_key, _req_key, _normal_catalog,
    catalog_manifest, THRESHOLDS, ROLES)

CatalogValue = CommandDescriptor | CapabilitySpec | Requirement | DefaultEntry | CompanyAction | AdminAction | ResourceSource

@dataclass(frozen=True, slots=True)
class Policy:
    grants: tuple[str, ...]
    denies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Membership:
    role: Role
    policy: Policy


@dataclass(frozen=True, slots=True)
class MembershipSlot:
    subject: str
    scope: ScopeKey
    value: Membership | None


@dataclass(frozen=True, slots=True)
class Subject:
    kind: SubjectKind
    active: bool
    hub_admin: bool


@dataclass(frozen=True, slots=True)
class SubjectSlot:
    id: str
    value: Subject | None


@dataclass(frozen=True, slots=True)
class OrganizationSlot:
    id: str
    present: bool


@dataclass(frozen=True, slots=True)
class CompanySlot:
    id: str
    present: bool
    organization: str | None


@dataclass(frozen=True, slots=True)
class Visibility:
    subject: str
    scope: ScopeKey
    visible: bool


@dataclass(frozen=True, slots=True)
class AgentState:
    suspended: bool
    eligible_humans: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentSlot:
    id: str
    value: AgentState | None


@dataclass(frozen=True, slots=True)
class PhaseManifest:
    organizations: tuple[str, ...]
    companies: tuple[tuple[str,str], ...]
    subjects: tuple[tuple[str,SubjectKind], ...]
    catalog: CatalogManifest


@dataclass(frozen=True, slots=True)
class Expected:
    old: PhaseManifest
    new: PhaseManifest


@dataclass(frozen=True, slots=True)
class Phase:
    catalog: Catalog
    organizations: tuple[OrganizationSlot, ...]
    companies: tuple[CompanySlot, ...]
    subjects: tuple[SubjectSlot, ...]
    memberships: tuple[MembershipSlot, ...]
    visibility: tuple[Visibility, ...]
    agents: tuple[AgentSlot, ...]


@dataclass(frozen=True, slots=True)
class ComparisonInput:
    expected: Expected
    old: Phase
    new: Phase


@dataclass(frozen=True, slots=True)
class CatalogSlot:
    kind: Literal['command','capability','requirement','default','company_action','admin_action','resource_source']
    key: tuple[str,...]
    old_value: CatalogValue | None
    new_value: CatalogValue | None


@dataclass(frozen=True, slots=True, init=False)
class ValidatedComparison:
    def __new__(cls, *args, **kwargs):
        raise TypeError("Use validate_comparison")

    expected: Expected
    old: Phase
    new: Phase
    scopes: tuple[ScopeKey,...]
    subjects: tuple[str,...]
    agents: tuple[str,...]
    catalog_slots: tuple[CatalogSlot,...]


@dataclass(frozen=True, slots=True)
class RoleFacts:
    membership_role: Role | None
    sources: tuple[ScopeKey,...]
    hub_admin: bool
    default_role: DefaultRole | None


@dataclass(frozen=True, slots=True)
class Admission:
    scope: ScopeKey
    requirement: Requirement
    admitted: bool
    role: RoleFacts
    default_present: bool
    grant_sources: tuple[ScopeKey,...]
    deny_sources: tuple[ScopeKey,...]
    reasons: tuple[Reason,...]


@dataclass(frozen=True, slots=True)
class AdminAdmission:
    scope: ScopeKey
    action: str
    admitted: bool
    reasons: tuple[Reason,...]


@dataclass(frozen=True, slots=True)
class ScopeSignature:
    scope: ScopeKey
    present: bool
    effective_visible: bool
    membership_role: Role | None
    hub_admin: bool
    company_bits: tuple[tuple[Requirement,bool],...]
    admin_bits: tuple[tuple[str,bool],...]


@dataclass(frozen=True, slots=True)
class AuthoritySignature:
    subject: str
    present: bool
    active: bool
    scopes: tuple[ScopeSignature,...]


@dataclass(frozen=True, slots=True)
class ActionResult:
    static_admitted: bool
    available: bool
    remaining_graph_owners: tuple[str,...]
    reasons: tuple[Reason,...]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    actor_admitted: bool
    principal_admitted: bool | None
    binding_eligible: bool
    authority_unsuspended: bool
    intersection_admitted: bool
    reasons: tuple[Reason,...]


@dataclass(frozen=True, slots=True)
class AgentTransition:
    agent: str
    needs_suspension: bool
    remains_suspended: bool
    proposed_set_equal: bool
    proposed_set_nonempty: bool
    reasons: tuple[Reason,...]


@dataclass(frozen=True, slots=True)
class Comparison:
    signatures_old: tuple[AuthoritySignature,...]
    signatures_new: tuple[AuthoritySignature,...]
    agents: tuple[AgentTransition,...]


class Reason(str, Enum):
    subject_absent = 'subject_absent'
    subject_inactive = 'subject_inactive'
    scope_absent = 'scope_absent'
    scope_hidden = 'scope_hidden'
    role_floor = 'role_floor'
    no_grant = 'no_grant'
    explicit_deny = 'explicit_deny'
    human_required = 'human_required'
    binding_ineligible = 'binding_ineligible'
    agent_suspended = 'agent_suspended'
    actor_denied = 'actor_denied'
    principal_denied = 'principal_denied'
    own_authority_loss = 'own_authority_loss'
    principal_authority_loss = 'principal_authority_loss'
    binding_loss = 'binding_loss'
    principal_set_unequal = 'principal_set_unequal'
    empty_principal_set = 'empty_principal_set'
    already_suspended = 'already_suspended'
    action_unavailable = 'action_unavailable'
    graph_checks_remaining = 'graph_checks_remaining'


def _reasons(values):
    return tuple(r for r in Reason if r in values)


def normalize_policy(grants: tuple[str, ...] | None, denies: tuple[str, ...] | None,
                     *, catalog: Catalog) -> Policy:
    catalog = _normal_catalog(catalog)
    return _normalize_policy(grants, denies, catalog)


def _normalize_policy(grants, denies, catalog):
    names = {c.name for c in catalog.capabilities}
    result = []
    for field, values in (('grants', grants), ('denies', denies)):
        values = () if values is None else values
        _check(values, tuple[str,...], field)
        _unique(values, lambda x:x, field)
        if not set(values) <= names:
            _fail('unknown_capability', field)
        result.append(tuple(sorted(values)))
    if set(result[0]) & set(result[1]):
        _fail('policy_overlap', 'policy')
    return Policy(*result)


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


def _manifest(m):
    _unique(m.organizations,lambda x:x,'organizations')
    _unique(m.companies,lambda x:x[0],'companies')
    _unique(m.subjects,lambda x:x[0],'subjects')
    if any(parent not in m.organizations for _,parent in m.companies):
        _fail('invalid_reference','companies')
    cm=m.catalog
    for field in ('command_names','standalone_names','capability_names','company_requirements','registered_requirements','company_action_keys','admin_action_keys'):
        _unique(getattr(cm,field),lambda x:x,field)
    return replace(m, organizations=tuple(sorted(m.organizations)),companies=tuple(sorted(m.companies)),subjects=tuple(sorted(m.subjects)),catalog=replace(cm,
        command_names=tuple(sorted(cm.command_names)),standalone_names=tuple(sorted(cm.standalone_names)),capability_names=tuple(sorted(cm.capability_names)),
        company_requirements=tuple(sorted(cm.company_requirements,key=_req_key)),registered_requirements=tuple(sorted(cm.registered_requirements,key=_req_key)),
        company_action_keys=tuple(sorted(cm.company_action_keys)),admin_action_keys=tuple(sorted(cm.admin_action_keys))))


def _catalog_map(catalog):
    result={}
    for kind, values, key in (
        ('command',catalog.commands,lambda x:(x.name,)),('capability',catalog.capabilities,lambda x:(x.name,)),
        ('default',catalog.defaults,lambda x:(x.role,x.requirement.capability,x.requirement.threshold)),
        ('company_action',catalog.company_actions,lambda x:(x.key,)),('admin_action',catalog.admin_actions,lambda x:(x.key,)),
        ('resource_source',catalog.conditional_sources,lambda x:(x.owner,))):
        result.update({(kind,key(x)):x for x in values})
    for c in catalog.capabilities:
        for t in set(c.company_thresholds)|set(c.registered_thresholds):
            result[('requirement',(c.name,t))]=Requirement(c.name,t)
    return result


def validate_comparison(value: ComparisonInput) -> ValidatedComparison:
    _check(value, ComparisonInput)
    manifests=tuple(_manifest(m) for m in (value.expected.old,value.expected.new))
    orgs=set(manifests[0].organizations)|set(manifests[1].organizations)
    companies={x[0] for m in manifests for x in m.companies}
    subjects={x[0] for m in manifests for x in m.subjects}
    kinds={}
    for m in manifests:
        for identifier,kind in m.subjects:
            if identifier in kinds and kinds[identifier]!=kind:
                _fail('inconsistent_fact','subjects')
            kinds[identifier]=kind
    agents={i for i,k in kinds.items() if k=='agent'}
    scopes=tuple(sorted((ScopeKey('hub','root'),*(ScopeKey('organization',i) for i in orgs),*(ScopeKey('company',i) for i in companies),*(ScopeKey('future_company',i) for i in orgs)),key=_scope_key))
    membership_scopes={s for s in scopes if s.kind in ('organization','company')}
    phases=[]
    for phase,manifest in zip((value.old,value.new),manifests):
        catalog=_normal_catalog(phase.catalog)
        if catalog_manifest(catalog,manifest.catalog.standalone_names)!=manifest.catalog:
            _fail('manifest_mismatch','catalog')
        oo=_unique(phase.organizations,lambda x:x.id,'organizations');_same_keys(oo,orgs,'organizations')
        cc=_unique(phase.companies,lambda x:x.id,'companies');_same_keys(cc,companies,'companies')
        ss=_unique(phase.subjects,lambda x:x.id,'subjects');_same_keys(ss,subjects,'subjects')
        aa=_unique(phase.agents,lambda x:x.id,'agents');_same_keys(aa,agents,'agents')
        mm=_unique(phase.memberships,lambda x:(x.subject,x.scope),'memberships')
        _same_keys(mm,((i,s) for i in subjects for s in membership_scopes),'memberships')
        vv=_unique(phase.visibility,lambda x:(x.subject,x.scope),'visibility')
        _same_keys(vv,((i,s) for i in subjects for s in scopes),'visibility')
        if {i for i,x in oo.items() if x.present}!=set(manifest.organizations):
            _fail('manifest_mismatch','organizations')
        expected_comp=dict(manifest.companies);expected_sub=dict(manifest.subjects)
        for i,c in cc.items():
            if c.present!=(i in expected_comp) or c.organization!=expected_comp.get(i):
                _fail('manifest_mismatch','companies')
        for i,s in ss.items():
            if (s.value is not None)!=(i in expected_sub) or (s.value and s.value.kind!=expected_sub[i]):
                _fail('manifest_mismatch','subjects')
        members=[]
        for slot in mm.values():
            if slot.value is not None:
                if ss[slot.subject].value is None or not _present(phase,slot.scope):
                    _fail('invalid_reference','memberships')
                policy=_normalize_policy(slot.value.policy.grants,slot.value.policy.denies,catalog)
                slot=replace(slot,value=replace(slot.value,policy=policy))
            members.append(slot)
        for slot in vv.values():
            s=ss[slot.subject].value
            if slot.visible and (s is None or not _present(phase,slot.scope)):
                _fail('inconsistent_fact','visibility')
            if slot.visible and s and not s.hub_admin and slot.scope.kind in ('company','future_company') and not _members(phase,slot.subject,slot.scope):
                _fail('inconsistent_fact','visibility')
        for i,slot in aa.items():
            if (slot.value is None)!=(ss[i].value is None):
                _fail('inconsistent_fact','agents')
            if slot.value:
                _unique(slot.value.eligible_humans,lambda x:x,'eligible_humans')
                for person in slot.value.eligible_humans:
                    human=ss.get(person)
                    if human is None or human.value is None or human.value.kind!='human' or not human.value.active:
                        _fail('invalid_reference','eligible_humans')
        phases.append(replace(phase,catalog=catalog,organizations=tuple(oo[i] for i in sorted(oo)),companies=tuple(cc[i] for i in sorted(cc)),subjects=tuple(ss[i] for i in sorted(ss)),
            memberships=tuple(sorted(members,key=lambda x:(x.subject,_scope_key(x.scope)))),visibility=tuple(sorted(vv.values(),key=lambda x:(x.subject,_scope_key(x.scope)))),
            agents=tuple(replace(aa[i],value=replace(aa[i].value,eligible_humans=tuple(sorted(aa[i].value.eligible_humans)))) if aa[i].value else aa[i] for i in sorted(aa))))
    left,right=map(_catalog_map,(phases[0].catalog,phases[1].catalog))
    slots=tuple(CatalogSlot(k,key,left.get((k,key)),right.get((k,key))) for k,key in sorted(set(left)|set(right)))
    # No public constructor can create an admitted wrapper. Nested values are frozen.
    result=object.__new__(ValidatedComparison)
    for field,item in zip(('expected','old','new','scopes','subjects','agents','catalog_slots'),
                          (Expected(*manifests),*phases,scopes,tuple(sorted(subjects)),tuple(sorted(agents)),slots)):
        object.__setattr__(result,field,item)
    return result


def _select(value,phase,subject,scope=None):
    _check(phase, Literal['old', 'new'], 'phase')
    _check(subject, str, 'subject')
    if scope is not None:
        _check(scope, ScopeKey, 'scope')
    if type(value) is not ValidatedComparison or phase not in ('old','new'):
        _fail('invalid_type','comparison')
    if subject not in value.subjects:
        _fail('unexpected_key','subject')
    if scope is not None and scope not in value.scopes:
        _fail('unexpected_key','scope')
    p=getattr(value,phase)
    s=next(x.value for x in p.subjects if x.id==subject)
    return p,s


def _role(p,identifier,subject,scope):
    members=_members(p,identifier,scope)
    role=max((m.role for _,m in members),key=ROLES.index,default=None)
    admin=bool(subject and subject.hub_admin)
    return RoleFacts(role,tuple(sorted((s for s,m in members if m.role==role),key=_scope_key)),admin,'hub_admin' if admin else role)


def _floor(role,threshold):
    if threshold=='authenticated':return True
    if role.hub_admin:return True
    if threshold=='hub_admin' or role.membership_role is None:return False
    return ROLES.index(role.membership_role)>={'member':0,'standard':1,'admin':2,'owner':3}[threshold]


def _base_reasons(p,identifier,subject,scope):
    result=[]
    if subject is None:result.append(Reason.subject_absent)
    elif not subject.active:result.append(Reason.subject_inactive)
    if not _present(p,scope):result.append(Reason.scope_absent)
    if not next(v.visible for v in p.visibility if v.subject==identifier and v.scope==scope):result.append(Reason.scope_hidden)
    return result


def _company_pairs(value):
    return tuple(sorted({Requirement(c.name,t) for p in (value.old,value.new) for c in p.catalog.capabilities for t in c.company_thresholds},key=_req_key))


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
        scopes.append(ScopeSignature(scope,_present(p,scope),not _base_reasons(p,subject,s,scope),role.membership_role,role.hub_admin,company,tuple(bits)))
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


def _payload(s):
    return s.present,s.active,s.scopes


def _loss(old,new):
    if old.active and not new.active:return True
    for a,b in zip(old.scopes,new.scopes):
        if a.effective_visible and not b.effective_visible:return True
        rank=lambda r: -1 if r is None else ROLES.index(r)
        if rank(a.membership_role)>rank(b.membership_role) or (a.hub_admin and not b.hub_admin):return True
        if any(x and not y for (_,x),(_,y) in zip(a.company_bits,b.company_bits)):return True
        if any(x and not y for (_,x),(_,y) in zip(a.admin_bits,b.admin_bits)):return True
    return False


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
