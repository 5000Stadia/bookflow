"""Posted hand-fixed rounding, capture, immutable tax order and rollback witnesses."""
import json
import sqlite3
import pytest
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY, snapshot
from tests.test_row8_journal import database_path, assert_oracle


@pytest.fixture
def tax_sale(client, sale):
    with sqlite3.connect(database_path(client)) as db:
        liability = db.execute("SELECT id FROM accounts WHERE system_role='sales_tax_payable'").fetchone()[0]
    agency = client.vendor.create(name='Policy agency', is_tax_agency=True, company=COMPANY)['id']
    taxable = next(r['id'] for r in client.run('sales-tax-code list', {}, company=COMPANY)['items'] if r['taxable'])
    rules = [client.run('item create', dict(name='Policy '+label, type='sales_tax_item', tax_percent='5',
        tax_agency_vendor_id=agency, liability_account_id=liability), company=COMPANY)['id'] for label in ('A', 'Z')]
    rules.sort()
    group = client.run('item create', dict(name='Policy group', type='sales_tax_group',
        members=[dict(component_item_id=r, quantity='1') for r in reversed(rules)]), company=COMPANY)['id']
    client.run('company update', dict(sales_tax_enabled=True), company=COMPANY)
    return dict(sale, rules=rules, group=group, taxable=taxable, liability=liability)


def request(facts, policy=None, nets=('0.10', '0.10')):
    data = dict(customer=facts['customer'], date='2026-06-01', sales_tax_item=facts['group'],
        lines=[dict(item=facts['item'], net_amount=net, tax_code=facts['taxable']) for net in nets])
    if policy is not None:
        data['sales_tax_calculation'] = policy
    return data


def cells(output):
    return {(i, c['tax_item_id']): c['tax_minor_units'] for i, line in enumerate(output['revision']['lines']) for c in line['tax_components']}


