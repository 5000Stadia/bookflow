"""Handwritten policy outcomes on complete in-memory rosters; no company fixture."""
from dataclasses import FrozenInstanceError, replace
import pytest
from bookflow.hub import permission_policy as p
from bookflow.hub.permission_catalog import (
    Requirement as R, ScopeKey as S, CapabilitySpec, Catalog, DefaultEntry,
    CompanyAction, AdminAction, catalog_manifest, PolicyInputError, ROLES)

READ=R('ledger.read','member'); POST=R('ledger.post','standard')
WORK=R('customer-work','member'); DELETE=R('transaction.invoice.delete','standard')
PAYDELETE=R('transaction.payment.delete','standard')
O=S('organization','O'); Z=S('organization','Z'); C=S('company','C'); D=S('company','D'); F=S('future_company','O'); HUB=S('hub','root')


def catalog():
    # Literal expected role/default semantics; no production resolver builds this fixture.
    specs=(CapabilitySpec('ledger.read',('member',),('member',)),
           CapabilitySpec('ledger.post',('standard',),('standard',)),
           CapabilitySpec('customer-work',('member',),('member',)),
           CapabilitySpec(DELETE.capability,('standard',),()),
           CapabilitySpec(PAYDELETE.capability,('standard',),()))
    defaults=tuple(DefaultEntry(r,READ) for r in (*ROLES,'hub_admin'))+tuple(DefaultEntry(r,POST) for r in ('standard','admin','owner','hub_admin'))+tuple(DefaultEntry(r,WORK) for r in (*ROLES,'hub_admin'))
    actions=(CompanyAction('delete',(DELETE,READ),False,('financial-owner',)),CompanyAction('post',(POST,),True,()))
    admin=(AdminAction('admin:users','hub','hub_admin',True,False),
           AdminAction('admin:members:owner:company','company','owner',True,False),
           AdminAction('admin:members:standard:company','company','admin',True,False),
           AdminAction('admin:members:standard:organization','organization','admin',True,False))
    return Catalog('test-v1',(),specs,defaults,actions,admin,())


def member(role='standard',grants=(),denies=()):
    return p.Membership(role,p.Policy(grants,denies))


def pair(*, old_policies=None,new_policies=None,old_people=None,new_people=None,
         old_bind=('P','Q'),new_bind=None,old_suspended=False,new_suspended=None,
         old_comp=None,new_comp=None,old_org=('O','Z'),new_org=None,
         old_catalog=None,new_catalog=None,hidden=()):
    old_people=old_people if old_people is not None else {'G':p.Subject('agent',True,False),'P':p.Subject('human',True,False),'Q':p.Subject('human',True,False),'R':p.Subject('human',True,False)}
    new_people=old_people if new_people is None else new_people
    old_policies=old_policies if old_policies is not None else {(i,O):member() for i in old_people}
    new_policies=old_policies if new_policies is None else new_policies
    old_comp={'C':'O','D':'Z'} if old_comp is None else old_comp
    new_comp=old_comp if new_comp is None else new_comp
    new_org=old_org if new_org is None else new_org
    old_catalog=old_catalog or catalog();new_catalog=new_catalog or old_catalog
    people=set(old_people)|set(new_people);orgs=set(old_org)|set(new_org);companies=set(old_comp)|set(new_comp)
    agents={i for users in (old_people,new_people) for i,u in users.items() if u.kind=='agent'}
    scopes=[HUB,*[S('organization',i) for i in orgs],*[S('company',i) for i in companies],*[S('future_company',i) for i in orgs]]
    def phase(users,org,comp,policies,cat,bindings,suspended):
        vis=[]
        for i in people:
            for scope in scopes:
                exists=scope.kind=='hub' or scope.id in (comp if scope.kind=='company' else org)
                parent=comp.get(scope.id) if scope.kind=='company' else scope.id
                visible=scope.kind=='hub' or ((i,S('organization',parent)) in policies or (i,scope) in policies)
                if i in users and users[i].hub_admin:visible=True  # explicitly supplied test fact, not a policy decision
                vis.append(p.Visibility(i,scope,bool(exists and i in users and visible and (i,scope) not in hidden)))
        return p.Phase(cat,tuple(p.OrganizationSlot(i,i in org) for i in orgs),tuple(p.CompanySlot(i,i in comp,comp.get(i)) for i in companies),
            tuple(p.SubjectSlot(i,users.get(i)) for i in people),tuple(p.MembershipSlot(i,s,policies.get((i,s))) for i in people for s in scopes if s.kind in ('organization','company')),
            tuple(vis),tuple(p.AgentSlot(i,p.AgentState(suspended,bindings) if i in users else None) for i in agents))
    left=phase(old_people,old_org,old_comp,old_policies,old_catalog,old_bind,old_suspended)
    right=phase(new_people,new_org,new_comp,new_policies,new_catalog,old_bind if new_bind is None else new_bind,old_suspended if new_suspended is None else new_suspended)
    def manifest(users,org,comp,cat):return p.PhaseManifest(tuple(org),tuple(comp.items()),tuple((i,u.kind) for i,u in users.items()),catalog_manifest(cat))
    return p.ComparisonInput(p.Expected(manifest(old_people,old_org,old_comp,old_catalog),manifest(new_people,new_org,new_comp,new_catalog)),left,right)


