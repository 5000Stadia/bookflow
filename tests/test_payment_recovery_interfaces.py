"""All eleven recovery commands through Python, actual CLI, HTTP and MCP tools."""
from pathlib import Path
import json
import anyio
import pytest
from tests.mcp_matrix_support import Matrix
from tests.test_service_sales_lifecycle import sale
from tests.test_payment_recovery import setup,declaration

@pytest.mark.timeout(600)
def test_complete_recovery_contract_on_all_four_interfaces(client,sale,root,tmp_path,monkeypatch):
    draft,first,_=setup(client,sale)
    binary=Path(__file__).parents[1]/'.cache/recovery/bin/bookflow'
    monkeypatch.setenv('BOOKFLOW_MCP_TEST_BINARY',str(binary))
    import tests.conftest
    monkeypatch.setattr(tests.conftest,'BIN',binary)
    async def witness():
        matrix=Matrix()
        try:
            await matrix.open(root,tmp_path)
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
