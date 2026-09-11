"""A charge entered straight onto a customer's account, and the books checked by hand.

Every figure asserted here is written out in full in the module, so a reader can add it up
without running anything. The worked case is the one the anchor product's own guide
describes -- a professional billing a quarter hour here and a quarter hour there, each one
another charge to the client's account, read as one total on the statement:

    SC-1, 2026-06-03, Consultation, 0.25 at 240.00  =  60.00
    INV-1, 2026-06-10, Consultation, 1.25 at 240.00 = 300.00, due 2026-07-10
    a receipt of 300.00 on 2026-07-01, applied to INV-1 in full
    SC-2, 2026-07-21, Filing Fee, a flat 125.00

    0 + 60.00 + 300.00 - 300.00 + 125.00 = 185.00, which is what the customer owes on
    2026-08-31 and what Accounts Receivable carries on the same date.

    Aged on 2026-08-31, each charge lands on its own date: 2026-06-03 is 89 days back and
    2026-07-21 is 41, so 60.00 falls in 61-90 and 125.00 in 31-60. The invoice is paid and
    the receipt is spent, so neither is aged at all.

What the reports say at each step is read back through the real report commands, never
against the writer's own output.
"""
from copy import deepcopy

import pytest

import bookflow
from bookflow.core.errors import BookflowError

