"""Complete raw/logical observations; private supplied policy, never activation."""
from dataclasses import asdict, replace
from pathlib import Path, PureWindowsPath
import json
import pytest
from bookflow.hub import permission_snapshot as s, permission_catalog as c, permission_policy as a
from bookflow.storage.engine import open_database
from tests.permission_storage_support import create_hub, snapshot
from tests.test_permission_snapshots import BUNDLE, SuppliedVisibility, install_fixture_policy

AT = '2026-09-07T05:00:00Z'
MAX = 9223372036854775807


def get(rows, identifier):
    return next(x for x in rows if getattr(x, 'id', getattr(x, 'agent_user_id', None)) == identifier)


def load(path):
    with open_database(path, writable=False) as db:
        return s.load_root(db, catalog=BUNDLE)


def make_root(path, *, retired_fixture=True):
    create_hub(path, 'hub0012', suspended=False)
    with open_database(path, writable=True) as db:
        install_fixture_policy(db.raw)
        if retired_fixture:
            db.raw.execute("UPDATE memberships SET scope_id='E' WHERE id IN ('N','P')")
            db.raw.execute("UPDATE memberships SET role='readonly' WHERE id='P'")
            db.raw.execute("UPDATE agent_principals SET revoked_at=NULL WHERE principal_user_id='Q'")
            db.raw.execute("INSERT INTO companies(id,organization_id,display_name,name_key,path,pending_path,legal_name,home_currency,schema_revision,is_demo,version,created_at,created_by,created_via,updated_at,updated_by,updated_via) SELECT 'F','Z','F','F','F','trash/kept','F',home_currency,schema_revision,is_demo,version,created_at,created_by,created_via,updated_at,updated_by,updated_via FROM companies WHERE id='E'")
            db.raw.execute("INSERT INTO memberships(id,user_id,scope_type,scope_id,role,granted_by,granted_at,revoked_at,version) VALUES('X','H','company','F','standard','H','2026-01-01','2026-01-03',1)")
    return load(path)


@pytest.fixture(scope='module')
def old(tmp_path_factory):
    return make_root(tmp_path_factory.mktemp('retirement-old')/'hub.db')


class GovernedVisibility(SuppliedVisibility):
    """Owned complete policy: actual live facts and explicit retired-false rule."""
    def facts(self, root, scopes, subjects):
        supplied = super().facts(root, scopes, subjects)
        # Independent lexical fixture oracle, not the production classifier/observation.
        retired_orgs = {x.id for x in root.organizations if x.pending_path and x.pending_path.replace('\\', '/').split('/')[0] == 'trash'}
        retired_companies = {x.id for x in root.companies if x.organization_id in retired_orgs or x.pending_path and x.pending_path.replace('\\', '/').split('/')[0] == 'trash'}
        return replace(supplied, rows=tuple(replace(v, visible=False) if v.scope.id in (
            retired_companies if v.scope.kind == 'company' else retired_orgs if v.scope.kind in ('organization','future_company') else set()) else v for v in supplied.rows))


def derive(old, scopes=s.ScopeRows(), changes=s.ProposalRows(), **kw):
    return s.derive_scope_proposal(old, old_catalog=BUNDLE, new_catalog=kw.pop('new_catalog',BUNDLE),
        scopes=scopes, changes=changes, generation=kw.pop('generation',old.stamp.generation), **kw)


def observe(old, new, admin=False, provider=None):
    return s.observe_pair(old, new, old_catalog=BUNDLE, new_catalog=BUNDLE,
                          visibility=provider if provider is not None else GovernedVisibility(admin))


def retirement(old, pending='trash/retired-O'):
    return s.ScopeRows(organizations=(replace(get(old.organizations,'O'),pending_path=pending),))


def save(path, **values):
    path.write_text(json.dumps({k:asdict(v) for k,v in values.items()},indent=2))


