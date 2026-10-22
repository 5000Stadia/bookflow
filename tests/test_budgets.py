"""Budgets and comparisons (R109): entry, budget vs actual on both bases, comparisons, memorized reports.

The books, worked by hand (fiscal year starts in January):

    2025-02-01  journal  Rent 300.00 paid from Checking
    2025-02-15  journal  Fees 500.00 received into Checking
    2025-03-10  invoice  Acme, Fees 1,000.00, never paid       (accrual only)
    2025-12-20  journal  Fees 250.00 received into Checking
    2026-01-05  journal  Rent 1,200.00 paid from Checking
    2026-01-20  invoice  Acme, Fees 2,000.00                   (accrual: January)
    2026-02-05  payment  Acme pays the 2,000.00 in full        (cash: February)

The budget for FY2026: Fees 1,500.00 every month (fill, copy across); Rent 1,000.00 from January
rising 1% a month, compounding and rounded half to even: 1,000.00, 1,010.00, 1,020.10, ...

Budget vs actual, January and February 2026, by month:

    accrual  Fees  Jan 2,000.00 / 1,500.00 / 500.00 / 133.3   Feb 0.00 / 1,500.00 / -1,500.00 / 0.0
             Rent  Jan 1,200.00 / 1,000.00 / 200.00 / 120.0   Feb 0.00 / 1,010.00 / -1,010.00 / 0.0
             Total Fees 2,000.00 / 3,000.00 / -1,000.00 / 66.7;  Rent 1,200.00 / 2,010.00 / -810.00 / 59.7
             Net income 800.00 / 990.00 / -190.00 / 80.8
    cash     Fees  Jan 0.00 / 1,500.00 / -1,500.00 / 0.0      Feb 2,000.00 / 1,500.00 / 500.00 / 133.3
             (totals as accrual: the 2,000.00 lands inside the range on both bases)

January 1-15 only: Fees 1,500 x 15/31 = 725.806... -> 725.81; Rent 1,000 x 15/31 = 483.870... -> 483.87.

Profit and loss, Jan-Feb 2026 (accrual) against the previous year Jan-Feb 2025: Fees 2,000.00 vs
500.00 (+1,500.00, 300.0%), Rent 1,200.00 vs 300.00 (+900.00, 300.0%). Against the previous period,
Nov-Dec 2025, across the fiscal year boundary: Fees 2,000.00 vs 250.00 (+1,750.00, 700.0%), Rent
1,200.00 vs 0.00 (% blank).
"""
from datetime import date

import pytest

import bookflow
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'budget'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Budget organization')
    company = client.company.new(legal_name='Planned Books', home_currency='USD', timezone='UTC',
                                 organization='Budget organization', chart='general')['company_id']

    def run(name, raw, **context):
        from bookflow.core import registry
        if registry.get(name).is_write:
            context.setdefault('reason', 'budget test')
        return client.run(name, raw, company=company, **context)

    accounts = {row['type']: row['id'] for row in client.account.query(company=company, limit=200)['items']}
    fees = client.account.create(company=company, name='Fees', type='income')['id']
    rent = client.account.create(company=company, name='Office Rent', type='expense')['id']
    code = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    service = run('item create', dict(name='Work', type='service', sales_enabled=True, description='Work',
                                      income_account_id=fees, price='100.00', sales_tax_code_id=code))['id']
    acme = client.customer.create(company=company, name='Acme')['id']
    bank = accounts['bank']
    methods = {row['name']: row['id'] for row in run('payment-method query', {'limit': 50})['items']}

    def journal(day, debit, credit, amount):
        run('journal post', dict(date=day, lines=[dict(account=debit, side='debit', amount=amount),
                                                  dict(account=credit, side='credit', amount=amount)]))

    def invoice(day, number, amount):
        return run('invoice post', dict(customer=acme, date=day, number=number,
                                        lines=[dict(item=service, quantity='1', unit_price=amount)]))

    journal('2025-02-01', rent, bank, '300.00')
    journal('2025-02-15', bank, fees, '500.00')
    invoice('2025-03-10', 'INV-1', '1000.00')
    journal('2025-12-20', bank, fees, '250.00')
    journal('2026-01-05', rent, bank, '1200.00')
    sale = invoice('2026-01-20', 'INV-2', '2000.00')
    run('payment receive', dict(customer=acme, date='2026-02-05', amount='2000.00', operation_key='pay-2',
                                deposit_to=bank, payment_method=methods['Check'],
                                applications={'mode': 'inline', 'items': [dict(invoice=sale['id'], amount='2000.00',
                                                                               expected_version=sale['version'])]}))
    return dict(client=client, company=company, run=run, fees=fees, rent=rent, acme=acme, bank=bank)


