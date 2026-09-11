"""Pure proposal construction, handwritten values and independent SQL observations."""
from dataclasses import asdict, fields, replace
import json
import pytest
from bookflow.hub import permission_snapshot as s, permission_catalog as c, permission_policy as a
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS
from tests.permission_storage_support import create_hub, snapshot
from tests.test_permission_snapshots import BUNDLE, SuppliedVisibility, install_fixture_policy, without_capability

MAX = 9223372036854775807
AT = '2026-09-07T05:00:00Z'  # Fixed synthetic operation time, not a runtime timestamp.


def owned_root(path, *, suspended=True):
    create_hub(path, HEADS['hub'], suspended=suspended)
    with open_database(path, writable=True) as db:
        install_fixture_policy(db.raw)
    with open_database(path, writable=False) as db:
        return s.load_root(db, catalog=BUNDLE)


@pytest.fixture(scope='module')
def old(tmp_path_factory):
    return owned_root(tmp_path_factory.mktemp('proposal-old')/'hub.db')


def derive(old, changes=s.ProposalRows(), **kwargs):
    return s.derive_proposal(old, old_catalog=BUNDLE, new_catalog=BUNDLE,
                             changes=changes, generation=kwargs.pop('generation',old.stamp.generation), **kwargs)


def row(rows, key):
    return next(x for x in rows if getattr(x,'id',getattr(x,'agent_user_id',None))==key)


def test_empty_patch_retains_complete_loaded_root_without_io(old,monkeypatch):
    def forbidden(*args, **kwargs):raise AssertionError('pure construction attempted IO or dense assembly')
    monkeypatch.setattr(s, 'load_root', forbidden)
    monkeypatch.setattr(s, 'assemble_pair', forbidden)
    monkeypatch.setattr(s.sqlite3, 'connect', forbidden)
    result=derive(old)
    assert result==s.DerivedFacts(old,old.stamp)
    assert result.provenance=='derived_from_loaded_old'
    assert old.keys.users==('G','H','I','J','Q','S')
    assert old.keys.organizations==('O','Z') and old.keys.companies==( ('C','O'),('D','O'),('E','Z'))
    assert not row(old.users,'I').active and not row(old.users,'J').active
    assert row(old.memberships,'R').revoked_at=='2026-01-03'
    assert c.DefaultEntry('standard',c.Requirement('ledger.post','standard')) not in result.root.role_defaults
    assert result.root.role_defaults != BUNDLE.descriptor.defaults


def test_upserts_handwritten_keys_rows_and_complete_sql_reload(tmp_path):
    path=tmp_path/'hub.db';old=owned_root(path,suspended=False)
    h=replace(row(old.users,'H'), active=False, version=8)
    n=replace(row(old.memberships,'N'), role='readonly', version=2, updated_at=AT, updated_by='H', updated_via='cli')
    b=s.MembershipRow('B','Q','company','D','standard',None,'[]','H',AT,None,1,None,None,None)
    gh=s.AssignmentRow('G','H','H','2026-01-01',AT)
    jq=s.AssignmentRow('J','Q','H',AT,None)
    patch=s.ProposalRows(users=(h,),memberships=(n,b),assignments=(jq,gh))
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw);statements=[];db.raw.set_trace_callback(statements.append)
        derived=derive(old,patch,generation=2)
        db.raw.set_trace_callback(None)
        assert statements==[] and snapshot(db.raw)==before
    proposed=derived.root
    assert proposed.keys.memberships==( ('B','Q','company','D'),('M','H','organization','O'),('N','Q','company','C'),('P','G','company','C'),('R','I','company','E'))
    assert proposed.keys.assignments==( ('G','H'),('G','I'),('G','Q'),('J','Q'))
    assert proposed.keys.users==old.keys.users and proposed.keys.authorities==old.keys.authorities
    assert proposed.users==tuple(h if x.id=='H' else x for x in old.users)
    assert proposed.memberships==(b,row(old.memberships,'M'),n,row(old.memberships,'P'),row(old.memberships,'R'))
    assert proposed.assignments==(gh,old.assignments[1],old.assignments[2],jq)
    assert proposed.authorities==old.authorities and proposed.organizations==old.organizations and proposed.companies==old.companies
    assert proposed.catalog==old.catalog and proposed.role_defaults==old.role_defaults
    assert proposed.stamp.generation==2 and derived.base_stamp==old.stamp
    shuffled=derive(old,replace(patch,memberships=(b,n),assignments=(gh,jq)),generation=2)
    assert shuffled==derived
    pair=s.assemble_pair(old,proposed,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=SuppliedVisibility())
    assert len(pair.comparison.old.memberships)==len(pair.comparison.new.memberships)==30
    assert next(x.value for x in pair.comparison.old.agents if x.id=='G').eligible_humans==('H',)
    assert next(x.value for x in pair.comparison.new.agents if x.id=='G').eligible_humans==()
    assert a.signature(pair.comparison,phase='old',subject='H') != a.signature(pair.comparison,phase='new',subject='H')
    # Independently written fixture SQL, never a B2 service or SQL generated from proposed rows.
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE users SET active=0,version=8 WHERE id='H'")
        db.raw.execute("UPDATE memberships SET role='readonly',version=2,updated_at=?,updated_by='H',updated_via='cli' WHERE id='N'",(AT,))
        db.raw.execute("INSERT INTO memberships(id,user_id,scope_type,scope_id,role,grants,denies,granted_by,granted_at,revoked_at,version) VALUES('B','Q','company','D','standard',NULL,'[]','H',?,NULL,1)",(AT,))
        db.raw.execute("UPDATE agent_principals SET revoked_at=? WHERE agent_user_id='G' AND principal_user_id='H'",(AT,))
        db.raw.execute("INSERT INTO agent_principals VALUES('J','Q','H',?,NULL)",(AT,))
        db.raw.execute('UPDATE permission_state SET generation=2')
    with open_database(path,writable=False) as db:
        observed=s.load_root(db,catalog=BUNDLE)
    assert observed==proposed  # Full typed rows, keys, canonical catalog AND stamps.
    (tmp_path/'complete-proposal-observation.json').write_text(json.dumps({'prepared':asdict(proposed),'observed':asdict(observed)},indent=2))


