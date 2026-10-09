"""Adjust Sales Tax Due, checked by hand against the report commands.

The sale is the remittance test's: 100.00 taxable, 8.00 to the state and 2.00 to the city (see
tests/test_sales_tax_remittance.py, whose `books` fixture this reuses). On top of it:

- an increase of 1.25 to the state (a penalty) makes the state owed 9.25 and Penalties 1.25;
- a reduction of 0.40 to the state (a timely-filing discount) makes it owed 8.85, credited to
  income;
- `sales-tax pay` with no amount then pays exactly 8.85, and more is refused.

Every test re-reads the identity the liability read rests on: its total is the Sales Tax Payable
account's own balance on the trial balance for the same date.
"""
from copy import deepcopy

import pytest

from bookflow.core.errors import BookflowError
from tests.test_sales_tax_remittance import _balances, _invoice, _ties, books  # noqa: F401 - fixture

PENALTY = '1.25'
DISCOUNT = '0.40'
STATE_OWED = 800                        # 8% of 100.00
CITY_OWED = 200                         # 2% of 100.00
COLUMNS = ('tax_charged', 'tax_credited', 'remitted', 'adjusted', 'unattributed', 'balance')


def _penalties(books):
    return books['run']('account create', dict(name='Penalties', type='expense'))['id']


def _adjust(books, *, idempotency_key=None, **extra):
    values = dict(agency=books['state'], date='2026-03-31', adjustment_account=books['expense'],
                  direction='increase', amount=PENALTY, memo='Late-filing penalty')
    values.update(extra)
    context = {'idempotency_key': idempotency_key} if idempotency_key else {}
    return books['run']('sales-tax adjust', values, reason='Adjust sales tax due', **context)


def _rows(books, **query):
    report = books['run']('sales-tax liability', dict(limit=50, **query))
    return {row['display_agency_label']: {key: row[key]['minor_units'] for key in COLUMNS}
            for row in report['rows']}, report


def test_an_increase_raises_what_the_agency_is_owed_against_the_adjustment_account(books):
    _invoice(books)
    penalties = _penalties(books)
    adjusted = _adjust(books, adjustment_account=penalties)
    assert adjusted['direction'] == 'increase' and adjusted['number'] == '1'
    assert adjusted['total']['minor_units'] == 125 and adjusted['signed_amount']['minor_units'] == 125
    assert (adjusted['owed_before']['minor_units'], adjusted['owed_after']['minor_units']) == (800, 925)
    batch, = adjusted['revision']['batches']
    assert (batch['kind'], batch['debit_minor_units'], batch['credit_minor_units']) == ('original', 125, 125)

    rows, _ = _rows(books, as_of='2026-12-31')
    assert rows['State Tax'] == dict(tax_charged=800, tax_credited=0, remitted=0, adjusted=125,
                                     unattributed=0, balance=925)
    assert rows['City Tax']['balance'] == CITY_OWED and rows['City Tax']['adjusted'] == 0
    balances, totals = _ties(books)
    # The liability is credited 1.25 more than the sale's 10.00; Penalties is debited 1.25.
    assert totals['balance'] == 1125 and balances[books['liability']] == -1125
    assert balances[penalties] == 125


def test_a_reduction_lowers_it_and_credits_the_adjustment_account(books):
    _invoice(books)
    _adjust(books)
    reduced = _adjust(books, date='2026-04-02', direction='reduce', amount=DISCOUNT,
                      adjustment_account=books['income'], memo='Timely-filing discount')
    assert reduced['signed_amount']['minor_units'] == -40
    assert (reduced['owed_before']['minor_units'], reduced['owed_after']['minor_units']) == (925, 885)
    rows, _ = _rows(books, as_of='2026-12-31')
    assert rows['State Tax']['adjusted'] == 85 and rows['State Tax']['balance'] == 885
    balances, totals = _ties(books)
    assert totals['balance'] == 1085


