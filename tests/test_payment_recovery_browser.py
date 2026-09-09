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


@pytest.mark.timeout(600)
def test_browser_resumes_201_of_403_from_complete_durable_outbox(register_browser,tmp_path):
    """Restart fixture begins after first200 acknowledged edits; no missing edit is inferred."""
    import json
    from uuid import uuid4
    from tests.test_payment_review_gui import invoice_setup
    from tests.test_payment_recovery import declaration
    b,run,payer,_,base=setup(register_browser)
    first,item,_=invoice_setup(run,payer)
    invoices=[first]+[run('invoice post',dict(customer=payer,date='2026-06-01',number=f'OUTBOX-403-{i}',lines=[dict(item=item,quantity='1',net_amount='1')])) for i in range(402)]
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    draft=run('payment selection show',dict(selection=selection))
    for offset in range(0,403,200):
        draft=run('payment selection update',dict(selection=selection,expected_version=draft['version'],set_items=[dict(invoice=r['id'],expected_version=1,amount='0.01',amount_origin='entered') for r in invoices[offset:offset+200]]))
    edits=sorted([dict(invoice_id=r['id'],observed_invoice_version=1,action='set',amount_minor_units=2,currency='USD',amount_origin='entered') for r in invoices[:201]],key=lambda r:r['invoice_id'])
    begin=declaration(draft,edits)
    manifest=dict(domain='bookflow.payment.recovery.intent',format=1,selection=selection,local_baseline_revision=draft['revision_id'],anchor_revision=draft['revision_id'],attempt_generation=begin['attempt_generation'],header_intent=begin['header_intent'],entries=edits)
    attempt=dict(begin=begin,entries=edits,manifest=manifest,actions={},done=False)
    # Fixture the *whole original browser intent*, independently of the server's
    # deliberately incomplete stage. Persist using the actual outbox wire format.
    b.evaluate("window.originalAttempt="+json.dumps(attempt))
    b.evaluate("(async()=>{const config=BookflowExactJSON.parse(document.querySelector('#payment-config').textContent);const a=window.originalAttempt;a.scope='payment-recovery:'+config.company+':'+config.actor+':'+a.begin.selection;a.storageKey=a.scope+':'+a.begin.attempt_generation;await new Promise((resolve,reject)=>{const request=indexedDB.open('bookflow-payment-recovery-v1',1);request.onupgradeneeded=()=>request.result.createObjectStore('attempts');request.onsuccess=()=>{const db=request.result,tx=db.transaction('attempts','readwrite');tx.objectStore('attempts').put(BookflowExactJSON.stringify(a),a.storageKey);tx.oncomplete=()=>{db.close();resolve()};tx.onerror=()=>reject(tx.error)};request.onerror=()=>reject(request.error)});})()")
    begun=run('payment recovery begin',begin);identifier=begun['original_receipt']['recovery_id']
    run('payment recovery upload',dict(recovery_id=identifier,chunk_index=0,entries=edits[:200]))
    from tests.test_row5_browser_acceptance import _Cdp,PASSWORD
    other_browser=_Cdp(tmp_path/'independent-recovery-browser')
    try:
        other_browser.navigate(register_browser.site.base_url+'/login')
        other_browser.evaluate("(()=>{document.querySelector('[name=username]').value="+json.dumps(register_browser.site.login)+";document.querySelector('[name=password]').value="+json.dumps(PASSWORD)+";document.querySelector('form[hx-post=\"/login\"]').requestSubmit();})()")
        other_browser.wait_for("!!document.querySelector('.nav-group')")
        for width in (1280,390):
            other_browser.viewport(width,900);other_browser.navigate(base+'/receive-payments?selection='+selection)
            other_browser.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=180)
            panel=other_browser.evaluate("document.querySelector('#payment-recovery-panel').textContent")
            assert 'Parts 2–2' in panel and '201–201' in panel and 'not stored in this browser' in panel
            assert 'Resume complete saved attempt' not in panel and 'Confirm complete recovery' not in panel
            assert 'Part 1 acknowledged' in panel and '200 of 201 edits received' in panel
            for _ in range(3):
                other_browser.evaluate("document.querySelector('[data-recovery-more=entries]').click()")
                other_browser.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
            shown=other_browser.evaluate("Array.from(document.querySelectorAll('[data-recovery-entry]')).map(x=>x.dataset.recoveryEntry)")
            assert shown==[entry['invoice_id'] for entry in edits[:200]]
            assert edits[200]['invoice_id'] not in shown
            assert other_browser.evaluate("document.querySelector('[data-recovery-more=entries]').hidden")
            assert other_browser.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
            shot(other_browser,tmp_path,'second-browser-200-received-one-unknown',width)
    finally:other_browser.close()
    for width in (1280,390):
        b.viewport(width,900);b.navigate(base+'/receive-payments?selection='+selection)
        b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=180)
        text=b.evaluate("document.querySelector('#payment-recovery-panel').innerText")
        assert '200 of 201' in text and '1 parts are still missing' in text
        assert run('payment selection show',dict(selection=selection))['version']==draft['version']
        assert run('payment query',dict(customer=payer))['total_count']==0
        shot(b,tmp_path,'201-interrupted-after200',width)
    press(b,'Resume complete saved attempt')
    comparison=b.evaluate("document.querySelector('#payment-recovery-panel').innerText")
    assert 'selected: 6.04' in comparison and 'unapplied: 3.96' in comparison
    state=run('payment recovery show',dict(recovery_id=identifier))
    assert state['received_entry_count']==201 and state['state']=='sealed'
    assert run('payment selection show',dict(selection=selection))['version']==draft['version']
    for width in (1280,390):
        b.viewport(width,900);shot(b,tmp_path,'201-complete-review',width)
    press(b,'Confirm complete recovery')
    final=run('payment selection show',dict(selection=selection))
    assert final['id']==selection and final['version']==draft['version']+1 and final['applied_minor_units']==604
    assert final['amount']['minor_units']==1000 and final['unapplied_minor_units']==396
    from tests.test_customer_payment_browser import field
    field(b,'method',run('payment-method list',{})['items'][0]['id'])
    for action in ('preview','save-new'):
        b.evaluate("document.querySelector('#payment-"+action+"').click()")
        b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=240)
        assert b.evaluate("document.querySelector('#payment-error').hidden"),b.evaluate("document.querySelector('#payment-error').innerText")
    assert [row['received_minor_units'] for row in run('payment query',dict(customer=payer))['items']]==[1000]
    assert b.evaluate("document.querySelector('#payment-amount').value")==''
    assert b.evaluate("document.querySelector('#payment-recovery-panel')===null")
    (tmp_path/'complete-outbox.json').write_text(json.dumps(dict(manifest=manifest,begin=begin,stage=state,final=final),indent=2))


