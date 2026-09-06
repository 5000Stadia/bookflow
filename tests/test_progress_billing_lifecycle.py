"""Partial invoices/paid receipts, exact history and independently rejected faults."""
from fractions import Fraction

import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, snapshot, COMPANY
from tests.test_work_billing_lifecycle import accepted, bill
from tests.test_customer_work_lifecycle import make, run


def current(client, source, noun='estimate'):
    return run(client,noun,'show',**{noun.replace('-','_'):source['id']})


def scope_line(source, index=0):
    return source['revision']['lines'][index]['line_id']


@pytest.mark.parametrize('noun',['estimate','work-order'])
@pytest.mark.parametrize('verb',['invoice','sales-receipt'])
def test_sub_microunit_amount_partial_and_remaining(client,sale,noun,verb):
    fields = dict(lines=[dict(item=sale['item'],quantity='0.000001',net_amount='1.00')])
    source = accepted(client,sale,**fields) if noun == 'estimate' else make(client,sale,noun=noun,**fields)
    extra = {}
    if verb == 'sales-receipt':
        extra = dict(deposit_to=client.account.create(name='Progress bank',type='bank',company=COMPANY)['id'],
                     payment_method=client.run('payment-method create',dict(name='Progress cash',kind='cash'),company=COMPANY)['id'],
                     amount_received='0.40')
    first = bill(client,source,noun=noun,verb=verb,
        selections=[dict(line_id=scope_line(source),net_amount='0.40')],**extra)
    line = first['revision']['lines'][0]
    assert line['net_minor_units'] == 40 and line['quantity_microunits'] is None
    assert line['quantity'] == '1/2500000' and line['quoted_quantity'] == '0.000001'
    assert line['quantity_fraction'] == dict(numerator='1',denominator='2500000')
    assert line['pricing_basis'] == 'allocated' and line['unit_price'] is None
    assert first['revision']['billing_sources'][0]['allocation_version'] == 2
    state = run(client,noun,'billing',**{noun.replace('-','_'):source['id']})
    assert state['remaining_net_minor_units'] == 60 and state['lines'][0]['state'] == 'partially_billed'
    assert state['lines'][0]['billed_scope_percent'] == '40'
    assert state['lines'][0]['remaining_quantity'] == '3/5000000'
    if extra:
        extra['amount_received'] = '0.60'
    second = bill(client,current(client,source,noun),'remaining',noun=noun,verb=verb,**extra)
    assert second['total_minor_units'] == 60
    done = run(client,noun,'billing',**{noun.replace('-','_'):source['id']})
    assert done['lines'][0]['billed_quantity'] == '0.000001' and done['remaining_net_minor_units'] == 0
    assert done['lines'][0]['billed_scope_percent'] == '100' and not done['can_invoice']
    if verb == 'sales-receipt':
        assert all(d['amount_due_minor_units'] == 0 for d in done['destinations'])


def test_exact_rebill_reproduces_second_installment_after_two_earlier_voids(client,sale):
    source = accepted(client,sale,lines=[dict(item=sale['item'],quantity='3',net_amount='0.04')])
    bills = []
    for index in range(3):
        bills.append(bill(client,current(client,source),f'installment-{index}',
            selections=[dict(line_id=scope_line(source),quantity='1')]))
    assert [b['subtotal_minor_units'] for b in bills] == [1,2,1]
    assert [b['revision']['lines'][0]['quantity'] for b in bills] == ['1']*3
    saved_third = client.run('invoice show',dict(invoice=bills[2]['id']),company=COMPANY)
    for b in bills[:2]:
        client.run('invoice void',dict(invoice=b['id'],expected_version=1),company=COMPANY,reason='Rebill installment')
    reference = bills[1]['revision']['billing_sources'][0]['id']
    rebilled = bill(client,current(client,source),'restore-second',
        selections=[dict(line_id=scope_line(source),rebill_allocation_id=reference)])
    assert rebilled['subtotal_minor_units'] == 2 and rebilled['revision']['lines'][0]['quantity'] == '1'
    assert rebilled['revision']['billing_sources'][0]['allocation_proof']['spans'] == bills[1]['revision']['billing_sources'][0]['allocation_proof']['spans']
    with pytest.raises(BookflowError) as err:
        bill(client,current(client,source),'duplicate-restore',
            selections=[dict(line_id=scope_line(source),rebill_allocation_id=reference)])
    assert err.value.code == 'E_WORK_DEPENDENCY'
    assert client.run('invoice show',dict(invoice=bills[2]['id']),company=COMPANY) == saved_third
    last = bill(client,current(client,source),'finish')
    assert last['subtotal_minor_units'] == 1


