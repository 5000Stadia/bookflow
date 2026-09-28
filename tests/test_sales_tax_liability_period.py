"""`sales-tax liability` over a period: one month's tax, not the running balance (R72).

The blind trial asked for "September's sales tax so far" and got the cumulative balance,
because the read took only `as_of`. Moved to 2026-09-28 as the trial's demo was, the demo owes
40.04 at the end of August and 76.46 on September 28, so September so far collected 36.42.
"""
import pytest

from bookflow.core.errors import BookflowError

CO = 'Demo Plumbing Co'


def _totals(result):
    return {k: (v['amount'] if isinstance(v, dict) else v) for k, v in result['totals'].items()}


def _reader(client):
    return lambda raw: client.run('sales-tax liability', raw, company=CO)


@pytest.fixture
def books(client):
    # Moving the demo's dates is the slow part (~40 s); only the figures test needs it.
    client.demo.reset(as_of='2026-09-28')
    return _reader(client)


def test_september_is_the_months_tax_and_as_of_is_unchanged(books):
    august = _totals(books({'as_of': '2026-08-31'}))
    assert august['balance'] == '40.04' and august['beginning_balance'] is None

    running = books({'as_of': '2026-09-28'})
    assert _totals(running) == {'beginning_balance': None, 'tax_charged': '76.46',
                                'tax_credited': '0.00', 'remitted': '0.00',
                                'unattributed': '0.00', 'balance': '76.46'}
    assert running['metadata']['period'] == {'date_from': None, 'date_to': '2026-09-28'}

    september = books({'date_from': '2026-09-01', 'date_to': '2026-09-28'})
    assert _totals(september) == {'beginning_balance': '40.04', 'tax_charged': '36.42',
                                  'tax_credited': '0.00', 'remitted': '0.00',
                                  'unattributed': '0.00', 'balance': '76.46'}
    assert september['metadata']['period'] == {'date_from': '2026-09-01', 'date_to': '2026-09-28'}
    # Every row adds up on its own, and as_of names the same end as date_to.
    for row in september['rows']:
        amount = {k: float(row[k]['amount']) for k in
                  ('beginning_balance', 'tax_charged', 'tax_credited', 'remitted', 'unattributed', 'balance')}
        assert round(amount['beginning_balance'] + amount['tax_charged'] - amount['tax_credited']
                     - amount['remitted'] + amount['unattributed'], 2) == amount['balance']
    assert _totals(books({'date_from': '2026-09-01', 'as_of': '2026-09-28'})) == _totals(september)


@pytest.mark.parametrize('raw', [
    {},
    {'date_from': '2026-09-01'},
    {'as_of': '2026-09-28', 'date_to': '2026-09-30'},
    {'date_from': '2026-10-01', 'date_to': '2026-09-30'},
])
def test_a_period_needs_one_end_on_or_after_its_start(client, raw):
    with pytest.raises(BookflowError) as error:
        _reader(client)(raw)
    assert error.value.code == 'E_VALIDATION'


def test_help_says_how_to_ask_for_a_period_without_a_date_that_reads_as_today(client):
    from bookflow.core.registry import REGISTRY
    cmd = REGISTRY['sales-tax liability']
    assert 'date_from=2025-01-01 and date_to=2025-03-31' in cmd.description
    assert 'the first of the month and today' in cmd.description and 'not one month' in cmd.description
    # A fixed recent date in the example was read by a trial's agent as the books' today (R72).
    assert '2026' not in cmd.description and 'September' not in cmd.description
