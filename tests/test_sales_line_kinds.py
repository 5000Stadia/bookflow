"""R147: subtotal, discount, percentage-charge and group lines on sales, against hand-computed oracles.

Every expected figure below is worked out by hand from the rule it witnesses, never read back
from the implementation: a subtotal sums the amounts shown above it back to the previous
subtotal; a discount or percentage charge applies to the line or subtotal directly above; a
discount's amount is shared over what it applies to in proportion to their nets (largest
remainder, earlier line first) and debits its own account; a taxable discount comes out of the
taxable base of the lines it reduces, a non-taxable one does not.
"""
import sqlite3
from pathlib import Path

import pytest

from bookflow import BookflowError
from tests.test_row8_journal import assert_oracle, database_path
from tests.test_service_sales_lifecycle import COMPANY, snapshot

DATE = '2026-06-01'


@pytest.fixture
def kinds(client):
    run = lambda name, data: client.run(name, data, company=COMPANY)
    account = lambda name, kind: client.account.create(name=name, type=kind, company=COMPANY)['id']
    income, fees = account('Kinds income', 'income'), account('Kinds fee income', 'income')
    given, expense = account('Kinds discounts given', 'income'), account('Kinds discount expense', 'expense')
    codes = run('sales-tax-code list', {})['items']
    taxable = next(r['id'] for r in codes if r['taxable'])
    exempt = next(r['id'] for r in codes if not r['taxable'])
    with sqlite3.connect(database_path(client)) as db:
        liability = db.execute("SELECT id FROM accounts WHERE system_role='sales_tax_payable'").fetchone()[0]
    agency = client.vendor.create(name='Kinds agency', is_tax_agency=True, company=COMPANY)['id']
    tax = run('item create', dict(name='Kinds ten percent', type='sales_tax_item', tax_percent='10',
                                  tax_agency_vendor_id=agency, liability_account_id=liability))['id']
    run('company update', dict(sales_tax_enabled=True))
    customer = client.customer.create(name='Kinds customer', company=COMPANY)['id']
    item = lambda **values: run('item create', values)['id']
    labor = item(name='Kinds labor', type='service', description='Labor', price='100.00',
                 income_account_id=income, sales_tax_code_id=taxable)
    parts = item(name='Kinds parts', type='service', description='Parts', price='50.00',
                 income_account_id=income, sales_tax_code_id=exempt)
    return dict(
        customer=customer, income=income, fees=fees, given=given, expense=expense, liability=liability,
        agency=agency, tax=tax, taxable=taxable, exempt=exempt, labor=labor, parts=parts,
        subtotal=item(name='Kinds subtotal', type='subtotal', description='Subtotal'),
        off_taxed=item(name='Kinds 10% off', type='discount', description='Ten percent off',
                       discount_percent='10', income_account_id=given, sales_tax_code_id=taxable),
        off_untaxed=item(name='Kinds 10% off untaxed', type='discount', description='Coupon',
                         discount_percent='10', income_account_id=given, sales_tax_code_id=exempt),
        five_off=item(name='Kinds 5 off', type='discount', description='Five dollars off',
                      discount_amount='5.00', expense_account_id=expense, sales_tax_code_id=taxable),
        fee=item(name='Kinds 15% fee', type='other_charge', description='Handling fee', charge_percent='15',
                 income_account_id=fees, sales_tax_code_id=taxable),
        kit=item(name='Kinds kit', type='group', description='Service kit', print_members=False,
                 members=[dict(component_item_id=labor, quantity='1'), dict(component_item_id=parts, quantity='2')]),
    )


def post(client, facts, lines, policy='invoice_combined_half_up', **extra):
    return client.run('invoice post', dict(date=DATE, customer=facts['customer'], sales_tax_item=facts['tax'],
                                           sales_tax_calculation=policy, lines=lines, **extra), company=COMPANY)