@pytest.mark.parametrize('kind', ['users','memberships','assignments','authorities'])
def test_duplicate_patch_identity_rejected(old,kind):
    first=getattr(old,kind)[0]
    with pytest.raises(s.SnapshotError):derive(old,s.ProposalRows(**{kind:(first,first)}))


@pytest.mark.parametrize('field,value', [('kind','agent'),('hub_admin',True),('owner_user_id','private-owner'),('id','private-new'),('active',1),('version',True),('version',0),('version',MAX+1)])
def test_user_identity_and_primitive_constraints(old,field,value):
    patch=replace(row(old.users,'Q'),**{field:value})
    with pytest.raises(s.SnapshotError) as caught:derive(old,s.ProposalRows(users=(patch,)))
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('field,value', [('user_id','H'),('scope_id','D'),('scope_type','organization'),('role','private-invalid'),('grants','["private-secret"]'),('version',False),('updated_at',123)])
def test_membership_existing_identity_and_policy_constraints(old,field,value):
    patch=replace(row(old.memberships,'N'),**{field:value})
    with pytest.raises(s.SnapshotError) as caught:derive(old,s.ProposalRows(memberships=(patch,)))
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('change', ['logical_collision','missing_user','missing_scope','revoked_orphan','wrong_assignment_kind','missing_principal','unknown_authority','bool_epoch','bool_flag','wrong_row_class','list_rows','dict_changes','list_defaults','wrong_default_class','duplicate_defaults'])
def test_invalid_upserts_shapes_and_defaults(old,change):
    patch=s.ProposalRows();kwargs={};n=row(old.memberships,'N');g=row(old.authorities,'G')
    if change=='logical_collision':patch=s.ProposalRows(memberships=(replace(n,id='NEW'),))
    if change=='missing_user':patch=s.ProposalRows(memberships=(replace(n,id='NEW',user_id='private-missing'),))
    if change=='missing_scope':patch=s.ProposalRows(memberships=(replace(n,id='NEW',scope_id='private-missing'),))
    if change=='revoked_orphan':patch=s.ProposalRows(memberships=(replace(n,id='NEW',scope_id='private-missing',revoked_at=AT),))
    if change=='wrong_assignment_kind':patch=s.ProposalRows(assignments=(s.AssignmentRow('G','J','H',AT,None),))
    if change=='missing_principal':patch=s.ProposalRows(assignments=(s.AssignmentRow('G','private-missing','H',AT,None),))
    if change=='unknown_authority':patch=s.ProposalRows(authorities=(replace(g,agent_user_id='private-missing'),))
    if change=='bool_epoch':patch=s.ProposalRows(authorities=(replace(g,epoch=True),))
    if change=='bool_flag':patch=s.ProposalRows(authorities=(replace(g,fresh_context_required=1),))
    if change=='wrong_row_class':patch=s.ProposalRows(users=(asdict(old.users[0]),))
    if change=='list_rows':patch=s.ProposalRows(users=list(old.users))
    if change=='dict_changes':patch={}
    if change=='list_defaults':kwargs['full_defaults']=list(old.role_defaults)
    if change=='wrong_default_class':kwargs['full_defaults']=(asdict(old.role_defaults[0]),)
    if change=='duplicate_defaults':kwargs['full_defaults']=(old.role_defaults[0],)*2
    with pytest.raises(s.SnapshotError) as caught:derive(old,patch,**kwargs)
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('edit', ['stamp','bundle','catalog','keys','rows','nested_bool','list_root','mode','revision','generation_type'])
def test_invalid_old_anchor_rejected_without_provenance_authentication_claim(old,edit):
    if edit=='stamp':old=replace(old,stamp=replace(old.stamp,authority_rows_digest='private-digest'))
    if edit=='bundle':old=replace(old,stamp=replace(old.stamp,bundle_digest='private-digest'))
    if edit=='catalog':old=replace(old,catalog=replace(old.catalog,version='private-version'))
    if edit=='keys':old=replace(old,keys=replace(old.keys,users=old.keys.users[:-1]))
    if edit=='rows':old=replace(old,users=old.users[:-1])
    if edit=='nested_bool':old=replace(old,users=(replace(old.users[0],active=1),*old.users[1:]))
    if edit=='list_root':old=replace(old,users=list(old.users))
    if edit=='mode':old=replace(old,stamp=replace(old.stamp,mode='legacy',catalog_sha256=None))
    if edit=='revision':old=replace(old,stamp=replace(old.stamp,hub_revision='hub0011'))
    if edit=='generation_type':old=replace(old,stamp=replace(old.stamp,generation=True))
    with pytest.raises(s.SnapshotError) as caught:derive(old)
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('value', [True,0,-1,3,MAX+1,'2'])
def test_generation_only_old_or_successor(old,value):
    with pytest.raises(s.SnapshotError):derive(old,generation=value)


