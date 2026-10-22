"""Fixed-asset depreciation, disposal and loans (R119), against hand-worked figures.

Straight line, monthly. Delivery van, cost 10,000.00, salvage 1,000.00, in service 2026-01-15,
36 months. Each month takes its share of what remains, rounded half to even:
9,000.00 / 36 = 250.00 every month; 36 x 250.00 = 9,000.00 exactly.

An uneven straight line: Laptop, cost 1,000.00, no salvage, 3 yearly periods.
Y1 1,000.00 x 12/36 = 333.333... -> 333.33; Y2 666.67 x 12/24 = 333.335 -> 333.34 (half to even
of 33333.5 is 33334); Y3 takes the rest, 333.33. Sum 1,000.00.

Double declining, yearly, with the switch. Press, cost 10,000.00, no salvage, 60 months (rate 40%):
    Y1 DB 4,000.00 vs SL 2,000.00 -> 4,000.00   book 6,000.00
    Y2 DB 2,400.00 vs SL 1,500.00 -> 2,400.00   book 3,600.00
    Y3 DB 1,440.00 vs SL 1,200.00 -> 1,440.00   book 2,160.00
    Y4 DB   864.00 vs SL 1,080.00 -> 1,080.00   (switch to straight line) book 1,080.00
    Y5 the rest                   -> 1,080.00   book 0.00
With salvage 1,000.00 it never switches: 4,000.00, 2,400.00, 1,440.00, 864.00, then the rest 296.00.

The loan: 25,000.00 at 6.5% over 60 monthly payments. r = 0.065/12. The level payment
25,000 x r / (1 - (1+r)^-60) = 489.1505... -> 489.15. Payment 1: interest 25,000.00 x r =
135.4166... -> 135.42, principal 353.73, balance 24,646.27. Payment 2: interest 24,646.27 x r =
133.5006... -> 133.50, principal 355.65, balance 24,290.62. The 60th payment pays exactly what is
left, 486.71 principal plus 2.64 interest: 489.35.
"""
from decimal import ROUND_HALF_EVEN, Decimal

import pytest

import bookflow
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'assets'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Asset organization')
    company = client.company.new(legal_name='Asset Books', home_currency='USD', timezone='UTC',
                                 organization='Asset organization', chart='general')['company_id']

    def run(name, raw, **context):
        from bookflow.core import registry
        if registry.get(name).is_write:
            context.setdefault('reason', 'fixed asset test')
        return client.run(name, raw, company=company, **context)

    bank = next(row['id'] for row in client.account.query(company=company, limit=200)['items'] if row['type'] == 'bank')
    make = lambda name, kind: client.account.create(company=company, name=name, type=kind)['id']  # noqa: E731
    acc = dict(bank=bank, equipment=make('Equipment', 'fixed_asset'), accum=make('Accumulated Depreciation', 'fixed_asset'),
               expense=make('Depreciation Expense', 'expense'), gain=make('Gain or Loss on Assets', 'other_income'),
               loan=make('Truck Loan', 'long_term_liability'), interest=make('Interest Paid', 'expense'),
               escrow=make('Escrow', 'other_current_asset'))

    def asset(name, cost, date='2026-01-15'):
        item = run('item create', dict(name=name, type='fixed_asset', asset_number=name.upper()[:8], description=name, purchase_date=date,
                                       original_cost=cost, asset_account_id=acc['equipment'], disposal_status='in_service',
                                       depreciation_method='straight_line',
                                       accumulated_depreciation_account_id=acc['accum'],
                                       depreciation_expense_account_id=acc['expense'], gain_loss_account_id=acc['gain']))
        run('journal post', dict(date=date, lines=[dict(account=acc['equipment'], side='debit', amount=cost),
                                                   dict(account=bank, side='credit', amount=cost)]))
        return item['id']

    return dict(client=client, company=company, run=run, acc=acc, asset=asset)


def _amounts(schedule):
    return [row['amount']['amount'] for row in schedule['rows']]


# --------------------------------------------------------------------------------- depreciation

