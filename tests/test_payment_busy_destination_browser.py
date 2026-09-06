"""F12/F13: delayed real reads and intentional default/override input."""
import json
import pytest
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser, _type, _key
from tests.test_payment_review_gui import setup, invoice_setup
from tests.test_customer_payment_browser import field,click,wait,shot

@pytest.mark.parametrize('width',[1280,390])
def test_delayed_read_pauses_every_editor_and_retains_captured_cash(register_browser,tmp_path,width):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    invoice_setup(run,payer);click(b,'refresh-draft')
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    b.evaluate("""(()=>{const original=window.fetch;window.reviewReadHeld=false;
      window.fetch=async(...args)=>{const response=await original(...args);
        if(String(args[0]).includes('/commands/payment.selection.show')&&!window.reviewReadHeld){
          window.reviewReadHeld=true;await new Promise(resolve=>window.releaseReviewRead=resolve);
        } return response;};document.querySelector('#payment-refresh-draft').click();})()""")
    b.wait_for('window.reviewReadHeld===true')
    # Approved alternative to accepting input that cannot be retained: native
    # editing is unavailable across every header/row/custom editor while busy.
    assert b.evaluate("Array.from(document.querySelectorAll('#payment-workspace input,#payment-workspace select,#payment-workspace textarea')).every(x=>x.inert)")
    assert 'Editing is paused' in b.evaluate("document.querySelector('#payment-busy-status').innerText")
    b.evaluate("document.querySelector('#payment-amount').focus()")
    assert not b.evaluate("document.activeElement===document.querySelector('#payment-amount')")
    _type(b,'12');_key(b,'Tab')
    assert b.evaluate("document.querySelector('#payment-amount').value")=='10.00'
    # Also exercise an event already delivered by an integration before the
    # lock: its captured value must survive DOM replacement during the read.
    b.evaluate("(()=>{let x=document.querySelector('#payment-amount');x.value='12';x.dispatchEvent(new Event('change',{bubbles:true}));})()")
    assert 'queued' in b.evaluate("document.querySelector('#payment-busy-status').innerText")
    shot(b,tmp_path,'paused-editing-queued-captured-input',width)
    b.evaluate('window.releaseReviewRead()');wait(b)
    assert b.evaluate("document.querySelector('#payment-amount').value") in ('12','12.00')
    assert run('payment selection show',{'selection':selection})['amount']['minor_units']==1200
    assert run('payment query',{'customer':payer})['items']==[]
    assert b.evaluate("Array.from(document.querySelectorAll('#payment-workspace input,#payment-workspace select,#payment-workspace textarea')).every(x=>!x.inert)")
    b.evaluate("document.querySelector('#payment-amount').focus();document.querySelector('#payment-amount').select()")
    _type(b,'13');_key(b,'Tab');wait(b)
    assert run('payment selection show',{'selection':selection})['amount']['minor_units']==1300
    shot(b,tmp_path,'editable-after-completion',width)

