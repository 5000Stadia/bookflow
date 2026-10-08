"""R83: the demo is usable on the day it is reset.

The seed is written as of one day and `demo reset` moves every date in it back by whole months
to the reset day. These witnesses check the moving over every reset day for three years without
touching a database, then reset one real demo on the blind trials' day and read what it holds.
"""
from datetime import date, datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
import tomllib

import pytest
import sqlalchemy as sa

import bookflow
from bookflow.company import schema as c
from bookflow.demo import dates
from bookflow.storage.engine import open_database
from tests.demo_oracle import DEMO_AS_OF, DEMO_POSITION

# Captured at import, before the suite's autouse fixture pins in-process resets to DEMO_AS_OF.
REAL_RESET_DAY = dates.reset_day
DEMO = 'Demo Plumbing Co'
SEED = tomllib.loads(files('bookflow.demo').joinpath('seed.toml').read_text(encoding='utf-8'))
WRITTEN = date.fromisoformat(SEED['calendar']['written_as_of'])
OPENING = SEED['calendar']['fiscal_year_start']

# Columns that date something the demo recorded as having happened. Due dates, expected
# delivery dates and similar forward-looking dates are deliberately not here.
HAPPENED = {'date', 'effective_date', 'source_date', 'statement_date', 'opening_date', 'entry_date',
            'slot_date', 'through_date', 'purchase_date', 'hire_date'}
# Seed input keys that date a document or something done: the same idea on the written seed.
# `as_of` is the move-in's cutover date, the day the opening balances are posted.
HAPPENED_KEYS = {'date', 'actual_start', 'actual_end', 'as_of'}


def happened(commands):
    """(position, key, value) for every document or event date in seed command inputs."""
    found = []

    def walk(value, key, position):
        if isinstance(value, dict):
            for inner, item in value.items():
                walk(item, inner, position)
        elif isinstance(value, list):
            for item in value:
                walk(item, key, position)
        elif isinstance(value, str) and key in HAPPENED_KEYS:
            found.append((position, key, value))

    for position, entry in enumerate(commands):
        walk(entry.get('input', {}), None, position)
    return found


def test_months_move_whole_and_month_ends_stay_month_ends():
    assert dates.add_months(date(2026, 12, 12), -3) == date(2026, 9, 12)
    assert dates.add_months(date(2026, 10, 31), -1) == date(2026, 9, 30)
    assert dates.add_months(date(2026, 11, 30), -1) == date(2026, 10, 31)
    assert dates.add_months(date(2026, 9, 29), -7) == date(2026, 2, 28)
    assert dates.add_months(date(2026, 9, 29), 17) == date(2028, 2, 29)
    assert dates.add_months(date(2026, 2, 28), 24) == date(2028, 2, 29)
    assert dates.add_months(date(2026, 1, 15), -13) == date(2024, 12, 15)


def test_every_reset_day_for_three_years_keeps_the_story_in_order_and_in_the_past():
    written = happened(SEED['commands'])
    assert any(value > '2026-09-27' for _, _, value in written), 'the seed no longer needs moving'
    values = sorted({value[:10] for _, _, value in written})
    day = date(2026, 1, 1)
    while day <= date(2028, 12, 31):
        moved = dates.move_seed(SEED, day)
        months = moved['calendar']['months_moved']
        assert dates.add_months(WRITTEN, months) <= day < dates.add_months(WRITTEN, months + 1)
        pairs = {old[:10]: new[:10] for (_, _, old), (_, _, new) in zip(written, happened(moved['commands']))}
        assert max(pairs.values()) <= day.isoformat(), day
        # The order of any two written dates is kept: never reversed, at most brought together.
        after = [pairs[value] for value in values]
        assert after == sorted(after), day
        # The opening balances open a fiscal year, before everything else.
        opening = pairs[OPENING]
        assert opening[5:] == '01-01' and opening == min(after), day
        day += timedelta(days=1)


def test_as_of_the_written_day_the_seed_is_exactly_as_written():
    moved = dates.move_seed(SEED, WRITTEN)
    assert moved['calendar']['months_moved'] == 0
    assert moved['commands'] == SEED['commands']
    assert dates.move_seed(SEED, WRITTEN + timedelta(days=30))['commands'] == SEED['commands']