def _plan(books):
    run = books['run']
    run('budget create', dict(name='Plan 2026', fiscal_year=2026))
    run('budget fill', dict(budget='Plan 2026', account='Fees', amount='1500.00'))
    run('budget fill', dict(budget='Plan 2026', account='Office Rent', amount='1000.00', change_percent='1'))


def _row(result, account):
    return next(row for row in result['rows'] if row['account_id'] == account)


def _amounts(cells):
    return [cell['amount'] for cell in cells]


def test_entry_fill_set_clear_and_versioning(books):
    run = books['run']
    _plan(books)
    shown = run('budget show', dict(budget='plan 2026'))
    assert shown['months'][0] == '2026-01' and shown['months'][-1] == '2026-12' and shown['kind'] == 'profit_and_loss'
    rent = next(row for row in shown['rows'] if row['account_id'] == books['rent'])
    assert _amounts(rent['amounts'])[:4] == ['1000.00', '1010.00', '1020.10', '1030.30']
    assert all(cell['amount'] == '1500.00' for cell in next(row for row in shown['rows'] if row['account_id'] == books['fees'])['amounts'])
    version = shown['version']
    # Set two months by hand; '' clears one. Amounts are exact: three places are refused, a float never arrives.
    done = run('budget set', dict(budget='Plan 2026', account='Fees', start_month='2026-03', amounts=['1750.25', ''],
                                  expected_version=version))
    fees = next(row for row in done['budget']['rows'] if row['account_id'] == books['fees'])
    assert _amounts(fees['amounts'])[2:5] == ['1750.25', '0.00', '1500.00'] and done['changed_cells'] == 2
    assert done['budget']['version'] == version + 1 and fees['total']['minor_units'] == 1500_00 * 10 + 1750_25
    with pytest.raises(BookflowError) as stale:
        run('budget set', dict(budget='Plan 2026', account='Fees', amounts=['1.00'], expected_version=version))
    assert stale.value.code == 'E_VERSION_CONFLICT'
    with pytest.raises(BookflowError) as precise:
        run('budget set', dict(budget='Plan 2026', account='Fees', amounts=['1.005']))
    assert precise.value.code == 'E_AMOUNT_PRECISION'
    with pytest.raises(BookflowError) as wrong_kind:
        run('budget set', dict(budget='Plan 2026', account=books['bank'], amounts=['1.00']))
    assert wrong_kind.value.code == 'E_VALIDATION'
    # Copy across: no amount takes the month's own value across the rest of the year.
    run('budget fill', dict(budget='Plan 2026', account='Fees', start_month='2026-03'))
    fees = next(row for row in run('budget show', dict(budget='Plan 2026'))['rows'] if row['account_id'] == books['fees'])
    assert _amounts(fees['amounts'])[2:] == ['1750.25'] * 10
    # A dollar change compounds month to month.
    run('budget fill', dict(budget='Plan 2026', account='Office Rent', start_month='2026-10', amount='5000.00', change='100.00'))
    rent = next(row for row in run('budget show', dict(budget='Plan 2026'))['rows'] if row['account_id'] == books['rent'])
    assert _amounts(rent['amounts'])[9:] == ['5000.00', '5100.00', '5200.00']
    # Clear one row, then the rest; every cleared amount is in the audit trail.
    run('budget clear', dict(budget='Plan 2026', account='Office Rent'))
    assert [row['account_id'] for row in run('budget show', dict(budget='Plan 2026'))['rows']] == [books['fees']]
    cleared = run('budget clear', dict(budget='Plan 2026'))
    assert cleared['budget']['rows'] == [] and cleared['budget']['cell_count'] == 0
    events = run('audit list', dict(limit=50))['items']
    assert any(event['command'] == 'budget clear' for event in events)
    # Nothing ever posted: the trial balance has the same lines as before the budget existed.
    assert run('report profit-and-loss', dict(date_from='2026-01-01', date_to='2026-12-31'))['totals']['net_income']['amount'] == '800.00'