def test_straight_line_monthly_sums_exactly_and_uneven_yearly(books):
    run = books['run']
    van = books['asset']('Delivery van', '10000.00')
    out = run('fixed-asset setup', dict(item=van, method='straight_line', salvage='1000.00', life_months=36))
    rows = out['schedule']['rows']
    assert len(rows) == 36 and rows[0]['start'] == '2026-01-01' and rows[0]['end'] == '2026-01-31'
    assert set(_amounts(out['schedule'])) == {'250.00'} and rows[-1]['book_value']['amount'] == '1000.00'
    assert all(row['status'] == 'pending' for row in rows)
    laptop = books['asset']('Laptop', '1000.00')
    sched = run('fixed-asset setup', dict(item=laptop, method='straight_line', life_months=36, period='year'))['schedule']
    assert _amounts(sched) == ['333.33', '333.34', '333.33'] and [r['end'] for r in sched['rows']] == ['2026-12-31', '2027-12-31', '2028-12-31']


def test_double_declining_switches_to_straight_line(books):
    run = books['run']
    press = books['asset']('Press', '10000.00')
    sched = run('fixed-asset setup', dict(item=press, method='declining_balance', declining_percent=200, life_months=60,
                                          period='year'))['schedule']
    assert _amounts(sched) == ['4000.00', '2400.00', '1440.00', '1080.00', '1080.00']
    assert [r['basis'] for r in sched['rows']] == ['declining_balance'] * 3 + ['straight_line'] * 2
    sched = run('fixed-asset setup', dict(item=press, salvage='1000.00'))['schedule']
    assert _amounts(sched) == ['4000.00', '2400.00', '1440.00', '864.00', '296.00']
    assert sched['version'] == 2 and sched['rows'][-1]['book_value']['amount'] == '1000.00'
    one_fifty = books['asset']('Lathe', '10000.00')
    sched = run('fixed-asset setup', dict(item=one_fifty, method='declining_balance', declining_percent=150, life_months=60,
                                          period='year'))['schedule']
    # 30% a year: 3,000.00, 2,100.00, then 4,900 x 30% = 1,470.00 vs 4,900 / 3 = 1,633.33 -> switch.
    assert _amounts(sched) == ['3000.00', '2100.00', '1633.33', '1633.34', '1633.33']


def test_depreciation_posts_once_preview_first_and_void_reopens(books):
    run, acc = books['run'], books['acc']
    van = books['asset']('Delivery van', '10000.00')
    run('fixed-asset setup', dict(item=van, method='straight_line', salvage='1000.00', life_months=36))
    preview = run('fixed-asset depreciate', dict(through='2026-03-31'), dry_run=True)
    assert preview['periods_posted'] == 3 and len(preview['entries']) == 3 and preview['total']['amount'] == '750.00'
    assert run('fixed-asset schedule', dict(item=van))['pending_periods'] == 36  # the preview wrote nothing
    done = run('fixed-asset depreciate', dict(through='2026-03-31'))
    assert [e['date'] for e in done['entries']] == ['2026-01-31', '2026-02-28', '2026-03-31']
    again = run('fixed-asset depreciate', dict(through='2026-03-31'))
    assert again['periods_posted'] == 0 and again['entries'] == []
    sched = run('fixed-asset schedule', dict(item=van))
    assert [r['status'] for r in sched['rows'][:4]] == ['posted', 'posted', 'posted', 'pending']
    assert sched['accumulated_posted']['amount'] == '750.00' and sched['book_value']['amount'] == '9250.00'
    entry = run('journal show', dict(journal=done['entries'][1]['transaction_id']))
    lines = {(line['account_id'], line['side']): line['amount']['amount'] for line in entry['revision']['lines']}
    assert entry['status'] == 'posted' and lines == {(acc['expense'], 'debit'): '250.00', (acc['accum'], 'credit'): '250.00'}
    run('journal void', dict(journal=done['entries'][1]['transaction_id']))
    sched = run('fixed-asset schedule', dict(item=van))
    assert [r['status'] for r in sched['rows'][:3]] == ['posted', 'pending', 'posted']
    redo = run('fixed-asset depreciate', dict(through='2026-03-31', entry='combined'))
    assert redo['periods_posted'] == 1 and redo['entries'][0]['date'] == '2026-03-31'
    assert redo['entries'][0]['lines'][0]['periods'] == [2]
    assert run('fixed-asset list', {})['items'][0]['accumulated_posted']['amount'] == '750.00'


