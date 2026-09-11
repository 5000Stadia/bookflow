"""Pay a customer back, and check the books by hand.

Every figure asserted here is written out in full at the top of the module: a 100.00 invoice
with a 30.00 credit refunded in cash, and a taxed 108.00 invoice paid in full with one of its
four units returned and then disposed of three ways. What the trial balance, the A/R aging,
the general ledger and the customer statement say at each step is checked against those
figures through the real report commands, never against the refund's own output.

The two things this file exists to prove:

**A refund reverses no revenue a second time.** The credit memo already took the income and
the sales tax back down. Income is therefore asserted *unchanged* across the refund, which is
the only way a second reversal would be visible -- the totals would still balance.

**A credit is consumed once.** After a refund the same credit cannot also be applied to an
invoice, and after an application it cannot be refunded beyond what is left. Both are
`E_CREDIT_UNAVAILABLE`, and both are asserted to have written nothing.
"""
import pytest

from tests.credit_support import (  # noqa: F401  (books is a fixture)
    aging, apply_credit, balances, books, goodwill_credit, invoice, refund, settlement,
    taxed_invoice, ties_to_the_receivable, worth, worth_version,
)

# The money.
INVOICE = 10000             # 100.00
CREDIT = 3000               # 30.00, refunded in full on 2026-03-20
# The taxed invoice: four units at 25.00, 8% on the whole 100.00 net, paid in full.
TAXED_NET = 10000
TAXED_TAX = 800
TAXED_GROSS = 10800
# One unit back: floor(10000*1/4) = 2500 of net, floor(800*2500/10000) = 200 of tax.
RETURN_NET = 2500
RETURN_TAX = 200
RETURN_GROSS = 2700


def _returned(books_):
    """A taxed invoice paid in full, with one of its four units sent back."""
    sale = taxed_invoice(books_)
    line = sale['revision']['lines'][0]
    books_['run']('payment receive', dict(
        customer=books_['kerr'], date='2026-03-05', amount='108.00', operation_key='paid-in-full',
        deposit_to=books_['checking'], payment_method=books_['method'],
        applications={'mode': 'inline', 'items': [
            {'invoice': sale['id'], 'expected_version': sale['version'], 'amount': '108.00'}]}),
        reason='Receive the money')
    credit = books_['run']('credit-memo post', dict(
        customer=books_['kerr'], date='2026-04-05',
        lines=[{'source_invoice': sale['id'], 'source_line': line['line_id'], 'quantity': '1'}]),
        reason='One unit came back')
    return sale, credit


# ---------------------------------------------------------------- O1 row 5


def test_a_refund_pays_the_customer_and_reverses_no_revenue_a_second_time(books):
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    before, _ = balances(books)
    assert before[books['receivable']] == INVOICE - CREDIT
    assert before[books['income']] == -(INVOICE - CREDIT)

    paid = refund(books, credit['id'])

    assert paid['type'] == 'customer_refund' and paid['status'] == 'posted'
    assert paid['total']['minor_units'] == CREDIT
    assert paid['customer_id'] == books['ridge']
    after, _ = balances(books)
    # The receivable goes back up by the credit, the bank falls by it, and income does not move.
    assert after[books['receivable']] == INVOICE
    assert after[books['income']] == -(INVOICE - CREDIT)
    assert after[books['checking']] == -CREDIT
    assert worth(books, credit['id'])['available_minor_units'] == 0
    assert worth(books, credit['id'])['refunded_minor_units'] == CREDIT


def test_a_refund_posts_exactly_one_receivable_debit_and_one_bank_credit(books):
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    paid = refund(books, credit['id'])
    ledger = books['run']('report general-ledger', dict(
        date_from='2026-03-01', date_to='2026-03-31', limit=200))
    legs = [(row['account_id'], row['debit']['minor_units'], row['credit']['minor_units'])
            for row in ledger['rows'] if row.get('transaction_id') == paid['id']]
    assert sorted(legs) == sorted([(books['receivable'], CREDIT, 0), (books['checking'], 0, CREDIT)])
    assert all(row['transaction_type'] == 'customer_refund' for row in ledger['rows']
               if row.get('transaction_id') == paid['id'])
    # Nothing landed on income or on the tax liability, which is the whole point.
    assert books['income'] not in {row[0] for row in legs}
    assert books['liability'] not in {row[0] for row in legs}


