from dataclasses import asdict, replace
import pytest
from bookflow.hub import identity_admin as b, permission_catalog as c
from bookflow.storage.engine import open_database
from tests.permission_admin_support import *


@pytest.fixture
def path(tmp_path):return make_root(tmp_path/'hub.db')


@pytest.mark.parametrize('case,reason,fresh',[
    ('remove','binding_loss',True),('replace','binding_loss',True),('deactivate','binding_loss',True),
    ('own_loss','own_authority_loss',True),('human_beneath_narrow','principal_authority_loss',True),
    ('unequal_gain','principal_set_unequal',False),('latent_role','principal_set_unequal',False),
    ('future','principal_set_unequal',False),('loss_gain','principal_authority_loss',True),
    ('further_suspended','binding_loss',True)])
def test_exact_full_authority_and_all_token_vectors(path,tmp_path,case,reason,fresh):
    with open_database(path,writable=True) as db:
        if case=='human_beneath_narrow':db.raw.execute("UPDATE memberships SET role='readonly' WHERE id='M-G'")
        if case=='latent_role':
            db.raw.execute("UPDATE memberships SET role='readonly' WHERE id IN ('M-P','M-Q','M-G')")
            db.raw.execute("UPDATE memberships SET grants='[\"transaction.invoice.delete\"]' WHERE id='M-P'")
        if case=='future':db.raw.execute("UPDATE memberships SET scope_type='company',scope_id='C' WHERE id IN ('M-P','M-Q')")
        if case=='further_suspended':db.raw.execute("UPDATE agent_authority SET suspended_at=?,suspension_reason='earlier',fresh_context_required=1 WHERE agent_user_id='G'",(OLD,))
        old_agents={x['agent_user_id']:x for x in rows(db.raw,'agent_authority')};old_tokens=tokens(db.raw)
        old_assign=rows(db.raw,'agent_principals')
        intents={
            'remove':b.SetAssignments('G',4,7,('Q','I'),False),
            'replace':b.SetAssignments('G',4,7,('R','Q','I'),True),
            'deactivate':b.SetUserActive('P',5,False),
            'own_loss':b.PutMembership('G',c.ScopeKey('organization','O'),b.Version(1),'readonly'),
            'human_beneath_narrow':b.PutMembership('P',c.ScopeKey('organization','O'),b.Version(1),'readonly'),
            'unequal_gain':b.PutMembership('P',c.ScopeKey('organization','O'),b.Version(1),'standard',('transaction.invoice.delete',)),
            'latent_role':b.PutMembership('P',c.ScopeKey('organization','O'),b.Version(1),'standard',('transaction.invoice.delete',)),
            'future':b.PutMembership('Q',c.ScopeKey('organization','O'),b.Absent(),'readonly'),
            'loss_gain':b.PutMembership('P',c.ScopeKey('organization','O'),b.Version(1),'standard',('transaction.invoice.delete',),('ledger.read',)),
            'further_suspended':b.SetAssignments('G',4,7,('Q','I'),False)}
        db.raw.execute('BEGIN IMMEDIATE');result=apply(db,intents[case])
        expected_g=dict(old_agents['G'],epoch=8,version=5,suspended_at=OLD if case=='further_suspended' else AT,
            suspension_reason=reason,fresh_context_required=int(fresh),updated_at=AT,updated_by='H',updated_via='cli')
        if case=='replace':expected_g['permitted_use_at']=AT
        actual_agents={x['agent_user_id']:x for x in rows(db.raw,'agent_authority')}
        assert actual_agents=={'G':expected_g,'J':old_agents['J']}
        expected=expected_tokens(old_tokens,G_REVOKED|({'T-P'} if case=='deactivate' else set()))
        assert tokens(db.raw)==expected
        assert db.raw.execute('SELECT generation FROM permission_state').fetchone()==(2,)
        assert len(rows(db.raw,'audit_events'))==1
        if case in ('remove','replace','further_suspended'):
            expected_assign=tuple(dict(x,revoked_at=AT) if (x['agent_user_id'],x['principal_user_id'])==('G','P') else x for x in old_assign)
            if case=='replace':expected_assign=(*expected_assign,dict(agent_user_id='G',principal_user_id='R',assigned_by='H',assigned_at=AT,revoked_at=None))
            assert rows(db.raw,'agent_principals')==expected_assign
        if case=='future':
            before=result.private.reconciliation.comparison.signatures_old;after=result.private.reconciliation.comparison.signatures_new
            get=lambda values,who:next(x for x in next(v for v in values if v.subject==who).scopes if x.scope==c.ScopeKey('future_company','O')).effective_visible
            assert not get(before,'P') and not get(before,'Q') and not get(after,'P') and get(after,'Q')
        expected_signatures={}
        for phase in ('old','new'):
            vectors=[]
            for who in ('G','I','J','P','Q','R','U'):
                domain='Z' if who in ('J','U') else 'O'
                role='standard';delete=False;read=True;active=who!='I'
                if case=='human_beneath_narrow' and who=='G':role='readonly'
                if case=='latent_role' and who in ('P','Q','G'):role='readonly'
                if phase=='new':
                    if case=='own_loss' and who=='G' or case=='human_beneath_narrow' and who=='P':role='readonly'
                    if case in ('unequal_gain','loss_gain','latent_role') and who=='P':delete=True;role='standard'
                    if case=='loss_gain' and who=='P':read=False
                    if case=='deactivate' and who=='P':active=False
                roles={(kind,key):role for kind,key in [('organization',domain),('future_company',domain),*([('company','E')] if domain=='Z' else [('company','C'),('company','D')])]}
                if case=='future' and who in ('P','Q'):
                    roles={('company','C'):'standard'}
                    if phase=='new' and who=='Q':roles.update({('organization','O'):'readonly',('company','D'):'readonly',('future_company','O'):'readonly'})
                vectors.append(expected_signature(who,roles,active=active,delete=delete,read=read))
            actual=tuple(x for x in getattr(result.private.reconciliation.comparison,'signatures_'+phase) if x.subject in ('G','I','J','P','Q','R','U'))
            assert actual==tuple(vectors)
            expected_signatures[phase]=[asdict(x) for x in vectors]
        eligible_old={x.id:x.value.eligible_humans for x in result.private.pair.comparison.old.agents}
        eligible_new={x.id:x.value.eligible_humans for x in result.private.pair.comparison.new.agents}
        assert eligible_old=={'G':('P','Q'),'J':('U',)}
        expected_g=('Q',) if case in ('remove','deactivate','further_suspended') else ('Q','R') if case=='replace' else ('P','Q')
        assert eligible_new=={'G':expected_g,'J':('U',)}
        receipt(tmp_path/'signatures.json',expected=expected_signatures,observed_old=[asdict(x) for x in result.private.reconciliation.comparison.signatures_old],observed_new=[asdict(x) for x in result.private.reconciliation.comparison.signatures_new],eligible_old=eligible_old,eligible_new=eligible_new)
        receipt(tmp_path/'transition.json',expected_authorities={'G':expected_g,'J':old_agents['J']},observed_authorities=actual_agents,expected_tokens=expected,observed_tokens=tokens(db.raw))