def atom(v,req=READ,subject='P',scope=C,phase='new'):
    return next(a for a in p.admissions(v,phase=phase,subject=subject,scope=scope) if a.requirement==req)


def transition(inp):return p.compare(p.validate_comparison(inp)).agents[0]


@pytest.mark.parametrize('grants,denies,category',[(('ledger.read','ledger.read'),(),'duplicate_key'),(('Ledger.Read',),(),'unknown_capability'),(('ledger',),(),'unknown_capability'),(('ledger.read',),('ledger.read',),'policy_overlap'),((),('ledger.read','ledger.read'),'duplicate_key')])
def test_policy_input_rejects_not_normalizes(grants,denies,category):
    with pytest.raises(PolicyInputError) as e:p.normalize_policy(grants,denies,catalog=catalog())
    assert e.value.category.value==category and 'ledger' not in str(e.value)


def test_null_empty_and_immutable_values():
    assert p.normalize_policy(None,(),catalog=catalog())==p.Policy((),())
    v=p.validate_comparison(pair())
    with pytest.raises(FrozenInstanceError):v.old=None
    with pytest.raises(TypeError):p.ValidatedComparison()
    with pytest.raises(FrozenInstanceError):v.old.subjects[0].value.active=False


@pytest.mark.parametrize('org,company',[(member(grants=(DELETE.capability,),denies=('ledger.read',)),member(grants=('ledger.read',))),
                                      (member(grants=('ledger.read',)),member(denies=('ledger.read',)))])
def test_either_scope_deny_wins_and_all_provenance_retained(org,company):
    policies={(i,O):member() for i in ('G','P','Q','R')};policies['P',O]=org;policies['P',C]=company;policies['P',Z]=member(grants=('ledger.read',))
    a=atom(p.validate_comparison(pair(old_policies=policies)))
    assert not a.admitted and a.default_present
    assert a.deny_sources==((O,) if org.policy.denies else (C,))
    assert a.grant_sources==((C,) if org.policy.denies else (O,))
    assert a.reasons==(p.Reason.explicit_deny,)


@pytest.mark.parametrize('orgrole,companyrole,winner', [('readonly','admin','admin'),('owner','standard','owner'),('admin','admin','admin')])
def test_highest_role_and_tied_sources(orgrole,companyrole,winner):
    policies={('P',O):member(orgrole),('P',C):member(companyrole)}
    v=p.validate_comparison(pair(old_policies=policies,old_bind=()))
    a=atom(v,POST)
    assert a.admitted and a.role.membership_role==winner
    assert a.role.sources==((O,C) if orgrole==companyrole else ((O,) if winner==orgrole else (C,)))


