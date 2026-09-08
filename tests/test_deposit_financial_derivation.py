"""Stored producer facts through the company-only seam and unchanged gates.

Order controls describe this extraction, not a permanent product ordering policy.
"""
import copy
import json
from dataclasses import fields

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import deposit_financial_derivation as d
from bookflow.company import deposit_drafts as drafts, deposit_sources as sources
from bookflow.company import deposit_read_facts as facts, deposit_operation_pages as opages
from bookflow.company import deposit_dependency_history as history, schema as c
from bookflow.core.publication import OSBinding
from tests.test_deposit_draft_history import world, cash, driver, run, sale
from tests.test_deposit_draft_financial import run_private, make_posted
from tests.test_service_sales_lifecycle import COMPANY
from tests.test_tax_policy_sales import tax_sale, request as tax_request
from tests.test_deposit_sources import uf
from tests.test_payment_receipts import method


def error_value(call):
    with pytest.raises(Exception) as caught:
        call()
    error=caught.value
    return type(error),str(error),getattr(error,'code',None),getattr(error,'details',None)


def direct_draft(read, session, identity, kind='draft'):
    start=d.begin_draft(read,identity,kind=kind)
    pending=d.begin_revision(read,start.header,start.revision,kind=kind)
    graphs=sources.graph_many(session,[r.source.transaction_id for r in pending.manifest.sources])
    manifest=d.finish_revision(read,pending,graphs)
    if kind=='draft':
        keys=list(read.company.conn.execute(sa.select(c.deposit_draft_row_keys).where(c.deposit_draft_row_keys.c.draft_id==identity)).mappings())
        d.require_row_origins(read,start.header,keys)
    return start.header,start.revision,manifest


def assert_root(session, identity):
    binding=OSBinding.from_session(session)
    loaded=facts.load_complete(session,[identity],binding=binding)[0]
    read=d.CompanyFacts(d.CompanyConnection(session.company.conn))
    root=d.derive_root(read,copy.deepcopy(loaded.header),copy.deepcopy(loaded.graph),
        {key:copy.deepcopy(value[0]) for key,value in loaded.sources.items()}, {},
        company_info_id=session.company_info_row['id'],reader=history.History(read))
    assert root.selected==loaded.selected and root.effects==loaded.effects
    assert root.revisions==tuple(sorted(loaded.graph['transaction_revisions'],key=lambda r:r['revision_number']))
    assert {f.name for f in fields(root)}=={'selected','effects','revisions'}
    assert {f.name for f in fields(read)}=={'company'}
    assert {f.name for f in fields(read.company)}=={'conn'}
    return loaded,read


def test_real_payment_copy_selection_and_malformed_endpoints(world,driver):
    with driver.session() as s:
        before=tuple(s.company.raw.iterdump());binding=OSBinding.from_session(s)
        loaded,read=assert_root(s,world['posted'].current.id)
        assert {r.source.source_type for e in loaded.effects.values() for r in e.intent.sources}=={'payment','sales_receipt'}
        for key,kind in (('edit','draft'),('copy','draft'),('selection','selection')):
            identity=world[key].id
            wrapped=drafts.load(s,identity,kind=kind,binding=binding)
            assert wrapped[3] is binding
            assert direct_draft(read,s,identity,kind)==wrapped[:3]
        for number in (999,):
            assert error_value(lambda:d.begin_draft(read,world['copy'].id,number))==error_value(
                lambda:drafts.load(s,world['copy'].id,number,binding=binding))
        # Decode the same damaged stored image at both entrances, without writing
        # through the readonly producer session.
        h,r,_=direct_draft(read,s,world['copy'].id)
        damaged=dict(r,snapshot='{}')
        from bookflow.company import deposit_draft_validation as validation
        assert error_value(lambda:d.begin_revision(read,h,damaged,kind='draft'))==error_value(
            lambda:validation.decode_revision(s,h,damaged,'draft'))
        assert tuple(s.company.raw.iterdump())==before