@pytest.mark.parametrize('width',[1280,390])
def test_uf_omission_explicit_override_reset_and_save_new(register_browser,tmp_path,width):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    pm=b.evaluate("document.querySelector('#payment-method').value")
    info=run('company show',{})['info']
    run('company update',{'expected_version':info['version'],'use_undeposited_funds_for_payments':True})
    b.navigate(base+'/receive-payments?customer='+payer)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    field(b,'date','2026-06-02');field(b,'amount','10');field(b,'method',pm)
    b.evaluate("""(()=>{const original=window.fetch;window.paymentRequests=[];window.fetch=(...args)=>{
      if(String(args[0]).includes('/commands/payment.receive'))window.paymentRequests.push(JSON.parse(args[1].body));
      return original(...args);};})()""")
    assert b.evaluate("document.querySelector('#payment-destination-label').hidden")
    assert not b.evaluate("document.querySelector('#payment-destination-default').hidden")
    click(b,'preview')
    assert 'deposit_to' not in b.evaluate('window.paymentRequests.at(-1)')
    assert 'Undeposited Funds' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    shot(b,tmp_path,'uf-default-form',width)
    b.evaluate("document.querySelector('#payment-preview-result').scrollIntoView()")
    shot(b,tmp_path,'uf-default-preview',width)
    click(b,'destination-override')
    field(b,'destination','')
    b.evaluate("document.querySelector('#payment-preview').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert 'Choose a bank or Undeposited Funds' in b.evaluate("document.querySelector('#payment-error').innerText")
    b.evaluate("document.querySelector('#payment-error').hidden=true")
    field(b,'destination',register_browser.bank['id']);click(b,'preview')
    assert b.evaluate('window.paymentRequests.at(-1).deposit_to')==register_browser.bank['id']
    assert 'explicit choice' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    b.evaluate("document.querySelector('#payment-preview-result').scrollIntoView()")
    shot(b,tmp_path,'explicit-bank-preview',width)
    click(b,'destination-reset');click(b,'preview')
    assert 'deposit_to' not in b.evaluate('window.paymentRequests.at(-1)')
    click(b,'save-new')
    rows=run('payment query',{'customer':payer})['items'];assert len(rows)==1
    saved=run('payment show',{'payment':rows[0]['id']})
    assert saved['revision']['profile']['deposit_account']['full_name']=='Undeposited Funds'
    assert saved['current']['received_minor_units']==1000
    assert b.evaluate("document.querySelector('#payment-destination-label').hidden")
    assert b.evaluate("document.querySelector('#payment-amount').value")==''
    assert all('deposit_to' not in r for r in b.evaluate('window.paymentRequests.slice(-2)'))
    recovered=run('payment operation show',{'operation_key':b.evaluate('window.paymentRequests.at(-1).operation_key')})
    assert 'deposit_to' not in recovered['request']['input']
    (tmp_path/'request-origins.json').write_text(json.dumps(b.evaluate('window.paymentRequests'),indent=2))

@pytest.mark.parametrize('width',[1280,390])
def test_queued_row_events_keep_identity_value_and_removal(register_browser,tmp_path,width):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    invoice,_,_=invoice_setup(run,payer);click(b,'refresh-draft')
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    b.evaluate("""(()=>{const original=window.fetch;window.held=false;
      window.fetch=async(...args)=>{const response=await original(...args);
        if(String(args[0]).includes('/commands/payment.selection.show')&&!window.held){
          window.held=true;await new Promise(resolve=>window.releaseRead=resolve);
        } return response;};document.querySelector('#payment-refresh-draft').click();})()""")
    b.wait_for('window.held===true')
    # Delivery of pre-lock change events: both closures outlive a row redraw.
    b.evaluate("""(()=>{let row=document.querySelector('#payment-invoices tr');
      let check=row.querySelector('input[type=checkbox]');check.checked=true;
      check.dispatchEvent(new Event('change',{bubbles:true}));
      let amount=row.querySelector('input[type=text]');amount.value='1.23';
      amount.dispatchEvent(new Event('change',{bubbles:true}));
      // A read/redraw can replace display state; event payloads stay immutable.
      check.checked=false;amount.value='9.99';window.releaseRead();})()""")
    wait(b)
    header=run('payment selection show',{'selection':selection})
    rows=run('payment selection items',{'selection':selection,'revision':header['version']})['items']
    assert len(rows)==1 and rows[0]['invoice_id']==invoice['id']
    assert rows[0]['amount_minor_units']==123 and rows[0]['amount_origin']=='entered'
    b.evaluate("window.held=false;document.querySelector('#payment-refresh-draft').click()")
    b.wait_for('window.held===true')
    b.evaluate("""(()=>{let check=document.querySelector('#payment-invoices input[type=checkbox]');
      check.checked=false;check.dispatchEvent(new Event('change',{bubbles:true}));
      check.checked=true;window.releaseRead();})()""")
    wait(b)
    assert run('payment selection show',{'selection':selection})['item_count']==0
    assert run('payment query',{'customer':payer})['items']==[]
    shot(b,tmp_path,'queued-row-removal-retained',width)
