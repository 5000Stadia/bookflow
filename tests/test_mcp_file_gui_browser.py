"""Actual installed MCP files and human browser annotations share one company."""
import hashlib
import json
import os
from pathlib import Path
import sys
import anyio
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _contained


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_installed_mcp_file_browser_and_agent_continuation(register_browser,tmp_path,width):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env,b = register_browser,register_browser.browser
    root=Path(os.environ['BOOKFLOW_DATA_ROOT'])
    assert root.is_relative_to(tmp_path)
    record=_command(b,env.site,'customer.create',{'name':'MCP browser receipt customer'})['id']
    issuance=b.evaluate("""fetch('/commands/token.issue', {method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1'},
        body:JSON.stringify({label:'Owned file browser MCP'})})
        .then(async r=>({status:r.status,body:await r.json()}))""",await_promise=True)
    assert issuance['status']==200,issuance['body']
    issued=issuance['body']
    inbox,outbox,human_download=tmp_path/'inbox',tmp_path/'outbox',tmp_path/'human-download'
    for directory in (inbox,outbox,human_download):directory.mkdir(mode=0o700)
    body=b'%PDF-1.4\n'+bytes(range(256))*8192+b'\n%%EOF\n'
    source=inbox/'Agent receipt é.pdf';source.write_bytes(body)
    human_file=tmp_path/'Human receipt.pdf';human_file.write_bytes(b'%PDF-1.4\nHuman-supplied receipt\n%%EOF\n')
    target={'record_type':'customer','record_id':record}
    note='Human checked the café receipt; retain both original files.'
    async def witness():
        binary=os.environ.get('BOOKFLOW_MCP_TEST_BINARY',str(Path(sys.executable).with_name('bookflow')))
        params=StdioServerParameters(command=binary,args=['mcp','--url',env.site.base_url,'--input-dir',str(inbox),
            '--output-dir',str(outbox),'--client-name','mcp-file-gui-witness'],cwd=str(tmp_path),
            env={'BOOKFLOW_TOKEN':issued['secret'],'BOOKFLOW_COMPANY':env.site.company_id,'BOOKFLOW_DATA_ROOT':str(tmp_path/'absent')})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.discover()
                async def run(command,raw,**options):
                    result=await session.call_tool('bookflow_run',{'command':command,'input':raw,**options})
                    assert not result.is_error,result
                    return result.structured_content
                original_events={e['id'] for e in (await run('audit list',{'limit':200}))['items']}
                raw={**target,'original_filename':source.name,'media_type':'application/pdf','caption':'Filed from MCP'}
                preview=await run('attachment add',raw,dry_run=True,reason='Preview owned receipt',transport={'input_file':str(source)})
                assert preview['dry_run'] and (await run('attachment list',target))['count']==0
                added=await run('attachment add',raw,reason='File owned receipt',transport={'input_file':str(source)})
                attachment=added['attachment']
                assert attachment['created_via']=='mcp' and attachment['sha256']==hashlib.sha256(body).hexdigest()
                b.viewport(width,900)
                b.navigate(env.site.base_url+'/c/'+env.site.company_id+'/customer/'+record)
                b.wait_for('document.readyState === "complete" && !!document.querySelector("[data-annotations]")')
                b.wait_for('document.querySelector("[data-link-id]")?.textContent.includes("Agent receipt é.pdf")')
                _contained(b,width)
                b.call('Browser.setDownloadBehavior',{'behavior':'allow','downloadPath':str(human_download)})
                b.evaluate('document.querySelector("[data-link-id] button").click()')
                b.wait_for('document.querySelector("[data-link-id] [role=status]")?.textContent.includes("File verified")')
                for _ in range(100):
                    if (human_download/source.name).exists():break
                    await anyio.sleep(.05)
                assert (human_download/source.name).read_bytes()==body
                b.evaluate(f'document.querySelector("#annotation-note").value={json.dumps(note)}; document.querySelector("[data-note-add]").requestSubmit()')
                b.wait_for('document.querySelector("[data-note-add] [data-status]").textContent === "Note added."')
                node=b.call('DOM.getDocument')['root']['nodeId']
                file_node=b.call('DOM.querySelector',{'nodeId':node,'selector':'#annotation-file'})['nodeId']
                b.call('DOM.setFileInputFiles',{'nodeId':file_node,'files':[str(human_file)]})
                b.evaluate('document.querySelector("#annotation-caption").value="Filed by human";document.querySelector("[data-file-add]").requestSubmit()')
                b.wait_for('document.querySelector("[data-file-add] [data-status]").textContent === "File attached."')
                listed=await run('attachment list',target)
                assert listed['count']==2
                notes=await run('note list',target)
                found=next(row for row in notes['items'] if row['body']==note)
                assert found['created_via']=='http'
                # The continuation discovers both original identities and downloads
                # the human's new bytes using only the delivered output-file facility.
                other=next(row for row in listed['items'] if row['attachment']['original_filename']==human_file.name)
                assert other['attachment']['created_via']=='http'
                destination=outbox/'Human receipt retrieved.pdf'
                fetched=await run('attachment get',{'attachment':other['attachment']['id']},transport={'output_file':str(destination)})
                assert destination.read_bytes()==human_file.read_bytes()
                assert fetched['sha256']==hashlib.sha256(human_file.read_bytes()).hexdigest()
                events=await run('audit list',{'limit':200})
                related=[e for e in events['items'] if e['command']=='attachment add' and e['id'] not in original_events]
                assert len(related)==2 and {e['interface'] for e in related}=={'mcp','http'}
                assert next(e for e in related if e['interface']=='http')['client_name']=='bookflow-workbench'
                assert next(e for e in related if e['interface']=='mcp')['client_name']=='mcp-file-gui-witness'
    anyio.run(witness)
    assert not (tmp_path/'absent').exists()
