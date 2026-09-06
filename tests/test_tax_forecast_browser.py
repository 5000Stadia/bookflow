"""Real over-cap histories, readable hypothetical forecasts and GUI bounded recovery.

Uses ordinary public financial operations and the existing local browser/auth
fixture pattern. No fabricated allocation rows or disabled guards.
"""
import base64
from contextlib import contextmanager
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from bookflow.commands.host_cmds import start_serving
from bookflow.core.config import os_login
from bookflow.core.context import client_version
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp
from tests.test_tax_policy_sales import sale, tax_sale, request, COMPANY
from tests.test_customer_work_lifecycle import run
from tests.test_work_billing_lifecycle import accepted,bill
from tests.test_progress_billing_lifecycle import current
from tests.test_service_sales_browser import _fill,_click,_preview,_saved,_contained
from tests.test_customer_work_browser import visit
from tests.test_progress_billing_browser import check_line

pytestmark=pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')


class CapBrowser(_Cdp):
    # This new large-history correctness journey records latency separately.
    # Existing shared browser helpers and their 15s waits remain unchanged.
    def wait_for(self, expression, *, timeout=60):
        started=time.monotonic()
        try:
            return super().wait_for(expression, timeout=timeout)
        finally:
            self.cap_waits=getattr(self,'cap_waits',[])+[dict(expression=expression,
                timeout_seconds=timeout,elapsed_seconds=time.monotonic()-started)]


@contextmanager
def existing_site(root,client,tmp_path):
    # Same owned root/login/ephemeral-loopback lifecycle as browser_site, without
    # replacing the ordinary financial history just constructed by this test.
    login=os_login();client.run('user set-password',dict(username=login,password=PASSWORD))
    company=client.company.show(company=COMPANY)['id']
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(64)
    port=listener.getsockname()[1]
    handle=start_serving(root,client_version(),bind=f'127.0.0.1:{port}',secure_cookies=False,publish_descriptor=False)
    server=uvicorn.Server(uvicorn.Config(handle.app,log_level='warning',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start()
    b=None
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        b=CapBrowser(tmp_path/'cap-chrome');b.navigate(f'http://127.0.0.1:{port}/login')
        b.evaluate(f'''(() => {{document.querySelector('[name="username"]').value={json.dumps(login)};
          document.querySelector('[name="password"]').value={json.dumps(PASSWORD)};
          document.querySelector('form[hx-post="/login"]').requestSubmit();}})()''')
        b.wait_for('!!document.querySelector(".group-grid")')
        yield b,f'http://127.0.0.1:{port}/c/{company}'
    except BaseException:
        if b:
            (tmp_path/'browser-failure.txt').write_text(b.evaluate('document.body.innerText'))
            capture(b,tmp_path/'browser-failure.png')
        raise
    finally:
        if b:
            (tmp_path/'browser-wait-timings.json').write_text(json.dumps(getattr(b,'cap_waits',[]),indent=2))
            b.close()
        server.should_exit=True;thread.join(timeout=10);listener.close();handle.stop()


def capture(b,path):
    path.write_bytes(base64.b64decode(b.call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))['data']))


@pytest.mark.timeout(1200)
@pytest.mark.parametrize('count,installments,net,tax',[(1,401,202,20),(21,201,2142,214)],ids=['line200','conversion2000'])
def test_over_cap_forecast_and_bounded_browser_recovery(client,tax_sale,root,tmp_path,count,installments,net,tax):
    started=time.monotonic()
    source=accepted(client,tax_sale,**dict(request(tax_sale),lines=[dict(item=tax_sale['item'],
        quantity=str(installments+1),net_amount=f'{(installments+1)//100}.{(installments+1)%100:02d}',
        tax_code=tax_sale['taxable'],description=f'Cap source {i+1}') for i in range(count)]))
    ids=[r['line_id'] for r in source['revision']['lines']]
    invoices=[bill(client,dict(source,version=2+i),f'browser-cap-{i}',selections=[dict(line_id=k,quantity='1') for k in ids]) for i in range(installments)]
    for invoice in invoices[::2]:
        client.run('invoice void',dict(invoice=invoice['id'],expected_version=1),company=COMPANY,reason='Release alternating scope')
    run(client,'company','update',progress_billing_enabled=False)
    state=run(client,'estimate','billing',estimate=source['id'])
    assert state['remaining_net_minor_units']==net and state['remaining_tax_minor_units']==tax
    assert state['can_bill_together'] is False
    code='line_span_limit' if count==1 else 'conversion_span_limit'
    assert code in {r['code'] for r in state['forecast_eligibility_reasons']}
    (tmp_path/'state-before-browser.json').write_text(json.dumps(state,indent=2))
    exercise_cap_browser(client,root,tmp_path,source,count,net,tax,started)