def test_tax_custom_and_consumed_full_returns(client,tax_sale,run_private):
    values={'text':'Captured','number':'1.000000001','date':'2026-06-03','bool':False,'choice':'Alpha'}
    definitions={kind:client.run('custom-field create',dict(name='Derivation '+kind,kind=kind,
        scopes=['sales_receipt','deposit'],default=value,
        **({'choices':[dict(value='Alpha'),dict(value='Beta')]} if kind=='choice' else {})),company=COMPANY)['id'] for kind,value in values.items()}
    receipt=client.run('sales-receipt post',dict(tax_request(tax_sale,nets=('10.00','20.00')),deposit_to=uf(client),payment_method=method(client)),company=COMPANY)
    source=dict(source_type='sales_receipt',source=receipt['id'],expected_version=receipt['version'])
    posted,draft=make_posted(client,source,run_private)
    def check(s,ctx):
        from bookflow.company import deposit_draft_consumption as consumption
        before=tuple(s.company.raw.iterdump());binding=OSBinding.from_session(s)
        loaded,read=assert_root(s,posted.current.id)
        selected=loaded.selected[1]
        assert {v.captured.kind:v.captured.value for v in selected.custom_fields}==values
        src=loaded.effects[selected.pin.revision_id].intent.sources[0].source
        assert any(component.key.kind=='sale_tax' and component.tax is not None for component in src.components)
        h,r,m,b=drafts.load(s,draft.id,binding=binding)
        assert b is binding and direct_draft(read,s,draft.id)==(h,r,m)
        assert {v.kind:v.canonical_text for v in m.header.custom_fields.values()}.keys()==values.keys()
        d.require_consumed(read,h,r)
        assert consumption.current(s,posted.operation_id,binding=binding)==d.consumed_state(posted.operation_id,h,r)
        saved=dict(s.company.conn.execute(sa.select(c.deposit_operations).where(c.deposit_operations.c.id==posted.operation_id)).mappings().one())
        targets=tuple(sorted(s.company.conn.execute(sa.select(c.deposit_operation_targets.c.transaction_id).where(c.deposit_operation_targets.c.operation_id==posted.operation_id)).scalars()))
        assert opages.authorized_original(s,saved,binding)==opages.require_original(saved,targets,opages.decode_original(saved))
        broken=dict(saved,effect_snapshot='{}')
        assert error_value(lambda:opages.authorized_original(s,broken,binding))==error_value(lambda:opages.require_original(broken,targets,opages.decode_original(broken)))
        assert tuple(s.company.raw.iterdump())==before
    run_private(check)


def test_operation_denial_precedes_deferred_decode_error(world,driver,monkeypatch,client,sale):
    account=world['posted'].effect.financial.intent.additional[0].account.id
    bank=client.account.create(name='Derivation disjoint bank',type='bank',company=COMPANY)['id']
    document=dict(mode='inline',date='2026-06-03',deposit_to=bank,additional=[dict(
        received_from=dict(kind='customer',id=sale['customer']),from_account=account,amount='1')])
    independent=driver.run('post',dict(operation_key='derivation-disjoint',document=document))
    with driver.session() as s:
        b=OSBinding.from_session(s)
        saved=dict(s.company.conn.execute(sa.select(c.deposit_operations).where(c.deposit_operations.c.id==world['posted'].operation_id)).mappings().one())
        # Actual disjoint root remains recoverable even with invalid output.
        request=json.loads(saved['request_snapshot'])
        request['resolved_transaction_ids'].append(independent.current.id)
        saved=dict(saved,request_snapshot=json.dumps(request),effect_snapshot='{}')
        calls=[];original=history._authorize_binding_graph;decode=opages.decode_original
        def gate(*args,**kwargs):
            calls.append('gate')
            if calls.count('gate')==2:raise BookflowError('E_PERMISSION',details={'private':'redacted'})
            return original(*args,**kwargs)
        def decoding(value):
            calls.append('decode');return decode(value)
        with monkeypatch.context() as patch:
            patch.setattr(history,'_authorize_binding_graph',gate)
            patch.setattr(opages,'decode_original',decoding)
            failed=error_value(lambda:opages.authorized_original(s,saved,b))
        assert calls==['gate','decode','gate']
        assert failed[2:] == ('E_PERMISSION',{})
        assert error_value(lambda:opages.authorized_original(s,saved,b))[2]=='E_INTERNAL'


def test_draft_gate_and_manifest_failure_precede_source_admission(world,driver,monkeypatch):
    with driver.session() as s:
        b=OSBinding.from_session(s);calls=[]
        gate=drafts.v.admit;graph=sources.graph_many
        failure=BookflowError('E_VALIDATION',details={'reason':'manifest_hash'})
        def admitted(*args,**kwargs):calls.append('admit');return gate(*args,**kwargs)
        def invalid(*args,**kwargs):calls.append('manifest');raise failure
        def source(*args,**kwargs):calls.append('source');return graph(*args,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(drafts.v,'admit',admitted);patch.setattr(d,'begin_revision',invalid)
            patch.setattr(sources,'graph_many',source)
            with pytest.raises(BookflowError) as caught:drafts.load(s,world['copy'].id,binding=b)
            assert caught.value is failure
        assert calls==['admit','manifest']


@pytest.mark.parametrize('entrance',[
    lambda r:d.begin_draft(r,'unused'),
    lambda r:d.begin_revision(r,{}, {},kind='draft'),
    lambda r:d.finish_revision(r,None,{}),
    lambda r:d.require_row_origins(r,{},[]),
    lambda r:d.require_consumed(r,{},{}),
    lambda r:d.derive_root(r,{}, {}, {}, {},company_info_id='unused',reader=None),
])
def test_derivation_rejects_session_shaped_objects_before_reads(entrance):
    class SessionShape:
        @property
        def company(self):raise AssertionError('Session must not be inspected')
    with pytest.raises(TypeError,match='requires CompanyFacts'):entrance(SessionShape())
