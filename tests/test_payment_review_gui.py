"""Retained independent Gate C business oracles, now regression coverage."""
import json
from pathlib import Path
import pytest
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_customer_payment_browser import field, click, wait, shot


def setup(env):
    b=env.browser
    run=lambda name,data,**headers:_command(b,env.site,name.replace(' ','.'),data,**headers)
    payer=run('customer create',{'name':'Critic Alice'})['id']
    other=run('customer create',{'name':'Critic Bob'})['id']
    pm=run('payment-method create',{'name':'Critic cash','kind':'cash'})['id']
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/receive-payments?customer='+payer)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    field(b,'date','2026-06-02');field(b,'amount','10');field(b,'method',pm);field(b,'destination',env.bank['id'])
    click(b,'load')
    return b,run,payer,other,base

@pytest.mark.parametrize('width',[1280,390])
def test_customer_change_must_invalidate_preview(register_browser,tmp_path,width):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    click(b,'preview')
    field(b,'customer','Critic Bob');click(b,'find-customer')
    b.evaluate("Array.from(document.querySelectorAll('#payment-customer-results button')).find(x=>x.textContent==='Critic Bob').click()")
    wait(b)
    displayed=b.evaluate("document.querySelector('#payment-customer').value")
    disabled=b.evaluate("document.querySelector('#payment-save').disabled")
    shot(b,tmp_path,'changed-customer-preview',width)
    if not disabled:
        click(b,'save')
    facts={'displayed_customer':displayed,'save_disabled':disabled,
           'alice_payments':run('payment query',{'customer':payer})['items'],
           'bob_payments':run('payment query',{'customer':other})['items']}
    (tmp_path/'customer-facts.json').write_text(json.dumps(facts,indent=2))
    assert disabled, facts

@pytest.mark.parametrize('width',[1280,390])
def test_save_new_clears_displayed_selection_totals(register_browser,tmp_path,width):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    click(b,'preview');click(b,'save-new')
    facts={key:b.evaluate(f"document.querySelector('#payment-{key}').innerText") for key in ['totals','selection-status','amount-origin']}
    facts['amount']=b.evaluate("document.querySelector('#payment-amount').value")
    facts['selected_count']=b.evaluate("document.querySelectorAll('#payment-invoices input:checked').length")
    shot(b,tmp_path,'blank-new-stale-totals',width)
    (tmp_path/'save-new-facts.json').write_text(json.dumps(facts,indent=2))
    assert '10.00' not in facts['totals'] and 'selected invoices' not in facts['selection-status'],facts

@pytest.mark.parametrize('width',[1280,390])
def test_large_exact_cash_display(register_browser,tmp_path,width):
    import sqlite3
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    amount='90071992547409.93'
    field(b,'amount',amount);click(b,'preview')
    preview=b.evaluate("document.querySelector('#payment-preview-result').innerText")
    click(b,'save')
    receipt=b.evaluate("document.querySelector('#payment-record').innerText")
    path=Path(run('company show',{})['path'])/'company.db'
    with sqlite3.connect(path) as db:
        cash=db.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE account_id=?',(register_browser.bank['id'],)).fetchone()[0]
    facts=dict(preview=preview,receipt=receipt,cash=cash)
    (tmp_path/'exact-cash.json').write_text(json.dumps(facts,indent=2));shot(b,tmp_path,'exact-cash',width)
    assert cash==9007199254740993
    assert '90071992547409.92' not in preview+receipt and ('unapplied credit '+amount) in receipt,facts


def invoice_setup(run,payer):
    income=run('account create',dict(name='Critic income',type='income'))['id']
    code=next(x['id'] for x in run('sales-tax-code list',{})['items'] if not x['taxable'])
    item=run('item create',dict(name='Critic labor',description='Completed work',type='service',sales_enabled=True,income_account_id=income,price='100',sales_tax_code_id=code))['id']
    invoice=run('invoice post',dict(customer=payer,date='2026-06-01',number='CRITIC-INVOICE',lines=[dict(item=item,quantity='1'),dict(item=item,quantity='0.5')]))
    return invoice,item,income