def exercise_cap_browser(client,root,tmp_path,source,count,net,tax,started):
    ids=[r['line_id'] for r in source['revision']['lines']]
    state=run(client,'estimate','billing',estimate=source['id'])
    assert state['remaining_net_minor_units']==net and state['remaining_tax_minor_units']==tax
    assert not state['can_bill_together']
    receipts=[]
    with existing_site(root,client,tmp_path) as (b,base):
        url=base+'/estimate/'+source['id']
        for width in (1280,390):
            b.viewport(width,900)
            b.navigate(url+'/billing');b.wait_for('!!document.querySelector("[aria-label=\\"Remaining tax forecast\\"]")')
            note=b.evaluate('document.querySelector("[aria-label=\\"Remaining tax forecast\\"]").innerText')
            assert 'Hypothetical total' in note and 'cannot currently be billed together' in note
            recovery=state['forecast_eligibility_reasons'][0]['recovery']
            assert recovery in note
            footer=b.evaluate('document.querySelector(".billing-table").parentElement.nextElementSibling.innerText')
            assert f'Remaining net {net//100}.{net%100:02d}' in footer
            assert f'forecast tax {tax//100}.{tax%100:02d}' in footer
            _contained(b,width)
            b.evaluate('document.querySelector("[aria-label=\\"Remaining tax forecast\\"]").scrollIntoView({block:"center"})')
            # Guidance must have an actual visible client rectangle on each width.
            assert b.evaluate('''(() => {const n=document.querySelector('[aria-label="Remaining tax forecast"]'),r=n.getBoundingClientRect();
                return getComputedStyle(n).visibility==='visible' && r.width>0 && r.height>0 && r.left>=0 && r.right<=innerWidth+1 && r.top>=0 && r.top<innerHeight;})()''')
            capture(b,tmp_path/f'cap-guidance-{width}.png')
            # Independently show the monetary table's rightmost cells via ordinary inner navigation.
            b.evaluate('''(() => {const p=document.querySelector('.billing-table').parentElement;p.scrollIntoView({block:'start'});p.scrollLeft=p.scrollWidth;})()''')
            assert b.evaluate('''(() => {const c=document.querySelector('.billing-table tbody tr td:last-child'),r=c.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth+1 && r.width>0;})()''')
            capture(b,tmp_path/f'cap-table-right-{width}.png')
            visit(b,url+'/invoice');_fill(b,'f:date','2026-06-03')
            b.evaluate('void(window.capForm=document.querySelector("[data-sales-form]"))')
            _click(b,'preview');b.wait_for('!window.capForm.isConnected && !!document.querySelector(".error")')
            error=b.evaluate('document.querySelector(".error").innerText')
            assert 'E_VALUE_RANGE' in error,error
            capture(b,tmp_path/f'cap-rejected-{width}.png')
            _fill(b,'billing-selection','recovery' if count==1 else 'selected')
            selected=ids if count==1 else ids[:19]
            for key in selected:
                check_line(b,key)
                if count==1:
                    assert b.evaluate(f'document.getElementsByName({json.dumps("billing-recovery:"+key)})[0].value')=='2.00'
            _preview(b);_contained(b,width)
            expected_net,expected_tax=(200,20) if count==1 else (1938,194)
            totals=b.evaluate('Array.from(document.querySelectorAll("section.sales-document > .table-wrap > table tfoot th")).map(n=>n.innerText)')
            fmt=lambda n:f'{n//100}.{n%100:02d}'
            assert totals==['Total (USD)',fmt(expected_net),fmt(expected_tax),fmt(expected_net+expected_tax)]
            capture(b,tmp_path/f'cap-bounded-preview-{width}.png')
            receipts.append(dict(width=width,hypothetical=note,error=error,preview_totals=totals))
            if width==390:_click(b,'submit');first_id=_saved(b,'invoice')
    first=client.run('invoice show',dict(invoice=first_id),company=COMPANY)
    assert first['subtotal_minor_units']==expected_net and first['tax_minor_units']==expected_tax
    state=run(client,'estimate','billing',estimate=source['id']);assert state['can_bill_together']
    second=bill(client,current(client,source),'browser-cap-finish')
    assert second['revision']['tax_calculation_details']['attribution']==state['forecast_tax_attribution']
    done=run(client,'estimate','billing',estimate=source['id'])
    assert done['remaining_net_minor_units']==done['remaining_tax_minor_units']==0
    assert first['subtotal_minor_units']+second['subtotal_minor_units']==net
    (tmp_path/'browser-receipts.json').write_text(json.dumps(dict(views=receipts,elapsed_seconds=time.monotonic()-started,
        first_invoice=first_id,second_invoice=second['id'],remaining_net=0,remaining_tax=0),indent=2))