def test_the_aging_reads_the_refund_and_still_ties_to_the_receivable(books):
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    rows, _ = ties_to_the_receivable(books, '2026-03-31')
    assert rows['Ridge']['total']['minor_units'] == INVOICE - CREDIT

    refund(books, credit['id'])

    rows, _ = ties_to_the_receivable(books, '2026-03-31')
    # The credit's -30.00 and the refund's +30.00 are both aged by their own dates and net to
    # zero in the same bucket; the invoice is what is left, and the total is the control.
    assert rows['Ridge']['current']['minor_units'] == INVOICE
    assert rows['Ridge']['days_1_30']['minor_units'] == 0
    assert rows['Ridge']['total']['minor_units'] == INVOICE


def test_the_statement_names_the_refund_and_closes_at_the_full_invoice(books):
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    refund(books, credit['id'])
    statement = books['run']('report statement', dict(
        date_from='2026-03-01', date_to='2026-03-31', customer=books['ridge']))
    assert [(row['entry'], row['amount']['minor_units'])
            for row in statement['rows'] if row['kind'] == 'activity'] == [
        ('invoice', INVOICE), ('credit_memo', -CREDIT), ('customer_refund', CREDIT)]
    closing = next(row for row in statement['rows'] if row['kind'] == 'closing')
    assert closing['balance']['minor_units'] == INVOICE


def test_a_second_refund_of_the_same_credit_is_refused_and_writes_nothing(books):
    from bookflow.core.errors import BookflowError
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    refund(books, credit['id'])
    before, _ = balances(books)

    with pytest.raises(BookflowError) as refused:
        refund(books, credit['id'], amount='5.00', date='2026-03-21')

    assert refused.value.code == 'E_CREDIT_UNAVAILABLE'
    assert refused.value.details['available']['minor_units'] == 0
    after, _ = balances(books)
    assert after == before
    assert books['run']('customer-refund query', {'limit': 50})['count'] == 1


def test_a_refunded_credit_cannot_also_be_applied(books):
    from bookflow.core.errors import BookflowError
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    refund(books, credit['id'])
    later = invoice(books, date='2026-03-22', due='2026-04-22')

    with pytest.raises(BookflowError) as refused:
        apply_credit(books, credit, later['id'], amount='5.00')

    assert refused.value.code == 'E_CREDIT_UNAVAILABLE'
    assert settlement(books, later['id'])['due_minor_units'] == INVOICE


# ---------------------------------------------------------------- O2: the three dispositions


def test_a_taxed_return_against_a_paid_invoice_can_only_be_retained_applied_or_refunded(books):
    from bookflow.core.errors import BookflowError
    sale, credit = _returned(books)
    assert credit['total']['minor_units'] == RETURN_GROSS
    ledger, _ = balances(books)
    assert ledger[books['receivable']] == -RETURN_GROSS
    assert ledger[books['income']] == -(TAXED_NET - RETURN_NET)
    assert ledger[books['liability']] == -(TAXED_TAX - RETURN_TAX)
    assert settlement(books, sale['id'])['due_minor_units'] == 0

    # It cannot go back on the invoice it came from: that invoice owes nothing.
    with pytest.raises(BookflowError) as refused:
        apply_credit(books, credit, sale['id'], amount='27.00')
    assert refused.value.code == 'E_APPLICATION_CAPACITY'
    assert refused.value.details['side'] == 'target'

    # Disposition 1 -- retain. The customer's receivable is negative by the credit.
    rows, _ = ties_to_the_receivable(books)
    assert rows['Kerr']['total']['minor_units'] == -RETURN_GROSS
    assert worth(books, credit['id'])['available_minor_units'] == RETURN_GROSS


def test_the_retained_credit_can_be_applied_to_another_invoice_instead(books):
    from bookflow.core.errors import BookflowError
    sale, credit = _returned(books)
    other = books['run']('invoice post', dict(
        customer=books['kerr'], date='2026-04-06', due_date='2026-05-06',
        lines=[{'item': books['service'], 'quantity': '1', 'net_amount': '40.00'}]),
        reason='Bill again')
    before, _ = balances(books)

    applied = apply_credit(books, credit, other['id'], amount='27.00')

    after, _ = balances(books)
    assert after == before                       # disposition 2 posts nothing
    assert applied['effect']['document_changes'][0]['due_minor_units'] == 4000 - RETURN_GROSS
    assert worth(books, credit['id'])['available_minor_units'] == 0
    rows, _ = ties_to_the_receivable(books)
    assert rows['Kerr']['total']['minor_units'] == 4000 - RETURN_GROSS

    # Disposition 2 and 3 are mutually exclusive by capacity.
    with pytest.raises(BookflowError) as refused:
        refund(books, credit['id'], amount='27.00', date='2026-04-07')
    assert refused.value.code == 'E_CREDIT_UNAVAILABLE'


