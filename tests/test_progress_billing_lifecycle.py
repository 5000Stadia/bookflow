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


def test_leading_decimal_quote_and_partial_quantity(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item'], quantity='.5', net_amount='50.00')])
    first = bill(client, source, selections=[dict(line_id=scope_line(source), quantity='.25')])
    assert first['subtotal_minor_units'] == 2500
    assert first['revision']['lines'][0]['quantity'] == '0.25'
    remaining = run(client, 'estimate', 'billing', estimate=source['id'])
    assert remaining['lines'][0]['remaining_quantity'] == '0.25'
    assert remaining['remaining_net_minor_units'] == 2500


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
    assert first['revision']['billing_sources'][0]['allocation_version'] == 3
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
            item.proof = type(item.proof).model_validate(fields)
        return selected
    monkeypatch.setattr(billing_selection,'select',corrupt)
    with pytest.raises(BookflowError) as err:
        bill(client,source,percent='25')
    assert err.value.code == 'E_INTERNAL'
    assert snapshot(client) == before


def taxed_quote(client,sale,net='0.10'):
    import sqlalchemy as sa
    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    from tests.test_row8_journal import database_path
    agency = client.vendor.create(name='Progress tax agency',is_tax_agency=True,company=COMPANY)['id']
    taxable = next(x['id'] for x in client.run('sales-tax-code list',{},company=COMPANY)['items'] if x['taxable'])
    with open_database(database_path(client),writable=True) as db:
        liability = db.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.system_role == 'sales_tax_payable')).scalar_one()
        db.conn.execute(c.company_info.update().values(sales_tax_enabled=True,sales_tax_liability_basis='invoice_date'))
        db.conn.commit()
    tax = client.run('item create',dict(name='Progress ten percent',type='sales_tax_item',tax_percent='10',
        tax_agency_vendor_id=agency,liability_account_id=liability),company=COMPANY)['id']
    return accepted(client,sale,sales_tax_item=tax,sales_tax_calculation="line_component_half_even",
        lines=[dict(item=sale['item'],quantity='1',net_amount=net,tax_code=taxable)])


def test_installment_tax_is_ordinary_and_quote_tax_is_not_a_debt(client,sale):
    source = taxed_quote(client,sale)
    assert source['tax_minor_units'] == 1
    first = bill(client,source,percent='50')
    projected = first['billing_progress'][0]
    assert projected['current']['tax_minor_units'] == projected['remaining']['tax_minor_units'] == 0
    assert projected['cumulative']['gross_minor_units'] == 5
    second = bill(client,current(client,source),'finish')
    assert second['billing_progress'][0]['cumulative']['gross_minor_units'] == 10
    assert second['billing_progress'][0]['remaining']['tax_minor_units'] == 0
    assert [x['subtotal_minor_units'] for x in (first,second)] == [5,5]
    assert [x['tax_minor_units'] for x in (first,second)] == [0,0]
    state = run(client,'estimate','billing',estimate=source['id'])
    assert state['remaining_tax_minor_units'] == 0
    assert state['lines'][0]['tax_minor_units'] == 1 and state['lines'][0]['billed_tax_minor_units'] == 0
    assert not state['can_invoice'] and any('rounding' in w for w in state['warnings'])
    assert first['revision']['billing_sources'][0]['facts_snapshot']['line']['tax_minor_units'] == 1


def test_fragmented_remainder_tax_is_calculated_once_per_line(client,sale):
    source = taxed_quote(client,sale,net='0.09')
    installments = [bill(client,current(client,source),f'part-{i}',
        selections=[dict(line_id=scope_line(source),net_amount='0.03')]) for i in range(3)]
    assert [b['tax_minor_units'] for b in installments] == [0,0,0]
    for index in (0,2):
        client.run('invoice void',dict(invoice=installments[index]['id'],expected_version=1),company=COMPANY,reason='Recombine released work')
    combined = bill(client,current(client,source),'combined-remainder')
    assert combined['subtotal_minor_units'] == 6 and combined['tax_minor_units'] == 1
    assert len(combined['revision']['billing_sources'][0]['allocation_proof']['spans']) == 2
    assert client.run('invoice show',dict(invoice=installments[1]['id']),company=COMPANY)['tax_minor_units'] == 0


def test_partial_estimate_to_work_order_retains_shared_entitlement(client,sale):
    source = accepted(client,sale)
    first = bill(client,source,percent='25')
    order = run(client,'estimate','work-order',estimate=source['id'],expected_version=3,
        date='2026-01-14',conversion_key='progress-order')
    before = run(client,'work-order','billing',work_order=order['id'])
    assert before['remaining_net_minor_units'] == 1851 and before['lines'][0]['billed_scope_percent'] == '25'
    remaining = bill(client,order,'order-remainder',noun='work-order')
    assert first['subtotal_minor_units']+remaining['subtotal_minor_units'] == 2468
    assert run(client,'estimate','billing',estimate=source['id'])['owner_id'] == order['id']


