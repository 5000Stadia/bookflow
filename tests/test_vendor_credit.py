"""A vendor credit, applied to a bill, and the books checked by hand.

Every figure asserted here is written out in full in the module, so a reader can add it up
without running anything. The worked case is the one the implementation plan specifies:

    B-1, vendor Alto, 100000 minor units, dated 2026-05-01 and due 2026-05-31.
    VC-1, vendor Alto,  46254 minor units, dated 2026-05-10.
    A bill payment of  53746 minor units, dated 2026-05-20.

    100000 - 46254 = 53746, and 46254 + 53746 = 100000.

and what the payables reports say at each step is asserted through the real report commands,
never against the writer's own output.
"""
from copy import deepcopy

import pytest

import bookflow
from bookflow.core.errors import BookflowError

BILL = '1000.00'          # 100000 minor units
CREDIT = '462.54'         #  46254 minor units
CASH = '537.46'           #  53746 minor units, which is 100000 - 46254
BILL_UNITS, CREDIT_UNITS, CASH_UNITS = 100000, 46254, 53746


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
    accounts = client.account.query(company=company, limit=200)['items']
    payable = next(row['id'] for row in accounts if row['type'] == 'accounts_payable')
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    repairs = client.account.create(company=company, name='Roof Repairs', type='expense')['id']
    parts = client.account.create(company=company, name='Parts Bought', type='expense')['id']
    alto = client.vendor.create(company=company, name='Alto Roofing')['id']
    other = client.vendor.create(company=company, name='Corner Hardware')['id']
    methods = {row['name']: row['id'] for row in
               client.run('payment-method query', {'limit': 50}, company=company)['items']}

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, payable=payable, bank=bank, repairs=repairs,
                parts=parts, alto=alto, other=other, check=methods['Check'], run=run)


def _bill(books, **extra):
    return books['run']('bill post', dict(
        vendor=books['alto'], date='2026-05-01', due_date='2026-05-31', number='B-1',
        memo='Roof work', expenses=[{'account': books['repairs'], 'amount': BILL, 'memo': 'Roof'}],
        **extra), reason='Enter B-1')


def _credit(books, **extra):
    return books['run']('vendor-credit post', dict(
        vendor=books['alto'], date='2026-05-10', number='VC-1', memo='Material returned',
        expenses=[{'account': books['repairs'], 'amount': CREDIT, 'memo': 'Returned tiles'}],
        **extra), reason='Enter VC-1')


def _net(rows):
    """Signed minor units per account, debits positive, from the ledger's posting rows.

    Accounts that net to nothing are dropped: a payable debited and credited the same amount is
    not a balance, and listing it as a zero would make every expected mapping carry noise.
    """
    net = {}
    for line in rows:
        if line['kind'] != 'posting':
            continue
        net[line['account_id']] = net.get(line['account_id'], 0) + (
            line['debit']['minor_units'] - line['credit']['minor_units'])
    return {account: units for account, units in net.items() if units}


def _ledger(books):
    return _net(books['run']('report general-ledger',
                             {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 200})['rows'])


def _settlement(books, bill_id):
    return books['run']('bill show', {'bill': bill_id})['settlement_current']


def _aging(books, as_of='2026-05-31'):
    return books['run']('report ap-aging', {'as_of': as_of, 'limit': 50})


def _unpaid(books, as_of='2026-05-31'):
    return books['run']('report unpaid-bills', {'as_of': as_of, 'limit': 50})