def test_the_retained_credit_can_be_refunded_instead_without_touching_income_or_tax(books):
    from bookflow.core.errors import BookflowError
    sale, credit = _returned(books)

    paid = refund(books, credit['id'], date='2026-04-07')

    assert paid['total']['minor_units'] == RETURN_GROSS
    ledger, _ = balances(books)
    assert ledger.get(books['receivable'], 0) == 0
    assert ledger[books['income']] == -(TAXED_NET - RETURN_NET)     # still 75.00 recognised
    assert ledger[books['liability']] == -(TAXED_TAX - RETURN_TAX)  # still 6.00 owed
    assert ledger[books['checking']] == TAXED_GROSS - RETURN_GROSS
    _, report = ties_to_the_receivable(books)
    assert report['totals']['total']['minor_units'] == 0
    assert worth(books, credit['id'])['available_minor_units'] == 0

    other = books['run']('invoice post', dict(
        customer=books['kerr'], date='2026-04-08', due_date='2026-05-08',
        lines=[{'item': books['service'], 'quantity': '1', 'net_amount': '40.00'}]),
        reason='Bill again')
    with pytest.raises(BookflowError) as refused:
        apply_credit(books, credit, other['id'], amount='27.00')
    assert refused.value.code == 'E_CREDIT_UNAVAILABLE'


# ---------------------------------------------------------------- void, reads and refusals


def test_voiding_a_refund_reverses_it_and_gives_the_credit_back(books):
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    before, _ = balances(books)
    paid = refund(books, credit['id'])

    voided = books['run']('customer-refund void',
                          dict(refund=paid['id'], expected_version=paid['version']),
                          reason='Wrong bank account')

    assert voided['status'] == 'voided' and voided['void_reason'] == 'Wrong bank account'
    assert [batch['kind'] for batch in voided['revision']['batches']] == ['original', 'reversal']
    assert {batch['effective_date'] for batch in voided['revision']['batches']} == {'2026-03-20'}
    after, _ = balances(books)
    assert after == before
    assert worth(books, credit['id'])['available_minor_units'] == CREDIT
    assert worth(books, credit['id'])['refunded_minor_units'] == 0
    assert sorted((row['kind'], row['amount_minor_units']) for row in voided['consumptions']) == [
        ('consume', CREDIT), ('release', CREDIT)]
    # The number stays occupied and the document stays readable.
    assert books['run']('customer-refund show', {'refund': paid['id']})['number'] == paid['number']


def test_a_voided_refund_cannot_be_voided_again_and_needs_a_reason(books):
    from bookflow.core.errors import BookflowError
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    paid = refund(books, credit['id'])
    with pytest.raises(BookflowError) as no_reason:
        books['run']('customer-refund void', dict(refund=paid['id']))
    assert no_reason.value.code == 'E_REASON_REQUIRED'
    books['run']('customer-refund void', dict(refund=paid['id']), reason='Wrong account')
    with pytest.raises(BookflowError) as again:
        books['run']('customer-refund void', dict(refund=paid['id']), reason='Wrong account')
    assert again.value.code == 'E_APPLICATION_INACTIVE'


def test_a_refund_is_drawn_on_a_bank_account_and_carries_a_check_number_only_on_a_check(books):
    from bookflow.core.errors import BookflowError
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    with pytest.raises(BookflowError) as wrong_account:
        refund(books, credit['id'], funding_account=books['receivable'])
    assert wrong_account.value.code == 'E_VALIDATION'
    assert wrong_account.value.details['fields'][0]['field'] == 'funding_account'

    cash = next(row['id'] for row in books['run']('payment-method list', {})['items']
                if row['kind'] == 'cash')
    with pytest.raises(BookflowError) as wrong_method:
        refund(books, credit['id'], method=cash, check_number='1041')
    assert wrong_method.value.details['fields'][0]['field'] == 'check_number'

    paid = refund(books, credit['id'], check_number='1041')
    assert paid['check_number'] == '1041'
    assert books['run']('customer-refund query', {'check_number': '1041'})['count'] == 1


def test_one_refund_pays_back_one_customer(books):
    from bookflow.core.errors import BookflowError
    invoice(books)
    invoice(books, customer=books['kerr'])
    mine = goodwill_credit(books, '30.00')
    theirs = goodwill_credit(books, '30.00', customer=books['kerr'], date='2026-03-11')
    with pytest.raises(BookflowError) as mixed:
        books['run']('customer-refund post', dict(
            date='2026-03-20', funding_account=books['checking'], method=books['method'],
            sources=[{'credit_memo': mine['id']}, {'credit_memo': theirs['id']}]),
            reason='Refund two customers at once')
    assert mixed.value.code == 'E_APPLICATION_INCOMPATIBLE'

    with pytest.raises(BookflowError) as guarded:
        refund(books, mine['id'], customer=books['kerr'])
    assert guarded.value.code == 'E_VALIDATION'
    assert guarded.value.details['fields'][0]['field'] == 'customer'


