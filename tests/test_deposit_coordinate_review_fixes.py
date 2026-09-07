"""Focused artifact findings; actual producer rows and explicit negative boundaries."""
import ast
import copy
import json
import subprocess
from pathlib import Path
import pytest
from bookflow import BookflowError
from bookflow.core import audit
from bookflow.core.registry import Touched
from bookflow.core.context import Context,Interface
from bookflow.core.publication import OSBinding
from bookflow.company import deposit_lifecycle as lifecycle,deposit_persistence as ordinary
from bookflow.company import deposit_persistence_validation as validation,deposit_operation_pages as pages
from bookflow.company import deposit_coordinate_persistence as persistence,payment_authority as authority
from tests.test_deposit_coordinate_persistence import n2,prepare,sale,driver
from tests.test_deposit_lifecycle import replacement

BASE='703c002945b2ccbd564d98ea20b0a0d5f690f300'


def old_validate():
    text=subprocess.check_output(['git','show',BASE+':src/bookflow/company/deposit_persistence_validation.py'],text=True)
    node=next(v for v in ast.parse(text).body if isinstance(v,ast.FunctionDef) and v.name=='validate')
    ns=dict(validation.__dict__)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<accepted ordinary validator>','exec'),ns)
    return ns['validate']


def test_ordinary_precedence_and_noeffect_match_accepted(client,sale,driver,n2,monkeypatch):
    inp,post,document,*_=n2
    ctx=Context.new(Interface.python,'ordinary precedence',reason='Confirm captured document')
    with driver.session() as s:
        body=replacement(post,document)
        for row in body['sources']:row['expected_version']=2
        wire=dict(operation_key='review-ordinary',deposit=post.current.id,expected_version=1,document=body)
        no=lifecycle.prepare(s,ctx,lifecycle.INPUTS['update'].model_validate(wire),'update')
        unchanged=ordinary.build(s,ctx,no)
        assert not unchanged['data']['changed']
        changed=copy.deepcopy(wire);changed['document']['memo']='Changed memo'
        p=lifecycle.prepare(s,ctx,lifecycle.INPUTS['update'].model_validate(changed),'update')
        bundle=ordinary.build(s,ctx,p)
        assert bundle['data']['changed']
        before=tuple(s.company.raw.iterdump())
        for fn in (old_validate(),validation.validate):
            calls=[]
            def stale(*a,**k):calls.append(True);raise BookflowError('E_PREVIEW_STALE')
            with monkeypatch.context() as patch:
                patch.setattr(validation.validation,'validate_current',stale)
                fn(s,ctx,no,unchanged)
                assert not calls # accepted no-effect path also never called it
                bad=copy.deepcopy(bundle);bad['header']['id']='wrong-owner'
                with pytest.raises(BookflowError) as e:fn(s,ctx,p,bad)
                assert e.value.code=='E_VALIDATION' and not calls
                with pytest.raises(BookflowError) as e:fn(s,ctx,p,bundle)
                assert e.value.code=='E_PREVIEW_STALE' and calls==[True]
        # A purported unchanged request with a stale actual source version is
        # rejected during ordinary preparation before any no-effect shortcut.
        wire['document']['sources'][0]['expected_version']=1
        with pytest.raises(BookflowError) as e:
            lifecycle.prepare(s,ctx,lifecycle.INPUTS['update'].model_validate(wire),'update')
        assert e.value.code in ('E_VERSION_CONFLICT','E_PREVIEW_STALE')
        assert tuple(s.company.raw.iterdump())==before


def test_historical_single_touch_gains_complete_root_and_current_gate(client,sale,driver,n2,monkeypatch):
    assert authority._COORDINATE_TARGETS=={
        'deposit_profile':('deposit_profiles','revision_id'), 'deposit_row_key':('deposit_row_keys','id'),
        'deposit_component_key':('deposit_component_keys','id'), 'deposit_component':('deposit_components','id'),
        'deposit_cash_cell':('deposit_cash_cells','id'), 'deposit_membership':('deposit_memberships','id'),
        'bank_effect_key':('bank_effect_keys','id'), 'bank_effect_version':('bank_effect_versions','id'),
        'work_billing_allocation':('work_billing_allocations','id'), 'sales_profile':('sales_profiles','revision_id'),
        'sales_line_profile':('sales_line_profiles','document_line_id'), 'sales_tax_component':('sales_tax_components','id'),
        'sales_tax_line_key':('sales_tax_line_keys','line_id'), 'sales_tax_attribution':('sales_tax_attributions','revision_id'),
        'sales_tax_attribution_line':('sales_tax_attribution_lines','document_line_id')}
    _,post,_,payment,*_=n2
    ctx=Context.new(Interface.python,'historical owner witness')
    with driver.session() as s:
        key=s.company.raw.execute("SELECT id FROM deposit_memberships WHERE transaction_id=? AND source_transaction_id=? AND kind='claim'",(post.current.id,payment['id'])).fetchone()[0]
        # Existing audit writer and an existing pre-coordinate membership owner;
        # no coordinate event/receipt is involved in this historical occurrence.
        event=audit.write_event_to(s.company,ctx,'historical owner inspection','historical owner',
            [Touched('deposit_membership',key,'update',None,None,{},before={})],actor_id=s.actor.id,actor_kind=s.actor.kind)
        before=tuple(s.company.raw.iterdump())
        with monkeypatch.context() as patch:
            patch.setattr(authority,'_EVIDENCE_TARGETS',authority.PAYMENT_TARGETS)
            assert authority.record_transactions(s.company,'deposit_membership',key)==set()
            assert authority.event_requirements(s.company,event)==()
        assert authority.record_transactions(s.company,'deposit_membership',key)=={post.current.id,payment['id']}
        assert authority.event_requirements(s.company,event)==(('ledger.read','member'),)
        assert authority._EventCohort(s.company,[event]).requirements(event)==(('ledger.read','member'),)
        authority.authorize_event(s,event) # actual current session permission allows
        actual=authority.require_resource
        def denied(session,resource,role):
            actual(session,resource,role)
            assert (resource,role)==('ledger.read','member')
            raise BookflowError('E_PERMISSION',details={})
        with monkeypatch.context() as patch:
            patch.setattr(authority,'require_resource',denied) # explicit denied resource fact, not new policy
            for authorize in (lambda:authority.authorize_event(s,event),lambda:authority.authorize_events(s,[event])):
                with pytest.raises(BookflowError) as e:authorize()
                assert e.value.code=='E_PERMISSION'
        assert tuple(s.company.raw.iterdump())==before