def test_a_vendor_credit_debits_the_payable_and_credits_the_account_the_cost_went_to(books):
    _bill(books)
    credit = _credit(books)

    assert credit['type'] == 'vendor_credit'
    assert credit['status'] == 'posted'
    assert credit['number'] == 'VC-1'
    assert credit['vendor_id'] == books['alto']
    assert credit['ap_account_id'] == books['payable']
    assert credit['total'] == {'amount': CREDIT, 'currency': 'USD', 'minor_units': CREDIT_UNITS}
    assert credit['expense_total']['minor_units'] == CREDIT_UNITS
    batch = credit['revision']['batches'][0]
    assert (batch['kind'], batch['effective_date']) == ('original', '2026-05-10')
    assert batch['debit_total']['amount'] == batch['credit_total']['amount'] == CREDIT

    # 100000 owed on the bill, 46254 given back: Accounts Payable nets to 53746, and Roof
    # Repairs, debited 100000 by the bill and credited 46254 by the credit, nets to the same.
    assert _ledger(books) == {books['repairs']: CASH_UNITS, books['payable']: -CASH_UNITS}

    # The credit is a source, not a payable: it settles nothing until it is applied.
    assert credit['settlement_current']['amount_minor_units'] == CREDIT_UNITS
    assert credit['settlement_current']['applied_minor_units'] == 0
    assert credit['settlement_current']['unapplied_minor_units'] == CREDIT_UNITS
    assert credit['settlement_current']['status'] == 'unapplied'
    assert credit['revision']['source']['source_type'] == 'vendor_credit'
    assert [row['amount_minor_units'] for row in credit['revision']['source']['components']] == [CREDIT_UNITS]

    events = books['client'].audit.list(company=books['company'], limit=5)['items']
    assert events[0]['command'] == 'vendor-credit post'


def test_before_the_application_the_payables_reports_agree_with_the_bill_by_hand(books):
    bill = _bill(books)
    _credit(books)

    # B-1 ages by its due date, 2026-05-31, which is current on that date. VC-1 ages by its own
    # date, 2026-05-10, which is 21 days back, so it lands in 1-30 at -46254. The vendor's line
    # is 100000 - 46254 = 53746, and that is the Accounts Payable control exactly.
    aging = _aging(books)
    assert aging['totals']['current']['minor_units'] == BILL_UNITS
    assert aging['totals']['days_1_30']['minor_units'] == -CREDIT_UNITS
    assert aging['totals']['total']['minor_units'] == CASH_UNITS
    assert len(aging['rows']) == 1
    assert aging['rows'][0]['display_vendor_label'] == 'Alto Roofing'
    assert aging['rows'][0]['current']['minor_units'] == BILL_UNITS
    assert aging['rows'][0]['days_1_30']['minor_units'] == -CREDIT_UNITS
    assert aging['rows'][0]['total']['minor_units'] == CASH_UNITS
    assert -_ledger(books)[books['payable']] == aging['totals']['total']['minor_units'] == CASH_UNITS

    # Unpaid bills lists B-1 at its gross and does not list VC-1: a credit is not a bill.
    unpaid = _unpaid(books)
    assert [row['number'] for row in unpaid['rows']] == ['B-1']
    assert unpaid['rows'][0]['amount']['minor_units'] == BILL_UNITS
    assert unpaid['rows'][0]['applied']['minor_units'] == 0
    assert unpaid['rows'][0]['balance']['minor_units'] == BILL_UNITS
    assert unpaid['rows'][0]['settlement_status'] == 'unpaid'
    assert unpaid['totals']['balance']['minor_units'] == BILL_UNITS

    # And the bill itself says the same thing the report does.
    settlement = _settlement(books, bill['id'])
    assert settlement['gross_minor_units'] == BILL_UNITS
    assert settlement['applied_minor_units'] == 0
    assert settlement['open_minor_units'] == BILL_UNITS
    assert settlement['status'] == 'unpaid'
    assert settlement['sources'] == []


