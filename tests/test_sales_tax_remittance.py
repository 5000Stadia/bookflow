"""Remit sales tax to an agency, and check the books by hand.

Every figure asserted here is written out in full at the top of the module, so a reader can add
it up without running anything: a 100.00 taxable sale with two agencies on it, 8% state and 2%
city, remitted to the state in two parts and then one of those parts voided. What the trial
balance, the general ledger and the liability read say after each step is checked against those
figures through the real report commands, never against the remittance's own output.

The one identity everything here rests on: `sales-tax liability` totals the Sales Tax Payable
account's own balance for the same date. Each test that moves money checks it again.
"""
from copy import deepcopy

import pytest
import sqlalchemy as sa

import bookflow
from bookflow.core.errors import BookflowError

# The sale. Everything below is arithmetic on these.
UNIT_PRICE = '25.00'
NET = '100.00'              # 4 * 25.00
STATE_TAX = '8.00'          # 8% of 100.00
CITY_TAX = '2.00'           # 2% of 100.00
TAX = '10.00'               # 8.00 + 2.00
GROSS = '110.00'
# Remitting the state's 8.00 in two goes.
PART = '5.00'
REMAINDER = '3.00'          # 8.00 - 5.00


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    data_root = tmp_path / 'salestax'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Sales tax organization')
    company = client.company.new(legal_name='Sales tax', home_currency='USD', timezone='UTC',
                                 organization='Sales tax organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    database = next(data_root.rglob('company.db'))
    with open_database(database, writable=True) as handle:
        liability = handle.conn.execute(sa.select(c.accounts.c.id).where(
            c.accounts.c.system_role == 'sales_tax_payable')).scalar_one()
        handle.conn.execute(c.company_info.update().values(
            sales_tax_enabled=True, sales_tax_liability_basis='invoice_date'))
        handle.conn.commit()

    accounts = client.account.query(company=company, limit=200)['items']
    receivable = next(row['id'] for row in accounts if row['type'] == 'accounts_receivable')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    expense = next(row['id'] for row in accounts if row['type'] == 'expense')
    card = run('account create', dict(name='Visa Card', type='credit_card'))['id']
    codes = {row['taxable']: row['id'] for row in run('sales-tax-code list', {})['items']}
    state = client.vendor.create(company=company, name='State Tax', is_tax_agency=True)['id']
    city = client.vendor.create(company=company, name='City Tax', is_tax_agency=True)['id']
    plain = client.vendor.create(company=company, name='Plain Vendor')['id']
    eight = run('item create', dict(name='Eight percent', type='sales_tax_item', tax_percent='8',
                                    tax_agency_vendor_id=state, liability_account_id=liability))['id']
    two = run('item create', dict(name='Two percent', type='sales_tax_item', tax_percent='2',
                                  tax_agency_vendor_id=city, liability_account_id=liability))['id']
    group = run('item create', dict(name='State and city', type='sales_tax_group',
                                    members=[{'component_item_id': eight, 'quantity': '1'},
                                             {'component_item_id': two, 'quantity': '1'}]))['id']
    widget = run('item create', dict(name='Widget', type='non_inventory_part', sales_enabled=True,
                                     description='Widget', income_account_id=income,
                                     price=UNIT_PRICE, sales_tax_code_id=codes[True]))['id']
    kerr = client.customer.create(company=company, name='Kerr')['id']
    methods = {row['name']: row['id'] for row in
               run('payment-method query', dict(limit=50))['items']}
    return dict(client=client, company=company, run=run, database=database, receivable=receivable,
                income=income, bank=bank, card=card, expense=expense, liability=liability,
                state=state, city=city, plain=plain, eight=eight, two=two, group=group,
                widget=widget, kerr=kerr, taxable=codes[True], methods=methods)


def _balances(books, date='2026-12-31'):
    """Signed minor units per account from the trial balance, debits positive."""
    report = books['run']('report trial-balance', dict(date_to=date, limit=200))
    rows = {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows']}
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return rows, report


def _liability(books, date='2026-12-31', **extra):
    """What `sales-tax liability` says, per agency label, straight from the report command."""
    report = books['run']('sales-tax liability', dict(as_of=date, limit=50, **extra))
    columns = ('tax_charged', 'tax_credited', 'remitted', 'unattributed', 'balance')
    return ({row['display_agency_label']: {key: row[key]['minor_units'] for key in columns}
             for row in report['rows']},
            {key: report['totals'][key]['minor_units'] for key in columns})


def _ties(books, date='2026-12-31'):
    """The identity this whole feature rests on, re-read through both report commands."""
    balances, _ = _balances(books, date)
    _, totals = _liability(books, date)
    assert totals['balance'] == -balances.get(books['liability'], 0)
    return balances, totals


def _invoice(books, **extra):
    values = dict(customer=books['kerr'], date='2026-03-02', due_date='2026-04-01',
                  sales_tax_item=books['group'],
                  lines=[{'item': books['widget'], 'quantity': '4', 'unit_price': UNIT_PRICE,
                          'tax_code': books['taxable']}])
    values.update(extra)
    return books['run']('invoice post', values, reason='Bill the customer')


def _remit(books, *, idempotency_key=None, **extra):
    values = dict(agency=books['state'], date='2026-04-20', funding_account=books['bank'],
                  method=books['methods']['Check'])
    values.update(extra)
    context = {'idempotency_key': idempotency_key} if idempotency_key else {}
    return books['run']('sales-tax pay', values, reason='Remit the tax', **context)


# ---------------------------------------------------------------- what the liability read says


def test_an_empty_company_owes_nothing_and_still_ties_to_the_account(books):
    balances, totals = _ties(books)
    assert totals == dict(tax_charged=0, tax_credited=0, remitted=0, unattributed=0, balance=0)
    assert balances.get(books['liability'], 0) == 0


def test_a_taxable_invoice_raises_the_liability_by_exactly_the_tax_it_charged(books):
    invoice = _invoice(books)
    assert invoice['total']['amount'] == GROSS and invoice['tax']['amount'] == TAX
    balances, totals = _ties(books)
    # 100.00 of income, 10.00 of tax, 110.00 receivable: the liability is the tax and nothing else.
    assert balances[books['receivable']] == 11000
    assert balances[books['income']] == -10000
    assert balances[books['liability']] == -1000
    rows, _ = _liability(books)
    assert rows['State Tax']['balance'] == 800 and rows['State Tax']['tax_charged'] == 800
    assert rows['City Tax']['balance'] == 200 and rows['City Tax']['tax_charged'] == 200
    assert totals['balance'] == 1000


def test_the_liability_is_read_as_of_a_date_rather_than_today(books):
    _invoice(books)
    _, before = _liability(books, '2026-03-01')
    _, after = _liability(books, '2026-03-02')
    assert before['balance'] == 0 and after['balance'] == 1000


def test_one_agency_can_be_read_alone_without_changing_what_it_is_owed(books):
    _invoice(books)
    rows, totals = _liability(books, agency=books['state'])
    assert set(rows) == {'State Tax'} and totals['balance'] == 800


# ---------------------------------------------------------------- remitting


def test_a_part_remittance_moves_the_liability_and_the_bank_by_the_same_amount(books):
    _invoice(books)
    before, _ = _balances(books)
    payment = _remit(books, amount=PART, check_number='2001')

    assert payment['type'] == 'sales_tax_payment' and payment['status'] == 'posted'
    assert payment['total']['amount'] == PART and payment['agency_id'] == books['state']
    assert payment['liability_at_posting']['amount'] == STATE_TAX
    assert payment['remainder_at_posting']['amount'] == REMAINDER
    assert payment['check_number'] == '2001' and payment['funding_kind'] == 'bank_cash'
    after, totals = _ties(books)
    # 8.00 owed, 5.00 remitted: Sales Tax Payable falls by 5.00 and so does the bank.
    assert after[books['liability']] - before[books['liability']] == 500
    assert after[books['bank']] - before.get(books['bank'], 0) == -500
    assert after[books['receivable']] == before[books['receivable']]
    assert after[books['income']] == before[books['income']]
    rows, _ = _liability(books)
    assert rows['State Tax'] == dict(tax_charged=800, tax_credited=0, remitted=500,
                                     unattributed=0, balance=300)
    assert rows['City Tax']['balance'] == 200
    assert totals['balance'] == 500


def test_remitting_the_rest_leaves_that_agency_owed_nothing_and_the_other_untouched(books):
    _invoice(books)
    _remit(books, amount=PART, check_number='2001')
    rest = _remit(books, date='2026-04-21', check_number='2002')

    assert rest['total']['amount'] == REMAINDER
    balances, totals = _ties(books)
    rows, _ = _liability(books)
    assert rows['State Tax']['balance'] == 0 and rows['State Tax']['remitted'] == 800
    assert rows['City Tax']['balance'] == 200
    assert totals['balance'] == 200 and balances[books['liability']] == -200
    assert balances[books['bank']] == -800


def test_an_omitted_amount_remits_everything_that_agency_is_owed(books):
    _invoice(books)
    payment = _remit(books)
    assert payment['total']['amount'] == STATE_TAX
    rows, _ = _liability(books)
    assert rows['State Tax']['balance'] == 0


def test_a_remittance_answers_the_period_it_names_rather_than_the_day_it_was_paid(books):
    _invoice(books)
    later = _invoice(books, date='2026-05-02', due_date='2026-06-01')
    assert later['tax']['amount'] == TAX
    # Through the end of March the state is owed 8.00; through the payment date it is owed 16.00.
    payment = _remit(books, date='2026-06-10', through_date='2026-03-31')
    assert payment['through_date'] == '2026-03-31'
    assert payment['liability_at_posting']['amount'] == STATE_TAX
    assert payment['total']['amount'] == STATE_TAX
    rows, _ = _liability(books)
    assert rows['State Tax']['balance'] == 800
    _ties(books)


def test_a_card_remittance_raises_the_card_instead_of_lowering_the_bank(books):
    _invoice(books)
    payment = _remit(books, funding_account=books['card'], method=books['methods']['Visa'])
    assert payment['funding_kind'] == 'card_liability'
    balances, _ = _ties(books)
    assert balances[books['card']] == -800 and books['bank'] not in balances


def test_remitting_more_than_is_owed_is_refused_rather_than_posted(books):
    _invoice(books)
    with pytest.raises(BookflowError) as exc:
        _remit(books, amount='99.00')
    assert exc.value.code == 'E_APPLICATION_CAPACITY'
    assert exc.value.details['available_minor_units'] == 800
    assert exc.value.details['requested_minor_units'] == 9900
    _, totals = _ties(books)
    assert totals['balance'] == 1000


def test_an_agency_owed_nothing_cannot_be_remitted_to(books):
    with pytest.raises(BookflowError) as exc:
        _remit(books, amount=PART)
    assert exc.value.code == 'E_APPLICATION_CAPACITY'
    assert exc.value.details['available_minor_units'] == 0


def test_sales_tax_is_remitted_to_a_flagged_agency_and_not_to_any_vendor(books):
    _invoice(books)
    with pytest.raises(BookflowError) as exc:
        _remit(books, agency=books['plain'], amount='1.00')
    assert exc.value.code == 'E_VALIDATION'
    assert exc.value.details['fields'][0]['field'] == 'agency'


def test_a_remittance_is_not_drawn_on_an_expense_or_a_receivable(books):
    _invoice(books)
    for account in (books['expense'], books['receivable'], books['liability']):
        with pytest.raises(BookflowError) as exc:
            _remit(books, amount='1.00', funding_account=account)
        assert exc.value.code == 'E_VALIDATION'
        assert exc.value.details['fields'][0]['field'] == 'funding_account'


def test_a_check_number_belongs_to_a_check_drawn_on_a_bank(books):
    _invoice(books)
    with pytest.raises(BookflowError) as exc:
        _remit(books, amount='1.00', method=books['methods']['Cash'], check_number='9')
    assert exc.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError) as exc:
        _remit(books, amount='1.00', funding_account=books['card'],
               method=books['methods']['Check'], check_number='9')
    assert exc.value.code == 'E_VALIDATION'


def test_a_period_the_closing_date_shut_refuses_the_remittance_and_says_the_date(books):
    _invoice(books)
    books['run']('company update', dict(closing_date='2026-04-30'), reason='Close April')
    with pytest.raises(BookflowError) as exc:
        _remit(books, amount=PART)
    assert exc.value.code == 'E_PERIOD_CLOSED'
    assert exc.value.details == {'date': '2026-04-20', 'closing_date': '2026-04-30'}


# ---------------------------------------------------------------- taking it back


def test_voiding_a_remittance_restores_every_signed_balance_it_changed(books):
    _invoice(books)
    _remit(books, amount=PART, check_number='2001')
    before, before_totals = _ties(books)
    payment = _remit(books, date='2026-04-21', check_number='2002')
    assert payment['total']['amount'] == REMAINDER

    voided = books['run']('sales-tax payment void',
                          dict(payment=payment['id'], expected_version=payment['version']),
                          reason='Wrong bank account')
    assert voided['status'] == 'voided' and voided['void_reason'] == 'Wrong bank account'
    after, after_totals = _ties(books)
    assert after == before
    assert after_totals == before_totals
    rows, _ = _liability(books)
    assert rows['State Tax'] == dict(tax_charged=800, tax_credited=0, remitted=500,
                                     unattributed=0, balance=300)


def test_a_voided_remittance_cannot_be_voided_again_and_keeps_its_number(books):
    _invoice(books)
    payment = _remit(books, amount=PART)
    books['run']('sales-tax payment void', dict(payment=payment['id']), reason='Mistake')
    with pytest.raises(BookflowError) as exc:
        books['run']('sales-tax payment void', dict(payment=payment['id']), reason='Again')
    assert exc.value.code == 'E_APPLICATION_INACTIVE'
    with pytest.raises(BookflowError) as exc:
        _remit(books, amount=PART, number=payment['number'])
    assert exc.value.code == 'E_DUPLICATE_NUMBER'


def test_voiding_needs_a_reason(books):
    _invoice(books)
    payment = _remit(books, amount=PART)
    with pytest.raises(BookflowError) as exc:
        books['client'].run('sales-tax payment void', dict(payment=payment['id']),
                            company=books['company'])
    assert exc.value.code == 'E_REASON_REQUIRED'


def test_a_stale_expected_version_refuses_the_void(books):
    _invoice(books)
    payment = _remit(books, amount=PART)
    with pytest.raises(BookflowError) as exc:
        books['run']('sales-tax payment void',
                     dict(payment=payment['id'], expected_version=payment['version'] + 1),
                     reason='Mistake')
    assert exc.value.code == 'E_VERSION_CONFLICT'


# ---------------------------------------------------------------- what else moves the liability


def test_a_credit_memo_takes_the_agency_liability_back_down(books):
    invoice = _invoice(books)
    line = invoice['revision']['lines'][0]
    credit = books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-03-10',
        lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'],
                'quantity': '1'}]), reason='One unit came back')
    assert credit['tax']['amount'] == '2.50'
    rows, totals = _liability(books)
    _ties(books)
    # floor(800*2500/10000) = 200 of the state's 800; floor(200*2500/10000) = 50 of the city's 200.
    assert rows['State Tax'] == dict(tax_charged=800, tax_credited=200, remitted=0,
                                     unattributed=0, balance=600)
    assert rows['City Tax'] == dict(tax_charged=200, tax_credited=50, remitted=0,
                                    unattributed=0, balance=150)
    assert totals['balance'] == 750


