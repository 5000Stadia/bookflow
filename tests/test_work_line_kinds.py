"""R147 on quotes: estimates and work orders carry subtotal, discount, charge and group lines.

Hand-computed oracles, as in tests/test_sales_line_kinds.py. A quote posts nothing; what is
witnessed is the quoted nets, taxable bases and tax, their survival through correction and
conversion to a work order, and the plain refusal to bill such lines until billing carries them.
"""
import pytest

from bookflow import BookflowError
from tests.test_customer_work_lifecycle import run
from tests.test_sales_line_kinds import kinds  # noqa: F401
from tests.test_service_sales_lifecycle import COMPANY, snapshot


def quote(result):
    return [(line.get('line_kind', 'item'), (line.get('amount') or line['net'])['minor_units'],
             line['net']['minor_units'], line['tax']['minor_units'],
             [cell['taxable_minor_units'] for cell in line['facts']['taxes']])
            for line in result['revision']['lines']]


def estimate(client, k, lines, **extra):
    return run(client, 'estimate', 'create', date='2026-06-01', title='Kinds quote', customer=k['customer'],
               sales_tax_item=k['tax'], sales_tax_calculation='invoice_combined_half_up', lines=lines, **extra)


def test_quote_discount_charge_and_group(client, kinds):
    k = kinds
    first = estimate(client, k, [dict(item=k['labor']), dict(item=k['labor'], quantity='2'), dict(item=k['subtotal']),
                                 dict(item=k['off_taxed']), dict(item=k['kit']), dict(item=k['fee'])])
    # 300.00 subtotal, 30.00 off shared 10/20; kit = labor 100.00 + parts 100.00 (untaxed);
    # the fee is 15% of the parts line directly above it: 15.00, taxable.
    assert quote(first) == [('item', 10000, 9000, 900, [9000]), ('item', 20000, 18000, 1800, [18000]),
                            ('subtotal', 30000, 0, 0, []), ('discount', -3000, 0, 0, []),
                            ('item', 10000, 10000, 1000, [10000]), ('item', 10000, 10000, 0, []),
                            ('charge', 1500, 1500, 150, [1500])]
    revision = first['revision']
    assert (revision['net']['minor_units'], revision['tax']['minor_units']) == (48500, 3850)
    assert revision['lines'][4]['facts']['profile']['group']['item']['id'] == k['kit']
    ids = [line['line_id'] for line in revision['lines']]
    items = [line['facts']['item_id'] for line in revision['lines']]
    lines = [dict(item=item, line_id=identity) for item, identity in zip(items, ids)]
    lines[1]['quantity'] = '3'
    changed = run(client, 'estimate', 'update', estimate=first['id'], expected_version=1, lines=lines)
    # 400.00 subtotal, 40.00 off shared 10/30.
    assert quote(changed)[:4] == [('item', 10000, 9000, 900, [9000]), ('item', 30000, 27000, 2700, [27000]),
                                  ('subtotal', 40000, 0, 0, []), ('discount', -4000, 0, 0, [])]
    before = snapshot(client)
    noop = run(client, 'estimate', 'update', estimate=first['id'], expected_version=2)
    assert not noop.get('changed', True) or snapshot(client) == before
    accepted = run(client, 'estimate', 'update', estimate=first['id'], expected_version=2, status='accepted',
                   decision_note='Customer accepted')
    order = run(client, 'estimate', 'work-order', estimate=first['id'], expected_version=accepted['version'],
                conversion_key='kinds order', date='2026-06-02')
    assert quote(order) == quote(changed)
    with pytest.raises(BookflowError) as caught:
        run(client, 'work-order', 'invoice', work_order=order['id'], expected_version=order['version'],
            conversion_key='kinds bill', date='2026-06-03')
    assert 'not supported yet' in caught.value.details['fields'][0]['problem']


def test_quote_non_taxable_discount_keeps_the_taxable_base(client, kinds):
    k = kinds
    result = estimate(client, k, [dict(item=k['labor']), dict(item=k['off_untaxed'])])
    assert quote(result) == [('item', 10000, 9000, 1000, [10000]), ('discount', -1000, 0, 0, [])]
    facts = result['revision']['lines'][0]['facts']
    assert (facts['discount_minor_units'], facts['taxable_minor_units']) == (1000, 10000)


def test_quote_refusals_write_nothing(client, kinds):
    k = kinds
    before = snapshot(client)
    for lines in ([dict(item=k['off_taxed'])],
                  [dict(item=k['labor']), dict(item=k['parts']), dict(item=k['subtotal']), dict(item=k['off_taxed'])]):
        with pytest.raises(BookflowError) as caught:
            estimate(client, k, lines)
        assert caught.value.code == 'E_VALIDATION'
    assert snapshot(client) == before
