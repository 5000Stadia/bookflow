"""Credit a customer, and check the books by hand.

Every figure asserted here is written out in full at the top of the module, so a reader can add
it up without running anything: a 100.00 invoice and a 30.00 goodwill credit against it; a
108.00 taxed invoice of four units with one unit sent back; and a three-unit line worth 1.00
plus 0.07 tax returned one unit at a time until there is nothing left. What the trial balance,
the A/R aging, the general ledger and the customer statement say after each step is checked
against those figures through the real report commands, never against the credit's own output.
"""
from copy import deepcopy

import pytest
import sqlalchemy as sa

import bookflow
from bookflow.core.errors import BookflowError

# The money. Everything below is arithmetic on these.
INVOICE = '100.00'          # 10000 minor units
CREDIT = '30.00'            # 3000
STILL_OWED = '70.00'        # 100.00 - 30.00
# The taxed invoice: four units at 25.00, 8% on the whole 100.00 net.
UNIT_PRICE = '25.00'
TAXED_NET = '100.00'        # 4 * 25.00
TAXED_TAX = '8.00'          # 8% of 100.00
TAXED_GROSS = '108.00'
# One unit back: floor(10000*1/4) = 2500 of net, floor(800*2500/10000) = 200 of tax.
RETURN_NET = '25.00'
RETURN_TAX = '2.00'
RETURN_GROSS = '27.00'
# The three-unit line: net 100 minor units, one 7% cell of 7 minor units.
THIRDS = ((33, 2, 35), (33, 2, 35), (34, 3, 37))


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    data_root = tmp_path / 'credits'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Credits organization')
    company = client.company.new(legal_name='Credits', home_currency='USD', timezone='UTC',
                                 organization='Credits organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    database = next((data_root).rglob('company.db'))
    with open_database(database, writable=True) as handle:
        liability = handle.conn.execute(sa.select(c.accounts.c.id).where(
            c.accounts.c.system_role == 'sales_tax_payable')).scalar_one()
        handle.conn.execute(c.company_info.update().values(
            sales_tax_enabled=True, sales_tax_liability_basis='invoice_date'))
        handle.conn.commit()

    accounts = client.account.query(company=company, limit=200)['items']
    receivable = next(row['id'] for row in accounts if row['type'] == 'accounts_receivable')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    codes = {row['taxable']: row['id'] for row in run('sales-tax-code list', {})['items']}
    agency = client.vendor.create(company=company, name='State Tax', is_tax_agency=True)['id']
    tax_item = run('item create', dict(name='Eight percent', type='sales_tax_item', tax_percent='8',
                                       tax_agency_vendor_id=agency, liability_account_id=liability))['id']
    seven = run('item create', dict(name='Seven percent', type='sales_tax_item', tax_percent='7',
                                    tax_agency_vendor_id=agency, liability_account_id=liability))['id']
    service = run('item create', dict(name='Site visit', type='service', sales_enabled=True,
                                      description='Site visit', income_account_id=income,
                                      price=UNIT_PRICE, sales_tax_code_id=codes[False]))['id']
    widget = run('item create', dict(name='Widget', type='non_inventory_part', sales_enabled=True,
                                     description='Widget', income_account_id=income, price=UNIT_PRICE,
                                     sales_tax_code_id=codes[True]))['id']
    ridge = client.customer.create(company=company, name='Ridge')['id']
    kerr = client.customer.create(company=company, name='Kerr')['id']
    return dict(client=client, company=company, run=run, database=database,
                receivable=receivable, income=income,
                liability=liability, tax_item=tax_item, seven=seven, service=service, widget=widget,
                ridge=ridge, kerr=kerr, taxable=codes[True], exempt=codes[False])


def _balances(books, date='2026-03-31'):
    """Signed minor units per account from the trial balance, debits positive."""
    report = books['run']('report trial-balance', dict(date_to=date, limit=200))
    rows = {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows']}
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return rows, report


def _aging(books, as_of='2026-03-31'):
    report = books['run']('report ar-aging', dict(as_of=as_of))
    return {row['display_customer_label']: row for row in report['rows']}, report


def _invoice(books, **extra):
    values = dict(customer=books['ridge'], date='2026-03-02', due_date='2026-04-01',
                  lines=[{'item': books['service'], 'quantity': '4', 'unit_price': UNIT_PRICE}])
    values.update(extra)
    return books['run']('invoice post', values, reason='Bill the customer')


def _taxed_invoice(books):
    return books['run']('invoice post', dict(
        customer=books['kerr'], date='2026-03-02', due_date='2026-04-01',
        sales_tax_item=books['tax_item'], sales_tax_calculation='line_component_half_even',
        lines=[{'item': books['widget'], 'quantity': '4', 'unit_price': UNIT_PRICE,
                'tax_code': books['taxable']}]), reason='Bill the customer')


# ---------------------------------------------------------------- O1: a credit that stands alone


def test_a_standalone_credit_takes_income_and_the_receivable_down_together(books):
    invoice = _invoice(books)
    before, _ = _balances(books)
    assert before[books['receivable']] == 10000 and before[books['income']] == -10000

    credit = books['run']('credit-memo post', dict(
        customer=books['ridge'], date='2026-03-10',
        lines=[{'item': books['service'], 'quantity': '1', 'unit_price': CREDIT}]),
        reason='Goodwill credit')

    assert credit['type'] == 'credit_memo' and credit['status'] == 'posted'
    assert credit['origin'] == 'standalone' and credit['total']['amount'] == CREDIT
    after, report = _balances(books)
    # 100.00 billed, 30.00 credited: both sides fall by the same 30.00 and nothing else moves.
    assert after[books['receivable']] == 7000 and after[books['income']] == -7000
    assert report['totals']['debit']['amount'] == report['totals']['credit']['amount']
    # The credit posts one receivable credit and one income debit, and applies nothing.
    batch = credit['revision']['batches'][0]
    assert batch['kind'] == 'original' and batch['effective_date'] == '2026-03-10'
    assert batch['debit_minor_units'] == batch['credit_minor_units'] == 3000
    settlement = books['run']('invoice show', {'invoice': invoice['id']})['settlement_current']
    assert settlement['due_minor_units'] == 10000 and settlement['status'] == 'unpaid'
    assert credit['source_current']['available']['amount'] == CREDIT
    assert credit['source_current']['applied_minor_units'] == 0


def test_the_aging_shows_the_credit_as_its_own_row_and_still_ties_to_the_receivable(books):
    _invoice(books)
    books['run']('credit-memo post', dict(
        customer=books['ridge'], date='2026-03-10',
        lines=[{'item': books['service'], 'quantity': '1', 'unit_price': CREDIT}]),
        reason='Goodwill credit')

    rows, report = _aging(books)
    ridge = rows['Ridge']
    # The invoice is not due until 1 April, so it is current; the credit is aged by its own
    # date, 21 days back, which is what gives it a column of its own.
    assert ridge['current']['amount'] == INVOICE
    assert ridge['days_1_30']['minor_units'] == -3000
    assert ridge['total']['amount'] == STILL_OWED
    balances, _ = _balances(books)
    assert report['totals']['total']['minor_units'] == balances[books['receivable']] == 7000


def test_the_general_ledger_and_the_statement_both_name_the_credit(books):
    _invoice(books)
    credit = books['run']('credit-memo post', dict(
        customer=books['ridge'], date='2026-03-10',
        lines=[{'item': books['service'], 'quantity': '1', 'unit_price': CREDIT}]),
        reason='Goodwill credit')

    ledger = books['run']('report general-ledger', dict(
        date_from='2026-03-01', date_to='2026-03-31', account=books['income'], limit=100))
    posted = [row for row in ledger['rows'] if row.get('transaction_id') == credit['id']]
    assert posted and all(row['transaction_type'] == 'credit_memo' for row in posted)
    assert sum(row['debit']['minor_units'] for row in posted) == 3000

    statement = books['run']('report statement', dict(
        date_from='2026-03-01', date_to='2026-03-31', customer=books['ridge']))
    entries = [row for row in statement['rows'] if row['transaction_id'] == credit['id']]
    assert [row['entry'] for row in entries] == ['credit_memo']
    assert entries[0]['amount']['minor_units'] == -3000
    closing = next(row for row in statement['rows'] if row['kind'] == 'closing')
    assert closing['balance']['amount'] == STILL_OWED


# ---------------------------------------------------------------- O2: a taxed return


def test_a_returned_unit_takes_its_net_and_its_tax_from_what_the_invoice_captured(books):
    invoice = _taxed_invoice(books)
    line = invoice['revision']['lines'][0]
    assert line['net_minor_units'] == 10000 and line['tax_minor_units'] == 800
    assert invoice['total']['amount'] == TAXED_GROSS
    before, _ = _balances(books)
    assert before[books['receivable']] == 10800 and before[books['liability']] == -800

    credit = books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-03-10',
        lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
        reason='One unit came back')

    assert credit['origin'] == 'return' and credit['total']['amount'] == RETURN_GROSS
    returned = credit['revision']['lines'][0]
    assert returned['net']['amount'] == RETURN_NET and returned['tax']['amount'] == RETURN_TAX
    assert returned['pricing_basis'] == 'amount' and returned['unit_price'] is None
    assert returned['source_transaction_id'] == invoice['id']
    assert [(claim['start_microunits'], claim['end_microunits']) for claim in returned['claims']] == [(0, 1_000_000)]
    cell = returned['tax_components'][0]
    assert cell['tax_minor_units'] == 200 and cell['taxable_minor_units'] == 2500
    assert cell['source_tax_component_id'] == line['tax_components'][0]['id']

    after, _ = _balances(books)
    # 108.00 billed, 27.00 back: income falls 25.00 and the tax liability falls 2.00.
    assert after[books['receivable']] == 8100
    assert after[books['income']] == -7500
    assert after[books['liability']] == -600


def test_a_later_price_or_rate_change_moves_no_cent_of_an_issued_credit(books):
    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    invoice = _taxed_invoice(books)
    line = invoice['revision']['lines'][0]
    books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-03-10',
        lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
        reason='One unit came back')
    before, _ = _balances(books)

    with open_database(books['database'], writable=True) as handle:
        handle.conn.execute(c.items.update().where(c.items.c.id == books['tax_item']).values(
            tax_percent_millionths=20_000_000))
        handle.conn.execute(c.items.update().where(c.items.c.id == books['widget']).values(
            price_minor_units=999_99))
        handle.conn.commit()

    second = books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-03-11',
        lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
        reason='A second unit came back')
    assert second['total']['amount'] == RETURN_GROSS
    after, _ = _balances(books)
    assert after[books['receivable']] - before[books['receivable']] == -2700


