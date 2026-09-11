"""Apply a credit to an invoice, take it back, and check the books by hand.

Every figure asserted here is written out in full at the top of the module, so a reader can
add it up without running anything: a 100.00 invoice and a 30.00 credit, applied and unapplied
again. What the trial balance, the A/R aging and `invoice show` say at each step is checked
against those figures through the real report commands, never against the settlement's own
output.

The claim this file exists to test is the one the design rests on: **applying a credit changes
no posting leg at all.** The credit memo already moved the money; what an application changes
is what the invoice still owes. So the trial balance is asserted identical either side of the
apply, cent for cent, rather than merely balanced.

The second half is the reason the credit reuses the invoice's own settlement edge rather than
a table of its own. Three owners of `applications` were argued to be correct with no edit;
each is exercised here through its real command.
"""
import pytest

from tests.credit_support import (  # noqa: F401  (books is a fixture)
    aging, apply_credit, balances, books, goodwill_credit, invoice, settlement,
    ties_to_the_receivable, worth, worth_version,
)

# The money. Everything below is arithmetic on these.
INVOICE = 10000             # 100.00, four units at 25.00, due 2026-04-01
CREDIT = 3000               # 30.00, dated 2026-03-10
STILL_OWED = INVOICE - CREDIT   # 7000


def _applied(books_, credit, invoice_id, **kw):
    result = apply_credit(books_, credit, invoice_id, **kw)
    return result, result['effect']['applications'][0]['application_id']


def _unapply(books_, credit_id, application_id, invoice_version, **extra):
    return books_['run']('customer-credit unapply', dict(
        credit_memo=credit_id, expected_version=worth_version(books_, credit_id),
        applications=[{'application_id': application_id,
                       'invoice_expected_version': invoice_version}], **extra),
        reason='Take the credit back off')


# ---------------------------------------------------------------- O1 rows 3 and 4


def test_applying_a_credit_moves_no_posting_leg_only_what_the_invoice_owes(books):
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    before, before_report = balances(books)
    assert before[books['receivable']] == STILL_OWED and before[books['income']] == -STILL_OWED

    result, _ = _applied(books, credit, sale['id'], amount='30.00')

    after, after_report = balances(books)
    # Not "still balanced" -- identical. An application that posted anything would show here.
    assert after == before
    assert after_report['rows'] == before_report['rows']
    assert settlement(books, sale['id'])['due_minor_units'] == STILL_OWED
    assert settlement(books, sale['id'])['status'] == 'partial'
    assert worth(books, credit['id'])['available_minor_units'] == 0
    assert worth(books, credit['id'])['applied_minor_units'] == CREDIT
    assert result['effect']['kind'] == 'apply'
    assert [row['amount']['minor_units'] for row in result['effect']['applications']] == [CREDIT]
    assert sum(row['amount']['minor_units'] for row in result['effect']['allocations']) == CREDIT


def test_unapplying_restores_the_invoice_and_the_credit_at_the_original_date(books):
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    before, _ = balances(books)
    result, application = _applied(books, credit, sale['id'], amount='30.00')
    invoice_version = result['effect']['document_changes'][0]['version']

    undone = _unapply(books, credit['id'], application, invoice_version)

    after, _ = balances(books)
    assert after == before
    assert settlement(books, sale['id'])['due_minor_units'] == INVOICE
    assert settlement(books, sale['id'])['status'] == 'unpaid'
    assert worth(books, credit['id'])['available_minor_units'] == CREDIT
    # The inverse carries the original application's date, never today's.
    assert [row['effective_date'] for row in undone['effect']['applications']] == ['2026-03-10']
    assert [row['kind'] for row in undone['effect']['applications']] == ['unapply']
    assert all(row['kind'] == 'reversal' for row in undone['effect']['allocations'])