@pytest.mark.parametrize('admin', [False, True])
def test_complete_t1_raw_and_logical_union_and_full_admission(old, admin, tmp_path):
    proposed=derive(old,retirement(old)).root
    pair=observe(old,proposed,admin)
    assert proposed.keys==old.keys
    assert old.keys.organizations==('O','Z')
    assert old.keys.companies==( ('C','O'),('D','O'),('E','Z'),('F','Z'))
    assert old.keys.memberships==( ('M','H','organization','O'),('N','Q','company','E'),('P','G','company','E'),('R','I','company','E'),('X','H','company','F'))
    for field in ('users','companies','memberships','assignments','authorities','role_defaults','catalog'):
        assert getattr(proposed,field)==getattr(old,field)
    expected={('hub','root'):(True,True,None,None,'none'),
        ('organization','O'):(True,False,None,'trash/retired-O','own'),
        ('future_company','O'):(True,False,None,'trash/retired-O','own'),
        ('organization','Z'):(True,True,None,None,'none'),('future_company','Z'):(True,True,None,None,'none'),
        ('company','C'):(True,False,'O',None,'parent'),('company','D'):(True,False,'O',None,'parent'),
        ('company','E'):(True,True,'Z',None,'none'),('company','F'):(True,False,'Z','trash/kept','own')}
    assert {(x.scope.kind,x.scope.id):(x.raw_present,x.logical_present,x.raw_parent,x.pending_path,x.retirement) for x in pair.new_observation.scopes}==expected
    assert pair.new_observation.live_organizations==('Z',)
    assert pair.new_observation.live_companies==( ('E','Z'),)
    assert pair.old_observation.live_organizations==('O','Z')
    assert pair.old_observation.live_companies==( ('C','O'),('D','O'),('E','Z'))
    assert {(x.subject,x.scope.kind,x.scope.id) for x in pair.new_observation.memberships}=={
        (who,kind,scope) for who in ('G','H','I','J','Q','S') for kind,scope in
        [('organization','O'),('organization','Z'),('company','C'),('company','D'),('company','E'),('company','F')]}
    assert {(x.subject,x.scope.id) for x in pair.new_observation.memberships if x.value is not None}=={('G','E'),('Q','E')}
    comparison=pair.snapshot.comparison
    assert {(x.kind,x.id) for x in comparison.scopes}==set(expected)-{('company','F')}
    result=a.compare(comparison)
    assert result.agents[0].agent=='G' and result.agents[0].needs_suspension
    assert a.Reason.principal_authority_loss in result.agents[0].reasons
    assert not result.agents[1].needs_suspension
    for signature in result.signatures_new:
        for entry in signature.scopes:
            if entry.scope.id in ('O','C','D'):
                assert not entry.present and not entry.effective_visible and entry.membership_role is None
                assert not any(value for _,value in entry.company_bits)
                assert not any(value for _,value in entry.admin_bits)
    assert s.assemble_pair(old,proposed,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=GovernedVisibility(admin))==pair.snapshot
    save(tmp_path/'t1-full-observation.json',old=old,prepared=proposed,observation=pair,result=result)


