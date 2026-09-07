"""Permanent coordinate receipts, full pages and transaction failure boundaries."""
import copy
import json
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.core.context import Context, Interface
from bookflow.company import schema as c, deposit_operation_pages as pages
from bookflow.company import deposit_coordinate_persistence as persistence
from bookflow.company.deposit_dependency_models import PageInput
from bookflow.company.deposit_coordinate_models import CoordinateInput
from tests.test_deposit_coordinate_persistence import n2,prepare,sale,driver


def test_original_pages_retry_reason_and_directive_distinction(client,sale,driver,n2):
    inp,*_=n2
    ctx=Context.new(Interface.python,'C recovery witness',reason='Correct captured cash')
    with driver.session() as s:
        p=prepare(s,ctx,inp)
        result=persistence.execute(s,ctx,p)
        frozen=tuple(s.company.raw.iterdump())
        expected=pages.collections(result)
        for kind,values in expected.items():
            for limit in (1,50,200):
                observed=[];cursor=None;seen=set()
                while True:
                    page=pages.items(s,inp.operation_key,kind,PageInput(limit=limit,cursor=cursor),p.binding)
                    assert page.total_count==len(values)
                    observed.extend(page.model_dump(mode='json')['items'])
                    if page.next_cursor is None:break
                    assert page.next_cursor not in seen
                    seen.add(page.next_cursor);cursor=page.next_cursor
                assert observed==values
        assert tuple(s.company.raw.iterdump())==frozen
        other_directive=ctx.model_copy(update={'directive_id':'different-external-authorization'})
        replay=persistence.recover(s,other_directive,inp,p.binding)
        assert replay.effect==result.effect and replay.idempotent_replay
        other_reason=ctx.model_copy(update={'reason':'A different business explanation'})
        assert persistence.recover(s,other_reason,inp,p.binding) is None
        assert tuple(s.company.raw.iterdump())==frozen
        # Original source monetary aliases normalize, omission is preserved.
        wire=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
        wire['source_action']['input']['amount']={'minor_units':12000,'currency':'USD'}
        alias=CoordinateInput.model_validate(wire)
        assert persistence.recover(s,ctx,alias,p.binding).effect==result.effect
        with pytest.raises(BookflowError):pages.items(s,'missing','source_components',PageInput(),p.binding)
        with pytest.raises(ValueError):PageInput(limit=201)
        with pytest.raises(ValueError):PageInput(limit=0)


@pytest.mark.parametrize('table',['audit_events','audit_entries','transactions','transaction_revisions','document_lines',
    'payment_profiles','payment_components','posting_batches','posting_lines','posting_line_sources','application_allocations',
    'deposit_profiles','deposit_components','deposit_cash_cells','deposit_memberships','deposit_current_memberships',
    'bank_effect_versions','bank_effect_current','deposit_operations','deposit_operation_targets','deposit_operation_items'])
def test_each_written_family_rolls_back_and_same_key_succeeds(client,sale,driver,n2,monkeypatch,table):
    inp,*_=n2
    ctx=Context.new(Interface.python,'C transaction witness',reason='Correct captured cash')
    with driver.session() as s:
        s.company.raw.execute('CREATE TABLE own_c_sentinel(id INTEGER PRIMARY KEY, value TEXT)')
        s.company.raw.execute("INSERT INTO own_c_sentinel VALUES(1,'caller work')")
        p=prepare(s,ctx,inp)
        baseline=tuple(s.company.raw.iterdump());hit=[]
        execute=s.company.conn.execute
        def fail(statement,*a,**kw):
            if getattr(getattr(statement,'table',None),'name',None)==table and (getattr(statement,'is_insert',False) or getattr(statement,'is_update',False) or getattr(statement,'is_delete',False)):
                hit.append(str(statement));raise RuntimeError('owned fault '+table)
            return execute(statement,*a,**kw)
        with monkeypatch.context() as patch:
            patch.setattr(s.company.conn,'execute',fail)
            with pytest.raises(RuntimeError,match='owned fault'):persistence.execute(s,ctx,p)
        assert hit and tuple(s.company.raw.iterdump())==baseline
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert s.company.raw.execute('SELECT * FROM own_c_sentinel').fetchall()==[(1,'caller work')]
        result=persistence.execute(s,ctx,p)
        assert result.new_effect and result.current.revision_bank_total==19200


