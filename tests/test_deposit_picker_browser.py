"""An agent receives cash; a person banks it through the receipt picker."""
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_sales_document_browser import _fixture
from tests.test_service_sales_browser import _fill, _click

pytestmark=pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')

@pytest.mark.timeout(120)
def test_bank_payment_and_receipt_without_entering_identifiers(register_browser, tmp_path, monkeypatch):
    from bookflow.adapters.workbench import deposits
    monkeypatch.setattr(deposits, "PAGE_LIMIT", 1)
    env=register_browser;b=env.browser
    run=lambda name,data: _command(b,env.site,name,data)
    books=_fixture(run,'Deposit UI')
    accounts=run('account.list',{})['items']
    uf=next(row['id'] for row in accounts if row['name']=='Undeposited Funds')
    method=next(row['id'] for row in run('payment-method.list',{})['items'] if row['kind']=='cash')
    invoice=run('invoice.post',dict(date='2026-06-01',customer=books['customer'],ar_account=books['receivable'],lines=[dict(item=books['item'],net_amount='10')]))
    payment=run('payment.receive',dict(customer=books['customer'],date='2026-06-02',amount='10',payment_method=method,operation_key='picker-payment',ar_account=books['receivable'],applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='10')])))
    receipt=run('sales-receipt.post',dict(date='2026-06-02',customer=books['customer'],deposit_to=uf,payment_method=method,lines=[dict(item=books['item'],net_amount='25')]))
    b.call('Page.addScriptToEvaluateOnNewDocument', {'source': 'crypto.randomUUID=undefined;'})
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/deposit/post')
    b.wait_for('!!document.querySelector(".deposit-receipts")')
    b.viewport(390,900)
    _fill(b,'f:document.date','2026-06-03')
    b.wait_for('document.querySelector(`select[aria-label="Deposit to bank"]`).options.length>1')
    b.evaluate('''(() => {const e=document.querySelector('select[aria-label="Deposit to bank"]');e.value=%r;e.dispatchEvent(new Event('change',{bubbles:true}));})()''' % env.bank['id'])
    b.evaluate('document.querySelector(`input[aria-label="Find receipts"]`).value="Deposit UI"')
    b.evaluate('[...document.querySelectorAll("button")].find(e=>e.textContent==="Find undeposited receipts").click()')
    b.wait_for('document.querySelectorAll(".deposit-receipts input[type=checkbox]").length===2')
    b.evaluate('document.querySelectorAll(".deposit-receipts input").forEach(e=>e.click())')
    for width in (1280,390):
        b.viewport(width,900)
        import base64
        (tmp_path / f'deposit-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True})['data']))
        assert b.evaluate('document.querySelector(".deposit-receipts").scrollWidth===document.querySelector(".deposit-receipts").clientWidth')
    assert b.evaluate("BookflowExactJSON.stringify(BookflowExactJSON.parse('{\"n\":9007199254740993}'))") == '{"n":9007199254740993}'
    key=b.evaluate('document.getElementsByName("f:operation_key")[0].value')
    b.evaluate('void (window.oldDepositForm=document.querySelector("form[data-generated-form]"))')
    _click(b,'preview');b.wait_for('!window.oldDepositForm?.isConnected')
    assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
    assert '35.00 USD' in b.evaluate('document.querySelector(`[aria-label="Deposit preview"]`).innerText')
    assert b.evaluate('document.getElementsByName("f:operation_key")[0].value')==key
    assert b.evaluate('document.querySelector(".deposit-receipts").scrollWidth <= document.querySelector(".deposit-receipts").clientWidth')
    b.evaluate('void (window.oldDepositForm=document.querySelector("form[data-generated-form]"))')
    _fill(b,'f:document.date','2026-06-01')
    assert b.evaluate('document.getElementsByName("f:expected_facts_fingerprint")[0].value') == ''
    _click(b,'preview');b.wait_for('!window.oldDepositForm?.isConnected')
    assert 'E_DEPOSIT_DATE_BEFORE_SOURCE' in b.evaluate('document.querySelector(".error").textContent')
    assert b.evaluate('document.getElementsByName("f:operation_key")[0].value') == key
    assert b.evaluate('document.querySelectorAll(`[name^="c:document.sources:"][name$=":source"]`).length') == 2
    _fill(b,'f:document.date','2026-06-03')
    b.evaluate('void (window.oldDepositForm=document.querySelector("form[data-generated-form]"))')
    _click(b,'preview');b.wait_for('!window.oldDepositForm?.isConnected')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), b.evaluate('document.body.innerText')
    _click(b,'submit')
    assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
    b.wait_for('!!document.querySelector(`[aria-label="Saved deposit"]`)')
    assert '35.00 USD' in b.evaluate('document.querySelector(`[aria-label="Saved deposit"]`).innerText')
    result=run('deposit.sources',dict(date='2026-06-03',q='Deposit UI'))
    assert result['items']==[]

    saved_link = b.evaluate('document.querySelector(`[aria-label="Saved deposit"] a`).href')
    assert '/deposit/' in saved_link and b.evaluate('location.href').split('?')[0] == saved_link
    b.evaluate('document.querySelector(`[aria-label="Saved deposit"] a`).click()')
    b.wait_for('!!document.querySelector(".deposit-heading")')
    assert '35.00 USD' in b.evaluate('document.querySelector(".deposit-lede").innerText')
    for width in (1280,390):
        b.viewport(width,900)
        assert b.evaluate('document.documentElement.scrollWidth===document.documentElement.clientWidth')
        assert b.evaluate('[...document.querySelectorAll(".deposit-card,.deposit-totals")].every(e=>e.scrollWidth===e.clientWidth)')
    (tmp_path / 'saved-deposit-390.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png','captureBeyondViewport':True})['data']))
    b.navigate(saved_link + '/items')
    b.wait_for('!!document.querySelector(".deposit-row-card")')
    assert b.evaluate('document.querySelectorAll(".deposit-row-card").length') == 1
    first = b.evaluate('document.querySelector(".deposit-row-card").innerText')
    assert '10.00 USD' in first or '25.00 USD' in first
    next_url = b.evaluate('document.querySelector("a[rel=next]").href')
    b.navigate(next_url)
    b.wait_for('!!document.querySelector(".deposit-row-card")')
    second = b.evaluate('document.querySelector(".deposit-row-card").innerText')
    assert first != second and ('10.00 USD' in second or '25.00 USD' in second)
    assert b.evaluate('document.documentElement.scrollWidth===document.documentElement.clientWidth')
    assert b.evaluate('document.querySelector(".deposit-row-card").scrollWidth===document.querySelector(".deposit-row-card").clientWidth')
    (tmp_path / 'saved-deposit-items-390.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png','captureBeyondViewport':True})['data']))
    for verb in ('update','void'):
        b.navigate(saved_link + '/' + verb)
        b.wait_for('!!document.querySelector("[data-generated-form]")')
        assert not b.evaluate('document.querySelector(".error")?.textContent'), b.evaluate('document.body.innerText')
        assert b.evaluate('document.getElementsByName("f:expected_version")[0].value') == '1'
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/deposit/sources')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