def test_budget_vs_actual_by_hand_on_both_bases(books):
    run = books['run']
    _plan(books)
    accrual = run('report budget-vs-actual', dict(budget='Plan 2026', date_from='2026-01-01', date_to='2026-02-28',
                                                  basis='accrual'))
    labels = [column['label'] for column in accrual['columns']]
    assert labels[:4] == ['Jan 2026 · Actual', 'Jan 2026 · Budget', 'Jan 2026 · $ Over Budget', 'Jan 2026 · % of Budget']
    assert labels[-4:] == ['Total · Actual', 'Total · Budget', 'Total · $ Over Budget', 'Total · % of Budget']
    assert _amounts(_row(accrual, books['fees'])['cells']) == [
        '2000.00', '1500.00', '500.00', '133.3', '0.00', '1500.00', '-1500.00', '0.0',
        '2000.00', '3000.00', '-1000.00', '66.7']
    assert _amounts(_row(accrual, books['rent'])['cells']) == [
        '1200.00', '1000.00', '200.00', '120.0', '0.00', '1010.00', '-1010.00', '0.0',
        '1200.00', '2010.00', '-810.00', '59.7']
    net = accrual['totals']['net_income']
    assert (net['actual']['amount'], net['budget']['amount'], net['over_budget']['amount'],
            net['percent_of_budget']['amount']) == ('800.00', '990.00', '-190.00', '80.8')
    # The Actual of the whole range is the profit and loss for the same dates.
    pl = run('report profit-and-loss', dict(date_from='2026-01-01', date_to='2026-02-28', basis='accrual'))
    assert pl['totals']['net_income']['amount'] == net['actual']['amount']

    cash = run('report budget-vs-actual', dict(budget='Plan 2026', date_from='2026-01-01', date_to='2026-02-28',
                                               basis='cash'))
    assert cash['metadata']['basis'] == 'cash'
    assert _amounts(_row(cash, books['fees'])['cells']) == [
        '0.00', '1500.00', '-1500.00', '0.0', '2000.00', '1500.00', '500.00', '133.3',
        '2000.00', '3000.00', '-1000.00', '66.7']
    cash_pl = run('report profit-and-loss', dict(date_from='2026-01-01', date_to='2026-01-31', basis='cash'))
    assert cash_pl['totals']['income']['amount'] == '0.00'

    # Half a month is half a budget, by days, rounded half to even.
    half = run('report budget-vs-actual', dict(budget='Plan 2026', date_from='2026-01-01', date_to='2026-01-15',
                                               columns='total', basis='accrual'))
    assert [column['label'] for column in half['columns']] == [
        'Jan 1 – Jan 15, 2026 · Actual', 'Jan 1 – Jan 15, 2026 · Budget', 'Jan 1 – Jan 15, 2026 · $ Over Budget',
        'Jan 1 – Jan 15, 2026 · % of Budget']
    assert _row(half, books['fees'])['cells'][1]['amount'] == '725.81'
    assert _row(half, books['rent'])['cells'][1]['amount'] == '483.87'

    # Quarters follow the fiscal year; the overview shows the budget alone with net income.
    quarters = run('report budget-vs-actual', dict(budget='Plan 2026', columns='quarter', basis='accrual'))
    assert [column['period'] for column in quarters['columns'][::4]] == [
        'Jan – Mar 2026', 'Apr – Jun 2026', 'Jul – Sep 2026', 'Oct – Dec 2026', 'Total']
    overview = run('report budget-overview', dict(budget='Plan 2026', columns='quarter'))
    assert [line['cells'][0]['amount'] for line in overview['summary'] if line['key'] == 'income'] == ['4500.00']
    assert overview['totals']['income']['budget']['amount'] == '18000.00'


def test_a_merged_away_account_reads_under_its_survivor_in_budget_reports(books):
    # R117 x R109: merging two expense accounts, one budgeted, puts budget and actual on one survivor row.
    run, client, company = books['run'], books['client'], books['company']
    _plan(books)
    other = client.account.create(company=company, name='Rent Expense', type='expense')['id']
    run('journal post', dict(date='2026-01-10', lines=[dict(account=other, side='debit', amount='100.00'),
                                                       dict(account=books['bank'], side='credit', amount='100.00')]))
    run('budget set', dict(budget='Plan 2026', account='Rent Expense', start_month='2026-01', amounts=['50.00']))
    rent_year = next(row for row in run('budget show', dict(budget='Plan 2026'))['rows']
                     if row['account_id'] == books['rent'])['total']['minor_units']
    run('account merge', dict(merged='Office Rent', into='Rent Expense'))
    for basis in ('accrual', 'cash'):
        report = run('report budget-vs-actual', dict(budget='Plan 2026', date_from='2026-01-01', date_to='2026-01-31',
                                                     columns='total', basis=basis))
        assert books['rent'] not in {row['account_id'] for row in report['rows']}
        assert _amounts(_row(report, other)['cells'])[:2] == ['1300.00', '1050.00']
    overview = run('report budget-overview', dict(budget='Plan 2026', columns='total'))
    assert books['rent'] not in {row['account_id'] for row in overview['rows']}
    # Office Rent's twelve budgeted months plus Rent Expense's 50.00 in January.
    assert _row(overview, other)['cells'][0]['minor_units'] == rent_year + 50_00
    # Undoing the merge gives each account its own budget row again.
    run('account unmerge', dict(merged='Office Rent'))
    report = run('report budget-vs-actual', dict(budget='Plan 2026', date_from='2026-01-01', date_to='2026-01-31',
                                                 columns='total', basis='accrual'))
    assert _amounts(_row(report, books['rent'])['cells'])[:2] == ['1200.00', '1000.00']
    assert _amounts(_row(report, other)['cells'])[:2] == ['100.00', '50.00']