# ---------------------------------------------------------------- O3: repeated partials


def _three_unit_invoice(books):
    return books['run']('invoice post', dict(
        customer=books['kerr'], date='2026-03-02', due_date='2026-04-01',
        sales_tax_item=books['seven'], sales_tax_calculation='line_component_half_even',
        lines=[{'item': books['widget'], 'quantity': '3', 'net_amount': '1.00',
                'tax_code': books['taxable']}]), reason='Three units')


def test_three_partial_returns_sum_to_exactly_what_the_invoice_captured(books):
    invoice = _three_unit_invoice(books)
    line = invoice['revision']['lines'][0]
    assert (line['net_minor_units'], line['tax_minor_units'], line['gross_minor_units']) == (100, 7, 107)

    issued = []
    for index, (net, tax, gross) in enumerate(THIRDS):
        credit = books['run']('credit-memo post', dict(
            customer=books['kerr'], date='2026-03-%02d' % (10 + index),
            lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
            reason='A unit came back')
        returned = credit['revision']['lines'][0]
        assert (returned['net_minor_units'], returned['tax_minor_units'],
                returned['gross_minor_units']) == (net, tax, gross)
        assert [(claim['start_microunits'], claim['end_microunits']) for claim in returned['claims']] \
            == [(index * 1_000_000, (index + 1) * 1_000_000)]
        issued.append(credit)

    assert sum(row['revision']['lines'][0]['net_minor_units'] for row in issued) == 100
    assert sum(row['revision']['lines'][0]['tax_minor_units'] for row in issued) == 7
    assert sum(row['total_minor_units'] for row in issued) == 107
    # Everything the invoice put on the books has come back off it, to the cent.
    balances, report = _balances(books)
    assert books['receivable'] not in balances and books['income'] not in balances
    assert report['totals']['debit']['minor_units'] == 0
    rows, aging = _aging(books)
    # The invoice is not due until April so it ages current; the credits age by their own
    # dates. Kerr still has a row because both columns are non-zero -- they net to nothing.
    assert rows['Kerr']['current']['minor_units'] == 107
    assert rows['Kerr']['days_1_30']['minor_units'] == -107
    assert rows['Kerr']['total']['minor_units'] == 0
    assert aging['totals']['total']['minor_units'] == 0


def test_a_fourth_unit_is_refused_and_nothing_is_written(books):
    invoice = _three_unit_invoice(books)
    line = invoice['revision']['lines'][0]
    for index in range(3):
        books['run']('credit-memo post', dict(
            customer=books['kerr'], date='2026-03-%02d' % (10 + index),
            lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
            reason='A unit came back')
    before, _ = _balances(books)

    with pytest.raises(BookflowError) as caught:
        books['run']('credit-memo post', dict(
            customer=books['kerr'], date='2026-03-20',
            lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
            reason='A fourth unit')
    assert caught.value.code == 'E_RETURN_EXHAUSTED'
    assert caught.value.details['remaining_base_microunits'] == 0
    after, _ = _balances(books)
    assert after == before


def test_two_units_at_once_claim_the_two_intervals_they_are_worth(books):
    invoice = _three_unit_invoice(books)
    line = invoice['revision']['lines'][0]
    books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-03-10',
        lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
        reason='One unit came back')

    rest = books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-03-11',
        lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '2'}]),
        reason='The other two came back')

    returned = rest['revision']['lines'][0]
    # One claim, because the residue after the first unit is one unbroken span.
    assert [(claim['start_microunits'], claim['end_microunits']) for claim in returned['claims']] \
        == [(1_000_000, 3_000_000)]
    # 100 - 33 of net and 7 - 2 of tax: the remainder, to the cent, however it was split.
    assert returned['net_minor_units'] == 67 and returned['tax_minor_units'] == 5


