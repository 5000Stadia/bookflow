"""Cash projection through real commercial writers; independent signed balances."""
from collections import defaultdict
from pathlib import Path
import sqlite3

import pytest

from bookflow.company.cash_basis import cutoff_adjustments, _signed_shares
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset
from tests.test_stocked_credit_returns import _service, _return
from tests.credit_support import books as credit_books, invoice, taxed_invoice, goodwill_credit, apply_credit, refund


@pytest.fixture(autouse=True)
def report_error_contract(monkeypatch):
    # Parent owns the shared error catalog/report declarations. This isolated
    # base predates that additive registration; exercise the agreed code here.
    from bookflow.core.errors import ALL_CODES
    if 'E_CASH_BASIS_EVIDENCE' not in ALL_CODES:
        monkeypatch.setitem(ALL_CODES, 'E_CASH_BASIS_EVIDENCE', 'Cash recognition source evidence is inconsistent.')


def projection(books, cutoff):
    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        before = '\n'.join(db.iterdump())
        rows = cutoff_adjustments(db, cutoff)
        assert sum(r.amount_minor_units for r in rows) == 0
        values = defaultdict(int)
        for account, debit, credit in db.execute('''SELECT l.account_id,l.debit_minor_units,l.credit_minor_units
          FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id WHERE b.effective_date<=?''', (cutoff,)):
            values[account] += debit - credit
        for row in rows:
            values[row.account_id] += row.amount_minor_units
            assert db.execute('SELECT 1 FROM posting_line_sources WHERE id=?', (row.posting_source_id,)).fetchone()
        assert before == '\n'.join(db.iterdump())
        assert sum(values.values()) == 0
    return {k: v for k, v in values.items() if v}, rows


def pay(books, sale, amount, date='2017-01-20', key='payment'):
    return books['run']('payment receive', dict(customer=books['customer'], date=date,
        amount=amount, operation_key=key, deposit_to=books['bank'], payment_method=books['methods']['Cash'],
        applications={'mode': 'inline', 'items': [dict(invoice=sale['id'], amount=amount,
          expected_version=sale['version'])]}), reason='Pay')


def test_stock_partial_current_matching_dates_unapply_and_free_cost(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='3', unit_cost='8')]), reason='Stock')
    sale = run('invoice post', dict(customer=books['customer'], date='2017-01-10',
        lines=[dict(item=item, quantity='1', unit_price='12')]), reason='Sell')
    ar = sale['revision']['profile']['control_account']['id']
    assert projection(books, '2017-01-15')[0] == {asset: 1600, books['payable']: -2400, ar: 800}
    payment = pay(books, sale, '6')
    assert projection(books, '2017-01-31')[0] == {
        asset: 1600, books['payable']: -2400, ar: 400, books['bank']: 600,
        books['income']: -600, books['cogs']: 400}
    application = payment['effect']['applications'][0]['application_id']
    run('payment unapply', dict(payment=payment['id'], expected_version=1, operation_key='unmatch',
        applications=[dict(application_id=application, invoice_expected_version=2)]), reason='Unmatch')
    assert projection(books, '2017-01-31')[0] == {
        asset: 1600, books['payable']: -2400, ar: 200, books['bank']: 600}
    run('payment apply', dict(payment=payment['id'], expected_version=2, date='2017-02-01',
        operation_key='rematch', applications={'mode': 'inline', 'items': [dict(invoice=sale['id'], amount='6', expected_version=3)]}), reason='Match later')
    # February clerical application reattributes January money to January sale.
    assert projection(books, '2017-01-31')[0][books['income']] == -600
    free = run('invoice post', dict(customer=books['customer'], date='2017-02-02',
        lines=[dict(item=item, quantity='1', unit_price='0')]), reason='Free sample')
    assert free['total']['minor_units'] == 0
    assert projection(books, '2017-02-28')[0][books['cogs']] == 1200
    # Backdated stock makes each of the two sold units cost 6 instead of 8.
    run('bill post', dict(vendor=books['vendor'], date='2016-12-31',
        items=[dict(item=item, quantity='3', unit_cost='4')]), reason='Earlier receipt')
    assert projection(books, '2017-02-28')[0][books['cogs']] == 900