def test_t1_final_t2_cleanup_replacement_independent_sql(tmp_path):
    path=tmp_path/'hub.db';old=make_root(path)
    scopes=retirement(old)
    initiating=derive(old,scopes)
    g=replace(get(old.authorities,'G'),epoch=10,version=2,suspended_at=AT,suspension_reason='principal_authority_loss',fresh_context_required=True)
    final=derive(old,scopes,s.ProposalRows(authorities=(g,)),generation=2)
    assert initiating.base_stamp==final.base_stamp==old.stamp
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE organizations SET pending_path='trash/retired-O' WHERE id='O'")
        db.raw.execute("UPDATE agent_authority SET epoch=10,version=2,suspended_at=?,suspension_reason='principal_authority_loss',fresh_context_required=1 WHERE agent_user_id='G'",(AT,))
        db.raw.execute('UPDATE permission_state SET generation=2')
    retired=load(path)
    assert retired==final.root
    cleanup=s.ScopeRows(remove_organizations=('O',),remove_companies=('D','C'),remove_memberships=('M',))
    cleaned=derive(retired,cleanup).root
    transition=a.compare(observe(retired,cleaned).snapshot.comparison)
    assert [(x.agent,x.needs_suspension,x.remains_suspended) for x in transition.agents]==[('G',False,True),('J',False,False)]
    # Equal active principals after explicit removal of their remaining live scope membership.
    # Use the cleanup alone first, with complete SQL equality and raw removed-key proof.
    with open_database(path,writable=True) as db:
        db.raw.execute("DELETE FROM memberships WHERE id='M'")
        db.raw.execute("DELETE FROM companies WHERE id IN ('C','D')")
        db.raw.execute("DELETE FROM organizations WHERE id='O'")
    assert load(path)==cleaned
    assert cleaned.keys.organizations==('Z',) and cleaned.keys.companies==( ('E','Z'),('F','Z'))
    assert cleaned.keys.memberships==old.keys.memberships[1:]
    save(tmp_path/'cleanup-reload.json',old=retired,prepared=cleaned,observed=load(path),transitions=transition)
    # Make the future replacement witness's two humans equal without changing bindings.
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE memberships SET revoked_at=? WHERE id='N'",(AT,))
        db.raw.execute("UPDATE users SET hub_admin=0 WHERE id='H'")
    base=load(path)
    member=s.MembershipRow('NEW','H','company','V','owner',None,None,'H',AT,None,1,None,None,None)
    addition=s.ScopeRows(organizations=(s.OrganizationRow('U',1,None),),companies=(s.CompanyRow('V','U',1,None),))
    result=derive(base,addition,s.ProposalRows(memberships=(member,)),generation=3).root
    change=a.compare(observe(base,result).snapshot.comparison)
    assert change.agents[0].needs_suspension and not change.agents[0].proposed_set_equal
    with open_database(path,writable=True) as db:
        db.raw.execute("INSERT INTO organizations(id,display_name,name_key,path,is_demo,version,created_at,created_by,created_via,updated_at,updated_by,updated_via) SELECT 'U','U','U','U',is_demo,1,created_at,created_by,created_via,updated_at,updated_by,updated_via FROM organizations WHERE id='Z'")
        db.raw.execute("INSERT INTO companies(id,organization_id,display_name,name_key,path,legal_name,home_currency,schema_revision,is_demo,version,created_at,created_by,created_via,updated_at,updated_by,updated_via) SELECT 'V','U','V','V','V','V',home_currency,schema_revision,is_demo,1,created_at,created_by,created_via,updated_at,updated_by,updated_via FROM companies WHERE id='E'")
        db.raw.execute("INSERT INTO memberships(id,user_id,scope_type,scope_id,role,granted_by,granted_at,version) VALUES('NEW','H','company','V','owner','H',?,1)",(AT,))
        db.raw.execute('UPDATE permission_state SET generation=3')
    assert load(path)==result
    save(tmp_path/'replacement-reload.json',old=base,prepared=result,observed=load(path),transitions=change)


WINDOWS_MOVE = str((PureWindowsPath(r'C:\owned')/'organizations'/'Org'/'New Company').relative_to(PureWindowsPath(r'C:\owned')))


@pytest.mark.parametrize('pending,retired', [(None,False),('organizations/Org/New Company',False),(WINDOWS_MOVE,False),
    ('organizations\\Org/New Company',False),('organizations/Org/new company',False),('Trash/old',False),('trashcan/old',False),
    ('trash/old',True),('trash\\old',True),('trash/old\\child',True)])
def test_native_and_posix_paths_preserve_exact_raw_and_ordinary_authority(old,pending,retired,tmp_path):
    root=derive(old,retirement(old,pending)).root
    assert get(root.organizations,'O').pending_path==pending
    pair=observe(old,root)
    observation=next(x for x in pair.new_observation.scopes if x.scope==c.ScopeKey('organization','O'))
    assert observation.logical_present is not retired and observation.pending_path==pending
    assert observation.retirement==('own' if retired else 'none')
    comparison=a.compare(pair.snapshot.comparison)
    if not retired:
        assert comparison.signatures_old==comparison.signatures_new
        assert not any(x.needs_suspension for x in comparison.agents)
    if pending is not None:assert old.stamp.authority_rows_digest!=root.stamp.authority_rows_digest
    save(tmp_path/'path-observation.json',old=old,prepared=root,observation=pair)


