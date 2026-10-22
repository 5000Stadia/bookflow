"""R124: customer prepayments held as a liability until the work bills.

The worked case: Lakeside Builders takes deposits from the Ortegas for a kitchen. Every figure
asserted is worked out beside it from the documents' own amounts and read back from the trial
balance, the invoice and `report customer-prepayments`, never from the prepayment command's own
output alone.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'prepay'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Prepay organization')
    company = client.company.new(legal_name='Lakeside Builders', home_currency='USD', timezone='UTC',
                                 organization='Prepay organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    accounts = run('account query', dict(limit=200))['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    exempt = next(row['id'] for row in run('sales-tax-code list')['items'] if not row['taxable'])
    labor = run('item create', dict(name='Kitchen labor', type='service', sales_enabled=True, description='Labor',
                                    sales_tax_code_id=exempt, income_account_id=income, price='100'))['id']
    methods = {row['name']: row['id'] for row in run('payment-method query', dict(limit=50))['items']}
    ortega = run('customer create', dict(name='Ortega, Ana'))['id']
    other = run('customer create', dict(name='Brook, Sam'))['id']
    return dict(client=client, company=company, run=run, bank=bank, income=income, labor=labor, methods=methods,
                exempt=exempt, ortega=ortega, other=other)


def estimate(books, quantity='4', date='2026-03-02'):
    run = books['run']
    made = run('estimate create', dict(date=date, title='Kitchen', customer=books['ortega'],
                                       lines=[dict(item=books['labor'], quantity=quantity)]), reason='Quote')
    return run('estimate update', dict(estimate=made['id'], expected_version=1, status='accepted',
                                       decision_note='Signed'), reason='Accepted')


def work_order(books, est, key='wo-1', date='2026-03-03'):
    return books['run']('estimate work-order', dict(estimate=est['id'], expected_version=est['version'],
                                                     date=date, conversion_key=key), reason='Start')


def receive(books, amount, date, **target):
    return books['run']('prepayment receive', dict(amount=amount, date=date, payment_method=books['methods']['Check'],
                                                   **target))


def balances(books, date_to='2026-12-31', basis=None):
    raw = dict(date_to=date_to, limit=200)
    if basis:
        raw['basis'] = basis
    report = books['run']('report trial-balance', raw)
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return {row['current_account_name']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows'] if row['debit']['minor_units'] != row['credit']['minor_units']}


def held(books, as_of='2026-12-31'):
    return books['run']('report customer-prepayments', dict(as_of=as_of))['total_held']['minor_units']


def owed(books, invoice):
    return books['run']('invoice show', dict(invoice=invoice))['settlement_current']['due_minor_units']


def statement(books, report, date_from, date_to, basis):
    rows = books['run']('report ' + report, dict(date_from=date_from, date_to=date_to, basis=basis, limit=200)
                        if report == 'profit-and-loss' else dict(date_to=date_to, basis=basis, limit=200))['rows']
    return {row['current_account_name']: row['amount']['minor_units'] for row in rows if row['amount']['minor_units']}


def bill(books, wo, key, date, **extra):
    current = books['run']('work-order show', dict(work_order=wo['id']))
    return books['run']('work-order invoice', dict(work_order=wo['id'], expected_version=current['version'],
                                                   conversion_key=key, date=date, **extra), reason='Bill')


def refused(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


def test_deposit_on_an_accepted_estimate_is_drawn_by_its_work_orders_invoice(books):
    run = books['run']
    est = estimate(books)                                     # 4 x 100.00 = 400.00
    preview = run('prepayment receive', dict(document=f'estimate {est["number"]}', amount='150.00', date='2026-03-02'),
                  dry_run=True)
    assert preview['set_up'] and preview['held_after']['minor_units'] == 15000
    got = receive(books, '150.00', '2026-03-02', document=f'estimate {est["number"]}')
    assert got['receipt']['number'] and got['held_after']['minor_units'] == 15000
    # Dr Undeposited Funds 150, Cr Customer Prepayments 150; no income.
    tb = balances(books)
    assert tb['Customer Prepayments'] == -15000 and tb['Undeposited Funds'] == 15000
    assert not any(name == 'Sale witness income' for name in tb)
    wo = work_order(books, run('estimate show', dict(estimate=est['id'])))
    preview = run('work-order invoice', dict(work_order=wo['id'], expected_version=wo['version'], conversion_key='inv-1',
                                            date='2026-03-20'), dry_run=True, reason='Bill')
    assert preview['prepayment_applied']['minor_units'] == 15000 and preview['balance_due']['minor_units'] == 25000
    inv = run('work-order invoice', dict(work_order=wo['id'], expected_version=wo['version'], conversion_key='inv-1',
                                        date='2026-03-20'), reason='Bill')
    assert inv['prepayment_applied']['minor_units'] == 15000 and inv['balance_due']['minor_units'] == 25000
    # The invoice owes 400 - 150 = 250; the account is back to zero; income is the whole 400.
    assert owed(books, inv['id']) == 25000
    tb = balances(books)
    assert tb.get('Customer Prepayments', 0) == 0
    assert tb['Accounts Receivable'] == 25000
    assert held(books) == 0 and held(books, '2026-03-19') == 15000


def test_two_receipts_are_drawn_oldest_first_by_two_partial_invoices(books):
    run = books['run']
    wo = work_order(books, estimate(books))                    # 4 x 100.00 = 400.00
    first = receive(books, '120.00', '2026-03-04', document=f'work-order {wo["number"]}')
    second = receive(books, '80.00', '2026-03-05', document=f'work-order {wo["number"]}')
    line = run('work-order show', dict(work_order=wo['id']))['revision']['lines'][0]['line_id']
    one = bill(books, wo, 'part-1', '2026-03-10', selections=[dict(line_id=line, quantity='1.5')])
    # 1.5 x 100 = 150: all of the older 120, then 30 of the 80.
    assert one['prepayment_applied']['minor_units'] == 15000 and owed(books, one['id']) == 0
    shown = run('prepayment show', dict(document=f'work-order {wo["number"]}'))
    assert {row['receipt_number']: row['held']['minor_units'] for row in shown['receipts']} == {
        first['receipt']['number']: 0, second['receipt']['number']: 5000}
    two = bill(books, wo, 'part-2', '2026-03-12')
    # The remaining 2.5 x 100 = 250 takes the last 50; 200 is owed.
    assert two['prepayment_applied']['minor_units'] == 5000 and owed(books, two['id']) == 20000
    draws = run('prepayment show', dict(document=f'work-order {wo["number"]}'))['draws']
    assert [(row['receipt_number'], row['amount']['minor_units'], row['invoice_number']) for row in draws] == [
        (first['receipt']['number'], 12000, one['number']), (second['receipt']['number'], 3000, one['number']),
        (second['receipt']['number'], 5000, two['number'])]
    assert held(books) == 0 and held(books, '2026-03-11') == 5000 and held(books, '2026-03-04') == 12000
    assert balances(books).get('Customer Prepayments', 0) == 0


def test_a_customer_receipt_attached_later_is_drawn_and_apply_prepayment_false_leaves_it(books):
    run = books['run']
    got = receive(books, '100.00', '2026-03-01', customer=books['ortega'])
    est = estimate(books)
    moved = run('prepayment attach', dict(receipt=got['receipt']['number'], document=f'estimate {est["number"]}'))
    assert moved['document']['number'] == est['number']
    bad = refused(lambda: run('prepayment attach', dict(receipt=got['receipt']['number'], document=f'estimate {est["number"]}')))
    assert bad.code == 'E_VALIDATION'
    current = run('estimate show', dict(estimate=est['id']))
    kept = run('estimate invoice', dict(estimate=est['id'], expected_version=current['version'], conversion_key='e-1',
                                        date='2026-03-09', apply_prepayment=False, selections=[dict(
                                            line_id=current['revision']['lines'][0]['line_id'], quantity='1')]), reason='Bill')
    assert kept['prepayment_applied']['minor_units'] == 0 and owed(books, kept['id']) == 10000
    current = run('estimate show', dict(estimate=est['id']))
    used = run('estimate invoice', dict(estimate=est['id'], expected_version=current['version'], conversion_key='e-2',
                                        date='2026-03-10'), reason='Bill')
    assert used['prepayment_applied']['minor_units'] == 10000 and owed(books, used['id']) == 20000
    # The first invoice can still take it by hand: nothing is held now, so it is refused.
    assert refused(lambda: run('prepayment apply', dict(invoice=kept['number']))).code == 'E_VALIDATION'


def test_unapply_restores_and_refund_pays_out(books):
    run = books['run']
    est = estimate(books)
    receive(books, '150.00', '2026-03-02', document=f'estimate {est["number"]}')
    wo = work_order(books, run('estimate show', dict(estimate=est['id'])))
    inv = bill(books, wo, 'inv', '2026-03-20')
    memo = run('prepayment show', dict(document=f'work-order {wo["number"]}'))['draws'][0]['credit_memo_number']
    assert refused(lambda: run('prepayment unapply', dict(draw=memo))).code == 'E_REASON_REQUIRED'
    undone = run('prepayment unapply', dict(draw=memo), reason='Applied too early')
    assert undone['held_after']['minor_units'] == 15000
    assert owed(books, inv['id']) == 40000 and held(books) == 15000
    assert balances(books)['Customer Prepayments'] == -15000
    assert refused(lambda: run('prepayment unapply', dict(draw=memo), reason='again')).code == 'E_APPLICATION_INACTIVE'
    paid = run('prepayment refund', dict(document=f'work-order {wo["number"]}', date='2026-03-25',
                                         bank_account=books['bank'], payment_method=books['methods']['Check'],
                                         check_number='2210'))
    assert paid['amount']['minor_units'] == 15000 and paid['refund']['number']
    tb = balances(books)
    # Received into Undeposited Funds 150, paid out of the bank 150: the liability is gone.
    assert tb.get('Customer Prepayments', 0) == 0 and tb['Undeposited Funds'] == 15000
    assert held(books) == 0 and held(books, '2026-03-24') == 15000
    back = run('prepayment unapply', dict(draw=paid['credit_memo']['number']), reason='Check not sent')
    assert back['refund']['number'] == paid['refund']['number'] and held(books) == 15000


def test_a_kept_fee_reaches_income_only_through_its_invoice(books):
    run = books['run']
    fee_income = run('account create', dict(name='Cancellation Fees', type='income'))['id']
    fee = run('item create', dict(name='Cancellation fee', type='service', sales_enabled=True, description='Fee',
                                  sales_tax_code_id=books['exempt'], income_account_id=fee_income, price='40'))['id']
    wo = work_order(books, estimate(books))
    receive(books, '100.00', '2026-03-04', document=f'work-order {wo["number"]}')
    current = run('work-order show', dict(work_order=wo['id']))
    blocked = refused(lambda: run('work-order update', dict(work_order=wo['id'], expected_version=current['version'],
                                                            status='cancelled'), reason='Customer cancelled'))
    assert blocked.code == 'E_HAS_APPLICATIONS' and 'prepayment refund' in blocked.details['next']
    assert 'Cancellation Fees' not in balances(books)
    invoice = run('invoice post', dict(customer=books['ortega'], date='2026-03-06', lines=[dict(item=fee, quantity='1')]))
    note = ' '.join(invoice['warnings'])
    assert 'prepayment apply' in note
    kept = run('prepayment apply', dict(invoice=invoice['number'], document=f'work-order {wo["number"]}'))
    assert kept['amount']['minor_units'] == 4000 and kept['balance_due']['minor_units'] == 0
    run('prepayment refund', dict(document=f'work-order {wo["number"]}', date='2026-03-07',
                                  bank_account=books['bank'], payment_method=books['methods']['Check']))
    tb = balances(books)
    assert tb['Cancellation Fees'] == -4000 and tb.get('Customer Prepayments', 0) == 0
    run('work-order update', dict(work_order=wo['id'], expected_version=current['version'], status='cancelled'),
        reason='Customer cancelled')


def test_every_guard_refuses_with_its_hint(books):
    run = books['run']
    est = estimate(books)
    got = receive(books, '150.00', '2026-03-02', document=f'estimate {est["number"]}')
    receipt = got['receipt']
    # Not on an unaccepted estimate.
    draft = run('estimate create', dict(date='2026-03-02', title='Bath', customer=books['ortega'],
                                        lines=[dict(item=books['labor'], quantity='1')]), reason='Quote')
    error = refused(lambda: receive(books, '10.00', '2026-03-02', document=f'estimate {draft["number"]}'))
    assert error.code == 'E_VALIDATION' and 'accepted' in str(error.details)
    # Not on another customer's document.
    error = refused(lambda: run('prepayment attach', dict(receipt=receipt['number'], document=f'estimate {draft["number"]}')))
    assert error.code == 'E_VALIDATION'
    wo = work_order(books, run('estimate show', dict(estimate=est['id'])))
    inv = bill(books, wo, 'inv', '2026-03-20')
    memo = run('prepayment show', dict(receipt=receipt['number']))['draws'][0]['credit_memo_number']
    # A drawn receipt cannot be voided or cut below what is drawn.
    shown = run('sales-receipt show', dict(sales_receipt=receipt['id']))
    error = refused(lambda: run('sales-receipt void', dict(sales_receipt=receipt['id'], expected_version=shown['version']),
                                reason='Mistake'))
    assert error.code == 'E_HAS_APPLICATIONS' and 'prepayment unapply' in error.details['next']
    line = shown['revision']['lines'][0]
    error = refused(lambda: run('sales-receipt update', dict(sales_receipt=receipt['id'], expected_version=shown['version'],
        lines=[dict(line_id=line['line_id'], item=line['item_id'], unit_price='100.00')])))
    assert error.code == 'E_APPLIED_EXCEEDS_TOTAL'
    # The draw is undone only as a whole.
    memo_row = run('credit-memo show', dict(credit_memo=memo))
    error = refused(lambda: run('credit-memo void', dict(credit_memo=memo, expected_version=memo_row['version']), reason='x'))
    assert error.code == 'E_HAS_APPLICATIONS' and 'prepayment unapply' in error.details['next']
    # Checked before the application named is even looked at.
    error = refused(lambda: run('customer-credit unapply', dict(credit_memo=memo, expected_version=memo_row['version'],
        applications=[dict(application_id=memo_row['id'], invoice_expected_version=2)]), reason='x'))
    assert error.code == 'E_HAS_APPLICATIONS' and 'prepayment unapply' in error.details['next']
    current = run('invoice show', dict(invoice=inv['id']))
    error = refused(lambda: run('invoice void', dict(invoice=inv['id'], expected_version=current['version']), reason='x'))
    assert error.code == 'E_HAS_APPLICATIONS' and 'prepayment unapply' in error.details['next']
    # Caps: more than held, more than owed.
    receive(books, '20.00', '2026-03-21', document=f'work-order {wo["number"]}')
    error = refused(lambda: run('prepayment apply', dict(invoice=inv['number'], amount='30.00')))
    assert error.code == 'E_APPLIED_EXCEEDS_TOTAL' and error.details['held_minor_units'] == 2000
    other = run('invoice post', dict(customer=books['ortega'], date='2026-03-22', lines=[dict(item=books['labor'], quantity='0.1')]))
    error = refused(lambda: run('prepayment apply', dict(invoice=other['number'], document=f'work-order {wo["number"]}',
                                                          amount='15.00')))
    assert error.code == 'E_APPLIED_EXCEEDS_TOTAL' and error.details['invoice_due_minor_units'] == 1000
    # Held money cannot be quoted, invoiced or journaled around the ledger.
    item = run('prepayment show', dict(receipt=receipt['number']))
    prepay_item = shown['revision']['lines'][0]['item_id']
    error = refused(lambda: run('invoice post', dict(customer=books['ortega'], date='2026-03-22',
                                                     lines=[dict(item=prepay_item, unit_price='5.00')])))
    assert error.code == 'E_VALIDATION' and 'prepayment receive' in str(error.details)
    error = refused(lambda: run('estimate create', dict(date='2026-03-02', title='x', customer=books['ortega'],
                                                        lines=[dict(item=prepay_item, unit_price='5.00')]), reason='q'))
    assert error.code == 'E_VALIDATION'
    account = got and run('account query', dict(limit=200))['items']
    prepay_account = next(row['id'] for row in account if row['full_name'] == 'Customer Prepayments')
    error = refused(lambda: run('journal post', dict(date='2026-03-22', lines=[
        dict(account=prepay_account, side='debit', amount='5.00'), dict(account=books['bank'], side='credit', amount='5.00')])))
    assert error.code == 'E_VALIDATION' and 'prepayment receive' in str(error.details)
    error = refused(lambda: run('item create', dict(name='Deposit', type='other_charge', sales_enabled=True,
                                                    description='Deposit', income_account_id=prepay_account, price='0')))
    assert error.code == 'E_VALIDATION'
    # A document holding money cannot be voided.
    current = run('work-order show', dict(work_order=wo['id']))
    error = refused(lambda: run('work-order update', dict(work_order=wo['id'], expected_version=current['version'],
                                                          status='cancelled'), reason='x'))
    assert error.code == 'E_HAS_APPLICATIONS'
    assert item['held']['minor_units'] == 0


def test_an_unapply_inside_a_closed_period_is_refused(books):
    run = books['run']
    wo = work_order(books, estimate(books))
    receive(books, '100.00', '2026-03-04', document=f'work-order {wo["number"]}')
    bill(books, wo, 'inv', '2026-03-10')
    memo = run('prepayment show', dict(document=f'work-order {wo["number"]}'))['draws'][0]['credit_memo_number']
    info = run('company show')
    run('company update', dict(expected_version=info['info_version'], closing_date='2026-03-31'))
    error = refused(lambda: run('prepayment unapply', dict(draw=memo), reason='Too late'))
    assert error.code == 'E_PERIOD_CLOSED' and 'closed period' in error.details['next']
    assert held(books) == 0


def test_cash_and_accrual_bases_against_a_hand_worked_example(books):
    """January: a 100.00 deposit for the customer. February: a 100.00 invoice, the deposit applied.

    Accrual: January income 0, liability 100; February income 100, liability 0, receivable 0.
    Cash: the same. The deposit is never January income, because a receipt moves only
    balance-sheet accounts; the draw settles the February invoice, recognised in February.
    """
    run = books['run']
    receive(books, '100.00', '2026-01-15', customer=books['ortega'])
    invoice = run('invoice post', dict(customer=books['ortega'], date='2026-02-10',
                                       lines=[dict(item=books['labor'], quantity='1')]))
    run('prepayment apply', dict(invoice=invoice['number'], customer=books['ortega']))
    income = next(row['full_name'] for row in run('account query', dict(limit=200))['items'] if row['id'] == books['income'])
    for basis in ('accrual', 'cash'):
        january = statement(books, 'profit-and-loss', '2026-01-01', '2026-01-31', basis)
        february = statement(books, 'profit-and-loss', '2026-02-01', '2026-02-28', basis)
        assert not january, (basis, january)
        assert february == {income: 10000}, (basis, february)
        jan_bs = statement(books, 'balance-sheet', None, '2026-01-31', basis)
        feb_bs = statement(books, 'balance-sheet', None, '2026-02-28', basis)
        assert abs(jan_bs['Customer Prepayments']) == 10000, (basis, jan_bs)
        assert 'Customer Prepayments' not in feb_bs and 'Accounts Receivable' not in feb_bs, (basis, feb_bs)


def test_the_report_ties_to_the_account_on_any_date(books):
    run = books['run']
    wo = work_order(books, estimate(books))
    receive(books, '120.00', '2026-03-04', document=f'work-order {wo["number"]}')
    receive(books, '30.00', '2026-03-05', customer=books['other'])
    bill(books, wo, 'inv', '2026-03-10', selections=[dict(
        line_id=run('work-order show', dict(work_order=wo['id']))['revision']['lines'][0]['line_id'], quantity='1')])
    expected = {'2026-03-03': 0, '2026-03-04': 12000, '2026-03-05': 15000, '2026-03-10': 5000, '2026-12-31': 5000}
    for date, amount in expected.items():
        report = run('report customer-prepayments', dict(as_of=date))
        assert report['total_held']['minor_units'] == amount == report['account_balance']['minor_units']
        sheet = statement(books, 'balance-sheet', None, date, 'accrual')
        assert abs(sheet.get('Customer Prepayments', 0)) == amount
    report = run('report customer-prepayments', dict(as_of='2026-12-31', include_drawn=True))
    assert [(row['kind'], row['amount']['minor_units'], row['held']['minor_units']) for row in report['rows']
            if row['customer_id'] == books['ortega']] == [('receipt', 12000, 12000), ('draw', -10000, 2000)]
    one = run('report customer-prepayments', dict(as_of='2026-12-31', customer=books['other']))
    assert one['total_held']['minor_units'] == 3000 == one['account_balance']['minor_units']


def test_a_draw_racing_a_receipt_void(books):
    run = books['run']
    got = receive(books, '50.00', '2026-03-01', customer=books['ortega'])
    invoice = run('invoice post', dict(customer=books['ortega'], date='2026-03-02',
                                       lines=[dict(item=books['labor'], quantity='1')]))
    preview = run('prepayment apply', dict(invoice=invoice['number']), dry_run=True)
    assert preview['amount']['minor_units'] == 5000
    shown = run('sales-receipt show', dict(sales_receipt=got['receipt']['id']))
    run('sales-receipt void', dict(sales_receipt=got['receipt']['id'], expected_version=shown['version']), reason='Bounced')
    # The draw is decided again inside the writer lock, and sees the void.
    error = refused(lambda: run('prepayment apply', dict(invoice=invoice['number'])))
    assert error.code == 'E_VALIDATION' and owed(books, invoice['id']) == 10000
    assert held(books) == 0


def test_the_receipt_is_banked_by_deposit_post(books):
    run = books['run']
    got = receive(books, '150.00', '2026-03-02', customer=books['ortega'])
    version = run('sales-receipt show', dict(sales_receipt=got['receipt']['id']))['version']
    run('deposit post', dict(operation_key='bank-deposit', document=dict(
        mode='inline', deposit_to=books['bank'], date='2026-03-03', additional=[],
        sources=[dict(source_type='sales_receipt', source=got['receipt']['id'], expected_version=version)])),
        reason='Bank it')
    tb = balances(books)
    assert tb.get('Undeposited Funds', 0) == 0 and tb['Customer Prepayments'] == -15000
    assert held(books) == 15000
