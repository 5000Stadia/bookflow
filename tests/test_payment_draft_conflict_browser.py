"""Rejected local patches survive review without erasing another writer's rows."""
import json
import pytest
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser
from tests.test_payment_review_gui import setup, invoice_setup
from tests.test_customer_payment_browser import click, shot
from tests.test_payment_recovery_browser import press


@pytest.mark.parametrize('width', [1280,390])
@pytest.mark.parametrize('action', ['amount','remove','add'])
def test_review_rebases_only_attempted_selection_edits(register_browser,tmp_path,width,action):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    first,item,_=invoice_setup(run,payer)
    invoices=[first]+[run('invoice post',dict(customer=payer,date='2026-06-01',number=f'RETAIN-{i}',
        lines=[dict(item=item,quantity='1')])) for i in range(2)]
    a,second,third=[row['id'] for row in invoices]
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    saved=run('payment selection show',dict(selection=selection))
    saved=run('payment selection update',dict(selection=selection,expected_version=saved['version'],amount='50',
        set_items=[dict(invoice=a,expected_version=1,amount='2',amount_origin='entered'),
                   dict(invoice=second,expected_version=1,amount='3',amount_origin='entered')]))
    click(b,'refresh-draft')
    concurrent=run('payment selection update',dict(selection=selection,expected_version=saved['version'],
        set_items=[dict(invoice=second,expected_version=1,amount='4',amount_origin='entered'),
                   dict(invoice=third,expected_version=1,amount='1',amount_origin='entered')]))
    target=third if action=='add' else a
    selector=f'tr[data-invoice="{target}"] input'+('[type="checkbox"]' if action=='remove' else '[type="text"]')
    edit="x.checked=false" if action=='remove' else "x.value="+json.dumps('7' if action=='add' else '5')
    b.evaluate(f"(()=>{{const x=document.querySelector({json.dumps(selector)});{edit};x.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert 'E_VERSION_CONFLICT' in b.evaluate("document.querySelector('#payment-error').innerText")
    assert run('payment selection show',dict(selection=selection))['version']==concurrent['version']
    comparison=b.evaluate("document.querySelector('[data-comparisons]').innerText")
    assert 'saved selection' in comparison and 'current selection' in comparison and 'attempted selection' in comparison
    click(b,'review')
    press(b,'Confirm complete recovery')
    current=run('payment selection show',dict(selection=selection))
    rows=run('payment selection items',dict(selection=selection,revision=current['version']))['items']
    actual={row['invoice_id']:row['amount_minor_units'] for row in rows}
    expected={a:200,second:400,third:100}
    if action=='remove':expected.pop(a)
    else:expected[target]=700 if action=='add' else 500
    assert actual==expected
    assert current['amount']['minor_units']==5000
    assert all(row['amount_origin']=='entered' for row in rows)
    assert run('payment query',dict(customer=payer))['items']==[]
    click(b,'preview')
    assert not b.evaluate("document.querySelector('#payment-save').disabled")
    (tmp_path/'selection-rebase.json').write_text(json.dumps(dict(action=action,comparison=comparison,actual=actual,expected=expected),indent=2))
    shot(b,tmp_path,'selection-'+action+'-retained',width)


@pytest.mark.parametrize('width', [1280,390])
def test_review_rejects_a_second_shared_writer_without_losing_attempt(register_browser,tmp_path,width):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    old=run('payment selection show',dict(selection=selection))
    current=run('payment selection update',dict(selection=selection,expected_version=old['version'],amount='11'))
    b.evaluate("let x=document.querySelector('#payment-amount');x.value='12';x.dispatchEvent(new Event('change',{bubbles:true}))")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    latest=run('payment selection update',dict(selection=selection,expected_version=current['version'],amount='13'))
    b.evaluate("document.querySelector('#payment-review').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert 'changed again' in b.evaluate("document.querySelector('#payment-error').innerText")
    assert b.evaluate("document.querySelector('#payment-amount').value")=='12'
    assert run('payment selection show',dict(selection=selection))['version']==latest['version']
    comparison=b.evaluate("document.querySelector('[data-comparisons]').innerText")
    assert '10.00' in comparison and '13.00' in comparison and '12 USD' in comparison
    click(b,'review')
    press(b,'Confirm complete recovery')
    assert run('payment selection show',dict(selection=selection))['amount']['minor_units']==1200
    assert b.evaluate("document.querySelector('#payment-amount').value")=='12.00'
    shot(b,tmp_path,'second-writer-attempt-retained',width)


@pytest.mark.parametrize('width', [1280,390])
@pytest.mark.parametrize('dependency', ['invoice','funding'])
def test_rejected_row_review_refreshes_all_retained_dependencies(register_browser,tmp_path,width,dependency):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    first,item,_=invoice_setup(run,payer)
    second=run('invoice post',dict(customer=payer,date='2026-06-01',number='OTHER-RETAINED',lines=[dict(item=item,quantity='1')]))
    payment=None
    if dependency=='funding':
        method=b.evaluate("document.querySelector('#payment-method').value")
        payment=run('payment receive',dict(customer=payer,date='2026-06-02',amount='50',payment_method=method,
            deposit_to=register_browser.bank['id'],operation_key='retained-funding'))
        b.navigate(base+'/receive-payments?payment='+payment['id']+'&mode=apply')
        b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'")
        b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    current=run('payment selection show',dict(selection=selection))
    run('payment selection update',dict(selection=selection,expected_version=current['version'],amount='50',set_items=[
        dict(invoice=first['id'],expected_version=1,amount='2',amount_origin='entered'),
        dict(invoice=second['id'],expected_version=1,amount='3',amount_origin='entered')]))
    click(b,'refresh-draft')
    if payment:
        shown=run('payment show',dict(payment=payment['id']))
        run('payment update',dict(payment=payment['id'],expected_version=1,amount='40',
            operation_key='retained-funding-amount',settlement_guard=shown['settlement_guard']),**{'X-Bookflow-Reason':'Correct receipt amount'})
    else:
        run('invoice update',dict(invoice=second['id'],expected_version=1,memo='Other writer clarified invoice'),**{'X-Bookflow-Reason':'Clarify invoice memo'})
    selector=f'tr[data-invoice="{first["id"]}"] input[type="text"]'
    b.evaluate(f"(()=>{{const x=document.querySelector({json.dumps(selector)});x.value='5';x.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert ('E_QUERY_STALE' if payment else 'E_VERSION_CONFLICT') in b.evaluate("document.querySelector('#payment-error').innerText")
    click(b,'review')
    press(b,'Confirm complete recovery')
    current=run('payment selection show',dict(selection=selection))
    rows=run('payment selection items',dict(selection=selection,revision=current['version']))['items']
    assert {row['invoice_id']:row['amount_minor_units'] for row in rows}=={first['id']:500,second['id']:300}
    if payment:
        assert current['context']['funding_version']==2
        assert run('payment show',dict(payment=payment['id']))['version']==2
        assert current['amount']['minor_units']==5000
        totals=b.evaluate("document.querySelector('#payment-totals').innerText")
        assert 'Unallocated draft amount: 42.00 USD' in totals
        assert 'Current available payment credit: 40.00 USD' in totals
        assert b.evaluate("document.querySelector('#payment-amount-label').innerText")=='Amount to allocate'
    else:
        assert next(row for row in rows if row['invoice_id']==second['id'])['expected_version']==2
        assert run('payment query',dict(customer=payer))['items']==[]
    click(b,'preview')
    assert not b.evaluate("document.querySelector('#payment-save').disabled")
    if payment:
        assert 'available 32.00 USD' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    shot(b,tmp_path,'retained-'+dependency+'-dependency',width)


@pytest.mark.timeout(600)
def test_review_recovers_complete_403_stale_rows_on_same_selection_preserving_history(register_browser,tmp_path):
    b,run,payer,other,base=setup(register_browser)
    first,item,_=invoice_setup(run,payer)
    invoices=[first]+[run('invoice post',dict(customer=payer,date='2026-06-01',number=f'RECOVER-403-{i}',
        lines=[dict(item=item,quantity='1',net_amount='1')])) for i in range(402)]
    selection=b.evaluate("new URL(location.href).searchParams.get('selection')")
    draft=run('payment selection show',dict(selection=selection))
    for offset in range(0,len(invoices),200):
        draft=run('payment selection update',dict(selection=selection,expected_version=draft['version'],set_items=[
            dict(invoice=row['id'],expected_version=1,amount='0.01',amount_origin='entered') for row in invoices[offset:offset+200]]))
    original=run('payment selection show',dict(selection=selection))
    def all_rows(revision):
        rows=[];cursor=None
        while True:
            page=run('payment selection items',dict(selection=selection,revision=revision,limit=200,**({'cursor':cursor} if cursor else {})))
            rows+=page['items'];cursor=page['next_cursor']
            if not cursor:return rows
    original_rows=all_rows(original['version'])
    captured_policy=original['context']['automatically_calculate']
    info=run('company show',{})
    run('company update',dict(expected_version=info['info_version'],automatically_calculate_payments=not captured_policy))
    click(b,'refresh-draft')
    method=b.evaluate("document.querySelector('#payment-method').value")
    # A separate complete remittance advances all403 invoice baselines without
    # consuming the user's still-open selection or bypassing the200-row API cap.
    remittance=run('payment selection create',dict(mode='new_receipt',customer=payer,date='2026-06-02',amount='4.03'))
    for offset in range(0,len(invoices),200):
        remittance=run('payment selection update',dict(selection=remittance['id'],expected_version=remittance['version'],set_items=[
            dict(invoice=row['id'],expected_version=1,amount='0.01',amount_origin='entered') for row in invoices[offset:offset+200]]))
    run('payment receive',dict(customer=payer,date='2026-06-02',amount='4.03',payment_method=method,
        deposit_to=register_browser.bank['id'],operation_key='403-other-remittance',
        applications=dict(mode='selection',selection=remittance['id'],expected_version=remittance['version'])))
    target=first['id'];selector=f'tr[data-invoice="{target}"] input[type="text"]'
    b.evaluate(f"(()=>{{let x=document.querySelector({json.dumps(selector)});x.value='0.02';x.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=240)
    assert 'E_VERSION_CONFLICT' in b.evaluate("document.querySelector('#payment-error').innerText")
    b.evaluate("document.querySelector('#payment-review').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=240)
    assert b.evaluate("document.querySelector('#payment-error').hidden"),b.evaluate("document.querySelector('#payment-error').innerText")
    recovered=b.evaluate("new URL(location.href).searchParams.get('selection')")
    assert recovered==selection
    assert run('payment selection show',dict(selection=selection))['version']==original['version']
    press(b,'Confirm complete recovery')
    assert run('payment selection show',dict(selection=selection))['version']==original['version']+1
    assert all_rows(original['version'])==original_rows
    items=[];cursor=None
    while True:
        page=run('payment selection items',dict(selection=recovered,limit=200,**({'cursor':cursor} if cursor else {})))
        items+=page['items'];cursor=page['next_cursor']
        if not cursor:break
    assert len(items)==len({row['invoice_id'] for row in items})==403
    assert {row['expected_version'] for row in items}=={2}
    assert sum(row['amount_minor_units'] for row in items)==404
    assert next(row for row in items if row['invoice_id']==target)['amount_minor_units']==2
    assert {row['amount_origin'] for row in items}=={'entered'}
    assert [row['received_minor_units'] for row in run('payment query',dict(customer=payer))['items']]==[403]
    for width in (1280,390):
        b.viewport(width,900);shot(b,tmp_path,'complete-403-recovered-selection',width)
    recovered_header=run('payment selection show',dict(selection=recovered))
    assert recovered_header['context']['automatically_calculate']==captured_policy
    run('payment receive',dict(customer=payer,date='2026-06-02',amount='10',payment_method=method,
        deposit_to=register_browser.bank['id'],operation_key='403-recovered-remittance',
        applications=dict(mode='selection',selection=recovered,expected_version=recovered_header['version'])))
    selections_before=run('payment selection query',{})
    b.evaluate(f"(()=>{{let x=document.querySelector({json.dumps(selector)});x.value='0.03';x.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=240)
    b.evaluate("document.querySelector('#payment-review').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=240)
    assert 'E_SELECTION_CONSUMED' in b.evaluate("document.querySelector('#payment-error').innerText")
    assert run('payment selection query',{})==selections_before
    assert sorted(row['received_minor_units'] for row in run('payment query',dict(customer=payer))['items'])==[403,1000]
    # Reopen ORIGINAL after consumption in each viewport and a new page context.
    # Load cannot silently create an alias of the already-recorded cash intent.
    for width in (1280,390):
        b.viewport(width,900);b.navigate(base+'/receive-payments?selection='+selection)
        b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'",timeout=180)
        b.evaluate("document.querySelector('#payment-load').click()")
        b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')",timeout=180)
        assert 'E_SELECTION_CONSUMED' in b.evaluate("document.querySelector('#payment-error').innerText")
        assert run('payment selection query',{})==selections_before
        assert sorted(row['received_minor_units'] for row in run('payment query',dict(customer=payer))['items'])==[403,1000]
        assert all_rows(original['version'])==original_rows
        shot(b,tmp_path,'original-consumed-selection-reopened',width)