def test_prepayment_never_recognizes_before_commercial_date(books):
    run = books['run']
    sale = run('invoice post', dict(customer=books['customer'], date='2017-02-01',
        lines=[dict(item=_service(books), quantity='1', unit_price='100')]), reason='February work')
    receipt = run('payment receive', dict(customer=books['customer'], date='2017-01-20',
        amount='100', operation_key='advance', deposit_to=books['bank'], payment_method=books['methods']['Cash']), reason='Advance')
    run('payment apply', dict(payment=receipt['id'], expected_version=1, date='2017-02-10',
        operation_key='advance-match', applications={'mode': 'inline', 'items': [dict(invoice=sale['id'], amount='100', expected_version=1)]}), reason='Match advance')
    assert books['income'] not in projection(books, '2017-01-31')[0]
    assert sum(r.amount_minor_units for r in projection(books, '2017-02-28')[1] if not r.control_offset) == 0


def test_signed_endpoint_denominator_and_remainders():
    assert _signed_shares([10000, 10000, -2000], 9000, 18000) == [5000, 5000, -1000]
    assert _signed_shares([101, 100, -20], 90, 181) == [50, 49, -9]
    assert _signed_shares([101, 100, -20], 181, 181) == [101, 100, -20]


def test_mixed_bill_partial_payment_and_vendor_credit(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    bill = run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        expenses=[dict(account=books['parts'], amount='100')],
        items=[dict(item=item, quantity='1', unit_cost='100')]), reason='Mixed bill')
    assert projection(books, '2017-01-31')[0] == {asset: 10000, books['payable']: -10000}
    run('bill pay', dict(date='2017-02-01', bills=[dict(bill=bill['id'], amount='100')],
        funding_account=books['bank'], method=books['methods']['Check']), reason='Half paid')
    assert projection(books, '2017-02-28')[0] == {
        asset: 10000, books['parts']: 5000, books['payable']: -5000, books['bank']: -10000}
    credit = run('vendor-credit post', dict(vendor=books['vendor'], date='2017-03-01',
        expenses=[dict(account=books['parts'], amount='100')]), reason='Credit')
    assert projection(books, '2017-03-31')[0][books['parts']] == 5000
    run('vendor-credit apply', dict(credit=credit['id'], expected_version=1,
        bills=[dict(bill=bill['id'])]), reason='Offset bill')
    assert projection(books, '2017-03-31')[0] == {asset: 10000, books['bank']: -10000}


def test_credit_refund_and_non_cash_application_dates(credit_books):
    b = credit_books
    sale = invoice(b)
    credit = goodwill_credit(b, '30')
    assert projection(b, '2026-03-31')[0] == {}
    refund(b, credit['id'], '10', date='2026-03-20')
    assert projection(b, '2026-03-31')[0] == {b['checking']: -1000, b['income']: 1000}
    apply_credit(b, credit, sale['id'], '20', date='2026-04-01')
    # Noncash release waits for application, unlike monetary attribution.
    assert projection(b, '2026-03-31')[0] == {b['checking']: -1000, b['income']: 1000}
    assert projection(b, '2026-04-30')[0] == {b['checking']: -1000, b['income']: 1000}


def test_tax_is_not_revenue_and_paid_credit_return_does_not_double_income(credit_books):
    b = credit_books
    sale = taxed_invoice(b)
    b['run']('payment receive', dict(customer=b['kerr'], date='2026-03-05', amount='54',
        operation_key='tax-half', deposit_to=b['checking'], payment_method=b['method'],
        applications={'mode': 'inline', 'items': [dict(invoice=sale['id'], expected_version=1, amount='54')]}), reason='Half including tax')
    values, rows = projection(b, '2026-03-31')
    assert values == {b['income']: -5000, b['liability']: -800, b['receivable']: 400, b['checking']: 5400}
    assert all(row.item_id == b['widget'] for row in rows)


