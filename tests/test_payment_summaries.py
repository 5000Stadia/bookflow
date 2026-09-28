"""R93: a payment result carries a short summary beside its full detail, worked by hand.

Terms "2% 10 Net 30", documents dated 2026-03-01, paid 2026-03-08 (inside the discount window).
"""
from tests.test_early_discounts import books, invoice, receive, bill, pay  # noqa: F401


def _rows(summary):
    return {row['number']: (row['applied']['amount'], (row.get('discount') or {}).get('amount'),
                            row['still_due']['amount'], row['paid_in_full']) for row in summary['documents']}


def test_customer_payment_summary_names_paid_owing_credit_and_discount(books):
    run = books['run']
    first = invoice(books, '1000.00', number='SUM-1')   # 2% discount 20.00 -> 980.00 cash settles it
    second = invoice(books, '300.00', number='SUM-2')   # 120.00 cash leaves 180.00 due
    # Cash 1150.00 = 980.00 + 120.00 applied, 50.00 left over as the customer's credit.
    paid = receive(books, '1150.00', [(first, 1, '980.00'), (second, 1, '120.00')],
                   discounts=[dict(invoice=first['id'], amount='20.00')])
    summary = paid['summary']
    assert _rows(summary) == {'SUM-1': ('980.00', '20.00', '0.00', True),
                              'SUM-2': ('120.00', None, '180.00', False)}
    assert summary['document_count'] == 2 and summary['paid_in_full_count'] == 1
    assert summary['still_due']['amount'] == '180.00'
    assert summary['credit']['amount'] == '50.00' and summary['discount']['amount'] == '20.00'
    assert summary['text'] == (
        'Received 1150.00 USD from Adams Plumbing. It was deposited to Checking. Paid in full: 1 invoice (SUM-1). '
        'Invoice SUM-2: 120.00 USD paid, 180.00 USD still due. Early-payment discount taken: 20.00 USD. '
        '50.00 USD left as credit for Adams Plumbing.')
    # Applying 30.00 of that credit to SUM-2 leaves 150.00 due and 20.00 of credit.
    applied = run('payment apply', dict(payment=paid['id'], expected_version=paid['version'], date='2026-03-09',
                                        operation_key='sum-apply', applications=dict(mode='inline', items=[
                                            dict(invoice=second['id'], expected_version=2, amount='30.00')])),
                  reason='Use the credit')
    assert applied['summary']['text'] == (
        "Applied 30.00 USD of Adams Plumbing's credit. Invoice SUM-2: 30.00 USD paid, 150.00 USD still due. "
        '20.00 USD left as credit for Adams Plumbing.')
    # The preview says the same before anything is saved; the full detail is still there.
    assert 'effect' in paid and paid['effect']['applications']


def test_bill_pay_summary_names_each_bill_and_what_it_still_owes(books):
    first = bill(books, '1000.00')    # 980.00 + 20.00 discount settles it
    second = bill(books, '500.00')    # 200.00 leaves 300.00 open
    paid = pay(books, [dict(bill=first['id'], amount='980.00', discount='20.00'),
                       dict(bill=second['id'], amount='200.00')])
    summary = paid['summary']
    assert _rows(summary) == {first['number']: ('980.00', '20.00', '0.00', True),
                              second['number']: ('200.00', None, '300.00', False)}
    assert 'credit' not in summary and summary['discount']['amount'] == '20.00'
    assert summary['text'] == (
        f"Paid 1180.00 USD to Northside Supply. Paid in full: 1 bill ({first['number']}). "
        f"Bill {second['number']}: 200.00 USD paid, 300.00 USD still due. Early-payment discount taken: 20.00 USD.")