def test_maximum_generation_noop_and_forbidden_successor(tmp_path):
    path=tmp_path/'hub.db';owned_root(path)
    with open_database(path,writable=True) as db:db.raw.execute('UPDATE permission_state SET generation=?',(MAX,))
    with open_database(path,writable=False) as db:old=s.load_root(db,catalog=BUNDLE)
    assert derive(old).root==old
    with pytest.raises(s.SnapshotError):derive(old,generation=MAX+1)


@pytest.mark.parametrize('defaults', [(),(c.DefaultEntry('readonly',c.Requirement('ledger.read','member')),c.DefaultEntry('standard',c.Requirement('ledger.read','member')))])
def test_new_bundle_actual_defaults_complete_sql_observation(tmp_path,defaults):
    path=tmp_path/'hub.db';old=owned_root(path)
    descriptor=replace(without_capability(BUNDLE.descriptor,'transaction.payment.delete'),version='owned-new-catalog')
    bundle=replace(BUNDLE,descriptor=descriptor,source_commit='a'*40)
    result=s.derive_proposal(old,old_catalog=BUNDLE,new_catalog=bundle,changes=s.ProposalRows(),generation=2,full_defaults=tuple(reversed(defaults)))
    expected_catalog=replace(descriptor,defaults=defaults)
    assert s.encode_catalog(result.root.catalog)==s.encode_catalog(expected_catalog)
    assert result.root.catalog.defaults==result.root.role_defaults==defaults
    assert result.root.keys.defaults==tuple(sorted((d.role,'ledger.read','member') for d in defaults))
    for name in ('users','organizations','companies','memberships','assignments','authorities'):
        assert getattr(result.root,name)==getattr(old,name)
    pair=s.assemble_pair(old,result.root,old_catalog=BUNDLE,new_catalog=bundle,visibility=SuppliedVisibility())
    assert any(x.kind=='capability' and x.key==('transaction.payment.delete',) and x.new_value is None for x in pair.comparison.catalog_slots)
    with open_database(path,writable=True) as db:
        db.raw.execute('DELETE FROM role_capabilities')
        # Explicit expected fixture values, not values extracted from the result.
        if defaults:
            db.raw.execute("INSERT INTO role_capabilities VALUES ('readonly','ledger.read','member')")
            db.raw.execute("INSERT INTO role_capabilities VALUES ('standard','ledger.read','member')")
        install_fixture_policy(db.raw,bundle)
        db.raw.execute('UPDATE permission_state SET generation=2')
    with open_database(path,writable=False) as db:observed=s.load_root(db,catalog=bundle)
    assert result.root==observed
    assert s.decode_catalog(s.encode_catalog(result.root.catalog),version='owned-new-catalog',sha256=result.root.stamp.catalog_sha256)==observed.catalog
    (tmp_path/'catalog-observation.json').write_text(json.dumps({'expected_defaults': [asdict(x) for x in defaults],'prepared':asdict(result.root),'observed':asdict(observed)},indent=2))


