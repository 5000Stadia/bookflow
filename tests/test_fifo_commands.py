"""R104 increment 7: FIFO costing through the public commands (design/advanced-inventory.md §3, §9 increment 7).

The hand-worked example of the design, entered as paperwork: three bills at different costs, two invoices, a
credit memo returning part of the first, a vendor credit naming its receipt line and one naming none, a freight
bill spread onto the second receipt, a count, and a bill backdated before the first sale. Then the switch itself:
dated after the closing date it corrects only dates on or after it and revalues nothing; switching back continues
at the average; an agent is refused. Throughout, the asset account equals the valuation, placements equal the stock
ledger, and the journal balances.
"""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_sites import _db, _part, _tie_every_date
from tests.test_landed_cost import _asset


@pytest.fixture
def shop(books):
    run = books['run']
    clearing = run('landed-cost setup', {}, reason='Freight on stock')['account_id']
    widget = _part(books, 'Widget', cost='10.00')
    acme = books['client'].customer.create(company=books['company'], name='Acme')['id']
    return dict(books=books, run=run, clearing=clearing, widget=widget, acme=acme)


def _buy(shop, date, quantity, cost, item=None):
    return shop['run']('bill post', dict(vendor=shop['books']['vendor'], date=date, items=[dict(
        item=item or shop['widget'], quantity=str(quantity), unit_cost=cost)]), reason='Stock in')


def _sell(shop, date, quantity, item='Widget'):
    return shop['run']('invoice post', dict(customer=shop['acme'], date=date, lines=[dict(
        item=item, quantity=str(quantity), unit_price='50.00')]), reason='Job')


def _value(shop, as_of='2017-12-31', item='Widget'):
    rows = shop['run']('report inventory-valuation', dict(as_of=as_of))['rows']
    return next((row['asset_value']['minor_units'] for row in rows if row['item_name'] == item), 0)


def _ties(shop):
    books = shop['books']
    _tie_every_date(books)
    with sqlite3.connect(_db(books)) as conn:
        net = {account: int(value) for account, value in conn.execute(
            'SELECT account_id, sum(debit_minor_units - credit_minor_units) FROM posting_lines GROUP BY account_id')}
    valuation = shop['run']('report inventory-valuation', dict(as_of='2017-12-31'))
    assert valuation['totals']['asset_value']['minor_units'] == net.get(_asset(books), 0)
    assert sum(net.values()) == 0


def _cogs(shop):
    return _net(shop['books']).get(shop['books']['cogs'], 0)


def _receipt_line(shop, bill_id):
    bill = shop['run']('bill show', dict(bill=bill_id))
    return bill['revision']['items'][0]['line_id']


def _layers(shop, as_of='2017-12-31'):
    report = shop['run']('report inventory-layers', dict(as_of=as_of))
    return [(row['quantity'], row['value']['minor_units']) for row in report['rows'] if row['item_name'] == 'Widget']


def test_the_hand_worked_fifo_example_matches_to_the_cent(shop):
    run = shop['run']
    switch = run('costing-method set', dict(method='fifo', effective_from='2017-01-01'),
                 reason="FIFO from the new year, on our accountant's advice")
    assert switch['switch']['method'] == 'fifo' and switch['entries'] == []
    _buy(shop, '2017-01-02', 10, '10.00')
    r2 = _buy(shop, '2017-01-05', 10, '12.00')['id']
    r3 = _buy(shop, '2017-01-10', 10, '15.00')['id']
    s1 = _sell(shop, '2017-01-12', 15)
    assert _cogs(shop) == 16000                          # R1 100.00 + 5 of R2 60.00
    run('credit-memo post', dict(customer=shop['acme'], date='2017-01-13', lines=[dict(
        quantity='3', source_invoice=s1['id'],
        source_line=s1['revision']['lines'][0]['line_id'])]),
        reason='Three came back')
    assert _cogs(shop) == 16000 - 3200                   # floor(160.00 * 3/15)
    run('vendor-credit post', dict(vendor=shop['books']['vendor'], date='2017-01-14', ap_account=shop['books']['payable'],
                                   items=[dict(item=shop['widget'], quantity='4', unit_cost='15.00',
                                               receipt_line=_receipt_line(shop, r3))]), reason='Four back to R3')
    run('vendor-credit post', dict(vendor=shop['books']['vendor'], date='2017-01-15', ap_account=shop['books']['payable'],
                                   items=[dict(item=shop['widget'], quantity='2', unit_cost='11.00')]),
        reason='Two back, no receipt named')
    freight = run('bill post', dict(vendor=shop['books']['vendor'], date='2017-01-16', expenses=[dict(
        account=shop['clearing'], amount='20.00')]), reason='Freight')['id']
    run('landed-cost post', dict(charges=[dict(document=freight)], targets=[dict(document=r2)]), reason='Freight on R2')
    # Sold before the allocation: 5 of R2's 10 units owe 10.00 forward; the return gives back 2.00 of it.
    assert _cogs(shop) == 16000 - 3200 + 1000 - 200
    count = run('inventory-count create', dict(site='Main', date='2017-01-20'), reason='Count')
    run('inventory-count enter', dict(count=count['number'], entries=[dict(item='Widget', quantity='10')]),
        reason='Counted')
    fresh = run('inventory-count show', dict(count=count['number']))
    post = dict(count=count['number'], expected_version=fresh['version'], adjustment_account=shop['books']['cogs'])
    preview = run('inventory-count post', post, reason='Count', dry_run=True)
    run('inventory-count post', dict(post, preview_fingerprint=preview['preview_fingerprint']), reason='Count')
    _sell(shop, '2017-01-22', 6)
    _ties(shop)
    # Without the backdated bill: K1 2 = 29.00, S2 6 = 92.00; 4 left worth 51.00 (R3 1 = 15.50, C1 3 = 35.50).
    assert _value(shop) == 5100
    assert _layers(shop) == [('1', 1550), ('3', 3550)]
    # The bill backdated to 3 January: 5 at 8.00, between R1 and R2.
    _buy(shop, '2017-01-03', 5, '8.00')
    _ties(shop)
    # The count of 10 now finds 17 on the books: it takes 7 of R2 (8 / 114.82) = 100.47, and S2 the last of R2
    # (14.35) and 5 of R3's 6 / 92.12 = 76.77, so 4 are left: R3 1 = 15.35, C1 3 = 29.06.
    assert _value(shop) == 4441
    assert _layers(shop) == [('1', 1535), ('3', 2906)]