def test_voiding_the_invoice_takes_the_whole_charge_back_off_both_agencies(books):
    invoice = _invoice(books)
    books['run']('invoice void', dict(invoice=invoice['id']), reason='Never happened')
    rows, totals = _liability(books)
    balances, _ = _ties(books)
    assert totals['balance'] == 0 and balances.get(books['liability'], 0) == 0
    assert rows == {}


def test_a_journal_entry_at_the_liability_is_reported_rather_than_dropped(books):
    _invoice(books)
    books['run']('journal post', dict(date='2026-05-01', lines=[
        {'account': books['liability'], 'side': 'debit', 'amount': '1.00'},
        {'account': books['bank'], 'side': 'credit', 'amount': '1.00'}]),
        reason='Hand adjustment')
    rows, totals = _liability(books)
    balances, _ = _ties(books)
    assert rows['Not attributed to an agency'] == dict(
        tax_charged=0, tax_credited=0, remitted=0, unattributed=-100, balance=-100)
    assert totals['balance'] == 900 and balances[books['liability']] == -900


# ---------------------------------------------------------------- the basis the books can support


def test_the_cash_basis_is_refused_rather_than_answered_with_an_accrual_figure(books):
    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    _invoice(books)
    with open_database(books['database'], writable=True) as handle:
        handle.conn.execute(c.company_info.update().values(
            sales_tax_liability_basis='payment_receipt'))
        handle.conn.commit()
    with pytest.raises(BookflowError) as exc:
        books['run']('sales-tax liability', dict(as_of='2026-12-31'))
    assert exc.value.code == 'E_TAX_BASIS_UNSUPPORTED'
    assert exc.value.details['sales_tax_liability_basis'] == 'payment_receipt'
    with pytest.raises(BookflowError) as exc:
        _remit(books, amount=PART)
    assert exc.value.code == 'E_TAX_BASIS_UNSUPPORTED'