RATE = '240.00'
SC1 = '60.00'      # 0.25 hours at 240.00
SC2 = '125.00'
INVOICE = '300.00'  # 1.25 hours at 240.00
SC1_UNITS, SC2_UNITS, INVOICE_UNITS = 6000, 12500, 30000
OWED_UNITS = SC1_UNITS + SC2_UNITS          # 18500: the invoice is paid, the charges are not
OWED = '185.00'


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    data_root = tmp_path / 'charges'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Charges organization')
    company = client.company.new(legal_name='Charges', home_currency='USD', timezone='UTC',
                                 organization='Charges organization', chart='general')['company_id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    accounts = {row['type']: row['id'] for row in client.account.query(company=company, limit=200)['items']}
    fees = client.account.create(company=company, name='Legal Fees', type='income')['id']
    costs = client.account.create(company=company, name='Reimbursed Costs', type='income')['id']
    code = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    consultation = run('item create', dict(name='Consultation', type='service', sales_enabled=True,
                                           description='Legal services', income_account_id=fees,
                                           price=RATE, sales_tax_code_id=code))['id']
    filing = run('item create', dict(name='Filing Fee', type='other_charge', sales_enabled=True,
                                     description='Filing fee', income_account_id=costs,
                                     price=SC2, sales_tax_code_id=code))['id']
    customer = client.customer.create(company=company, name='Acme Holdings')['id']
    methods = {row['name']: row['id'] for row in run('payment-method query', {'limit': 50})['items']}
    return dict(client=client, company=company, run=run, ar=accounts['accounts_receivable'],
                bank=accounts['bank'], fees=fees, costs=costs, consultation=consultation,
                filing=filing, customer=customer, check=methods['Check'])


def _balances(books, date_to='2026-12-31'):
    """Signed minor units per account, debits positive, read off the trial balance."""
    page = books['run']('report trial-balance', dict(date_to=date_to))
    return {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in page['rows']}


def _first(books):
    return books['run']('statement-charge post', dict(
        customer=books['customer'], date='2026-06-03', number='SC-1', item=books['consultation'],
        quantity='0.25', rate=RATE, description='Call re lease, 15 minutes'), reason='Enter SC-1')


def _second(books):
    return books['run']('statement-charge post', dict(
        customer=books['customer'], date='2026-07-21', number='SC-2', item=books['filing'],
        amount=SC2, description='County filing fee'), reason='Enter SC-2')


def _invoice_and_receipt(books):
    invoice = books['run']('invoice post', dict(
        customer=books['customer'], date='2026-06-10', number='INV-1', due_date='2026-07-10',
        lines=[dict(item=books['consultation'], quantity='1.25', unit_price=RATE,
                    description='Deposition prep')]), reason='Enter INV-1')
    books['run']('payment receive', dict(
        customer=books['customer'], date='2026-07-01', amount=INVOICE, operation_key='pay-inv-1',
        deposit_to=books['bank'], payment_method=books['check'],
        applications={'mode': 'inline', 'items': [
            dict(invoice=invoice['id'], amount=INVOICE, expected_version=invoice['version'])]}),
        reason='Receive the invoice payment')
    return invoice


def test_two_charges_debit_receivable_and_credit_their_own_income_accounts(books):
    before = _balances(books)
    first, second = _first(books), _second(books)
    assert first['total']['amount'] == SC1 and first['total_minor_units'] == SC1_UNITS
    assert second['total']['amount'] == SC2 and second['total_minor_units'] == SC2_UNITS
    after = _balances(books)
    moved = {key: after.get(key, 0) - before.get(key, 0) for key in set(before) | set(after)
             if after.get(key, 0) != before.get(key, 0)}
    # 60.00 to one income account and 125.00 to the other, and 185.00 of receivable for both.
    assert moved == {books['ar']: SC1_UNITS + SC2_UNITS,
                     books['fees']: -SC1_UNITS, books['costs']: -SC2_UNITS}


def test_a_charge_posts_the_two_legs_an_invoice_line_posts(books):
    charge = _first(books)
    shown = books['run']('statement-charge show', {'statement_charge': 'SC-1'})
    assert shown['id'] == charge['id'] and shown['due_date'] is None
    ledger = books['run']('report general-ledger', dict(date_from='2026-06-01', date_to='2026-06-30'))
    legs = {(row['account_id'], row['debit']['minor_units'], row['credit']['minor_units'])
            for row in ledger['rows'] if row['transaction_id'] == charge['id']}
    assert legs == {(books['ar'], SC1_UNITS, 0), (books['fees'], 0, SC1_UNITS)}
    assert all(row['transaction_type'] == 'statement_charge' for row in ledger['rows']
               if row['transaction_id'] == charge['id'])


def test_the_statement_reads_charges_in_date_order_among_invoices_and_payments(books):
    _first(books)
    _invoice_and_receipt(books)
    _second(books)
    statement = books['run']('report statement', dict(
        date_from='2026-06-01', date_to='2026-08-31', customer=books['customer']))
    assert [(row['entry'], row['date'], row['number'], row['amount']['minor_units'],
             row['balance']['minor_units']) for row in statement['rows']] == [
        ('balance_forward', None, None, 0, 0),
        ('statement_charge', '2026-06-03', 'SC-1', SC1_UNITS, 6000),
        ('invoice', '2026-06-10', 'INV-1', INVOICE_UNITS, 36000),
        ('payment', '2026-07-01', '1', -INVOICE_UNITS, 6000),
        ('statement_charge', '2026-07-21', 'SC-2', SC2_UNITS, 18500),
        ('balance_due', None, None, 0, OWED_UNITS)]
    # The charge says on the statement what it was for: the description a person entered is
    # what the customer reads, without their having to write it twice.
    charges = [row for row in statement['rows'] if row['entry'] == 'statement_charge']
    assert [row['memo'] for row in charges] == ['Call re lease, 15 minutes', 'County filing fee']
    assert statement['totals']['closing']['amount'] == OWED
    assert statement['totals']['charges']['minor_units'] == SC1_UNITS + SC2_UNITS + INVOICE_UNITS


def test_aging_ages_each_charge_by_its_own_date_and_ties_to_the_receivable_control(books):
    _first(books)
    _invoice_and_receipt(books)
    _second(books)
    aging = books['run']('report ar-aging', dict(as_of='2026-08-31'))
    totals = {name: aging['totals'][name]['minor_units'] for name in
              ('current', 'days_1_30', 'days_31_60', 'days_61_90', 'over_90', 'total')}
    # 2026-07-21 is 41 days before 2026-08-31 and 2026-06-03 is 89.
    assert totals == {'current': 0, 'days_1_30': 0, 'days_31_60': SC2_UNITS,
                      'days_61_90': SC1_UNITS, 'over_90': 0, 'total': OWED_UNITS}
    assert len(aging['rows']) == 1 and aging['rows'][0]['total']['amount'] == OWED
    control = next(row for row in books['run']('report trial-balance', dict(date_to='2026-08-31'))['rows']
                   if row['account_id'] == books['ar'])
    assert control['debit']['minor_units'] - control['credit']['minor_units'] == OWED_UNITS
    # The statement's own aging foot is the same arithmetic over the same customers.
    statement = books['run']('report statement', dict(
        date_from='2026-06-01', date_to='2026-08-31', customer=books['customer']))
    assert {name: statement['aging'][name]['minor_units'] for name in totals} == totals


def test_voiding_a_charge_puts_every_account_back(books):
    _first(books)
    second = _second(books)
    before = _balances(books)
    voided = books['run']('statement-charge void',
                          {'statement_charge': second['id'], 'expected_version': 1},
                          reason='Charged to the wrong client')
    assert voided['status'] == 'voided'
    after = _balances(books)
    moved = {key: after.get(key, 0) - before.get(key, 0) for key in set(before) | set(after)
             if after.get(key, 0) != before.get(key, 0)}
    assert moved == {books['ar']: -SC2_UNITS, books['costs']: SC2_UNITS}
    aging = books['run']('report ar-aging', dict(as_of='2026-08-31'))
    assert aging['totals']['total']['minor_units'] == SC1_UNITS
    statement = books['run']('report statement', dict(
        date_from='2026-06-01', date_to='2026-08-31', customer=books['customer']))
    # A voided charge is worth nothing, so it has no row at all -- not a row marked void.
    assert [row['number'] for row in statement['rows'] if row['entry'] == 'statement_charge'] == ['SC-1']
    assert statement['totals']['closing']['minor_units'] == SC1_UNITS
    with pytest.raises(BookflowError) as refused:
        books['run']('statement-charge void',
                     {'statement_charge': second['id'], 'expected_version': 1}, reason='Again')
    assert refused.value.code == 'E_VERSION_CONFLICT'


def test_a_void_needs_a_reason_and_a_charge_needs_a_positive_amount(books):
    charge = _first(books)
    with pytest.raises(BookflowError) as refused:
        books['run']('statement-charge void', {'statement_charge': charge['id']})
    assert refused.value.code == 'E_REASON_REQUIRED'
    with pytest.raises(BookflowError) as refused:
        books['run']('statement-charge post', dict(
            customer=books['customer'], date='2026-06-03', item=books['consultation'],
            rate='0.00'), reason='Nothing owed')
    assert refused.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError) as refused:
        books['run']('statement-charge post', dict(
            customer=books['customer'], date='2026-06-03', item=books['consultation'],
            rate=RATE, amount=SC1), reason='Two prices')
    assert refused.value.code == 'E_VALIDATION'