@pytest.mark.parametrize('role',ROLES+('hub_admin',))
def test_delete_defaults_off_and_floor(role):
    users={'P':p.Subject('human',True,role=='hub_admin')}
    policies={('P',O):member(role if role!='hub_admin' else 'readonly')}
    a=p.validate_comparison(pair(old_people=users,old_policies=policies,old_bind=()))
    assert not atom(a,DELETE).admitted
    policies['P',O]=member(role if role!='hub_admin' else 'readonly',grants=(DELETE.capability,))
    b=p.validate_comparison(pair(old_people=users,old_policies=policies,old_bind=()))
    assert atom(b,DELETE).admitted==(role!='readonly')
    assert not atom(b,PAYDELETE).admitted


def test_future_inheritance_and_conjunction_do_not_claim_delete_execution():
    policies={(i,O):member() for i in ('G','P','Q','R')}
    policies['P',O]=member(grants=(DELETE.capability,),denies=('ledger.post',))
    policies['Q',C]=member(grants=(DELETE.capability,))
    v=p.validate_comparison(pair(old_policies=policies))
    assert atom(v,DELETE,scope=C).admitted and atom(v,DELETE,scope=F).admitted
    assert atom(v,DELETE,subject='Q',scope=C).admitted and not atom(v,DELETE,subject='Q',scope=F).admitted
    action=p.company_action(v,phase='new',subject='P',scope=C,action='delete')
    assert action.static_admitted and not action.available and action.remaining_graph_owners==('financial-owner',)
    assert not atom(v,POST).admitted
    policies['P',O]=member(grants=(DELETE.capability,),denies=('ledger.read',))
    v=p.validate_comparison(pair(old_policies=policies))
    assert atom(v,DELETE).admitted and not p.company_action(v,phase='new',subject='P',scope=C,action='delete').static_admitted


def test_narrow_agent_and_every_human_loss():
    policies={(i,O):member() for i in ('G','P','Q','R')};policies['G',O]=member('readonly')
    v=p.validate_comparison(pair(old_policies=policies))
    x=p.execution(v,phase='new',actor='G',bound_human='P',scope=C,requirement=POST)
    assert not x.actor_admitted and x.principal_admitted and not x.intersection_admitted
    changed=dict(policies);changed['P',O]=member('readonly')
    t=transition(pair(old_policies=policies,new_policies=changed))
    assert t.needs_suspension and p.Reason.principal_authority_loss in t.reasons
    assert not transition(pair(old_policies=policies)).needs_suspension


@pytest.mark.parametrize('suspended',[False,True])
def test_f1_unchanged_unequal_noop_is_not_new_effect(suspended):
    policies={(i,O):member() for i in ('G','P','Q','R')};policies['P',O]=member('readonly')
    changed=dict(policies);changed['P',O]=member('readonly',grants=(DELETE.capability,))
    for right in (policies,changed):
        t=transition(pair(old_policies=policies,new_policies=right,old_suspended=suspended))
        assert (t.needs_suspension,t.remains_suspended,t.proposed_set_equal)==(False,suspended,False)
        assert p.Reason.principal_set_unequal in t.reasons


@pytest.mark.parametrize('bindings',[('Q',),('R','Q'),()])
def test_binding_loss_even_equal_replacement_or_empty(bindings):
    t=transition(pair(new_bind=bindings,old_suspended=True))
    assert t.needs_suspension and t.remains_suspended and t.proposed_set_equal
    assert p.Reason.binding_loss in t.reasons
    assert t.proposed_set_nonempty==bool(bindings)