def test_a_taxable_sale_cannot_be_posted_on_the_cash_basis_at_all(books):
    """Why the refusal above is honest rather than lazy: nothing is ever recorded to report."""
    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    with open_database(books['database'], writable=True) as handle:
        handle.conn.execute(c.company_info.update().values(
            sales_tax_liability_basis='payment_receipt'))
        handle.conn.commit()
    with pytest.raises(BookflowError) as exc:
        _invoice(books)
    assert exc.value.code == 'E_VALIDATION'
    assert 'invoice_date liability policy' in exc.value.details['fields'][0]['problem']


# ---------------------------------------------------------------- reading it back


def test_show_and_query_read_back_what_was_remitted(books):
    _invoice(books)
    first = _remit(books, amount=PART, check_number='2001', memo='Q1 part')
    second = _remit(books, date='2026-04-21', agency=books['city'], amount='1.00')

    shown = books['run']('sales-tax payment show', {'payment': first['id']})
    assert shown['number'] == first['number'] and shown['total']['amount'] == PART
    assert shown['agency_name'] == 'State Tax' and shown['check_number'] == '2001'
    assert shown['memo'] == 'Q1 part' and shown['through_date'] == '2026-04-20'
    assert len(shown['revision']['lines']) == 1
    assert shown['revision']['lines'][0]['agency_id'] == books['state']
    batch = shown['revision']['batches'][0]
    assert batch['kind'] == 'original' and batch['line_count'] == 2
    assert batch['debit_minor_units'] == batch['credit_minor_units'] == 500
    # The two accounts the money moved between, read from the ledger rather than from the document.
    ledger = books['run']('report general-ledger', dict(
        date_from='2026-04-20', date_to='2026-04-20', limit=50))['rows']
    moved = {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
             for row in ledger if row.get('transaction_type') == 'sales_tax_payment'}
    assert moved == {books['liability']: 500, books['bank']: -500}

    page = books['run']('sales-tax payment query', dict(limit=10))
    assert [item['number'] for item in page['items']] == [first['number'], second['number']]
    one = books['run']('sales-tax payment query', dict(agency=books['city'], limit=10))
    assert [item['agency_name'] for item in one['items']] == ['City Tax']
    by_check = books['run']('sales-tax payment query', dict(check_number='2001', limit=10))
    assert [item['number'] for item in by_check['items']] == [first['number']]
    newest = books['run']('sales-tax payment query', dict(direction='desc', limit=10))
    assert [item['number'] for item in newest['items']] == [second['number'], first['number']]