# ---------------------------------------------------------------- numbering and reading back


def test_a_credit_memo_takes_the_next_invoice_number_and_cannot_reuse_one(books):
    first = _invoice(books)
    assert first['number'] == '1'
    credit = books['run']('credit-memo post', dict(
        customer=books['ridge'], date='2026-03-10',
        lines=[{'item': books['service'], 'quantity': '1', 'unit_price': CREDIT}]), reason='Credit')
    assert credit['number'] == '2'
    second = _invoice(books, number=None)
    assert second['number'] == '3'

    with pytest.raises(BookflowError) as caught:
        books['run']('credit-memo post', dict(
            customer=books['ridge'], date='2026-03-11', number='3',
            lines=[{'item': books['service'], 'quantity': '1', 'unit_price': CREDIT}]), reason='Clash')
    assert caught.value.code == 'E_DUPLICATE_NUMBER'


def test_show_and_history_read_back_what_was_written(books):
    invoice = _taxed_invoice(books)
    line = invoice['revision']['lines'][0]
    credit = books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-03-10', memo='Damaged in transit',
        lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
        reason='One unit came back')

    shown = books['run']('credit-memo show', {'credit_memo': credit['number']})
    assert shown['id'] == credit['id'] and shown['memo'] == 'Damaged in transit'
    assert shown['total']['amount'] == RETURN_GROSS and shown['origin'] == 'return'
    assert shown['source_current']['available']['amount'] == RETURN_GROSS
    assert shown['revision']['lines'][0]['claims'][0]['end_quantity'] == '1'
    assert shown['revision']['tax_calculation_details']['attribution'] is None

    history = books['run']('credit-memo history', {'credit_memo': credit['id']})
    assert history['count'] == 1 and history['items'][0]['revision_number'] == 1
    assert history['items'][0]['total']['amount'] == RETURN_GROSS