@pytest.mark.parametrize('suspended',[False,True])
def test_unchanged_unequal_and_retained_inactive_assignment_is_raw_noop(path,suspended):
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE memberships SET denies='[\"ledger.read\"]' WHERE id='M-P'")
        if suspended:db.raw.execute("UPDATE agent_authority SET suspended_at=?,suspension_reason='existing',fresh_context_required=1 WHERE agent_user_id='G'",(OLD,))
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        result=apply(db,b.SetAssignments('G',4,7,('P','Q','I'),False))
        assert not result.visible.changed and snapshot(db.raw)==before


def test_retained_inactive_only_removes_active_bindings_without_extra_revoke(path):
    with open_database(path,writable=True) as db:
        before=rows(db.raw,'agent_principals');old_tokens=tokens(db.raw);db.raw.execute('BEGIN IMMEDIATE')
        apply(db,b.SetAssignments('G',4,7,('I',),False))
        assert rows(db.raw,'agent_principals')==tuple(dict(x,revoked_at=AT) if x['agent_user_id']=='G' and x['principal_user_id'] in ('P','Q') else x for x in before)
        assert tokens(db.raw)==expected_tokens(old_tokens,G_REVOKED)
        before=snapshot(db.raw)
        with pytest.raises(b.AdministrationError,match='invalid_assignment'):apply(db,b.AuthorizeAgent('G',5,8,True,True))
        assert snapshot(db.raw)==before