def test_the_aging_total_is_the_receivable_control_before_during_and_after(books):
    # Aged as of 31 March, when the invoice is not yet due and the credit is three weeks old.
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    rows, _ = ties_to_the_receivable(books, '2026-03-31')
    # Before the apply the invoice is not due until 1 April, so it is current; the credit is
    # aged by its own date, three weeks back, which is what gives it a column of its own.
    assert rows['Ridge']['current']['minor_units'] == INVOICE
    assert rows['Ridge']['days_1_30']['minor_units'] == -CREDIT
    assert rows['Ridge']['total']['minor_units'] == STILL_OWED

    result, application = _applied(books, credit, sale['id'], amount='30.00')
    rows, _ = ties_to_the_receivable(books, '2026-03-31')
    # After it, the credit's row nets to zero and is omitted; the invoice is what is left.
    assert rows['Ridge']['current']['minor_units'] == STILL_OWED
    assert rows['Ridge']['days_1_30']['minor_units'] == 0
    assert rows['Ridge']['total']['minor_units'] == STILL_OWED

    _unapply(books, credit['id'], application, result['effect']['document_changes'][0]['version'])
    rows, _ = ties_to_the_receivable(books, '2026-03-31')
    assert rows['Ridge']['current']['minor_units'] == INVOICE
    assert rows['Ridge']['days_1_30']['minor_units'] == -CREDIT
    assert rows['Ridge']['total']['minor_units'] == STILL_OWED


def test_the_statement_closes_at_what_the_customer_owes_whether_the_credit_is_applied(books):
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    statement = books['run']('report statement', dict(
        date_from='2026-03-01', date_to='2026-03-31', customer=books['ridge']))
    closing = next(row for row in statement['rows'] if row['kind'] == 'closing')
    assert closing['balance']['minor_units'] == STILL_OWED

    _applied(books, credit, sale['id'], amount='30.00')
    statement = books['run']('report statement', dict(
        date_from='2026-03-01', date_to='2026-03-31', customer=books['ridge']))
    closing = next(row for row in statement['rows'] if row['kind'] == 'closing')
    # The settlement's two halves are equal and opposite inside one customer, so the statement
    # shows the documents and no movement row, and the close is the same money either way.
    assert closing['balance']['minor_units'] == STILL_OWED
    assert [row['entry'] for row in statement['rows'] if row['kind'] == 'activity'] == [
        'invoice', 'credit_memo']


# ---------------------------------------------------------------- the shared-edge owners


def test_an_invoice_with_an_applied_credit_cannot_be_voided(books):
    """`sales.prepare` calls `active_applications`, which does not care what supplied them."""
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    result, application = _applied(books, credit, sale['id'], amount='30.00')
    version = result['effect']['document_changes'][0]['version']

    with pytest.raises(BookflowError) as refused:
        books['run']('invoice void', dict(invoice=sale['id'], expected_version=version),
                     reason='Cancel the sale')
    assert refused.value.code == 'E_HAS_APPLICATIONS'
    assert settlement(books, sale['id'])['status'] == 'partial'

    # Take the credit off and the void goes through, so the refusal was about the credit.
    _unapply(books, credit['id'], application, version)
    books['run']('invoice void', dict(
        invoice=sale['id'], expected_version=books['run'](
            'invoice show', {'invoice': sale['id']})['version']), reason='Cancel the sale')
    assert settlement(books, sale['id'])['status'] == 'voided'


def _correct(books_, sale_id, credit_id, key, **fields):
    request = dict(invoice=sale_id, operation_key=key,
                   expected_version=books_['run']('invoice show', {'invoice': sale_id})['version'],
                   settlement_versions=[{'payment': credit_id,
                                         'expected_version': worth_version(books_, credit_id)}])
    request.update(fields)
    return books_['run']('invoice update', request, reason='Correct the invoice')


def test_correcting_an_invoice_under_an_applied_credit_is_refused_by_the_existing_guards(books):
    """`payment_restatement`'s two refusals, reached through a credit rather than a receipt."""
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    _applied(books, credit, sale['id'], amount='30.00')

    with pytest.raises(BookflowError) as too_small:
        _correct(books, sale['id'], credit['id'], 'shrink',
                 lines=[{'item': books['service'], 'quantity': '1', 'unit_price': '20.00'}])
    assert too_small.value.code == 'E_APPLIED_EXCEEDS_TOTAL'

    with pytest.raises(BookflowError) as too_late:
        _correct(books, sale['id'], credit['id'], 'redate', date='2026-03-20',
                 lines=[{'item': books['service'], 'quantity': '4', 'unit_price': '25.00'}])
    assert too_late.value.code == 'E_HAS_APPLICATIONS'
    assert too_late.value.details['field'] == 'date'
    # Neither refusal wrote anything.
    assert settlement(books, sale['id'])['gross_minor_units'] == INVOICE


