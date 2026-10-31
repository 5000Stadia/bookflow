"""Integration of increments 9-11: a split sale, a one-line return and the day report, worked by hand."""
import pytest

from tests.test_pos_sale import _norm, _reads, _setup, twins  # noqa: F401

CART = {'customer': 'Eddie Exempt', 'lines': [{'item': 'Pipe Sealant', 'quantity': '2', 'unit_price': '10.00'}]}
# Exempt customer, 2 x 10.00: a 20.00 sale. Cash 8.00 tendered gives 3.00 change, so cash nets 5.00; Visa pays 15.00.
TENDERS = [{'payment_method': 'Cash', 'amount': '8.00'}, {'payment_method': 'Visa', 'amount': '15.00'}]


@pytest.mark.timeout(1200)
def test_split_sale_one_line_refund_books_and_day_tie_by_hand(twins):
    pos, typed = twins
    _setup(pos)
    reg = _setup(typed)
    sold = pos.run('pos sale complete', dict(CART, ticket='split-refund-day', expected_total='20.00', tenders=TENDERS))
    assert sold['document_type'] == 'invoice' and sold['change']['minor_units'] == 300
    lines = pos.run('pos refund lines', {'sale': sold['id']})['lines']
    returned = pos.run('pos refund', {'operation_key': 'split-day-return', 'sale': sold['id'], 'settlement': 'original',
                                      'lines': [{'source_line': lines[0]['source_line'], 'quantity': '1'}]})
    # One 10.00 unit returns. Design §3/§4 (as built in increment 11): payouts follow the original tender order,
    # each capped at that tender's net payment, so cash gives back its 5.00 net and Visa the other 5.00.
    assert returned['total']['minor_units'] == 1000
    assert [(t['payment_method']['name'], t['amount']['minor_units']) for t in returned['tenders']] == [
        ('Cash', 500), ('Visa', 500)]

    # The bookkeeper's hand entry: an invoice paid by two receipts, a credit memo for one unit, two refunds.
    invoice = typed.run('invoice post', dict(CART, customer='Eddie Exempt', site=reg['site']['id'],
                                            terms='Due on receipt', due_date=sold['date']))
    for n, (method, amount) in enumerate((('Cash', '5.00'), ('Visa', '15.00'))):
        current = typed.run('invoice show', {'invoice': invoice['id']})
        typed.run('payment receive', dict(customer='Eddie Exempt', date=sold['date'], amount=amount,
            deposit_to=reg['deposit_account']['id'], payment_method=method, operation_key=f'hand-payment-{n}',
            applications={'mode': 'inline', 'items': [dict(invoice=invoice['id'],
                                                            expected_version=current['version'], amount=amount)]}))
    line = typed.run('invoice show', {'invoice': invoice['id']})['revision']['lines'][0]
    memo = typed.run('credit-memo post', {'customer': 'Eddie Exempt', 'lines': [{
        'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1', 'site': reg['site']['id']}]})
    assert memo['total'] == returned['total']
    for method in ('Cash', 'Visa'):
        typed.run('customer-refund post', {'date': memo['date'], 'sources': [{'credit_memo': memo['id'], 'amount': '5.00'}],
                                           'funding_account': reg['refund_account']['id'], 'method': method})
    assert _reads(pos) == _reads(typed)

    # The day by hand: 20.00 sold (cash 5.00 net of change, Visa 15.00); 10.00 returned (cash 5.00, Visa 5.00).
    day = pos.run('report pos-day', {'date': sold['date']})
    for name, amount in {'total_sales': 2000, 'gross_sales': 2000, 'tax': 0, 'refunds_total': 1000,
                         'cash_sales': 500, 'cash_refunds': 500, 'cash_to_deposit': 0}.items():
        assert day[name]['minor_units'] == amount, name
    assert day['sales_count'] == 1
    by = {r['payment_method']['name']: r for r in day['by_tender']}
    assert (by['Cash']['amount']['minor_units'], by['Cash']['refund_amount']['minor_units'], by['Cash']['refund_count']) == (500, 500, 1)
    assert (by['Visa']['amount']['minor_units'], by['Visa']['refund_amount']['minor_units'], by['Visa']['refund_count']) == (1500, 500, 1)
