"""Timekeeping (R106): weekly timesheets, the time clock and the job schedule.

The money, by hand: the `sale` fixture's service item sells at 12.34 an hour. A sheet with 8 hours
on Monday and 7.5 on Tuesday is two time entries, 98.72 and 92.55; billing Monday's is an invoice of
98.72, and it bills once.

Clock arithmetic: 08:00 to 16:20 is 8h20m = 8.333333 hours unrounded; to the nearest 6 minutes
8h18m..8h24m rounds to 8.3 (8h20m is nearer 8h18m than 8h24m); to the nearest 15 minutes 8h15m = 8.25.
A night shift 22:00 to 06:30 splits at midnight into 2 hours and 6.5 hours.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bookflow import BookflowError
from bookflow.core import clock
from tests.test_service_sales_lifecycle import COMPANY, sale  # noqa: F401  (sale is a fixture)

MONDAY = '2026-01-12'


def write(client, command, reason='Timesheet entry', **data):
    return client.run(command, data, company=COMPANY, reason=reason)


def read(client, command, **data):
    return client.run(command, data, company=COMPANY)


@pytest.fixture
def crew(client, sale):
    dana = write(client, 'employee create', name='Dana Fitter')['id']
    dave = write(client, 'employee create', name='Dave Brazer')['id']
    other = write(client, 'customer create', name='Smith Residence')['id']
    return dict(sale, dana=dana, dave=dave, smith=other)


def row(crew, hours, **extra):
    values = dict(customer=crew['customer'], item=crew['item'], billable=True, notes='Rough-in', hours=hours)
    values.update(extra)
    return values


def week(*cells):
    return list(cells) + [None] * (7 - len(cells))


def test_a_timesheet_round_trips_to_time_entries_bills_once_and_locks(client, crew):
    saved = write(client, 'timesheet save', employee='Dana Fitter', week='2026-01-14', rows=[
        row(crew, week('8', '7.5')),
        row(crew, week(None, None, '2'), customer=crew['smith'], billable=False, notes=None)])
    assert saved['changed'] and saved['week_start'] == MONDAY and saved['days'][-1] == '2026-01-18'
    assert sorted((c['date'], c['action'], c['hours']) for c in saved['changes']) == [
        ('2026-01-12', 'create', '8'), ('2026-01-13', 'create', '7.5'), ('2026-01-14', 'create', '2')]
    assert saved['day_totals'][:3] == ['8', '7.5', '2'] and saved['week_total'] == '17.5'
    assert saved['rows'][0]['payroll_item'] is None

    # The entries are ordinary time entries: time-activity reads them, at the item's rate.
    monday = saved['rows'][0]['cells'][0]['entries']
    assert len(monday) == 1
    entry = read(client, 'time-activity show', time_activity=monday[0])
    assert entry['net_minor_units'] == 9872 and entry['time']['duration'] == '8'

    # Saving the same sheet again writes nothing.
    again = write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY,
                  expected_sheet_version=saved['sheet_version'], rows=[
                      row(crew, week('8', '7.5')),
                      row(crew, week(None, None, '2'), customer=crew['smith'], billable=False, notes=None)])
    assert again['changed'] is False and again['sheet_version'] == saved['sheet_version']

    # Bill Monday once; a second invoice finds nothing free.
    invoice = write(client, 'time-activity invoice', time_activity=monday[0], expected_version=entry['version'],
                    conversion_key='dana-monday', date='2026-01-19')
    assert invoice['total_minor_units'] == 9872
    current = read(client, 'time-activity show', time_activity=monday[0])
    with pytest.raises(BookflowError) as twice:
        write(client, 'time-activity invoice', time_activity=monday[0], expected_version=current['version'],
              conversion_key='dana-monday-again', date='2026-01-19')
    assert twice.value.code == 'E_WORK_DEPENDENCY'

    # Monday is now locked in the grid, with a note naming the sale; changing it is refused.
    sheet = read(client, 'timesheet show', employee='Dana Fitter', week=MONDAY)
    cell = sheet['rows'][0]['cells'][0]
    assert cell['locked'] and 'invoice' in cell['lock_note'] and sheet['lock_note']
    with pytest.raises(BookflowError) as locked:
        write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY, rows=[
            row(crew, week('6', '7.5')),
            row(crew, week(None, None, '2'), customer=crew['smith'], billable=False, notes=None)])
    assert locked.value.code == 'E_WORK_DEPENDENCY'
    assert 'not drop below' in locked.value.details['problem']
    # More hours in the billed cell become a new, unbilled entry; the unbilled one stays editable.
    grown = write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY, rows=[
        row(crew, week('9', '7.5')),
        row(crew, week(None, None, '2'), customer=crew['smith'], billable=False, notes=None)])
    assert [(c['date'], c['action'], c['hours']) for c in grown['changes']] == [('2026-01-12', 'create', '1')]
    cell = grown['rows'][0]['cells'][0]
    assert len(cell['entries']) == 2 and cell['hours'] == '9' and cell['billed_hours'] == '8' and cell['locked']
    trimmed = write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY, rows=[
        row(crew, week('8.5', '7.5')),
        row(crew, week(None, None, '2'), customer=crew['smith'], billable=False, notes=None)])
    assert [(c['action'], c['hours']) for c in trimmed['changes']] == [('update', '0.5')]
    with pytest.raises(BookflowError) as below:
        write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY, rows=[
            row(crew, week('7.5', '7.5')),
            row(crew, week(None, None, '2'), customer=crew['smith'], billable=False, notes=None)])
    assert below.value.code == 'E_WORK_DEPENDENCY'
    write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY, rows=[
        row(crew, week('8', '7.5')),
        row(crew, week(None, None, '2'), customer=crew['smith'], billable=False, notes=None)])
    # Unbilled cells still change: Tuesday corrected, the Smith row withdrawn.
    changed = write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY, rows=[row(crew, week('8', '6.25'))])
    assert sorted((c['date'], c['action'], c['hours']) for c in changed['changes']) == [
        ('2026-01-13', 'update', '6.25'), ('2026-01-14', 'void', '0')]
    assert changed['week_total'] == '14.25' and len(changed['rows']) == 1
    # report unbilled-costs reads the sheet's entries as it reads any time: Tuesday waits to be billed, Monday is billed.
    unbilled = str(read(client, 'report unbilled-costs', as_of='2026-01-31'))
    assert changed['rows'][0]['cells'][1]['entries'][0] in unbilled and monday[0] not in unbilled

    # A stale sheet version is refused.
    with pytest.raises(BookflowError) as stale:
        write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY, expected_sheet_version=saved['sheet_version'],
              rows=[row(crew, week('8', '6'))])
    assert stale.value.code == 'E_VERSION_CONFLICT'


def test_copy_last_week_fills_rows_without_hours_and_sheets_refuse_impossible_days(client, crew):
    write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY, rows=[
        row(crew, week('8')), row(crew, week(None, '4'), customer=crew['smith'], notes='Service call')])
    copied = read(client, 'timesheet copy-last', employee='Dana Fitter', week='2026-01-21')
    assert copied['copied_from'] == MONDAY and copied['week_start'] == '2026-01-19'
    assert [(r['customer_name'], r['notes'], r['total']) for r in copied['rows']] == [
        ('Sale witness customer', 'Rough-in', '0'), ('Smith Residence', 'Service call', '0')]
    assert all(h is None for r in copied['rows'] for h in r['hours'])
    assert read(client, 'timesheet show', employee='Dana Fitter', week='2026-01-19')['rows'] == []

    with pytest.raises(BookflowError) as long_day:
        write(client, 'timesheet save', employee='Dana Fitter', week='2026-01-19', rows=[
            row(crew, week('16')), row(crew, week('9'), customer=crew['smith'])])
    assert long_day.value.code == 'E_VALUE_RANGE'
    with pytest.raises(BookflowError) as twice:
        write(client, 'timesheet save', employee='Dana Fitter', week='2026-01-19', rows=[row(crew, week('1')),
                                                                                         row(crew, week('2'))])
    assert twice.value.code == 'E_VALIDATION'

    # The work week follows the preference, and a sheet read under the old week start is stale.
    before = read(client, 'timesheet show', employee='Dana Fitter', week=MONDAY)
    write(client, 'timesheet set-preferences', week_start='sunday')
    assert read(client, 'timesheet show', employee='Dana Fitter', week=MONDAY)['week_start'] == '2026-01-11'
    with pytest.raises(BookflowError) as stale:
        write(client, 'timesheet save', employee='Dana Fitter', week=MONDAY, expected_sheet_version=before['sheet_version'],
              rows=[row(crew, week('8'))])
    assert stale.value.code == 'E_VERSION_CONFLICT'

    # Weeks at the ends of the calendar are refused by name, not crashed on.
    for command, day in (('timesheet show', '9999-12-31'), ('timesheet copy-last', '0001-01-01')):
        with pytest.raises(BookflowError) as edge:
            read(client, command, employee='Dana Fitter', week=day)
        assert edge.value.code == 'E_VALIDATION', command
    write(client, 'timesheet set-preferences', week_start='monday')
    assert read(client, 'timesheet copy-last', employee='Dana Fitter', week='0001-01-03')['copied_from'] is None

    # A save writes one summary event naming the week and the counts, beside the per-entry events.
    import sqlite3
    from pathlib import Path
    with sqlite3.connect(Path(client.company.show(company=COMPANY)['path']) / 'company.db') as db:
        summaries = [r[0] for r in db.execute("SELECT summary FROM audit_events WHERE command = 'timesheet save'")]
    assert summaries[0] == ("saved Dana Fitter's timesheet for the week of 2026-01-12: 12 hours; "
                            "2 entries created, 0 corrected, 0 withdrawn"), summaries


class _Clock:
    def __init__(self, monkeypatch, zone):
        self.zone, self.at = zone, None
        monkeypatch.setattr(clock, 'now', lambda: self.at)

    def set(self, local):
        self.at = datetime.fromisoformat(local).replace(tzinfo=self.zone).astimezone(timezone.utc)


@pytest.fixture
def wall(client, monkeypatch):
    import sqlite3
    from pathlib import Path
    with sqlite3.connect(Path(client.company.show(company=COMPANY)['path']) / 'company.db') as db:
        name = db.execute('SELECT timezone FROM company_info').fetchone()[0]
    return _Clock(monkeypatch, ZoneInfo(name) if name else timezone.utc)


def test_clock_in_and_out_rounds_splits_at_midnight_and_refuses_a_forgotten_clock(client, crew, wall):
    wall.set('2026-01-12T08:00')
    started = write(client, 'clock in', employee='Dana Fitter', customer=crew['customer'], item=crew['item'],
                    notes='Rough-in')
    assert started['status'] == 'open' and started['clock_in_local'] == '2026-01-12T08:00'
    with pytest.raises(BookflowError) as twice:
        write(client, 'clock in', employee='Dana Fitter')
    assert twice.value.code == 'E_VALIDATION'
    wall.set('2026-01-12T16:20')
    status = read(client, 'clock status')
    assert [(c['employee_name'], c['elapsed_hours']) for c in status['clocks']] == [('Dana Fitter', '8.333333')]
    out = write(client, 'clock out', employee='Dana Fitter')
    assert out['recorded_hours'] == [{'date': '2026-01-12', 'hours': '8.333333'}] and len(out['entries']) == 1

    write(client, 'timesheet set-preferences', clock_rounding='6')
    wall.set('2026-01-13T08:00')
    write(client, 'clock in', employee='Dana Fitter', customer=crew['customer'], item=crew['item'])
    wall.set('2026-01-13T16:20')
    assert write(client, 'clock out', employee='Dana Fitter')['recorded_hours'] == [{'date': '2026-01-13', 'hours': '8.3'}]

    write(client, 'timesheet set-preferences', clock_rounding='15')
    wall.set('2026-01-14T22:00')
    write(client, 'clock in', employee='Dana Fitter', customer=crew['customer'], item=crew['item'], notes='Night shift')
    wall.set('2026-01-15T06:30')
    night = write(client, 'clock out', employee='Dana Fitter')
    assert night['recorded_hours'] == [{'date': '2026-01-14', 'hours': '2'}, {'date': '2026-01-15', 'hours': '6.5'}]
    sheet = read(client, 'timesheet show', employee='Dana Fitter', week=MONDAY)
    night_row = next(r for r in sheet['rows'] if r['notes'] == 'Night shift')
    assert night_row['hours'][2:4] == ['2', '6.5']

    # A clock left on overnight past the 16-hour maximum is closed only with a stated end.
    wall.set('2026-01-15T07:00')
    write(client, 'clock in', employee='Dave Brazer', customer=crew['customer'], item=crew['item'])
    wall.set('2026-01-16T09:00')
    assert read(client, 'clock status', employee='Dave Brazer')['clocks'][0]['over_max_shift'] is True
    with pytest.raises(BookflowError) as forgotten:
        write(client, 'clock out', employee='Dave Brazer')
    assert forgotten.value.code == 'E_VALIDATION' and 'maximum shift' in str(forgotten.value.details)
    with pytest.raises(BookflowError):
        write(client, 'clock out', employee='Dave Brazer', end='2026-01-16T08:00')  # 25 hours stated
    closed = write(client, 'clock out', employee='Dave Brazer', end='2026-01-15T15:30')
    assert closed['end_stated'] and closed['recorded_hours'] == [{'date': '2026-01-15', 'hours': '8.5'}]

    # Cancelling a mistaken clock records nothing.
    write(client, 'clock in', employee='Dave Brazer', customer=crew['customer'], item=crew['item'])
    cancelled = write(client, 'clock cancel', employee='Dave Brazer', reason='Clocked in by mistake')
    assert cancelled['status'] == 'cancelled' and read(client, 'clock status')['clocks'] == []


def test_the_schedule_warns_of_double_booking_and_reads_by_employee_and_job(client, crew):
    first = write(client, 'schedule create', employee='Dave Brazer', customer=crew['smith'],
                  starts='2026-01-13T08:00', ends='2026-01-13T12:00', notes='Water heater')
    assert first['warnings'] == [] and first['status'] == 'scheduled'
    clash = write(client, 'schedule create', employee='Dave Brazer', customer=crew['customer'],
                  starts='2026-01-13T11:00', ends='2026-01-13T15:00')
    assert len(clash['conflicts']) == 1 and 'already booked' in clash['warnings'][0]
    later = write(client, 'schedule create', employee='Dana Fitter', customer=crew['customer'],
                  starts='2026-01-14T09:00', ends='2026-01-14T10:00')
    with pytest.raises(BookflowError) as backwards:
        write(client, 'schedule create', employee='Dana Fitter', customer=crew['customer'],
              starts='2026-01-14T09:00', ends='2026-01-14T08:00')
    assert backwards.value.code == 'E_VALIDATION'

    order = write(client, 'work-order create', date='2026-01-10', customer=crew['smith'], title='Repipe',
                  assignees=['Dana Fitter'], scheduled_start='2026-01-15T16:00:00Z',
                  scheduled_end='2026-01-15T18:00:00Z')
    dave = read(client, 'schedule calendar', employee='Dave Brazer', view='day', date='2026-01-13')
    assert [i['notes'] for i in dave['days'][0]['items']] == ['Water heater', None] and dave['conflicts'] == 2
    smith = read(client, 'schedule calendar', customer=crew['smith'], view='week', date='2026-01-13')
    assert smith['date_from'] == MONDAY and len(smith['days']) == 7
    kinds = [(i['kind'], i['employee_name']) for d in smith['days'] for i in d['items']]
    assert kinds == [('schedule', 'Dave Brazer'), ('work_order', 'Dana Fitter')]
    month = read(client, 'schedule calendar', view='month', date='2026-01-20')
    assert len(month['days']) == 31 and sum(len(d['items']) for d in month['days']) == 4

    moved = write(client, 'schedule update', entry=clash['id'], expected_version=1, starts='2026-01-13T13:00')
    assert moved['conflicts'] == [] and moved['version'] == 2
    cancelled = write(client, 'schedule cancel', entry=later['id'], expected_version=1, reason='Customer postponed')
    assert cancelled['status'] == 'cancelled'
    assert read(client, 'schedule show', entry=later['id'])['cancel_reason'] == 'Customer postponed'
    assert order['kind'] == 'work_order'


def test_a_fall_back_hour_a_deactivated_person_and_an_overlapping_clock(client, crew, monkeypatch):
    moment = {'at': datetime(2026, 10, 1, tzinfo=timezone.utc)}
    monkeypatch.setattr(clock, 'now', lambda: moment['at'])

    def at(z):
        moment['at'] = datetime.fromisoformat(z.replace('Z', '+00:00'))
    write(client, 'company update', timezone='America/New_York')
    job = dict(customer=crew['customer'], item=crew['item'])

    # 01:30 EDT to 01:15 EST on the fall-back night is 45 minutes, not nothing.
    at('2026-11-01T05:30:00Z')
    write(client, 'clock in', employee='Dana Fitter', **job)
    at('2026-11-01T06:15:00Z')
    assert write(client, 'clock out', employee='Dana Fitter')['recorded_hours'] == [{'date': '2026-11-01', 'hours': '0.75'}]
    # Dave, 22:00 EDT to 06:00 EST across the change: 2 hours before midnight, 7 after (the night has 25 hours).
    at('2026-11-01T02:00:00Z')
    write(client, 'clock in', employee='Dave Brazer', **job, at='2026-10-31T22:00')
    at('2026-11-01T11:00:00Z')
    assert write(client, 'clock out', employee='Dave Brazer')['recorded_hours'] == [
        {'date': '2026-10-31', 'hours': '2'}, {'date': '2026-11-01', 'hours': '7'}]

    # Somebody deactivated while clocked in is still clocked out, and their time recorded.
    at('2026-11-02T14:00:00Z')
    write(client, 'clock in', employee='Dave Brazer', **job)
    write(client, 'employee deactivate', employee='Dave Brazer')
    at('2026-11-02T16:00:00Z')
    out = write(client, 'clock out', employee='Dave Brazer')
    assert out['recorded_hours'] == [{'date': '2026-11-02', 'hours': '2'}] and len(out['entries']) == 1

    # A clock started earlier than a closed clock of the same person ended would count that time twice.
    at('2026-11-03T20:00:00Z')
    write(client, 'clock in', employee='Dana Fitter', **job, at='2026-11-03T09:00')
    write(client, 'clock out', employee='Dana Fitter', end='2026-11-03T12:00')
    with pytest.raises(BookflowError) as overlap:
        write(client, 'clock in', employee='Dana Fitter', **job, at='2026-11-03T11:00')
    assert overlap.value.code == 'E_VALIDATION' and 'already clocked' in str(overlap.value.details)
    assert write(client, 'clock in', employee='Dana Fitter', **job, at='2026-11-03T12:30')['status'] == 'open'


def test_an_entry_on_a_work_order_is_not_a_clash_with_it_and_a_month_reads_at_once(client, crew):
    import time
    order = write(client, 'work-order create', date='2026-02-01', customer=crew['smith'], title='Repipe',
                  assignees=['Dana Fitter'], scheduled_start='2026-02-03T14:00:00Z', scheduled_end='2026-02-03T18:00:00Z')
    local = read(client, 'schedule calendar', employee='Dana Fitter', view='day', date='2026-02-03')['days'][0]['items']
    starts, ends = local[0]['starts'], local[0]['ends']
    on_it = write(client, 'schedule create', employee='Dana Fitter', work_order=order['number'], starts=starts, ends=ends)
    assert on_it['conflicts'] == [] and on_it['customer_id'] == crew['smith']
    day = read(client, 'schedule calendar', employee='Dana Fitter', view='day', date='2026-02-03')
    assert day['conflicts'] == 0 and len(day['days'][0]['items']) == 2

    # A month of 200 work orders and 200 entries reads in one pass.
    for n in range(200):
        d = f'2026-03-{n % 28 + 1:02d}'
        write(client, 'work-order create', date='2026-03-01', customer=crew['customer'], title=f'Visit {n}',
              assignees=['Dave Brazer'], scheduled_start=f'{d}T{14 + n % 3}:00:00Z', scheduled_end=f'{d}T{15 + n % 3}:00:00Z')
        write(client, 'schedule create', employee='Dana Fitter', customer=crew['customer'],
              starts=f'{d}T{8 + n % 8:02d}:00', ends=f'{d}T{9 + n % 8:02d}:00')
    began = time.perf_counter()
    month = read(client, 'schedule calendar', view='month', date='2026-03-15')
    took = time.perf_counter() - began
    print(f'month calendar, 200 work orders and 200 entries: {took:.2f}s')
    assert sum(len(d['items']) for d in month['days']) >= 400 and took < 20