def test_correcting_an_invoice_restates_the_credits_allocations(books):
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    _applied(books, credit, sale['id'], amount='30.00')
    line = sale['revision']['lines'][0]['line_id']

    corrected = _correct(books, sale['id'], credit['id'], 'grow', lines=[
        {'line_id': line, 'item': books['service'], 'quantity': '4', 'unit_price': '25.00'},
        {'item': books['service'], 'quantity': '1', 'unit_price': '10.00'}])

    allocations = corrected['settlement']['effect']['allocations']
    assert sorted((row['kind'], row['amount']['minor_units']) for row in allocations) == [
        ('allocation', 273), ('allocation', 2727), ('reversal', 3000)]
    # 3000 over a 10000 and a 1000 component by largest remainder: 2727 and 273.
    assert sum(row['amount']['minor_units'] for row in allocations if row['kind'] == 'allocation') == CREDIT
    current = settlement(books, sale['id'])
    assert (current['gross_minor_units'], current['applied_minor_units'], current['due_minor_units']) \
        == (11000, CREDIT, 8000)
    ledger, _ = balances(books)
    assert ledger[books['receivable']] == 11000 - CREDIT
    ties_to_the_receivable(books)


def test_a_later_cash_payment_cannot_relieve_what_the_credit_already_did(books):
    """`payments._target_components` subtracts a credit's live allocations before allocating.

    Without the credit writing `application_allocations`, a 30.00 credit and a 70.01 payment
    would both fit inside 100.00 of nominal component capacity and the extra cent would be
    mis-attributed in silence, because `calc.allocate` only raises above the total.
    """
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    _applied(books, credit, sale['id'], amount='30.00')

    with pytest.raises(BookflowError) as refused:
        books['run']('payment receive', dict(
            customer=books['ridge'], date='2026-03-15', amount='70.01', operation_key='over',
            deposit_to=books['checking'], payment_method=books['method'],
            applications={'mode': 'inline', 'items': [
                {'invoice': sale['id'], 'expected_version': 2, 'amount': '70.01'}]}),
            reason='Receive the rest')
    assert refused.value.code == 'E_APPLICATION_CAPACITY'
    assert refused.value.details['available']['minor_units'] == STILL_OWED

    paid = books['run']('payment receive', dict(
        customer=books['ridge'], date='2026-03-15', amount='70.00', operation_key='exact',
        deposit_to=books['checking'], payment_method=books['method'],
        applications={'mode': 'inline', 'items': [
            {'invoice': sale['id'], 'expected_version': 2, 'amount': '70.00'}]}),
        reason='Receive the rest')
    assert sum(row['amount']['minor_units'] for row in paid['effect']['allocations']) == STILL_OWED
    current = settlement(books, sale['id'])
    assert (current['applied_minor_units'], current['due_minor_units'], current['status']) \
        == (INVOICE, 0, 'paid')
    ledger, _ = balances(books)
    assert ledger.get(books['receivable'], 0) == 0
    assert ledger[books['checking']] == STILL_OWED


# ---------------------------------------------------------------- what is refused


def test_a_credit_only_settles_its_own_customer_and_account(books):
    from bookflow.core.errors import BookflowError
    other = invoice(books, customer=books['kerr'])
    credit = goodwill_credit(books, '30.00')
    with pytest.raises(BookflowError) as refused:
        apply_credit(books, credit, other['id'], amount='30.00')
    assert refused.value.code == 'E_APPLICATION_INCOMPATIBLE'
    assert refused.value.details['credit_party_id'] == books['ridge']
    assert refused.value.details['invoice_party_id'] == books['kerr']
    assert worth(books, credit['id'])['available_minor_units'] == CREDIT


def test_applying_more_than_the_credit_is_worth_is_refused_whole(books):
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    with pytest.raises(BookflowError) as refused:
        apply_credit(books, credit, sale['id'], amount='30.01')
    assert refused.value.code == 'E_CREDIT_UNAVAILABLE'
    assert refused.value.details['available']['minor_units'] == CREDIT
    assert worth(books, credit['id'])['applied_minor_units'] == 0
    assert settlement(books, sale['id'])['due_minor_units'] == INVOICE


