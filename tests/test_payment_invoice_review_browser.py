"""Independent 403-payment invoice correction oracle retained as regression."""
import json,sqlite3
from pathlib import Path
import pytest
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser,_command
from tests.test_service_sales_browser import _fill,_preview,_click,_saved
from tests.test_customer_payment_browser import shot,recorded_state

@pytest.mark.timeout(600)
def test_gui_invoice_correction_all_403_payment_dependencies(register_browser,tmp_path):
    env=register_browser;b=env.browser
    run=lambda name,data,**headers:_command(b,env.site,name.replace(' ','.'),data,**headers)
    payer=run('customer create',dict(name='Critic 403 installment payer'))['id']
    pm=run('payment-method create',dict(name='Critic 403 cash',kind='cash'))['id']
    income=run('account create',dict(name='Critic 403 income',type='income'))['id']
    code=next(x['id'] for x in run('sales-tax-code list',{})['items'] if not x['taxable'])
    item=run('item create',dict(name='Critic 403 work',description='Completed work',type='service',sales_enabled=True,income_account_id=income,sales_tax_code_id=code,price='5'))['id']
    invoice=run('invoice post',dict(customer=payer,date='2026-06-01',number='CRITIC-403-INVOICE',lines=[dict(item=item,quantity='1')]))
    for index in range(403):
        run('payment receive',dict(customer=payer,date='2026-06-02',amount='0.01',payment_method=pm,deposit_to=env.bank['id'],operation_key=f'critic-403-{index}',number=f'CRITIC-PAY-{index}',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=index+1,amount='0.01')])))
    path=Path(run('company show',{})['path'])/'company.db'
    before=recorded_state(path)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/{invoice["id"]}/update')
    b.wait_for("!!document.querySelector('[data-sales-form]')")
    _fill(b,'c:lines:0:quantity','2');_fill(b,'ctx:reason','Correct complete 403 installment invoice')
    _preview(b);b.wait_for("document.querySelector('#invoice-settlement-preview')?.dataset.complete==='true'",timeout=180)
    text=b.evaluate("document.querySelector('#invoice-settlement-preview').innerText")
    assert 'document changes: 404 complete changes' in text and 'allocations: 806 complete changes' in text,text
    assert recorded_state(path)==before
    b.evaluate("document.querySelectorAll('#invoice-settlement-preview details').forEach(x=>x.open=true)")
    expanded=b.evaluate("document.querySelector('#invoice-settlement-preview').innerText")
    assert 'Proposed Due: 5.97 USD' in expanded
    assert expanded.count('Proposed Received: 0.01 USD')==403
    assert expanded.count('Proposed Available credit: 0.00 USD')==403
    assert b.evaluate("new Set(Array.from(document.querySelectorAll('#invoice-settlement-preview a')).filter(a=>a.textContent.startsWith('Payment ')).map(a=>a.textContent)).size")==403
    for width in [1280,390]:
        b.viewport(width,900);b.evaluate("document.querySelector('#invoice-settlement-preview').scrollIntoView()")
        shot(b,tmp_path,'all-403-invoice-preview',width)
    _click(b,'submit');assert _saved(b,'invoice')==invoice['id']
    with sqlite3.connect(path) as db:
        cash=db.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE account_id=?',(env.bank['id'],)).fetchone()[0]
        revenue=db.execute('SELECT sum(credit_minor_units-debit_minor_units) FROM posting_lines WHERE account_id=?',(income,)).fetchone()[0]
        versions=db.execute("SELECT version,count(*) FROM transactions WHERE number LIKE 'CRITIC-PAY-%' GROUP BY version").fetchall()
        allocation_rows=db.execute('SELECT kind,count(*),sum(amount_minor_units) FROM application_allocations WHERE target_transaction_id=? GROUP BY kind',(invoice['id'],)).fetchall()
    settlement=run('invoice settlement',dict(invoice=invoice['id']))
    facts=dict(cash=cash,revenue=revenue,versions=versions,allocations=allocation_rows,settlement=settlement)
    (tmp_path/'403-invoice-oracle.json').write_text(json.dumps(facts,indent=2))
    assert (cash,revenue,settlement['due_minor_units'],settlement['applied_minor_units'])==(403,1000,597,403),facts
    assert versions==[(2,403)] and sorted(allocation_rows)==[('allocation',806,806),('reversal',403,403)],facts