@pytest.mark.parametrize('pending', ['', '/', '\\', '/root', r'\root', r'\\host\share', 'C:relative',r'Z:\absolute',
    'x//y','x\\\\y','x/','x\\','./x','x/../y','x\\.\\y','trash','private\0secret'])
def test_invalid_grammar_is_safe_and_never_repaired(old,pending):
    with pytest.raises(s.SnapshotError) as caught:derive(old,retirement(old,pending))
    assert caught.value.args==('invalid_facts','pending_path')
    assert get(old.organizations,'O').pending_path is None


def test_separator_forms_have_distinct_digest_and_same_observation_meaning(old):
    left=derive(old,retirement(old,'trash/old')).root
    right=derive(old,retirement(old,'trash\\old')).root
    assert left.stamp.authority_rows_digest!=right.stamp.authority_rows_digest
    assert a.compare(observe(left,right).snapshot.comparison).signatures_old==a.compare(observe(left,right).snapshot.comparison).signatures_new


def test_own_and_parent_retirement_preserves_child_destination(old):
    company=replace(get(old.companies,'C'),pending_path='trash\\C')
    scopes=replace(retirement(old,'trash\\O'),companies=(company,))
    root=derive(old,scopes).root
    item=next(x for x in observe(old,root).new_observation.scopes if x.scope==c.ScopeKey('company','C'))
    assert item==s.ScopeObservation(c.ScopeKey('company','C'),True,False,'O','trash\\C','own_and_parent')


@pytest.mark.parametrize('edit', ['duplicate_org','duplicate_company','unknown_remove','duplicate_remove','org_overlap','company_overlap',
    'remaining_child','missing_member','extra_member','removed_member_upsert','removed_revoked_upsert','new_parent_missing','parent_change',
    'new_retired_org','new_retired_company','new_child_retired_parent','resurrect_org','rewrite_destination','resurrect_child',
    'add_retired_membership','reactivate_retired_membership','list_scopes','dict_scopes','bad_pending_type','bool_version'])
