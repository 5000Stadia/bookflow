"""Independent cents, stored headers and immutable saved-calculation provenance."""
import json
import sqlite3
from pathlib import Path
import pytest
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_payment_selection import invoice
from tests.test_payment_receipts import method
from tests.test_payment_recovery import setup,call,declaration,seal_compare,confirm,raw_books


@pytest.mark.parametrize('derived',[True,False])
@pytest.mark.parametrize('edit,subtotal',[('remove',100),('add',250),('change',175),('unresolved',None)])
def test_header_is_stored_truth_and_financial_consumer_uses_it(client,sale,root,derived,edit,subtotal):
    draft,first,second=setup(client,sale,derived)
    extra=invoice(client,sale,'REC-MONEY-X') if edit in ('add','unresolved') else None
    target=(extra or first)['id']
    entry=dict(invoice_id=target,observed_invoice_version=1,action='remove' if edit=='remove' else 'set')
    if edit!='remove':entry.update(currency='USD',amount_origin='unresolved' if edit=='unresolved' else 'entered',amount_minor_units=None if edit=='unresolved' else 50 if edit=='add' else 75)
    begin=declaration(draft,[entry]);identifier,comparison=seal_compare(client,begin,[entry])
    expected=subtotal if derived else 200
    assert comparison['amount_minor_units']==expected and comparison['selected_minor_units']==subtotal
    assert comparison['header_comparison']['anchor']['amount_minor_units']==200
    before=raw_books(root)
    request=dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'])
    assert call(client,'compare',request)==comparison
    apply=dict(**request,expected_recovery_version=comparison['recovery_version'],expected_selection_version=draft['version'],expected_facts_fingerprint=comparison['facts_fingerprint'])
    prospective=call(client,'apply',apply,dry_run=True)
    assert prospective['comparison']==comparison and raw_books(root)==before
    applied=call(client,'apply',apply)
    shown=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    assert (shown['amount']['minor_units'] if shown['amount'] else None)==expected
    assert shown['amount_origin']==('selection_total' if derived else 'entered')
    company=client.company.show(company=COMPANY)
    with sqlite3.connect(Path(company['path'])/'company.db') as db:
        stored=db.execute('SELECT amount_minor_units,amount_origin,manifest_hash FROM payment_selection_revisions WHERE id=?',(shown['revision_id'],)).fetchone()
        assert stored[0]==expected and stored[1]==shown['amount_origin'] and stored[2]==shown['manifest_hash']
    assert call(client,'apply',apply)['original_receipt']==applied['original_receipt']
    if edit=='unresolved':
        with pytest.raises(BookflowError):
            client.run('payment receive',dict(customer=sale['customer'],date='2026-06-01',amount='2.00',payment_method=method(client),operation_key='unresolved-recovery',applications=dict(mode='selection',selection=draft['id'],expected_version=shown['version'])),company=COMPANY,dry_run=True)
        shown=client.run('payment selection update',dict(selection=draft['id'],expected_version=shown['version'],remove_invoices=[target]),company=COMPANY)
        assert shown['amount']['minor_units']==200
    elif derived or subtotal<=200:
        result=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-01',amount=dict(minor_units=expected,currency='USD'),payment_method=method(client),operation_key='resolved-recovery',applications=dict(mode='selection',selection=draft['id'],expected_version=shown['version'])),company=COMPANY,dry_run=True)
        assert result