def test_a_credit_memo_refuses_what_this_release_does_not_do(books):
    invoice = _taxed_invoice(books)
    line = invoice['revision']['lines'][0]
    # Mixing a returned line and a named item in one document.
    with pytest.raises(BookflowError) as mixed:
        books['run']('credit-memo post', dict(
            customer=books['kerr'], date='2026-03-10',
            lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'},
                   {'item': books['service'], 'quantity': '1', 'unit_price': CREDIT}]), reason='Mixed')
    assert mixed.value.code == 'E_VALIDATION'
    # Pricing a returned line, which would be an allowance this release does not admit.
    with pytest.raises(BookflowError) as priced:
        books['run']('credit-memo post', dict(
            customer=books['kerr'], date='2026-03-10',
            lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'],
                    'quantity': '1', 'net_amount': '1.00'}]), reason='Allowance')
    assert priced.value.code == 'E_VALIDATION'
    # A credit for one customer against another customer's invoice line.
    with pytest.raises(BookflowError) as crossed:
        books['run']('credit-memo post', dict(
            customer=books['ridge'], date='2026-03-10',
            lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
            reason='Cross party')
    assert crossed.value.code == 'E_VALIDATION'


def test_every_write_is_attributed_in_the_company_audit(books):
    _invoice(books)
    credit = books['run']('credit-memo post', dict(
        customer=books['ridge'], date='2026-03-10',
        lines=[{'item': books['service'], 'quantity': '1', 'unit_price': CREDIT}]),
        reason='Goodwill credit')
    events = books['run']('audit list', dict(limit=20))['items']
    written = next(event for event in events if event['command'] == 'credit-memo post')
    assert written['summary'] == f"post credit memo {credit['number']}"
    assert written['reason'] == 'Goodwill credit'


# ---------------------------------------------------------------- every surface, same result

COMMANDS = frozenset(('credit-memo post', 'credit-memo update', 'credit-memo show', 'credit-memo history'))


@pytest.mark.timeout(300)
def test_the_same_credit_memo_through_python_cli_http_and_mcp(root, tmp_path):
    """Mixed-use item corrections and returned-line claim release/retake on every adapter."""
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
                                            dict(name='Parity credits income', type='income')))['id']
                exempt = next(row['id'] for row in (await matrix.call(
                    surface, 'sales-tax-code list', {}))['items'] if not row['taxable'])
                item = (await matrix.call(surface, 'item create', dict(
                    name='Parity credited service', type='service', sales_enabled=True,
                    description='Parity service', income_account_id=income, price='40.00',
                    sales_tax_code_id=exempt)))['id']
                customer = (await matrix.call(surface, 'customer create',
                                              dict(name='Parity Credit Co')))['id']
                await matrix.call(surface, 'invoice post', dict(
                    customer=customer, date='2026-03-02', number='PARITY-INV-1',
                    lines=[{'item': item, 'quantity': '2', 'unit_price': '40.00'}]))
                request = dict(customer=customer, date='2026-03-06', number='PARITY-CM-1',
                               memo='Parity credit',
                               lines=[{'item': item, 'quantity': '1', 'unit_price': '40.00'}])

                assert (await call('credit-memo post', request, dry_run=True))['dry_run']
                credit = await call('credit-memo post', request, idempotency_key='credit-1')
                replay = await call('credit-memo post', request, idempotency_key='credit-1')
                assert replay['id'] == credit['id'] and replay['idempotent_replay']
                assert credit['total']['amount'] == '40.00'

                bank = (await matrix.call(surface, 'account create', dict(name='Credit refund bank', type='bank')))['id']
                method = next(row['id'] for row in (await matrix.call(surface, 'payment-method list', {}))['items']
                              if row['kind'] == 'check')
                target = await matrix.call(surface, 'invoice show', dict(invoice='PARITY-INV-1'))
                await matrix.call(surface, 'customer-credit apply', dict(
                    credit_memo=credit['id'], expected_version=credit['version'], date='2026-03-10',
                    applications=[dict(invoice=target['id'], expected_version=target['version'], amount='10.00')]))
                await matrix.call(surface, 'customer-refund post', dict(
                    date='2026-03-12', funding_account=bank, method=method,
                    sources=[dict(credit_memo=credit['id'], amount='5.00')]))
                credit = await call('credit-memo show', dict(credit_memo=credit['id']))
                correction = dict(credit_memo=credit['id'], expected_version=credit['version'],
                    memo='Corrected through the same command', lines=[dict(
                        line_id=credit['revision']['lines'][0]['line_id'], item=item, quantity='0.5')])
                preview = await call('credit-memo update', correction, dry_run=True)
                assert preview['total']['amount'] == '20.00' and preview['dry_run']
                assert (await call('credit-memo show', {'credit_memo': credit['id']}))['version'] == 2
                corrected = await call('credit-memo update', correction, idempotency_key='credit-update-1')
                assert corrected['id'] == credit['id'] and corrected['version'] == 3
                assert corrected['total']['amount'] == '20.00'
                assert len(corrected['revision']['lines']) == 1
                assert corrected['revision']['lines'][0]['line_id'] == credit['revision']['lines'][0]['line_id']
                assert corrected['source_current']['available_minor_units'] == 500
                retried = await call('credit-memo update', correction, idempotency_key='credit-update-1')
                assert retried['idempotent_replay'] and retried['version'] == 3

                await call('credit-memo show', {'credit_memo': credit['id']})
                await call('credit-memo history', {'credit_memo': credit['id'], 'limit': 10})

                source_line = target['revision']['lines'][0]['line_id']
                returned_request = dict(customer=customer, date='2026-03-06', lines=[dict(
                    source_invoice=target['id'], source_line=source_line, quantity='1')])
                returned = await call('credit-memo post', returned_request)
                neighbor = await call('credit-memo post', returned_request)
                assert [(c['start_microunits'], c['end_microunits'])
                        for c in neighbor['revision']['lines'][0]['claims']] == [(1_000_000, 2_000_000)]
                returned_line = returned['revision']['lines'][0]['line_id']
                shrink = dict(credit_memo=returned['id'], expected_version=returned['version'],
                    lines=[dict(line_id=returned_line, source_invoice=target['id'],
                                source_line=source_line, quantity='0.5')])
                shrunk = await call('credit-memo update', shrink)
                assert shrunk['total']['amount'] == '20.00'
                assert shrunk['source_current']['capacity_minor_units'] == 2000
                assert shrunk['source_current']['available_minor_units'] == 2000
                assert len(shrunk['revision']['lines']) == 1
                assert shrunk['revision']['lines'][0]['line_id'] == returned_line
                assert [(c['start_microunits'], c['end_microunits'])
                        for c in shrunk['revision']['lines'][0]['claims']] == [(0, 500_000)]
                released = await call('credit-memo post', dict(customer=customer, date='2026-03-06',
                    lines=[dict(source_invoice=target['id'], source_line=source_line, quantity='0.5')]))
                assert [(c['start_microunits'], c['end_microunits'])
                        for c in released['revision']['lines'][0]['claims']] == [(500_000, 1_000_000)]
                assert (await call('credit-memo show', dict(credit_memo=neighbor['id'])))['revision']['lines'] == neighbor['revision']['lines']
                exhausted = await call('credit-memo update', dict(shrink,
                    expected_version=shrunk['version'], lines=[dict(line_id=returned_line,
                    source_invoice=target['id'], source_line=source_line, quantity='1')]), rejected=True)
                assert exhausted['code'] == 'E_RETURN_EXHAUSTED'
                assert (await call('credit-memo show', dict(credit_memo=returned['id'])))['version'] == shrunk['version']

                refused = await call('credit-memo post', {
                    **request, 'number': 'PARITY-INV-1'}, rejected=True)
                assert refused['code'] == 'E_DUPLICATE_NUMBER'
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