def test_applying_more_than_the_invoice_owes_is_refused(books):
    from bookflow.core.errors import BookflowError
    small = invoice(books, lines=[{'item': books['service'], 'quantity': '1', 'unit_price': '10.00'}])
    credit = goodwill_credit(books, '30.00')
    with pytest.raises(BookflowError) as refused:
        apply_credit(books, credit, small['id'], amount='30.00')
    assert refused.value.code == 'E_APPLICATION_CAPACITY'
    assert refused.value.details['side'] == 'target'
    assert refused.value.details['available']['minor_units'] == 1000


def test_an_omitted_amount_takes_what_the_invoice_still_owes(books):
    small = invoice(books, lines=[{'item': books['service'], 'quantity': '1', 'unit_price': '10.00'}])
    credit = goodwill_credit(books, '30.00')
    result = apply_credit(books, credit, small['id'])
    assert result['effect']['document_changes'][0]['due_minor_units'] == 0
    assert result['effect']['document_changes'][0]['status'] == 'paid'
    assert worth(books, credit['id'])['available_minor_units'] == CREDIT - 1000


def test_an_application_cannot_be_dated_before_the_credit(books):
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    with pytest.raises(BookflowError) as refused:
        apply_credit(books, credit, sale['id'], amount='30.00', date='2026-03-09')
    assert refused.value.code == 'E_VALIDATION'
    assert refused.value.details['fields'][0]['field'] == 'date'


def test_a_closed_period_refuses_the_application_and_its_inverse(books):
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    spare = goodwill_credit(books, '30.00', date='2026-03-11')
    result, application = _applied(books, credit, sale['id'], amount='30.00')
    version = result['effect']['document_changes'][0]['version']
    books['run']('company update', {'closing_date': '2026-03-31'}, reason='Close March')

    # The inverse is dated where the application was, so closing that period closes the undo.
    with pytest.raises(BookflowError) as refused:
        _unapply(books, credit['id'], application, version)
    assert refused.value.code == 'E_PERIOD_CLOSED'
    assert worth(books, credit['id'])['available_minor_units'] == 0

    # And a settlement dated into the closed period is refused before anything else is read.
    later = invoice(books, date='2026-04-02', due='2026-05-02')
    with pytest.raises(BookflowError) as closed:
        apply_credit(books, spare, later['id'], amount='30.00', date='2026-03-15')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    assert worth(books, spare['id'])['available_minor_units'] == CREDIT

    # Dated where the period is open, the same application goes through.
    applied = apply_credit(books, spare, later['id'], amount='30.00', date='2026-04-02')
    assert applied['effect']['document_changes'][0]['due_minor_units'] == INVOICE - CREDIT


def test_each_invoice_is_selected_once_and_a_stale_version_is_refused(books):
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    version = books['run']('invoice show', {'invoice': sale['id']})['version']
    with pytest.raises(BookflowError) as twice:
        books['run']('customer-credit apply', dict(
            credit_memo=credit['id'], expected_version=credit['version'], applications=[
                {'invoice': sale['id'], 'expected_version': version, 'amount': '10.00'},
                {'invoice': sale['id'], 'expected_version': version, 'amount': '10.00'}]),
            reason='Apply twice')
    assert twice.value.code == 'E_VALIDATION'

    with pytest.raises(BookflowError) as stale:
        books['run']('customer-credit apply', dict(
            credit_memo=credit['id'], expected_version=credit['version'] + 5, applications=[
                {'invoice': sale['id'], 'expected_version': version, 'amount': '10.00'}]),
            reason='Apply with a stale version')
    assert stale.value.code == 'E_VERSION_CONFLICT'


def test_a_preview_fingerprint_round_trips_and_a_stale_one_is_refused(books):
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    request = dict(credit_memo=credit['id'], expected_version=credit['version'],
                   applications=[{'invoice': sale['id'], 'expected_version': sale['version'],
                                  'amount': '30.00'}])
    preview = books['run']('customer-credit apply', request, reason='Apply', dry_run=True)
    assert preview['dry_run'] and settlement(books, sale['id'])['due_minor_units'] == INVOICE

    with pytest.raises(BookflowError) as stale:
        books['run']('customer-credit apply',
                     dict(request, expected_facts_fingerprint='0' * 64), reason='Apply')
    assert stale.value.code == 'E_PREVIEW_STALE'
    assert stale.value.details['current_facts_fingerprint'] == preview['facts_fingerprint']

    saved = books['run']('customer-credit apply',
                         dict(request, expected_facts_fingerprint=preview['facts_fingerprint']),
                         reason='Apply')
    assert saved['effect']['document_changes'][0]['due_minor_units'] == STILL_OWED