@pytest.mark.parametrize('case',['inactive_reactivation','unequal_addition','missing_confirmation','inactive_agent'])
def test_assignment_admission_is_atomic(path,case):
    with open_database(path,writable=True) as db:
        if case=='inactive_reactivation':db.raw.execute("UPDATE agent_principals SET revoked_at=? WHERE principal_user_id='I'",(OLD,))
        if case=='unequal_addition':db.raw.execute("UPDATE memberships SET role='readonly' WHERE id='M-R'")
        if case=='inactive_agent':db.raw.execute("UPDATE users SET active=0 WHERE id='G'")
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        principals=('P','Q','I') if case=='inactive_reactivation' else ('P','Q','I','R')
        with pytest.raises(b.AdministrationError):apply(db,b.SetAssignments('G',4,7,principals,case!='missing_confirmation'))
        assert snapshot(db.raw)==before


def test_ineffective_readonly_delete_removal_changes_policy_not_epoch(path):
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE memberships SET role='readonly' WHERE id IN ('M-P','M-Q','M-G')")
        db.raw.execute("UPDATE memberships SET grants='[\"transaction.invoice.delete\"]' WHERE id='M-P'")
        before_agents=rows(db.raw,'agent_authority');before_tokens=tokens(db.raw);db.raw.execute('BEGIN IMMEDIATE')
        assert apply(db,b.PutMembership('P',c.ScopeKey('organization','O'),b.Version(1),'readonly')).visible.changed
        assert rows(db.raw,'agent_authority')==before_agents and tokens(db.raw)==before_tokens
        assert db.raw.execute("SELECT version,grants FROM memberships WHERE id='M-P'").fetchone()==(2,'[]')
        assert db.raw.execute('SELECT generation FROM permission_state').fetchone()==(2,)


def test_restoration_then_explicit_reauthorization_never_revives_tokens(path,tmp_path):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        apply(db,b.RevokeMembership('M-P',1));revoked=tokens(db.raw)
        assert db.raw.execute("SELECT epoch,version,fresh_context_required FROM agent_authority WHERE agent_user_id='G'").fetchone()==(8,5,1)
        apply(db,b.PutMembership('P',c.ScopeKey('organization','O'),b.Version(2),'standard'))
        assert tokens(db.raw)==revoked
        assert db.raw.execute("SELECT epoch,version,suspended_at FROM agent_authority WHERE agent_user_id='G'").fetchone()==(8,5,AT)
        for confirmed,ack in [(False,True),(True,False)]:
            before=snapshot(db.raw)
            with pytest.raises(b.AdministrationError,match='confirmation_required'):apply(db,b.AuthorizeAgent('G',5,8,confirmed,ack))
            assert snapshot(db.raw)==before
        apply(db,b.AuthorizeAgent('G',5,8,True,True))
        actual=next(x for x in rows(db.raw,'agent_authority') if x['agent_user_id']=='G')
        assert actual==dict(agent_user_id='G',epoch=8,suspended_at=None,suspension_reason=None,version=6,updated_at=AT,updated_by='H',updated_via='cli',authorized_at=AT,authorized_by='H',permitted_use_at=AT,fresh_context_ack_at=AT,fresh_context_required=0)
        assert tokens(db.raw)==revoked
        before=snapshot(db.raw);assert not apply(db,b.AuthorizeAgent('G',6,8,True,False)).visible.changed
        assert snapshot(db.raw)==before and len(rows(db.raw,'audit_events'))==3
        receipt(tmp_path/'reauthorization.json',authority=actual,tokens=tokens(db.raw))


