"""Public recovery lifecycle and independently asserted ordinary/financial invariants."""
import hashlib
import json
import sqlite3
from uuid import uuid4
import pytest
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_selection import invoice


def call(client,verb,data,**kw):
    return client.run('payment recovery '+verb,data,company=COMPANY,**kw)


def declaration(draft,entries,header=None):
    header=header or {'action':'keep'}
    generation=str(uuid4())
    body=dict(domain='bookflow.payment.recovery.intent',format=1,selection=draft['id'],
        local_baseline_revision=draft['revision_id'],anchor_revision=draft['revision_id'],attempt_generation=generation,
        header_intent=header,entries=sorted(entries,key=lambda e:e['invoice_id']))
    digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    return dict(recovery_key='recovery-'+generation,selection=draft['id'],expected_version=draft['version'],
        local_baseline_revision=draft['revision_id'],attempt_generation=generation,declared_entry_count=len(entries),intent_hash=digest,header_intent=header)


def setup(client,sale,derived=False):
    first,second=invoice(client,sale,'REC-A'),invoice(client,sale,'REC-B')
    draft=client.run('payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-01',
        amount='2.00',amount_origin='selection_total' if derived else 'entered'),company=COMPANY)
    draft=client.run('payment selection update',dict(selection=draft['id'],expected_version=draft['version'],
        set_items=[dict(invoice=r['id'],expected_version=1,amount='1.00') for r in (first,second)]),company=COMPANY)
    return draft,first,second


