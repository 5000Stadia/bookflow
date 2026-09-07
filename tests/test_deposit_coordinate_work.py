"""Actual linked-work/tax/custom-field source persistence and immutable evidence."""
import json
import pytest
from bookflow.core.context import Context,Interface
from bookflow.company import deposit_coordinate_persistence as persistence, deposit_operation_pages as pages
from bookflow.company.deposit_coordinate_models import CoordinateInput
from bookflow.company.deposit_dependency_models import PageInput
from tests.test_deposit_coordinate_persistence import prepare
from tests.test_deposit_lifecycle import driver,additional_document,replacement
from tests.test_tax_policy_sales import tax_sale,request as tax_request
from tests.test_work_billing_lifecycle import accepted,bill
from tests.test_deposit_sources import uf
from tests.test_service_sales_lifecycle import COMPANY, sale


def test_complete_work_tax_and_five_custom_kinds_persist_and_page(client,tax_sale,driver,monkeypatch):
    work=accepted(client,tax_sale,**tax_request(tax_sale))
    receipt=bill(client,work,verb='sales-receipt',deposit_to=uf(client),payment_method='Check',amount_received='0.22')
    doc=additional_document(client,tax_sale,'1')
    doc['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    deposited=driver.run('post',dict(operation_key='C-work-post',document=doc))
    values={'text':'01AAAAAAAAAAAAAAAAAAAAAAAA','number':'123456789.123456789','date':'2026-06-01','bool':False,'choice':'Web'}
    fields={kind:client.run('custom-field create',dict(name='C source '+kind,kind=kind,scopes=['sales_receipt','deposit'],
        **({'choices':[{'value':'Web'},{'value':'Phone'}]} if kind=='choice' else {})),company=COMPANY) for kind in values}
    body=replacement(deposited,doc);body['sources']=[dict(source_result=True,source=receipt['id'])]
    body['custom_fields']={fields[k]['id']:v for k,v in values.items()}
    body['expected_custom_field_kinds']={fields[k]['id']:k for k in values}
    source=dict(sales_receipt=receipt['id'],expected_version=2,amount_received='0.33',
        lines=[dict(line_id=receipt['revision']['lines'][0]['line_id'],item=tax_sale['item']),
               dict(item=tax_sale['item'],net_amount='0.20',tax_code=tax_sale['taxable'])],
        custom_fields={fields[k]['id']:v for k,v in values.items()},custom_field_kinds={fields[k]['id']:k for k in values})
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='C-work-coordinate',
        source_action=dict(kind='sales_receipt_update',input=source),replacement=dict(mode='document',document=body))
    ctx=Context.new(Interface.python,'C work evidence',reason='Retain work and captured custom facts')
    with driver.session() as s:
        p=prepare(s,ctx,inp)
        original=s.company.raw.execute('SELECT source_basis_hash,denominator_hex,spans_json FROM work_billing_allocations WHERE transaction_id=?',(receipt['id'],)).fetchall()
        from dataclasses import replace
        import copy
        from bookflow import BookflowError
        from bookflow.company import deposit_coordinate_validation as validation
        bundle=persistence.build(s,ctx,p)
        assert bundle.source_rows.work_billing_allocations and bundle.deposit_bundle['pending']['deposit_component_keys']
        baseline=tuple(s.company.raw.iterdump())
        damaged=copy.deepcopy(bundle.deposit_bundle)
        damaged['pending']['deposit_component_keys'][0]['semantic_identity']='wrong-owned-occurrence'
        for broken in (replace(bundle,deposit_bundle=damaged),replace(bundle,source_rows=bundle.source_rows.model_copy(update={'work_billing_allocations':()}))):
            with pytest.raises(BookflowError):validation.validate(s,ctx,broken)
            assert tuple(s.company.raw.iterdump())==baseline

        result=persistence.execute(s,ctx,p)
        assert result.current.revision_bank_total==133
        inserted=result.effect.source.inserted
        assert len(inserted.work_billing_allocations)==1
        carried=inserted.work_billing_allocations[0]
        assert (carried.source_basis_hash,carried.denominator_hex,carried.spans_json)==original[0]
        assert carried.source_document_id==work['id'] and carried.transaction_id==receipt['id']
        assert len(result.effect.source.custom_changes)==5
        for identity in (receipt['id'],deposited.current.id):
            revision=s.company.raw.execute('SELECT r.custom_fields_snapshot FROM transaction_revisions r JOIN transactions t ON t.current_revision_id=r.id WHERE t.id=?',(identity,)).fetchone()[0]
            snapshots=json.loads(revision)
            assert {k:snapshots[f['id']]['value'] for k,f in fields.items()}==values
        from bookflow.company import payment_authority as evidence
        cohort=evidence._EventCohort(s.company,[result.effect.deposit.audit_event_id])
        assert cohort.requirements(result.effect.deposit.audit_event_id)==(('ledger.read','member'),('customer-work','member'))
        for kind,key in s.company.raw.execute('SELECT record_type,record_id FROM audit_entries WHERE event_id=?',(result.effect.deposit.audit_event_id,)):
            if kind in evidence._COORDINATE_TARGETS:
                scalar=evidence.record_transactions(s.company,kind,key)
                assert scalar and scalar<=set(result.effect.target_ids)
                assert cohort._walk((kind,key))==scalar
        expected=pages.collections(result)['source_document_changes']
        assert sum(v['kind']=='custom' for v in expected)==5
        assert sum(v['kind']=='work' for v in expected)==1
        page=pages.items(s,inp.operation_key,'source_document_changes',PageInput(limit=200),p.binding)
        assert page.model_dump(mode='json')['items']==expected
        final=tuple(s.company.raw.iterdump())
        assert persistence.recover(s,ctx,inp,p.binding).effect==result.effect
        assert tuple(s.company.raw.iterdump())==final
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        # Current resource denial is injected only at the existing owned gate;
        # the source, work link, saved target lookup and ordering are all real.
        from bookflow import BookflowError
        from bookflow.company import payment_authority as authority,deposit_operations
        original_require=authority.require_resource
        checks=[]
        def deny_work(session,resource,role):
            checks.append((resource,role))
            if resource=='customer-work':raise BookflowError('E_PERMISSION',details={'private_fixture':'never disclose'})
            return original_require(session,resource,role)
        def forbidden_hash(*args,**kwargs):raise AssertionError('saved denial must precede intent comparison')
        with monkeypatch.context() as patch:
            patch.setattr(authority,'require_resource',deny_work)
            patch.setattr(deposit_operations,'request',forbidden_hash)
            for candidate in (inp,inp.model_copy(update={'expected_version':99})):
                with pytest.raises(BookflowError) as denied:persistence.recover(s,ctx,candidate,p.binding)
                assert denied.value.code=='E_PERMISSION' and denied.value.details=={}
            with pytest.raises(BookflowError) as denied:pages.items(s,inp.operation_key,'source_document_changes',PageInput(limit=1),p.binding)
            assert denied.value.code=='E_PERMISSION' and denied.value.details=={}
        assert ('customer-work','standard') in checks and ('customer-work','member') in checks
        assert tuple(s.company.raw.iterdump())==final
