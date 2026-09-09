"""True MCP agent journal preview/save/replay is the same browser ledger/audit."""
import json
import pytest
from tests.test_row3_host import hosted, live, PASSWORD
from tests.test_row5_browser_acceptance import CHROME, _Cdp
from tests.test_mcp_hosted import agent_invoice_and_directive_journal_workflow
from tests.test_service_sales_browser import _contained


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_installed_agent_journal_visible_in_browser_register_and_expanded_audit(hosted,live,tmp_path,width):
    result = agent_invoice_and_directive_journal_workflow(hosted,live,tmp_path)
    browser = _Cdp(tmp_path/'journal-agent-chrome')
    try:
        browser.viewport(width,900)
        browser.navigate(live+'/login')
        browser.evaluate(f"""(() => {{
          document.querySelector('[name="username"]').value={json.dumps(hosted.login)};
          document.querySelector('[name="password"]').value={json.dumps(PASSWORD)};
          document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()""")
        browser.wait_for('!!document.querySelector(".nav-group")')
        base=live+'/c/'+hosted.company_id
        browser.navigate(base+'/journal/'+result['journal'])
        browser.wait_for("!!document.querySelector('section[aria-label=\"Journal entry\"]')")
        _contained(browser,width)
        cells=browser.evaluate('''[...document.querySelectorAll('section[aria-label="Journal entry"] tbody tr')]
            .map(row=>[...row.cells].map(cell=>cell.textContent.trim()))''')
        assert [(row[0],row[4],row[5]) for row in cells]==[
            ('MCP operating bank','12.34',''),('MCP labor income','','12.34')]
        totals=browser.evaluate('''[...document.querySelectorAll('section[aria-label="Journal entry"] tfoot th')].map(x=>x.textContent.trim())''')
        assert totals[-2:]==['12.34','12.34']
        event=result['event']
        assert event['directive_code'] in browser.evaluate('document.body.innerText')
        browser.navigate(base+'/account/'+result['bank']+'/register')
        browser.wait_for('!document.querySelector("#register-current").textContent.includes("loading")')
        browser.evaluate('''(() => {const form=document.querySelector('#register-period');
            form.elements.date_from.value='2026-01-12';form.elements.date_to.value='2026-01-12';
            form.requestSubmit();})()''')
        selector='a[href*="/journal/'+result['journal']+'?"]'
        browser.wait_for(f'!!document.querySelector({json.dumps(selector)})')
        row=browser.evaluate(f'document.querySelector({json.dumps(selector)}).closest("tr").innerText')
        assert '12.34' in row and '2026-01-12' in row
        money=browser.evaluate(f'[...document.querySelector({json.dumps(selector)}).closest("tr").querySelectorAll(".register-money")].map(e=>e.textContent)')
        assert money[0]=='12.34 USD' and money[-1]=='12.34 USD'
        _contained(browser,width)
        browser.navigate(base+'/audit/'+event['id'])
        browser.wait_for('document.body.innerText.includes("session_id")')
        fields=browser.evaluate('''Object.fromEntries([...document.querySelectorAll('.field')]
            .filter(e=>e.querySelector('b') && e.querySelector('span'))
            .map(e=>[e.querySelector('b').textContent,e.querySelector('span').textContent]))''')
        for name in ('id','actor_id','actor_name','actor_kind','on_behalf_of','on_behalf_of_name',
                     'interface','client_name','session_id','directive_code','directive_text'):
            assert fields[name]==event[name],name
        assert fields['actor_kind']=='agent' and fields['interface']=='mcp'
        # Merely viewing the journal/register/audit cannot create another posting.
        events=hosted.ok('audit.list',{'record_type':'transaction','record_id':result['journal']},company=hosted.company_id)
        assert [e['id'] for e in events['items']]==[event['id']]
    finally:
        browser.close()