def test_create_from_actuals_equals_last_years_profit_and_loss(books):
    run = books['run']
    made = run('budget create', dict(name='From 2025', fiscal_year=2026, from_actuals=True, basis='accrual'))
    rows = {row['account_id']: row for row in made['budget']['rows']}
    assert _amounts(rows[books['fees']]['amounts']) == ['0.00', '500.00', '1000.00'] + ['0.00'] * 8 + ['250.00']
    assert _amounts(rows[books['rent']]['amounts'])[1] == '300.00'
    pl = run('report profit-and-loss', dict(date_from='2025-01-01', date_to='2025-12-31', basis='accrual'))
    by_account = {row['account_id']: row['amount']['minor_units'] for row in pl['rows']}
    assert {account: row['total']['minor_units'] for account, row in rows.items()} == by_account
    assert made['budget']['total']['minor_units'] == pl['totals']['net_income']['minor_units']
    cash = run('budget create', dict(name='Cash 2025', fiscal_year=2026, from_actuals=True, basis='cash'))
    assert books['fees'] in {row['account_id'] for row in cash['budget']['rows']}
    assert next(row for row in cash['budget']['rows'] if row['account_id'] == books['fees'])['total']['amount'] == '750.00'


def test_customer_budget_reads_that_customers_actuals(books):
    run = books['run']
    run('budget create', dict(name='Acme 2026', fiscal_year=2026, by='customer'))
    with pytest.raises(BookflowError) as missing:
        run('budget set', dict(budget='Acme 2026', account='Fees', amounts=['100.00']))
    assert missing.value.code == 'E_VALIDATION'
    run('budget set', dict(budget='Acme 2026', account='Fees', customer='Acme', amounts=['1800.00']))
    report = run('report budget-vs-actual', dict(budget='Acme 2026', date_from='2026-01-01', date_to='2026-01-31',
                                                 customer='Acme', basis='accrual', columns='total'))
    # The invoice names Acme; the rent journal names nobody and is not Acme's.
    assert _amounts(_row(report, books['fees'])['cells']) == ['2000.00', '1800.00', '200.00', '111.1']
    assert books['rent'] not in {row['account_id'] for row in report['rows']}


def test_previous_year_and_previous_period_across_the_year_boundary(books):
    run = books['run']
    year = run('report profit-and-loss-comparison', dict(date_from='2026-01-01', date_to='2026-02-28',
                                                         compare='previous_year', basis='accrual'))
    assert [column['label'] for column in year['columns']] == ['Jan – Feb 2026', 'Jan – Feb 2025', '$ Change', '% Change']
    assert _amounts(_row(year, books['fees'])['cells']) == ['2000.00', '500.00', '1500.00', '300.0']
    assert _amounts(_row(year, books['rent'])['cells']) == ['1200.00', '300.00', '900.00', '300.0']
    period = run('report profit-and-loss-comparison', dict(date_from='2026-01-01', date_to='2026-02-28',
                                                           compare='previous_period', basis='accrual'))
    assert period['columns'][1]['label'] == 'Nov – Dec 2025'
    assert _amounts(_row(period, books['fees'])['cells']) == ['2000.00', '250.00', '1750.00', '700.0']
    assert _amounts(_row(period, books['rent'])['cells']) == ['1200.00', '0.00', '1200.00', None]
    # Each column is the profit and loss for its own dates.
    for (first, last), cells in ((('2026-01-01', '2026-02-28'), 0), (('2025-11-01', '2025-12-31'), 1)):
        pl = run('report profit-and-loss', dict(date_from=first, date_to=last, basis='accrual'))
        assert period['totals']['net_income']['current' if cells == 0 else 'previous']['amount'] == pl['totals']['net_income']['amount']
    only_dollars = run('report profit-and-loss-comparison', dict(date_from='2026-01-01', date_to='2026-02-28',
                                                                 percent_change=False))
    assert [cell['measure'] for cell in only_dollars['rows'][0]['cells']] == ['current', 'previous', 'change']
    sheet = run('report balance-sheet-comparison', dict(date_to='2026-02-28', basis='accrual'))
    for measure, day in (('current', '2026-02-28'), ('previous', '2025-02-28')):
        bs = run('report balance-sheet', dict(date_to=day, basis='accrual'))
        for key in ('assets', 'liabilities', 'total_equity', 'liabilities_and_equity'):
            assert sheet['totals'][key][measure]['amount'] == bs['totals'][key]['amount'], (key, measure)
    assert [column['label'] for column in sheet['columns'][:2]] == ['Feb 28, 2026', 'Feb 28, 2025']


