"""A customer's credit memo, as an agent meets it on the way to applying it (blind July trial, week 3).

Kaminski's 150.00 credit memo 2350 was meant for invoice 2397. The agent tried `payment apply` with the
credit memo as the payment, then `payment suggest` in mode existing_credit; every refusal said only
"no such payment" or "existing_credit requires payment", nothing pointed at `customer-credit apply`,
and the credit stayed unapplied. The anchor's Receive Payments shows the customer's credits
(Discounts & Credits); here the payment preparation reads list them with the command that applies them,
and each refusal on that path names it.

The last test walks the July findings of this batch through MCP, which is what the agent sees.
"""
import anyio
import pytest

from bookflow.core.errors import BookflowError
from tests.credit_support import books, goodwill_credit, invoice, worth_version  # noqa: F401
from tests.mcp_matrix_support import Matrix


def _refused(books_, command, raw, code):
    with pytest.raises(BookflowError) as error:
        books_['run'](command, raw, **({'reason': 'Apply the credit'} if command == 'payment apply' else {}))
    assert error.value.code == code, (error.value.code, error.value.details)
    return error.value


def test_every_wrong_turn_toward_a_credit_memo_names_customer_credit_apply(books):
    run = books['run']
    sale = invoice(books, number='2397')
    credit = goodwill_credit(books, '30.00', date='2026-03-10')
    run('credit-memo show', {'credit_memo': credit['id']})
    apply_raw = dict(expected_version=1, date='2026-03-15', operation_key='cm-to-2397', applications=dict(
        mode='inline', items=[dict(invoice=sale['id'], expected_version=1, amount='30.00')]))
    # The credit memo named as the payment, by id and by number.
    for selector in (credit['id'], credit['number']):
        error = _refused(books, 'payment apply', dict(apply_raw, payment=selector), 'E_RECORD_NOT_FOUND')
        assert '`customer-credit apply`' in error.message
        assert error.details['next'] == 'customer-credit apply' and error.details['credit_memo_id'] == credit['id']
    error = _refused(books, 'payment suggest', dict(payment=credit['id'], date='2026-03-15', mode='existing_credit',
                                                    amount='30.00'), 'E_RECORD_NOT_FOUND')
    assert error.details['next'] == 'customer-credit apply'
    # existing_credit with the customer: the refusal says a credit memo is not a payment.
    error = _refused(books, 'payment suggest', dict(customer=books['ridge'], date='2026-03-15', mode='existing_credit',
                                                    amount='30.00'), 'E_VALIDATION')
    assert '`customer-credit apply`' in str(error.details)
    # A number that names nothing is still the plain refusal.
    error = _refused(books, 'payment apply', dict(apply_raw, payment='9999'), 'E_RECORD_NOT_FOUND')
    assert 'next' not in error.details

    # Preparing a receipt for the customer shows the open credit and the command that applies it.
    listed = run('payment invoices', dict(mode='new_receipt', customer=books['ridge'], date='2026-03-15'))
    [open_credit] = listed['available_credits']
    assert open_credit['credit_memo_id'] == credit['id'] and open_credit['available']['minor_units'] == 3000
    assert open_credit['next'] == 'customer-credit apply'
    suggested = run('payment suggest', dict(mode='new_receipt', customer=books['ridge'], date='2026-03-15',
                                            amount='70.00', strategy='exact_then_oldest'))
    assert [row['credit_memo_id'] for row in suggested['available_credits']] == [credit['id']]
    # Another customer's receipt does not see Ridge's credit.
    assert 'available_credits' not in run('payment invoices', dict(mode='new_receipt', customer=books['kerr'],
                                                                   date='2026-03-15'))

    # Doing what it says applies the credit; once used up it is no longer offered.
    run('customer-credit apply', dict(credit_memo=credit['id'], expected_version=worth_version(books, credit['id']),
                                      applications=[dict(invoice=sale['id'], expected_version=run(
                                          'invoice show', {'invoice': sale['id']})['version'], amount='30.00')]),
        reason='Apply the credit')
    assert 'available_credits' not in run('payment invoices', dict(mode='new_receipt', customer=books['ridge'],
                                                                   date='2026-03-15'))


@pytest.mark.timeout(900)
def test_what_the_agent_sees_through_mcp(root, tmp_path):
    """Through MCP, on the demo: the credit memo hint and listing, the bounce's default customer fee,
    and the bill payment that asks for its discount account rather than inventing one."""
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            call = lambda *args, **kw: matrix.call('mcp', *args, **kw)
            customer = 'Credit Correction Example Customer'
            sale = await call('invoice post', {'customer': customer, 'date': '2026-12-01', 'number': 'HINT-2397',
                                               'lines': [{'item': 'Payment Example Labor', 'quantity': '150'}]})
            credit = await call('credit-memo post', {'customer': customer, 'date': '2026-12-01', 'number': 'HINT-2350',
                                                     'lines': [{'item': 'Payment Example Labor', 'quantity': '150'}]})
            wrong = await call('payment apply', {
                'payment': 'HINT-2350', 'expected_version': 1, 'date': '2026-12-02', 'operation_key': 'hint-apply',
                'applications': {'mode': 'inline', 'items': [
                    {'invoice': sale['id'], 'expected_version': sale['version'], 'amount': '150.00'}]}}, rejected=True)
            listed = await call('payment invoices', {'mode': 'new_receipt', 'customer': customer, 'date': '2026-12-02'})

            receipt = await call('payment show', {'payment': 'DEMO-PAY-P2'})
            bounced = await call('payment bounce', {
                'payment': 'DEMO-PAY-P2', 'expected_version': receipt['version'], 'date': '2026-10-20',
                'operation_key': 'hint-bounce', 'customer_fee_amount': '25.00'})
            fee_item = await call('item show', {'item': 'Returned Check Charge'})

            await call('account update', {'account': 'Discounts Taken', 'name': 'Purchase Discounts'})
            bill = await call('bill post', {'vendor': 'Lakeview Pipe Supply', 'number': 'HINT-BILL', 'date': '2026-12-20',
                                            'expenses': [{'account': 'Bank Fees', 'amount': '100.00'}]})
            unasked = await call('bill pay', {'date': '2026-12-22', 'bills': [
                {'bill': bill['id'], 'amount': '98.00', 'discount': '2.00'}], 'funding_account': 'Payment Example Bank',
                'method': 'check'}, rejected=True)
            return credit, wrong, listed, bounced, fee_item, unasked
        finally:
            await matrix.close()

    credit, wrong, listed, bounced, fee_item, unasked = anyio.run(witness)
    assert wrong['code'] == 'E_RECORD_NOT_FOUND' and 'customer-credit apply' in str(wrong), wrong
    assert [row['credit_memo_id'] for row in listed['available_credits']] == [credit['id']]
    assert listed['available_credits'][0]['next'] == 'customer-credit apply'
    assert bounced['customer_fee']['amount']['minor_units'] == 2500
    assert fee_item['type'] == 'other_charge'
    assert unasked['code'] == 'E_VALIDATION', unasked
    [field] = unasked['details']['fields']
    assert field['field'] == 'discount_account' and '"Purchase Discounts"' in field['problem']