def test_catalog_default_loss_reconciles_all_agents_once(path,tmp_path):
    with open_database(path,writable=True) as db:
        old_tokens=tokens(db.raw);old_agents=rows(db.raw,'agent_authority');db.raw.execute('BEGIN IMMEDIATE')
        old=s.load_root(db,catalog=BUNDLE)
        desired=tuple(x for x in old.role_defaults if not (x.role=='standard' and x.requirement.capability=='ledger.post'))
        result=apply(db,b.ReplaceCatalog(old.stamp.catalog_sha256,BUNDLE,desired))
        expected_agents=tuple(dict(x,epoch=x['epoch']+1,version=x['version']+1,suspended_at=AT,suspension_reason='principal_authority_loss',fresh_context_required=1,updated_at=AT,updated_by='H',updated_via='cli') for x in old_agents)
        assert rows(db.raw,'agent_authority')==expected_agents
        assert tokens(db.raw)==expected_tokens(old_tokens,G_REVOKED|{'JU-live'})
        assert result.private.final.root.role_defaults==desired
        assert db.raw.execute('SELECT generation FROM permission_state').fetchone()==(2,)
        receipt(tmp_path/'catalog-loss.json',expected_authorities=expected_agents,observed_authorities=rows(db.raw,'agent_authority'),expected_tokens=expected_tokens(old_tokens,G_REVOKED|{'JU-live'}),observed_tokens=tokens(db.raw))


def test_assignment_reactivation_preserves_physical_history_and_needs_explicit_authorization(path,tmp_path):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        physical=db.raw.execute("SELECT rowid FROM agent_principals WHERE agent_user_id='G' AND principal_user_id='P'").fetchone()
        apply(db,b.SetAssignments('G',4,7,('Q','I'),False));revoked=tokens(db.raw)
        apply(db,b.SetAssignments('G',5,8,('P','Q','I'),True))
        assert db.raw.execute("SELECT rowid,assigned_by,assigned_at,revoked_at FROM agent_principals WHERE agent_user_id='G' AND principal_user_id='P'").fetchone()==(*physical,'H',AT,None)
        assert db.raw.execute("SELECT epoch,version,suspended_at,fresh_context_required,permitted_use_at FROM agent_authority WHERE agent_user_id='G'").fetchone()==(8,6,AT,1,AT)
        assert tokens(db.raw)==revoked
        apply(db,b.AuthorizeAgent('G',6,8,True,True))
        assert db.raw.execute("SELECT epoch,version,suspended_at FROM agent_authority WHERE agent_user_id='G'").fetchone()==(8,7,None)
        assert tokens(db.raw)==revoked
        receipt(tmp_path/'assignment-reactivation.json',physical_rowid=physical,assignments=rows(db.raw,'agent_principals'),tokens=tokens(db.raw))


def test_explicit_authorize_rejects_unequal_with_no_mutation(path):
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE memberships SET role='readonly' WHERE id='M-P'")
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises(b.AdministrationError,match='unequal_principals'):apply(db,b.AuthorizeAgent('G',4,7,True,True))
        assert snapshot(db.raw)==before


@pytest.mark.parametrize('person,revocations',[('P',G_REVOKED|{'T-P'}),('G',G_REVOKED)])
def test_deactivation_restoration_and_inactive_noop_do_not_revive_or_repair_tokens(path,person,revocations):
    with open_database(path,writable=True) as db:
        old=tokens(db.raw);db.raw.execute('BEGIN IMMEDIATE')
        apply(db,b.SetUserActive(person,5,False))
        assert tokens(db.raw)==expected_tokens(old,revocations)
        revoked=tokens(db.raw);authority=rows(db.raw,'agent_authority')
        before=snapshot(db.raw);assert not apply(db,b.SetUserActive(person,6,False)).visible.changed
        assert snapshot(db.raw)==before
        apply(db,b.SetUserActive(person,6,True))
        assert tokens(db.raw)==revoked and rows(db.raw,'agent_authority')==authority
        assert db.raw.execute('SELECT generation FROM permission_state').fetchone()==(3,)
        # Malformed old operational state is not silently repaired by an inactive noop.
        db.raw.execute("UPDATE users SET active=0 WHERE id='R'")
        before=snapshot(db.raw);assert not apply(db,b.SetUserActive('R',5,False)).visible.changed
        assert snapshot(db.raw)==before and tokens(db.raw)['T-R']['revoked_at'] is None


