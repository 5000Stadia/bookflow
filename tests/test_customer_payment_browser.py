"""Real Chrome payment workflows over reviewed HTTP execution and shared drafts."""
import base64
import json
import sqlite3
from pathlib import Path

import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def wait(b):
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert b.evaluate("document.querySelector('#payment-error').hidden"), b.evaluate("document.querySelector('#payment-error').innerText")


def field(b, identity, value):
    b.evaluate(f"(()=>{{let x=document.getElementById('payment-'+{json.dumps(identity)});x.value={json.dumps(value)};x.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    wait(b)


def click(b, identity):
    b.evaluate(f"document.getElementById('payment-'+{json.dumps(identity)}).click()")
    wait(b)


def shot(b, tmp_path, name, width):
    _contained(b, width)
    (tmp_path / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png'})['data']))


def recorded_state(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in (
            'transactions', 'transaction_revisions', 'posting_batches', 'posting_lines',
            'posting_line_sources', 'applications', 'application_allocations', 'audit_events',
            'audit_entries', 'payment_operations', 'payment_components', 'payment_component_keys')}


@pytest.mark.parametrize('width', [1280, 390])
def test_receive_shared_origins_preview_unapply_reuse(register_browser, width, tmp_path):
    env,b=register_browser,register_browser.browser
    run=lambda name,data,**headers: _command(b,env.site,name.replace(' ','.'),data,**headers)
    b.viewport(width,900)
    income=run('account create',dict(name='Payment browser income',type='income'))['id']
    payer=run('customer create',dict(name='Browser Payment Customer'))['id']
    method=run('payment-method create',dict(name='Browser cheque',kind='check'))['id']
    code=next(row['id'] for row in run('sales-tax-code list',{})['items'] if not row['taxable'])
    item=run('item create',dict(name='Payment browser labor',type='service',sales_enabled=True,
        income_account_id=income,price='100',description='Work completed',sales_tax_code_id=code))['id']
    run('company update',dict(automatically_calculate_payments=True))
    invoices=[run('invoice post',dict(customer=payer,date='2026-06-01',number=f'PAY-BROWSER-{i}',lines=[dict(item=item,quantity='1',net_amount='100.00')])) for i in range(2)]
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/receive-payments?customer='+payer)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'")
    wait(b)
    field(b,'date','2026-06-02');field(b,'amount','150.00');field(b,'method',method);field(b,'destination',env.bank['id'])
    click(b,'load')
    assert '200.00' in b.evaluate("document.querySelector('#payment-family-balance').innerText")
    for invoice in invoices:
        b.evaluate(f"document.querySelector('[data-invoice=\"{invoice['id']}\"] input[type=checkbox]').click()")
        wait(b)
    assert '150.00' in b.evaluate("document.querySelector('#payment-totals').innerText")
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    draft=run('payment selection show',dict(selection=selection))
    rows=run('payment selection items',dict(selection=selection))['items']
    assert draft['amount_origin']=='entered' and [r['amount_minor_units'] for r in rows]==[10000,5000]
    # A second interface changes the durable draft. Browser reload has no private origin flags.
    run('payment selection update',dict(selection=selection,expected_version=draft['version'],remove_invoices=[invoices[0]['id']]))
    click(b,'refresh-draft')
    assert b.evaluate("document.querySelector('#payment-amount').value")=='150.00'
    assert run('payment selection items',dict(selection=selection))['items'][0]['amount_minor_units']==10000
    assert '50.00' in b.evaluate("document.querySelector('#payment-totals').innerText")
    field(b,'memo','Two-job remittance')
    click(b,'preview')
    assert not b.evaluate("document.querySelector('#payment-save').disabled")
    shot(b,tmp_path,'payment-preview',width)
    click(b,'save')
    assert not b.evaluate("document.querySelector('#payment-record').hidden")
    payments=run('payment query',dict(customer=payer))['items'];assert len(payments)==1
    paid=run('payment show',dict(payment=payments[0]['id']))
    assert paid['current']['received_minor_units']==15000 and paid['current']['applied_minor_units']==10000 and paid['current']['available_minor_units']==5000
    shot(b,tmp_path,'payment-receipt',width)
    b.evaluate("Array.from(document.querySelectorAll('#payment-record button')).find(x=>x.textContent==='Unapply recorded applications').click()")
    wait(b)
    b.evaluate("document.querySelector('#payment-invoices input[type=checkbox]').click()")
    field(b,'reason','Customer corrected invoice allocation');click(b,'preview');click(b,'save')
    assert run('payment show',dict(payment=paid['id']))['current']['available_minor_units']==15000
    b.evaluate("Array.from(document.querySelectorAll('#payment-record button')).find(x=>x.textContent==='Apply available credit').click()")
    wait(b)
    b.evaluate(f"document.querySelector('[data-invoice=\"{invoices[0]['id']}\"] input[type=checkbox]').click()")
    wait(b);click(b,'preview');click(b,'save')
    assert run('invoice settlement',dict(invoice=invoices[0]['id']))['due_minor_units']==0
    assert run('invoice settlement',dict(invoice=invoices[1]['id']))['due_minor_units']==10000
    shot(b,tmp_path,'payment-credit-reused',width)


@pytest.mark.parametrize('width', [1280,390])
def test_correct_receipt_and_applied_invoice_with_customs(register_browser,width,tmp_path):
    from tests.test_service_sales_browser import _fill, _preview, _click, _saved
    env,b=register_browser,register_browser.browser
    run=lambda name,data,**headers: _command(b,env.site,name.replace(' ','.'),data,**headers)
    b.viewport(width,900)
    income=run('account create',dict(name='Correction browser income',type='income'))['id']
    payer=run('customer create',dict(name='Correction Payment Customer'))['id']
    pm=run('payment-method create',dict(name='Correction cheque',kind='check'))['id']
    exempt=next(row['id'] for row in run('sales-tax-code list',{})['items'] if not row['taxable'])
    item=run('item create',dict(name='Correction labor',type='service',sales_enabled=True,
        income_account_id=income,price='100',description='Service',sales_tax_code_id=exempt))['id']
    invoice=run('invoice post',dict(customer=payer,date='2026-06-01',lines=[dict(item=item,quantity='1',net_amount='100.00')]))
    definitions={}
    for kind,value in {'text':'Envelope','number':'12.500000001','date':'2026-06-02','bool':False,'choice':'Check'}.items():
        definitions[kind]=run('custom-field create',dict(name='Payment browser '+kind,kind=kind,scopes=['payment'],
            default=value,**(dict(choices=[dict(value='Check'),dict(value='Cash')]) if kind=='choice' else {})))['id']
    paid=run('payment receive',dict(customer=payer,date='2026-06-02',amount='50.00',payment_method=pm,deposit_to=env.bank['id'],
        operation_key='browser-edit-receipt',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='50.00')])))
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/receive-payments?payment='+paid['id']+'&mode=update')
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    assert b.evaluate("document.querySelectorAll('#payment-custom [data-definition]').length")==5
    field(b,'amount','60.00');field(b,'memo','Additional ten received');field(b,'reason','Correct received amount')
    b.evaluate(f"(()=>{{let n=document.querySelector('[data-definition=\"{definitions['text']}\"]');n.value='Corrected envelope';n.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    click(b,'preview');shot(b,tmp_path,'receipt-correction-preview',width);click(b,'save')
    current=run('payment show',dict(payment=paid['id']))
    assert current['current']['received_minor_units']==6000 and current['current']['available_minor_units']==1000
    assert current['revision']['custom_fields_snapshot'][definitions['text']]['value']=='Corrected envelope'
    assert current['revision']['custom_fields_snapshot'][definitions['number']]['value']=='12.500000001'
    assert 'CDP bank' in b.evaluate("document.querySelector('#payment-record').innerText")
    assert 'undefined' not in b.evaluate("document.querySelector('#payment-record').innerText")
    # Existing invoice editor uses the complete authenticated settlement baseline.
    b.navigate(base+'/invoice/'+invoice['id']+'/update')
    b.wait_for("!!document.querySelector('form[data-sales-form]')")
    _fill(b,'f:memo','Revised service description');_fill(b,'ctx:reason','Correct service description')
    _preview(b)
    b.wait_for("document.querySelector('#invoice-settlement-preview')?.dataset.complete==='true'")
    assert 'Every proposed settlement change' in b.evaluate("document.querySelector('#invoice-settlement-preview').innerText")
    shot(b,tmp_path,'applied-invoice-correction',width)
    _click(b,'submit');assert _saved(b,'invoice')==invoice['id']
    corrected=run('invoice show',dict(invoice=invoice['id']))
    assert corrected['revision']['memo']=='Revised service description' and corrected['settlement_current']['due_minor_units']==5000
    assert run('payment show',dict(payment=paid['id']))['current']['available_minor_units']==1000


@pytest.mark.parametrize('width', [1280, 390])
def test_agent_draft_fixed_amount_save_new_query_and_readonly_recovery(register_browser, width, tmp_path):
    env,b=register_browser,register_browser.browser
    run=lambda name,data,**headers: _command(b,env.site,name.replace(' ','.'),data,**headers)
    b.viewport(width,900)
    payer=run('customer create',dict(name='Shared fixed payment customer'))['id']
    pm=run('payment-method create',dict(name='Shared payment cheque',kind='check'))['id']
    income=run('account create',dict(name='Shared payment income',type='income'))['id']
    code=next(row['id'] for row in run('sales-tax-code list',{})['items'] if not row['taxable'])
    item=run('item create',dict(name='Shared fixed labor',type='service',sales_enabled=True,
        income_account_id=income,description='Completed work',sales_tax_code_id=code,price='100'))['id']
    invoices=[run('invoice post',dict(customer=payer,date='2026-06-01',number=f'SHARED-FIXED-{i}',
        lines=[dict(item=item,quantity='1',net_amount='100')])) for i in range(2)]
    run('company update',dict(automatically_calculate_payments=True))
    draft=run('payment selection create',dict(mode='new_receipt',customer=payer,date='2026-06-02',amount='150'))
    draft=run('payment selection update',dict(selection=draft['id'],expected_version=1,set_items=[
        dict(invoice=invoices[0]['id'],expected_version=1,amount='100',amount_origin='entered'),
        dict(invoice=invoices[1]['id'],expected_version=1,amount='50',amount_origin='entered')]))
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/payment-drafts')
    b.wait_for("document.body.innerText.includes('Shared fixed payment customer')")
    b.evaluate(f"document.querySelector('a[href$=\"selection={draft['id']}\"]').click()")
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    assert b.evaluate("new URL(location.href).searchParams.get('selection')")==draft['id']
    b.evaluate(f"document.querySelector('[data-invoice=\"{invoices[0]['id']}\"] input[type=checkbox]').click()")
    wait(b)
    state=run('payment selection show',dict(selection=draft['id']))
    rows=run('payment selection items',dict(selection=draft['id']))['items']
    assert state['amount']['minor_units']==15000 and state['unapplied_minor_units']==10000
    assert len(rows)==1 and rows[0]['amount_minor_units']==5000 and rows[0]['amount_origin']=='entered'
    field(b,'method',pm);field(b,'destination',env.bank['id']);field(b,'reference','SHARED-CHECK-001')
    click(b,'preview');click(b,'save-new')
    assert b.evaluate("document.querySelector('#payment-amount').value")==''
    assert b.evaluate("document.querySelector('#payment-method').value")==pm
    assert b.evaluate("document.querySelector('#payment-destination').value")==env.bank['id']
    assert b.evaluate("document.querySelector('#payment-reference').value")==''
    assert not b.evaluate("document.querySelector('#payment-form').hidden")
    paid=run('payment query',dict(customer=payer))['items'][0]
    assert paid['received_minor_units']==15000 and paid['unapplied_minor_units']==10000
    assert run('payment selection show',dict(selection=draft['id']))['state']=='consumed'
    shot(b,tmp_path,'payment-save-new-shared-handoff',width)
    b.navigate(base+'/payment?payment_method='+pm+'&available=1&q=SHARED-CHECK-001')
    b.wait_for("document.body.innerText.includes('1 matching payments')")
    assert 'Shared fixed payment customer' in b.evaluate('document.body.innerText')
    shot(b,tmp_path,'payment-query-method',width)
    b.navigate(base+'/receive-payments?payment='+paid['id'])
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    b.evaluate("Array.from(document.querySelectorAll('#payment-record button')).find(x=>x.textContent==='History').click()")
    wait(b)
    b.evaluate("Array.from(document.querySelectorAll('#payment-history button')).find(x=>x.textContent==='Original operation').click()")
    wait(b)
    assert '150.00 USD' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    assert 'No explicit reason was supplied' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    b.evaluate("Array.from(document.querySelectorAll('#payment-preview-result button')).find(x=>x.textContent==='View original applications').click()")
    wait(b)
    assert 'Original applications: 1 complete recorded items' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    before=run('payment show',dict(payment=paid['id']))
    path=Path(run('company show',{})['path'])/'company.db'
    original_rows=recorded_state(path)
    b.evaluate("Array.from(document.querySelectorAll('#payment-preview-result button')).find(x=>x.textContent==='Preview exact original request').click()")
    wait(b)
    assert 'No new payment or settlement change' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    after=run('payment show',dict(payment=paid['id']))
    assert {key:value for key,value in before.items() if key!='settlement_guard'}=={key:value for key,value in after.items() if key!='settlement_guard'}
    assert recorded_state(path)==original_rows
    shot(b,tmp_path,'payment-original-recovery',width)


@pytest.mark.parametrize('width', [1280, 390])
def test_lost_response_exact_recovery_locks_draft_and_voids_without_duplicate(register_browser, width, tmp_path):
    from tests.test_row8_register_browser import _key, _type
    env,b=register_browser,register_browser.browser
    run=lambda name,data,**headers: _command(b,env.site,name.replace(' ','.'),data,**headers)
    b.viewport(width,900)
    payer=run('customer create',dict(name='Lost response customer'))['id']
    pm=run('payment-method create',dict(name='Lost response cash',kind='cash'))['id']
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/receive-payments?customer='+payer)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    field(b,'date','2026-06-02');field(b,'method',pm);field(b,'destination',env.bank['id'])
    b.evaluate("document.querySelector('#payment-amount').focus()")
    _type(b,'20+20');_key(b,'Enter')
    b.wait_for("document.querySelector('#payment-amount').value==='40'")  # Exact calculator uses shortest decimal.
    wait(b)
    assert run('payment query',dict(customer=payer))['total_count']==0
    click(b,'load');click(b,'preview')
    # Actual server commit succeeds; only its delivery to this browser is lost.
    b.evaluate("""(()=>{const original=window.fetch;let lost=false;window.fetch=async(...args)=>{
      const response=await original(...args);
      if(!lost && String(args[0]).endsWith('/commands/payment.receive')){lost=true;throw new Error('Injected response loss after commit');}
      return response;
    };})()""")
    b.evaluate("document.querySelector('#payment-save-new').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert not b.evaluate("document.querySelector('#payment-error').hidden")
    assert b.evaluate("[...document.querySelectorAll('#payment-form input,#payment-form button')].every(x=>x.disabled)")
    paid=run('payment query',dict(customer=payer))['items']
    assert len(paid)==1 and paid[0]['received_minor_units']==4000
    path=Path(run('company show',{})['path'])/'company.db'
    before=recorded_state(path)
    url=b.evaluate('location.href');b.navigate(url)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'")
    assert not b.evaluate("document.querySelector('#payment-retry').hidden")
    shot(b,tmp_path,'payment-ambiguous-locked',width)
    click(b,'retry')
    assert 'Recovered original payment' in b.evaluate("document.querySelector('#payment-message').innerText")
    assert recorded_state(path)==before
    assert run('payment query',dict(customer=payer))['total_count']==1
    b.evaluate("Array.from(document.querySelectorAll('#payment-record button')).find(x=>x.textContent==='Void unapplied receipt').click()")
    wait(b);field(b,'reason','Cancel the test remittance');click(b,'preview');click(b,'save')
    current=run('payment show',dict(payment=paid[0]['id']))
    assert current['status']=='voided' and current['current']['available_minor_units']==0
    assert run('customer show',dict(customer=payer))['current_balance']['minor_units']==0
    shot(b,tmp_path,'payment-voided-after-recovery',width)


@pytest.mark.parametrize('width', [1280,390])
def test_stale_receipt_keeps_inputs_and_custom_empty_clear_exact_number(register_browser,width,tmp_path):
    env,b=register_browser,register_browser.browser
    run=lambda name,data,**headers: _command(b,env.site,name.replace(' ','.'),data,**headers)
    b.viewport(width,900)
    payer=run('customer create',dict(name='Stale custom payment customer'))['id']
    pm=run('payment-method create',dict(name='Stale custom cash',kind='cash'))['id']
    definitions={}
    for kind,value in {'text':'Initial text','date':'2026-06-02','bool':True,'number':'12.500000001','choice':'Cash'}.items():
        definitions[kind]=run('custom-field create',dict(name='Stale payment '+kind,kind=kind,scopes=['payment'],default=value,
            required=kind=='number',**(dict(choices=[dict(value='Cash'),dict(value='Check')]) if kind=='choice' else {})))['id']
    paid=run('payment receive',dict(customer=payer,date='2026-06-02',amount='40',payment_method=pm,deposit_to=env.bank['id'],operation_key='stale-original'))
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/receive-payments?payment='+paid['id']+'&mode=update')
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    field(b,'amount','50');field(b,'memo','Keep this draft memo');field(b,'reason','Correct cash and recorded custom facts')
    for kind,value in [('text',''),('number','12.600000001'),('bool','false'),('choice','Check')]:
        b.evaluate(f"(()=>{{let n=document.querySelector('[data-definition=\"{definitions[kind]}\"]');n.value={json.dumps(value)};n.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    b.evaluate(f"(()=>{{let n=document.querySelector('[data-custom-action=\"{definitions['date']}\"]');n.value='clear';n.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    other=run('payment show',dict(payment=paid['id']))
    run('payment update',dict(payment=paid['id'],expected_version=1,amount='45',operation_key='stale-other-writer',
        settlement_guard=other['settlement_guard']),**{'X-Bookflow-Reason':'Record another cash correction'})
    b.evaluate("document.querySelector('#payment-preview').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert 'E_VERSION_CONFLICT' in b.evaluate("document.querySelector('#payment-error').innerText")
    assert b.evaluate("document.querySelector('#payment-amount').value")=='50'
    assert b.evaluate("document.querySelector('#payment-memo').value")=='Keep this draft memo'
    shot(b,tmp_path,'payment-stale-retained',width)
    click(b,'review');click(b,'preview');click(b,'save')
    current=run('payment show',dict(payment=paid['id']))
    assert current['version']==3 and current['revision']['total']['minor_units']==5000
    assert current['revision']['memo']=='Keep this draft memo'
    values=current['revision']['custom_fields_snapshot']
    assert values[definitions['text']]['value']=='' and definitions['date'] not in values
    assert values[definitions['number']]['value']=='12.600000001'
    assert values[definitions['bool']]['value'] is False and values[definitions['choice']]['value']=='Check'
    original=run('payment show',dict(payment=paid['id'],revision=1))
    assert original['revision']['total']['minor_units']==4000
    assert original['revision']['custom_fields_snapshot'][definitions['text']]['value']=='Initial text'
    assert original['revision']['custom_fields_snapshot'][definitions['date']]['value']=='2026-06-02'
    shot(b,tmp_path,'payment-custom-corrected-after-review',width)


@pytest.mark.parametrize('width',[1280,390])
def test_preferences_credit_draft_application_history_and_internal_print(register_browser,width,tmp_path):
    from tests.test_service_sales_browser import _fill, _click
    env,b=register_browser,register_browser.browser
    run=lambda name,data,**headers: _command(b,env.site,name.replace(' ','.'),data,**headers)
    b.viewport(width,900)
    payer=run('customer create',dict(name='Preference payment customer'))['id']
    pm=run('payment-method create',dict(name='Preference cash',kind='cash'))['id']
    income=run('account create',dict(name='Preference payment income',type='income'))['id']
    code=next(row['id'] for row in run('sales-tax-code list',{})['items'] if not row['taxable'])
    item=run('item create',dict(name='Preference payment work',type='service',sales_enabled=True,
        income_account_id=income,description='Work completed',sales_tax_code_id=code,price='100'))['id']
    invoice=run('invoice post',dict(customer=payer,date='2026-06-01',number='PREFERENCE-CREDIT-INVOICE',lines=[dict(item=item,quantity='1')]))
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/company/self/update')
    b.wait_for("document.body.innerText.includes('Customer payment preferences')")
    preferences=('automatically_apply_payments','automatically_calculate_payments','use_undeposited_funds_for_payments')
    for preference in preferences:_fill(b,'f:'+preference,'true')
    _click(b,'submit');b.wait_for("document.body.innerText.includes('Saved successfully')")
    assert all(run('company show',{})['info'][field] for field in preferences)
    b.navigate(base+'/receive-payments?customer='+payer)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    assert 'Undeposited Funds' in b.evaluate("document.querySelector('#payment-destination').selectedOptions[0].textContent")
    field(b,'date','2026-06-02');field(b,'number','PAY-PREFERENCE-EXPLICIT');field(b,'method',pm);click(b,'load');field(b,'amount','40')
    assert b.evaluate("document.querySelector('#payment-invoices input[type=checkbox]').checked")
    assert '40.00' in b.evaluate("document.querySelector('#payment-totals').innerText")
    click(b,'clear')
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    assert run('payment selection show',dict(selection=selection))['item_count']==0
    assert run('payment query',dict(customer=payer))['total_count']==0
    click(b,'auto');click(b,'preview');click(b,'save')
    paid=run('payment query',dict(customer=payer))['items'][0]
    shown=run('payment show',dict(payment=paid['id']))
    assert shown['number']=='PAY-PREFERENCE-EXPLICIT'
    assert shown['revision']['profile']['deposit_account']['full_name']=='Undeposited Funds'
    b.evaluate("window.paymentPrintCalls=0;window.print=()=>{window.paymentPrintCalls++;}")
    b.evaluate("Array.from(document.querySelectorAll('#payment-record button')).find(x=>x.textContent==='Print internal receipt').click()")
    wait(b);assert b.evaluate('window.paymentPrintCalls')==1
    pdf=base64.b64decode(b.call('Page.printToPDF',{'printBackground':True})['data'])
    assert pdf.startswith(b'%PDF');(tmp_path/f'payment-internal-receipt-{width}.pdf').write_bytes(pdf)
    # The receipt's evidence link must use the existing transaction identity,
    # retaining ordinary note controls without changing financial headers.
    before_note=recorded_state(Path(run('company show',{})['path'])/'company.db')
    b.evaluate("Array.from(document.querySelectorAll('#payment-record a')).find(x=>x.textContent==='Notes and attachments').click()")
    b.wait_for("document.querySelector('[data-annotations]')?.dataset.ready==='true'")
    b.wait_for("!document.querySelector('[data-section=notes]').hasAttribute('aria-busy')")
    assert b.evaluate("JSON.parse(document.querySelector('[data-annotations]').dataset.annotations).target.record_type")=='transaction'
    b.evaluate("document.querySelector('#annotation-note').value='Customer confirmed this remittance';document.querySelector('[data-note-add]').requestSubmit()")
    b.wait_for("!document.querySelector('[data-note-add]').hasAttribute('aria-busy')")
    assert b.evaluate("document.querySelector('[data-note-add] [data-status]').innerText")=='Note added.'
    b.wait_for("document.querySelector('[data-section=notes] [data-items]').innerText.includes('Customer confirmed this remittance')")
    assert any(n['body']=='Customer confirmed this remittance' for n in run('note list',dict(record_type='transaction',record_id=paid['id']))['items'])
    after_note=recorded_state(Path(run('company show',{})['path'])/'company.db')
    for table in before_note:
        if table not in ('audit_events','audit_entries'):assert before_note[table]==after_note[table],table
    shot(b,tmp_path,'payment-evidence-note',width)
    app=run('payment settlement',dict(payment=paid['id'],kind='applications'))['items'][0]
    b.navigate(base+'/application/'+app['application_id']+'/history')
    b.wait_for("document.body.innerText.includes('Application and allocation history')")
    assert '2 recorded entries' in b.evaluate('document.body.innerText')
    assert 'PREFERENCE-CREDIT-INVOICE' in b.evaluate('document.body.innerText')
    shot(b,tmp_path,'payment-application-history',width)
    # A second interface prepares existing credit; opening it must reuse its ID,
    # fixed source/version and entered row rather than create new cash or a draft.
    credit=run('payment receive',dict(customer=payer,date='2026-06-02',amount='20',payment_method=pm,operation_key='credit-handoff'))
    draft=run('payment selection create',dict(mode='existing_credit',payment=credit['id'],date='2026-06-02',amount='20'))
    draft=run('payment selection update',dict(selection=draft['id'],expected_version=1,set_items=[
        dict(invoice=invoice['id'],expected_version=2,amount='20',amount_origin='entered')]))
    path=Path(run('company show',{})['path'])/'company.db';before=recorded_state(path)
    b.navigate(base+'/receive-payments?selection='+draft['id'])
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    assert 'Apply existing payment credit' in b.evaluate("document.querySelector('#payment-title').innerText")
    assert b.evaluate("new URL(location.href).searchParams.get('selection')")==draft['id']
    click(b,'preview');click(b,'save')
    assert run('invoice settlement',dict(invoice=invoice['id']))['due_minor_units']==4000
    after=recorded_state(path)
    for table in ('transaction_revisions','posting_batches','posting_lines','posting_line_sources'):
        assert after[table]==before[table]
    b.navigate(base+'/payment?date_from=2026-06-03&date_to=2026-06-04&q=Preference+payment+customer')
    b.wait_for("document.body.innerText.includes('0 matching payments')")
    shot(b,tmp_path,'payment-date-filter',width)
    b.navigate(base+'/invoice/'+invoice['id']+'/settlement?as_of=2026-05-31')
    b.wait_for("document.body.innerText.includes('Recorded applications and reversals')")
    assert 'Not effective' in b.evaluate("document.querySelector('[aria-label=\"Dated invoice settlement\"]').innerText")
    assert 'Due 40.00 USD' in b.evaluate("document.querySelector('[aria-label=\"All committed current settlement\"]').innerText")
    shot(b,tmp_path,'invoice-dated-current-settlement',width)