def test_partial_retry_after_deactivation_and_generic_cache_expiry(client,sale):
    from bookflow.storage.engine import open_database
    from tests.test_row8_journal import database_path
    source = accepted(client,sale)
    values = dict(estimate=source['id'],expected_version=source['version'],date='2026-01-13',
                  conversion_key='permanent-partial',percent='25')
    first = client.run('estimate invoice',values,company=COMPANY,idempotency_key='cache-partial')
    client.run('item deactivate',dict(item=sale['item']),company=COMPANY)
    assert client.run('estimate invoice',values,company=COMPANY,idempotency_key='cache-partial')['id'] == first['id']
    with open_database(database_path(client),writable=True) as db:
        db.raw.execute("UPDATE idempotency_keys SET created_at='2000-01-01T00:00:00Z' WHERE key='cache-partial'")
    replay = client.run('estimate invoice',values,company=COMPANY,idempotency_key='cache-partial')
    assert replay['id'] == first['id'] and replay['idempotent_replay']
    assert run(client,'estimate','billing',estimate=source['id'])['remaining_net_minor_units'] == 1851


def test_partial_preview_release_names_actual_actor_event(client,sale):
    source = accepted(client,sale)
    first = bill(client,source,percent='25')
    source = current(client,source)
    values = dict(estimate=source['id'],expected_version=source['version'],date='2026-01-13',
                  conversion_key='second-partial',percent='25')
    preview = client.run('estimate invoice',values,company=COMPANY,dry_run=True)
    client.run('invoice void',dict(invoice=first['id'],expected_version=1),company=COMPANY,reason='Release prior scope')
    with pytest.raises(BookflowError) as err:
        client.run('estimate invoice',dict(values,expected_facts_fingerprint=preview['facts_fingerprint']),company=COMPANY)
    assert err.value.code == 'E_PREVIEW_STALE'
    changes = err.value.details['consumption_changes']
    assert len(changes) == 1 and changes[0]['transaction_id'] == first['id']
    assert changes[0]['updated_via'] == 'python' and changes[0]['updated_by']
    assert changes[0]['seconds_since_update'] >= 0 and 'status' in changes[0]['changed_fields']


def test_concurrent_partial_requests_serialize_with_source_versions(client,sale):
    from concurrent.futures import ThreadPoolExecutor
    source = accepted(client,sale)
    def attempt(key):
        try:
            return bill(client,source,key,percent='75')
        except BookflowError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt,['partial-race-a','partial-race-b']))
    assert sum(isinstance(x,dict) for x in results) == 1
    index = next(i for i,x in enumerate(results) if isinstance(x,str))
    rejected = results[index]
    if rejected == 'E_DB_BUSY':
        rejected = attempt(['partial-race-a','partial-race-b'][index])
    assert rejected == 'E_VERSION_CONFLICT'
    state = run(client,'estimate','billing',estimate=source['id'])
    assert state['remaining_net_minor_units'] == 617 and len(state['destinations']) == 1