def test_structural_rejections(old,edit):
    scopes=s.ScopeRows();changes=s.ProposalRows();base=old
    o=get(old.organizations,'O');co=get(old.companies,'C');member=get(old.memberships,'M')
    if edit=='duplicate_org':scopes=s.ScopeRows(organizations=(o,o))
    if edit=='duplicate_company':scopes=s.ScopeRows(companies=(co,co))
    if edit=='unknown_remove':scopes=s.ScopeRows(remove_companies=('private-missing',))
    if edit=='duplicate_remove':scopes=s.ScopeRows(remove_companies=('C','C'))
    if edit=='org_overlap':scopes=s.ScopeRows(organizations=(o,),remove_organizations=('O',))
    if edit=='company_overlap':scopes=s.ScopeRows(companies=(co,),remove_companies=('C',))
    if edit=='remaining_child':scopes=s.ScopeRows(remove_organizations=('O',),remove_memberships=('M',))
    if edit=='missing_member':scopes=s.ScopeRows(remove_companies=('E',))
    if edit=='extra_member':scopes=s.ScopeRows(remove_companies=('C',),remove_memberships=('M',))
    if edit=='removed_member_upsert':
        scopes=s.ScopeRows(remove_organizations=('O',),remove_companies=('C','D'),remove_memberships=('M',))
        changes=s.ProposalRows(memberships=(replace(member,revoked_at=AT),))
    if edit=='removed_revoked_upsert':
        scopes=s.ScopeRows(remove_companies=('F',),remove_memberships=('X',))
        changes=s.ProposalRows(memberships=(get(old.memberships,'X'),))
    if edit=='new_parent_missing':scopes=s.ScopeRows(companies=(s.CompanyRow('NEW','private-missing',1,None),))
    if edit=='parent_change':scopes=s.ScopeRows(companies=(replace(co,organization_id='Z'),))
    if edit=='new_retired_org':scopes=s.ScopeRows(organizations=(s.OrganizationRow('NEW',1,'trash/new'),))
    if edit=='new_retired_company':scopes=s.ScopeRows(companies=(s.CompanyRow('NEW','Z',1,'trash/new'),))
    if edit in ('new_child_retired_parent','resurrect_org','rewrite_destination','resurrect_child','add_retired_membership'):
        base=derive(old,retirement(old)).root
    if edit=='new_child_retired_parent':scopes=s.ScopeRows(companies=(s.CompanyRow('NEW','O',1,None),))
    if edit=='resurrect_org':scopes=s.ScopeRows(organizations=(o,))
    if edit=='rewrite_destination':scopes=retirement(base,'trash/different')
    if edit=='resurrect_child':scopes=s.ScopeRows(companies=(replace(co,pending_path='ordinary/new'),))
    if edit=='add_retired_membership':changes=s.ProposalRows(memberships=(replace(member,id='NEW',user_id='Q'),))
    if edit=='reactivate_retired_membership':changes=s.ProposalRows(memberships=(replace(get(old.memberships,'X'),revoked_at=None),))
    if edit=='list_scopes':scopes=s.ScopeRows(organizations=[o])
    if edit=='dict_scopes':scopes={}
    if edit=='bad_pending_type':scopes=s.ScopeRows(organizations=(replace(o,pending_path=1),))
    if edit=='bool_version':scopes=s.ScopeRows(organizations=(replace(o,version=True),))
    with pytest.raises(s.SnapshotError) as caught:derive(base,scopes,changes)
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('edit', ['missing_retired','true_retired','duplicate_retired','wrong_type_retired','missing_live','no_revision','different_revision'])
def test_full_visibility_domain_validated_before_A_subset(old,edit):
    class Invalid(GovernedVisibility):
        def __init__(self):super().__init__();self.calls=0
        def facts(self,root,scopes,subjects):
            self.calls+=1
            facts=super().facts(root,scopes,subjects)
            target=c.ScopeKey('company','E' if edit=='missing_live' else 'F')
            rows=list(facts.rows);index=next(i for i,x in enumerate(rows) if x.subject=='H' and x.scope==target)
            if edit in ('missing_retired','missing_live'):rows.pop(index)
            if edit=='true_retired':rows[index]=replace(rows[index],visible=True)
            if edit=='wrong_type_retired':rows[index]=replace(rows[index],visible=1)
            if edit=='duplicate_retired':rows.append(rows[index])
            return replace(facts,rows=tuple(rows),policy_revision='' if edit=='no_revision' else str(self.calls) if edit=='different_revision' else facts.policy_revision)
    with pytest.raises(s.SnapshotError):observe(old,old,provider=Invalid())
    with pytest.raises(s.SnapshotError):s.observe_pair(old,old,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=None)