def test_a_switch_after_the_close_corrects_only_dates_on_or_after_it_and_revalues_nothing(shop):
    run = shop['run']
    _buy(shop, '2017-01-02', 10, '10.00')
    _buy(shop, '2017-01-05', 10, '20.00')
    _sell(shop, '2017-01-10', 5)                       # average 15.00: 75.00
    _sell(shop, '2017-02-10', 5)                       # 75.00
    run('company update', dict(closing_date='2017-01-31'), reason='January closed')
    before = (_value(shop, '2017-01-31'), _value(shop, '2017-02-01'), _cogs(shop))
    with pytest.raises(BookflowError) as refused:
        run('costing-method set', dict(method='fifo', effective_from='2017-01-31'), reason='Too early')
    assert refused.value.code == 'E_PERIOD_CLOSED'
    preview = run('costing-method set', dict(method='fifo', effective_from='2017-02-01'), reason='FIFO', dry_run=True)
    # On 1 February 10 units worth 150.00 become one opening layer at 15.00: the February sale takes 75.00 either way.
    assert preview['entries'] == [] and preview['stock_value_change_minor_units'] == 0
    run('costing-method set', dict(method='fifo', effective_from='2017-02-01'), reason='FIFO')
    assert (_value(shop, '2017-01-31'), _value(shop, '2017-02-01'), _cogs(shop)) == before
    # A receipt dated after the switch makes a difference only after it, and only by FIFO.
    _buy(shop, '2017-02-05', 10, '30.00')
    _sell(shop, '2017-02-20', 5)                       # FIFO: the opening layer's last 5 at 15.00
    assert _cogs(shop) == 7500 * 3
    _ties(shop)


def test_a_switch_dated_before_posted_sales_corrects_them_at_their_own_dates(shop):
    run = shop['run']
    _buy(shop, '2017-01-02', 10, '10.00')
    _buy(shop, '2017-02-02', 10, '20.00')
    _sell(shop, '2017-02-10', 10)                      # average 15.00: 150.00
    preview = run('costing-method set', dict(method='fifo', effective_from='2017-02-01'), reason='FIFO', dry_run=True)
    # FIFO: the sale takes the opening layer's 10 at 10.00 = 100.00, 50.00 less cost of goods sold.
    assert [(e['date'], e['amount_minor_units']) for e in preview['entries']] == [('2017-02-10', 5000)]
    assert preview['cost_of_goods_sold_change_minor_units'] == -5000
    assert preview['items'][0]['stock_value_change_minor_units'] == 5000
    run('costing-method set', dict(method='fifo', effective_from='2017-02-01'), reason='FIFO')
    assert _cogs(shop) == 10000 and _value(shop) == 20000
    _ties(shop)
    # Switching back from March continues at the average of what is on hand then.
    run('costing-method set', dict(method='average', effective_from='2017-03-01'), reason='Back to average')
    _buy(shop, '2017-03-02', 10, '30.00')
    _sell(shop, '2017-03-03', 10)                      # average of 20 worth 500.00: 250.00
    assert _cogs(shop) == 10000 + 25000
    shown = run('costing-method show', {})
    assert [row['method'] for row in shown['switches']] == ['fifo', 'average']
    _ties(shop)


def test_withdrawing_a_switch_whose_date_has_not_come(shop):
    run = shop['run']
    run('costing-method set', dict(method='fifo', effective_from='2099-01-01'), reason='Later')
    assert run('costing-method show', {})['method_today'] == 'average'
    run('costing-method withdraw', dict(switch='2099-01-01'), reason='Not after all')
    assert [row['status'] for row in run('costing-method show', {})['switches']] == ['withdrawn']
    with pytest.raises(BookflowError):
        run('costing-method withdraw', dict(switch='2017-01-01'), reason='Nothing there')


from tests.test_row3_host import hosted  # noqa: F401,E402 - fixture


def test_an_agent_is_refused_costing_method_set_and_withdraw_and_a_person_is_not(hosted):
    from tests.test_people_only import REASON, _agent
    _, as_agent = _agent(hosted)
    cid = hosted.company_id
    switch = {'method': 'fifo', 'effective_from': '2099-01-01'}
    r = hosted.call('costing-method.set', switch, company=cid, headers=as_agent)
    assert r.status_code == 403, r.text
    body = r.json()
    assert body['code'] == 'E_PERMISSION' and body['details']['reason'] == 'people_only'
    assert "Changing the costing method is a person's act" in body['message']
    hosted.ok('costing-method.set', switch, company=cid, headers=REASON)
    r = hosted.call('costing-method.withdraw', {'switch': '2099-01-01'}, company=cid, headers=as_agent)
    assert r.status_code == 403 and r.json()['details']['reason'] == 'people_only', r.text
    bearer = {k: v for k, v in as_agent.items() if k == 'Authorization'}
    shown = hosted.ok('costing-method.show', {}, company=cid, headers=bearer)    # reads stay allowed
    assert [row['status'] for row in shown['switches']] == ['standing']
