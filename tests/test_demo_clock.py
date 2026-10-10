"""The demo and the reference year build the same on any real day.

A seed is written as of one day: the demo's reset day, and the reference year's `[calendar] as_of`.
Checks that ask "has this passed?" (an estimate's expiry) are judged on that day inside a reset, and
on the real day everywhere else, so a person accepting an expired estimate today is still asked to
acknowledge it.
"""
from datetime import date, datetime, timezone

import pytest

import bookflow
from bookflow.company import memorized_schedule
from bookflow.core import clock
from bookflow.core.errors import BookflowError

DEMO, REFERENCE = 'Demo Plumbing Co', 'Reference Plumbing Co'


def frozen(monkeypatch, day):
    moment = datetime.fromisoformat(day + 'T12:00:00').replace(tzinfo=timezone.utc)
    monkeypatch.setattr(clock, 'now', lambda: moment)


def test_the_working_day_stands_in_for_today_only_inside_its_block(monkeypatch):
    frozen(monkeypatch, '2026-10-10')
    assert clock.working_day() is None and clock.today_iso() == '2026-10-10'
    with clock.as_of_day('2026-12-31'):
        assert clock.working_day() == date(2026, 12, 31)
        assert clock.today_iso() == '2026-12-31'
        assert memorized_schedule.today('America/Chicago') == '2026-12-31'
        with clock.as_of_day(None):
            assert clock.today_iso() == '2026-10-10'
    assert clock.working_day() is None and clock.today_iso() == '2026-10-10'
    assert memorized_schedule.today('UTC') == '2026-10-10'


def test_accepting_an_expired_estimate_on_the_real_day_still_needs_acknowledging(root, monkeypatch):
    frozen(monkeypatch, '2026-10-10')
    client = bookflow.connect(data_root=str(root))
    customer = client.run('customer list', {}, company=DEMO)['items'][0]['id']

    def estimate(number):
        made = client.run('estimate create', {'date': '2026-09-01', 'number': number, 'customer': customer,
                                              'title': 'Expiry check', 'expires_on': '2026-10-01',
                                              'sales_tax_item': 'Combined Sales Tax',
                                              'lines': [{'item': 'Mainline Clearing', 'quantity': '1'}]}, company=DEMO)
        return made['id'], made['version']

    accept = dict(status='accepted', decision_note='Accepted late')
    document, version = estimate('CLOCK-EST-1')
    with pytest.raises(BookflowError) as refused:
        client.run('estimate update', {'estimate': document, 'expected_version': version, **accept}, company=DEMO)
    assert [f['field'] for f in refused.value.details['fields']] == ['acknowledge_expired']
    done = client.run('estimate update', {'estimate': document, 'expected_version': version,
                                          'acknowledge_expired': True, **accept}, company=DEMO)
    assert done['status'] == 'accepted'

    # Written as of a day before the expiry, the same acceptance is in time.
    document, version = estimate('CLOCK-EST-2')
    with clock.as_of_day('2026-09-15'):
        done = client.run('estimate update', {'estimate': document, 'expected_version': version, **accept}, company=DEMO)
    assert done['status'] == 'accepted'


def balances(client, company):
    rows, cursor = [], None
    while True:
        page = client.report.trial_balance(company=company, date_to='2026-12-31', limit=200,
                                           **({'cursor': cursor} if cursor else {}))
        rows += page['rows']
        cursor = page.get('next_cursor')
        if not cursor:
            return {row['current_account_label']: row['signed_net']['minor_units'] for row in rows}


def test_reset_with_the_reference_builds_the_same_on_any_real_day(tmp_path, monkeypatch):
    """Before, inside and after the seeds' year: the same books, company for company."""
    built = {}
    for day in ('2025-06-01', '2026-10-10', '2027-03-15'):
        frozen(monkeypatch, day)
        root = tmp_path / day
        monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
        client = bookflow.connect(data_root=str(root))
        client.init()
        result = client.demo.reset(as_of='2026-12-12', include_reference=True)
        assert result['as_of'] == '2026-12-12' and result['reference_display_name'] == REFERENCE
        built[day] = {company: balances(client, company) for company in (DEMO, REFERENCE)}
        assert clock.working_day() is None
    first, *rest = built.values()
    assert first[DEMO] and first[REFERENCE]
    assert all(other == first for other in rest)