def shown(result):
    """(kind, amount shown, net, tax, taxable bases) per line."""
    return [(line.get('line_kind', 'item'), (line.get('amount') or line['net'])['minor_units'],
             line['net_minor_units'], line['tax_minor_units'],
             [component['taxable_minor_units'] for component in line['tax_components']])
            for line in result['revision']['lines']]


def ar(result):
    return result['revision']['profile']['control_account']['id']


def test_subtotal_and_taxable_percentage_discount_after_it(client, kinds):
    k = kinds
    lines = [dict(item=k['labor']), dict(item=k['labor'], quantity='2'), dict(item=k['subtotal']),
             dict(item=k['off_taxed'])]
    preview = client.run('invoice post', dict(date=DATE, customer=k['customer'], sales_tax_item=k['tax'],
        sales_tax_calculation='invoice_combined_half_up', lines=lines), company=COMPANY, dry_run=True)
    result = post(client, k, lines, expected_facts_fingerprint=preview['facts_fingerprint'])
    # 300.00 subtotal; 10% = 30.00 shared 100:200 -> 10.00 and 20.00; tax 10% of 270.00.
    assert shown(result) == [('item', 10000, 9000, 900, [9000]), ('item', 20000, 18000, 1800, [18000]),
                             ('subtotal', 30000, 0, 0, []), ('discount', -3000, 0, 0, [])]
    assert (result['subtotal_minor_units'], result['tax_minor_units'], result['total_minor_units']) == (27000, 2700, 29700)
    assert shown(preview) == shown(result)
    discount = result['revision']['lines'][3]['item_snapshot']['adjustment']
    assert discount['applies_to'] == 'subtotal' and discount['base_minor_units'] == 30000
    assert [(t['position'], t['amount_minor_units']) for t in discount['targets']] == [(1, 1000), (2, 2000)]
    # Income at the full amount, the discount on its own account, the receivable net.
    assert_oracle(client, result['id'], {(DATE, ar(result)): 29700, (DATE, k['income']): -30000,
                                         (DATE, k['given']): 3000, (DATE, k['liability']): -2700})


def test_non_taxable_discount_leaves_the_taxable_base_whole(client, kinds):
    k = kinds
    result = post(client, k, [dict(item=k['labor']), dict(item=k['labor'], quantity='2'),
                              dict(item=k['subtotal']), dict(item=k['off_untaxed'])])
    # Nets fall by 30.00 but tax is still 10% of the 300.00 sold.
    assert shown(result) == [('item', 10000, 9000, 1000, [10000]), ('item', 20000, 18000, 2000, [20000]),
                             ('subtotal', 30000, 0, 0, []), ('discount', -3000, 0, 0, [])]
    assert result['total_minor_units'] == 27000 + 3000
    assert_oracle(client, result['id'], {(DATE, ar(result)): 30000, (DATE, k['income']): -30000,
                                         (DATE, k['given']): 3000, (DATE, k['liability']): -3000})


def test_fixed_discount_on_the_line_above_posts_to_an_expense_account(client, kinds):
    k = kinds
    result = post(client, k, [dict(item=k['labor']), dict(item=k['five_off'])])
    assert shown(result) == [('item', 10000, 9500, 950, [9500]), ('discount', -500, 0, 0, [])]
    assert result['revision']['lines'][1]['item_snapshot']['adjustment']['applies_to'] == 'line'
    assert_oracle(client, result['id'], {(DATE, ar(result)): 10450, (DATE, k['income']): -10000,
                                         (DATE, k['expense']): 500, (DATE, k['liability']): -950})


