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
    billed = run(client, 'work-order', 'invoice', work_order=order['id'], expected_version=order['version'],
                 conversion_key='kinds bill', date='2026-06-03')
    # Billed whole, the invoice is the quote line for line: same amounts, nets and tax.
    assert [(line.get('line_kind', 'item'), (line.get('amount') or line['net'])['minor_units'], line['net_minor_units'],
             line['tax_minor_units']) for line in billed['revision']['lines']] == [row[:4] for row in quote(changed)]
    assert billed['total_minor_units'] == changed['revision']['total']['minor_units']


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
                  [dict(item=k['labor']), dict(item=k['off_taxed']), dict(item=k['five_off'])]):
        with pytest.raises(BookflowError) as caught:
            estimate(client, k, lines)
        assert caught.value.code == 'E_VALIDATION'
    assert snapshot(client) == before


def sale(result):
    return [(line.get('line_kind', 'item'), (line.get('amount') or line['net'])['minor_units'],
             line['net_minor_units'], line['tax_minor_units']) for line in result['revision']['lines']]


BILLED_LINES = lambda k: [dict(item=k['labor']), dict(item=k['labor'], quantity='2'), dict(item=k['subtotal']),
                          dict(item=k['off_taxed']), dict(item=k['labor']), dict(item=k['fee'])]


def accepted(client, k):
    first = estimate(client, k, BILLED_LINES(k))
    return run(client, 'estimate', 'update', estimate=first['id'], expected_version=1, status='accepted',
               decision_note='Customer accepted')


def test_billing_a_quote_whole_posts_the_invoice_the_quote_describes(client, kinds):
    from tests.test_row8_journal import assert_oracle
    k = kinds
    source = accepted(client, k)
    # Quote: 100 + 200, subtotal 300, 10% off shared 10/20; 100; 15% fee on the 100 above it.
    # Nets 90, 180, 0, 0, 100, 15 = 385.00; tax 10% = 38.50.
    billed = run(client, 'estimate', 'invoice', estimate=source['id'], expected_version=source['version'],
                 conversion_key='kinds whole', date='2026-06-03')
    assert sale(billed) == [('item', 10000, 9000, 900), ('item', 20000, 18000, 1800), ('subtotal', 30000, 0, 0),
                            ('discount', -3000, 0, 0), ('item', 10000, 10000, 1000), ('charge', 1500, 1500, 150)]
    ar = billed['revision']['profile']['control_account']['id']
    assert_oracle(client, billed['id'], {('2026-06-03', ar): 42350, ('2026-06-03', k['income']): -40000,
                                         ('2026-06-03', k['fees']): -1500, ('2026-06-03', k['given']): 3000,
                                         ('2026-06-03', k['liability']): -3850})
    assert len(billed['revision']['billing_sources']) == 4  # the four sold lines; subtotal and discount come along
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'invoice', estimate=source['id'], expected_version=source['version'] + 1,
            conversion_key='kinds again', date='2026-06-04', line_ids=[source['revision']['lines'][3]['line_id']])
    assert caught.value.code == 'E_WORK_DEPENDENCY'


def test_progress_billing_bills_each_line_and_its_discount_share_by_the_same_fraction(client, kinds):
    from tests.test_row8_journal import assert_oracle
    k = kinds
    client.run('company update', dict(progress_billing_enabled=True), company='Demo Plumbing Co')
    source = accepted(client, k)
    first = run(client, 'estimate', 'invoice', estimate=source['id'], expected_version=source['version'],
                conversion_key='kinds half', date='2026-06-03', percent='50')
    # Half of every line: nets 45, 90, 50, 7.50; the discount's shares 5 and 10 come along.
    assert sale(first) == [('item', 5000, 4500, 450), ('item', 10000, 9000, 900), ('subtotal', 15000, 0, 0),
                           ('discount', -1500, 0, 0), ('item', 5000, 5000, 500), ('charge', 750, 750, 75)]
    assert first['total_minor_units'] == 21175
    ar = first['revision']['profile']['control_account']['id']
    assert_oracle(client, first['id'], {('2026-06-03', ar): 21175, ('2026-06-03', k['income']): -20000,
                                        ('2026-06-03', k['fees']): -750, ('2026-06-03', k['given']): 1500,
                                        ('2026-06-03', k['liability']): -1925})
    current = run(client, 'estimate', 'show', estimate=source['id'])
    rest = run(client, 'estimate', 'invoice', estimate=source['id'], expected_version=current['version'],
               conversion_key='kinds rest', date='2026-06-04')
    assert sale(rest) == sale(first) and first['total_minor_units'] + rest['total_minor_units'] == 42350
    # A correction of a billed invoice keeps the billed discount on its lines, wherever they move.
    lines = [dict(item=line['item_id'], line_id=line['line_id']) for line in first['revision']['lines']]
    moved = run(client, 'invoice', 'update', invoice=first['id'], expected_version=1,
                lines=[lines[1], lines[0], *lines[2:], dict(item=k['parts'])])
    assert sale(moved)[:6] == [sale(first)[1], sale(first)[0], *sale(first)[2:]]
    assert moved['total_minor_units'] == 21175 + 5000  # the untaxed part added at 50.00
    voided = run(client, 'invoice', 'void', invoice=first['id'], expected_version=2)
    assert voided['status'] == 'voided'
    again = run(client, 'estimate', 'invoice', estimate=source['id'],
                expected_version=run(client, 'estimate', 'show', estimate=source['id'])['version'],
                conversion_key='kinds rebill', date='2026-06-05')
    assert sale(again) == sale(first)


def test_resaving_a_quote_does_not_take_a_discount_off_an_entered_amount_twice(client, kinds):
    k = kinds
    first = estimate(client, k, [dict(item=k['labor'], net_amount='1.00'), dict(item=k['subtotal']),
                                 dict(item=k['off_taxed'])])
    assert quote(first)[0][:3] == ('item', 100, 90)
    again = run(client, 'estimate', 'update', estimate=first['id'], expected_version=1, memo='Resaved')
    assert quote(again) == quote(first)
    accepted = run(client, 'estimate', 'update', estimate=first['id'], expected_version=2, status='accepted',
                   decision_note='Accepted')
    billed = run(client, 'estimate', 'invoice', estimate=first['id'], expected_version=accepted['version'],
                 conversion_key='resaved bill', date='2026-06-02')
    assert sale(billed) == [('item', 100, 90, 9), ('subtotal', 100, 0, 0), ('discount', -10, 0, 0)]