def test_statement_charge_correction_void_and_direct_families(books):
    run = books['run']
    service = _service(books)
    charge = run('statement-charge post', dict(customer=books['customer'], date='2017-01-01',
        item=service, amount='100'), reason='Charge')
    assert projection(books, '2017-01-31')[0] == {}
    pay(books, charge, '25')
    assert projection(books, '2017-01-31')[0] == {books['bank']: 2500, books['income']: -2500}
    # Commercial correction keeps currently matched money, not the stale capture amount.
    sale = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=service, quantity='1', unit_price='20')]), reason='Invoice')
    run('invoice update', dict(invoice=sale['id'], expected_version=1,
        lines=[dict(line_id=sale['revision']['lines'][0]['line_id'], item=service, quantity='1', unit_price='30')]), reason='Correct')
    assert projection(books, '2017-01-31')[0] == {books['bank']: 2500, books['income']: -2500}
    run('invoice void', dict(invoice=sale['id'], expected_version=2), reason='Void')
    run('sales-receipt post', dict(customer=books['customer'], date='2017-01-02',
        deposit_to=books['bank'], payment_method=books['methods']['Cash'], lines=[dict(item=service, quantity='1', unit_price='10')]), reason='Cash sale')
    run('check post', dict(account=books['bank'], date='2017-01-02', amount='5',
        expenses=[dict(account=books['parts'], amount='5')]), reason='Cash expense')
    card = run('account create', dict(name='Card', type='credit_card'))['id']
    run('card-charge post', dict(account=card, date='2017-01-02', amount='3',
        expenses=[dict(account=books['parts'], amount='3')]), reason='Card expense')
    assert projection(books, '2017-01-31')[0] == {
        books['bank']: 3000, books['income']: -3500, books['parts']: 800, card: -300}


def test_stock_return_refund_cost_and_reversal(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost='8')]), reason='Stock')
    sale = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price='12')]), reason='Sale')
    pay(books, sale, '12')
    credit = _return(books, sale, date='2017-02-01')
    ar = sale['revision']['profile']['control_account']['id']
    assert projection(books, '2017-02-28')[0] == {
        asset: 1600, books['payable']: -1600, ar: -800, books['bank']: 1200,
        books['income']: -1200, books['cogs']: 800}
    refunded = run('customer-refund post', dict(date='2017-03-01', funding_account=books['bank'],
        method=books['methods']['Cash'], sources=[dict(credit_memo=credit['id'], amount='6')]), reason='Half refund')
    assert projection(books, '2017-03-31')[0] == {
        asset: 1600, books['payable']: -1600, ar: -400, books['bank']: 600,
        books['income']: -600, books['cogs']: 400}
    run('customer-refund void', dict(refund=refunded['id'], expected_version=1), reason='Cancel refund')
    assert projection(books, '2017-03-31')[0][books['cogs']] == 800


def test_linked_shipping_bill_mixed_expense_preserves_stock_and_denominator(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    receipt = run('item-receipt post', dict(date='2017-01-01', vendor=books['vendor'], shipping='12',
        items=[dict(item=item, quantity='3', unit_cost='8')]), reason='Receive with freight')
    bill = run('bill post', dict(date='2017-01-10',
        receipts=[dict(receipt_line=receipt['items'][0]['id'], expected_receipt_version=1, quantity='3')],
        expenses=[dict(account=books['parts'], amount='36')]), reason='Bill goods and service')
    assert bill['total_minor_units'] == 7200
    run('bill pay', dict(date='2017-02-01', bills=[dict(bill=bill['id'], amount='36')],
        funding_account=books['bank'], method=books['methods']['Check']), reason='Half')
    assert projection(books, '2017-02-28')[0] == {
        asset: 3600, books['parts']: 1800, books['payable']: -1800, books['bank']: -3600}


def test_paid_stock_correction_discards_exact_old_cost_reversal_before_allocation(books):
    run = books['run']
    item = _inventory_part(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='3', unit_cost='8')]), reason='Stock')
    sale = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price='12')]), reason='Sell')
    receipt = pay(books, sale, '6')
    edited = run('invoice update', dict(invoice=sale['id'], expected_version=2, operation_key='correct-paid',
        settlement_versions=[dict(payment=receipt['id'], expected_version=1)],
        lines=[dict(line_id=sale['revision']['lines'][0]['line_id'], item=item, quantity='2', unit_price='12')]), reason='Two sold')
    pay(books, edited, '18', date='2017-02-01', key='balance')
    assert projection(books, '2017-02-28')[0] == {
        _inventory_asset(books): 800, books['payable']: -2400, books['bank']: 2400,
        books['income']: -2400, books['cogs']: 1600}