def test_empty_structural_and_ordinary_preserve_retired_rows_and_no_io(old,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('pure proposal attempted IO')
    monkeypatch.setattr(s.sqlite3,'connect',forbidden)
    monkeypatch.setattr(s,'load_root',forbidden)
    monkeypatch.setattr(Path,'exists',forbidden)
    result=derive(old)
    assert result==s.DerivedFacts(old,old.stamp)
    assert s.derive_proposal(old,old_catalog=BUNDLE,new_catalog=BUNDLE,changes=s.ProposalRows(),generation=old.stamp.generation)==result
    assert {x.agent for x in a.compare(observe(old,old).snapshot.comparison).agents}=={'G','J'}


def test_same_snapshot_pending_path_and_independent_keys(tmp_path,monkeypatch):
    path=tmp_path/'hub.db';make_root(path)
    original=s._read_rows;changed=False
    def interleave(raw,table,kind):
        nonlocal changed
        if not changed:
            changed=True
            with open_database(path,writable=True) as db:
                db.raw.execute('BEGIN IMMEDIATE')
                db.raw.execute("UPDATE organizations SET pending_path='trash\\O' WHERE id='O'")
                db.raw.execute('COMMIT')
        return original(raw,table,kind)
    monkeypatch.setattr(s,'_read_rows',interleave)
    before=load(path);after=load(path)
    assert before.keys==after.keys
    assert get(before.organizations,'O').pending_path is None
    assert get(after.organizations,'O').pending_path=='trash\\O'
    assert before.stamp.authority_rows_digest!=after.stamp.authority_rows_digest
    assert not next(x for x in observe(before,after).new_observation.scopes if x.scope==c.ScopeKey('company','C')).logical_present
    # Omitting a raw retired company cannot provide its own incomplete source keys.
    monkeypatch.setattr(s,'_read_rows',lambda raw,table,kind:tuple(x for x in original(raw,table,kind) if table!='companies' or x.id!='F'))
    with pytest.raises(s.SnapshotError,match='source_incomplete'):load(path)


def test_raw_read_preservation_tamper_legacy_and_no_old_field_guess(tmp_path):
    path=tmp_path/'hub.db';old=make_root(path)
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw)
        assert s.load_root(db,catalog=BUNDLE)==old
        derive(old,retirement(old))
        observe(old,old)
        assert snapshot(db.raw)==before
    bad=replace(old,organizations=(replace(old.organizations[0],pending_path='trash/forged'),*old.organizations[1:]))
    with pytest.raises(s.SnapshotError,match='source_incomplete'):derive(bad)
    with pytest.raises(s.SnapshotError,match='source_incomplete'):derive(replace(old,companies=old.companies[:-1]))
    with pytest.raises(s.SnapshotError):derive(replace(old,stamp=replace(old.stamp,mode='legacy',catalog_sha256=None)))
    with pytest.raises(TypeError):s.OrganizationRow('O',1)
    with pytest.raises(TypeError):s.CompanyRow('C','O',1)
    encoded=asdict(old);del encoded['organizations'][0]['pending_path']
    with pytest.raises(s.SnapshotError):s._decode(encoded,s.RootFacts,'root')
    with open_database(path,writable=True) as db:db.raw.execute("UPDATE organizations SET pending_path='C:unsupported' WHERE id='O'")
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw)
        with pytest.raises(s.SnapshotError):s.load_root(db,catalog=BUNDLE)
        assert snapshot(db.raw)==before


