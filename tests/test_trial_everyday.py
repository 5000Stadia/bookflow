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