def test_a_charge_is_not_an_invoice_and_takes_its_own_number_series(books):
    _first(books)
    _second(books)
    charges = books['run']('statement-charge query', {'limit': 10})
    assert [(row['number'], row['date'], row['total']['amount']) for row in charges['items']] == [
        ('SC-1', '2026-06-03', SC1), ('SC-2', '2026-07-21', SC2)]
    assert books['run']('invoice query', {'limit': 10})['items'] == []
    # An unnumbered charge and an unnumbered invoice both start at 1: they are separate series,
    # so a charge is never refused for a number an invoice already holds.
    numbered = books['run']('statement-charge post', dict(
        customer=books['customer'], date='2026-06-04', item=books['consultation'],
        rate=RATE), reason='Take the next charge number')
    assert numbered['number'] == '1'
    invoice = books['run']('invoice post', dict(
        customer=books['customer'], date='2026-06-05', due_date='2026-07-05',
        lines=[dict(item=books['consultation'], quantity='1', unit_price=RATE)]),
        reason='Take the next invoice number')
    assert invoice['number'] == '1'
    with pytest.raises(BookflowError) as refused:
        books['run']('statement-charge post', dict(
            customer=books['customer'], date='2026-06-06', number='SC-1',
            item=books['consultation'], rate=RATE), reason='Reuse a charge number')
    assert refused.value.code == 'E_DUPLICATE_NUMBER'


def test_a_payment_cannot_settle_a_charge_yet_and_says_which_it_found(books):
    """The real boundary of this increment, asserted rather than described.

    A charge is receivable and ages like one, but no settlement edge reaches it: the
    ``applications`` insertion fence admits an invoice and nothing else. Somebody who names a
    charge where an invoice goes gets told what they named, not that nothing was found.
    """
    charge = _first(books)
    with pytest.raises(BookflowError) as refused:
        books['run']('payment receive', dict(
            customer=books['customer'], date='2026-06-05', amount=SC1, operation_key='try-charge',
            deposit_to=books['bank'], payment_method=books['check'],
            applications={'mode': 'inline', 'items': [
                dict(invoice=charge['id'], amount=SC1, expected_version=charge['version'])]}),
            reason='Try to settle a charge')
    assert refused.value.code == 'E_RECORD_NOT_FOUND'
    assert refused.value.details['found_type'] == 'statement_charge'
    assert 'statement charge' in (refused.value.message or '')
    # And it is not offered as something to pay, which is why naming one is a mistake to catch.
    assert books['run']('report open-invoices', dict(as_of='2026-12-31'))['rows'] == []
    assert books['run']('payment invoices', dict(mode='new_receipt', customer=books['customer'],
                                                 date='2026-06-05', limit=10))['items'] == []


COMMANDS = frozenset(('statement-charge post', 'statement-charge show',
                      'statement-charge query', 'statement-charge void'))


def test_the_same_statement_charge_through_python_cli_http_and_mcp(root, tmp_path):
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
                                            dict(name='Parity legal fees', type='income')))['id']
                code = next(row['id'] for row in
                            (await matrix.call(surface, 'sales-tax-code list', {}))['items']
                            if not row['taxable'])
                item = (await matrix.call(surface, 'item create', dict(
                    name='Parity consultation', type='service', sales_enabled=True,
                    description='Legal services', income_account_id=income, price=RATE,
                    sales_tax_code_id=code)))['id']
                customer = (await matrix.call(surface, 'customer create',
                                              dict(name='Parity Holdings')))['id']
                entry = dict(customer=customer, date='2026-06-03', number='PSC-1', item=item,
                             quantity='0.25', rate=RATE, description='Parity call')
                assert (await call('statement-charge post', entry, dry_run=True))['dry_run']
                posted = await call('statement-charge post', entry, idempotency_key='charge-1')
                replay = await call('statement-charge post', entry, idempotency_key='charge-1')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']
                assert posted['total']['amount'] == SC1

                await call('statement-charge show', {'statement_charge': posted['id']})
                await call('statement-charge query', {'customer': customer, 'limit': 10})
                await call('statement-charge void', {'statement_charge': posted['id'],
                                                     'expected_version': posted['version']})

                refused = await call('statement-charge post',
                                     {**entry, 'number': 'PSC-2', 'rate': RATE, 'amount': SC1},
                                     rejected=True)
                assert refused['code'] == 'E_VALIDATION'
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