def test_a_refund_cannot_be_dated_before_the_credit_it_pays_out(books):
    from bookflow.core.errors import BookflowError
    invoice(books)
    credit = goodwill_credit(books, '30.00')
    with pytest.raises(BookflowError) as refused:
        refund(books, credit['id'], date='2026-03-09')
    assert refused.value.code == 'E_VALIDATION'
    assert refused.value.details['fields'][0]['field'] == 'date'


def test_the_refund_list_pages_by_date_and_filters(books):
    invoice(books)
    first = goodwill_credit(books, '30.00')
    second = goodwill_credit(books, '30.00', date='2026-03-11')
    early = refund(books, first['id'], date='2026-03-20')
    late = refund(books, second['id'], date='2026-03-25')
    page = books['run']('customer-refund query', {'limit': 50})
    assert [row['id'] for row in page['items']] == [early['id'], late['id']]
    assert [row['total']['minor_units'] for row in page['items']] == [CREDIT, CREDIT]
    reversed_page = books['run']('customer-refund query', {'limit': 50, 'direction': 'desc'})
    assert [row['id'] for row in reversed_page['items']] == [late['id'], early['id']]
    assert books['run']('customer-refund query', {'customer': books['kerr']})['count'] == 0
    assert books['run']('customer-refund query', {'date_from': '2026-03-21'})['count'] == 1
    assert books['run']('customer-refund query', {'status': 'voided'})['count'] == 0
    shown = books['run']('customer-refund show', {'refund': early['id']})
    assert shown['funding_account_id'] == books['checking']
    assert [(row['credit_memo_id'], row['amount_minor_units']) for row in shown['consumptions']] \
        == [(first['id'], CREDIT)]


COMMANDS = frozenset(('customer-refund post', 'customer-refund show', 'customer-refund query',
                      'customer-refund void'))


@pytest.mark.timeout(300)
def test_the_same_refund_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio
    from copy import deepcopy

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
                                            dict(name='Parity refund income', type='income')))['id']
                bank = (await matrix.call(surface, 'account create',
                                          dict(name='Parity refund bank', type='bank')))['id']
                exempt = next(row['id'] for row in (await matrix.call(
                    surface, 'sales-tax-code list', {}))['items'] if not row['taxable'])
                item = (await matrix.call(surface, 'item create', dict(
                    name='Parity refunded service', type='service', sales_enabled=True,
                    description='Parity service', income_account_id=income, price='40.00',
                    sales_tax_code_id=exempt)))['id']
                method = next(row['id'] for row in (await matrix.call(
                    surface, 'payment-method list', {}))['items'] if row['kind'] == 'check')
                customer = (await matrix.call(surface, 'customer create',
                                              dict(name='Parity Refund Co')))['id']
                await matrix.call(surface, 'invoice post', dict(
                    customer=customer, date='2026-03-02', number='PARITY-REFUND-INV',
                    lines=[{'item': item, 'quantity': '2', 'unit_price': '40.00'}]))
                credit = await matrix.call(surface, 'credit-memo post', dict(
                    customer=customer, date='2026-03-06', number='PARITY-REFUND-CM',
                    lines=[{'item': item, 'quantity': '1', 'unit_price': '40.00'}]))

                request = dict(date='2026-03-08', number='PARITY-RF-1', funding_account=bank,
                               method=method, check_number='9001', memo='Parity refund',
                               sources=[{'credit_memo': credit['id']}])
                assert (await call('customer-refund post', request, dry_run=True))['dry_run']
                paid = await call('customer-refund post', request, idempotency_key='refund-1')
                replay = await call('customer-refund post', request, idempotency_key='refund-1')
                assert replay['id'] == paid['id'] and replay['idempotent_replay']
                assert paid['total']['amount'] == '40.00'

                await call('customer-refund show', {'refund': paid['id']})
                await call('customer-refund query', {'limit': 10})

                refused = await call('customer-refund post', {
                    **request, 'number': 'PARITY-RF-2'}, rejected=True)
                assert refused['code'] == 'E_CREDIT_UNAVAILABLE'
                voided = await call('customer-refund void',
                                    {'refund': paid['id'], 'expected_version': paid['version']})
                assert voided['status'] == 'voided'
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