def test_a_release_that_is_not_an_exact_undo_is_refused_by_storage_itself(books):
    """The fence under `credit-memo void`, checked at the row rather than at the command.

    What the void writes is checked through the real command in
    `tests/test_credit_memo_lifecycle.py`; this is the storage trigger underneath it, which no
    command can talk its way past -- a release that moves one endpoint is not an undo.
    """
    from bookflow.company import schema as c
    from bookflow.core.ids import new_id
    from bookflow.storage.engine import open_database
    invoice = _three_unit_invoice(books)
    line = invoice['revision']['lines'][0]
    issued = [books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-03-%02d' % (10 + index),
        lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
        reason='A unit came back') for index in range(3)]
    first = issued[0]['revision']['lines'][0]['claims'][0]

    with open_database(books['database'], writable=True) as handle:
        claim = handle.conn.execute(sa.select(c.credit_source_claims).where(
            c.credit_source_claims.c.id == first['id'])).mappings().one()
        # Anything less than an exact undo is refused by the storage fence itself.
        with pytest.raises(Exception):
            handle.conn.execute(c.credit_source_claims.insert().values(
                **{**dict(claim), 'id': new_id(), 'kind': 'release',
                   'reverses_claim_id': claim['id'], 'end_microunits': claim['end_microunits'] - 1}))
            handle.conn.commit()
        handle.conn.rollback()
        handle.conn.execute(c.credit_source_claims.insert().values(
            **{**dict(claim), 'id': new_id(), 'kind': 'release', 'reverses_claim_id': claim['id']}))
        handle.conn.commit()

    again = books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-03-20',
        lines=[{'source_invoice': invoice['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
        reason='Re-issued after the release')
    reclaimed = again['revision']['lines'][0]
    # The released span is the lowest one available again, so it is worth exactly what it was.
    assert (reclaimed['net_minor_units'], reclaimed['tax_minor_units'],
            reclaimed['gross_minor_units']) == THIRDS[0]
    assert [(claim['start_microunits'], claim['end_microunits']) for claim in reclaimed['claims']] \
        == [(0, 1_000_000)]