def test_combined_entry_and_closed_period(books):
    run = books['run']
    van = books['asset']('Delivery van', '10000.00')
    press = books['asset']('Press', '10000.00')
    run('fixed-asset setup', dict(item=van, method='straight_line', salvage='1000.00', life_months=36))
    run('fixed-asset setup', dict(item=press, method='declining_balance', declining_percent=200, life_months=60, period='year'))
    run('company update', dict(closing_date='2026-02-28'))
    with pytest.raises(BookflowError) as refused:
        run('fixed-asset depreciate', dict(through='2026-12-31'))
    assert refused.value.code == 'E_PERIOD_CLOSED'
    out = run('fixed-asset depreciate', dict(through='2026-12-31', entry='combined'))
    assert len(out['entries']) == 1 and out['entries'][0]['date'] == '2026-12-31'
    assert {line['item']: line['amount']['amount'] for line in out['entries'][0]['lines']} == {'Delivery van': '3000.00', 'Press': '4000.00'}
    assert out['periods_posted'] == 13


def test_disposal_gain_and_loss_and_stop(books):
    run, acc = books['run'], books['acc']
    van = books['asset']('Delivery van', '10000.00')
    run('fixed-asset setup', dict(item=van, method='straight_line', salvage='1000.00', life_months=36))
    with pytest.raises(BookflowError) as early:
        run('fixed-asset dispose', dict(item=van, date='2026-06-15', proceeds='9000.00', deposit_to=acc['bank']))
    assert early.value.code == 'E_VALIDATION'
    run('fixed-asset depreciate', dict(through='2026-05-31'))
    # Book value 10,000 - 5 x 250 = 8,750.00; sold for 9,000.00 less 100.00 costs: gain 150.00.
    out = run('fixed-asset dispose', dict(item=van, date='2026-06-15', proceeds='9000.00', costs='100.00', deposit_to=acc['bank']))
    assert out['gain']['amount'] == '150.00' and out['accumulated']['amount'] == '1250.00'
    sides = {line['account']: ('debit', line['debit']['amount']) if line['debit'] else ('credit', line['credit']['amount'])
             for line in out['lines']}
    bank_name = books['client'].account.show(company=books['company'], account=acc['bank'])['full_name']
    assert sides == {bank_name: ('debit', '8900.00'), 'Accumulated Depreciation': ('debit', '1250.00'),
                     'Equipment': ('credit', '10000.00'), 'Gain or Loss on Assets': ('credit', '150.00')}
    balances = {a: books['client'].account.show(company=books['company'], account=acc[a])['balance']['amount']
                for a in ('equipment', 'accum')}
    assert balances == {'equipment': '0.00', 'accum': '0.00'}  # the asset and its depreciation are gone
    sched = run('fixed-asset schedule', dict(item=van))
    assert sched['status'] == 'disposed' and sched['disposal']['gain']['amount'] == '150.00'
    assert {r['status'] for r in sched['rows'][5:]} == {'stopped'}
    assert run('fixed-asset depreciate', dict(through='2027-12-31'))['periods_posted'] == 0
    item = run('item show', dict(item=van))
    assert item['disposal_status'] == 'sold' and item['disposal_date'] == '2026-06-15'
    # A scrapped asset with no proceeds is a loss of its book value.
    press = books['asset']('Press', '10000.00')
    run('fixed-asset setup', dict(item=press, method='declining_balance', declining_percent=200, life_months=60, period='year'))
    run('fixed-asset depreciate', dict(through='2026-12-31'))
    scrapped = run('fixed-asset dispose', dict(item=press, date='2027-03-01'))
    assert scrapped['gain']['amount'] == '-6000.00' and [line['debit']['amount'] for line in scrapped['lines'] if line['account'] == 'Gain or Loss on Assets'] == ['6000.00'] and run('item show', dict(item=press))['disposal_status'] == 'disposed'


# ------------------------------------------------------------------------------------------- loans

def _hand_schedule(principal, rate, n, payment):
    r = Decimal(rate) / 100 / 12
    balance, rows = Decimal(principal), []
    for k in range(1, n + 1):
        interest = (balance * r).quantize(Decimal('0.01'), ROUND_HALF_EVEN)
        part = balance if k == n else Decimal(payment) - interest
        balance -= part
        rows.append((str(part), str(interest), str(balance)))
    return rows


@pytest.fixture
def loan(books):
    run, acc = books['run'], books['acc']
    run('journal post', dict(date='2026-01-01', lines=[dict(account=acc['bank'], side='debit', amount='25000.00'),
                                                       dict(account=acc['loan'], side='credit', amount='25000.00')]))
    lender = books['client'].vendor.create(company=books['company'], name='First Bank')['id']
    made = run('loan create', dict(name='Truck loan', liability_account=acc['loan'], interest_account=acc['interest'],
                                   principal='25000.00', annual_rate='6.5', term_payments=60, first_payment_date='2026-02-01',
                                   lender=lender, payment_account=acc['bank']))
    return made


