"""A purchase order, entered as a bill, and the books checked by hand.

Every figure asserted here is written out in full in the module, so a reader can add it up
without running anything:

    PO-1, vendor Northside Supply, ordered 2026-06-01, expected 2026-06-15.
      1. item Copper Pipe, 40 at 12.50                                 500.00
      2. account Job Supplies, "Fittings", a lump sum                   75.00
      3. account Freight In, "Delivery", 1 at 42.00                     42.00
                                                                       ------
                                                                       617.00

    500.00 + 75.00 + 42.00 = 617.00, and 50000 + 7500 + 4200 = 61700 minor units.

The order posts nothing, which is asserted by reading the trial balance through the real
report command before and after and comparing the rows and the totals verbatim. Entering the
bill from it then moves Accounts Payable by exactly 617.00 and nothing else: Job Supplies takes
500.00 + 75.00 = 575.00 because lines 1 and 2 share an account, and Freight In takes 42.00.
"""
import json
from copy import deepcopy

import pytest

import bookflow

ORDERED = '617.00'
ORDERED_UNITS = 61700
PIPE_UNITS, FITTINGS_UNITS, FREIGHT_UNITS = 50000, 7500, 4200
SUPPLIES_UNITS = PIPE_UNITS + FITTINGS_UNITS  # 57500: two ordered lines, one account


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    data_root = tmp_path / 'orders'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Orders organization')
    company = client.company.new(legal_name='Orders', home_currency='USD', timezone='UTC',
                                 organization='Orders organization', chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    payable = next(row['id'] for row in accounts if row['type'] == 'accounts_payable')
    supplies = client.account.create(company=company, name='Job Supplies', type='expense')['id']
    freight = client.account.create(company=company, name='Freight In', type='expense')['id']
    vendor = client.vendor.create(company=company, name='Northside Supply')['id']
    other = client.vendor.create(company=company, name='Corner Hardware')['id']
    customer = client.customer.create(company=company, name='Ada Client')['id']
    pipe = client.run('item create', dict(
        name='Copper Pipe', type='non_inventory_part', sales_enabled=False, purchase_enabled=True,
        purchase_description='3/4 inch copper pipe', expense_account_id=supplies, cost='12.50'),
        company=company)['id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, payable=payable, supplies=supplies, freight=freight,
                vendor=vendor, other=other, customer=customer, pipe=pipe, run=run)


def _order(books, **extra):
    return books['run']('purchase-order post', dict(
        vendor=books['vendor'], date='2026-06-01', expected_date='2026-06-15', number='PO-1',
        reference='QUOTE-88', memo='June restock', ship_to='12 Yard Road',
        lines=[{'item': books['pipe'], 'quantity': '40', 'rate': '12.50'},
               {'account': books['supplies'], 'description': 'Fittings', 'amount': '75.00'},
               {'account': books['freight'], 'description': 'Delivery',
                'quantity': '1', 'rate': '42.00'}],
        **extra), reason='Order June stock')


def _trial_balance(books):
    """The books themselves, through the real report, with the two fields that always move cut.

    ``generation_time`` is when the report ran and ``audit_watermark`` is how many audited
    writes the company has seen; both differ between any two calls that straddle any write at
    all. Everything else is the accounting, and that is what is compared verbatim.
    """
    report = books['run']('report trial-balance',
                          {'date_to': '2026-12-31', 'limit': 200, 'include_zero': True})
    metadata = {key: value for key, value in report['metadata'].items()
                if key not in ('generation_time', 'audit_watermark')}
    return json.dumps(dict(report, metadata=metadata), sort_keys=True, default=str)


def _watermark(books):
    return books['run']('report trial-balance',
                        {'date_to': '2026-12-31', 'limit': 1})['metadata']['audit_watermark']


def _net(books):
    """Signed minor units per account, debits positive, from the ledger's own posting rows."""
    rows = books['run']('report general-ledger',
                        {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 200})['rows']
    net = {}
    for line in rows:
        if line['kind'] != 'posting':
            continue
        net[line['account_id']] = net.get(line['account_id'], 0) + (
            line['debit']['minor_units'] - line['credit']['minor_units'])
    return {account: units for account, units in net.items() if units}


def test_a_purchase_order_records_what_was_ordered_and_posts_nothing(books):
    before, watermark = _trial_balance(books), _watermark(books)
    order = _order(books)

    assert order['number'] == 'PO-1' and order['status'] == 'open'
    assert order['total_minor_units'] == ORDERED_UNITS and order['total']['amount'] == ORDERED
    assert order['expected_date'] == '2026-06-15' and order['reference'] == 'QUOTE-88'
    assert order['consumed'] is False and order['bill_id'] is None

    lines = order['revision']['lines']
    assert [line['position'] for line in lines] == [1, 2, 3]
    assert [line['amount_minor_units'] for line in lines] == [PIPE_UNITS, FITTINGS_UNITS, FREIGHT_UNITS]
    # An item line captures the item, its quantity and its rate, and takes the item's own
    # purchase description; an account line names the account outright.
    assert lines[0]['item_id'] == books['pipe'] and lines[0]['account_id'] is None
    assert lines[0]['quantity'] == '40' and lines[0]['rate']['amount'] == '12.50'
    assert lines[0]['description'] == '3/4 inch copper pipe'
    assert lines[0]['line_snapshot']['item']['account']['id'] == books['supplies']
    assert lines[1]['account_id'] == books['supplies'] and lines[1]['item_id'] is None
    assert lines[1]['quantity'] is None and lines[1]['rate'] is None
    assert lines[2]['quantity'] == '1' and lines[2]['amount_minor_units'] == FREIGHT_UNITS

    # Nothing posted: the trial balance is the same report it was, line for line.
    assert _trial_balance(books) == before
    assert _net(books) == {}
    # One audited write did happen, which is what makes the identical report meaningful: the
    # company's audit watermark moved by exactly one and the books did not move at all.
    assert _watermark(books) == watermark + 1


def test_the_ordered_total_is_the_sum_of_the_lines_and_each_line_is_quantity_times_rate(books):
    order = _order(books)
    assert PIPE_UNITS + FITTINGS_UNITS + FREIGHT_UNITS == ORDERED_UNITS == order['total_minor_units']
    # A stated amount that contradicts the arithmetic is refused rather than trusted.
    with pytest.raises(bookflow.BookflowError) as caught:
        books['run']('purchase-order post', dict(
            vendor=books['vendor'], date='2026-06-02', number='PO-BAD',
            lines=[{'account': books['supplies'], 'quantity': '4', 'rate': '10.00',
                    'amount': '41.00'}]), reason='Order')
    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details['fields'][0]['field'] == 'lines.0.amount'
    assert '4000' in caught.value.details['fields'][0]['problem']


def test_an_ordered_line_is_an_item_or_an_account_and_never_both(books):
    for supplied, problem in (({}, 'exactly one of item or account'),
                              ({'item': 'Copper Pipe', 'account': 'Job Supplies'},
                               'exactly one of item or account')):
        with pytest.raises(bookflow.BookflowError) as caught:
            books['run']('purchase-order post', dict(
                vendor=books['vendor'], date='2026-06-02', number='PO-BAD',
                lines=[{'amount': '10.00', **supplied}]), reason='Order')
        assert caught.value.code == 'E_VALIDATION', supplied
        assert problem in json.dumps(caught.value.details)


def test_an_ordered_line_cannot_name_an_account_no_purchase_can_go_to(books):
    with pytest.raises(bookflow.BookflowError) as caught:
        books['run']('purchase-order post', dict(
            vendor=books['vendor'], date='2026-06-02', number='PO-BAD',
            lines=[{'account': books['payable'], 'amount': '10.00'}]), reason='Order')
    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details['fields'][0]['field'] == 'lines.0.account'


def test_an_ordered_cost_names_the_job_it_will_be_passed_on_to(books):
    order = books['run']('purchase-order post', dict(
        vendor=books['vendor'], date='2026-06-01', number='PO-JOB',
        lines=[{'account': books['supplies'], 'amount': '10.00',
                'customer': books['customer'], 'billable': True}]), reason='Order for a job')
    line = order['revision']['lines'][0]
    assert line['customer_id'] == books['customer'] and line['billable'] is True
    with pytest.raises(bookflow.BookflowError) as caught:
        books['run']('purchase-order post', dict(
            vendor=books['vendor'], date='2026-06-01', number='PO-JOB2',
            lines=[{'account': books['supplies'], 'amount': '10.00', 'billable': True}]),
            reason='Order')
    assert caught.value.code == 'E_VALIDATION'
    assert 'billable requires a customer' in json.dumps(caught.value.details)


def test_the_state_moves_from_open_through_partly_received_to_closed_and_back(books):
    order = _order(books)
    assert order['status'] == 'open'
    partly = books['run']('purchase-order update', {
        'purchase_order': order['id'], 'status': 'partly_received',
        'expected_version': order['version']}, reason='Half of it arrived')
    assert partly['status'] == 'partly_received' and partly['changed_fields'] == ['status']
    assert partly['revision']['revision_number'] == 2
    assert partly['revision']['status'] == 'partly_received'

    closed = books['run']('purchase-order update', {
        'purchase_order': order['id'], 'status': 'closed',
        'expected_version': partly['version']}, reason='The rest is not coming')
    assert closed['status'] == 'closed'
    # Closing is a decision, not an accounting event, and it is reversible.
    reopened = books['run']('purchase-order update', {
        'purchase_order': order['id'], 'status': 'open',
        'expected_version': closed['version']}, reason='The rest is coming after all')
    assert reopened['status'] == 'open'
    assert [row['status'] for row in books['run'](
        'purchase-order history', {'purchase_order': order['id'], 'limit': 10})['items']] == [
        'open', 'partly_received', 'closed', 'open']
    # Four revisions and still nothing in the ledger.
    assert _net(books) == {}


def test_correcting_an_order_keeps_every_earlier_revision_readable(books):
    order = _order(books)
    kept = order['revision']['lines'][0]['line_id']
    corrected = books['run']('purchase-order update', {
        'purchase_order': order['id'], 'expected_version': order['version'],
        'lines': [{'line_id': kept, 'item': books['pipe'], 'quantity': '50', 'rate': '12.50'}]},
        reason='The vendor requoted')
    assert corrected['total_minor_units'] == 62500  # 50 at 12.50
    assert len(corrected['revision']['lines']) == 1
    assert corrected['revision']['lines'][0]['line_id'] == kept
    first = books['run']('purchase-order show', {'purchase_order': order['id'], 'revision_number': 1})
    assert first['revision']['total_minor_units'] == ORDERED_UNITS
    assert len(first['revision']['lines']) == 3
    # A header-only correction keeps the lines exactly as they were captured.
    memo = books['run']('purchase-order update', {
        'purchase_order': order['id'], 'expected_version': corrected['version'],
        'memo': 'Rush it'}, reason='Note the urgency')
    assert memo['memo'] == 'Rush it' and memo['total_minor_units'] == 62500
    assert len(memo['revision']['lines']) == 1
    unchanged = books['run']('purchase-order update', {
        'purchase_order': order['id'], 'expected_version': memo['version'],
        'memo': 'Rush it'}, reason='Say it again')
    assert unchanged['changed'] is False


def test_a_stale_correction_says_who_changed_the_order_and_what_they_changed(books):
    order = _order(books)
    books['run']('purchase-order update', {'purchase_order': order['id'], 'memo': 'Rush it',
                                           'expected_version': order['version']}, reason='Note it')
    with pytest.raises(bookflow.BookflowError) as caught:
        books['run']('purchase-order update', {'purchase_order': order['id'], 'memo': 'Other',
                                               'expected_version': order['version']}, reason='Again')
    assert caught.value.code == 'E_VERSION_CONFLICT'
    assert caught.value.details['changed_fields'] == ['memo']
    assert 'memo' in caught.value.message


def test_a_number_is_unique_across_purchase_orders_and_allocated_when_not_given(books):
    _order(books)
    with pytest.raises(bookflow.BookflowError) as caught:
        books['run']('purchase-order post', dict(
            vendor=books['vendor'], date='2026-06-02', number='PO-1',
            lines=[{'account': books['supplies'], 'amount': '1.00'}]), reason='Order')
    assert caught.value.code == 'E_DUPLICATE_NUMBER'
    allocated = books['run']('purchase-order post', dict(
        vendor=books['vendor'], date='2026-06-02',
        lines=[{'account': books['supplies'], 'amount': '1.00'}]), reason='Order')
    assert allocated['number'] == '1'


def test_query_pages_both_ways_and_narrows_to_what_can_still_be_billed(books):
    first = _order(books)
    second = books['run']('purchase-order post', dict(
        vendor=books['other'], date='2026-07-01', number='PO-2',
        lines=[{'account': books['supplies'], 'amount': '9.00'}]), reason='Order')
    page = books['run']('purchase-order query', {'limit': 10})
    assert [item['number'] for item in page['items']] == ['PO-1', 'PO-2']
    assert page['items'][0]['total']['amount'] == ORDERED and page['items'][0]['consumed'] is False
    assert [item['number'] for item in books['run'](
        'purchase-order query', {'limit': 10, 'direction': 'desc'})['items']] == ['PO-2', 'PO-1']
    assert [item['number'] for item in books['run'](
        'purchase-order query', {'limit': 10, 'vendor': books['other']})['items']] == ['PO-2']
    assert [item['number'] for item in books['run'](
        'purchase-order query', {'limit': 10, 'expected_from': '2026-06-10'})['items']] == ['PO-1']
    books['run']('purchase-order void', {'purchase_order': second['id'],
                                         'expected_version': second['version']},
                 reason='Ordered twice by mistake')
    assert [item['number'] for item in books['run'](
        'purchase-order query', {'limit': 10, 'open_only': True})['items']] == ['PO-1']
    assert first['id'] != second['id']


def test_a_withdrawn_order_stays_readable_and_can_never_become_a_bill(books):
    order = _order(books)
    voided = books['run']('purchase-order void', {'purchase_order': order['id'],
                                                  'expected_version': order['version']},
                          reason='The vendor cannot supply it')
    assert voided['status'] == 'voided'
    assert voided['void_reason'] == 'The vendor cannot supply it'
    assert voided['total_minor_units'] == ORDERED_UNITS
    # Readable in full, every line and captured fact.
    shown = books['run']('purchase-order show', {'purchase_order': order['id']})
    assert len(shown['revision']['lines']) == 3
    assert shown['revision']['total_minor_units'] == ORDERED_UNITS
    # Terminal, and nothing was reversed because nothing was posted.
    assert _net(books) == {}
    with pytest.raises(bookflow.BookflowError) as caught:
        books['run']('purchase-order update', {'purchase_order': order['id'], 'memo': 'x',
                                               'expected_version': voided['version']},
                     reason='Correct it')
    assert caught.value.code == 'E_VALIDATION'
    with pytest.raises(bookflow.BookflowError) as caught:
        books['run']('bill post', dict(date='2026-06-20', purchase_order=order['id']),
                     reason='Enter the bill')
    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details['fields'][0]['field'] == 'purchase_order'
    assert 'voided' in caught.value.details['fields'][0]['problem']
    # Voiding twice is the state it is already in, not a second withdrawal.
    again = books['run']('purchase-order void', {'purchase_order': order['id']}, reason='Again')
    assert again['changed'] is False


def test_a_bill_entered_from_an_order_carries_its_lines_and_raises_the_payable_by_the_total(books):
    order = _order(books)
    before = _trial_balance(books)
    bill = books['run']('bill post', dict(date='2026-06-16', purchase_order=order['id'],
                                          supplier_reference='INV-5501'),
                        reason='Enter the June bill')

    assert bill['purchase_order_id'] == order['id']
    assert bill['vendor_id'] == books['vendor']
    assert bill['total_minor_units'] == ORDERED_UNITS and bill['total']['amount'] == ORDERED
    assert bill['memo'] == 'June restock'
    # An ordered item becomes an item line, which is the only line that can name the item whose
    # quantity has to move; an ordered account line is still an expense. The two grids together
    # are the order, which is why the total above is the ordered total.
    expenses = bill['revision']['expenses']
    assert [line['amount_minor_units'] for line in expenses] == [FITTINGS_UNITS, FREIGHT_UNITS]
    assert [line['account_id'] for line in expenses] == [books['supplies'], books['freight']]
    assert [line['memo'] for line in expenses] == ['Fittings', 'Delivery']
    items = bill['revision']['items']
    assert [line['amount_minor_units'] for line in items] == [PIPE_UNITS]
    assert [line['account_id'] for line in items] == [books['supplies']]
    assert [line['description'] for line in items] == ['3/4 inch copper pipe']
    assert items[0]['quantity'] == '40'

    # Accounts Payable rose by exactly the bill total and by nothing else.
    assert _net(books) == {books['payable']: -ORDERED_UNITS,
                           books['supplies']: SUPPLIES_UNITS,
                           books['freight']: FREIGHT_UNITS}
    assert SUPPLIES_UNITS + FREIGHT_UNITS == ORDERED_UNITS
    assert _trial_balance(books) != before  # the bill did post; the order had not

    consumed = books['run']('purchase-order show', {'purchase_order': order['id']})
    assert consumed['consumed'] is True and consumed['bill_id'] == bill['id']
    assert consumed['status'] == 'closed'
    assert consumed['conversion']['destination_transaction_id'] == bill['id']
    assert consumed['conversion']['source_version'] == order['version']
    assert consumed['conversion']['destination_type'] == 'bill'
    # One command, one audit event, both documents in it.
    event = books['run']('audit list', {'limit': 1})['items'][0]
    assert event['command'] == 'bill post'
    kinds = {entry['record_type']
             for entry in books['run']('audit show', {'event': event['id']})['entries']}
    assert {'transaction', 'purchase_order', 'purchase_order_conversion'} <= kinds


def test_an_order_becomes_at_most_one_bill_and_a_consumed_order_cannot_be_touched(books):
    order = _order(books)
    bill = books['run']('bill post', dict(date='2026-06-16', purchase_order=order['id']),
                        reason='Enter the June bill')
    for command, raw in (('bill post', dict(date='2026-06-17', purchase_order=order['id'])),
                         ('purchase-order update', {'purchase_order': order['id'], 'memo': 'x'}),
                         ('purchase-order void', {'purchase_order': order['id']})):
        with pytest.raises(bookflow.BookflowError) as caught:
            books['run'](command, raw, reason='Try it')
        assert caught.value.code == 'E_WORK_DEPENDENCY', command
        assert caught.value.details['bill_id'] == bill['id']


def test_what_the_caller_supplies_wins_over_what_the_order_says(books):
    order = _order(books)
    bill = books['run']('bill post', dict(
        date='2026-06-16', purchase_order=order['id'], memo='Short delivery',
        expenses=[{'account': books['supplies'], 'amount': '400.00', 'memo': 'Part of it'}]),
        reason='Enter what actually arrived')
    assert bill['total_minor_units'] == 40000 and bill['memo'] == 'Short delivery'
    assert len(bill['revision']['expenses']) == 1
    # The ordered item is not received. Writing any line says what arrived, so the order's own
    # item grid is not filled in behind it: a short delivery that carried the item line would
    # owe the vendor for goods that did not come and take 40 lengths of pipe into stock as
    # well. The empty grid is the proof -- a stock entry exists only for an item line.
    assert bill['revision']['items'] == []
    assert bill['vendor_id'] == books['vendor']  # still the order's vendor
    assert bill['purchase_order_id'] == order['id']
    assert _net(books) == {books['payable']: -40000, books['supplies']: 40000}
    # The order is consumed all the same: there is one bill for it and it is this one.
    assert books['run']('purchase-order show', {'purchase_order': order['id']})['consumed'] is True


def test_a_short_delivery_takes_no_stock_the_vendor_never_sent(books):
    """The money half of this is covered above; this is the half that moves goods.

    The other short-delivery test orders a ``non_inventory_part``, so it proves the payable and
    not the stock -- ``_stock_entries`` filters to ``inventory.TRACKED_TYPES`` and never saw that
    line. An ordered item that IS tracked is the case that matters: carrying the order's item
    grid in behind the caller's own lines would take delivery of goods nobody received, and the
    books would carry stock that does not exist on a shelf anywhere.
    """
    client, company = books['client'], books['company']
    accounts = client.account.query(company=company, limit=200)['items']
    stock = client.item.create(
        company=company, name='Copper Elbow', type='inventory_part', price='4.50',
        description='3/4in copper elbow',
        purchase_description='3/4in copper elbow', cost='1.80',
        cogs_account_id=next(a['id'] for a in accounts if a['type'] == 'cost_of_goods_sold'),
        income_account_id=next(a['id'] for a in accounts if a['type'] == 'income'))['id']
    order = books['run']('purchase-order post', dict(
        vendor=books['vendor'], date='2026-06-01', number='PO-STOCK',
        lines=[{'item': stock, 'quantity': '40', 'rate': '1.80'}]), reason='Order 40 elbows')

    # The freight arrived and the goods did not. The caller says so by writing the one line.
    bill = books['run']('bill post', dict(
        date='2026-06-16', purchase_order=order['id'], memo='Freight only; goods to follow',
        expenses=[{'account': books['freight'], 'amount': '25.00', 'memo': 'Delivery'}]),
        reason='Enter the freight that did arrive')

    # Stock first, deliberately. The line grid is the mechanism and the shelf is the claim, so
    # the shelf is asserted before anything that would fail earlier and hide it.
    held = books['run']('report stock-status', {'as_of': '2026-06-16', 'limit': 20})
    rows = [row for row in held['rows'] if row['item_id'] == stock]
    on_hand = rows[0]['quantity_on_hand'] if rows else '0'
    assert on_hand == '0', (
        f'{on_hand} elbows were received against a bill that never claimed them')
    assert rows[0]['asset_value']['amount'] == '0.00' if rows else True
    assert bill['revision']['items'] == []
    assert bill['total_minor_units'] == 2500


def test_the_bill_cannot_be_owed_to_a_different_vendor_than_the_order(books):
    order = _order(books)
    with pytest.raises(bookflow.BookflowError) as caught:
        books['run']('bill post', dict(date='2026-06-16', purchase_order=order['id'],
                                       vendor=books['other']), reason='Enter it')
    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details['fields'][0]['field'] == 'vendor'
    assert books['run']('purchase-order show', {'purchase_order': order['id']})['consumed'] is False
    # Naming the same vendor the order carries is simply redundant, not wrong.
    bill = books['run']('bill post', dict(date='2026-06-16', purchase_order=order['id'],
                                          vendor=books['vendor']), reason='Enter it')
    assert bill['total_minor_units'] == ORDERED_UNITS


def test_a_preview_of_the_bill_consumes_nothing(books):
    order = _order(books)
    preview = books['client'].run('bill post', dict(date='2026-06-16', purchase_order=order['id']),
                                  company=books['company'], reason='Look first', dry_run=True)
    assert preview['dry_run'] is True and preview['total_minor_units'] == ORDERED_UNITS
    assert preview['purchase_order_id'] == order['id']
    still = books['run']('purchase-order show', {'purchase_order': order['id']})
    assert still['consumed'] is False and still['status'] == 'open' and still['version'] == 1
    assert _net(books) == {}


def test_a_bill_can_still_be_entered_outright_with_no_order_behind_it(books):
    bill = books['run']('bill post', dict(
        vendor=books['vendor'], date='2026-06-16',
        expenses=[{'account': books['supplies'], 'amount': '20.00'}]), reason='Enter a bill')
    assert bill['purchase_order_id'] is None
    with pytest.raises(bookflow.BookflowError) as caught:
        books['run']('bill post', dict(date='2026-06-16'), reason='Enter nothing')
    assert caught.value.code == 'E_VALIDATION'


def test_a_closed_order_can_still_be_billed(books):
    order = _order(books)
    closed = books['run']('purchase-order update', {
        'purchase_order': order['id'], 'status': 'closed',
        'expected_version': order['version']}, reason='Nothing more is coming')
    bill = books['run']('bill post', dict(date='2026-06-16', purchase_order=order['id']),
                        reason='The invoice arrived')
    assert bill['total_minor_units'] == ORDERED_UNITS
    assert bill['purchase_order_id'] == closed['id']


COMMANDS = frozenset(('purchase-order post', 'purchase-order show', 'purchase-order update',
                      'purchase-order void', 'purchase-order query', 'purchase-order history'))


@pytest.mark.timeout(300)
def test_the_same_purchase_order_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio

    from tests.mcp_matrix_support import Matrix, normalize
    from tests.test_mcp_registry_work import GHOST

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}

                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    return await matrix.call(surface, name, raw, **ctx)

                supplies = (await matrix.call(surface, 'account create',
                                              dict(name='Parity supplies', type='expense')))['id']
                vendor = (await matrix.call(surface, 'vendor create',
                                            dict(name='Parity Supply Co')))['id']
                entry = dict(vendor=vendor, date='2026-03-04', expected_date='2026-03-18',
                             number='PARITY-PO-1', reference='QUOTE-1', memo='Parity order',
                             lines=[{'account': supplies, 'description': 'Fittings',
                                     'quantity': '4', 'rate': '25.00'},
                                    {'account': supplies, 'description': 'Delivery',
                                     'amount': '13.00'}])
                assert (await call('purchase-order post', entry, dry_run=True))['dry_run']
                posted = await call('purchase-order post', entry, idempotency_key='order-1')
                replay = await call('purchase-order post', entry, idempotency_key='order-1')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']
                assert posted['total']['amount'] == '113.00'

                await call('purchase-order show', {'purchase_order': posted['id']})
                await call('purchase-order history', {'purchase_order': posted['id'], 'limit': 10})
                await call('purchase-order query', {'vendor': vendor, 'limit': 10})
                received = await call('purchase-order update', {
                    'purchase_order': posted['id'], 'status': 'partly_received',
                    'expected_version': posted['version']})
                assert received['status'] == 'partly_received'
                await call('purchase-order void', {'purchase_order': posted['id'],
                                                   'expected_version': received['version']})

                refused = await call('purchase-order post', {
                    **entry, 'number': 'PARITY-PO-2',
                    'lines': [{'account': supplies, 'item': 'anything', 'amount': '1.00'}]},
                    rejected=True)
                assert refused['code'] == 'E_VALIDATION'
                assert set(calls) == COMMANDS
                for name, data in list(calls.items()):
                    assert (await call(name, data, company=GHOST,
                                       rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
            expected = normalize(matrix.documents['python'], matrix.roots['python'], set())
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], set())
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left, right)
        finally:
            await matrix.close()

    anyio.run(witness)