def test_gain_inequality_restore_and_further_suspended_loss():
    base={(i,O):member() for i in ('G','P','Q','R')};wide=dict(base);wide['P',O]=member(grants=(DELETE.capability,))
    assert transition(pair(old_policies=base,new_policies=wide)).needs_suspension
    equal=dict(wide);equal['Q',O]=member(grants=(DELETE.capability,))
    t=transition(pair(old_policies=wide,new_policies=equal,old_suspended=True,new_suspended=False))
    assert not t.needs_suspension and t.remains_suspended and t.proposed_set_equal
    loss=dict(equal);loss['G',O]=member('readonly')
    assert transition(pair(old_policies=equal,new_policies=loss,old_suspended=True)).needs_suspension


def test_latent_grant_role_gain_net_gain_loss_and_role_reduction():
    base={(i,O):member('readonly') for i in ('G','P','Q','R')};latent=dict(base);latent['P',O]=member('readonly',grants=(DELETE.capability,))
    assert not transition(pair(old_policies=latent,new_policies=base)).needs_suspension
    raised=dict(latent);raised['P',O]=member('standard',grants=(DELETE.capability,))
    assert transition(pair(old_policies=latent,new_policies=raised)).needs_suspension
    standard={(i,O):member() for i in ('G','P','Q','R')};mixed=dict(standard);mixed['P',O]=member(grants=(DELETE.capability,),denies=('ledger.read',))
    t=transition(pair(old_policies=standard,new_policies=mixed));assert p.Reason.principal_authority_loss in t.reasons
    admin={(i,O):member('admin') for i in ('G','P','Q','R')};assert transition(pair(old_policies=admin,new_policies=standard)).needs_suspension


def test_removed_subject_scope_catalog_and_reparented_company():
    inp=pair();people={x.id:x.value for x in inp.old.subjects};people.pop('P')
    policies={(i,O):member() for i in people}
    assert transition(pair(new_people=people,new_policies=policies,new_bind=('Q',))).needs_suspension
    assert transition(pair(new_comp={'D':'Z'})).needs_suspension
    assert transition(pair(new_comp={'C':'Z','D':'Z'})).needs_suspension
    cat=catalog();less=replace(cat,version='v2',defaults=tuple(d for d in cat.defaults if d.requirement!=READ))
    assert transition(pair(new_catalog=less)).needs_suspension


def test_removed_contract_only_pair_is_explicit_false_on_new_side():
    cat=catalog();policies={(i,O):member(grants=(DELETE.capability,)) for i in ('G','P','Q','R')}
    less=replace(cat,version='v2',capabilities=tuple(s for s in cat.capabilities if s.name!=DELETE.capability),company_actions=tuple(a for a in cat.company_actions if a.key!='delete'))
    newpol={(i,O):member() for i in ('G','P','Q','R')}
    v=p.validate_comparison(pair(old_policies=policies,new_policies=newpol,new_catalog=less))
    assert atom(v,DELETE,phase='old').admitted and not atom(v,DELETE).admitted
    slot=next(s for s in v.catalog_slots if s.kind=='requirement' and s.key==(DELETE.capability,'standard'))
    assert slot.old_value==DELETE and slot.new_value is None
    assert p.compare(v).agents[0].needs_suspension


@pytest.mark.parametrize('field',['organizations','companies','subjects','memberships','visibility','agents'])
def test_every_dense_input_category_required(field):
    inp=pair();old=replace(inp.old,**{field:getattr(inp.old,field)[1:]})
    with pytest.raises(PolicyInputError) as e:p.validate_comparison(replace(inp,old=old))
    assert e.value.category.value=='missing_key'
    assert 'P' not in str(e.value)


