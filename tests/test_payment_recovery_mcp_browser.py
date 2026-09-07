"""Actual MCP agent → human Chrome → same MCP session continuation on one company."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
import anyio
import pytest
from bookflow.core import clock
from bookflow.core.config import Config
from bookflow.hub import schema as h
from tests.conftest import make_actor
from tests.test_row7_credentials import writer
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser
from tests.test_payment_review_gui import setup,invoice_setup
from tests.test_customer_payment_browser import click,field,shot
from tests.test_payment_recovery_browser import press
from tests.payment_recovery_support import launcher,provenance,source_environment

@pytest.mark.timeout(420)
@pytest.mark.parametrize('width',[1280,390])
def test_actual_mcp_recovery_human_confirmation_and_original_operation(register_browser,tmp_path,width):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters,stdio_client
    b,run,payer,_,base=setup(register_browser);b.viewport(width,900)
    invoice,_,_=invoice_setup(run,payer)
    method=b.evaluate("document.querySelector('#payment-method').value")
    root=Path(os.environ['BOOKFLOW_DATA_ROOT'])
    principal=Config.load(root/'config.toml').user_table(register_browser.site.login)['user_id']
    agent=make_actor(root,'recovery-mcp-agent',kind='agent',owner_user_id=principal,company_role=(register_browser.site.company_id,'owner'))
    with writer(root) as db:
        db.conn.execute(h.agent_authority.insert().values(agent_user_id=agent,epoch=1))
        db.conn.execute(h.agent_principals.insert().values(agent_user_id=agent,principal_user_id=principal,assigned_by=principal,assigned_at=clock.now_iso()))
    issuance=b.evaluate("fetch('/commands/token.issue',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1'},body:JSON.stringify("+json.dumps(dict(user=agent,principal=principal,label='Disposable recovery MCP'))+")}).then(r=>r.json())",await_promise=True)
    calls=[]
    binary=launcher()
    (tmp_path/'source-provenance.json').write_text(json.dumps(provenance(binary),indent=2))
    async def witness():
        params=StdioServerParameters(command=str(binary),
            args=['mcp','--url',register_browser.site.base_url],env={**source_environment(),'BOOKFLOW_TOKEN':issuance['secret'],'BOOKFLOW_COMPANY':register_browser.site.company_id,
                'BOOKFLOW_DATA_ROOT':str(tmp_path/'absent')},cwd=str(tmp_path))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.discover()
                async def tool(name,args):
                    result=await session.call_tool(name,args)
                    assert not result.is_error,result.structured_content
                    return result.structured_content
                catalog=await tool('bookflow_list_commands',dict(prefix='payment recovery',limit=200))
                assert len(catalog['commands'])==11
                for verb in ('begin','upload','seal','compare','compare-items','apply','abort','replace','show','items','query'):
                    help_=await tool('bookflow_help',dict(command='payment recovery '+verb))
                    assert help_['input_schema']['type']=='object'
                async def call(name,data,write=False):
                    result=await tool('bookflow_run',dict(command=name,input=data,**({'reason':'Recover the complete intended selection'} if write else {})))
                    calls.append(dict(command=name,input=data,output=result));return result
                draft=await call('payment selection create',dict(mode='new_receipt',customer=payer,date='2026-06-02',amount='1.50'),True)
                draft=await call('payment selection update',dict(selection=draft['id'],expected_version=draft['version'],
                    set_items=[dict(invoice=invoice['id'],expected_version=1,amount='1.00',amount_origin='entered')]),True)
                edits=[dict(invoice_id=invoice['id'],observed_invoice_version=1,action='set',amount_minor_units=50,currency='USD',amount_origin='entered')]
                generation=str(uuid4())
                manifest=dict(domain='bookflow.payment.recovery.intent',format=1,selection=draft['id'],local_baseline_revision=draft['revision_id'],anchor_revision=draft['revision_id'],
                    attempt_generation=generation,header_intent=dict(action='keep'),entries=edits)
                digest=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
                begin=dict(recovery_key='MCP-'+generation,selection=draft['id'],expected_version=draft['version'],local_baseline_revision=draft['revision_id'],
                    attempt_generation=generation,declared_entry_count=1,intent_hash=digest,header_intent=dict(action='keep'))
                begun=await call('payment recovery begin',begin,True);identifier=begun['original_receipt']['recovery_id']
                assert begun['original_receipt']['actor_id']==agent
                await call('payment recovery items',dict(recovery_id=identifier,kind='missing_ranges'))
                await call('payment recovery upload',dict(recovery_id=identifier,chunk_index=0,entries=edits),True)
                await call('payment recovery seal',dict(recovery_id=identifier,expected_recovery_version=2),True)
                comparison=await call('payment recovery compare',dict(recovery_id=identifier,attempt_generation=generation,intent_hash=digest))
                await call('payment recovery compare-items',dict(recovery_id=identifier,attempt_generation=generation,intent_hash=digest,facts_fingerprint=comparison['facts_fingerprint']))
                b.navigate(base+'/receive-payments?selection='+draft['id'])
                b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=120)
                assert '50' in b.evaluate("document.querySelector('#payment-recovery-panel').innerText")
                shot(b,tmp_path,'agent-attempt-human-comparison',width)
                press(b,'Confirm complete recovery')
                current=await call('payment recovery show',dict(recovery_id=identifier))
                assert current['state']=='applied' and current['terminal_receipt']['actor_id']==principal
                assert current['begin_receipt']==begun['original_receipt']
                field(b,'method',method);field(b,'destination',register_browser.bank['id'])
                click(b,'preview');click(b,'save-new')
                assert b.evaluate("document.querySelector('#payment-amount').value")==''
                selected=await call('payment selection show',dict(selection=draft['id']))
                operation=selected['current_lifecycle']['consumed_operation']
                assert selected['current_lifecycle']['state']=='consumed'
                receipt=await call('payment operation show',dict(operation_key=operation['operation_key']))
                assert receipt
                again=await call('payment recovery begin',begin,True)
                assert again['original_receipt']==begun['original_receipt'] and again['current']['state']=='consumed'
                payments=await call('payment query',dict(customer=payer))
                assert payments['total_count']==1 and payments['items'][0]['received_minor_units']==150
                await call('payment recovery query',dict(selection=draft['id']))
                shot(b,tmp_path,'human-recorded-agent-recovered',width)
    anyio.run(witness)
    (tmp_path/'actual-mcp-handoff.json').write_text(json.dumps(dict(width=width,calls=calls),indent=2))