def test_an_unknown_remittance_is_a_record_not_found(books):
    with pytest.raises(BookflowError) as exc:
        books['run']('sales-tax payment show', {'payment': 'nope'})
    assert exc.value.code == 'E_RECORD_NOT_FOUND'


def test_a_dry_run_reads_the_books_and_writes_nothing(books):
    _invoice(books)
    preview = books['run']('sales-tax pay', dict(
        agency=books['state'], date='2026-04-20', amount=PART, funding_account=books['bank'],
        method=books['methods']['Check']), reason='Look first', dry_run=True)
    assert preview['dry_run'] and preview['total']['amount'] == PART
    assert books['run']('sales-tax payment query', dict(limit=10))['items'] == []
    _, totals = _ties(books)
    assert totals['balance'] == 1000


def test_the_same_idempotency_key_remits_once(books):
    _invoice(books)
    first = _remit(books, amount=PART, idempotency_key='remit-1')
    second = _remit(books, amount=PART, idempotency_key='remit-1')
    assert second['id'] == first['id'] and second['idempotent_replay']
    _, totals = _ties(books)
    assert totals['balance'] == 500


def test_every_write_is_attributed_in_the_company_audit(books):
    _invoice(books)
    payment = _remit(books, amount=PART)
    books['run']('sales-tax payment void', dict(payment=payment['id']), reason='Mistake')
    events = books['run']('audit list', dict(limit=50))['items']
    commands = [row['command'] for row in events]
    assert 'sales-tax pay' in commands and 'sales-tax payment void' in commands
    written = next(row for row in events if row['command'] == 'sales-tax pay')
    assert written['reason'] == 'Remit the tax'


