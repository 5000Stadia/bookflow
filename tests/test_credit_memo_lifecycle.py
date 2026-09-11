"""The rest of a credit memo's own life: void it, list it, and protect what it claimed.

The figures: a three-unit line worth 1.00 plus 0.07 tax, returned one unit at a time, and a
108.00 taxed invoice with one of four units sent back. What the trial balance says after each
void is checked against those figures through the real report commands.

Two properties are the reason this file exists.

**A void gives the source interval back, and re-returning it is worth the identical cents.**
The endpoint rule makes a claimed interval's money a function of the interval and the capture,
so releasing one and claiming it again has to return the same money -- and that is only
interesting once something can actually release one.

**An invoice correction cannot contradict a live claim.** The credit side already failed
closed: a return priced against a line that has moved answers `E_SOURCE_CORRECTION_CONFLICT`.
The invoice side did not fail at all, so a correction could quietly reprice a line an issued
credit was built from. Both halves are asserted here, from the invoice side.
"""
import pytest

from tests.credit_support import (  # noqa: F401  (books is a fixture)
    aging, apply_credit, balances, books, goodwill_credit, invoice, refund, returned_credit,
    settlement, taxed_invoice, ties_to_the_receivable, worth, worth_version,
)

# The money.
TAXED_GROSS = 10800
RETURN_NET = 2500
RETURN_TAX = 200
RETURN_GROSS = 2700
# The three-unit line: net 100 minor units, one 7% cell of 7 minor units, by the endpoint rule.
THIRDS = ((33, 2, 35), (33, 2, 35), (34, 3, 37))


def _three_unit_invoice(books_):
    seven = books_['run']('item create', dict(
        name='Seven percent', type='sales_tax_item', tax_percent='7',
        tax_agency_vendor_id=books_['client'].vendor.query(
            company=books_['company'], limit=50)['items'][0]['id'],
        liability_account_id=books_['liability']))['id']
    return books_['run']('invoice post', dict(
        customer=books_['kerr'], date='2026-03-02', due_date='2026-04-01',
        sales_tax_item=seven, sales_tax_calculation='line_component_half_even',
        lines=[{'item': books_['widget'], 'quantity': '3', 'net_amount': '1.00',
                'tax_code': books_['taxable']}]), reason='Three units')


def _void(books_, credit_id, **extra):
    return books_['run']('credit-memo void', dict(
        credit_memo=credit_id, expected_version=worth_version(books_, credit_id), **extra),
        reason='Issued to the wrong customer')


# ---------------------------------------------------------------- void


def test_voiding_a_credit_reverses_it_at_its_own_date_and_leaves_its_number_occupied(books):
    sale = taxed_invoice(books)
    before, _ = balances(books)
    credit = returned_credit(books, sale['id'], sale['revision']['lines'][0]['line_id'])
    during, _ = balances(books)
    assert during[books['receivable']] == TAXED_GROSS - RETURN_GROSS
    assert during[books['liability']] == -(800 - RETURN_TAX)

    voided = _void(books, credit['id'])

    assert voided['status'] == 'voided' and voided['changed'] is True
    assert voided['void_reason'] == 'Issued to the wrong customer'
    assert [batch['kind'] for batch in voided['revision']['batches']] == ['original', 'reversal']
    # The reversal is dated where the credit is, not today.
    assert {batch['effective_date'] for batch in voided['revision']['batches']} == {'2026-04-05'}
    after, _ = balances(books)
    assert after == before
    assert worth(books, credit['id'])['available_minor_units'] == 0
    assert worth(books, credit['id'])['capacity_minor_units'] == 0
    # Readable, numbered, and still in the invoice series.
    shown = books['run']('credit-memo show', {'credit_memo': credit['id']})
    assert shown['number'] == credit['number'] and shown['status'] == 'voided'
    assert books['run']('credit-memo history', {'credit_memo': credit['id']})['count'] == 1
    ties_to_the_receivable(books)