def seal_compare(client,begin,entries):
    result=call(client,'begin',begin)
    identifier=result['original_receipt']['recovery_id']
    for offset in range(0,len(entries),200):
        result=call(client,'upload',dict(recovery_id=identifier,chunk_index=offset//200,entries=sorted(entries,key=lambda e:e['invoice_id'])[offset:offset+200]))
    state=call(client,'show',dict(recovery_id=identifier))
    call(client,'seal',dict(recovery_id=identifier,expected_recovery_version=state['version']))
    compare=call(client,'compare',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash']))
    return identifier,compare


def confirm(client,identifier,begin,compare):
    return call(client,'apply',dict(recovery_id=identifier,expected_recovery_version=compare['recovery_version'],
        attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'],expected_selection_version=begin['expected_version'],
        expected_facts_fingerprint=compare['facts_fingerprint']))


def test_begin_barrier_abort_and_exact_retry(client,sale):
    draft,first,second=setup(client,sale)
    data=declaration(draft,[dict(invoice_id=first['id'],observed_invoice_version=1,action='remove')])
    begun=call(client,'begin',data)
    identifier=begun['original_receipt']['recovery_id']
    assert begun['current']['state']=='recovery_uploading'
    assert call(client,'begin',data)['original_receipt']==begun['original_receipt']
    with pytest.raises(BookflowError) as caught:
        client.run('payment selection clear',dict(selection=draft['id'],expected_version=draft['version']),company=COMPANY)
    assert caught.value.code=='E_RECOVERY_PENDING'
    with pytest.raises(BookflowError) as caught:
        call(client,'seal',dict(recovery_id=identifier,expected_recovery_version=1))
    assert caught.value.code=='E_RECOVERY_INCOMPLETE'
    missing=call(client,'items',dict(recovery_id=identifier,kind='missing_ranges'))
    assert missing['items']==[dict(first_chunk_index=0,last_chunk_index=0,chunk_count=1)]
    aborted=call(client,'abort',dict(recovery_id=identifier,expected_recovery_version=1,disposition='discard_entire_attempt'))
    assert aborted['current']['state']=='open'
    restored=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    assert restored['id']==draft['id'] and restored['version']==draft['version']+1
    assert restored['amount']==draft['amount'] and restored['item_count']==2
    assert call(client,'begin',data)['current']['state']=='open'


def test_complete_publish_derives_header_and_retains_identity(client,sale):
    draft,first,second=setup(client,sale,derived=True)
    edits=[dict(invoice_id=first['id'],observed_invoice_version=1,action='remove')]
    begin=declaration(draft,edits)
    identifier,compare=seal_compare(client,begin,edits)
    assert compare['amount_minor_units']==100 and compare['selected_minor_units']==100
    assert compare['unapplied_minor_units']==0 and compare['header_comparison']['derived']
    applied=confirm(client,identifier,begin,compare)
    assert applied['current']['selection_id']==draft['id']
    shown=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    assert shown['amount']['minor_units']==100 and shown['version']==draft['version']+1
    assert shown['current_lifecycle']['state']=='open'
    assert confirm(client,identifier,begin,compare)['original_receipt']==applied['original_receipt']
    old=client.run('payment selection show',dict(selection=draft['id'],revision=draft['version']),company=COMPANY)
    assert old['amount']['minor_units']==200


def raw_books(root):
    from tests.payment_raw_evidence import books
    return books(root)


def test_every_exact_action_is_raw_readonly_and_reason_identity(client,sale,root):
    draft,first,_=setup(client,sale)
    edits=[dict(invoice_id=first['id'],observed_invoice_version=1,action='set',amount_minor_units=50,currency='USD',amount_origin='entered')]
    begin=declaration(draft,edits)
    recorded=[]
    def action(verb,inp):
        result=call(client,verb,inp,reason='Preserve this exact attempt')
        recorded.append((verb,inp,result['original_receipt']))
        for key in (None,'new-'+str(uuid4())):
            before=raw_books(root)
            repeat=call(client,verb,inp,reason='Preserve this exact attempt',**({'idempotency_key':key} if key else {}))
            assert repeat['original_receipt']==result['original_receipt']
            assert raw_books(root)==before
        before=raw_books(root)
        with pytest.raises(BookflowError) as caught:
            call(client,verb,inp,reason='Different reason')
        assert caught.value.code=='E_RECOVERY_KEY_REUSED'
        assert raw_books(root)==before
        return result
    result=action('begin',begin);identifier=result['original_receipt']['recovery_id']
    action('upload',dict(recovery_id=identifier,chunk_index=0,entries=edits))
    action('seal',dict(recovery_id=identifier,expected_recovery_version=2))
    compare=call(client,'compare',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash']))
    action('apply',dict(recovery_id=identifier,expected_recovery_version=3,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'],
        expected_selection_version=draft['version'],expected_facts_fingerprint=compare['facts_fingerprint']))
    for verb,inp,receipt in recorded:
        before=raw_books(root)
        assert call(client,verb,inp,reason='Preserve this exact attempt')['original_receipt']==receipt
        assert raw_books(root)==before


def test_new_calculation_cannot_claim_a_caller_amount(client,sale):
    draft,first,_=setup(client,sale)
    bad=[dict(invoice_id=first['id'],observed_invoice_version=1,action='set',amount_minor_units=1,currency='USD',amount_origin='calculated',retained_calculation_revision_id=draft['revision_id'])]
    begin=declaration(draft,bad)
    identifier=call(client,'begin',begin)['original_receipt']['recovery_id']
    with pytest.raises(BookflowError) as caught:
        call(client,'upload',dict(recovery_id=identifier,chunk_index=0,entries=bad))
    assert caught.value.code=='E_VALIDATION'
    call(client,'abort',dict(recovery_id=identifier,expected_recovery_version=1,disposition='discard_entire_attempt'))
    draft=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    edits=[dict(invoice_id=first['id'],observed_invoice_version=1,action='calculate',attempted_calculated_minor_units=1)]
    begin=declaration(draft,edits);identifier,compare=seal_compare(client,begin,edits)
    assert compare['selected_minor_units']==200  # other fixed100 leaves100, not caller hint1
    changes=call(client,'compare-items',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'],facts_fingerprint=compare['facts_fingerprint']))
    changed=next(row for row in changes['items'] if row['invoice_id']==first['id'])
    assert changed['attempted']['attempted_calculated_minor_units']==1 and changed['proposed']['amount_minor_units']==100
    confirm(client,identifier,begin,compare)


def test_added_target_stales_confirmation_without_partial_revision(client,sale,root):
    draft,first,_=setup(client,sale)
    added=invoice(client,sale,'REC-ADDED')
    edits=[dict(invoice_id=added['id'],observed_invoice_version=1,action='set',amount_minor_units=50,currency='USD',amount_origin='entered')]
    begin=declaration(draft,edits);identifier,compare=seal_compare(client,begin,edits)
    client.run('invoice update',dict(invoice=added['id'],expected_version=1,memo='Only the added target changes'),company=COMPANY,reason='Correct the added invoice')
    before=raw_books(root)
    with pytest.raises(BookflowError) as caught:
        call(client,'compare-items',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'],facts_fingerprint=compare['facts_fingerprint']))
    assert caught.value.code=='E_QUERY_STALE'
    with pytest.raises(BookflowError) as caught:
        confirm(client,identifier,begin,compare)
    assert caught.value.code=='E_PREVIEW_STALE'
    assert raw_books(root)==before
    assert client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)['version']==draft['version']


@pytest.mark.timeout(1800)
@pytest.mark.parametrize('changed_count',[201,257,403])
def test_full_403_201_interruption_one_revision_one_receipt(client,sale,root,tmp_path,changed_count):
    import time
    started=time.monotonic()
    invoices=[invoice(client,sale,'REC-LARGE-'+str(index)) for index in range(403)]
    draft=client.run('payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-01',amount='10.00'),company=COMPANY)
    for offset in range(0,403,200):
        draft=client.run('payment selection update',dict(selection=draft['id'],expected_version=draft['version'],
            set_items=[dict(invoice=row['id'],expected_version=1,amount='0.01',amount_origin='entered') for row in invoices[offset:offset+200]]),company=COMPANY)
    baseline=client.run('payment selection items',dict(selection=draft['id'],revision=draft['version'],limit=200),company=COMPANY)
    edits=sorted([dict(invoice_id=row['id'],observed_invoice_version=1,action='set',amount_minor_units=2,currency='USD',amount_origin='entered') for row in invoices[:changed_count]],key=lambda e:e['invoice_id'])
    begin=declaration(draft,edits);identifier=call(client,'begin',begin)['original_receipt']['recovery_id']
    call(client,'upload',dict(recovery_id=identifier,chunk_index=0,entries=edits[:200]))
    state=call(client,'show',dict(recovery_id=identifier))
    assert state['received_entry_count']==200 and state['declared_entry_count']==changed_count and state['missing_chunk_count']==(changed_count+199)//200-1
    method=client.run('payment-method list',{},company=COMPANY)['items'][0]['id']
    financial=dict(customer=sale['customer'],date='2026-06-01',amount='10.00',payment_method=method,operation_key='REC-LARGE-one-receipt',
        applications=dict(mode='selection',selection=draft['id'],expected_version=draft['version']))
    before=raw_books(root)
    with pytest.raises(BookflowError) as caught:
        client.run('payment receive',financial,company=COMPANY)
    assert caught.value.code=='E_RECOVERY_PENDING'
    assert raw_books(root)==before
    # New Client represents independent process state; stage evidence lives only in the company.
    import bookflow
    resumed=bookflow.connect(data_root=str(root))
    for offset in range(200,len(edits),200):
        call(resumed,'upload',dict(recovery_id=identifier,chunk_index=offset//200,entries=edits[offset:offset+200]))
    call(resumed,'seal',dict(recovery_id=identifier,expected_recovery_version=1+(changed_count+199)//200))
    comparison_start=time.monotonic()
    comparison=call(resumed,'compare',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash']))
    assert comparison['item_count']==403 and comparison['selected_minor_units']==403+changed_count and comparison['unapplied_minor_units']==597-changed_count
    pages=[];cursor=None
    while True:
        page=call(resumed,'compare-items',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'],
            facts_fingerprint=comparison['facts_fingerprint'],limit=200,**({'cursor':cursor} if cursor else {})))
        pages.extend(page['items']);cursor=page['next_cursor']
        if not cursor:break
    assert len(pages)==403
    compare_time=time.monotonic()-comparison_start
    publication_start=time.monotonic();confirm(resumed,identifier,begin,comparison);publication_time=time.monotonic()-publication_start
    shown=resumed.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    assert shown['id']==draft['id'] and shown['version']==draft['version']+1 and shown['applied_minor_units']==403+changed_count
    historical=resumed.run('payment selection items',dict(selection=draft['id'],revision=draft['version'],limit=200),company=COMPANY)
    assert historical==baseline
    financial['applications']['expected_version']=shown['version']
    paid=resumed.run('payment receive',financial,company=COMPANY)
    assert paid['id'] and resumed.run('payment query',dict(customer=sale['customer']),company=COMPANY)['total_count']==1
    assert resumed.run('payment receive',financial,company=COMPANY)['id']==paid['id']
    consumed=resumed.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    assert consumed['current_lifecycle']['state']=='consumed'
    assert consumed['current_lifecycle']['consumed_operation']['payment_id']==paid['id']
    (tmp_path/'measurements.json').write_text(json.dumps(dict(graph_rows=403,changed_rows=changed_count,selected_minor_units=403+changed_count,unapplied_minor_units=597-changed_count,full_seconds=time.monotonic()-started,compare_pages_seconds=compare_time,publication_seconds=publication_time),indent=2))