def test_saved_A_calculated_restoration_and_new_candidate_reserve_fixed_cash(client,sale):
    first,second=[client.run('invoice post',dict(customer=sale['customer'],date='2026-06-01',number='ONE-CALC-'+str(i),lines=[dict(item=sale['item'],quantity='1',unit_price='1.00')]),company=COMPANY) for i in range(2)]
    draft=client.run('payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-01',amount='1.50'),company=COMPANY)
    a=client.run('payment selection update',dict(selection=draft['id'],expected_version=draft['version'],amount='1.50',
        set_items=[dict(invoice=first['id'],expected_version=1,amount='1.00',amount_origin='calculated')]),company=COMPANY)
    b=client.run('payment selection update',dict(selection=draft['id'],expected_version=a['version'],set_items=[dict(invoice=first['id'],expected_version=1,amount='0.75',amount_origin='entered')]),company=COMPANY)
    entries=sorted([dict(invoice_id=first['id'],observed_invoice_version=1,action='set',currency='USD',amount_minor_units=100,amount_origin='calculated',retained_calculation_revision_id=a['revision_id']),dict(invoice_id=second['id'],observed_invoice_version=1,action='calculate',attempted_calculated_minor_units=1)],key=lambda e:e['invoice_id'])
    begin=declaration(b,entries);begin['local_baseline_revision']=a['revision_id']
    from bookflow.company.payment_queries import digest
    begin['intent_hash']=digest(dict(domain='bookflow.payment.recovery.intent',format=1,selection=b['id'],local_baseline_revision=a['revision_id'],anchor_revision=b['revision_id'],attempt_generation=begin['attempt_generation'],header_intent=begin['header_intent'],entries=entries))
    identifier,comparison=seal_compare(client,begin,entries)
    assert comparison['amount_minor_units']==150 and comparison['selected_minor_units']==150
    confirm(client,identifier,begin,comparison)
    rows=client.run('payment selection items',dict(selection=b['id']),company=COMPANY)['items']
    assert {r['invoice_id']:(r['amount_minor_units'],r['amount_origin']) for r in rows}=={first['id']:(100,'calculated'),second['id']:(50,'calculated')}


def test_unresolved_header_new_calculation_stays_null(client,sale):
    draft,first,second=setup(client,sale)
    entries=[dict(invoice_id=first['id'],observed_invoice_version=1,action='calculate',attempted_calculated_minor_units=100)]
    begin=declaration(draft,entries,dict(action='set',amount_origin='unresolved'))
    identifier,comparison=seal_compare(client,begin,entries)
    assert comparison['amount_minor_units'] is None and comparison['selected_minor_units'] is None
    confirm(client,identifier,begin,comparison)
    rows=client.run('payment selection items',dict(selection=draft['id']),company=COMPANY)['items']
    assert next(r for r in rows if r['invoice_id']==first['id'])['amount_minor_units'] is None


def test_saved_calculated_100_50_remain_fixed_after_due_falls(client,sale):
    first,second=[client.run('invoice post',dict(customer=sale['customer'],date='2026-06-01',number='FIXED-DUE-'+str(i),lines=[dict(item=sale['item'],quantity='1',unit_price='1.00')]),company=COMPANY) for i in range(2)]
    draft=client.run('payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-01',amount='1.50'),company=COMPANY)
    draft=client.run('payment selection update',dict(selection=draft['id'],expected_version=draft['version'],set_items=[dict(invoice=r['id'],expected_version=1,amount_origin='calculated') for r in (first,second)]),company=COMPANY)
    original=client.run('payment selection items',dict(selection=draft['id']),company=COMPANY)['items']
    assert [r['amount_minor_units'] for r in original]==[100,50]
    client.run('payment receive',dict(customer=sale['customer'],date='2026-06-01',amount='0.50',payment_method=method(client),operation_key='lower-fixed-due',applications=dict(mode='inline',items=[dict(invoice=first['id'],expected_version=1,amount='0.50')])),company=COMPANY)
    begin=declaration(draft,[]);identifier,comparison=seal_compare(client,begin,[])
    assert comparison['amount_minor_units']==150 and comparison['selected_minor_units']==150 and comparison['problem_count']>0 and comparison['hard_blocker_count']==0
    confirm(client,identifier,begin,comparison)
    rows=client.run('payment selection items',dict(selection=draft['id']),company=COMPANY)['items']
    assert [r['amount_minor_units'] for r in rows]==[100,50] and {r['amount_origin'] for r in rows}=={'calculated'}
    assert rows[0]['due_minor_units']==50 and rows[0]['expected_version']==2
    shown=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    with pytest.raises(BookflowError):
        client.run('payment receive',dict(customer=sale['customer'],date='2026-06-01',amount='1.50',payment_method=method(client),operation_key='invalid-fixed-due',applications=dict(mode='selection',selection=draft['id'],expected_version=shown['version'])),company=COMPANY)
    assert client.run('payment query',dict(customer=sale['customer']),company=COMPANY)['total_count']==1


