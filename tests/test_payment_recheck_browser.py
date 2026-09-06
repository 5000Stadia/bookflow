import json
import pytest
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser, _key, _type, _tab_to
from tests.test_payment_review_gui import setup
from tests.test_customer_payment_browser import click,wait,shot

@pytest.mark.parametrize('width',[1280,390])
def test_pending_row_patch_survives_attempted_customer_switch(register_browser,tmp_path,width):
    from tests.test_payment_review_gui import invoice_setup
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    invoice,_,_=invoice_setup(run,payer)
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    saved=run('payment selection show',dict(selection=selection))
    saved=run('payment selection update',dict(selection=selection,expected_version=saved['version'],
        set_items=[dict(invoice=invoice['id'],expected_version=1,amount='2',amount_origin='entered')]))
    click(b,'refresh-draft')
    run('payment selection update',dict(selection=selection,expected_version=saved['version'],amount='11'))
    selector=f'tr[data-invoice="{invoice["id"]}"] input[type="text"]'
    b.evaluate(f"(()=>{{let x=document.querySelector({json.dumps(selector)});x.value='5';x.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    for name in ('Critic Bob','Critic Alice'):
        b.evaluate(f"(()=>{{let x=document.querySelector('#payment-customer');x.value={json.dumps(name)};x.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")
        b.evaluate("document.querySelector('#payment-find-customer').click()")
        b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
        b.evaluate(f"Array.from(document.querySelectorAll('#payment-customer-results button')).find(x=>x.textContent==={json.dumps(name)}).click()")
        b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
        assert b.evaluate("new URL(location.href).searchParams.get('selection')")==selection
        if name=='Critic Bob':
            assert 'before changing its customer' in b.evaluate("document.querySelector('#payment-error').innerText")
            assert b.evaluate("document.querySelector('#payment-save').disabled")
    click(b,'review')
    rows=run('payment selection items',dict(selection=selection))['items']
    assert [(row['invoice_id'],row['amount_minor_units']) for row in rows]==[(invoice['id'],500)]
    assert run('payment query',dict(customer=payer))['items']==[]
    assert run('payment query',dict(customer=other))['items']==[]
    shot(b,tmp_path,'pending-row-customer-switch-retained',width)

@pytest.mark.parametrize('width',[1280,390])
def test_typed_unresolved_customer_cannot_repreview_old_payer(register_browser,tmp_path,width):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    click(b,'preview')
    # Ordinary keyboard edit: input/change invalidate the preview. No chosen Bob
    # identity yet; Preview must not silently use the still-held Alice identity.
    b.evaluate("document.querySelector('#payment-customer').focus();document.querySelector('#payment-customer').select()")
    _type(b,'Critic Bob');_key(b,'Tab')
    b.evaluate("document.querySelector('#payment-preview').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    disabled=b.evaluate("document.querySelector('#payment-save').disabled")
    facts={'customer':b.evaluate("document.querySelector('#payment-customer').value"),'save_disabled':disabled,'error':b.evaluate("document.querySelector('#payment-error').innerText")}
    if not disabled:
        click(b,'save');facts['alice']=run('payment query',{'customer':payer})['items'];facts['bob']=run('payment query',{'customer':other})['items']
    (tmp_path/'typed-payer.json').write_text(json.dumps(facts,indent=2));shot(b,tmp_path,'typed-payer',width)
    assert disabled,facts

@pytest.mark.parametrize('width',[1280,390])
def test_stale_shared_amount_review_retains_my_entered_cash(register_browser,tmp_path,width):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    saved=run('payment selection show',dict(selection=selection))
    # A legitimate concurrent edit on the same shared draft.
    run('payment selection update',dict(selection=selection,expected_version=saved['version'],amount='11'))
    b.evaluate("let x=document.querySelector('#payment-amount');x.value='12';x.dispatchEvent(new Event('change',{bubbles:true}))")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    error=b.evaluate("document.querySelector('#payment-error').innerText")
    assert 'E_VERSION_CONFLICT' in error,error
    click(b,'review')
    facts={'error':error,'typed_after_review':b.evaluate("document.querySelector('#payment-amount').value"),'message':b.evaluate("document.querySelector('#payment-message').innerText"),'draft':run('payment selection show',dict(selection=selection))}
    comparison=b.evaluate("document.querySelector('#payment-reviewed-comparisons').innerText")
    assert '10.00' in comparison and '11.00' in comparison and '12 USD' in comparison
    assert facts['draft']['amount']['minor_units']==1200
    (tmp_path/'stale-selection.json').write_text(json.dumps(facts,indent=2));shot(b,tmp_path,'stale-selection',width)
    assert facts['typed_after_review']=='12',facts
    assert run('payment query',dict(customer=payer))['items']==[]
    click(b,'preview');click(b,'save')
    assert [row['received_minor_units'] for row in run('payment query',dict(customer=payer))['items']]==[1200]

@pytest.mark.parametrize('width',[1280,390])
def test_keyboard_preview_and_single_save(register_browser,tmp_path,width):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    # Setup is public HTTP; actual final editing and preview/save are keyboard.
    b.evaluate("document.querySelector('#payment-amount').focus();document.querySelector('#payment-amount').select()")
    _type(b,'12+3');_key(b,'Enter');wait(b)
    assert b.evaluate("document.querySelector('#payment-amount').value") in ['15','15.00']
    assert run('payment query',dict(customer=payer))['items']==[]
    _tab_to(b,'#payment-preview');_key(b,'Enter');wait(b)
    _tab_to(b,'#payment-save');_key(b,'Enter');wait(b)
    rows=run('payment query',dict(customer=payer))['items'];assert len(rows)==1 and rows[0]['received_minor_units']==1500
    shot(b,tmp_path,'keyboard-saved',width)
