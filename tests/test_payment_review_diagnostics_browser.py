"""Retained independent Gate C business oracles, now regression coverage."""
import json
import pytest
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser,_command
from tests.test_customer_payment_browser import field,click,wait,shot


def prepare(env):
    b=env.browser
    run=lambda name,data,**headers:_command(b,env.site,name.replace(' ','.'),data,**headers)
    payer=run('customer create',dict(name='Critic diagnostic customer'))['id']
    pm=run('payment-method create',dict(name='Critic diagnostic method',kind='cash'))['id']
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    return b,run,payer,pm,base

@pytest.mark.parametrize('width',[1280,390])
def test_stale_review_displays_old_and_current_cash(register_browser,tmp_path,width):
    b,run,payer,pm,base=prepare(register_browser);b.viewport(width,900)
    paid=run('payment receive',dict(customer=payer,date='2026-06-02',amount='10',payment_method=pm,deposit_to=register_browser.bank['id'],operation_key='critic-diag-first'))
    b.navigate(base+'/receive-payments?payment='+paid['id']+'&mode=update');b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    field(b,'amount','12');field(b,'reason','Correct cash after recount')
    shown=run('payment show',dict(payment=paid['id']))
    run('payment update',dict(payment=paid['id'],expected_version=1,amount='11',settlement_guard=shown['settlement_guard'],operation_key='critic-diag-other'),**{'X-Bookflow-Reason':'Another recorded recount'})
    b.evaluate("document.querySelector('#payment-preview').click()");b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    error=b.evaluate("document.querySelector('#payment-error').innerText")
    comparisons=b.evaluate("document.querySelector('[data-comparisons]').innerText")
    assert 'E_VERSION_CONFLICT' in error
    shot(b,tmp_path,'stale-without-amount-comparison',width)
    click(b,'review')
    facts=dict(error=error,comparisons=comparisons,after_review=b.evaluate("document.querySelector('#payment-message').innerText"),typed=b.evaluate("document.querySelector('#payment-amount').value"))
    (tmp_path/'diagnostics.json').write_text(json.dumps(facts,indent=2))
    assert facts['typed']=='12'
    assert '10.00' in comparisons and '11.00' in comparisons,facts

@pytest.mark.parametrize('width',[1280,390])
def test_blank_boolean_set_does_not_silently_record_false(register_browser,tmp_path,width):
    b,run,payer,pm,base=prepare(register_browser);b.viewport(width,900)
    definition=run('custom-field create',dict(name='Critic boolean fact',kind='bool',scopes=['payment']))['id']
    b.navigate(base+'/receive-payments?customer='+payer);b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    field(b,'date','2026-06-02');field(b,'amount','10');field(b,'method',pm);field(b,'destination',register_browser.bank['id'])
    b.evaluate(f"(()=>{{let e=document.querySelector('[data-custom-action=\"{definition}\"]');e.value='set';e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    choice=b.evaluate(f"document.querySelector('[data-definition=\"{definition}\"]').selectedOptions[0].textContent")
    b.evaluate("document.querySelector('#payment-preview').click()");b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    rejected=not b.evaluate("document.querySelector('#payment-error').hidden")
    recorded=None
    if not rejected:
        click(b,'save');p=run('payment query',dict(customer=payer))['items'][0]
        recorded=run('payment show',dict(payment=p['id']))['revision']['custom_fields_snapshot']
    facts=dict(choice=choice,rejected=rejected,recorded=recorded)
    (tmp_path/'blank-bool.json').write_text(json.dumps(facts,indent=2));shot(b,tmp_path,'blank-boolean-record',width)
    assert rejected,facts


@pytest.mark.timeout(300)
def test_stale_review_loads_all_203_audited_changes(register_browser,tmp_path):
    b,run,payer,pm,base=prepare(register_browser)
    paid=run('payment receive',dict(customer=payer,date='2026-06-02',amount='10',payment_method=pm,
        deposit_to=register_browser.bank['id'],operation_key='paged-review-cash'))
    b.navigate(base+'/receive-payments?payment='+paid['id']+'&mode=update')
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'");wait(b)
    field(b,'amount','12');field(b,'reason','Retain my correction after reviewing all changes')
    for index in range(203):
        shown=run('payment show',dict(payment=paid['id']))
        run('payment update',dict(payment=paid['id'],expected_version=shown['version'],memo=f'Audited correction {index}',
            settlement_guard=shown['settlement_guard'],operation_key=f'paged-review-{index}'),
            **{'X-Bookflow-Reason':f'Record actual memo correction {index}'})
    b.evaluate("document.querySelector('#payment-preview').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=120)
    comparisons=b.evaluate("document.querySelector('[data-comparisons]').innerText")
    assert '203 complete recorded changes' in comparisons
    assert comparisons.count('change event ')==203
    assert comparisons.count('Latest writer:')==203
    assert 'Saved receipt: 10.00 USD; current receipt: 10.00 USD; your entered cash: 12 USD.' in comparisons
    for width in (1280,390):
        b.viewport(width,900);shot(b,tmp_path,'complete-paged-stale-review',width)
    click(b,'review')
    assert b.evaluate("document.querySelector('#payment-amount').value")=='12'
    assert '203 complete recorded changes' in b.evaluate("document.querySelector('#payment-reviewed-comparisons').innerText")
    click(b,'preview')