def test_percentage_charge_on_the_line_above_is_a_taxable_sold_line(client, kinds):
    k = kinds
    result = post(client, k, [dict(item=k['labor']), dict(item=k['fee'])])
    # 15% of 100.00 = 15.00 to fee income; tax 10% of 115.00 = 11.50.
    assert shown(result) == [('item', 10000, 10000, 1000, [10000]), ('charge', 1500, 1500, 150, [1500])]
    assert_oracle(client, result['id'], {(DATE, ar(result)): 12650, (DATE, k['income']): -10000,
                                         (DATE, k['fees']): -1500, (DATE, k['liability']): -1150})
    explicit = post(client, k, [dict(item=k['labor']), dict(item=k['fee'], percent='20')])
    assert shown(explicit)[1][:3] == ('charge', 2000, 2000)


def test_group_expands_into_its_members_and_prints_as_one_line(client, kinds):
    k = kinds
    result = post(client, k, [dict(item=k['kit'], quantity='2')])
    lines = result['revision']['lines']
    assert [(line['item_id'], line['quantity_microunits'], line['net_minor_units'], line['tax_minor_units'])
            for line in lines] == [(k['labor'], 2_000_000, 20000, 2000), (k['parts'], 4_000_000, 20000, 0)]
    assert {line['item_snapshot']['group']['item']['id'] for line in lines} == {k['kit']}
    assert all(line['item_snapshot']['group']['print_members'] is False for line in lines)
    assert result['total_minor_units'] == 42000
    from bookflow.documents.model import build
    printed = build(lambda name, data, company: client.run(name, data, company=company),
                    client.company.show(company=COMPANY)['id'], 'invoice', {'document': result['id']})
    assert printed.rows == (('Kinds kit', 'Service kit', '', '', '20.00', '400.00'),)
    # Members are ordinary lines afterwards: one can change on its own.
    changed = client.run('invoice update', dict(invoice=result['id'], expected_version=1, lines=[
        dict(item=k['labor'], line_id=lines[0]['line_id'], quantity='1'),
        dict(item=k['parts'], line_id=lines[1]['line_id'])]), company=COMPANY)
    assert [line['net_minor_units'] for line in changed['revision']['lines']] == [10000, 20000]
    assert changed['revision']['lines'][0]['item_snapshot']['group']['item']['id'] == k['kit']


@pytest.mark.parametrize('policy,taxed,untaxed', [
    # Two 0.06 lines, a 0.02 discount on their subtotal (0.01 each), tax 10%.
    # Taxable discount: bases 0.05 + 0.05. Non-taxable discount: bases 0.06 + 0.06.
    ('line_component_half_even', [0, 0], [1, 1]),   # 0.5 -> 0 (even); 0.6 -> 1 each
    ('line_combined_half_up', [1, 1], [1, 1]),      # 0.5 -> 1 each; 0.6 -> 1 each
    ('invoice_combined_half_up', [1, 0], [1, 0]),   # 1.0 -> 1 to ordinal 1; 1.2 -> 1 to ordinal 1
])
def test_line_versus_invoice_combined_rounding_with_a_discount(client, kinds, policy, taxed, untaxed):
    k = kinds
    for discount, expected in ((k['off_taxed'], taxed), (k['off_untaxed'], untaxed)):
        result = post(client, k, [dict(item=k['labor'], net_amount='0.06'), dict(item=k['labor'], net_amount='0.06'),
                                  dict(item=k['subtotal']), dict(item=discount, net_amount='0.02')], policy=policy)
        assert [line['net_minor_units'] for line in result['revision']['lines']] == [5, 5, 0, 0]
        assert [line['tax_minor_units'] for line in result['revision']['lines']][:2] == expected
        assert result['total_minor_units'] == 10 + sum(expected)


@pytest.mark.parametrize('policy,discount', [
    ('line_component_half_even', 10),   # 10% of 1.05 = 10.5 cents, half-even -> 10
    ('line_combined_half_up', 11),      # half-up -> 11
    ('invoice_combined_half_up', 11),
])
def test_percentage_amount_rounds_with_the_documents_policy(client, kinds, policy, discount):
    k = kinds
    result = post(client, k, [dict(item=k['labor'], net_amount='1.05'), dict(item=k['off_taxed'])], policy=policy)
    assert shown(result)[1][1] == -discount
    assert result['revision']['lines'][0]['net_minor_units'] == 105 - discount