def test_voiding_a_credit_twice_changes_nothing_the_second_time(books):
    sale = taxed_invoice(books)
    credit = returned_credit(books, sale['id'], sale['revision']['lines'][0]['line_id'])
    first = _void(books, credit['id'])
    before, _ = balances(books)
    second = _void(books, credit['id'])
    assert second['changed'] is False and second['version'] == first['version']
    after, _ = balances(books)
    assert after == before


def test_a_void_needs_a_reason(books):
    from bookflow.core.errors import BookflowError
    sale = taxed_invoice(books)
    credit = returned_credit(books, sale['id'], sale['revision']['lines'][0]['line_id'])
    with pytest.raises(BookflowError) as refused:
        books['run']('credit-memo void', dict(credit_memo=credit['id']))
    assert refused.value.code == 'E_REASON_REQUIRED'
    assert worth(books, credit['id'])['available_minor_units'] == RETURN_GROSS


def test_a_released_interval_is_worth_the_identical_cents_when_it_is_claimed_again(books):
    sale = _three_unit_invoice(books)
    line = sale['revision']['lines'][0]
    assert (line['net_minor_units'], line['tax_minor_units'], line['gross_minor_units']) == (100, 7, 107)
    issued = [returned_credit(books, sale['id'], line['line_id'], date='2026-03-%02d' % (10 + index))
              for index in range(3)]
    assert [(row['revision']['lines'][0]['net_minor_units'],
             row['revision']['lines'][0]['tax_minor_units'],
             row['revision']['lines'][0]['gross_minor_units']) for row in issued] == list(THIRDS)

    _void(books, issued[0]['id'])

    again = returned_credit(books, sale['id'], line['line_id'], date='2026-03-20')
    reclaimed = again['revision']['lines'][0]
    assert (reclaimed['net_minor_units'], reclaimed['tax_minor_units'],
            reclaimed['gross_minor_units']) == THIRDS[0]
    assert [(claim['start_microunits'], claim['end_microunits']) for claim in reclaimed['claims']] \
        == [(0, 1_000_000)]


def test_a_fourth_unit_is_still_exhausted_after_a_void_and_a_re_return(books):
    from bookflow.core.errors import BookflowError
    sale = _three_unit_invoice(books)
    line = sale['revision']['lines'][0]
    issued = [returned_credit(books, sale['id'], line['line_id'], date='2026-03-%02d' % (10 + index))
              for index in range(3)]
    _void(books, issued[2]['id'])
    returned_credit(books, sale['id'], line['line_id'], date='2026-03-20')
    with pytest.raises(BookflowError) as exhausted:
        returned_credit(books, sale['id'], line['line_id'], date='2026-03-21')
    assert exhausted.value.code == 'E_RETURN_EXHAUSTED'


def test_a_credit_with_a_live_application_or_a_live_refund_cannot_be_voided(books):
    from bookflow.core.errors import BookflowError
    sale = invoice(books)
    credit = goodwill_credit(books, '30.00')
    applied = apply_credit(books, credit, sale['id'], amount='30.00')

    with pytest.raises(BookflowError) as has_application:
        _void(books, credit['id'])
    assert has_application.value.code == 'E_HAS_APPLICATIONS'
    assert has_application.value.details['application_ids'] == [
        applied['effect']['applications'][0]['application_id']]

    books['run']('customer-credit unapply', dict(
        credit_memo=credit['id'], expected_version=worth_version(books, credit['id']),
        applications=[{'application_id': applied['effect']['applications'][0]['application_id'],
                       'invoice_expected_version': applied['effect']['document_changes'][0]['version']}]),
        reason='Take it back')
    paid = refund(books, credit['id'])

    with pytest.raises(BookflowError) as has_refund:
        _void(books, credit['id'])
    assert has_refund.value.code == 'E_HAS_REFUND'
    assert has_refund.value.details['refund_ids'] == [paid['id']]

    # Void the refund and the credit can be voided, so both refusals were about the dependency.
    books['run']('customer-refund void', dict(refund=paid['id']), reason='Wrong account')
    assert _void(books, credit['id'])['status'] == 'voided'


# ---------------------------------------------------------------- the correction guard