def test_memorized_report_round_trip_with_a_relative_date(books):
    from bookflow.company.memorized_reports import resolve_range
    run = books['run']
    _plan(books)
    made = run('memorized-report create', dict(name='Budget this year', report='budget-vs-actual', group='Company',
                                               filters={'budget': 'Plan 2026', 'columns': 'quarter',
                                                        'date_from': '2020-01-01', 'date_to': '2020-12-31'},
                                               date_range='this_fiscal_year'))['memorized_report']
    assert made['filters'] == {'budget': 'Plan 2026', 'columns': 'quarter'} and made['date_range'] == 'this_fiscal_year'
    run('memorized-report create', dict(name='Fixed P&L', report='profit-and-loss',
                                        filters={'date_from': '2026-01-01', 'date_to': '2026-02-28', 'basis': 'cash'}))
    listed = run('memorized-report list', {})['items']
    assert [item['name'] for item in listed] == ['Fixed P&L', 'Budget this year']  # no group first, then groups
    first, last = resolve_range('this_fiscal_year', date.today(), 1)
    ran = run('memorized-report run', dict(memorized_report='budget this year'))
    assert ran['command'] == 'report budget-vs-actual'
    assert ran['inputs'] == {'budget': 'Plan 2026', 'columns': 'quarter', 'date_from': first.isoformat(),
                             'date_to': last.isoformat()}
    direct = run('report budget-vs-actual', dict(ran['inputs']))
    assert ran['result']['totals'] == direct['totals']
    fixed = run('memorized-report run', dict(memorized_report='Fixed P&L'))
    assert fixed['inputs']['basis'] == 'cash'
    assert fixed['result']['totals']['net_income']['amount'] == '800.00'  # cash: 2,000.00 received Feb 5 less 1,200.00 rent
    renamed = run('memorized-report update', dict(memorized_report='Fixed P&L', name='Cash P&L', group='Banking'))
    assert renamed['memorized_report']['group'] == 'Banking' and renamed['memorized_report']['version'] == 2
    with pytest.raises(BookflowError) as bad:
        run('memorized-report create', dict(name='Nope', report='budget-vs-actual', filters={'columns': 'weekly'}))
    assert bad.value.code == 'E_VALIDATION'
    run('memorized-report delete', dict(memorized_report='Cash P&L'))
    assert [item['name'] for item in run('memorized-report list', {})['items']] == ['Budget this year']


def test_named_ranges_follow_the_fiscal_year():
    from bookflow.company.memorized_reports import resolve_range
    day = date(2026, 5, 14)  # a Thursday; fiscal year from April
    assert resolve_range('this_fiscal_year', day, 4) == (date(2026, 4, 1), date(2027, 3, 31))
    assert resolve_range('last_fiscal_year', day, 4) == (date(2025, 4, 1), date(2026, 3, 31))
    assert resolve_range('this_fiscal_quarter', day, 4) == (date(2026, 4, 1), date(2026, 6, 30))
    assert resolve_range('last_fiscal_quarter', day, 4) == (date(2026, 1, 1), date(2026, 3, 31))
    assert resolve_range('this_month_to_date', day, 4) == (date(2026, 5, 1), day)
    assert resolve_range('last_month', day, 4) == (date(2026, 4, 1), date(2026, 4, 30))
    assert resolve_range('this_week', day, 4) == (date(2026, 5, 10), date(2026, 5, 16))
    assert resolve_range('this_fiscal_year', date(2026, 2, 1), 4) == (date(2025, 4, 1), date(2026, 3, 31))


def test_fill_rounds_half_to_even_and_never_floats():
    from fractions import Fraction
    from bookflow.company.budgets import fill_values
    assert fill_values(500000, 3, percent=Fraction(1)) == [500000, 505000, 510050]
    assert fill_values(5, 3, percent=Fraction(10)) == [5, 6, 7]  # 5.5 -> 6 (even), 6.6 -> 7
    assert all(type(value) is int for value in fill_values(333, 12, percent=Fraction(7, 3)))