def test_dates_in_text_move_with_the_rest():
    moved = dates.move_seed(SEED, date(2026, 9, 27))
    notes = [entry['input']['decision_note'] for entry in moved['commands']
             if 'decision_note' in entry.get('input', {}) and 'on 20' in entry['input']['decision_note']]
    assert notes and all('2026-06-' in note for note in notes)


def test_the_reset_day_is_today_in_the_demo_companys_timezone(monkeypatch):
    from bookflow.core import clock
    monkeypatch.setattr(clock, 'now', lambda: datetime(2026, 9, 28, 3, 0, tzinfo=timezone.utc))
    assert REAL_RESET_DAY('America/Chicago') == date(2026, 9, 27)
    assert REAL_RESET_DAY(None) == date(2026, 9, 28)
    assert REAL_RESET_DAY('Not/AZone') == date(2026, 9, 28)


def test_reset_defaults_to_the_reset_day_and_takes_an_explicit_one(client, monkeypatch):
    from bookflow.core.errors import BookflowError
    monkeypatch.setattr(dates, 'reset_day', lambda zone: date(2027, 3, 4))
    assert client.demo.reset(dry_run=True)['as_of'] == '2027-03-04'
    assert client.demo.reset(as_of='2026-09-27', dry_run=True)['as_of'] == '2026-09-27'
    for bad in ('2026-02-30', '27/09/2026'):
        with pytest.raises(BookflowError) as caught:
            client.demo.reset(as_of=bad, dry_run=True)
        assert caught.value.code == 'E_VALIDATION'


def latest_dates(client):
    path = Path(client.company.show(company=DEMO)['path']) / 'company.db'
    found = {}
    with open_database(path, writable=False) as db:
        for table in c.metadata.tables.values():
            for column in table.columns:
                if column.name in HAPPENED:
                    value = db.conn.execute(sa.select(sa.func.max(column))).scalar()
                    if value:
                        found[f'{table.name}.{column.name}'] = str(value)[:10]
    return found


def stock(client, as_of):
    rows = client.report.stock_status(company=DEMO, as_of=as_of, limit=200)['rows']
    return {row['item_name']: row['quantity_on_hand'] for row in rows}


@pytest.mark.timeout(900)
def test_a_demo_reset_on_the_blind_trials_day_is_usable_that_day(tmp_path, monkeypatch):
    """R72 met a valve that only arrived after today. Reset on that day, the demo sells one."""
    day = '2026-09-27'
    root = tmp_path / 'root'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    client = bookflow.connect(data_root=str(root))
    client.init()
    assert client.demo.reset(as_of=day)['as_of'] == day

    latest = latest_dates(client)
    assert latest and max(latest.values()) <= day, {k: v for k, v in latest.items() if v > day}

    on_hand = stock(client, day)
    assert on_hand and all(float(quantity) >= 0 for quantity in on_hand.values()), on_hand
    assert float(on_hand['Brass Shutoff Valve']) >= 1

    # The same documents on or before the day: the same books, account for account.
    rows, cursor = [], None
    while True:
        page = client.report.trial_balance(company=DEMO, date_to=day, limit=200, **({'cursor': cursor} if cursor else {}))
        rows += page['rows']
        cursor = page.get('next_cursor')
        if not cursor:
            break
    balances = {row['current_account_label']: row['signed_net']['minor_units'] for row in rows}
    assert balances == DEMO_POSITION['balances']
    assert page['totals']['debit'] == page['totals']['credit']
    assert page['totals']['debit']['minor_units'] == DEMO_POSITION['trial_balance']

    customer = client.run('customer list', {}, company=DEMO)['items'][0]['id']
    client.run('invoice post', {'date': day, 'customer': customer, 'sales_tax_item': 'Combined Sales Tax',
                                'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '1'}]},
               company=DEMO, reason='Sell a valve on the day the demo was reset')
    assert float(stock(client, day)['Brass Shutoff Valve']) == float(on_hand['Brass Shutoff Valve']) - 1
