"""Actual desktop/phone durable outbox, restart, complete review and same-ID publication."""
import pytest
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser
from tests.test_payment_review_gui import setup
from tests.test_customer_payment_browser import click,shot


def press(b,text):
    import json
    b.evaluate('Array.from(document.querySelectorAll("#payment-recovery-panel button")).find(x=>x.textContent==='+json.dumps(text)+').click()')
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
    assert b.evaluate("document.querySelector('#payment-error').hidden"),b.evaluate("document.querySelector('#payment-error').innerText")


@pytest.mark.timeout(300)
@pytest.mark.parametrize('width',[1280,390])
def test_whole_intent_survives_unacknowledged_begin_and_tab_restart(register_browser,tmp_path,width):
    b,run,payer,_,base=setup(register_browser);b.viewport(width,900)
    identifier=b.evaluate("new URL(location.href).searchParams.get('selection')")
    old=run('payment selection show',dict(selection=identifier))
    current=run('payment selection update',dict(selection=identifier,expected_version=old['version'],amount='11'))
    b.evaluate("(()=>{const x=document.querySelector('#payment-amount');x.value='12';x.dispatchEvent(new Event('change',{bubbles:true}));})()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert 'E_VERSION_CONFLICT' in b.evaluate("document.querySelector('#payment-error').innerText")
    # Fail before any begin reaches the server. The entire intended header must
    # already have been committed to IndexedDB, surviving the old page's loss.
    b.evaluate("window.realFetch=window.fetch;window.fetch=(url,...args)=>String(url).includes('payment.recovery.begin')?Promise.reject(new Error('lost before begin')):window.realFetch(url,...args)")
    b.evaluate("document.querySelector('#payment-review').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert 'Connection interrupted' in b.evaluate("document.querySelector('#payment-error').innerText")
    assert run('payment selection show',dict(selection=identifier))['version']==current['version']
    assert run('payment recovery query',dict(selection=identifier))['total_count']==0
    b.navigate(base+'/receive-payments?selection='+identifier)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=120)
    assert 'unresolved sharing acknowledgement' in b.evaluate("document.querySelector('#payment-recovery-panel').innerText")
    press(b,'Resolve sharing and resume')
    state=run('payment selection show',dict(selection=identifier))
    assert state['version']==current['version'] and state['current_lifecycle']['state']=='recovery_review'
    assert '12.00' in b.evaluate("document.querySelector('#payment-recovery-panel').innerText")
    shot(b,tmp_path,'complete-recovery-before-confirm',width)
    # A form invalidation while a comparison page is in flight cannot allow
    # the late response to enable confirmation for the old browser generation.
    b.evaluate("window.realFetch=window.fetch;window.delayRecovery=true;window.fetch=async(url,...args)=>{const response=await window.realFetch(url,...args);if(window.delayRecovery&&String(url).includes('payment.recovery.compare-items')){window.delayRecovery=false;await new Promise(resolve=>window.releaseRecovery=resolve);}return response;}")
    b.evaluate("Array.from(document.querySelectorAll('#payment-recovery-panel button')).find(x=>x.textContent==='Reload complete comparison').click()")
    b.wait_for("typeof window.releaseRecovery==='function'")
    b.evaluate("document.querySelector('#payment-date').dispatchEvent(new Event('change',{bubbles:true}));window.releaseRecovery()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
    assert 'E_QUERY_STALE' in b.evaluate("document.querySelector('#payment-error').innerText")
    assert not b.evaluate("!!document.querySelector('[data-recovery-confirm]:not(:disabled)')")
    # A second reload resumes the server generation, without beginning an alias.
    b.navigate(base+'/receive-payments?selection='+identifier)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=120)
    press(b,'Confirm complete recovery')
    final=run('payment selection show',dict(selection=identifier))
    assert final['id']==identifier and final['version']==current['version']+1 and final['amount']['minor_units']==1200
    assert run('payment recovery query',dict(selection=identifier))['total_count']==1
    assert run('payment query',dict(customer=payer))['total_count']==0
    assert b.evaluate("document.documentElement.scrollWidth<=window.innerWidth"),b.evaluate("document.documentElement.scrollWidth")
    shot(b,tmp_path,'recovery-published-same-selection',width)
