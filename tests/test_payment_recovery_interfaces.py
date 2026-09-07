"""All eleven recovery commands through Python, actual CLI, HTTP and MCP tools."""
from pathlib import Path
import json
import anyio
import pytest
from tests.mcp_matrix_support import Matrix
from tests.test_service_sales_lifecycle import sale
from tests.test_payment_recovery import setup,declaration
from tests.payment_recovery_support import launcher,provenance,source_environment

@pytest.mark.timeout(600)
def test_complete_recovery_contract_on_all_four_interfaces(client,sale,root,tmp_path,monkeypatch):
    draft,first,_=setup(client,sale)
    binary=launcher(monkeypatch)
    (tmp_path/'source-provenance.json').write_text(json.dumps(provenance(binary),indent=2))
    async def witness():
        matrix=Matrix()
        try:
            await matrix.open(root,tmp_path,mcp_env=source_environment())
            for surface in matrix.documents:
                async def call(verb,inp,**context):
                    return await matrix.call(surface,'payment recovery '+verb,inp,**context)
                edits=[dict(invoice_id=first['id'],observed_invoice_version=1,action='remove')]
                begin=declaration(draft,edits)
                begun=await call('begin',begin);old=begun['original_receipt']['recovery_id']
                assert begun['current']['state']=='recovery_uploading'
                assert (await call('items',dict(recovery_id=old,kind='missing_ranges')))['total_count']==1
                await call('upload',dict(recovery_id=old,chunk_index=0,entries=edits))
                entries=[dict(invoice_id=first['id'],observed_invoice_version=1,action='set',amount_minor_units=50,currency='USD',amount_origin='entered')]
                replacement=declaration(draft,entries)
                replace=dict(recovery_id=old,expected_recovery_version=2,replacement=replacement)
                replaced=await call('replace',replace)
                identifier=replaced['original_receipt']['replacement_recovery_id']
                assert (await call('replace',replace))['original_receipt']==replaced['original_receipt']
                assert (await call('show',dict(recovery_key=begin['recovery_key'])))['state']=='superseded'
                await call('upload',dict(recovery_id=identifier,chunk_index=0,entries=entries))
                await call('seal',dict(recovery_id=identifier,expected_recovery_version=2))
                comparison_input=dict(recovery_id=identifier,attempt_generation=replacement['attempt_generation'],intent_hash=replacement['intent_hash'])
                comparison=await call('compare',comparison_input)
                assert (comparison['amount_minor_units'],comparison['selected_minor_units'],comparison['unapplied_minor_units'])==(200,150,50)
                page_input=dict(**comparison_input,facts_fingerprint=comparison['facts_fingerprint'],kind='changes',limit=1)
                page=await call('compare-items',page_input)
                assert page['total_count']==2 and page['next_cursor']
                second=await call('compare-items',dict(page_input,cursor=page['next_cursor']))
                assert second['next_cursor'] is None and second['items'][0]['invoice_id']!=page['items'][0]['invoice_id']
                apply=dict(**comparison_input,expected_recovery_version=3,expected_selection_version=draft['version'],expected_facts_fingerprint=comparison['facts_fingerprint'])
                preview=await call('apply',apply,dry_run=True)
                assert preview['comparison']['selected_minor_units']==150 and preview['original_receipt']['revision_id'] is None
                applied=await call('apply',apply)
                assert applied['current']['selection_version']==draft['version']+1
                assert (await call('apply',apply))['original_receipt']==applied['original_receipt']
                current=await matrix.call(surface,'payment selection show',dict(selection=draft['id']))
                abandoned_begin=declaration(current,[])
                abandoned=await call('begin',abandoned_begin)
                abandon=dict(recovery_id=abandoned['original_receipt']['recovery_id'],expected_recovery_version=1,disposition='discard_entire_attempt')
                aborted=await call('abort',abandon)
                assert aborted['current']['selection_version']==draft['version']+2
                assert (await call('abort',abandon))['original_receipt']==aborted['original_receipt']
                assert (await call('query',dict(selection=draft['id'])))['total_count']==3
                assert (await call('begin',begin))['original_receipt']==begun['original_receipt']
                seen={name for name,_ in matrix.documents[surface]}
                assert {'payment recovery '+verb for verb in ('begin','upload','seal','compare','compare-items','apply','abort','replace','show','items','query')}<=seen
            (tmp_path/'interfaces.json').write_text(json.dumps(matrix.documents,indent=2))
        finally:
            await matrix.close()
    anyio.run(witness)