def test_bad_types_duplicates_parents_manifest_and_immutability():
    inp=pair()
    bad=[replace(inp,old=replace(inp.old,subjects=list(inp.old.subjects))),
         replace(inp,old=replace(inp.old,subjects=inp.old.subjects+(inp.old.subjects[0],))),
         replace(inp,old=replace(inp.old,companies=(p.CompanySlot('C',True,'absent'),p.CompanySlot('D',True,'Z')))),
         replace(inp,old=replace(inp.old,subjects=(replace(inp.old.subjects[0],value=p.Subject('human',1,False)),)+inp.old.subjects[1:]))]
    for value in bad:
        with pytest.raises(PolicyInputError):p.validate_comparison(value)
    m=inp.expected.old.catalog
    with pytest.raises(PolicyInputError):p.validate_comparison(replace(inp,expected=replace(inp.expected,old=replace(inp.expected.old,catalog=replace(m,capability_names=m.capability_names[1:])))))


def test_order_normalization_features_and_unavailable_actions_not_signature():
    inp=pair();rev=lambda phase:replace(phase,subjects=tuple(reversed(phase.subjects)),memberships=tuple(reversed(phase.memberships)),visibility=tuple(reversed(phase.visibility)))
    assert p.validate_comparison(inp)==p.validate_comparison(replace(inp,old=rev(inp.old),new=rev(inp.new)))
    cat=catalog();changed=replace(cat,version='new',company_actions=tuple(replace(a,available=not a.available) for a in cat.company_actions))
    assert not transition(pair(new_catalog=changed)).needs_suspension


def test_admin_grant_only_scope_and_human_separation():
    users={'P':p.Subject('human',True,False),'G':p.Subject('agent',True,True)}
    v=p.validate_comparison(pair(old_people=users,old_policies={('P',C):member('admin')},old_bind=()))
    sig=p.signature(v,phase='new',subject='P');by={s.scope:dict(s.admin_bits) for s in sig.scopes}
    assert by[C]['admin:members:standard:company'] and not by[C]['admin:members:owner:company']
    assert not by[O]['admin:members:standard:organization']
    assert not dict(p.signature(v,phase='new',subject='G').scopes[0].admin_bits)['admin:users']
    users['P']=p.Subject('human',True,True)
    v=p.validate_comparison(pair(old_people=users,old_policies={},old_bind=()))
    assert dict(p.signature(v,phase='new',subject='P').scopes[0].admin_bits)['admin:users']
    v=p.validate_comparison(pair(old_people=users,old_policies={},old_bind=(),hidden=(('P',HUB),)))
    assert not dict(p.signature(v,phase='new',subject='P').scopes[0].admin_bits)['admin:users']


def test_existing_companies_equal_but_symbolic_future_distinguishes():
    # Same present company policy, different inheritance for a not-yet-created C2.
    policies = {(i,O):member() for i in ('G','P','Q','R')}
    policies['P',O] = member(grants=(DELETE.capability,))
    policies['Q',C] = member(grants=(DELETE.capability,))
    v = p.validate_comparison(pair(old_policies=policies))
    left = {s.scope:s for s in p.signature(v,phase='new',subject='P').scopes}
    right = {s.scope:s for s in p.signature(v,phase='new',subject='Q').scopes}
    assert left[C] == right[C] and left[D] == right[D]
    assert dict(left[F].company_bits)[DELETE] and not dict(right[F].company_bits)[DELETE]
    assert not p.compare(v).agents[0].proposed_set_equal


def test_inactive_human_and_agent_loss_and_absent_agent_execution():
    inp = pair()
    users = {s.id:s.value for s in inp.old.subjects}
    inactive = dict(users, P=p.Subject('human',False,False))
    t = transition(pair(new_people=inactive,new_bind=('Q',),old_suspended=True))
    assert t.needs_suspension and t.remains_suspended
    assert p.Reason.principal_authority_loss in t.reasons and p.Reason.binding_loss in t.reasons
    inactive = dict(users, G=p.Subject('agent',False,False))
    v = p.validate_comparison(pair(new_people=inactive))
    assert p.Reason.own_authority_loss in p.compare(v).agents[0].reasons
    x = p.execution(v,phase='new',actor='G',bound_human='P',scope=C,requirement=READ)
    assert not x.actor_admitted and x.principal_admitted and not x.intersection_admitted
    removed = {i:s for i,s in users.items() if i != 'G'}
    v = p.validate_comparison(pair(new_people=removed,new_policies={(i,O):member() for i in removed}))
    x = p.execution(v,phase='new',actor='G',bound_human='P',scope=C,requirement=READ)
    assert not x.actor_admitted and not x.binding_eligible and not x.authority_unsuspended and not x.intersection_admitted