@pytest.mark.timeout(600)
@pytest.mark.parametrize('width',[1280,390])
def test_every_acknowledgement_boundary_resumes_original_whole_intent(register_browser,tmp_path,width):
    """Actual browser fetch loss before/after each durable server boundary."""
    import json
    from tests.test_payment_review_gui import invoice_setup
    from tests.test_payment_recovery import declaration
    b,run,payer,_,base=setup(register_browser);b.viewport(width,900)
    invoice,_,_=invoice_setup(run,payer)
    evidence=[]
    for boundary in ('begin','upload','seal','apply'):
        for after in (False,True):
            draft=run('payment selection create',dict(mode='new_receipt',customer=payer,date='2026-06-01',amount='10'))
            selection=draft['id']
            entries=[dict(invoice_id=invoice['id'],observed_invoice_version=1,action='set',amount_minor_units=123,currency='USD',amount_origin='entered')]
            begin=declaration(draft,entries)
            manifest=dict(domain='bookflow.payment.recovery.intent',format=1,selection=selection,local_baseline_revision=draft['revision_id'],anchor_revision=draft['revision_id'],attempt_generation=begin['attempt_generation'],header_intent=begin['header_intent'],entries=entries)
            attempt=dict(begin=begin,entries=entries,manifest=manifest,actions={},done=False)
            b.navigate(base+'/receive-payments?selection='+selection)
            b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=120)
            b.evaluate('window.boundaryAttempt='+json.dumps(attempt))
            b.evaluate("(async()=>{const config=BookflowExactJSON.parse(document.querySelector('#payment-config').textContent),a=window.boundaryAttempt;a.scope='payment-recovery:'+config.company+':'+config.actor+':'+a.begin.selection;a.storageKey=a.scope+':'+a.begin.attempt_generation;await new Promise((resolve,reject)=>{const request=indexedDB.open('bookflow-payment-recovery-v1',1);request.onupgradeneeded=()=>request.result.createObjectStore('attempts');request.onsuccess=()=>{const db=request.result,tx=db.transaction('attempts','readwrite');tx.objectStore('attempts').put(BookflowExactJSON.stringify(a),a.storageKey);tx.oncomplete=()=>{db.close();resolve()};tx.onerror=()=>reject(tx.error)};request.onerror=()=>reject(request.error)});})()")
            b.navigate(base+'/receive-payments?selection='+selection)
            b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=120)
            if boundary=='apply':press(b,'Resolve sharing and resume')
            b.evaluate('window.lossBoundary='+json.dumps('payment.recovery.'+boundary)+';window.loseAfter='+json.dumps(after))
            b.evaluate("window.realFetch=window.fetch;window.lossObserved=false;window.fetch=async(url,...args)=>{if(!window.lossObserved&&String(url).includes(window.lossBoundary)){window.lossObserved=true;if(window.loseAfter){const response=await window.realFetch(url,...args);window.lostOriginal=await response.clone().json();}throw new Error('Injected acknowledgement loss');}return window.realFetch(url,...args);}")
            action='Confirm complete recovery' if boundary=='apply' else 'Resolve sharing and resume'
            b.evaluate('Array.from(document.querySelectorAll("#payment-recovery-panel button")).find(x=>x.textContent==='+json.dumps(action)+').click()')
            b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
            assert b.evaluate('window.lossObserved')
            assert not b.evaluate("document.querySelector('#payment-error').hidden")
            lost=b.evaluate('window.lostOriginal||null')
            state=run('payment selection show',dict(selection=selection))
            assert state['version']==draft['version']+int(boundary=='apply' and after)
            b.navigate(base+'/receive-payments?selection='+selection)
            b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=120)
            for text in ('Resolve sharing and resume','Resume complete saved attempt'):
                if b.evaluate('Array.from(document.querySelectorAll("#payment-recovery-panel button")).some(x=>x.textContent==='+json.dumps(text)+')'):press(b,text)
            if not (boundary=='apply' and after):press(b,'Confirm complete recovery')
            final=run('payment selection show',dict(selection=selection))
            assert final['id']==selection and final['version']==draft['version']+1
            assert final['applied_minor_units']==123 and final['amount']['minor_units']==1000
            attempts=run('payment recovery query',dict(selection=selection))
            assert attempts['total_count']==1
            recovery=run('payment recovery show',dict(recovery_key=begin['recovery_key']))
            assert recovery['state']=='applied' and recovery['received_entry_count']==1
            assert run('payment query',dict(customer=payer))['total_count']==0
            assert b.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            evidence.append(dict(boundary=boundary,after_server=after,lost_response=lost,begin=begin,final=final,recovery=recovery))
            (tmp_path/'all-acknowledgement-boundaries.json').write_text(json.dumps(evidence,indent=2))
    shot(b,tmp_path,'all-acknowledgement-boundaries-complete',width)