def test_applied_totals_stays_one_number_through_unpaid_partial_and_paid(books):
    bill = _bill(books)
    credit = _credit(books)
    before = _ledger(books)

    applied = books['run']('vendor-credit apply', dict(
        credit=credit['id'], bills=[{'bill': bill['id']}], expected_version=credit['version']),
        reason='Apply VC-1 to B-1')

    # An application posts nothing at all: every account is exactly where it was.
    assert _ledger(books) == before
    assert applied['settlement_current']['applied_minor_units'] == CREDIT_UNITS
    assert applied['settlement_current']['unapplied_minor_units'] == 0
    assert applied['settlement_current']['status'] == 'applied'
    assert [row['bill_number'] for row in applied['applications']] == ['B-1']

    # 100000 - 46254 = 53746 open, and `applied` is still one number.
    partial = _settlement(books, bill['id'])
    assert partial['applied_minor_units'] == CREDIT_UNITS
    assert partial['open_minor_units'] == CASH_UNITS
    assert partial['status'] == 'partial'
    assert partial['sources'] == [{'source_type': 'vendor_credit',
                                   'applied_minor_units': CREDIT_UNITS,
                                   'applied': {'amount': CREDIT, 'currency': 'USD',
                                               'minor_units': CREDIT_UNITS}}]

    # The reports agree with the bill: VC-1's row nets to zero and is omitted, B-1 is 53746.
    aging = _aging(books)
    assert aging['totals']['current']['minor_units'] == CASH_UNITS
    assert aging['totals']['days_1_30']['minor_units'] == 0
    assert aging['totals']['total']['minor_units'] == CASH_UNITS
    unpaid = _unpaid(books)
    assert [row['number'] for row in unpaid['rows']] == ['B-1']
    assert unpaid['rows'][0]['applied']['minor_units'] == CREDIT_UNITS
    assert unpaid['rows'][0]['balance']['minor_units'] == CASH_UNITS
    assert unpaid['rows'][0]['settlement_status'] == 'partly_paid'

    payment = books['run']('bill pay', dict(
        date='2026-05-20', funding_account=books['bank'], method=books['check'],
        check_number='1041', bills=[{'bill': bill['id']}]), reason='Pay the rest of B-1')
    # Nothing was named, so the payment is written for what is still open: 53746.
    assert payment['paid'] == {'amount': CASH, 'currency': 'USD', 'minor_units': CASH_UNITS}

    paid = _settlement(books, bill['id'])
    assert paid['applied_minor_units'] == BILL_UNITS
    assert paid['open_minor_units'] == 0
    assert paid['status'] == 'paid'
    # 46254 credit + 53746 cash = 100000, said by a separate read that changed no other field.
    assert paid['sources'] == [
        {'source_type': 'bill_payment', 'applied_minor_units': CASH_UNITS,
         'applied': {'amount': CASH, 'currency': 'USD', 'minor_units': CASH_UNITS}},
        {'source_type': 'vendor_credit', 'applied_minor_units': CREDIT_UNITS,
         'applied': {'amount': CREDIT, 'currency': 'USD', 'minor_units': CREDIT_UNITS}}]
    assert sum(row['applied_minor_units'] for row in paid['sources']) == paid['applied_minor_units']

    # A paid bill leaves the unpaid list and ages to nothing, and Accounts Payable is zero.
    assert _unpaid(books)['rows'] == []
    assert _unpaid(books)['totals']['balance']['minor_units'] == 0
    assert _aging(books)['totals']['total']['minor_units'] == 0
    assert _aging(books)['rows'] == []
    # 53746 out of the bank, 53746 of expense left standing: 100000 debited by the bill less
    # 46254 credited back.
    assert _ledger(books) == {books['repairs']: CASH_UNITS, books['bank']: -CASH_UNITS}