@pytest.mark.parametrize('policy,expected', [
    ('line_component_half_even', (0, 0, 0, 0)),
    ('line_combined_half_up', (1, 0, 1, 0)),
    ('invoice_combined_half_up', (1, 1, 0, 0)),
])
def test_complete_posted_cells_and_preview(client, tax_sale, policy, expected):
    data = request(tax_sale, policy)
    before = snapshot(client)
    preview = client.run('invoice post', data, company=COMPANY, dry_run=True)
    assert snapshot(client) == before
    result = client.run('invoice post', dict(data, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    a, z = tax_sale['rules']
    assert cells(result) == dict(zip([(0,a),(0,z),(1,a),(1,z)], expected))
    details = result['revision']['tax_calculation_details']
    assert details == preview['revision']['tax_calculation_details']
    assert details['policy'] == policy and details['origin']['kind'] == 'explicit'
    assert result['tax_minor_units'] == sum(expected)
    ar = result['revision']['profile']['control_account']['id']
    oracle = {('2026-06-01', ar): 20+sum(expected), ('2026-06-01', tax_sale['income']): -20}
    if sum(expected): oracle['2026-06-01', tax_sale['liability']] = -sum(expected)
    assert_oracle(client, result['id'], oracle)
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT tax_ordinal FROM sales_tax_line_keys WHERE transaction_id=? ORDER BY tax_ordinal', (result['id'],)).fetchall() == [(1,), (2,)]
        assert db.execute('SELECT count(*) FROM settlement_line_keys WHERE transaction_id=?', (result['id'],)).fetchone() == (0,)


def test_reorder_append_retire_and_noop(client, tax_sale):
    initial = client.run('invoice post', request(tax_sale), company=COMPANY)
    ids = [line['line_id'] for line in initial['revision']['lines']]
    data = dict(invoice=initial['id'], expected_version=1,
        lines=[dict(item=tax_sale['item'], line_id=identity) for identity in reversed(ids)])
    changed = client.run('invoice update', data, company=COMPANY)
    a,z = tax_sale['rules']
    assert cells(changed) == {(0,a):0,(0,z):0,(1,a):1,(1,z):1}
    assert [line['tax_ordinal'] for line in changed['revision']['lines']] == [2,1]
    before = snapshot(client)
    noop = client.run('invoice update', dict(invoice=initial['id'], expected_version=2), company=COMPANY)
    assert not noop['changed'] and snapshot(client) == before
    changed = client.run('invoice update', dict(invoice=initial['id'], expected_version=2,
        lines=[dict(item=tax_sale['item'], line_id=ids[1]), dict(item=tax_sale['item'], net_amount='0.10', tax_code=tax_sale['taxable'])]), company=COMPANY)
    with sqlite3.connect(database_path(client)) as db:
        mapping = dict(db.execute('SELECT line_id,tax_ordinal FROM sales_tax_line_keys WHERE transaction_id=?', (initial['id'],)))
    assert mapping[ids[0]] == 1 and mapping[ids[1]] == 2
    assert mapping[changed['revision']['lines'][1]['line_id']] == 3


def test_default_origin_refresh_explicit_and_stale_preview(client, tax_sale):
    data = request(tax_sale)
    preview = client.run('invoice post', data, company=COMPANY, dry_run=True)
    client.run('company update', dict(sales_tax_calculation='line_combined_half_up'), company=COMPANY)
    with pytest.raises(BookflowError) as exc:
        client.run('invoice post', dict(data, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert exc.value.code == 'E_PREVIEW_STALE'
    result = client.run('invoice post', data, company=COMPANY)
    client.run('company update', dict(sales_tax_calculation='line_component_half_even'), company=COMPANY)
    retained = client.run('invoice update', dict(invoice=result['id'], expected_version=1, memo='Keep captured'), company=COMPANY)
    assert retained['tax_minor_units'] == 2
    refreshed = client.run('invoice update', dict(invoice=result['id'], expected_version=2, refresh_defaults=True), company=COMPANY)
    assert refreshed['tax_minor_units'] == 0
    explicit = client.run('invoice update', dict(invoice=result['id'], expected_version=3, sales_tax_calculation='invoice_combined_half_up'), company=COMPANY)
    refreshed = client.run('invoice update', dict(invoice=result['id'], expected_version=4, refresh_defaults=True), company=COMPANY)
    assert refreshed['tax_minor_units'] == 2 and refreshed['revision']['tax_calculation_details']['origin']['kind'] == 'explicit'


@pytest.mark.parametrize('value', [None, '', 'half_up', True, 2])
def test_invalid_policy_is_atomic(client, tax_sale, value):
    before = snapshot(client)
    with pytest.raises(BookflowError) as exc:
        client.run('invoice post', dict(request(tax_sale), sales_tax_calculation=value), company=COMPANY)
    assert exc.value.code == 'E_VALIDATION' and snapshot(client) == before


def test_coherent_wrong_cells_rejected_before_any_effect(client,tax_sale,monkeypatch):
    from bookflow.company import sales, sales_validation
    validate=sales_validation.validate
    def corrupt(plan,s,ctx):
        if not plan.data['changed'] or plan.data['operation']=='void':return validate(plan,s,ctx)
        pending=plan.data['pending']
        comps=pending['sales_tax_components']
        # One line, one charged cent: exchange A/Z in both commercial and ledger
        # attribution. The same liability account and complete ledger sums remain.
        a,z=sorted(comps,key=lambda c:c['tax_item_id'])
        a['tax_minor_units'],z['tax_minor_units']=z['tax_minor_units'],a['tax_minor_units']
        for source in pending['posting_line_sources']:
            if source['tax_component_id']==a['id']:source['tax_component_id']=z['id']
            elif source['tax_component_id']==z['id']:source['tax_component_id']=a['id']
        snap=json.loads(pending['sales_tax_attributions'][0]['facts_snapshot'])
        entries=snap['calculation']['buckets'][0]['cells']
        entries[0]['tax_minor_units'],entries[1]['tax_minor_units']=entries[1]['tax_minor_units'],entries[0]['tax_minor_units']
        pending['sales_tax_attributions'][0]['facts_snapshot']=json.dumps(snap)
        return validate(plan,s,ctx)
    monkeypatch.setattr(sales_validation,'validate',corrupt)
    before=snapshot(client)
    with pytest.raises(BookflowError) as exc:
        client.run('invoice post',request(tax_sale,nets=('0.10',)),company=COMPANY)
    assert exc.value.code=='E_INTERNAL' and snapshot(client)==before


def test_posted_policy_edit_restates_payment_without_cash_change(client,tax_sale):
    invoice=client.run('invoice post',request(tax_sale,'line_component_half_even'),company=COMPANY)
    paid=client.run('payment receive',dict(customer=tax_sale['customer'],date='2026-06-02',amount='0.10',payment_method='Check',
        operation_key='policy-cash',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='0.10')])),company=COMPANY)
    with sqlite3.connect(database_path(client)) as db:
        cash=db.execute('SELECT * FROM payment_components WHERE transaction_id=? ORDER BY rowid',(paid['id'],)).fetchall()
        settlement=db.execute('SELECT * FROM settlement_line_keys WHERE transaction_id=? ORDER BY rowid',(invoice['id'],)).fetchall()
    args=dict(invoice=invoice['id'],expected_version=2,operation_key='policy-edit',sales_tax_calculation='invoice_combined_half_up',
        settlement_versions=[dict(payment=paid['id'],expected_version=1)])
    preview=client.run('invoice update',args,reason='Correct captured tax policy',company=COMPANY,dry_run=True)
    revised=client.run('invoice update',dict(args,expected_facts_fingerprint=preview['facts_fingerprint']),reason='Correct captured tax policy',company=COMPANY)
    assert revised['tax_minor_units']==2 and revised['settlement']['current']['due_minor_units']==12
    client.run('company update',dict(sales_tax_calculation='line_combined_half_up'),company=COMPANY)
    replay=client.run('invoice update',args,reason='Correct captured tax policy',company=COMPANY)
    assert replay['idempotent_replay'] and replay['revision']==revised['revision']
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT * FROM payment_components WHERE transaction_id=? ORDER BY rowid',(paid['id'],)).fetchall()==cash
        assert db.execute('SELECT * FROM settlement_line_keys WHERE transaction_id=? ORDER BY rowid',(invoice['id'],)).fetchall()==settlement


def test_tax_order_differs_from_first_settlement_binary_order(client,tax_sale,monkeypatch):
    from bookflow.company import sales
    from ulid import ULID
    counter=iter(range(100000,99000,-1))
    with monkeypatch.context() as patch:
        patch.setattr(sales,'new_id',lambda: str(ULID.from_int(next(counter))))
        invoice=client.run('invoice post',request(tax_sale),company=COMPANY)
    first,second=[line['line_id'] for line in invoice['revision']['lines']]
    assert first>second
    paid=client.run('payment receive',dict(customer=tax_sale['customer'],date='2026-06-02',amount='0.10',payment_method='Check',
        operation_key='different-orders',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='0.10')])),company=COMPANY)
    with sqlite3.connect(database_path(client)) as db:
        tax=dict(db.execute('SELECT line_id,tax_ordinal FROM sales_tax_line_keys WHERE transaction_id=?',(invoice['id'],)))
        settlement=dict(db.execute('SELECT line_id,ordinal FROM settlement_line_keys WHERE transaction_id=?',(invoice['id'],)))
    assert tax=={first:1,second:2} and settlement=={second:1,first:2}
    changed=client.run('invoice update',dict(invoice=invoice['id'],expected_version=2,operation_key='different-orders-edit',
        settlement_versions=[dict(payment=paid['id'],expected_version=1)],memo='Keep both namespaces'),reason='Keep captured tax order',company=COMPANY)
    assert cells(changed)==cells(invoice)
    with sqlite3.connect(database_path(client)) as db:
        assert dict(db.execute('SELECT line_id,ordinal FROM settlement_line_keys WHERE transaction_id=?',(invoice['id'],)))==settlement
