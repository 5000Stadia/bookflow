"""R147 on credit memos: the invoice's line rules read backwards, with hand-computed oracles.

A credit memo takes subtotal, discount, percentage-charge and group lines by the same rules as an
invoice; its postings are the invoice's inverted -- income debited at each line's full amount, the
discount credited back to its own account, the receivable credited the net plus tax. A returned
line of a discounted invoice comes back at its net, the amount the customer was charged for it.
"""
import pytest

from bookflow import BookflowError
from tests.test_row8_journal import assert_oracle
from tests.test_sales_line_kinds import kinds, post, shown  # noqa: F401
from tests.test_service_sales_lifecycle import COMPANY, snapshot

DATE = '2026-06-05'


def credit(client, k, lines, **extra):
    return client.run('credit-memo post', dict(customer=k['customer'], date=DATE, sales_tax_item=k['tax'],
                                               sales_tax_calculation='invoice_combined_half_up', lines=lines, **extra),
                      company=COMPANY)


def test_credit_memo_carries_subtotal_discount_and_group_lines(client, kinds):
    k = kinds
    result = credit(client, k, [dict(item=k['labor']), dict(item=k['labor'], quantity='2'), dict(item=k['subtotal']),
                                dict(item=k['off_taxed']), dict(item=k['kit'])])
    # 300.00 subtotal, 30.00 off shared 10/20; the kit is labor 100.00 (taxed) and parts 100.00.
    # Nets 90 + 180 + 100 + 100 = 470.00; tax 10% of 370.00 = 37.00.
    assert shown(result) == [('item', 10000, 9000, 900, [9000]), ('item', 20000, 18000, 1800, [18000]),
                             ('subtotal', 30000, 0, 0, []), ('discount', -3000, 0, 0, []),
                             ('item', 10000, 10000, 1000, [10000]), ('item', 10000, 10000, 0, [])]
    assert result['total_minor_units'] == 50700
    ar = result['revision']['profile']['control_account']['id']
    # Inverted: income back at the full 500.00, the discount credited back to its account.
    assert_oracle(client, result['id'], {(DATE, ar): -50700, (DATE, k['income']): 50000,
                                         (DATE, k['given']): -3000, (DATE, k['liability']): 3700})
    ids = [line['line_id'] for line in result['revision']['lines']]
    items = [line['item_id'] for line in result['revision']['lines']]
    lines = [dict(item=item, line_id=identity) for item, identity in zip(items, ids)]
    lines[1]['quantity'] = '3'
    changed = client.run('credit-memo update', dict(credit_memo=result['id'], expected_version=1, lines=lines),
                         company=COMPANY)
    # 400.00 subtotal, 40.00 off shared 10/30: nets 90, 270, 100, 100; tax 10% of 460.00.
    assert [line['net_minor_units'] for line in changed['revision']['lines']] == [9000, 27000, 0, 0, 10000, 10000]
    assert changed['total_minor_units'] == 56000 + 4600
    client.run('credit-memo void', dict(credit_memo=result['id'], expected_version=2), reason='Entered twice',
               company=COMPANY)
    assert_oracle(client, result['id'], {})


def test_fixed_discount_on_a_credit_credits_its_expense_account(client, kinds):
    k = kinds
    result = credit(client, k, [dict(item=k['labor']), dict(item=k['five_off'])])
    assert shown(result) == [('item', 10000, 9500, 950, [9500]), ('discount', -500, 0, 0, [])]
    ar = result['revision']['profile']['control_account']['id']
    assert_oracle(client, result['id'], {(DATE, ar): -10450, (DATE, k['income']): 10000,
                                         (DATE, k['expense']): -500, (DATE, k['liability']): 950})


def test_a_returned_discounted_line_comes_back_at_its_net(client, kinds):
    k = kinds
    invoice = post(client, k, [dict(item=k['labor']), dict(item=k['labor'], quantity='2'),
                               dict(item=k['subtotal']), dict(item=k['off_taxed'])])
    lines = invoice['revision']['lines']
    returned = client.run('credit-memo post', dict(customer=k['customer'], date=DATE, lines=[
        dict(source_invoice=invoice['id'], source_line=lines[1]['line_id'], quantity='1')]), company=COMPANY)
    # One of two units of a line billed at 200.00 less 20.00: 90.00 net and half its 18.00 tax.
    assert [(line['net_minor_units'], line['tax_minor_units']) for line in returned['revision']['lines']] == [(9000, 900)]
    before = snapshot(client)
    with pytest.raises(BookflowError) as caught:
        client.run('credit-memo post', dict(customer=k['customer'], date=DATE, lines=[
            dict(source_invoice=invoice['id'], source_line=lines[3]['line_id'], quantity='1')]), company=COMPANY)
    assert caught.value.code == 'E_VALIDATION' and 'not returned' in caught.value.details['fields'][0]['problem']
    assert snapshot(client) == before