def _correct(books_, sale_id, key, lines, **extra):
    request = dict(invoice=sale_id, operation_key=key, lines=lines,
                   expected_version=books_['run']('invoice show', {'invoice': sale_id})['version'])
    request.update(extra)
    return books_['run']('invoice update', request, reason='Correct the invoice')


def test_correcting_a_claimed_invoice_line_is_refused_from_the_invoice_side(books):
    from bookflow.core.errors import BookflowError
    sale = books['run']('invoice post', dict(
        customer=books['kerr'], date='2026-03-02', due_date='2026-04-01',
        sales_tax_item=books['tax_item'], sales_tax_calculation='line_component_half_even',
        lines=[{'item': books['widget'], 'quantity': '4', 'unit_price': '25.00',
                'tax_code': books['taxable']},
               {'item': books['service'], 'quantity': '1', 'unit_price': '25.00'}]),
        reason='Bill the customer')
    claimed, spare = (line['line_id'] for line in sale['revision']['lines'])
    returned_credit(books, sale['id'], claimed, date='2026-03-10')
    before, _ = balances(books)

    def line(line_id, **fields):
        base = dict(line_id=line_id, item=books['widget'], quantity='4', unit_price='25.00',
                    tax_code=books['taxable'])
        base.update(fields)
        return base

    spare_line = dict(line_id=spare, item=books['service'], quantity='1', unit_price='25.00')

    with pytest.raises(BookflowError) as requantified:
        _correct(books, sale['id'], 'requantify', [line(claimed, quantity='2'), spare_line])
    assert requantified.value.code == 'E_SOURCE_CORRECTION_CONFLICT'
    assert requantified.value.details['reason'] == 'claimed_line_repriced'

    with pytest.raises(BookflowError) as repriced:
        _correct(books, sale['id'], 'reprice', [line(claimed, unit_price='30.00'), spare_line])
    assert repriced.value.code == 'E_SOURCE_CORRECTION_CONFLICT'

    with pytest.raises(BookflowError) as removed:
        _correct(books, sale['id'], 'remove', [spare_line])
    assert removed.value.code == 'E_SOURCE_CORRECTION_CONFLICT'
    assert removed.value.details['reason'] == 'claimed_line_removed'

    with pytest.raises(BookflowError) as untaxed:
        _correct(books, sale['id'], 'untax', [line(claimed, tax_code=books['exempt']), spare_line])
    assert untaxed.value.code == 'E_SOURCE_CORRECTION_CONFLICT'
    assert untaxed.value.details['reason'] == 'claimed_line_tax_changed'

    after, _ = balances(books)
    assert after == before      # not one of the four refusals wrote anything


def test_correcting_an_unclaimed_line_of_a_claimed_invoice_still_works(books):
    sale = books['run']('invoice post', dict(
        customer=books['kerr'], date='2026-03-02', due_date='2026-04-01',
        sales_tax_item=books['tax_item'], sales_tax_calculation='line_component_half_even',
        lines=[{'item': books['widget'], 'quantity': '4', 'unit_price': '25.00',
                'tax_code': books['taxable']},
               {'item': books['service'], 'quantity': '1', 'unit_price': '25.00'}]),
        reason='Bill the customer')
    claimed, spare = (line['line_id'] for line in sale['revision']['lines'])
    credit = returned_credit(books, sale['id'], claimed, date='2026-03-10')

    corrected = _correct(books, sale['id'], 'grow', [
        dict(line_id=claimed, item=books['widget'], quantity='4', unit_price='25.00',
             tax_code=books['taxable']),
        dict(line_id=spare, item=books['service'], quantity='2', unit_price='25.00')])

    # The claim names the permanent line occurrence, so an untouched claimed line keeps it.
    assert corrected['total']['minor_units'] == TAXED_GROSS + 5000
    ledger, _ = balances(books)
    assert ledger[books['receivable']] == TAXED_GROSS + 5000 - RETURN_GROSS
    assert worth(books, credit['id'])['available_minor_units'] == RETURN_GROSS
    ties_to_the_receivable(books)


# ---------------------------------------------------------------- the list