def test_correction_recomputes_and_void_reverses_exactly(client, kinds):
    k = kinds
    first = post(client, k, [dict(item=k['labor']), dict(item=k['labor'], quantity='2'),
                             dict(item=k['subtotal']), dict(item=k['off_taxed'])])
    ids = [line['line_id'] for line in first['revision']['lines']]
    changed = client.run('invoice update', dict(invoice=first['id'], expected_version=1, date='2026-06-02', lines=[
        dict(item=k['labor'], line_id=ids[0]), dict(item=k['labor'], line_id=ids[1], quantity='3'),
        dict(item=k['subtotal'], line_id=ids[2]), dict(item=k['off_taxed'], line_id=ids[3])]), company=COMPANY)
    # 400.00 subtotal; 40.00 shared 100:300 -> 10.00, 30.00; tax 10% of 360.00.
    assert shown(changed) == [('item', 10000, 9000, 900, [9000]), ('item', 30000, 27000, 2700, [27000]),
                              ('subtotal', 40000, 0, 0, []), ('discount', -4000, 0, 0, [])]
    assert_oracle(client, first['id'], {('2026-06-02', ar(first)): 39600, ('2026-06-02', k['income']): -40000,
                                        ('2026-06-02', k['given']): 4000, ('2026-06-02', k['liability']): -3600})
    history = client.run('invoice history', dict(invoice=first['id']), company=COMPANY)
    assert [item['total_minor_units'] for item in history['items']] == [29700, 39600]
    retained = client.run('invoice show', dict(invoice=first['id'], revision_number=1), company=COMPANY)
    assert shown(retained) == shown(first)
    before = snapshot(client)
    noop = client.run('invoice update', dict(invoice=first['id'], expected_version=2), company=COMPANY)
    assert not noop['changed'] and snapshot(client) == before
    client.run('invoice void', dict(invoice=first['id'], expected_version=2), reason='Entered twice', company=COMPANY)
    assert_oracle(client, first['id'], {})


def test_payment_settles_the_discounted_nets_and_tax(client, kinds):
    k = kinds
    invoice = post(client, k, [dict(item=k['labor']), dict(item=k['labor'], quantity='2'),
                               dict(item=k['subtotal']), dict(item=k['off_taxed'])])
    paid = client.run('payment receive', dict(customer=k['customer'], date='2026-06-03', amount='297.00',
        payment_method='Check', operation_key='kinds-pay', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='297.00')])), company=COMPANY)
    shown_invoice = client.run('invoice show', dict(invoice=invoice['id']), company=COMPANY)
    assert shown_invoice['settlement_current']['due_minor_units'] == 0
    with sqlite3.connect(database_path(client)) as db:
        rows = db.execute("""SELECT target_ordinal, logical_kind, amount_minor_units FROM application_allocations
                             WHERE application_id IN (SELECT id FROM applications WHERE paying_transaction_id=?)
                             ORDER BY target_ordinal, logical_kind""", (paid['id'],)).fetchall()
    # Settlement ordinals follow binary line-id order; amounts are the stored components.
    assert sorted((kind, amount) for _, kind, amount in rows) == sorted(
        [('net', 9000), ('tax', 900), ('net', 18000), ('tax', 1800)])
    from bookflow.company.cash_basis import cutoff_adjustments
    path = Path(client.company.show(company=COMPANY)['path']) / 'company.db'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        def cash(cutoff):
            values = {}
            for account, debit, credit in db.execute('''SELECT l.account_id, l.debit_minor_units, l.credit_minor_units
                    FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id WHERE l.transaction_id=?
                    AND b.effective_date<=?''', (invoice['id'], cutoff)):
                values[account] = values.get(account, 0) + debit - credit
            for row in cutoff_adjustments(db, cutoff):
                if row.transaction_id == invoice['id']:
                    values[row.account_id] = values.get(row.account_id, 0) + row.amount_minor_units
            return {account: value for account, value in values.items() if value and account in (k['income'], k['given'])}
        # Unpaid, cash basis recognises neither the income nor the discount; paid, both in full.
        assert cash('2026-06-01') == {}
        assert cash('2026-06-03') == {k['income']: -30000, k['given']: 3000}