def test_positive_scope_rounding_to_zero_is_visible_and_consumed(client,sale):
    source = accepted(client,sale,lines=[dict(item=sale['item'],quantity='1',net_amount='0.01'),
                                       dict(item=sale['item'],quantity='1',net_amount='1.00')])
    first = bill(client,source,percent='25')
    assert [line['net_minor_units'] for line in first['revision']['lines']] == [0,25]
    assert first['revision']['lines'][0]['quantity'] == '0.25'
    state = run(client,'estimate','billing',estimate=source['id'])
    assert state['lines'][0]['billed_quantity'] == '0.25'
    assert state['lines'][0]['remaining_net_minor_units'] == 1
    assert state['lines'][0]['state'] == 'partially_billed'


def test_partial_correction_keeps_proof_and_adds_independent_charge(client,sale):
    source = accepted(client,sale)
    first = bill(client,source,percent='25')
    original = first['revision']['lines'][0]
    changed = client.run('invoice update',dict(invoice=first['id'],expected_version=1,
        lines=[dict(line_id=original['line_id'],item=sale['item']),dict(item=sale['item'],net_amount='5',description='Extra work')]),company=COMPANY)
    assert changed['subtotal_minor_units'] == first['subtotal_minor_units']+500
    assert changed['revision']['lines'][0]['item_snapshot'] == original['item_snapshot']
    assert len(changed['revision']['billing_sources']) == 1
    state = run(client,'estimate','billing',estimate=source['id'])
    assert state['remaining_net_minor_units'] == 1851
    noop = client.run('invoice update',dict(invoice=first['id'],expected_version=2),company=COMPANY)
    assert not noop['changed']
    with pytest.raises(BookflowError) as err:
        client.run('invoice update',dict(invoice=first['id'],expected_version=2,
            lines=[dict(line_id=original['line_id'],item=sale['item'],quantity='1')]),company=COMPANY)
    assert err.value.code == 'E_WORK_DEPENDENCY'
    client.run('invoice update',dict(invoice=first['id'],expected_version=2,
        lines=[dict(item=sale['item'],net_amount='5')]),company=COMPANY)
    assert run(client,'estimate','billing',estimate=source['id'])['remaining_net_minor_units'] == 2468


def test_original_scope_percent_cannot_silently_overbill(client,sale):
    source = accepted(client,sale)
    bill(client,source,percent='75')
    before = snapshot(client)
    with pytest.raises(BookflowError) as err:
        bill(client,current(client,source),'overrun',percent='50')
    assert err.value.code == 'E_VALUE_RANGE'
    assert err.value.details['available_net_minor_units'] == 617
    assert snapshot(client) == before


def test_allocated_quantity_fault_is_rejected_against_stored_quote(client,sale,monkeypatch):
    from bookflow.company import billing
    source = accepted(client,sale,lines=[dict(item=sale['item'],quantity='0.000001',net_amount='1')])
    before = snapshot(client)
    original = billing.resolved_line
    def corrupt(facts,proof=None):
        line = original(facts,proof)
        if proof:
            line['profile'].allocation_proof.quoted_quantity_microunits *= 2
        return line
    monkeypatch.setattr(billing,'resolved_line',corrupt)
    with pytest.raises(BookflowError) as err:
        bill(client,source,selections=[dict(line_id=scope_line(source),net_amount='0.40')])
    assert err.value.code == 'E_INTERNAL'
    assert snapshot(client) == before


def test_wrong_free_spans_are_not_accepted_merely_because_amounts_match(client,sale,monkeypatch):
    from bookflow.company import billing_selection
    from bookflow.company.billing_facts import AllocationProof
    source = accepted(client,sale,lines=[dict(item=sale['item'],quantity='1',net_amount='1')])
    before = snapshot(client)
    original = billing_selection.select
    def corrupt(*args,**kwargs):
        selected = original(*args,**kwargs)
        for item in selected:
            d = int(item.proof.denominator)
            item.spans = ((d//2,3*d//4),)
            fields = item.proof.model_dump()
            fields['spans'] = [dict(start=str(d//2),end=str(3*d//4))]
            item.proof = AllocationProof.model_validate(fields)
        return selected
    monkeypatch.setattr(billing_selection,'select',corrupt)
    with pytest.raises(BookflowError) as err:
        bill(client,source,percent='25')
    assert err.value.code == 'E_INTERNAL'
    assert snapshot(client) == before