@pytest.mark.parametrize('binding', [('G',), ('missing-private-human',), ('P','P')])
def test_invalid_eligible_bindings_reject_without_identifiers(binding):
    with pytest.raises(PolicyInputError) as exc:
        p.validate_comparison(pair(new_bind=binding))
    assert str(exc.value) in ("('invalid_reference', 'eligible_humans')", "('duplicate_key', 'eligible_humans')")
    assert 'missing-private-human' not in repr(exc.value)


def test_inactive_human_binding_rejected_and_execution_conjunction_guards():
    users = {'G':p.Subject('agent',True,False), 'P':p.Subject('human',False,False)}
    with pytest.raises(PolicyInputError):
        p.validate_comparison(pair(old_people=users,old_bind=('P',)))
    v = p.validate_comparison(pair(old_suspended=True))
    x = p.execution(v,phase='new',actor='G',bound_human='P',scope=C,requirement=READ)
    assert x.actor_admitted and x.principal_admitted and x.binding_eligible
    assert not x.authority_unsuspended and not x.intersection_admitted
    assert x.reasons == (p.Reason.agent_suspended,)
    v = p.validate_comparison(pair())
    for human in (None,'R'):
        x = p.execution(v,phase='new',actor='G',bound_human=human,scope=C,requirement=READ)
        assert not x.binding_eligible and not x.intersection_admitted
    with pytest.raises(PolicyInputError):
        p.execution(v,phase='new',actor='P',bound_human='Q',scope=C,requirement=READ)
    with pytest.raises(PolicyInputError):
        p.execution(v,phase='new',actor='G',bound_human='missing-private-human',scope=C,requirement=READ)
    assert p.execution(v,phase='new',actor='P',bound_human=None,scope=C,requirement=READ).intersection_admitted


def test_human_loss_below_narrow_agent_for_either_principal_and_removed_principal():
    old = {(i,O):member() for i in ('G','P','Q','R')}
    old['G',O] = member('readonly')
    for human in ('P','Q'):
        new = dict(old)
        new[human,O] = member('readonly')
        t = transition(pair(old_policies=old,new_policies=new,old_suspended=True))
        assert t.needs_suspension and t.remains_suspended
        assert p.Reason.principal_authority_loss in t.reasons
    new = dict(old)
    new['P',O] = member('readonly')
    t = transition(pair(old_policies=old,new_policies=new,new_bind=('Q',)))
    assert t.needs_suspension and t.proposed_set_equal
    assert p.Reason.binding_loss in t.reasons and p.Reason.principal_authority_loss in t.reasons


def test_company_and_future_visibility_facts_are_required_not_invented():
    users = {'P':p.Subject('human',True,False)}
    inp = pair(old_people=users,old_policies={},old_bind=())
    assert not atom(p.validate_comparison(inp)).admitted
    old = replace(inp.old,visibility=tuple(replace(v,visible=True) if v.scope==C else v for v in inp.old.visibility))
    with pytest.raises(PolicyInputError) as exc:
        p.validate_comparison(replace(inp,old=old))
    assert exc.value.category.value == 'inconsistent_fact'
    admin = {'P':p.Subject('human',True,True)}
    # These are supplied facts for the pending product decision, not its resolution.
    for scope in (C,F):
        visible = p.validate_comparison(pair(old_people=admin,old_policies={},old_bind=()))
        hidden = p.validate_comparison(pair(old_people=admin,old_policies={},old_bind=(),hidden=(('P',scope),)))
        assert atom(visible,scope=scope).admitted and not atom(hidden,scope=scope).admitted
        assert atom(hidden,scope=scope).role.hub_admin