# Frozen e2b1 assembler algorithm: the pre-retirement ordinary-root oracle.
# Only namespace qualification and the signature change; no new observation code.
def legacy_pair(old, proposed, visibility):
    old_policy = s._policy
    old_catalog = new_catalog = BUNDLE
    """Strict policy-to-policy assembly. Legacy activation needs its later owner."""
    for root,bundle in ((old,old_catalog),(proposed,new_catalog)):
        s._validated_root(root, bundle)
    if visibility is None or not callable(getattr(visibility,'facts',None)):
        s._fail('visibility_unresolved','visibility')
    orgs = sorted(set(old.keys.organizations)|set(proposed.keys.organizations))
    companies = sorted({x[0] for root in (old,proposed) for x in root.keys.companies})
    subjects = tuple(sorted(set(old.keys.users)|set(proposed.keys.users)))
    agents = sorted({x.id for root in (old,proposed) for x in root.users if x.kind=='agent'})
    scopes = tuple(sorted((c.ScopeKey('hub','root'), *(c.ScopeKey('organization',x) for x in orgs), *(c.ScopeKey('future_company',x) for x in orgs), *(c.ScopeKey('company',x) for x in companies)),key=c._scope_key))
    manifests=[]; phases=[]; revisions=[]
    for root,bundle in ((old,old_catalog),(proposed,new_catalog)):
        supplied = visibility.facts(root,scopes,subjects)
        if type(supplied) is not s.VisibilityFacts or type(supplied.policy_revision) is not str or not supplied.policy_revision or type(supplied.rows) is not tuple:
            s._fail('visibility_unresolved','visibility')
        revisions.append(supplied.policy_revision)
        manifests.append(a.PhaseManifest(root.keys.organizations,root.keys.companies,tuple((x.id,x.kind) for x in root.users),c.catalog_manifest(root.catalog,bundle.exclusions)))
        uu={x.id:x for x in root.users}; cc=dict(root.keys.companies); mm={(x.user_id,c.ScopeKey(x.scope_type,x.scope_id)):x for x in root.memberships if x.revoked_at is None}
        aa={x.agent_user_id:x for x in root.authorities}
        phases.append(a.Phase(root.catalog,
            tuple(a.OrganizationSlot(x,x in root.keys.organizations) for x in orgs),
            tuple(a.CompanySlot(x,x in cc,cc.get(x)) for x in companies),
            tuple(a.SubjectSlot(x,a.Subject(uu[x].kind,uu[x].active,uu[x].hub_admin) if x in uu else None) for x in subjects),
            tuple(a.MembershipSlot(x,s,a.Membership(mm[x,s].role,old_policy(mm[x,s],root.catalog)) if (x,s) in mm else None) for x in subjects for s in scopes if s.kind in ('organization','company')),
            supplied.rows,
            tuple(a.AgentSlot(x,a.AgentState(aa[x].suspended_at is not None,tuple(sorted(y.principal_user_id for y in root.assignments if y.agent_user_id==x and y.revoked_at is None and uu[y.principal_user_id].active))) if x in aa else None) for x in agents)))
    if revisions[0]!=revisions[1]:
        s._fail('visibility_unresolved','visibility')
    try:
        comparison=a.validate_comparison(a.ComparisonInput(a.Expected(*manifests),*phases))
    except c.PolicyInputError as exc:
        s._fail('invalid_comparison',exc.field)
    return s.SnapshotPair(old,proposed,comparison,revisions[0])


@pytest.mark.parametrize('admin', [False, True])
def test_full_ordinary_pair_equals_pinned_e2b1_assembler(tmp_path,admin):
    path=tmp_path/'hub.db';old=make_root(path,retired_fixture=False)
    q=replace(get(old.users,'Q'),active=False,version=8)
    new=s.derive_proposal(old,old_catalog=BUNDLE,new_catalog=BUNDLE,
        changes=s.ProposalRows(users=(q,)),generation=2).root
    current=s.assemble_pair(old,new,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=SuppliedVisibility(admin))
    baseline=legacy_pair(old,new,SuppliedVisibility(admin))
    assert asdict(current)==asdict(baseline)
    assert a.compare(current.comparison)==a.compare(baseline.comparison)
    save(tmp_path/'full-e2b1-comparison.json',current=current,baseline=baseline)


def test_compound_cleanup_create_cascade_and_order(old):
    # One T2 construction, including revoked membership X removal and new scope membership.
    company=s.CompanyRow('V','Z',1,None)
    member=s.MembershipRow('NEW','H','company','V','owner',None,None,'H',AT,None,1,None,None,None)
    scopes=s.ScopeRows(companies=(company,),remove_companies=('F','E'),remove_memberships=('X','R','P','N'))
    result=derive(old,scopes,s.ProposalRows(memberships=(member,))).root
    assert result.keys.companies==( ('C','O'),('D','O'),('V','Z'))
    assert result.keys.memberships==( ('M','H','organization','O'),('NEW','H','company','V'))
    assert result.memberships==(get(old.memberships,'M'),member)
    assert result.users==old.users and result.assignments==old.assignments and result.authorities==old.authorities
    assert derive(old,replace(scopes,remove_companies=('E','F'),remove_memberships=('N','P','R','X')),s.ProposalRows(memberships=(member,))).root==result
    # Current ordinary helper remains unable to materialize a new scope's membership.
    with pytest.raises(s.SnapshotError):
        s.derive_proposal(old,old_catalog=BUNDLE,new_catalog=BUNDLE,changes=s.ProposalRows(memberships=(member,)),generation=1)