def test_reports_attribute_income_to_items_and_tax_to_the_agency(client, kinds):
    k = kinds
    post(client, k, [dict(item=k['labor']), dict(item=k['labor'], quantity='2'),
                     dict(item=k['subtotal']), dict(item=k['off_taxed'])])
    report = client.run('report sales-by-item', dict(date_from=DATE, date_to=DATE, customer=k['customer']), company=COMPANY)
    rows = {row['item_id']: (row['income']['minor_units'], row['quantity_microunits']) for row in report['rows']}
    # Sold at 300.00 for three units; the discount item carries its own -30.00 and no quantity.
    assert rows == {k['labor']: (30000, 3_000_000), k['off_taxed']: (-3000, 0)}
    assert report['totals']['income']['minor_units'] == 27000
    liability = client.run('sales-tax liability', dict(as_of=DATE, agency=k['agency']), company=COMPANY)
    assert liability['totals']['tax_charged']['minor_units'] == 2700
    loss = client.run('report profit-and-loss', dict(date_from=DATE, date_to=DATE), company=COMPANY)
    assert 'Kinds discounts given' in str(loss)


@pytest.mark.parametrize('lines,field,words', [
    (lambda k: [dict(item=k['off_taxed']), dict(item=k['labor'])], 'lines.0', 'directly above'),
    (lambda k: [dict(item=k['labor']), dict(item=k['off_taxed']), dict(item=k['five_off'])], 'lines.2', 'put a subtotal'),
    (lambda k: [dict(item=k['labor']), dict(item=k['off_taxed']), dict(item=k['fee'])], 'lines.2', 'put a subtotal'),
    (lambda k: [dict(item=k['labor']), dict(item=k['parts']), dict(item=k['subtotal']), dict(item=k['off_taxed'])],
     'lines.3', 'non-taxable line'),
    (lambda k: [dict(item=k['labor'], net_amount='1.00'), dict(item=k['five_off'])], 'lines.1', 'larger than'),
    (lambda k: [dict(item=k['kit'], unit_price='1.00')], 'lines.0.unit_price', 'group line'),
    (lambda k: [dict(item=k['labor']), dict(item=k['subtotal'], net_amount='1.00')], 'net_amount', 'subtotal'),
    (lambda k: [dict(item=k['labor']), dict(item=k['off_taxed'], quantity='2')], 'quantity', 'no quantity'),
    (lambda k: [dict(item=k['labor'], percent='5')], 'percent', 'only a discount'),
])
def test_refusals_name_the_line_and_write_nothing(client, kinds, lines, field, words):
    before = snapshot(client)
    with pytest.raises(BookflowError) as caught:
        post(client, kinds, lines(kinds))
    problem = caught.value.details['fields'][0]
    assert caught.value.code == 'E_VALIDATION' and problem['field'] == field and words in problem['problem']
    assert snapshot(client) == before


def test_sales_receipt_takes_the_same_lines(client, kinds):
    k = kinds
    bank = client.account.create(name='Kinds bank', type='bank', company=COMPANY)['id']
    result = client.run('sales-receipt post', dict(date=DATE, customer=k['customer'], sales_tax_item=k['tax'],
        sales_tax_calculation='invoice_combined_half_up', payment_method='Check', deposit_to=bank,
        lines=[dict(item=k['labor']), dict(item=k['five_off'])]), company=COMPANY)
    assert shown(result) == [('item', 10000, 9500, 950, [9500]), ('discount', -500, 0, 0, [])]
    assert result['total_minor_units'] == 10450