@pytest.mark.timeout(300)
@pytest.mark.parametrize('width',[1280,390])
def test_browser_reviews_new_calculation_after_preserved_fixed_amount(register_browser,tmp_path,width):
    from tests.test_payment_review_gui import invoice_setup
    from tests.test_payment_recovery import declaration
    b,run,payer,_,base=setup(register_browser);b.viewport(width,900)
    _,item,_=invoice_setup(run,payer)
    invoices=[run('invoice post',dict(customer=payer,date='2026-06-01',number='F8-BROWSER-'+str(i),lines=[dict(item=item,quantity='1',net_amount='1')])) for i in range(2)]
    draft=run('payment selection create',dict(mode='new_receipt',customer=payer,date='2026-06-01',amount='1.50'))
    draft=run('payment selection update',dict(selection=draft['id'],expected_version=1,set_items=[dict(invoice=invoices[0]['id'],expected_version=1,amount_origin='calculated')]))
    entries=[dict(invoice_id=invoices[1]['id'],observed_invoice_version=1,action='calculate',attempted_calculated_minor_units=1)]
    begin=declaration(draft,entries);identifier=run('payment recovery begin',begin)['original_receipt']['recovery_id']
    run('payment recovery upload',dict(recovery_id=identifier,chunk_index=0,entries=entries))
    run('payment recovery seal',dict(recovery_id=identifier,expected_recovery_version=2))
    b.navigate(base+'/receive-payments?selection='+draft['id'])
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=120)
    text=b.evaluate("document.querySelector('#payment-recovery-panel').textContent")
    assert 'previous display 0.01' in text and 'Proposed selection: 0.50 (calculated)' in text
    assert 'Current saved selection: 1.00 (calculated)' in text
    b.evaluate("document.querySelectorAll('#payment-recovery-panel details').forEach(x=>x.open=true)")
    shot(b,tmp_path,'fixed100-new50-explicit-review',width)
    press(b,'Confirm complete recovery')
    rows=run('payment selection items',dict(selection=draft['id']))['items']
    assert {r['invoice_id']:(r['amount_minor_units'],r['amount_origin']) for r in rows}=={invoices[0]['id']:(100,'calculated'),invoices[1]['id']:(50,'calculated')}
    final=run('payment selection show',dict(selection=draft['id']))
    assert final['version']==draft['version']+1 and final['amount']['minor_units']==150 and final['unapplied_minor_units']==0
    (tmp_path/'calculated-provenance-browser.json').write_text(__import__('json').dumps(dict(begin=begin,final=final,items=rows),indent=2))