@pytest.mark.parametrize('change',['remove_capability','remove_threshold'])
def test_catalog_universe_loss_complete_authority_and_tokens(path,change,tmp_path):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');old=s.load_root(db,catalog=BUNDLE)
        old_tokens=tokens(db.raw);old_agents=rows(db.raw,'agent_authority')
        capability='ledger.post'
        specs=tuple(x for x in old.catalog.capabilities if x.name!=capability) if change=='remove_capability' else tuple(replace(x,company_thresholds=(),registered_thresholds=('standard',)) if x.name==capability else x for x in old.catalog.capabilities)
        defaults=tuple(x for x in old.role_defaults if x.requirement.capability!=capability)
        actions=tuple(x for x in old.catalog.company_actions if x.key!='post')
        descriptor=replace(old.catalog,version='test-v2',capabilities=specs,defaults=defaults,company_actions=actions)
        bundle=replace(BUNDLE,descriptor=descriptor,source_commit='a'*40)
        result=apply(db,b.ReplaceCatalog(old.stamp.catalog_sha256,bundle,defaults))
        expected=tuple(dict(x,epoch=x['epoch']+1,version=x['version']+1,suspended_at=AT,suspension_reason='principal_authority_loss',fresh_context_required=1,updated_at=AT,updated_by='H',updated_via='cli') for x in old_agents)
        assert rows(db.raw,'agent_authority')==expected
        assert tokens(db.raw)==expected_tokens(old_tokens,G_REVOKED|{'JU-live'})
        observed=s.load_root(db,catalog=bundle)
        assert observed==result.private.final.root and observed.role_defaults==defaults
        receipt(tmp_path/'universe-loss.json',expected_authorities=expected,observed_authorities=rows(db.raw,'agent_authority'),expected_tokens=expected_tokens(old_tokens,G_REVOKED|{'JU-live'}),observed_tokens=tokens(db.raw))


def test_catalog_removed_policy_reference_is_not_silently_deleted(path):
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE memberships SET denies='[\"ledger.post\"]' WHERE id='M-R'")
        db.raw.execute('BEGIN IMMEDIATE');old=s.load_root(db,catalog=BUNDLE);before=snapshot(db.raw)
        defaults=tuple(x for x in old.role_defaults if x.requirement.capability!='ledger.post')
        descriptor=replace(old.catalog,version='test-v2',capabilities=tuple(x for x in old.catalog.capabilities if x.name!='ledger.post'),defaults=defaults,company_actions=tuple(x for x in old.catalog.company_actions if x.key!='post'))
        with pytest.raises(b.AdministrationError):apply(db,b.ReplaceCatalog(old.stamp.catalog_sha256,replace(BUNDLE,descriptor=descriptor),defaults))
        assert snapshot(db.raw)==before


def expected_signature(who,roles,*,active=True,delete=False,read=True):
    # Handwritten fixture oracle; no A/B1/B2 resolver or proposed facts input.
    from bookflow.hub.permission_policy import AuthoritySignature,ScopeSignature
    scopes=[]
    for kind,key in [('hub','root'),('organization','O'),('organization','Z'),('company','C'),('company','D'),('company','E'),('future_company','O'),('future_company','Z')]:
        role=roles.get((kind,key));visible=active and role is not None
        bits=()
        if kind in ('company','future_company'):
            bits=((c.Requirement('customer-work','member'),visible),
                  (c.Requirement('ledger.post','standard'),visible and role=='standard'),
                  (c.Requirement('ledger.read','member'),visible and read),
                  (c.Requirement('transaction.invoice.delete','standard'),visible and role=='standard' and delete),
                  (c.Requirement('transaction.payment.delete','standard'),False))
        admin={'hub':(('admin:users',False),),'organization':(('admin:members:standard:organization',False),),'company':(('admin:members:owner:company',False),('admin:members:standard:company',False))}.get(kind,())
        scopes.append(ScopeSignature(c.ScopeKey(kind,key),True,visible,role,False,bits,admin))
    return AuthoritySignature(who,True,active,tuple(scopes))
