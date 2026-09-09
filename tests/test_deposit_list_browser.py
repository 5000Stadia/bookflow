"""Bounded saved-list rendering checkpoint; complete paging/MCP journey pending."""
import base64
import json
from time import perf_counter

import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

@pytest.mark.timeout(90)
def test_saved_deposit_list_rendering_checkpoint(register_browser, tmp_path):
    env=register_browser; b=env.browser
    run=lambda name,args: _command(b,env.site,name,args)
    income=run('account.create',dict(name='List income',type='income'))['id']
    customer=run('customer.create',dict(name='List customer'))['id']
    memo='Full saved deposit prose remains readable on a phone. '*5
    saved=run('deposit.post',dict(operation_key='list-checkpoint',document=dict(mode='inline',
        deposit_to=env.bank['id'],date='2026-06-03',memo=memo,
        additional=[dict(received_from=dict(kind='customer',id=customer),from_account=income,amount='12.34')])))
    expected=run('deposit.query',dict(q='List customer',page=dict(limit=25)))
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    start=perf_counter();b.navigate(base+'/deposit?q=List+customer')
    b.wait_for('!!document.querySelector("#deposit-list-lines")')
    elapsed=perf_counter()-start
    assert b.evaluate('document.querySelector("[data-total=bank_total] dd").textContent')=='12.34 USD'
    assert expected['totals']['bank_total']['minor_units']==1234
    assert saved['deposit']['id']==expected['items'][0]['current']['deposit_id']
    assert '1 matching deposits' in b.evaluate('document.querySelector("#deposit-page-count").textContent')
    for width in (1280,390):
        b.viewport(width,900)
        assert b.evaluate('document.documentElement.scrollWidth===document.documentElement.clientWidth')
        assert b.evaluate('[document.querySelector("#deposit-list-lines"),document.querySelector(".deposit-list-wrap")].every(e=>e.scrollWidth===e.clientWidth)')
        assert b.evaluate('getComputedStyle(document.querySelector("#deposit-list-lines tbody tr")).display')==('grid' if width==390 else 'table-row')
        assert memo.strip() in b.evaluate('document.querySelector("#deposit-list-lines").innerText')
        assert b.evaluate('[...document.querySelectorAll("[data-amount]")].every(e=>e.innerText.includes("12.34 USD") && e.getBoundingClientRect().width>0 && getComputedStyle(e).visibility==="visible")')
        (tmp_path/f'deposit-list-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True))['data']))
    b.call('Emulation.setEmulatedMedia',dict(media='print'))
    assert b.evaluate('getComputedStyle(document.querySelector("#deposit-list-lines tbody tr")).display')=='table-row'
    b.call('Emulation.setEmulatedMedia',dict(media='screen'))
    b.evaluate('document.querySelector(".deposit-open").click()');b.wait_for('!!document.querySelector(".deposit-heading")')
    assert '12.34 USD' in b.evaluate('document.querySelector(".deposit-lede").innerText')
    b.evaluate('document.querySelector(".deposit-list-back a").click()');b.wait_for('!!document.querySelector("#deposit-list-lines")')
    assert b.evaluate('document.querySelector("[name=q]").value')=='List customer'
    (tmp_path/'browser-times.json').write_text(json.dumps(dict(population=1,list_navigation_to_table_s=elapsed),indent=2)+'\n')
    print('List navigation to table:',elapsed,flush=True)
