"""Print Checks for sales tax payments and customer refunds (R152 follow-up, co0096).

One bank account, one chequebook, four kinds of cheque saved with Print later: a check, a bill
payment, a sales tax payment and a customer refund. They queue together, take consecutive numbers
from one run, answer "Did checks print OK?" together, and never share a number. The tax payment's
stub names the agency and the period; the refund's names the credit memo it paid out.
"""
import pytest

from bookflow import BookflowError
from tests.test_check_printing import _bill_payment, _check, _pdf, _queue, books  # noqa: F401


@pytest.fixture
def taxed(books, tmp_path):
    import sqlalchemy as sa
    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    run = books['run']
    with open_database(next((tmp_path / 'printing').rglob('company.db')), writable=True) as handle:
        liability = handle.conn.execute(sa.select(c.accounts.c.id).where(
            c.accounts.c.system_role == 'sales_tax_payable')).scalar_one()
        handle.conn.execute(c.company_info.update().values(sales_tax_enabled=True, sales_tax_liability_basis='invoice_date'))
        handle.conn.commit()
    agency = run('vendor create', dict(name='State Revenue Dept', is_tax_agency=True, account_number='ST-4471',
                                       address={'line1': '1 Capitol Way', 'city': 'Springfield', 'state': 'IL',
                                                'postal_code': '62701'}))['id']
    tax = run('item create', dict(name='Six percent', type='sales_tax_item', tax_percent='6', tax_agency_vendor_id=agency,
                                  liability_account_id=liability))['id']
    income = next(row['id'] for row in run('account query', dict(limit=200))['items'] if row['type'] == 'income')
    taxable = next(row['id'] for row in run('sales-tax-code list', {})['items'] if row['taxable'])
    item = run('item create', dict(name='Consulting', type='service', sales_enabled=True, description='Consulting',
                                   income_account_id=income, price='100.00', sales_tax_code_id=taxable))['id']
    customer = run('customer create', dict(name='Acme Retail', billing_address={'line1': '5 Elm St', 'city': 'Peoria',
                                                                         'state': 'IL', 'postal_code': '61602'}))['id']
    run('invoice post', dict(customer=customer, date='2026-03-01', sales_tax_item=tax,
                             lines=[dict(item=item, quantity='5')]))
    return dict(books, agency=agency, tax=tax, item=item, customer=customer)


def _tax_payment(t, amount='12.00', date='2026-03-06', **extra):
    body = dict(agency=t['agency'], date=date, through_date='2026-03-01', funding_account=t['bank'],
                method=t['methods']['check'], amount=amount, to_be_printed=True)
    body.update(extra)
    return t['run']('sales-tax pay', body, reason='Pay sales tax')


def _refund(t, quantity='1', date='2026-03-07', **extra):
    t['memos'] = t.get('memos', 0) + 1
    memo = t['run']('credit-memo post', dict(customer=t['customer'], date='2026-03-02', number=f'CM-{t["memos"]}',
                                             sales_tax_item=t['tax'], lines=[dict(item=t['item'], quantity=quantity)]))
    body = dict(date=date, funding_account=t['bank'], method=t['methods']['check'],
                sources=[dict(credit_memo=memo['id'])], to_be_printed=True)
    body.update(extra)
    return memo, t['run']('customer-refund post', body, reason='Refund the credit')


def _print(t, first=None, **extra):
    body = {'account': t['bank'], **({'first_number': first} if first else {}), **extra}
    return t['run']('check print', body, reason='Print')


def _confirm(t, run, **extra):
    return t['run']('check print-confirm', {'run': run['id'], **extra}, reason='Answered')


def _numbers(t):
    rows = t['run']('register query', dict(account=t['bank'], date_from='2026-01-01', date_to='2026-12-31', limit=200))['rows']
    return {row['transaction_id']: row['check_number'] for row in rows if row.get('check_number')}


