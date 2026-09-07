from dataclasses import replace
import pytest
from bookflow.hub import identity_admin as b, permission_catalog as c
from bookflow.storage.engine import open_database
from tests.permission_admin_support import *


@pytest.fixture
def path(tmp_path):return make_root(tmp_path/'hub.db')


def test_membership_create_and_exact_noop_raw_preservation(path,tmp_path):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        old=snapshot(db.raw)
        intent=b.PutMembership('R',c.ScopeKey('company','C'),b.Absent(),'readonly')
        result=apply(db,intent)
        member=next(x for x in rows(db.raw,'memberships') if x['id']==result.visible.target_id)
        assert member==dict(id=result.visible.target_id,user_id='R',scope_type='company',scope_id='C',role='readonly',grants='[]',denies='[]',granted_by='H',granted_at=AT,revoked_at=None,version=1,updated_at=AT,updated_by='H',updated_via='cli')
        assert db.raw.in_transaction
        after=snapshot(db.raw)
        repeat=apply(db,b.PutMembership('R',c.ScopeKey('company','C'),b.Version(1),'readonly',(),None))
        assert not repeat.visible.changed and snapshot(db.raw)==after and repeat.event_id is None
        assert db.raw.execute('SELECT generation FROM permission_state').fetchone()==(2,)
        receipt(tmp_path/'membership.json',row=member,event=rows(db.raw,'audit_events'),entries=rows(db.raw,'audit_entries'))
        db.raw.execute('ROLLBACK')
        assert snapshot(db.raw)==old


@pytest.mark.parametrize('person,role,allowed',[('RO','readonly',False),('B','standard',False),('A','admin',True),('W','owner',True),('H','hub_admin',True)])
def test_scoped_administration_roles(path,person,role,allowed):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        intent=b.PutMembership('R',c.ScopeKey('company','C'),b.Absent(),'standard')
        if allowed:assert apply(db,intent,person).visible.changed
        else:
            with pytest.raises(b.AdministrationError,match='not_administrator'):apply(db,intent,person)
            assert snapshot(db.raw)==before


@pytest.mark.parametrize('verb',['edit','revoke','grant_owner'])
def test_active_owner_cannot_be_demoted_or_revoked_by_admin(path,verb):
    intent={'edit':b.PutMembership('W',c.ScopeKey('organization','O'),b.Version(1),'readonly'),
            'revoke':b.RevokeMembership('M-W',1),
            'grant_owner':b.PutMembership('R',c.ScopeKey('company','C'),b.Absent(),'owner')}[verb]
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises(b.AdministrationError,match='not_administrator'):apply(db,intent,'A')
        assert snapshot(db.raw)==before
        assert apply(db,intent,'W').visible.changed


@pytest.mark.parametrize('target',['R','missing'])
def test_hidden_scope_precedes_target_and_conflict(path,target):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises(b.AdministrationError) as caught:apply(db,b.PutMembership(target,c.ScopeKey('company','E'),b.Version(55),'standard'),'A')
        assert caught.value.args==('unavailable_target','scope') and snapshot(db.raw)==before


def test_reactivated_revoked_owner_lower_role_and_inactive_membership(path):
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE memberships SET revoked_at=? WHERE id='M-W'",(OLD,))
        db.raw.execute('BEGIN IMMEDIATE')
        with pytest.raises(b.AdministrationError,match='not_administrator'):apply(db,b.PutMembership('W',c.ScopeKey('organization','O'),b.Version(1),'owner'),'A')
        result=apply(db,b.PutMembership('W',c.ScopeKey('organization','O'),b.Version(1),'readonly'),'A')
        assert result.visible.target_id=='M-W' and result.visible.version==2
        value=next(x for x in rows(db.raw,'memberships') if x['id']=='M-W')
        assert value==dict(id='M-W',user_id='W',scope_type='organization',scope_id='O',role='readonly',grants='[]',denies='[]',granted_by='A',granted_at=AT,revoked_at=None,version=2,updated_at=AT,updated_by='A',updated_via='cli')
        apply(db,b.PutMembership('I',c.ScopeKey('company','C'),b.Absent(),'standard'),'A')
        assert db.raw.execute("SELECT active FROM users WHERE id='I'").fetchone()==(0,)