def test_linked_receipt_corrections_confirm_changed_received_total_and_keep_history_guard(client,sale,monkeypatch):
    source = accepted(client,sale,lines=[dict(item=sale['item'],quantity='1',net_amount='0.10')])
    payment = dict(deposit_to=client.account.create(name='Correction bank',type='bank',company=COMPANY)['id'],
        payment_method=client.run('payment-method create',dict(name='Correction cash',kind='cash'),company=COMPANY)['id'])
    first = bill(client,source,verb='sales-receipt',percent='40',amount_received='0.04',**payment)
    other = bill(client,current(client,source),'other-installment',percent='60')
    original = first['revision']['lines'][0]
    edit = dict(sales_receipt=first['id'],expected_version=1,
        lines=[dict(line_id=original['line_id'],item=sale['item']),dict(item=sale['item'],net_amount='1')])
    before = snapshot(client)
    for confirmation in ({},dict(amount_received='0.04'),dict(amount_received='1.05'),dict(amount_received=None)):
        with pytest.raises(BookflowError) as err:
            client.run('sales-receipt update',dict(edit,**confirmation),company=COMPANY)
        assert err.value.code == 'E_VALIDATION'
        assert snapshot(client) == before
    # The independent aggregate check rejects a resolver bypass, even with balanced cash legs.
    from bookflow.company import sales as service
    prepare = service.commercial
    def bypass(s, inp, *args, **kwargs):
        inp.amount_received = '1.04'
        resolved = prepare(s, inp, *args, **kwargs)
        inp.amount_received = None
        return resolved
    with monkeypatch.context() as patch:
        patch.setattr(service, 'commercial', bypass)
        with pytest.raises(BookflowError) as err:
            client.run('sales-receipt update',edit,company=COMPANY)
        assert err.value.code == 'E_INTERNAL'
        assert snapshot(client) == before
    def corrupt_prior(s, inp, document_type, old_header=None, old_revision=None, **kwargs):
        old_revision['total_minor_units'] = 104
        return prepare(s, inp, document_type, old_header, old_revision, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(service, 'commercial', corrupt_prior)
        with pytest.raises(BookflowError) as err:
            client.run('sales-receipt update',edit,company=COMPANY)
        assert err.value.code == 'E_INTERNAL'
        assert snapshot(client) == before
    corrected = client.run('sales-receipt update',dict(edit,amount_received='1.04'),company=COMPANY)
    assert corrected['total_minor_units'] == 104
    assert corrected['revision']['lines'][0]['item_snapshot'] == original['item_snapshot']
    assert client.run('invoice show',dict(invoice=other['id']),company=COMPANY)['current_revision_id'] == other['current_revision_id']
    assert run(client,'estimate','billing',estimate=source['id'])['remaining_net_minor_units'] == 0
    noop = client.run('sales-receipt update',dict(sales_receipt=first['id'],expected_version=2),company=COMPANY)
    assert not noop['changed']
    memo = client.run('sales-receipt update',dict(sales_receipt=first['id'],expected_version=2,memo='Confirmed collection'),company=COMPANY)
    assert memo['total_minor_units'] == 104
    client.run('sales-receipt update',dict(sales_receipt=first['id'],expected_version=3,
        lines=[dict(item=sale['item'],net_amount='1')],amount_received='1'),company=COMPANY)
    before = snapshot(client)
    with pytest.raises(BookflowError) as err:
        client.run('sales-receipt update',dict(sales_receipt=first['id'],expected_version=4,
            lines=[dict(item=sale['item'],net_amount='2')]),company=COMPANY)
    assert err.value.code == 'E_VALIDATION' and snapshot(client) == before


def test_selection_identity_errors_are_indexed_and_do_not_reveal_foreign_sources(client,sale):
    source = accepted(client,sale)
    own = scope_line(source)
    for bad in (own, '0'*26):
        with pytest.raises(BookflowError) as err:
            bill(client,source,selections=[dict(line_id=own,percent='25'),dict(line_id=bad,percent='25')])
        assert err.value.code == 'E_VALIDATION'
        assert err.value.details['fields'][0]['field'] == 'selections.1.line_id'


def test_partial_positive_remaining_with_zero_price_line_does_not_say_no_charge(client,sale):
    source = accepted(client,sale,lines=[dict(item=sale['item'],net_amount='1'),dict(item=sale['item'],net_amount='0')])
    bill(client,source,selections=[dict(line_id=scope_line(source),percent='25')])
    state = run(client,'estimate','billing',estimate=source['id'])
    assert state['can_invoice'] and state['remaining_net_minor_units'] == 75
    assert not any('No charge remains' in warning for warning in state['warnings'])


def test_preview_progress_projects_exact_cumulative_and_remaining_without_writes(client,sale):
    source = accepted(client,sale,lines=[dict(item=sale['item'],quantity='0.000001',net_amount='1'),
        dict(item=sale['item'],quantity='2',net_amount='2')])
    first = bill(client,source,selections=[dict(line_id=scope_line(source),net_amount='0.40')])
    before = snapshot(client)
    preview = client.run('estimate invoice',dict(estimate=source['id'],expected_version=current(client,source)['version'],
        conversion_key='next-preview',date='2026-01-13',
        selections=[dict(line_id=scope_line(source),net_amount='0.20')]),company=COMPANY,dry_run=True)
    assert snapshot(client) == before
    a,b = preview['billing_progress']
    assert a['previous']['quantity'] == '1/2500000' and a['previous']['net_minor_units'] == 40
    assert a['current']['quantity'] == '1/5000000' and a['current']['net_minor_units'] == 20
    assert a['cumulative']['quantity'] == '3/5000000' and a['cumulative']['scope_percent'] == '60'
    assert a['remaining']['quantity'] == '1/2500000' and a['remaining']['net_minor_units'] == 40
    assert b['current']['quantity'] == '0' and b['remaining']['net_minor_units'] == 200
    posted = bill(client,current(client,source),'next-preview',
        selections=[dict(line_id=scope_line(source),net_amount='0.20')])
    assert posted['billing_progress'] == preview['billing_progress']
    state = run(client,'estimate','billing',estimate=source['id'])
    assert state['lines'][0]['billed_quantity'] == a['cumulative']['quantity']
    assert state['lines'][0]['remaining_quantity'] == a['remaining']['quantity']
