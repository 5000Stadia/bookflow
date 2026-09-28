"""V1.5 retest R80: a first reconciliation finishes on an account that holds a voided sales receipt.

The trial's Checking account carried sales receipts that had been voided. A void keeps the
receipt's revision, so its stored effect shares a movement with the active version it replaced,
and `reconcile finish` refused the certificate as an incomplete movement ("The stored statement
effects do not reconcile to the general ledger", with no details) although the preview balanced.

The bank, worked by hand. Adopted at zero on 2020-01-01; statement dated 2026-06-30:

    2026-06-05  sales receipt, deposited to the bank     +100.00
    2026-06-06  sales receipt, corrected 50.00 -> 60.00   +60.00
    2026-06-07  sales receipt, voided                       0.00
                                                        --------
                statement ending balance                 160.00
"""
import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_card_credits import books, charge  # noqa: F401
from tests.test_card_reconciliation import _call, _statement


def _receipt(books, method, date, amount):
    return books['run']('sales-receipt post', dict(
        customer=books['customer'], deposit_to=books['bank'], payment_method=method, date=date,
        lines=[dict(item=books['item'], quantity='1', unit_price=amount)]))


def test_a_first_statement_finishes_over_a_voided_and_a_corrected_receipt(books):
    run = books['run']
    income = run('account create', dict(name='Visit income', type='income'))['id']
    books['customer'] = run('customer create', dict(name='Riverside'))['id']
    exempt = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    books['item'] = run('item create', dict(name='Visit', type='service', sales_enabled=True, description='Visit',
                                            income_account_id=income, price='10.00', sales_tax_code_id=exempt))['id']
    method = run('payment-method create', dict(name='Customer check', kind='check'))['id']
    _receipt(books, method, '2026-06-05', '100.00')
    edited = _receipt(books, method, '2026-06-06', '50.00')
    run('sales-receipt update', dict(sales_receipt=edited['id'], expected_version=edited['version'], lines=[
        dict(line_id=row['line_id'], item=row['item_id'], quantity='1', unit_price='60.00')
        for row in edited['revision']['lines']]), reason='Corrected price')
    voided = _receipt(books, method, '2026-06-07', '25.00')
    run('sales-receipt void', dict(sales_receipt=voided['id'], expected_version=voided['version']),
        reason='Customer canceled')

    opening = _call(books, 'reconcile opening start', dict(
        operation_key=new_id(), account=books['bank'], opening_date='2020-01-01', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='Adopted at zero'), references=[]))
    rows, preview, finished = _statement(books, '2026-06-30', '160.00', opening['draft']['id'],
                                         account=books['bank'])
    assert sorted(row['amount'] for row in rows if row['eligible'] and not row['claimed']) == [6000, 10000]
    assert preview['totals']['difference'] == 0
    assert finished['totals']['ending_balance'] == 16000


def test_a_source_invalid_refusal_says_which_check_failed():
    from bookflow.company.reconciliation_preparation import ReconciliationError, adapter_errors
    from bookflow.company.reconciliation_storage_validation import InvalidStorage
    with pytest.raises(BookflowError) as caught, adapter_errors():
        raise InvalidStorage('incomplete_movement')
    assert isinstance(caught.value, ReconciliationError)
    assert caught.value.details == {'check': 'incomplete_movement'}


def test_a_statement_with_no_opening_names_the_first_movement_and_the_opening_to_start(books):
    """R80 day 5 #8: the refusal told the agent to start an opening but not where the account begins."""
    charge(books, amount='30.00', date='2026-06-10')
    charge(books)                                              # 2026-06-02, the first movement
    with pytest.raises(BookflowError) as refused:
        _call(books, 'reconcile start', dict(operation_key=new_id(), account=books['card'],
                                             statement_date='2026-06-30', ending_balance='150.00'))
    error = refused.value
    assert error.code == 'E_VALIDATION' and error.details['next_command'] == 'reconcile opening start'
    assert error.details['first_movement_date'] == '2026-06-02'
    assert error.details['next_input'] == dict(account=books['card'], opening_date='2026-06-01', entered_balance='0.00')
    assert 'first movement is dated 2026-06-02' in error.message
    # The suggested opening is accepted as given.
    opened = _call(books, 'reconcile opening start', dict(
        operation_key=new_id(), evidence=dict(format=1, statement_reference=None, entered_text='First reconciliation'),
        references=[], **error.details['next_input']))
    assert opened['draft']['header']['opening_date'] == '2026-06-01'
