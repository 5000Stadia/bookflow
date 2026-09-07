"""Permanent recovery reauthorizes real actors and the full saved graph."""
import pytest
from bookflow import BookflowError
from bookflow.core.context import Context,Interface
from bookflow.core.publication import OSBinding
from bookflow.company import deposit_coordinate_persistence as persistence, deposit_operation_pages as pages
from bookflow.company.deposit_dependency_models import PageInput
from tests.test_deposit_coordinate_persistence import n2,prepare,sale,driver
from tests.test_deposit_dependency_binding import bound_people,observe,_storage,_credential
from tests.test_row8_journal import database_path


def test_cross_actor_recovery_cursor_binding_and_original_command_gate(root,client,sale,driver,n2,monkeypatch,bound_people):
    inp,*_=n2;people=bound_people
    ctx=Context.new(Interface.python,'C authority',reason='Correct actual cash')
    with driver.session() as s:
        result=persistence.execute(s,ctx,prepare(s,ctx,inp))
    baseline=_storage(root,database_path(client))
    def first(s):
        binding=OSBinding.from_session(s)
        recovered=persistence.recover(s,ctx,inp,binding)
        assert recovered.effect==result.effect and recovered.idempotent_replay
        return pages.items(s,inp.operation_key,'memberships',PageInput(limit=1),binding)
    page=observe(people['one'],monkeypatch,first,people['company'])
    assert page.next_cursor and page.total_count==4
    def second(s):
        binding=OSBinding.from_session(s)
        assert persistence.recover(s,ctx,inp,binding).effect==result.effect
        with pytest.raises(BookflowError) as error:
            pages.items(s,inp.operation_key,'memberships',PageInput(limit=1,cursor=page.next_cursor),binding)
        assert error.value.code=='E_VALIDATION'
        with pytest.raises(BookflowError):
            pages.items(s,inp.operation_key,'memberships',PageInput(limit=1,cursor=page.next_cursor[:-2]+'ZZ'),binding)
    observe(people['two'],monkeypatch,second,people['company'])
    assert _storage(root,database_path(client))==baseline
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    with writer(root) as db:
        db.conn.execute(h.memberships.update().where(h.memberships.c.user_id==people['first']).values(role='readonly'))
    baseline=_storage(root,database_path(client))
    def readonly(s):
        binding=OSBinding.from_session(s)
        for key in (inp.operation_key,'C-absent-key'):
            with pytest.raises(BookflowError) as error:persistence.recover(s,ctx,inp.model_copy(update={'operation_key':key}),binding)
            assert error.value.code=='E_PERMISSION' and error.value.details=={}
        assert pages.items(s,inp.operation_key,'memberships',PageInput(),binding).total_count==4
    observe(people['one'],monkeypatch,readonly,people['company'])
    assert _storage(root,database_path(client))==baseline


@pytest.mark.parametrize('case',['revoked','expired','principal_loss','context_mismatch'])
def test_actual_fixed_principal_recovery_rejects_without_storage_effect(root,client,sale,driver,n2,monkeypatch,bound_people,case):
    inp,*_=n2;people=bound_people
    original=Context.new(Interface.python,'C human',reason='Correct actual cash')
    with driver.session() as s:result=persistence.execute(s,original,prepare(s,original,inp))
    credential=_credential(client,people['agent'],people['first'])
    ctx=Context.new(Interface.http,'C agent',reason=original.reason,on_behalf_of=people['first'])
    def recover(s):return persistence.recover(s,ctx,inp,credential)
    assert observe(people['bot'],monkeypatch,recover,people['company']).effect==result.effect
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    if case=='context_mismatch':ctx=Context.new(Interface.http,'wrong principal',reason=original.reason,on_behalf_of=people['second'])
    else:
        with writer(root) as db:
            if case=='principal_loss':db.conn.execute(h.memberships.update().where(h.memberships.c.user_id==people['first']).values(role='readonly'))
            else:db.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id==credential.token_id).values(**({'revoked_at':'2026-06-01T00:00:00.000Z'} if case=='revoked' else {'expires_at':'2000-01-01T00:00:00.000Z'})))
    baseline=_storage(root,database_path(client))
    def denied(s):
        for submitted in (inp,inp.model_copy(update={'operation_key':'C-never-saved'})):
            with pytest.raises(BookflowError) as error:persistence.recover(s,ctx,submitted,credential)
            assert error.value.code==('E_PERMISSION' if case=='principal_loss' else 'E_UNAUTHENTICATED')
            if case=='principal_loss':assert error.value.details=={}
    observe(people['bot'],monkeypatch,denied,people['company'])
    assert _storage(root,database_path(client))==baseline


def test_each_private_touch_has_complete_scalar_and_cohort_owner(client,sale,driver,n2):
    from bookflow.company import payment_authority as authority
    inp,post,_,payment,receipt,invoice=n2
    ctx=Context.new(Interface.python,'C evidence roots',reason='Correct actual cash')
    with driver.session() as s:
        result=persistence.execute(s,ctx,prepare(s,ctx,inp))
        event=result.effect.deposit.audit_event_id
        targets={post.current.id,payment['id'],receipt['id'],invoice['id']}
        assert authority.record_transactions(s.company,'deposit_operation',result.operation_id)==targets
        cohort=authority._EventCohort(s.company,[event])
        assert cohort.requirements(event)==authority.event_requirements(s.company,event)
        touched=s.company.raw.execute('SELECT record_type,record_id FROM audit_entries WHERE event_id=?',(event,)).fetchall()
        for kind,key in touched:
            if kind not in authority._COORDINATE_TARGETS:continue
            actual=authority.record_transactions(s.company,kind,key)
            table,field=authority._COORDINATE_TARGETS[kind]
            record=dict(zip([r[1] for r in s.company.raw.execute('PRAGMA table_info('+table+')')],s.company.raw.execute('SELECT * FROM '+table+' WHERE '+field+'=?',(key,)).fetchone()))
            expected={record['transaction_id']}
            if kind in ('deposit_membership','deposit_component') and record['source_transaction_id'] is not None:expected.add(record['source_transaction_id'])
            assert actual==expected
            # Isolated occurrence exercises this owner without a transaction
            # sibling supplying its graph; the cohort's explicit lookup is used.
            assert cohort._walk((kind,key))==expected