def test_unapplying_gives_the_bill_and_the_credit_back_exactly(books):
    bill = _bill(books)
    credit = _credit(books)
    before = _ledger(books)
    applied = books['run']('vendor-credit apply', dict(
        credit=credit['id'], bills=[{'bill': bill['id']}], expected_version=credit['version']),
        reason='Apply VC-1')

    taken = books['run']('vendor-credit unapply', dict(
        credit=credit['id'], expected_version=applied['version']), reason='Wrong bill')

    assert _ledger(books) == before
    assert taken['settlement_current']['applied_minor_units'] == 0
    assert taken['settlement_current']['unapplied_minor_units'] == CREDIT_UNITS
    assert [row['kind'] for row in taken['applications']] == ['apply', 'unapply']
    assert all(row['active'] is False for row in taken['applications'])
    settlement = _settlement(books, bill['id'])
    assert settlement['applied_minor_units'] == 0
    assert settlement['open_minor_units'] == BILL_UNITS
    assert settlement['status'] == 'unpaid'
    assert settlement['sources'] == []
    # The aging is back to where it was: B-1 at its gross and VC-1 at -46254 by its own date.
    assert _aging(books)['totals']['current']['minor_units'] == BILL_UNITS
    assert _aging(books)['totals']['days_1_30']['minor_units'] == -CREDIT_UNITS


def test_voiding_a_credit_restores_every_account_exactly(books):
    _bill(books)
    credit = _credit(books)
    voided = books['run']('vendor-credit void', dict(
        credit=credit['id'], expected_version=credit['version']),
        reason='Entered against the wrong vendor')

    assert voided['status'] == 'voided'
    assert voided['void_reason'] == 'Entered against the wrong vendor'
    assert voided['settlement_current']['amount_minor_units'] == 0
    assert voided['settlement_current']['status'] == 'voided'
    # The reversal is at the credit's own date and is an exact inverse, so the books read as if
    # only the bill had been entered: 100000 of expense and 100000 owed.
    reversal = [batch for batch in voided['revision']['batches'] if batch['kind'] == 'reversal']
    assert len(reversal) == 1 and reversal[0]['effective_date'] == '2026-05-10'
    assert _ledger(books) == {books['repairs']: BILL_UNITS, books['payable']: -BILL_UNITS}
    assert _aging(books)['totals']['total']['minor_units'] == BILL_UNITS
    assert _unpaid(books)['totals']['balance']['minor_units'] == BILL_UNITS
    # The number stays occupied and every revision stays readable.
    assert books['run']('vendor-credit show', {'credit': 'VC-1'})['status'] == 'voided'


def test_a_credit_that_settles_a_bill_cannot_be_voided_until_it_is_detached(books):
    bill = _bill(books)
    credit = _credit(books)
    applied = books['run']('vendor-credit apply', dict(
        credit=credit['id'], bills=[{'bill': bill['id']}], expected_version=credit['version']),
        reason='Apply VC-1')

    with pytest.raises(BookflowError) as caught:
        books['run']('vendor-credit void', dict(credit=credit['id'],
                                                expected_version=applied['version']),
                     reason='Change of mind')
    assert caught.value.code == 'E_HAS_APPLICATIONS'
    assert _settlement(books, bill['id'])['open_minor_units'] == CASH_UNITS

    # And the bill cannot be corrected or voided under a credit either, for the same reason.
    with pytest.raises(BookflowError) as second:
        books['run']('bill void', dict(bill=bill['id'], expected_version=bill['version'] + 1),
                     reason='Change of mind')
    assert second.value.code == 'E_HAS_APPLICATIONS'


