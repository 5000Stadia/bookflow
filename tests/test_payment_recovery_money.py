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