def test_the_credit_list_says_what_each_credit_is_still_worth(books):
    sale = invoice(books)
    spent = goodwill_credit(books, '30.00')
    apply_credit(books, spent, sale['id'], amount='30.00')
    standing = goodwill_credit(books, '12.00', date='2026-03-11')
    cancelled = goodwill_credit(books, '9.00', date='2026-03-12')
    _void(books, cancelled['id'])

    page = books['run']('credit-memo query', {'limit': 50})
    assert [(row['number'], row['status'], row['source_current']['available_minor_units'])
            for row in page['items']] == [
        (spent['number'], 'posted', 0), (standing['number'], 'posted', 1200),
        (cancelled['number'], 'voided', 0)]
    assert [row['source_current']['applied_minor_units'] for row in page['items']] == [3000, 0, 0]

    available = books['run']('credit-memo query', {'limit': 50, 'available_only': True})
    assert [row['id'] for row in available['items']] == [standing['id']]
    assert books['run']('credit-memo query', {'status': 'voided'})['count'] == 1
    assert books['run']('credit-memo query', {'customer': books['kerr']})['count'] == 0
    assert books['run']('credit-memo query', {'origin': 'return'})['count'] == 0
    reversed_page = books['run']('credit-memo query', {'limit': 50, 'direction': 'desc'})
    assert [row['id'] for row in reversed_page['items']] == [
        cancelled['id'], standing['id'], spent['id']]


def test_the_credit_list_pages_and_refuses_a_stale_cursor(books):
    from bookflow.core.errors import BookflowError
    issued = [goodwill_credit(books, '5.00', date='2026-03-%02d' % (10 + index)) for index in range(3)]
    first = books['run']('credit-memo query', {'limit': 2})
    assert first['has_more'] and [row['id'] for row in first['items']] == [row['id'] for row in issued[:2]]
    second = books['run']('credit-memo query', {'limit': 2, 'cursor': first['next_cursor']})
    assert not second['has_more'] and [row['id'] for row in second['items']] == [issued[2]['id']]
    goodwill_credit(books, '5.00', date='2026-03-13')
    with pytest.raises(BookflowError) as stale:
        books['run']('credit-memo query', {'limit': 2, 'cursor': first['next_cursor']})
    assert stale.value.code == 'E_QUERY_STALE'


COMMANDS = frozenset(('credit-memo query', 'credit-memo void'))


@pytest.mark.timeout(300)
def test_the_same_credit_memo_lifecycle_through_python_cli_http_and_mcp(root, tmp_path):
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
                                            dict(name='Parity lifecycle income', type='income')))['id']
                exempt = next(row['id'] for row in (await matrix.call(
                    surface, 'sales-tax-code list', {}))['items'] if not row['taxable'])
                item = (await matrix.call(surface, 'item create', dict(
                    name='Parity voided service', type='service', sales_enabled=True,
                    description='Parity service', income_account_id=income, price='40.00',
                    sales_tax_code_id=exempt)))['id']
                customer = (await matrix.call(surface, 'customer create',
                                              dict(name='Parity Lifecycle Co')))['id']
                credit = await matrix.call(surface, 'credit-memo post', dict(
                    customer=customer, date='2026-03-06', number='PARITY-LIFE-CM',
                    lines=[{'item': item, 'quantity': '1', 'unit_price': '40.00'}]))

                listed = await call('credit-memo query', {'limit': 10, 'available_only': True})
                assert any(row['id'] == credit['id'] for row in listed['items'])

                request = {'credit_memo': credit['id'], 'expected_version': credit['version']}
                assert (await call('credit-memo void', request, dry_run=True))['dry_run']
                voided = await call('credit-memo void', request, idempotency_key='void-1')
                replay = await call('credit-memo void', request, idempotency_key='void-1')
                assert replay['id'] == voided['id'] and voided['status'] == 'voided'

                refused = await call('credit-memo void',
                                     {'credit_memo': credit['id'], 'expected_version': 99},
                                     rejected=True)
                assert refused['code'] == 'E_VERSION_CONFLICT'
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
