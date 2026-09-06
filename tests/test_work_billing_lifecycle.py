"""Public billing, permanent replay and corrections across shared work roots."""
import pytest
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_customer_work_lifecycle import make, run


def accepted(client, sale, **extra):
    created = make(client, sale, **extra)
    return run(client, 'estimate', 'update', estimate=created['id'], expected_version=1,
        status='accepted', decision_note='Customer accepted the scope')


def bill(client, source, key='first', noun='estimate', verb='invoice', **extra):
    return run(client, noun, verb, **{noun.replace('-', '_'): source['id']},
        expected_version=source['version'], conversion_key=key, date='2026-01-13', **extra)


def test_invoice_full_replay_void_and_rebill(client, sale):
    source = accepted(client, sale)
    first = bill(client, source)
    assert first['total_minor_units'] == 2468
    assert len(first['revision']['billing_sources']) == 1
    assert bill(client, source)['id'] == first['id']
    state = run(client, 'estimate', 'billing', estimate=source['id'])
    assert state['source_version'] == 3
    assert state['lines'][0]['billed_quantity'] == '2'
    assert state['remaining_net_minor_units'] == 0
    client.run('invoice void', dict(invoice=first['id'], expected_version=1), company=COMPANY, reason='Cancelled invoice')
    assert bill(client, source)['status'] == 'voided'
    current = run(client, 'estimate', 'show', estimate=source['id'])
    second = bill(client, current, 'rebill')
    assert second['id'] != first['id']
    assert client.customer.show(customer=sale['customer'], company=COMPANY)['current_balance']['minor_units'] == 2468


def test_amount_invoice_correction_and_source_freeze(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item'], quantity='2', net_amount='10.01')])
    first = bill(client, source)
    line = first['revision']['lines'][0]
    assert line['unit_price'] is None and line['pricing_basis'] == 'amount'
    assert line['net_minor_units'] == 1001
    corrected = client.run('invoice update', dict(invoice=first['id'], expected_version=1, memo='Invoice note'), company=COMPANY)
    assert corrected['version'] == 2 and corrected['revision']['billing_sources']
    noop = client.run('invoice update', dict(invoice=first['id'], expected_version=2), company=COMPANY)
    assert not noop['changed'] and noop['version'] == 2
    with pytest.raises(BookflowError) as err:
        client.run('invoice update', dict(invoice=first['id'], expected_version=2,
            lines=[dict(item=sale['item'], line_id=line['line_id'], quantity='3')]), company=COMPANY)
    assert err.value.code == 'E_WORK_DEPENDENCY'
    with pytest.raises(BookflowError) as err:
        run(client, 'estimate', 'update', estimate=source['id'], expected_version=3, title='Different agreement')
    assert err.value.code == 'E_WORK_DEPENDENCY'


def test_billed_estimate_work_order_shares_consumption(client, sale):
    source = accepted(client, sale)
    first = bill(client, source)
    wo = run(client, 'estimate', 'work-order', estimate=source['id'], expected_version=3,
        date='2026-01-14', conversion_key='make-work')
    state = run(client, 'work-order', 'billing', work_order=wo['id'])
    assert state['lines'][0]['destination_id'] == first['id']
    with pytest.raises(BookflowError) as err:
        bill(client, wo, 'double', noun='work-order')
    assert err.value.code == 'E_WORK_DEPENDENCY'
    old = run(client, 'estimate', 'show', estimate=source['id'])
    with pytest.raises(BookflowError) as err:
        bill(client, old, 'ancestor')
    assert err.value.code == 'E_WORK_DEPENDENCY'


def test_zero_nonbillable_selection_and_line_release(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item'], net_amount='10.01'),
        dict(item=sale['item'], unit_price='0'), dict(item=sale['item'], unit_price='2', billable=False)])
    ids = [row['line_id'] for row in source['revision']['lines']]
    first = bill(client, source, line_ids=[ids[0]])
    state = run(client, 'estimate', 'billing', estimate=source['id'])
    assert [line['state'] for line in state['lines']] == ['billed', 'no_charge', 'nonbillable']
    assert not state['can_invoice']
    # Remove the linked line and keep an independent actual-sale line.
    edited = client.run('invoice update', dict(invoice=first['id'], expected_version=1,
        lines=[dict(item=sale['item'], unit_price='5')]), company=COMPANY)
    assert not edited['revision']['billing_sources']
    assert run(client, 'estimate', 'billing', estimate=source['id'])['remaining_net_minor_units'] == 1001