def test_a_credit_answers_only_its_own_vendor_and_never_more_than_it_carries(books):
    bill = _bill(books)
    credit = _credit(books)
    theirs = books['run']('bill post', dict(
        vendor=books['other'], date='2026-05-02', number='B-2',
        expenses=[{'account': books['parts'], 'amount': '75.25'}]), reason='Another vendor')

    with pytest.raises(BookflowError) as crossed:
        books['run']('vendor-credit apply', dict(credit=credit['id'],
                                                 bills=[{'bill': theirs['id']}]),
                     reason='Wrong vendor')
    assert crossed.value.code == 'E_APPLICATION_INCOMPATIBLE'

    with pytest.raises(BookflowError) as beyond:
        books['run']('vendor-credit apply', dict(
            credit=credit['id'], bills=[{'bill': bill['id'], 'amount': '500.00'}]),
            reason='More than it is worth')
    assert beyond.value.code == 'E_APPLICATION_CAPACITY'
    assert beyond.value.details['available_minor_units'] == CREDIT_UNITS
    assert beyond.value.details['requested_minor_units'] == 50000

    # Neither refusal wrote anything.
    assert _settlement(books, bill['id'])['open_minor_units'] == BILL_UNITS
    assert books['run']('vendor-credit show', {'credit': credit['id']})[
        'settlement_current']['unapplied_minor_units'] == CREDIT_UNITS


def test_a_credit_larger_than_the_bill_settles_it_and_keeps_the_rest_free(books):
    bill = _bill(books)
    big = books['run']('vendor-credit post', dict(
        vendor=books['alto'], date='2026-05-10', number='VC-2',
        expenses=[{'account': books['repairs'], 'amount': '600.00'},
                  {'account': books['repairs'], 'amount': '600.00'}]), reason='Large credit')
    assert big['total']['minor_units'] == 120000

    applied = books['run']('vendor-credit apply', dict(
        credit=big['id'], bills=[{'bill': bill['id']}], expected_version=big['version']),
        reason='Settle B-1 in full')

    # 100000 of the 120000 is attached, drawn from the credited lines in order: the first line
    # gives all 60000 and the second gives 40000 of its own 60000, leaving 20000 free.
    assert [row['amount_minor_units'] for row in applied['applications']] == [60000, 40000]
    assert applied['settlement_current']['applied_minor_units'] == BILL_UNITS
    assert applied['settlement_current']['unapplied_minor_units'] == 20000
    assert applied['settlement_current']['status'] == 'partial'
    settled = _settlement(books, bill['id'])
    assert settled['open_minor_units'] == 0 and settled['status'] == 'paid'
    assert settled['sources'] == [{'source_type': 'vendor_credit', 'applied_minor_units': BILL_UNITS,
                                   'applied': {'amount': BILL, 'currency': 'USD',
                                               'minor_units': BILL_UNITS}}]
    # A bill settled entirely by a credit leaves the unpaid list, and the vendor still shows the
    # 20000 the credit has left as a negative payable.
    assert _unpaid(books)['rows'] == []
    assert _aging(books)['totals']['total']['minor_units'] == -20000


def test_a_bill_part_paid_in_cash_takes_a_credit_for_exactly_the_rest(books):
    """The seam read the other way round: cash first, then the credit that closes it."""
    bill = _bill(books)
    books['run']('bill pay', dict(
        date='2026-05-05', funding_account=books['bank'], method=books['check'],
        check_number='1040', bills=[{'bill': bill['id'], 'amount': '400.00'}]),
        reason='Part payment')
    assert _settlement(books, bill['id'])['open_minor_units'] == 60000

    credit = books['run']('vendor-credit post', dict(
        vendor=books['alto'], date='2026-05-10', number='VC-3',
        expenses=[{'account': books['repairs'], 'amount': '600.00'}]), reason='Credit the rest')
    applied = books['run']('vendor-credit apply', dict(
        credit=credit['id'], bills=[{'bill': bill['id']}], expected_version=credit['version']),
        reason='Close B-1 with the credit')

    # 40000 cash + 60000 credit = 100000, and nothing named the amount: the row took what was
    # still open on the bill, which is what the cash left behind.
    assert [row['amount_minor_units'] for row in applied['applications']] == [60000]
    settled = _settlement(books, bill['id'])
    assert settled['applied_minor_units'] == BILL_UNITS
    assert settled['open_minor_units'] == 0 and settled['status'] == 'paid'
    assert [(row['source_type'], row['applied_minor_units']) for row in settled['sources']] == [
        ('bill_payment', 40000), ('vendor_credit', 60000)]
    assert _unpaid(books)['rows'] == []
    # 40000 left the bank; the expense standing is 100000 debited less 60000 credited back.
    assert _ledger(books) == {books['repairs']: 40000, books['bank']: -40000}