def test_ap_later_matching_restates_money_date_and_unapply_leaves_prepayment(books):
    run = books['run']
    bill = run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        expenses=[dict(account=books['parts'], amount='100')]), reason='Bill')
    result = run('bill pay', dict(date='2017-01-20', bills=[dict(bill=bill['id'], amount='40')],
        funding_account=books['bank'], method=books['methods']['Check']), reason='Pay')
    payment = result['payments'][0]
    run('bill payment unapply', dict(payment=payment['id'], expected_version=1), reason='Unmatch')
    assert projection(books, '2017-01-31')[0] == {books['payable']: 4000, books['bank']: -4000}
    run('bill payment apply', dict(payment=payment['id'], expected_version=2, date='2017-02-10',
        bills=[dict(bill=bill['id'], amount='40')]), reason='Match later')
    assert projection(books, '2017-01-31')[0] == {books['parts']: 4000, books['bank']: -4000}


def test_invalid_signed_capacity_refuses_instead_of_silently_clamping():
    from bookflow import BookflowError
    with pytest.raises(BookflowError) as error:
        _signed_shares([100, -20], 81, 80)
    assert error.value.code == 'E_CASH_BASIS_EVIDENCE'


def test_vendor_credit_uses_captured_source_component_not_unrelated_expense(books):
    run = books['run']
    bill = run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        expenses=[dict(account=books['parts'], amount='100')]), reason='Bill')
    credit = run('vendor-credit post', dict(vendor=books['vendor'], date='2017-01-10',
        expenses=[dict(account=books['parts'], amount='100'), dict(account=books['freight'], amount='100')]), reason='Two credit components')
    run('vendor-credit apply', dict(credit=credit['id'], expected_version=1,
        bills=[dict(bill=bill['id'], amount='100')]), reason='Consume first component')
    # The source writer consumes the first 100 credit component, not 50 of each.
    assert projection(books, '2017-01-31')[0] == {}


def test_zero_value_receipt_and_free_sale_then_positive_cost_correction(books):
    run = books['run']
    item = _inventory_part(books)
    bill = run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost='0')]), reason='Free stock')
    run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price='0')]), reason='Free sale')
    assert projection(books, '2017-01-31') == ({}, ())
    line = bill['revision']['items'][0]
    run('bill update', dict(bill=bill['id'], items=[dict(line_id=line['line_id'],
        item=item, quantity='2', unit_cost='8')]), reason='Correct known cost')
    assert projection(books, '2017-01-31')[0] == {
        _inventory_asset(books): 800, books['payable']: -1600, books['cogs']: 800}


def test_recost_penny_uses_cumulative_original_cost_not_rounded_delta(books):
    run = books['run']
    item = _inventory_part(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='1', unit_cost='0.03')]), reason='Three cent item')
    sale = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price='2')]), reason='Sell')
    pay(books, sale, '1')
    assert projection(books, '2017-01-31')[0][books['cogs']] == 1
    run('bill post', dict(vendor=books['vendor'], date='2016-12-31',
        items=[dict(item=item, quantity='1', unit_cost='0.05')]), reason='Earlier five cent item')
    # Cost is now four cents. Half is two, not floor(3/2)+floor(1/2).
    assert projection(books, '2017-01-31')[0][books['cogs']] == 2