@pytest.mark.parametrize('case',['null_empty','stale','absent_revoked','invalid_overlap','duplicate_grant','unknown_case'])
def test_membership_noop_and_strict_policy(path,case):
    with open_database(path,writable=True) as db:
        if case=='absent_revoked':db.raw.execute("UPDATE memberships SET revoked_at=? WHERE id='M-R'",(OLD,))
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        expected=b.Absent() if case=='absent_revoked' else b.Version(9 if case=='stale' else 1)
        grants={'invalid_overlap':('ledger.read',),'duplicate_grant':('ledger.read','ledger.read'),'unknown_case':('Ledger.Read',)}.get(case,())
        denies=('ledger.read',) if case=='invalid_overlap' else None
        intent=b.PutMembership('R',c.ScopeKey('organization','O'),expected,'standard',grants,denies)
        if case=='null_empty':assert not apply(db,intent).visible.changed
        else:
            with pytest.raises(b.AdministrationError):apply(db,intent)
        assert snapshot(db.raw)==before


def test_legacy_and_unknown_visibility_before_mutation(path):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        intent=b.SetUserActive('R',5,False)
        with pytest.raises(b.AdministrationError,match='visibility_unresolved'):apply(db,intent,visibility=None)
        assert snapshot(db.raw)==before
        db.raw.execute("UPDATE permission_state SET mode='legacy',catalog_version=NULL,catalog_sha256=NULL,catalog_json=NULL")
        before=snapshot(db.raw)
        with pytest.raises(b.AdministrationError,match='activation_required'):apply(db,intent)
        assert snapshot(db.raw)==before


def test_company_admin_cannot_administer_parent_or_sibling(path):
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE memberships SET scope_type='company',scope_id='C' WHERE id='M-A'")
        db.raw.execute('BEGIN IMMEDIATE')
        for scope in (c.ScopeKey('organization','O'),c.ScopeKey('company','D')):
            before=snapshot(db.raw)
            with pytest.raises(b.AdministrationError,match='unavailable_target'):apply(db,b.PutMembership('R',scope,b.Absent(),'standard'),'A')
            assert snapshot(db.raw)==before
        assert apply(db,b.PutMembership('R',c.ScopeKey('company','C'),b.Absent(),'standard'),'A').visible.changed


@pytest.mark.parametrize('holds_delete',[False,True])
def test_delete_delegation_does_not_require_business_delete(path,holds_delete):
    with open_database(path,writable=True) as db:
        if holds_delete:db.raw.execute("UPDATE memberships SET grants='[\"transaction.invoice.delete\"]' WHERE id='M-A'")
        db.raw.execute('BEGIN IMMEDIATE')
        result=apply(db,b.PutMembership('R',c.ScopeKey('company','C'),b.Absent(),'standard',('transaction.invoice.delete',)),'A')
        assert db.raw.execute('SELECT grants FROM memberships WHERE id=?',(result.visible.target_id,)).fetchone()==('["transaction.invoice.delete"]',)


@pytest.mark.parametrize('intent',[b.SetUserActive('S',5,False),b.SetUserActive('S',5,True),b.PutMembership('S',c.ScopeKey('company','C'),b.Absent(),'readonly')])
def test_system_identity_is_protected_even_for_noop(path,intent):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises(b.AdministrationError,match='protected_identity'):apply(db,intent)
        assert snapshot(db.raw)==before


def test_readonly_hub_admin_visibility_stays_explicit_and_unknown_safe(path):
    class Incomplete:
        def facts(self,*args):return s.VisibilityFacts('unknown',())
    class Exploding:
        def facts(self,*args):raise ValueError('private-provider-detail')
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        intent=b.PutMembership('R',c.ScopeKey('company','C'),b.Absent(),'standard')
        for provider in (SuppliedVisibility(False),Incomplete(),Exploding()):
            with pytest.raises(b.AdministrationError) as caught:apply(db,intent,visibility=provider)
            assert caught.value.category in ('unavailable_target','visibility_unresolved')
            assert 'private-provider' not in str(caught.value) and snapshot(db.raw)==before
        assert apply(db,intent,visibility=SuppliedVisibility(True)).visible.changed


@pytest.mark.parametrize('bad', [b.SetUserActive('R',True,False),b.SetUserActive('R',5,1),b.PutMembership('R',c.ScopeKey('company','C'),b.Absent(),'invalid'),b.SetAssignments('G',4,7,('Q','Q'),True)])
def test_strict_typed_intent_failure(path,bad):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises(b.AdministrationError):apply(db,bad)
        assert snapshot(db.raw)==before


def test_malformed_active_policy_is_not_repaired(path):
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE memberships SET grants='private-malformed' WHERE id='M-P'")
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises(b.AdministrationError,match='legacy_policy_invalid'):apply(db,b.SetUserActive('R',5,False))
        assert snapshot(db.raw)==before