@pytest.mark.timeout(600)
def test_full_403_201_barrier_and_single_receipt_on_every_adapter(client,sale,root,tmp_path,monkeypatch):
    from tests.test_payment_selection import invoice
    from tests.test_payment_recovery import call,raw_books
    from tests.test_service_sales_lifecycle import COMPANY
    invoices=[invoice(client,sale,'SURFACE-403-'+str(i)) for i in range(403)]
    draft=client.run('payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-01',amount='10.00'),company=COMPANY)
    for offset in range(0,403,200):
        draft=client.run('payment selection update',dict(selection=draft['id'],expected_version=draft['version'],set_items=[dict(invoice=r['id'],expected_version=1,amount='0.01',amount_origin='entered') for r in invoices[offset:offset+200]]),company=COMPANY)
    entries=sorted([dict(invoice_id=r['id'],observed_invoice_version=1,action='set',amount_minor_units=2,currency='USD',amount_origin='entered') for r in invoices[:201]],key=lambda r:r['invoice_id'])
    begin=declaration(draft,entries);identifier=call(client,'begin',begin)['original_receipt']['recovery_id']
    call(client,'upload',dict(recovery_id=identifier,chunk_index=0,entries=entries[:200]))
    method=client.run('payment-method list',{},company=COMPANY)['items'][0]['id']
    binary=launcher(monkeypatch)
    (tmp_path/'source-provenance.json').write_text(json.dumps(provenance(binary),indent=2))
    async def witness():
        matrix=Matrix()
        try:
            await matrix.open(root,tmp_path,mcp_env=source_environment())
            for surface in matrix.documents:
                async def recovery(verb,inp,**context):return await matrix.call(surface,'payment recovery '+verb,inp,**context)
                receive=dict(customer=sale['customer'],date='2026-06-01',amount='10.00',payment_method=method,operation_key='403-201-'+surface,applications=dict(mode='selection',selection=draft['id'],expected_version=draft['version']))
                before=raw_books(matrix.roots[surface])
                for command,inp in [('payment selection clear',dict(selection=draft['id'],expected_version=draft['version'])),('payment receive',receive)]:
                    denied=await matrix.call(surface,command,inp,rejected=True)
                    assert denied['code']=='E_RECOVERY_PENDING'
                    assert raw_books(matrix.roots[surface])==before
                state=await recovery('show',dict(recovery_id=identifier))
                assert state['received_entry_count']==200 and state['declared_entry_count']==201 and state['missing_chunk_count']==1
                await recovery('upload',dict(recovery_id=identifier,chunk_index=1,entries=entries[200:]))
                await recovery('seal',dict(recovery_id=identifier,expected_recovery_version=3))
                request=dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'])
                comparison=await recovery('compare',request)
                assert (comparison['item_count'],comparison['selected_minor_units'],comparison['unapplied_minor_units'])==(403,604,396)
                await recovery('apply',dict(**request,expected_recovery_version=4,expected_selection_version=draft['version'],expected_facts_fingerprint=comparison['facts_fingerprint']))
                current=await matrix.call(surface,'payment selection show',dict(selection=draft['id']))
                assert current['version']==draft['version']+1 and current['id']==draft['id']
                receive['applications']['expected_version']=current['version']
                original=await matrix.call(surface,'payment receive',receive)
                assert (await matrix.call(surface,'payment receive',receive))['id']==original['id']
                assert (await matrix.call(surface,'payment query',dict(customer=sale['customer'])))['total_count']==1
            (tmp_path/'403-201-interface-barriers.json').write_text(json.dumps(matrix.documents,indent=2))
        finally:await matrix.close()
    anyio.run(witness)