COMMANDS = frozenset(('sales-tax liability', 'sales-tax pay', 'sales-tax payment show',
                      'sales-tax payment query', 'sales-tax payment void'))


@pytest.mark.timeout(300)
def test_the_same_sales_tax_remittance_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio

    from tests.mcp_matrix_support import Matrix, normalize
    from tests.test_mcp_registry_work import GHOST

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}

                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    return await matrix.call(surface, name, raw, **ctx)

                income = (await matrix.call(surface, 'account create',
                                            dict(name='Parity sales', type='income')))['id']
                checking = (await matrix.call(surface, 'account create',
                                              dict(name='Parity checking', type='bank')))['id']
                # `account query` does not project system_role; `account list` does.
                liability = next(row['id'] for row in (await matrix.call(
                    surface, 'account list', {}))['items']
                    if row.get('system_role') == 'sales_tax_payable')
                await matrix.call(surface, 'company update', dict(sales_tax_enabled=True))
                agency = (await matrix.call(surface, 'vendor create',
                                            dict(name='Parity Revenue', is_tax_agency=True)))['id']
                tax_item = (await matrix.call(surface, 'item create', dict(
                    name='Parity eight percent', type='sales_tax_item', tax_percent='8',
                    tax_agency_vendor_id=agency, liability_account_id=liability)))['id']
                codes = {row['taxable']: row['id'] for row in (await matrix.call(
                    surface, 'sales-tax-code list', {}))['items']}
                widget = (await matrix.call(surface, 'item create', dict(
                    name='Parity widget', type='non_inventory_part', sales_enabled=True,
                    description='Parity widget', income_account_id=income, price=UNIT_PRICE,
                    sales_tax_code_id=codes[True])))['id']
                customer = (await matrix.call(surface, 'customer create',
                                              dict(name='Parity Customer')))['id']
                method = next(row['id'] for row in (await matrix.call(
                    surface, 'payment-method query', dict(limit=50)))['items']
                    if row['name'] == 'Check')
                await matrix.call(surface, 'invoice post', dict(
                    customer=customer, date='2026-03-02', due_date='2026-04-01',
                    number='PARITY-TAX-INV-1', sales_tax_item=tax_item,
                    lines=[{'item': widget, 'quantity': '4', 'unit_price': UNIT_PRICE,
                            'tax_code': codes[True]}]))

                owed = await call('sales-tax liability', dict(as_of='2026-03-31', limit=10))
                assert owed['totals']['balance']['amount'] == STATE_TAX

                request = dict(agency=agency, date='2026-04-20', through_date='2026-03-31',
                               amount=PART, funding_account=checking, method=method,
                               check_number='2001', number='PARITY-TAX-1', memo='Parity remittance')
                assert (await call('sales-tax pay', request, dry_run=True))['dry_run']
                paid = await call('sales-tax pay', request, idempotency_key='remit-1')
                replay = await call('sales-tax pay', request, idempotency_key='remit-1')
                assert replay['id'] == paid['id'] and replay['idempotent_replay']
                assert paid['total']['amount'] == PART
                assert paid['remainder_at_posting']['amount'] == REMAINDER

                await call('sales-tax payment show', {'payment': paid['id']})
                await call('sales-tax payment query', {'agency': agency, 'limit': 10})
                await call('sales-tax payment void',
                           {'payment': paid['id'], 'expected_version': paid['version']})

                refused = await call('sales-tax pay', {
                    **request, 'number': 'PARITY-TAX-2', 'amount': '9999.00'}, rejected=True)
                assert refused['code'] == 'E_APPLICATION_CAPACITY'
                assert refused['details']['available_minor_units'] == 800
                assert set(calls) == COMMANDS
                for name, data in list(calls.items()):
                    assert (await call(name, data, company=GHOST,
                                       rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
            expected = normalize(matrix.documents['python'], matrix.roots['python'], set())
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], set())
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left, right)
        finally:
            await matrix.close()

    anyio.run(witness)