def test_a_credit_line_takes_an_expense_account_and_never_the_payable(books):
    with pytest.raises(BookflowError) as caught:
        books['run']('vendor-credit post', dict(
            vendor=books['alto'], date='2026-05-10',
            expenses=[{'account': books['payable'], 'amount': CREDIT}]), reason='Bad account')
    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details['fields'][0]['field'] == 'expenses.0.account'
    assert books['run']('vendor-credit query', {'limit': 10})['count'] == 0


def test_the_credit_is_queryable_by_vendor_and_by_the_bill_it_settled(books):
    bill = _bill(books)
    credit = _credit(books)
    assert [row['number'] for row in books['run']('vendor-credit query', {'limit': 10})['items']] == ['VC-1']
    assert books['run']('vendor-credit query', {'vendor': books['other'], 'limit': 10})['count'] == 0
    assert books['run']('vendor-credit query', {'bill': bill['id'], 'limit': 10})['count'] == 0

    books['run']('vendor-credit apply', dict(credit=credit['id'], bills=[{'bill': bill['id']}],
                                             expected_version=credit['version']),
                 reason='Apply VC-1')
    found = books['run']('vendor-credit query', {'bill': bill['id'], 'limit': 10})
    assert [row['number'] for row in found['items']] == ['VC-1']
    assert found['items'][0]['settlement_current']['applied_minor_units'] == CREDIT_UNITS


COMMANDS = frozenset(('vendor-credit post', 'vendor-credit show', 'vendor-credit query',
                      'vendor-credit void', 'vendor-credit apply', 'vendor-credit unapply'))


@pytest.mark.timeout(300)
def test_the_same_vendor_credit_through_python_cli_http_and_mcp(root, tmp_path):
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

                expense = (await matrix.call(surface, 'account create',
                                             dict(name='Parity roofing', type='expense')))['id']
                vendor = (await matrix.call(surface, 'vendor create',
                                            dict(name='Parity Roofing Co')))['id']
                bill = await matrix.call(surface, 'bill post', dict(
                    vendor=vendor, date='2026-05-01', due_date='2026-05-31', number='PB-1',
                    expenses=[{'account': expense, 'amount': BILL, 'memo': 'Roof'}]))
                entry = dict(vendor=vendor, date='2026-05-10', number='PVC-1',
                             supplier_reference='CN-9001', memo='Parity return',
                             expenses=[{'account': expense, 'amount': CREDIT, 'memo': 'Tiles'}])
                assert (await call('vendor-credit post', entry, dry_run=True))['dry_run']
                posted = await call('vendor-credit post', entry, idempotency_key='credit-1')
                replay = await call('vendor-credit post', entry, idempotency_key='credit-1')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']
                assert posted['total']['amount'] == CREDIT

                await call('vendor-credit show', {'credit': posted['id']})
                await call('vendor-credit query', {'vendor': vendor, 'limit': 10})
                applied = await call('vendor-credit apply', {
                    'credit': posted['id'], 'bills': [{'bill': bill['id']}],
                    'expected_version': posted['version']})
                taken = await call('vendor-credit unapply', {
                    'credit': posted['id'], 'expected_version': applied['version']})
                await call('vendor-credit void', {'credit': posted['id'],
                                                  'expected_version': taken['version']})

                refused = await call('vendor-credit post', {
                    **entry, 'number': 'PVC-2',
                    'expenses': [{'account': bill['ap_account_id'], 'amount': CREDIT}]},
                    rejected=True)
                assert refused['code'] == 'E_VALIDATION'
                assert refused['details']['fields'][0]['field'] == 'expenses.0.account'
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