def test_the_liability_read_places_it_by_agency_and_by_period(books):
    _invoice(books)
    _adjust(books, date='2026-03-31')
    _adjust(books, agency=books['city'], date='2026-04-15', direction='reduce', amount='0.50')
    # As of the end of March only the state's adjustment exists.
    rows, _ = _rows(books, as_of='2026-03-31')
    assert rows['State Tax']['adjusted'] == 125 and rows['City Tax']['adjusted'] == 0
    # April alone: the state's March adjustment is in its beginning balance, the city's
    # reduction is April's activity.
    report = books['run']('sales-tax liability', dict(date_from='2026-04-01', date_to='2026-04-30'))
    april = {row['display_agency_label']: row for row in report['rows']}
    assert april['State Tax']['beginning_balance']['minor_units'] == 925
    assert april['State Tax']['adjusted']['minor_units'] == 0
    assert april['City Tax']['beginning_balance']['minor_units'] == 200
    assert april['City Tax']['adjusted']['minor_units'] == -50
    assert april['City Tax']['balance']['minor_units'] == 150
    # One agency read alone sees only its own.
    alone, _ = _rows(books, as_of='2026-12-31', agency=books['city'])
    assert set(alone) == {'City Tax'}
    _ties(books)


def test_sales_tax_pay_settles_an_adjusted_balance_and_no_more(books):
    _invoice(books)
    _adjust(books)
    paid = books['run']('sales-tax pay', dict(agency=books['state'], date='2026-04-20',
                                              funding_account=books['bank'],
                                              method=books['methods']['Check']), reason='Remit')
    assert paid['total']['minor_units'] == 925
    rows, _ = _rows(books, as_of='2026-12-31')
    assert rows['State Tax']['balance'] == 0 and rows['State Tax']['remitted'] == 925
    with pytest.raises(BookflowError) as exc:
        books['run']('sales-tax pay', dict(agency=books['state'], date='2026-04-21', amount='0.01',
                                           funding_account=books['bank'],
                                           method=books['methods']['Check']), reason='Remit')
    assert exc.value.code == 'E_APPLICATION_CAPACITY'
    _ties(books)


def test_an_adjustment_alone_can_be_paid(books):
    """An opening balance with no sale behind it: the move-in case."""
    _adjust(books, amount='431.07', adjustment_account=books['expense'])
    paid = books['run']('sales-tax pay', dict(agency=books['state'], date='2026-04-20',
                                              funding_account=books['bank'],
                                              method=books['methods']['Check']), reason='Remit')
    assert paid['total']['amount'] == '431.07'
    _, totals = _ties(books)
    assert totals['balance'] == 0


def test_voiding_puts_the_agency_back_and_keeps_the_history(books):
    _invoice(books)
    before, _ = _balances(books)
    adjusted = _adjust(books)
    voided = books['run']('sales-tax adjustment void',
                          dict(adjustment=adjusted['number'], expected_version=adjusted['version']),
                          reason='Penalty was waived')
    assert voided['status'] == 'voided' and voided['void_reason'] == 'Penalty was waived'
    after, _ = _balances(books)
    assert {k: v for k, v in after.items() if v} == {k: v for k, v in before.items() if v}
    rows, _ = _rows(books, as_of='2026-12-31')
    assert rows['State Tax']['adjusted'] == 0 and rows['State Tax']['balance'] == STATE_OWED
    shown = books['run']('sales-tax adjustment show', dict(adjustment=adjusted['id']))
    assert shown['status'] == 'voided' and len(shown['revision']['batches']) == 2
    with pytest.raises(BookflowError) as exc:
        books['run']('sales-tax adjustment void', dict(adjustment=adjusted['id']), reason='Again')
    assert exc.value.code == 'E_APPLICATION_INACTIVE'
    # The number stays occupied.
    with pytest.raises(BookflowError) as exc:
        _adjust(books, number=adjusted['number'])
    assert exc.value.code == 'E_DUPLICATE_NUMBER'
    _ties(books)


def test_a_closed_period_refuses_the_adjustment_and_its_void(books):
    adjusted = _adjust(books, date='2026-03-31')
    books['run']('company update', dict(closing_date='2026-04-30'), reason='Close April')
    with pytest.raises(BookflowError) as exc:
        _adjust(books, date='2026-04-20')
    assert exc.value.code == 'E_PERIOD_CLOSED'
    assert exc.value.details == {'date': '2026-04-20', 'closing_date': '2026-04-30'}
    with pytest.raises(BookflowError) as exc:
        books['run']('sales-tax adjustment void', dict(adjustment=adjusted['id']), reason='Late')
    assert exc.value.code == 'E_PERIOD_CLOSED'


