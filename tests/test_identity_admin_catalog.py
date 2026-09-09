"""Full accepted build pipeline and selected B2 safe-failure boundaries."""
from dataclasses import asdict, replace
import pytest
from bookflow.hub import identity_admin as b, permission_catalog as c
from bookflow.storage.engine import open_database
from tests.permission_admin_support import *
from tests.test_permission_snapshots import BUNDLE as PRODUCTION


@pytest.mark.timeout(300)
def test_accepted_full_catalog_six_intents_independent_final_sql(tmp_path):
    path=make_root(tmp_path/'hub.db')
    with open_database(path,writable=True) as db:
        db.raw.execute('DELETE FROM role_capabilities')
        for x in PRODUCTION.descriptor.defaults:insert(db.raw,'role_capabilities',dict(role=x.role,capability=x.requirement.capability,required_role=x.requirement.threshold))
        db.raw.execute('UPDATE permission_state SET catalog_version=?,catalog_sha256=?,catalog_json=?',(c.FROZEN_CATALOG.version,c.FROZEN_MANIFEST.descriptor_sha256,s.encode_catalog(c.FROZEN_CATALOG)))
        old_tokens=tokens(db.raw);old_users=rows(db.raw,'users');old_members=rows(db.raw,'memberships');old_auth=rows(db.raw,'agent_authority');old_assign=rows(db.raw,'agent_principals')
        db.raw.execute('BEGIN IMMEDIATE')
        put=apply(db,b.PutMembership('B',c.ScopeKey('company','C'),b.Absent(),'readonly'),catalog=PRODUCTION)
        mid=put.visible.target_id
        apply(db,b.RevokeMembership(mid,1),catalog=PRODUCTION)
        apply(db,b.SetUserActive('R',5,False),catalog=PRODUCTION)
        apply(db,b.SetAssignments('G',4,7,('Q','I'),False),catalog=PRODUCTION)
        apply(db,b.AuthorizeAgent('G',5,8,True,True),catalog=PRODUCTION)
        old=s.load_root(db,catalog=PRODUCTION)
        # Explicit custom root default change; same complete trusted build descriptor.
        defaults=tuple(x for x in old.role_defaults if not (x.role=='readonly' and x.requirement.capability=='ledger.read'))
        result=apply(db,b.ReplaceCatalog(old.stamp.catalog_sha256,PRODUCTION,defaults),catalog=PRODUCTION)
        expected_users=tuple(dict(x,version=6,active=0,updated_at=AT,updated_by='H',updated_via='cli') if x['id']=='R' else x for x in old_users)
        assert rows(db.raw,'users')==expected_users
        assert rows(db.raw,'memberships')==(*old_members,dict(id=mid,user_id='B',scope_type='company',scope_id='C',role='readonly',grants='[]',denies='[]',granted_by='H',granted_at=AT,revoked_at=AT,version=2,updated_at=AT,updated_by='H',updated_via='cli'))
        expected_auth=tuple(dict(x,epoch=8,version=6,suspended_at=None,suspension_reason=None,fresh_context_required=0,authorized_at=AT,authorized_by='H',permitted_use_at=AT,fresh_context_ack_at=AT,updated_at=AT,updated_by='H',updated_via='cli') if x['agent_user_id']=='G' else x for x in old_auth)
        assert rows(db.raw,'agent_authority')==expected_auth
        assert rows(db.raw,'agent_principals')==tuple(dict(x,revoked_at=AT) if x['agent_user_id']=='G' and x['principal_user_id']=='P' else x for x in old_assign)
        assert tokens(db.raw)==expected_tokens(old_tokens,G_REVOKED|{'T-R'})
        assert len(rows(db.raw,'audit_events'))==6
        observed=s.load_root(db,catalog=PRODUCTION)
        assert observed==result.private.final.root and observed.stamp.generation==7 and observed.role_defaults==defaults
        assert c.FROZEN_MANIFEST.descriptor_sha256=='9823c59e7689c3835b2be794284ee89c6e00e82e589f07adcd3d639dec90ab42'
        receipt(tmp_path/'full-build.json',expected_users=expected_users,observed_users=rows(db.raw,'users'),expected_authorities=expected_auth,observed_authorities=rows(db.raw,'agent_authority'),expected_tokens=expected_tokens(old_tokens,G_REVOKED|{'T-R'}),observed_tokens=tokens(db.raw),observed_root=asdict(observed))


@pytest.mark.parametrize('case',['missing_authority','unknown_default','stale_catalog','invalid_bundle','invalid_policy','bad_generation'])
def test_invalid_complete_facts_and_catalog_fail_without_mutation(tmp_path,case):
    path=make_root(tmp_path/'hub.db')
    with open_database(path,writable=True) as db:
        if case=='missing_authority':db.raw.execute("DELETE FROM agent_authority WHERE agent_user_id='G'")
        if case=='unknown_default':db.raw.execute("INSERT INTO role_capabilities VALUES ('standard','private-unknown','standard')")
        if case=='invalid_policy':db.raw.execute("UPDATE memberships SET grants='[\"private-invalid\"]' WHERE id='M-I'")
        if case=='bad_generation':db.raw.execute("PRAGMA ignore_check_constraints=ON");db.raw.execute('UPDATE permission_state SET generation=0')
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        intent=b.SetUserActive('R',5,False)
        if case=='stale_catalog':intent=b.ReplaceCatalog('0'*64,BUNDLE,CATALOG.defaults)
        if case=='invalid_bundle':intent=b.ReplaceCatalog(c.catalog_manifest(CATALOG).descriptor_sha256,replace(BUNDLE,source_commit='private-invalid'),CATALOG.defaults)
        with pytest.raises(b.AdministrationError) as caught:apply(db,intent)
        assert snapshot(db.raw)==before and 'private-' not in str(caught.value)


def test_full_catalog_input_fixed_tuple_types_are_preserved():
    from bookflow.hub.agent_authority import typed
    typed(b.ReplaceCatalog('0'*64,PRODUCTION,PRODUCTION.descriptor.defaults),b.ReplaceCatalog)
    for bad in (('source.py',True),('source.py','1'),('source.py',1,2)):
        with pytest.raises(b.AdministrationError):typed(bad,tuple[str,int])