def test_loan_schedule_matches_hand_worked(books, loan):
    shown = loan['loan']
    assert shown['loan']['payment']['amount'] == '489.15' and shown['loan']['ties']
    rows = shown['rows']
    hand = _hand_schedule('25000.00', '6.5', 60, '489.15')
    assert [(r['principal']['amount'], r['interest']['amount'], r['balance']['amount']) for r in rows] == hand
    assert rows[0]['interest']['amount'] == '135.42' and rows[0]['principal']['amount'] == '353.73'
    assert rows[1]['interest']['amount'] == '133.50' and rows[-1]['total']['amount'] == '489.35'
    assert rows[-1]['due_date'] == '2031-01-01' and shown['loan']['maturity_date'] == '2031-01-01'
    assert sum(Decimal(r['principal']['amount']) for r in rows) == Decimal('25000.00')


def test_loan_payment_split_extra_principal_and_balance_sheet_tie(books, loan):
    run, acc = books['run'], books['acc']
    preview = run('loan pay', dict(loan='Truck loan'), dry_run=True)
    assert preview['principal']['amount'] == '353.73' and preview['transaction_id'] is None
    paid = run('loan pay', dict(loan='Truck loan'))
    assert paid['document_type'] == 'check' and paid['date'] == '2026-02-01'
    check = run('check show', dict(check=paid['transaction_id']))
    assert check['document']['amount']['amount'] == '489.15'
    shown = run('loan show', dict(loan='Truck loan'))
    assert shown['loan']['balance']['amount'] == '24646.27' and shown['loan']['ties']
    assert shown['loan']['liability_balance']['amount'] == '24646.27'
    # Payment 2 with 1,000.00 extra principal: interest 133.50, principal 355.65 + 1,000.00.
    extra = run('loan pay', dict(loan='Truck loan', extra_principal='1000.00'))
    assert extra['principal']['amount'] == '1355.65' and extra['interest']['amount'] == '133.50'
    shown = run('loan show', dict(loan='Truck loan'))
    assert shown['loan']['balance']['amount'] == '23290.62' and shown['loan']['ties']
    # Same payment, fewer payments: interest on the lower balance, the loan ends before payment 60.
    nxt = shown['loan']['next_payment']
    assert nxt['number'] == 3 and nxt['interest']['amount'] == '126.16'  # 23,290.62 x r = 126.157...
    assert shown['loan']['remaining_count'] < 58
    # An extra payment outside the schedule leaves payment 3 due.
    run('loan pay', dict(loan='Truck loan', extra_only=True, extra_principal='290.62', date='2026-03-10'))
    shown = run('loan show', dict(loan='Truck loan'))
    assert shown['loan']['balance']['amount'] == '23000.00' and shown['loan']['next_payment']['number'] == 3 and shown['loan']['ties']
    # Voiding a payment's check puts it back in the schedule.
    run('check void', dict(check=extra['transaction_id']))
    shown = run('loan show', dict(loan='Truck loan'))
    assert shown['loan']['next_payment']['number'] == 2 and shown['loan']['ties']


def test_loan_escrow_balloon_and_closed_period(books):
    run, acc = books['run'], books['acc']
    made = run('loan create', dict(name='Building', liability_account=acc['loan'], interest_account=acc['interest'],
                                   principal='100000.00', annual_rate='6', term_payments=12, first_payment_date='2026-02-01',
                                   balloon='50000.00', escrow='200.00', escrow_account=acc['escrow']))
    rows = made['loan']['rows']
    assert len(rows) == 12 and rows[-1]['balance']['amount'] == '0.00'
    assert Decimal(rows[-1]['principal']['amount']) > Decimal('50000.00')
    assert rows[0]['interest']['amount'] == '500.00' and rows[0]['escrow']['amount'] == '200.00'
    paid = run('loan pay', dict(loan='Building', from_account=acc['bank']))
    assert paid['escrow']['amount'] == '200.00'
    run('company update', dict(closing_date='2026-02-28'))
    with pytest.raises(BookflowError) as closed:
        run('loan pay', dict(loan='Building', date='2026-02-15', from_account=acc['bank']))
    assert closed.value.code == 'E_PERIOD_CLOSED', closed.value.details