def test_grouped_same_day_recost_pairs_and_class_dimensions(books):
    run = books['run']
    item = _inventory_part(books)
    books['client'].company.update(company=books['company'], use_classes=True)
    classes = [run('class create', dict(name=name))['id'] for name in ('East', 'West')]
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost='0.03')]), reason='Stock')
    for index, class_id in enumerate(classes):
        sale = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
            lines=[dict(item=item, quantity='1', unit_price='2', class_id=class_id)]), reason='Sell')
        pay(books, sale, '1', key='class-' + str(index))
    run('bill post', dict(vendor=books['vendor'], date='2016-12-31',
        items=[dict(item=item, quantity='2', unit_cost='0.05')]), reason='Revalue both issues together')
    values, adjustments = projection(books, '2017-01-31')
    assert values[books['cogs']] == 4
    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*),count(DISTINCT posting_batch_id) FROM inventory_movements WHERE kind='recost'").fetchone() == (2, 1)
        dimensional = defaultdict(int)
        for class_id, amount in db.execute('SELECT class_id,debit_minor_units-credit_minor_units FROM posting_lines WHERE account_id=?', (books['cogs'],)):
            dimensional[class_id] += amount
        for row in adjustments:
            # Preserve actual posted dimensions, including recost's captured class.
            assert db.execute('SELECT name_type,name_id,class_id FROM posting_lines WHERE id=?', (row.posting_line_id,)).fetchone() == (row.name_type, row.name_id, row.class_id)
            if row.account_id == books['cogs']:
                dimensional[row.class_id] += row.amount_minor_units
    assert dict(dimensional) == {classes[0]: 2, classes[1]: 2}


def test_one_cent_sale_and_return_have_sign_symmetric_partial_cost(books):
    run = books['run']
    item = _inventory_part(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='1', unit_cost='0.01')]), reason='One cent cost')
    sale = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price='2')]), reason='Sell')
    pay(books, sale, '1')
    assert projection(books, '2017-01-31')[0].get(books['cogs'], 0) == 0
    credit = _return(books, sale, date='2017-02-01')
    run('customer-refund post', dict(date='2017-02-10', funding_account=books['bank'],
        method=books['methods']['Cash'], sources=[dict(credit_memo=credit['id'], amount='1')]), reason='Refund half')
    assert projection(books, '2017-02-28')[0].get(books['cogs'], 0) == 0


@pytest.mark.parametrize('damage', ['application', 'allocation', 'source', 'profile'])
def test_missing_live_evidence_refuses_and_fixture_rollback_is_exact(books, damage):
    from bookflow import BookflowError
    run = books['run']
    sale = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=_service(books), quantity='1', unit_price='100')]), reason='Invoice')
    pay(books, sale, '25')
    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        before = '\n'.join(db.iterdump())
        db.execute('BEGIN')
        table = {'application': 'applications', 'allocation': 'application_allocations',
                 'source': 'posting_line_sources', 'profile': 'sales_profiles'}[damage]
        for (trigger,) in db.execute('SELECT name FROM sqlite_master WHERE type=? AND tbl_name=?', ('trigger', table)).fetchall():
            db.execute('DROP TRIGGER "' + trigger.replace('"', '""') + '"')
        if damage in {'application', 'allocation', 'profile'}:
            db.execute('DELETE FROM ' + table)
        else:
            db.execute('DELETE FROM posting_line_sources WHERE posting_line_id IN (SELECT id FROM posting_lines WHERE transaction_id=? AND account_id=?)', (sale['id'], books['income']))
        with pytest.raises(BookflowError) as error:
            cutoff_adjustments(db, '2017-01-31')
        assert error.value.code == 'E_CASH_BASIS_EVIDENCE'
        db.rollback()
        assert before == '\n'.join(db.iterdump())