def test_deferred_fk_is_detected_inside_aggregate_and_preserves_caller_sentinel(client,sale,driver,n2,monkeypatch):
    inp,*_=n2
    ctx=Context.new(Interface.python,'C deferred FK witness',reason='Correct captured cash')
    with driver.session() as s:
        s.company.raw.execute('CREATE TABLE own_c_sentinel(id INTEGER PRIMARY KEY)')
        s.company.raw.execute('INSERT INTO own_c_sentinel VALUES(7)')
        p=prepare(s,ctx,inp);baseline=tuple(s.company.raw.iterdump());hit=[]
        original=persistence._foreign_keys
        def failure(session):
            # First check proves the caller baseline. On the final check insert
            # an actual deferred header-pointer violation; no guard is disabled.
            hit.append(1)
            if len(hit)==2:
                session.company.raw.execute('UPDATE transactions SET current_revision_id=? WHERE id=?',('01ZZZZZZZZZZZZZZZZZZZZZZZZ',inp.deposit))
            return original(session)
        with monkeypatch.context() as patch:
            patch.setattr(persistence,'_foreign_keys',failure)
            with pytest.raises(BookflowError) as caught:persistence.execute(s,ctx,p)
        assert caught.value.code=='E_INTERNAL' and len(hit)==2
        assert tuple(s.company.raw.iterdump())==baseline
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert persistence.execute(s,ctx,p).new_effect


def test_both_custom_slot_insert_and_update_failures_rollback_exact_raw_state(client,sale,driver,n2,monkeypatch):
    from tests.payment_raw_evidence import table as raw_table,attachments
    from tests.test_service_sales_lifecycle import COMPANY
    inp,post,doc,payment,receipt,invoice=n2
    definition=client.run('custom-field create',dict(name='C failure field',kind='text',scopes=['payment','deposit']),company=COMPANY)['id']
    ctx=Context.new(Interface.python,'C custom fault',reason='Correct actual cash')
    def raw(s):
        return dict(tables={name:raw_table(s.company.raw,name) for name, in s.company.raw.execute("SELECT name FROM sqlite_schema WHERE type='table'")},
            ddl=s.company.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall(),attachments=attachments(s.data_root))
    for phase in ('insert','update'):
        wire=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
        wire['operation_key']='C-custom-'+phase
        wire['source_action']['input']['custom_fields']={definition:phase}
        wire['source_action']['input']['expected_custom_field_kinds']={definition:'text'}
        wire['replacement']['document']['custom_fields']={definition:phase}
        wire['replacement']['document']['expected_custom_field_kinds']={definition:'text'}
        if phase=='update':
            wire['expected_version']=2
            wire['source_action']['input']['expected_version']=3
            wire['source_action']['input']['invoice_versions'][0]['expected_version']=3
            wire['replacement']['document']['sources'][1]['expected_version']=3
        request=CoordinateInput.model_validate(wire)
        with driver.session() as s:
            prepared=prepare(s,ctx,request);baseline=raw(s);hit=[]
            original=sa.engine.Connection.execute
            def fault(connection,statement,*args,**kwargs):
                wanted=getattr(statement,'is_insert' if phase=='insert' else 'is_update',False)
                if wanted and getattr(getattr(statement,'table',None),'name',None)=='custom_field_values':
                    hit.append(True);raise RuntimeError('owned custom '+phase)
                return original(connection,statement,*args,**kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(sa.engine.Connection,'execute',fault)
                with pytest.raises(RuntimeError,match='owned custom'):persistence.execute(s,ctx,prepared)
            assert hit==[True] and raw(s)==baseline
            result=persistence.execute(s,ctx,prepared)
            assert result.changed and result.current.revision_bank_total==19200
            values=s.company.raw.execute('SELECT record_type,canonical_text FROM custom_field_values WHERE def_id=? ORDER BY record_type',(definition,)).fetchall()
            assert values==[('deposit',phase),('payment',phase)]