def test_removed_capability_referenced_by_revoked_policy_is_not_erased(tmp_path):
    path=tmp_path/'hub.db';owned_root(path)
    with open_database(path,writable=True) as db:db.raw.execute("UPDATE memberships SET denies='[\"transaction.payment.delete\"]' WHERE id='R'")
    with open_database(path,writable=False) as db:old=s.load_root(db,catalog=BUNDLE)
    descriptor=without_capability(BUNDLE.descriptor,'transaction.payment.delete')
    with pytest.raises(s.SnapshotError,match='legacy_policy_invalid'):
        s.derive_proposal(old,old_catalog=BUNDLE,new_catalog=replace(BUNDLE,descriptor=descriptor),changes=s.ProposalRows(),generation=2)
    assert row(old.memberships,'R').denies=='["transaction.payment.delete"]'


def test_initiating_and_final_share_loaded_anchor_and_do_not_compute_effects(tmp_path):
    path=tmp_path/'hub.db';old=owned_root(path,suspended=False)
    removed=s.AssignmentRow('G','H','H','2026-01-01',AT)
    initiating=derive(old,s.ProposalRows(assignments=(removed,)))
    assert initiating.root.authorities==old.authorities
    pair=s.assemble_pair(old,initiating.root,old_catalog=BUNDLE,new_catalog=BUNDLE,visibility=SuppliedVisibility())
    assert next(x for x in a.compare(pair.comparison).agents if x.agent=='G').needs_suspension
    g=s.AuthorityRow('G',10,AT,'binding_loss',2,AT,'H','cli',None,None,None,None,True)
    final=derive(old,s.ProposalRows(assignments=(removed,),authorities=(g,)),generation=2)
    assert initiating.base_stamp==final.base_stamp==old.stamp
    assert final.root.assignments==initiating.root.assignments
    assert final.root.authorities==(g,row(old.authorities,'J'))
    assert final.root.stamp.generation==2 and initiating.root.stamp.generation==1
    # Independently materialize only the fixture's prepared authority facts.
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE agent_principals SET revoked_at=? WHERE agent_user_id='G' AND principal_user_id='H'",(AT,))
        db.raw.execute("UPDATE agent_authority SET epoch=10,suspended_at=?,suspension_reason='binding_loss',version=2,updated_at=?,updated_by='H',updated_via='cli',fresh_context_required=1 WHERE agent_user_id='G'",(AT,AT))
        db.raw.execute('UPDATE permission_state SET generation=2')
    with open_database(path,writable=False) as db:observed=s.load_root(db,catalog=BUNDLE)
    assert observed==final.root
    (tmp_path/'same-old-final.json').write_text(json.dumps({'initiating':asdict(initiating),'final':asdict(final),'observed':asdict(observed)},indent=2))


def test_explicit_authorization_only_in_final_typed_materialization(old):
    initiating=derive(old)
    assert initiating.root==old
    g=replace(row(old.authorities,'G'),suspended_at=None,suspension_reason=None,version=2,
              authorized_at=AT,authorized_by='H',permitted_use_at=AT,fresh_context_ack_at=AT,
              fresh_context_required=False,updated_at=AT,updated_by='H',updated_via='cli')
    final=derive(old,s.ProposalRows(authorities=(g,)),generation=2)
    assert final.base_stamp==initiating.base_stamp==old.stamp
    assert final.root.authorities==(g,row(old.authorities,'J'))
    assert g.epoch==9 and row(old.authorities,'G').suspended_at=='2026-01-03'
    # Values were supplied, not authorization performed by the helper.
    assert {x.name for x in fields(final.root)}=={'stamp','keys','users','organizations','companies','memberships','assignments','authorities','role_defaults','catalog'}