def test_saved_corruption_denial_first_and_audited_adapter(client,sale,driver,n2,monkeypatch):
    from bookflow.company import deposit_operations,deposit_dependency_history as history
    inp,*_=n2;ctx=Context.new(Interface.python,'private audited adapter',reason='Correct cash')
    with driver.session() as s:
        count=s.company.raw.execute('SELECT count(*) FROM audit_events').fetchone()[0]
        p=prepare(s,ctx,inp);applied=persistence.execute_applied(s,ctx,p)
        assert applied.audited and not applied.finalized and applied.touched==[]
        assert s.company.raw.execute('SELECT count(*) FROM audit_events').fetchone()[0]==count+1
        replay=persistence.execute_applied(s,ctx,p)
        assert replay.output.idempotent_replay and replay.audited and not replay.finalized
        assert s.company.raw.execute('SELECT count(*) FROM audit_events').fetchone()[0]==count+1
        saved=deposit_operations.find(s,inp.operation_key);before=tuple(s.company.raw.iterdump())
        for corrupt in ('{',json.dumps({'resolved_transaction_ids':[]})):
            bad=dict(saved,request_snapshot=corrupt)
            def deny(*a,**kw):raise BookflowError('E_PERMISSION',details={'hidden':'never emit'})
            with monkeypatch.context() as patch:
                patch.setattr(history,'_authorize_binding_graph',deny)
                with pytest.raises(BookflowError) as e:pages.authorized_original(s,bad,p.binding)
                assert e.value.code=='E_PERMISSION' and e.value.details=={}
            with pytest.raises(BookflowError) as e:pages.authorized_original(s,bad,p.binding)
            assert e.value.code=='E_INTERNAL'
        # A damaged index cannot hide an unreadable root still present in the
        # immutable request. Denial precedes the index-completeness diagnostic.
        target=inp.source_action.input.payment
        original_rows=pages.rows.rows;original_admit=history._authorize_binding_graph
        def incomplete(session,table,*where):
            values=original_rows(session,table,*where)
            return [v for v in values if v['transaction_id']!=target] if table is pages.c.deposit_operation_targets else values
        seen=[]
        def deny_saved(session,binding,ids,**kw):
            seen.append(set(ids))
            if target in ids:raise BookflowError('E_PERMISSION',details={})
            return original_admit(session,binding,ids,**kw)
        with monkeypatch.context() as patch:
            patch.setattr(pages.rows,'rows',incomplete)
            patch.setattr(history,'_authorize_binding_graph',deny_saved)
            for request_snapshot in (saved['request_snapshot'],'{',json.dumps({'resolved_transaction_ids':[]})):
                seen.clear()
                with pytest.raises(BookflowError) as e:pages.authorized_original(s,dict(saved,request_snapshot=request_snapshot),p.binding)
                assert e.value.code=='E_PERMISSION' and e.value.details=={}
                assert target not in seen[0] and target in seen[1]
        assert tuple(s.company.raw.iterdump())==before


def test_noeffect_prepared_before_real_unapply_is_stale_without_writes(client,sale,driver,n2):
    inp,post,document,payment,receipt,invoice=n2
    ctx=Context.new(Interface.python,'ordinary no-effect stale',reason='Confirm unchanged deposit')
    body=replacement(post,document)
    for row in body['sources']:row['expected_version']=2
    request=lifecycle.INPUTS['update'].model_validate(dict(operation_key='review-stale-noop',deposit=post.current.id,expected_version=1,document=body))
    with driver.session() as s:
        plan=lifecycle.prepare(s,ctx,request,'update')
        request=request.model_copy(update={'dependency_guard':plan.dependency_guard})
        plan=lifecycle.prepare(s,ctx,request,'update')
        assert not ordinary.build(s,ctx,plan)['data']['changed']
        application=s.company.raw.execute("SELECT id FROM applications WHERE paying_transaction_id=? AND kind='apply'",(payment['id'],)).fetchone()[0]
    client.run('payment unapply',dict(payment=payment['id'],expected_version=2,operation_key='review-unapply',
        applications=[dict(application_id=application,invoice_expected_version=2)]),company='Demo Plumbing Co',reason='Explicit supported unapply')
    before=driver.dump()
    with driver.session() as s:
        with pytest.raises(BookflowError) as e:ordinary.execute(s,ctx,plan)
        assert e.value.code=='E_PREVIEW_STALE'
        assert s.company.raw.execute("SELECT count(*) FROM deposit_operations WHERE operation_key='review-stale-noop'").fetchone()[0]==0
    assert driver.dump()==before