def test_four_kinds_queue_print_confirm_and_reprint_from_one_chequebook(taxed):
    t = taxed
    check = _check(t, '40.00')
    _, payment = _bill_payment(t, '300.00')
    tax = _tax_payment(t)
    memo, refund = _refund(t)
    assert tax['check_number'] is None and tax['print_status'] == 'queued'
    assert refund['check_number'] is None and refund['print_status'] == 'queued'
    queue = _queue(t)
    assert [(row['transaction_id'], row['document_type']) for row in queue['checks']] == [
        (check['id'], 'check'), (payment['id'], 'bill_payment'), (tax['id'], 'sales_tax_payment'),
        (refund['id'], 'customer_refund')]
    assert [row['payee_name'] for row in queue['checks'][2:]] == ['State Revenue Dept', 'Acme Retail']

    first = _print(t, '1001')
    assert [(i['check_number'], i['transaction_id']) for i in first['items']] == [
        ('1001', check['id']), ('1002', payment['id']), ('1003', tax['id']), ('1004', refund['id'])]
    assert t['run']('sales-tax payment show', {'payment': tax['id']})['print_status'] == 'in_run'
    # The tax payment jammed: it goes back to the queue, 1003 is spoiled, the rest keep their numbers.
    _confirm(t, first, failed=['1003'])
    assert [row['transaction_id'] for row in _queue(t)['checks']] == [tax['id']]
    assert t['run']('customer-refund show', {'refund': refund['id']})['check_number'] == '1004'
    second = _print(t)
    assert [(i['check_number'], i['transaction_id']) for i in second['items']] == [('1005', tax['id'])]
    _confirm(t, second)
    shown = t['run']('sales-tax payment show', {'payment': tax['id']})
    assert (shown['check_number'], shown['print_status']) == ('1005', 'printed')
    assert [row['id'] for row in t['run']('sales-tax payment query', {'check_number': '1005'})['items']] == [tax['id']]

    # Reprints: the refund under a new number (1004 spoiled), the tax payment with its own.
    again = t['run']('check reprint', {'check': refund['id'], 'new_number': '1010'}, reason='Ink smeared')
    assert again['spoiled_numbers'] == ['1004']
    t['run']('check reprint', {'check': tax['id']}, reason='Copy for the file')
    assert t['run']('customer-refund show', {'refund': refund['id']})['check_number'] == '1010'
    assert [row['id'] for row in t['run']('customer-refund query', {'check_number': '1010'})['items']] == [refund['id']]

    # The register reads every printed number, and no number is on two cheques.
    numbers = _numbers(t)
    assert numbers == {check['id']: '1001', payment['id']: '1002', tax['id']: '1005', refund['id']: '1010'}
    assert len(set(numbers.values())) == 4
    report = t['run']('report missing-checks', {'as_of': '2026-12-31'})
    assert sorted(row['spoiled_number'] for row in report['rows'] if row['kind'] == 'spoiled') == [1003, 1004]
    # The next cheque of any kind takes the next free number.
    assert _check(t, '1.00', to_be_printed=False)['document']['check_number'] == '1011'


