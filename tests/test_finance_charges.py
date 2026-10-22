"""Finance charges (R120): the company policy, the preview, assessing, and the books checked by hand.

The policy throughout, unless a test changes it: 18% a year, a 2.00 minimum, no grace period, credited
to Finance Charge Income, days counted from the due date.

    INV-A 1,000.00 dated 2026-01-01, due 2026-01-31. Assessed 2026-03-02 it is 30 days past due
    (28 days to 2026-02-28, then 2): 1000.00 x 18% x 30 / 365 = 14.7945... -> 14.79.
    From the invoice date it is 60 days: 29.5890... -> 29.59.
    With 400.00 paid on 2026-02-15 the open balance is 600.00: 600 x 18% x 30 / 365 = 8.8767... -> 8.88.
    INV-B 50.00 due 2026-02-20 is 10 days late: 0.2465... is under the minimum, so 2.00.
    Assessed again 2026-04-01 (30 days after the last charge): 14.79 again on INV-A; charging the
    overdue finance charge too adds 14.79 x 18% x 30 / 365 = 0.2188..., and the exact sum
    14.7945... + 0.2188... = 15.0133... -> 15.01.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'fincharge'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Finance charge organization')
    company = client.company.new(legal_name='Late Payers', home_currency='USD', timezone='UTC',
                                 organization='Finance charge organization', chart='general')['company_id']

    def run(name, raw, **context):
        from bookflow.core import registry
        if registry.get(name).is_write:
            context.setdefault('reason', 'finance charge test')
        return client.run(name, raw, company=company, **context)

    accounts = {row['type']: row['id'] for row in client.account.query(company=company, limit=200)['items']}
    income = client.account.create(company=company, name='Fees', type='income')['id']
    fc_income = client.account.create(company=company, name='Finance Charge Income', type='other_income')['id']
    code = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    service = run('item create', dict(name='Work', type='service', sales_enabled=True, description='Work', income_account_id=income,
                                      price='100.00', sales_tax_code_id=code))['id']
    acme = client.customer.create(company=company, name='Acme')['id']
    birch = client.customer.create(company=company, name='Birch')['id']
    methods = {row['name']: row['id'] for row in run('payment-method query', {'limit': 50})['items']}
    b = dict(client=client, company=company, run=run, ar=accounts['accounts_receivable'], bank=accounts['bank'],
             service=service, acme=acme, birch=birch, fc_income=fc_income, check=methods['Check'])
    return b


def _policy(books, **changes):
    values = dict(finance_charge_annual_rate='18', finance_charge_minimum='2.00',
                  finance_charge_account_id='Finance Charge Income')
    values.update(changes)
    return books['run']('company update', values)


def _invoice(books, customer, number, amount, date, due):
    return books['run']('invoice post', dict(customer=customer, date=date, number=number, due_date=due,
                                             lines=[dict(item=books['service'], quantity='1', unit_price=amount)]))


def _pay(books, customer, amount, date, invoice=None, key='pay'):
    items = [dict(invoice=invoice['id'], amount=amount, expected_version=invoice['version'])] if invoice else []
    return books['run']('payment receive', dict(customer=customer, date=date, amount=amount, operation_key=key,
                                                deposit_to=books['bank'], payment_method=books['check'],
                                                applications={'mode': 'inline', 'items': items}))


def _assess(books, date, dry_run=False, **extra):
    return books['run']('finance-charge assess', dict(date=date, **extra), dry_run=dry_run)


def _row(result, customer):
    return next(row for row in result['rows'] if row['customer_id'] == customer)


def _ar_balance(books, date):
    page = books['run']('report trial-balance', dict(date_to=date))
    return {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units'] for row in page['rows']}


def test_off_until_the_policy_is_set_and_the_preference_is_checked(books):
    _invoice(books, books['acme'], 'INV-A', '1000.00', '2026-01-01', '2026-01-31')
    with pytest.raises(BookflowError) as off:
        _assess(books, '2026-03-02', dry_run=True)
    assert off.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError) as wrong:
        books['run']('company update', dict(finance_charge_account_id=books['ar']))
    assert wrong.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError) as rate:
        books['run']('company update', dict(finance_charge_annual_rate='120'))
    assert rate.value.code == 'E_VALIDATION'
    _policy(books, finance_charge_annual_rate='18.000')
    info = books['run']('company show', {})['info']
    assert (info['finance_charge_annual_rate'], info['finance_charge_minimum'], info['finance_charge_account_id'],
            info['finance_charge_grace_days'], info['finance_charge_from']) == ('18', '2.00', books['fc_income'], 0, 'due_date')


def test_rate_minimum_and_grace_on_the_preview(books):
    _invoice(books, books['acme'], 'INV-A', '1000.00', '2026-01-01', '2026-01-31')
    _invoice(books, books['birch'], 'INV-B', '50.00', '2026-01-21', '2026-02-20')
    _policy(books)
    preview = _assess(books, '2026-03-02', dry_run=True)
    acme, birch = _row(preview, books['acme']), _row(preview, books['birch'])
    assert (acme['status'], acme['overdue_balance']['amount'], acme['computed']['amount']) == ('will_assess', '1000.00', '14.79')
    assert acme['documents'][0]['days'] == 30 and acme['last_finance_charge_date'] is None
    assert birch['computed']['amount'] == '2.00' and birch['documents'][0]['days'] == 10  # 0.25 -> the minimum
    assert preview['total']['amount'] == '16.79' and preview['assessed_count'] == 2
    assert books['run']('statement-charge query', {})['items'] == []  # a preview writes nothing
    # A grace period decides only whether a document is charged; the days still start at the due date.
    _policy(books, finance_charge_grace_days=10)
    graced = _assess(books, '2026-03-02', dry_run=True)
    assert [row['customer_id'] for row in graced['rows']] == [books['acme']]
    assert _row(graced, books['acme'])['computed']['amount'] == '14.79'
    _policy(books, finance_charge_grace_days=30)
    assert _assess(books, '2026-03-02', dry_run=True)['rows'] == []


def test_from_the_invoice_date(books):
    _invoice(books, books['acme'], 'INV-A', '1000.00', '2026-01-01', '2026-01-31')
    _policy(books, finance_charge_from='invoice_date')
    row = _row(_assess(books, '2026-03-02', dry_run=True), books['acme'])
    assert row['documents'][0]['days'] == 60 and row['computed']['amount'] == '29.59'


def test_a_partial_payment_lowers_the_balance_and_unapplied_credit_is_flagged_not_subtracted(books):
    invoice = _invoice(books, books['acme'], 'INV-A', '1000.00', '2026-01-01', '2026-01-31')
    _pay(books, books['acme'], '400.00', '2026-02-15', invoice)
    _pay(books, books['acme'], '100.00', '2026-02-20', key='unapplied')
    _policy(books)
    row = _row(_assess(books, '2026-03-02', dry_run=True), books['acme'])
    assert (row['overdue_balance']['amount'], row['computed']['amount']) == ('600.00', '8.88')
    assert row['has_unapplied_credits'] and row['unapplied_credits']['amount'] == '100.00'


def test_assessing_posts_a_statement_charge_ages_it_and_shows_it_on_the_statement(books):
    _invoice(books, books['acme'], 'INV-A', '1000.00', '2026-01-01', '2026-01-31')
    _invoice(books, books['birch'], 'INV-B', '50.00', '2026-01-21', '2026-02-20')
    _policy(books)
    before = _ar_balance(books, '2026-03-02')
    preview = _assess(books, '2026-03-02', dry_run=True)
    done = _assess(books, '2026-03-02', customers=[dict(customer='Acme')],
                   expected_facts_fingerprint=preview['facts_fingerprint'])
    acme, birch = _row(done, books['acme']), _row(done, books['birch'])
    assert acme['status'] == 'assessed' and acme['transaction_id'] and birch['status'] == 'not_selected'
    assert done['item'] == 'Finance Charge' and done['total']['amount'] == '14.79'
    after = _ar_balance(books, '2026-03-02')
    moved = {key: after.get(key, 0) - before.get(key, 0) for key in set(before) | set(after) if after.get(key, 0) != before.get(key, 0)}
    assert moved == {books['ar']: 1479, books['fc_income']: -1479}
    charge = books['run']('statement-charge show', {'statement_charge': acme['transaction_id']})
    assert charge['date'] == '2026-03-02' and charge['total']['amount'] == '14.79'
    aging = books['run']('report ar-aging', dict(as_of='2026-03-02'))
    assert next(r for r in aging['rows'] if r['customer_id'] == books['acme'])['total']['amount'] == '1014.79'
    statement = books['run']('report statement', dict(customer='Acme', date_from='2026-03-01', date_to='2026-03-31'))
    assert any(r['entry'] == 'statement_charge' and r['amount']['amount'] == '14.79' and r['memo'] == 'Finance charge'
               for r in statement['rows'])
    # Assessing the same date again charges nobody twice; Birch, never charged, still can be.
    again = _assess(books, '2026-03-02', customers=[dict(customer='Acme')])
    assert _row(again, books['acme'])['status'] == 'already_assessed' and again['assessed_count'] == 0
    assert _ar_balance(books, '2026-03-02') == after
    # A changed amount is what is charged.
    changed = _assess(books, '2026-03-02', customers=[dict(customer='Birch', amount='1.50')])
    assert _row(changed, books['birch'])['charge']['amount'] == '1.50' and _row(changed, books['birch'])['computed']['amount'] == '2.00'


def test_the_next_charge_starts_at_the_last_and_charges_on_charges_only_when_told(books):
    _invoice(books, books['acme'], 'INV-A', '1000.00', '2026-01-01', '2026-01-31')
    _policy(books)
    _assess(books, '2026-03-02')
    plain = _row(_assess(books, '2026-04-01', dry_run=True), books['acme'])
    assert plain['last_finance_charge_date'] == '2026-03-02' and plain['computed']['amount'] == '14.79'
    assert [doc['days'] for doc in plain['documents']] == [30]
    _policy(books, finance_charge_on_overdue_charges=True)
    compound = _row(_assess(books, '2026-04-01', dry_run=True), books['acme'])
    assert compound['computed']['amount'] == '15.01' and compound['overdue_balance']['amount'] == '1014.79'
    assert sorted(doc['finance_charge'] for doc in compound['documents']) == [False, True]


def test_a_closed_period_is_refused(books):
    _invoice(books, books['acme'], 'INV-A', '1000.00', '2026-01-01', '2026-01-31')
    _policy(books)
    books['run']('company update', dict(closing_date='2026-03-31'))
    with pytest.raises(BookflowError) as closed:
        _assess(books, '2026-03-02')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    assert books['run']('statement-charge query', {})['items'] == []