@pytest.mark.parametrize('field, value, problem', (
    ('adjustment_account', 'liability', 'sales tax payable account the adjustment already posts to'),
    ('adjustment_account', 'bank', 'use sales-tax pay'),
    ('adjustment_account', 'card', 'use sales-tax pay'),
    ('adjustment_account', 'receivable', 'open customer documents'),
    ('agency', 'plain', 'not flagged as a tax agency'),
))
def test_what_an_adjustment_cannot_be_against(books, field, value, problem):
    with pytest.raises(BookflowError) as exc:
        _adjust(books, **{field: books.get(value, value)})
    assert exc.value.code == 'E_VALIDATION'
    assert problem in exc.value.details['fields'][0]['problem']
    assert books['run']('sales-tax adjustment query', {})['items'] == []


def test_a_zero_amount_is_out_of_range(books):
    with pytest.raises(BookflowError) as exc:
        _adjust(books, amount='0.00')
    assert exc.value.code == 'E_VALUE_RANGE'


def test_the_cash_basis_is_refused(books):
    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    with open_database(books['database'], writable=True) as handle:
        handle.conn.execute(c.company_info.update().values(sales_tax_liability_basis='payment_receipt'))
        handle.conn.commit()
    with pytest.raises(BookflowError) as exc:
        _adjust(books)
    assert exc.value.code == 'E_TAX_BASIS_UNSUPPORTED'


def test_dry_run_idempotency_query_and_audit(books):
    preview = books['run']('sales-tax adjust', dict(
        agency=books['state'], date='2026-03-31', adjustment_account=books['expense'],
        direction='increase', amount=PENALTY), reason='Look first', dry_run=True)
    assert preview['dry_run'] and preview['total']['minor_units'] == 125
    assert books['run']('sales-tax adjustment query', {})['items'] == []
    first = _adjust(books, idempotency_key='adjust-1')
    again = _adjust(books, idempotency_key='adjust-1')
    assert again['id'] == first['id'] and again['idempotent_replay']
    _adjust(books, agency=books['city'], date='2026-05-01', number='CITY-1')
    page = books['run']('sales-tax adjustment query', dict(agency=books['state']))
    assert [row['id'] for row in page['items']] == [first['id']]
    newest = books['run']('sales-tax adjustment query', dict(direction='desc', limit=1))
    assert newest['items'][0]['number'] == 'CITY-1' and newest['has_more']
    commands = [row['command'] for row in books['run']('audit list', dict(limit=50))['items']]
    assert commands.count('sales-tax adjust') == 2


COMMANDS = frozenset(('sales-tax adjust', 'sales-tax adjustment show', 'sales-tax adjustment query',
                      'sales-tax adjustment void'))
SURFACES = ('python', 'cli', 'http', 'mcp')


@pytest.mark.timeout(300)
def test_the_same_sales_tax_adjustment_through_python_cli_http_and_mcp(root, tmp_path):
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

                await matrix.call(surface, 'company update', dict(sales_tax_enabled=True))
                penalties = (await matrix.call(surface, 'account create',
                                               dict(name='Parity penalties', type='expense')))['id']
                checking = (await matrix.call(surface, 'account create',
                                              dict(name='Parity checking', type='bank')))['id']
                agency = (await matrix.call(surface, 'vendor create',
                                            dict(name='Parity Revenue', is_tax_agency=True)))['id']
                request = dict(agency=agency, date='2026-03-31', adjustment_account=penalties,
                               direction='increase', amount=PENALTY, number='PARITY-ADJ-1',
                               memo='Parity penalty')
                assert (await call('sales-tax adjust', request, dry_run=True))['dry_run']
                adjusted = await call('sales-tax adjust', request, idempotency_key='adjust-1')
                replay = await call('sales-tax adjust', request, idempotency_key='adjust-1')
                assert replay['id'] == adjusted['id'] and replay['idempotent_replay']
                assert adjusted['owed_after']['amount'] == PENALTY
                await call('sales-tax adjustment show', {'adjustment': adjusted['id']})
                await call('sales-tax adjustment query', {'agency': agency, 'limit': 10})
                owed = await matrix.call(surface, 'sales-tax liability', dict(as_of='2026-03-31'))
                assert owed['rows'][0]['adjusted']['amount'] == PENALTY
                await call('sales-tax adjustment void',
                           {'adjustment': adjusted['id'], 'expected_version': adjusted['version']})
                refused = await call('sales-tax adjust', {
                    **request, 'number': 'PARITY-ADJ-2', 'adjustment_account': checking}, rejected=True)
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