@pytest.mark.parametrize('width',[1280,390])
def test_actual_invoice_financial_restatement_and_original_grid(register_browser,tmp_path,width):
    import sqlite3
    from tests.test_service_sales_browser import _fill,_preview,_click,_saved
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    invoice,item,income=invoice_setup(run,payer)
    pm=b.evaluate("document.querySelector('#payment-method').value")
    paid=run('payment receive',dict(customer=payer,date='2026-06-02',amount='75',payment_method=pm,deposit_to=register_browser.bank['id'],operation_key='critic-invoice-cash',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='75')])))
    b.navigate(base+'/invoice/'+invoice['id']+'/update');b.wait_for("!!document.querySelector('[data-sales-form]')")
    _fill(b,'c:lines:0:quantity','2');_fill(b,'ctx:reason','Correct actual completed quantity')
    _preview(b);b.wait_for("document.querySelector('#invoice-settlement-preview')?.dataset.complete==='true'")
    shot(b,tmp_path,'real-financial-restatement-preview',width)
    _click(b,'submit');assert _saved(b,'invoice')==invoice['id']
    current=run('invoice show',dict(invoice=invoice['id']))
    assert current['revision']['total']['minor_units']==25000
    assert current['settlement_current']['due_minor_units']==17500
    path=Path(run('company show',{})['path'])/'company.db'
    with sqlite3.connect(path) as db:
        allocations=db.execute('SELECT target_line_id,sum(CASE WHEN kind="reversal" THEN -amount_minor_units ELSE amount_minor_units END) FROM application_allocations WHERE application_id=? GROUP BY target_line_id',(paid['effect']['applications'][0]['application_id'],)).fetchall()
        cash=db.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE account_id=?',(register_browser.bank['id'],)).fetchone()[0]
        revenue=db.execute('SELECT sum(credit_minor_units-debit_minor_units) FROM posting_lines WHERE account_id=?',(income,)).fetchone()[0]
    assert sorted(x[1] for x in allocations)==[1500,6000],allocations
    assert (cash,revenue)==(7500,25000)
    b.navigate(base+'/receive-payments?customer='+payer);b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    field(b,'date','2026-06-02');click(b,'load')
    row=b.evaluate("document.querySelector('#payment-invoices tr').innerText")
    original=b.evaluate("document.querySelector('#payment-invoices [data-label=Original]').innerText")
    facts=dict(original_cell=original,row=row,allocations=allocations,cash=cash,revenue=revenue)
    (tmp_path/'restatement-grid.json').write_text(json.dumps(facts,indent=2));shot(b,tmp_path,'corrected-original-grid',width)
    assert '150.00' in original and '250.00' in row,facts


@pytest.mark.parametrize('width',[1280,390])
def test_exact_large_suggestion_round_trips_shared_selection(register_browser,tmp_path,width):
    import sqlite3
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    income=run('account create',dict(name='Exact receipt income',type='income'))['id']
    code=next(x['id'] for x in run('sales-tax-code list',{})['items'] if not x['taxable'])
    item=run('item create',dict(name='Exact accepted amount',description='Completed work',type='service',sales_enabled=True,
        income_account_id=income,price='90071992547409.93',sales_tax_code_id=code))['id']
    invoice=run('invoice post',dict(customer=payer,date='2026-06-01',lines=[dict(item=item,quantity='1')]))
    field(b,'amount','90071992547409.93');click(b,'load');click(b,'auto')
    # Suggestions use typed Money minor units through the real JSON serializer.
    assert '90071992547409.93' in b.evaluate("document.querySelector('#payment-totals').innerText")
    click(b,'preview');click(b,'save')
    path=Path(run('company show',{})['path'])/'company.db'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT amount_minor_units FROM applications WHERE paid_transaction_id=?',(invoice['id'],)).fetchall()==[(9007199254740993,)]
        assert db.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE account_id=?',(register_browser.bank['id'],)).fetchone()[0]==9007199254740993
    shot(b,tmp_path,'exact-suggestion-saved',width)
    # JSON strings, exponent literals, signs and currency scales stay distinct.
    result=b.evaluate("""(()=>{const x=BookflowExactJSON, raw='{"n":9007199254740993,"s":"9007199254740993","e":1e3,"negative":-9007199254740993}';const p=x.parse(raw);return {serialized:x.stringify(p),usd:x.minor(p.n,'USD'),negative:x.minor(p.negative,'USD'),jpy:x.minor(p.n,'JPY'),kwd:x.minor(p.n,'KWD')};})()""")
    assert json.loads(result['serialized'])==dict(n=9007199254740993,s='9007199254740993',e=1000,negative=-9007199254740993)
    assert [result[k] for k in ('usd','negative','jpy','kwd')]==['90071992547409.93','-90071992547409.93','9007199254740993','9007199254740.993']