def test_guards_hold_for_tax_payments_and_refunds(taxed):
    t = taxed
    # Print later needs a check from a bank account, and no typed number.
    with pytest.raises(BookflowError) as typed:
        _tax_payment(t, check_number='77')
    assert typed.value.code == 'E_VALIDATION' and typed.value.details['fields'][0]['field'] == 'check_number'
    with pytest.raises(BookflowError) as method:
        _refund(t, method=t['methods']['cash'])
    assert method.value.details['fields'][0]['field'] == 'to_be_printed'
    with pytest.raises(BookflowError) as card:
        _tax_payment(t, funding_account=t['run']('account create', {'name': 'Visa', 'type': 'credit_card'})['id'])
    assert card.value.details['fields'][0]['field'] == 'to_be_printed'

    tax = _tax_payment(t)
    _, refund = _refund(t)
    # A queued refund takes its number from the printer; typing one on a correction is refused.
    with pytest.raises(BookflowError) as queued:
        t['run']('customer-refund update', {'refund': refund['id'], 'check_number': '55'}, reason='Number it')
    assert queued.value.details['fields'][0]['field'] == 'check_number'

    run = _print(t, '2001')
    # No void and no correction while the run waits for "Did checks print OK?".
    with pytest.raises(BookflowError) as busy:
        t['run']('sales-tax payment void', {'payment': tax['id']}, reason='Wrong')
    assert busy.value.code == 'E_VALIDATION' and busy.value.details['run'] == run['id']
    for name, body in (('customer-refund update', {'refund': refund['id'], 'memo': 'changed'}),
                       ('customer-refund void', {'refund': refund['id']})):
        with pytest.raises(BookflowError) as held:
            t['run'](name, body, reason='Edit')
        assert held.value.details['run'] == run['id'], name
    _confirm(t, run)

    # Printed: the memo is still correctable and the number rides on the new revision; the amount is on paper.
    corrected = t['run']('customer-refund update', {'refund': refund['id'], 'memo': 'Returned goods'}, reason='Memo')
    assert corrected['check_number'] == '2002' and corrected['revision']['revision_number'] == 2
    with pytest.raises(BookflowError) as paper:
        t['run']('customer-refund update', {'refund': refund['id'], 'sources': [
            {'credit_memo': corrected['revision']['profile']['sources'][0]['credit_memo_id'], 'amount': '50.00'}]},
            reason='Less')
    assert paper.value.details.get('printed') is True
    with pytest.raises(BookflowError) as renumber:
        t['run']('customer-refund update', {'refund': refund['id'], 'check_number': '9'}, reason='Number')
    assert renumber.value.details['fields'][0]['field'] == 'check_number'
    # A void keeps the number; nothing hands 2001 or 2002 out again.
    t['run']('sales-tax payment void', {'payment': tax['id']}, reason='Paid twice')
    assert t['run']('sales-tax payment show', {'payment': tax['id']})['check_number'] == '2001'
    assert _check(t, '1.00', to_be_printed=False)['document']['check_number'] == '2003'

    # Closed periods: a bare run leaves a refund dated in one behind, by name.
    _, old = _refund(t, quantity='2', date='2026-03-20')
    _, new = _refund(t, quantity='3', date='2026-04-02')
    t['run']('company update', {'closing_date': '2026-03-31'}, reason='Close March')
    bare = _print(t)
    assert [i['transaction_id'] for i in bare['items']] == [new['id']]
    assert [row['transaction_id'] for row in bare['skipped']] == [old['id']]
    with pytest.raises(BookflowError) as closed:
        _print(t, checks=[old['id']])
    assert closed.value.code == 'E_PERIOD_CLOSED'


def test_a_number_typed_on_a_refund_is_never_printed_on_another_cheque(taxed):
    t = taxed
    _refund(t, to_be_printed=False, check_number='3001')
    _check(t, '5.00')
    with pytest.raises(BookflowError) as used:
        _print(t, '3001')
    assert used.value.code == 'E_DUPLICATE_NUMBER' and used.value.details['number'] == '3001'
    assert _print(t, '3002')['items'][0]['check_number'] == '3002'


def test_statement_import_matches_a_reprinted_tax_payment_on_its_new_number(taxed):
    t = taxed
    tax = _tax_payment(t, amount='12.00')
    _confirm(t, _print(t, '4001'))
    t['run']('check reprint', {'check': tax['id'], 'new_number': '4020'}, reason='Jammed')

    def imported(number):
        csv = f'Date,Description,Amount,Check\n2026-03-10,CHK,-12.00,{number}\n'
        return t['run']('reconcile import', dict(account=t['bank'], content=csv), reason='Statement')['lines'][0]
    new, old = imported('4020'), imported('4001')
    assert (new['status'], new['reason'], new['movement']['transaction_id']) == (
        'matched', 'same amount and check number', tax['id'])
    assert old['status'] == 'unmatched'


def test_stubs_name_the_agency_and_period_and_the_credit_memo_refunded(taxed):
    t = taxed
    _tax_payment(t, amount='12.00')
    memo, _ = _refund(t)
    run = _print(t, '5001', style='voucher')
    reader = _pdf(t['run']('check print-pdf', {'run': run['id']}))
    assert len(reader.pages) == 2
    tax_page, refund_page = (page.extract_text() for page in reader.pages)
    for text in ('State Revenue Dept', 'Sales tax agency', 'Tax through', '2026-03-01', 'ST-4471', '12.00', '1 Capitol Way'):
        assert text in tax_page, text
    for text in ('Acme Retail', 'Refund of', f'Credit memo {memo["number"]}', '2026-03-02', '106.00', '5 Elm St'):
        assert text in refund_page, text
