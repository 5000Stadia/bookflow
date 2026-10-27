"""R183 trial fixes for everyday use: service items on books without sales tax, bank payments with no
cheque number, and reports that default to today."""
import pytest

from bookflow.core.errors import BookflowError


@pytest.fixture
def books(client):
    org = client.run('organization new', {'name': 'Trial Org'}, reason='t')
    made = client.run('company new', {'organization': org['organization_id'], 'legal_name': 'Trial Lawn',
                                      'home_currency': 'USD', 'chart': 'service'}, reason='t')
    company = made['company_id']

    def run(name, data=None, **ctx):
        return client.run(name, data or {}, company=company, reason='t' if ctx.pop('write', False) else None, **ctx)
    accounts = run('account list')['items']
    pick = lambda kind: next(a['id'] for a in accounts if a['type'] == kind)
    return run, pick


def test_a_service_item_on_books_without_sales_tax_needs_only_a_name_and_income(books):
    run, pick = books
    item = run('item create', {'name': 'Cleanup', 'type': 'service', 'sales_enabled': True,
                               'income_account_id': pick('income')}, write=True)
    assert item['price']['amount'] == '0.00' and item['description'] == 'Cleanup' and item['sales_tax_code'] == 'Non'
    # What is given is kept.
    priced = run('item create', {'name': 'Mowing', 'type': 'service', 'sales_enabled': True, 'price': '45',
                                 'description': 'Weekly mowing', 'income_account_id': pick('income')}, write=True)
    assert priced['price']['amount'] == '45.00' and priced['description'] == 'Weekly mowing'


def test_debit_card_purchases_post_with_no_cheque_number(books):
    run, pick = books
    bank, expense = pick('bank'), pick('expense')
    posted = []
    for _ in range(2):   # a second one is not a duplicate number: there is none
        posted.append(run('check post', {'account': bank, 'date': '2026-10-20', 'amount': '12.00', 'no_number': True,
                                         'expenses': [{'account': expense, 'amount': '12.00'}]}, write=True))
    for check in posted:
        assert check['document'].get('check_number') is None and check['document'].get('print_status') is None
    fixed = run('check update', {'check': posted[0]['id'], 'memo': 'Fuel', 'expected_version': posted[0]['version']}, write=True)
    assert fixed['document'].get('check_number') is None, 'a correction does not hand it a cheque number'
    numbered = run('check post', {'account': bank, 'date': '2026-10-21', 'amount': '5.00',
                                  'expenses': [{'account': expense, 'amount': '5.00'}]}, write=True)
    assert numbered['document']['check_number'], 'a real cheque still takes the next number'
    with pytest.raises(BookflowError) as refused:
        run('check post', {'account': bank, 'date': '2026-10-20', 'amount': '1.00', 'no_number': True, 'number': '77',
                           'expenses': [{'account': expense, 'amount': '1.00'}]}, write=True)
    assert refused.value.code == 'E_VALIDATION'


def test_reports_and_the_receipt_list_default_to_today(books):
    run, pick = books
    from bookflow.core import clock
    today = clock.today_iso()
    pl = run('report profit-and-loss', {'date_from': '2026-01-01'})
    assert pl['metadata']['period']['date_to'] in (today, clock.now().date().isoformat())
    aging = run('report ar-aging')
    assert aging['metadata']['period']['date_to'] or aging['metadata']
    customer = run('customer create', {'name': 'Maple'}, write=True)
    listed = run('payment invoices', {'customer': customer['id']})
    assert listed['items'] == [] and listed['customer_id'] == customer['id']


def test_a_bank_payment_never_shows_its_document_reference_as_a_check_number(books):
    """R183 run 3: a debit purchase read "post check 2" in the audit and number 2 in find and the register."""
    run, pick = books
    bank, expense = pick('bank'), pick('expense')
    vendor = run('vendor create', {'name': 'Home Depot'}, write=True)['id']
    paid = run('check post', {'account': bank, 'date': '2026-10-20', 'amount': '12.00', 'no_number': True,
                              'pay_to': {'name_type': 'vendor', 'name_id': vendor},
                              'expenses': [{'account': expense, 'amount': '12.00'}]}, write=True)
    cheque = run('check post', {'account': bank, 'date': '2026-10-21', 'amount': '5.00',
                                'expenses': [{'account': expense, 'amount': '5.00'}]}, write=True)
    assert paid['document']['bank_payment'] is True and paid['document']['check_number'] is None
    assert 'bank_payment' not in cheque['document'], 'a real cheque is not a bank payment'
    number = cheque['document']['check_number']

    # The audit: a bank payment by no number, a cheque by the number on its face.
    summaries = {e['request_id']: e['summary'] for e in run('audit list', {'command': 'check post', 'limit': 5})['items']}
    assert sorted(summaries.values()) == sorted(['post bank payment', f'post check {number}'])
    fixed = run('check update', {'check': paid['id'], 'memo': 'Fuel', 'expected_version': paid['version']}, write=True)
    assert fixed['document']['bank_payment'] is True
    [update] = run('audit list', {'command': 'check update', 'limit': 1})['items']
    assert update['summary'] == 'update bank payment'

    # Find: no number, and called a bank payment; its journal reference does not find it as a number.
    found = {row['id']: row for row in run('transaction find', {'types': ['check']})['items']}
    assert found[paid['id']]['number'] is None and found[paid['id']]['type_label'] == 'Bank payment'
    assert found[cheque['id']]['number'] == number and found[cheque['id']]['type_label'] == 'Check'
    # R183 run 4: nor does the check's own result carry it as a top-level number.
    assert paid['number'] is None and run('check show', {'check': paid['id']})['number'] is None
    assert cheque['number'] is not None
    listed_numbers = {row['id']: row['number'] for row in run('check query', {})['items']}
    assert listed_numbers[paid['id']] is None

    # Show and the list carry the flag; the register calls it a bank payment, with no check number.
    assert run('check show', {'check': paid['id']})['document']['bank_payment'] is True
    listed = {row['id']: row['document'] for row in run('check query', {})['items']}
    assert listed[paid['id']].get('bank_payment') is True and not listed[cheque['id']].get('bank_payment')
    rows = {row['transaction_id']: row for row in run('register query', {'account': bank, 'date_from': '2026-10-01',
                                                                          'date_to': '2026-10-31'})['rows']}
    assert rows[paid['id']]['bank_payment'] is True and rows[paid['id']]['check_number'] is None
    reference = rows[paid['id']]['transaction_number']
    assert paid['id'] not in {row['id'] for row in run('transaction find', {'number': reference})['items']}
    assert rows[cheque['id']]['bank_payment'] is False and rows[cheque['id']]['check_number'] == number

    # A cheque saved to be printed has no number yet, but it is a cheque, not a bank payment.
    queued = run('check post', {'account': bank, 'date': '2026-10-22', 'amount': '7.00', 'to_be_printed': True,
                                'expenses': [{'account': expense, 'amount': '7.00'}]}, write=True)
    assert 'bank_payment' not in queued['document']
    [event] = run('audit list', {'command': 'check post', 'limit': 1})['items']
    assert event['summary'] == 'post check to print'