def test_an_application_that_is_already_undone_cannot_be_undone_again(books):
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    result, application = _applied(books, credit, sale['id'], amount='30.00')
    version = result['effect']['document_changes'][0]['version']
    _unapply(books, credit['id'], application, version)
    with pytest.raises(BookflowError) as refused:
        _unapply(books, credit['id'], application,
                 books['run']('invoice show', {'invoice': sale['id']})['version'])
    assert refused.value.code == 'E_APPLICATION_INACTIVE'


def test_a_credit_spread_over_two_invoices_lands_exactly(books):
    first = invoice(books, lines=[{'item': books['service'], 'quantity': '1', 'unit_price': '12.34'}])
    second = invoice(books, date='2026-03-03',
                     lines=[{'item': books['service'], 'quantity': '1', 'unit_price': '20.00'}])
    credit = goodwill_credit(books, '30.00')
    before, _ = balances(books)
    result = books['run']('customer-credit apply', dict(
        credit_memo=credit['id'], expected_version=credit['version'], applications=[
            {'invoice': first['id'],
             'expected_version': books['run']('invoice show', {'invoice': first['id']})['version']},
            {'invoice': second['id'],
             'expected_version': books['run']('invoice show', {'invoice': second['id']})['version'],
             'amount': '17.66'}]), reason='Spread the credit')
    after, _ = balances(books)
    assert after == before
    assert [row['due_minor_units'] for row in result['effect']['document_changes']] == [0, 234]
    assert worth(books, credit['id'])['available_minor_units'] == 0
    ties_to_the_receivable(books)


COMMANDS = frozenset(('customer-credit apply', 'customer-credit unapply'))


@pytest.mark.timeout(300)
def test_the_same_credit_application_through_python_cli_http_and_mcp(root, tmp_path):
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
                                            dict(name='Parity settlement income', type='income')))['id']
                exempt = next(row['id'] for row in (await matrix.call(
                    surface, 'sales-tax-code list', {}))['items'] if not row['taxable'])
                item = (await matrix.call(surface, 'item create', dict(
                    name='Parity settled service', type='service', sales_enabled=True,
                    description='Parity service', income_account_id=income, price='40.00',
                    sales_tax_code_id=exempt)))['id']
                customer = (await matrix.call(surface, 'customer create',
                                              dict(name='Parity Settlement Co')))['id']
                sale = await matrix.call(surface, 'invoice post', dict(
                    customer=customer, date='2026-03-02', number='PARITY-SETTLE-INV',
                    lines=[{'item': item, 'quantity': '2', 'unit_price': '40.00'}]))
                credit = await matrix.call(surface, 'credit-memo post', dict(
                    customer=customer, date='2026-03-06', number='PARITY-SETTLE-CM',
                    lines=[{'item': item, 'quantity': '1', 'unit_price': '40.00'}]))

                request = dict(credit_memo=credit['id'], expected_version=credit['version'],
                               applications=[{'invoice': sale['id'],
                                              'expected_version': sale['version'],
                                              'amount': '40.00'}])
                assert (await call('customer-credit apply', request, dry_run=True))['dry_run']
                applied = await call('customer-credit apply', request, idempotency_key='apply-1')
                replay = await call('customer-credit apply', request, idempotency_key='apply-1')
                assert replay['id'] == applied['id']
                assert applied['effect']['document_changes'][0]['due_minor_units'] == 4000

                undo = dict(credit_memo=credit['id'], expected_version=applied['version'],
                            applications=[{
                                'application_id': applied['effect']['applications'][0]['application_id'],
                                'invoice_expected_version':
                                    applied['effect']['document_changes'][0]['version']}])
                undone = await call('customer-credit unapply', undo, idempotency_key='unapply-1')
                assert undone['current']['available_minor_units'] == 4000

                refused = await call('customer-credit apply', {
                    **request, 'expected_version': undone['version'],
                    'applications': [{**request['applications'][0],
                                      'expected_version': undone['effect']['document_changes'][0]['version'],
                                      'amount': '40.01'}]}, rejected=True)
                assert refused['code'] == 'E_CREDIT_UNAVAILABLE'
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
