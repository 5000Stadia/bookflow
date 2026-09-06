"""A real 403-job remittance, all prospective pages, and receipt restatement in Chrome."""
import json
import os
import pytest
from tests.test_customer_payment_browser import CHROME, browser_site, register_browser, field, shot, recorded_state
from tests.test_row8_register_browser import _command
from pathlib import Path

pytestmark=pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')


@pytest.mark.timeout(600)  # 806 public fixture writes plus complete prospective-page recomputation.
def test_complete_403_job_receipt_and_correction_preview_both_viewports(register_browser,tmp_path):
    env,b=register_browser,register_browser.browser
    run=lambda name,data,**headers: _command(b,env.site,name.replace(' ','.'),data,**headers)
    payer=run('customer create',dict(name='403 job browser remittance'))['id']
    pm=run('payment-method create',dict(name='403 remittance cheque',kind='check'))['id']
    income=run('account create',dict(name='403 remittance income',type='income'))['id']
    code=next(row['id'] for row in run('sales-tax-code list',{})['items'] if not row['taxable'])
    item=run('item create',dict(name='403 completed jobs',type='service',sales_enabled=True,
        income_account_id=income,description='Completed job',sales_tax_code_id=code,price='0.03'))['id']
    invoices=[]
    for index in range(403):
        job=run('customer create',dict(name=f'Job {index:03}',parent_id=payer))['id']
        invoices.append(run('invoice post',dict(customer=job,date='2026-06-01',number=f'GUI-403-{index:03}',lines=[dict(item=item,quantity='1')]))['id'])
    draft=run('payment selection create',dict(mode='new_receipt',customer=payer,date='2026-06-02',amount='4.04'))
    for start in range(0,403,137):
        draft=run('payment selection update',dict(selection=draft['id'],expected_version=draft['version'],set_items=[
            dict(invoice=invoice,expected_version=1,amount='0.01',amount_origin='entered') for invoice in invoices[start:start+137]]))
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/receive-payments?selection='+draft['id'])
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=180)
    field(b,'method',pm);field(b,'destination',env.bank['id'])
    path=Path(run('company show',{})['path'])/'company.db'
    before=recorded_state(path)
    b.evaluate("document.querySelector('#payment-preview').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
    assert b.evaluate("document.querySelector('#payment-error').hidden"),b.evaluate("document.querySelector('#payment-error').innerText")
    assert not b.evaluate("document.querySelector('#payment-save').disabled")
    text=b.evaluate("document.querySelector('#payment-preview-result').innerText")
    for title in ('source components: 404 complete changes','applications: 403 complete changes','allocations: 403 complete changes','document changes: 403 complete changes'):
        assert title in text
    assert recorded_state(path)==before
    for width in (1280,390):
        b.viewport(width,900);b.evaluate("document.querySelector('#payment-preview-result').scrollIntoView()")
        shot(b,tmp_path,'403-job-receipt-complete-preview',width)
    b.evaluate("document.querySelector('#payment-save').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
    assert b.evaluate("document.querySelector('#payment-error').hidden"),b.evaluate("document.querySelector('#payment-error').innerText")
    paid=run('payment query',dict(customer=payer))['items'];assert len(paid)==1
    assert (paid[0]['received_minor_units'],paid[0]['applied_minor_units'],paid[0]['unapplied_minor_units'])==(404,403,1)
    assert b.evaluate("document.querySelector('#payment-record').innerText.match(/owned credit/g).length")==404
    b.evaluate("Array.from(document.querySelectorAll('#payment-record button')).find(x=>x.textContent==='Correct receipt').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
    field(b,'amount','4.05');field(b,'reason','Correct extra penny of actual cash')
    before=recorded_state(path)
    b.evaluate("document.querySelector('#payment-preview').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
    assert b.evaluate("document.querySelector('#payment-error').hidden"),b.evaluate("document.querySelector('#payment-error').innerText")
    assert not b.evaluate("document.querySelector('#payment-save').disabled")
    text=b.evaluate("document.querySelector('#payment-preview-result').innerText")
    assert 'allocations: 806 complete changes' in text and 'document changes: 403 complete changes' in text
    assert recorded_state(path)==before
    for width in (1280,390):
        b.viewport(width,900);b.evaluate("document.querySelector('#payment-preview-result').scrollIntoView()")
        shot(b,tmp_path,'403-job-correction-complete-preview',width)
    b.evaluate("document.querySelector('#payment-save').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
    assert b.evaluate("document.querySelector('#payment-error').hidden"),b.evaluate("document.querySelector('#payment-error').innerText")
    current=run('payment show',dict(payment=paid[0]['id']))['current']
    assert (current['received_minor_units'],current['applied_minor_units'],current['available_minor_units'])==(405,403,2)
    assert run('customer show',dict(customer=payer))['family_balance']['minor_units']==804
    with __import__('sqlite3').connect(path) as db:
        assert db.execute('SELECT version,count(*) FROM transactions WHERE number LIKE ? GROUP BY version',('GUI-403-%',)).fetchall()==[(3,403)]
        bank=db.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? AND account_id=?',(paid[0]['id'],env.bank['id'])).fetchone()[0]
        assert bank==405
    # The fixture's host owns the exclusive root until teardown. An actual CLI
    # read uses these same IDs after shutdown, never bypasses that ownership.
    (tmp_path/'403-cli-handoff.json').write_text(json.dumps(dict(data_root=os.environ['BOOKFLOW_DATA_ROOT'],
        company=env.site.company_id,payment=paid[0]['id'],customer=payer,expected=current),indent=2))