def test_empty_roster_only_when_manifest_empty_and_explicit_absence():
    empty = pair(old_people={},old_policies={},old_bind=(),old_comp={},old_org=())
    v = p.validate_comparison(empty)
    assert v.scopes == (HUB,) and p.compare(v) == p.Comparison((),(),())
    with pytest.raises(PolicyInputError):
        p.validate_comparison(replace(pair(),old=empty.old))
    inp = pair()
    old = replace(inp.old,subjects=tuple(replace(s,value=None) if s.id=='P' else s for s in inp.old.subjects))
    with pytest.raises(PolicyInputError):
        p.validate_comparison(replace(inp,old=old))


def test_role_reduction_even_when_every_company_bit_is_identical():
    old = {(i,O):member('admin') for i in ('G','P','Q','R')}
    new = dict(old)
    new['P',O] = member('standard')
    cat = replace(catalog(),admin_actions=())
    v = p.validate_comparison(pair(old_policies=old,new_policies=new,old_catalog=cat))
    before = p.signature(v,phase='old',subject='P')
    after = p.signature(v,phase='new',subject='P')
    assert tuple(s.company_bits for s in before.scopes) == tuple(s.company_bits for s in after.scopes)
    assert p.Reason.principal_authority_loss in p.compare(v).agents[0].reasons


def test_removed_registered_requirement_and_admin_action_explicit_absence():
    cat = catalog()
    removed = replace(cat,version='without-post',capabilities=tuple(s for s in cat.capabilities if s.name!='ledger.post'),defaults=tuple(d for d in cat.defaults if d.requirement!=POST),company_actions=tuple(a for a in cat.company_actions if a.key!='post'),admin_actions=())
    v = p.validate_comparison(pair(new_catalog=removed))
    assert atom(v,POST,phase='old').admitted and not atom(v,POST).admitted
    assert not p.company_action(v,phase='new',subject='P',scope=C,action='post').available
    assert all(s.new_value is None for s in v.catalog_slots if s.kind=='admin_action')
    assert p.compare(v).agents[0].needs_suspension


def test_public_queries_reject_invalid_types_with_typed_internal_errors():
    v = p.validate_comparison(pair())
    operations = (
        lambda: p.execution(v,phase='new',actor='G',bound_human='P',scope=C,requirement=[]),
        lambda: p.execution(v,phase='new',actor='G',bound_human=[],scope=C,requirement=READ),
        lambda: p.company_action(v,phase='new',subject='P',scope=C,action=[]),
        lambda: p.admissions(v,phase='new',subject='P',scope=None),
        lambda: p.signature(v,phase='new',subject=1),
        lambda: p.compare(pair()),
    )
    for operation in operations:
        with pytest.raises(PolicyInputError):
            operation()


def test_feature_metadata_and_provenance_do_not_erase_or_distort_policy_bits():
    from bookflow.hub.permission_catalog import CommandDescriptor
    command = CommandDescriptor('read', 'company', 'ledger.read', 'member', (), True, 'feature-a', False, False, None, None, None, None)
    cat = replace(catalog(),commands=(command,))
    changed = replace(cat,version='new-feature',commands=(replace(command,feature='feature-b',available=False),))
    assert not transition(pair(old_catalog=cat,new_catalog=changed)).needs_suspension
    base = {(i,O):member() for i in ('G','P','Q','R')}
    new = dict(base)
    new['P',C] = member(grants=('ledger.read',))
    v = p.validate_comparison(pair(old_policies=base,new_policies=new))
    assert atom(v).grant_sources == (C,) and atom(v,phase='old').grant_sources == ()
    assert p.signature(v,phase='old',subject='P') == p.signature(v,phase='new',subject='P')
    assert not p.compare(v).agents[0].needs_suspension