def test_new_entered_header_above_js_safe_integer_is_exact_in_storage(client,sale):
    draft,_,_=setup(client,sale)
    units=9007199254740993
    begin=declaration(draft,[],dict(action='set',amount_origin='entered',currency='USD',amount_minor_units=units))
    identifier,comparison=seal_compare(client,begin,[])
    assert comparison['amount_minor_units']==units and comparison['unapplied_minor_units']==units-200
    confirm(client,identifier,begin,comparison)
    shown=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    assert shown['amount']['minor_units']==units
    with sqlite3.connect(Path(client.company.show(company=COMPANY)['path'])/'company.db') as db:
        assert db.execute('SELECT typeof(amount_minor_units),amount_minor_units FROM payment_selection_revisions WHERE id=?',(shown['revision_id'],)).fetchone()==('integer',units)


def test_live_calculation_preference_does_not_stale_or_replace_captured_policy(client,sale):
    draft,first,_=setup(client,sale)
    before_policy=draft['context']['automatically_calculate']
    entries=[dict(invoice_id=first['id'],observed_invoice_version=1,action='set',amount_minor_units=50,currency='USD',amount_origin='entered')]
    begin=declaration(draft,entries);identifier,comparison=seal_compare(client,begin,entries)
    info=client.run('company show',{},company=COMPANY)
    client.run('company update',dict(expected_version=info['info_version'],automatically_calculate_payments=not before_policy),company=COMPANY)
    after=call(client,'compare',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash']))
    assert after['facts_fingerprint']==comparison['facts_fingerprint']
    confirm(client,identifier,begin,comparison)
    shown=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    assert shown['context']['automatically_calculate']==before_policy and shown['amount']['minor_units']==200 and shown['applied_minor_units']==150


@pytest.mark.parametrize('removed',[False,True])
def test_invoice_date_and_removed_target_stale_all_confirmation_pages(client,sale,root,removed):
    draft,first,_=setup(client,sale)
    entries=[dict(invoice_id=first['id'],observed_invoice_version=1,action='remove')] if removed else []
    begin=declaration(draft,entries);identifier,old=seal_compare(client,begin,entries)
    client.run('invoice update',dict(invoice=first['id'],expected_version=1,date='2026-06-02'),company=COMPANY,reason='Correct invoice date')
    before=raw_books(root)
    with pytest.raises(BookflowError) as caught:confirm(client,identifier,begin,old)
    assert caught.value.code=='E_PREVIEW_STALE' and raw_books(root)==before
    current=call(client,'compare',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash']))
    assert current['facts_fingerprint']!=old['facts_fingerprint']
    assert bool(current['hard_blocker_count']) is not removed
    if removed:
        confirm(client,identifier,begin,current)
        rows=client.run('payment selection items',dict(selection=draft['id']),company=COMPANY)['items']
        assert first['id'] not in {row['invoice_id'] for row in rows}
    else:
        with pytest.raises(BookflowError):confirm(client,identifier,begin,current)
        assert raw_books(root)==before


@pytest.mark.parametrize('change',['amount','date','void'])
def test_source_capacity_date_and_status_are_current_recovery_facts(client,sale,root,change):
    from tests.test_payment_selection import invoice
    first=invoice(client,sale,'REC-SOURCE-'+change)
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-01',amount='2',payment_method=method(client),operation_key='source-'+change),company=COMPANY)
    draft=client.run('payment selection create',dict(mode='existing_credit',payment=payment['id'],date='2026-06-01',amount='2'),company=COMPANY)
    draft=client.run('payment selection update',dict(selection=draft['id'],expected_version=draft['version'],set_items=[dict(invoice=first['id'],expected_version=1,amount='1',amount_origin='entered')]),company=COMPANY)
    begin=declaration(draft,[]);identifier,old=seal_compare(client,begin,[])
    shown=client.run('payment show',dict(payment=payment['id']),company=COMPANY)
    inp=dict(payment=payment['id'],expected_version=1,operation_key='source-change-'+change,settlement_guard=shown['settlement_guard'])
    if change!='void':inp[change]='0.50' if change=='amount' else '2026-06-02'
    else:inp.pop('settlement_guard')
    client.run('payment '+('void' if change=='void' else 'update'),inp,company=COMPANY,reason='Correct original funding')
    before=raw_books(root)
    with pytest.raises(BookflowError) as caught:confirm(client,identifier,begin,old)
    assert caught.value.code=='E_PREVIEW_STALE' and raw_books(root)==before
    current=call(client,'compare',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash']))
    assert current['facts_fingerprint']!=old['facts_fingerprint'] and current['problem_count']>0
    assert current['selected_minor_units']==100
    if change=='amount':
        assert current['hard_blocker_count']==0
        confirm(client,identifier,begin,current)
        final=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
        assert final['amount']['minor_units']==200 and final['applied_minor_units']==100
        with pytest.raises(BookflowError):
            client.run('payment apply',dict(payment=payment['id'],expected_version=2,date='2026-06-01',operation_key='over-capacity',applications=dict(mode='selection',selection=draft['id'],expected_version=final['version'])),company=COMPANY)
    else:
        assert current['hard_blocker_count']>0
        with pytest.raises(BookflowError):confirm(client,identifier,begin,current)
        assert raw_books(root)==before


def test_relevant_customer_lineage_change_invalidates_whole_confirmation(client,sale,root):
    draft,_,_=setup(client,sale)
    begin=declaration(draft,[]);identifier,old=seal_compare(client,begin,[])
    parent=client.customer.create(name='Recovery lineage parent',company=COMPANY)['id']
    customer=client.customer.show(customer=sale['customer'],company=COMPANY)
    client.customer.update(customer=sale['customer'],expected_version=customer['version'],parent_id=parent,company=COMPANY)
    before=raw_books(root)
    with pytest.raises(BookflowError) as caught:confirm(client,identifier,begin,old)
    assert caught.value.code=='E_PREVIEW_STALE' and raw_books(root)==before
    current=call(client,'compare',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash']))
    assert current['facts_fingerprint']!=old['facts_fingerprint']
    assert current['selected_minor_units']==old['selected_minor_units']==200
    confirm(client,identifier,begin,current)


def test_only_added_X_due_100_to_50_invalidates_paged_comparison(client,sale,root):
    draft,_,_=setup(client,sale)
    added=client.run('invoice post',dict(customer=sale['customer'],date='2026-06-01',number='REC-EXACT-X',lines=[dict(item=sale['item'],quantity='1',unit_price='1')]),company=COMPANY)
    entries=[dict(invoice_id=added['id'],observed_invoice_version=1,action='calculate',attempted_calculated_minor_units=1)]
    begin=declaration(draft,entries,dict(action='set',amount_origin='entered',amount_minor_units=300,currency='USD'))
    identifier,old=seal_compare(client,begin,entries)
    request=dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'],facts_fingerprint=old['facts_fingerprint'],kind='changes',limit=1)
    page=call(client,'compare-items',request);assert page['next_cursor']
    client.run('payment receive',dict(customer=sale['customer'],date='2026-06-01',amount='0.50',payment_method=method(client),operation_key='X-due-only',applications=dict(mode='inline',items=[dict(invoice=added['id'],expected_version=1,amount='0.50')])),company=COMPANY)
    before=raw_books(root)
    with pytest.raises(BookflowError) as caught:call(client,'compare-items',dict(request,cursor=page['next_cursor']))
    assert caught.value.code=='E_QUERY_STALE' and raw_books(root)==before
    with pytest.raises(BookflowError) as caught:confirm(client,identifier,begin,old)
    assert caught.value.code=='E_PREVIEW_STALE' and raw_books(root)==before
    fresh=call(client,'compare',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash']))
    rows=call(client,'compare-items',dict(request,limit=200,facts_fingerprint=fresh['facts_fingerprint']))['items']
    x=next(row for row in rows if row.get('invoice_id')==added['id'] and not row.get('history_event_id'))
    assert x['current']['version']==2 and x['current']['due']==50 and x['proposed']['amount_minor_units']==50
    assert x['observed_history']=='unknown'  # Never invent historical due from caller claim.
    confirm(client,identifier,begin,fresh)
    shown=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    assert shown['version']==draft['version']+1 and shown['applied_minor_units']==250