@pytest.mark.parametrize('pending', [WINDOWS_MOVE, 'organizations\\Org/New Company'])
def test_company_native_move_reload_and_completion_leave_authority_equal(tmp_path,pending):
    path=tmp_path/'hub.db';old=make_root(path)
    company=replace(get(old.companies,'C'),pending_path=pending)
    prepared=derive(old,s.ScopeRows(companies=(company,))).root
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE companies SET pending_path=? WHERE id='C'",(pending,))
    moved=load(path)
    assert moved==prepared and get(moved.companies,'C').pending_path==pending
    finished=derive(moved,s.ScopeRows(companies=(replace(company,pending_path=None,version=8),))).root
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE companies SET path=?,pending_path=NULL,version=8 WHERE id='C'",(pending,))
    assert load(path)==finished
    pair=observe(old,finished)
    result=a.compare(pair.snapshot.comparison)
    assert result.signatures_old==result.signatures_new and not any(x.needs_suspension for x in result.agents)
    assert get(finished.companies,'C').pending_path is None
    assert finished.stamp.authority_rows_digest!=moved.stamp.authority_rows_digest!=old.stamp.authority_rows_digest
    save(tmp_path/'company-move-reload.json',old=old,pending=prepared,observed=finished,comparison=pair)


def test_final_expected_root_detects_additional_sql_effect(tmp_path):
    path=tmp_path/'hub.db';old=make_root(path)
    expected=derive(old,retirement(old)).root
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE organizations SET pending_path='trash/retired-O' WHERE id='O'")
        db.raw.execute("UPDATE memberships SET version=version+1 WHERE id='R'")
    observed=load(path)
    assert expected.keys==observed.keys
    assert expected!=observed and expected.stamp.authority_rows_digest!=observed.stamp.authority_rows_digest
    assert get(expected.memberships,'R').version==1 and get(observed.memberships,'R').version==2
    save(tmp_path/'unexpected-effect-detected.json',expected=expected,observed=observed)


def test_nonempty_structural_proposal_does_not_open_or_probe(old,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('nonempty proposal performed IO')
    monkeypatch.setattr(s.sqlite3,'connect',forbidden)
    monkeypatch.setattr(s,'load_root',forbidden)
    monkeypatch.setattr(Path,'exists',forbidden)
    monkeypatch.setattr(Path,'resolve',forbidden)
    retired=derive(old,retirement(old)).root
    assert retired.keys==old.keys
    company=s.CompanyRow('V','Z',1,None)
    result=derive(old,s.ScopeRows(companies=(company,),remove_companies=('F',),remove_memberships=('X',))).root
    assert result.keys.companies==( ('C','O'),('D','O'),('E','Z'),('V','Z'))


@pytest.mark.parametrize('kind', ['wrong_envelope','nonstring_revision','empty_revision','list_rows'])
def test_visibility_envelope_retains_unresolved_category(old,kind):
    class BadEnvelope(GovernedVisibility):
        def facts(self,root,scopes,subjects):
            valid=super().facts(root,scopes,subjects)
            if kind=='wrong_envelope':return asdict(valid)
            if kind=='nonstring_revision':return replace(valid,policy_revision=1)
            if kind=='empty_revision':return replace(valid,policy_revision='')
            return replace(valid,rows=list(valid.rows))
    with pytest.raises(s.SnapshotError) as caught:observe(old,old,provider=BadEnvelope())
    assert caught.value.args==('visibility_unresolved','visibility')


def test_visibility_envelope_does_not_reclassify_nested_type_failure(old):
    class BadNested(GovernedVisibility):
        def facts(self,root,scopes,subjects):
            valid=super().facts(root,scopes,subjects)
            return replace(valid,rows=(replace(valid.rows[0],visible=1),*valid.rows[1:]))
    with pytest.raises(s.SnapshotError) as caught:observe(old,old,provider=BadNested())
    assert caught.value.args==('invalid_type','visibility')
