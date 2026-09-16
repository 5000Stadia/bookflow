"""Time worked on a job: recorded as the one-line work document it is, and billed exactly once.

Every figure below is arithmetic on the witness service item the `sale` fixture creates, which
sells at **12.34 an hour**. So an hour and a half is 18.51, two hours are 24.68, and twenty
minutes -- 0.333333 of an hour -- are 4.11. Nothing here asserts a number the commands merely
echoed back; the charge is always read from the billing state or the invoice that carries it.

The four things this file exists to prove:

**Recorded time is a customer-work document, not a second kind of record.** It has a version, an
immutable revision per correction, a number, a customer and one line carrying a service item, a
quantity and a rate -- the same shape a quoted line has, checked here through the same outputs.
Recording it moves no money: the transaction, batch and posting-line counts are identical before
and after.

**A duration is hours, exactly.** Twenty minutes is stored as 0.333333 hours and multiplied by
the rate, not rounded to 0.33 and then multiplied. The quantity that comes back out is the
quantity that went in, to six places, and durations outside a working day are refused by name.

**A stretch of time is billed exactly once.** The interval ledger the other work kinds already
use is what makes that true, so the second attempt is refused with no flag to consult and, more
to the point, with nothing written: the whole company database is compared before and after the
refusal and is byte-for-byte the same. The same equality guards the withdrawal a sale is
standing on.

**A correction to somebody's hours always says why.** That is stricter than `estimate update`,
which takes a correction with no reason at all -- an asymmetry witnessed here rather than
assumed, because hours already billed were billed on the old number.
"""
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.company.work import _state_invariants
from bookflow.storage.engine import open_database
from tests.test_service_sales_lifecycle import sale, COMPANY  # noqa: F401  (sale is a fixture)

# The money. Everything below is arithmetic on these.
RATE = 1234             # 12.34 an hour, the witness service item's price
HOUR_AND_A_HALF = 1851  # 18.51
TWO_HOURS = 2468        # 24.68
TWENTY_MINUTES = 411    # 4.11, from a quantity of 0.333333 hours


def write(client, command, reason='Timesheet entry', **data):
    return client.run(command, data, company=COMPANY, reason=reason)


def read(client, command, **data):
    return client.run(command, data, company=COMPANY)


def database(client):
    return Path(client.company.show(company=COMPANY)['path']) / 'company.db'


def snapshot(client):
    """The whole company database, as the statements that would rebuild it."""
    with sqlite3.connect(database(client)) as db:
        return list(db.iterdump())


@pytest.fixture
def worker(client):
    return client.run('employee create', dict(name='Dana Fitter'), company=COMPANY)['id']


def record(client, worker, sale, duration='1.5', **extra):
    values = dict(date='2026-01-12', employee=worker, customer=sale['customer'],
                  item=sale['item'], duration=duration, note='Traced the leak')
    values.update(extra)
    return write(client, 'time-activity create', **values)


def test_an_hour_and_a_half_is_one_line_of_work_and_moves_no_money(client, worker, sale):
    tables = (c.transactions, c.posting_batches, c.posting_lines)
    with open_database(database(client), writable=False) as db:
        before = {table.name: db.conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                  for table in tables}

    entry = record(client, worker, sale)
    assert entry['kind'] == 'time_activity' and entry['status'] == 'recorded'
    assert entry['version'] == 1 and entry['net_minor_units'] == HOUR_AND_A_HALF
    # The headline is derived, because a time entry has no name of its own: the person and the
    # date are what identify one in a list, and the date is already a column.
    assert entry['title'] == 'Time — Dana Fitter'
    assert [who['label'] for who in entry['revision']['facts']['assignees']] == ['Dana Fitter']

    line, = entry['revision']['lines']
    assert line['quantity'] == '1.5'
    assert line['unit_price']['minor_units'] == RATE
    assert line['net']['minor_units'] == HOUR_AND_A_HALF
    # The note is the line description a customer reads on the invoice, and the service item is
    # what carries the income account the labour lands in.
    assert line['facts']['description'] == 'Traced the leak'
    assert line['facts']['item_id'] == sale['item']
    assert line['facts']['profile']['income_account']['id'] == sale['income']
    assert line['facts']['quantity_microunits'] == 1_500_000, 'hours in millionths, as every quantity is'
    assert line['facts']['billable'] is True

    with open_database(database(client), writable=False) as db:
        assert {table.name: db.conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                for table in tables} == before


def test_twenty_minutes_stays_twenty_minutes(client, worker, sale):
    """0.333333 hours, not 0.33: a rounded quantity handed to an exact extension is a cent error."""
    entry = record(client, worker, sale, duration='0.333333')
    assert entry['revision']['lines'][0]['quantity'] == '0.333333'
    state = read(client, 'time-activity billing', time_activity=entry['id'])
    assert state['lines'][0]['remaining_net_minor_units'] == TWENTY_MINUTES
    # The quantity survives the round trip through the invoice, too.
    invoice = write(client, 'time-activity invoice', reason='Billing the visit',
                    time_activity=entry['id'], expected_version=entry['version'],
                    conversion_key='twenty minutes', date='2026-01-13')
    assert invoice['revision']['lines'][0]['quantity'] == '0.333333'
    assert invoice['total_minor_units'] == TWENTY_MINUTES


def test_a_correction_to_somebodys_hours_always_says_why(client, worker, sale):
    entry = record(client, worker, sale)

    with pytest.raises(BookflowError) as refused:
        client.run('time-activity update',
                   dict(time_activity=entry['id'], expected_version=1, duration='2'), company=COMPANY)
    assert refused.value.code == 'E_REASON_REQUIRED'

    # A quoted estimate takes the same shape of correction with no reason at all. The asymmetry
    # is deliberate: a quote is a proposal being revised, recorded hours are a fact being
    # restated after the fact, and what was already billed from them was billed on the old one.
    quote = write(client, 'estimate create', title='Replace the tap', date='2026-01-12',
                  customer=sale['customer'], lines=[dict(item=sale['item'], quantity='2')])
    revised = client.run('estimate update', dict(estimate=quote['id'], expected_version=1,
                         memo='Site visit arranged'), company=COMPANY)
    assert revised['version'] == 2, 'estimate update still takes a correction with no reason'

    # A patch that moves nothing is still a no-op, not a demand for a reason to do nothing.
    unchanged = client.run('time-activity update',
                           dict(time_activity=entry['id'], expected_version=1), company=COMPANY)
    assert unchanged['changed'] is False and unchanged['version'] == 1

    corrected = write(client, 'time-activity update', reason='Timesheet said two hours',
                      time_activity=entry['id'], expected_version=1, duration='2')
    assert corrected['version'] == 2 and corrected['net_minor_units'] == TWO_HOURS

    # The prior revision stays readable in full, and the reason is recorded against the new one.
    history = read(client, 'time-activity history', time_activity=entry['id'])
    assert [row['revision_number'] for row in history['items']] == [1, 2]
    first = read(client, 'time-activity show', time_activity=entry['id'], revision_number=1)
    assert first['revision']['lines'][0]['quantity'] == '1.5'

    over = 'x' * 141
    with pytest.raises(BookflowError) as refused:
        write(client, 'time-activity update', reason=over,
              time_activity=entry['id'], expected_version=2, duration='3')
    assert refused.value.code == 'E_VALIDATION'


def test_the_same_stretch_of_time_cannot_be_billed_twice(client, worker, sale):
    entry = record(client, worker, sale, duration='2')
    invoice = write(client, 'time-activity invoice', reason='Billing the visit',
                    time_activity=entry['id'], expected_version=entry['version'],
                    conversion_key='first', date='2026-01-13')
    assert invoice['total_minor_units'] == TWO_HOURS

    state = read(client, 'time-activity billing', time_activity=entry['id'])
    assert state['lines'][0]['state'] == 'billed'
    assert state['remaining_net_minor_units'] == 0 and state['can_invoice'] is False

    # No flag on the entry says "billed": the hour is an occupied span in the allocation ledger,
    # and a second attempt -- under its own conversion key, so this is not replay -- finds no
    # free span to take. It is refused having written nothing at all.
    current = read(client, 'time-activity show', time_activity=entry['id'])
    before = snapshot(client)
    with pytest.raises(BookflowError) as refused:
        write(client, 'time-activity invoice', reason='Billing it again',
              time_activity=entry['id'], expected_version=current['version'],
              conversion_key='second', date='2026-01-14')
    assert refused.value.code == 'E_WORK_DEPENDENCY'
    assert snapshot(client) == before

    # The customer owes for the hour exactly once.
    assert client.customer.show(customer=sale['customer'], company=COMPANY)[
        'current_balance']['minor_units'] == TWO_HOURS


def test_billed_time_cannot_be_withdrawn_until_the_sale_is(client, worker, sale):
    entry = record(client, worker, sale, duration='2')
    invoice = write(client, 'time-activity invoice', reason='Billing the visit',
                    time_activity=entry['id'], expected_version=entry['version'],
                    conversion_key='first', date='2026-01-13')

    current = read(client, 'time-activity show', time_activity=entry['id'])
    before = snapshot(client)
    with pytest.raises(BookflowError) as refused:
        write(client, 'time-activity void', reason='Logged against the wrong job',
              time_activity=entry['id'], expected_version=current['version'])
    assert refused.value.code == 'E_WORK_DEPENDENCY'
    assert refused.value.details['source_id'] == entry['id']
    assert snapshot(client) == before

    # Void the sale and the hour is released, so the withdrawal is then a withdrawal of time
    # nobody has charged for -- which is the only kind that can be withdrawn.
    client.run('invoice void', dict(invoice=invoice['id'], expected_version=1),
               company=COMPANY, reason='Billed the wrong job')
    released = read(client, 'time-activity show', time_activity=entry['id'])
    withdrawn = write(client, 'time-activity void', reason='Logged against the wrong job',
                      time_activity=entry['id'], expected_version=released['version'])
    assert withdrawn['status'] == 'voided' and withdrawn['active'] is False


def test_withdrawn_time_is_terminal_and_stays_readable(client, worker, sale):
    entry = record(client, worker, sale)
    withdrawn = write(client, 'time-activity void', reason='Logged against the wrong job',
                      time_activity=entry['id'], expected_version=1)
    assert withdrawn['status'] == 'voided' and withdrawn['active'] is False
    # It posted nothing, so nothing is reversed; the reason is the whole record of the decision.
    assert withdrawn['revision']['decision_note'] == 'Logged against the wrong job'
    assert withdrawn['revision']['lines'][0]['quantity'] == '1.5', 'the hours stay readable'

    with pytest.raises(BookflowError) as refused:
        write(client, 'time-activity invoice', reason='Billing it anyway',
              time_activity=entry['id'], expected_version=withdrawn['version'],
              conversion_key='dead', date='2026-01-13')
    assert refused.value.code == 'E_WORK_DEPENDENCY'

    with pytest.raises(BookflowError) as refused:
        write(client, 'time-activity update', reason='Changing withdrawn time',
              time_activity=entry['id'], expected_version=withdrawn['version'], duration='3')
    assert refused.value.code == 'E_VALIDATION'

    # A second withdrawal says so by changing nothing, exactly as a second `estimate void` does.
    again = write(client, 'time-activity void', reason='Withdrawing it twice',
                  time_activity=entry['id'], expected_version=withdrawn['version'])
    assert again['changed'] is False and again['version'] == withdrawn['version']


def test_non_billable_time_reaches_the_job_and_never_an_invoice(client, worker, sale):
    entry = record(client, worker, sale, duration='1', billable=False, note='Warranty callback')
    state = read(client, 'time-activity billing', time_activity=entry['id'])
    assert state['lines'][0]['state'] == 'nonbillable'
    assert state['can_invoice'] is False and state['can_sales_receipt'] is False
    with pytest.raises(BookflowError) as refused:
        write(client, 'time-activity invoice', reason='Billing the callback',
              time_activity=entry['id'], expected_version=entry['version'],
              conversion_key='callback', date='2026-01-13')
    assert refused.value.code == 'E_WORK_DEPENDENCY'
    # It is still the job's time: the entry is recorded, readable and attributed to the customer.
    assert read(client, 'time-activity show', time_activity=entry['id'])['status'] == 'recorded'


def test_a_rate_may_override_the_item_price_for_one_entry(client, worker, sale):
    entry = record(client, worker, sale, duration='2', rate='95.00', note='Emergency call-out')
    assert entry['revision']['lines'][0]['unit_price']['minor_units'] == 9500
    assert entry['net_minor_units'] == 19000
    # Clearing it charges the service item's own rate again rather than leaving no price.
    restored = write(client, 'time-activity update', reason='Charge the standard rate',
                     time_activity=entry['id'], expected_version=1, rate=None)
    assert restored['revision']['lines'][0]['unit_price']['minor_units'] == RATE
    assert restored['net_minor_units'] == TWO_HOURS


def test_what_a_time_entry_has_to_be(client, worker, sale):
    """The service item is required, the duration is a working day's worth, and that is the surface."""
    without_item = dict(date='2026-01-12', employee=worker, customer=sale['customer'], duration='1')
    with pytest.raises(BookflowError) as refused:
        write(client, 'time-activity create', **without_item)
    assert refused.value.code == 'E_VALIDATION'
    assert refused.value.details['fields'][0]['field'] == 'item'

    for duration, complaint in (('0', 'greater than zero'), ('-1', 'greater than zero'),
                                ('25', 'cannot exceed 24 hours'),
                                ('0.0000001', 'at most 6 fractional digits')):
        with pytest.raises(BookflowError) as refused:
            record(client, worker, sale, duration=duration)
        assert refused.value.code == 'E_VALIDATION'
        assert complaint in refused.value.details['fields'][0]['problem']

    # There is no status to drive: recorded time is withdrawn by voiding it.
    entry = record(client, worker, sale)
    with pytest.raises(BookflowError) as refused:
        write(client, 'time-activity update', time_activity=entry['id'],
              expected_version=1, status='voided')
    assert refused.value.code == 'E_VALIDATION'


def test_the_shape_guard_holds_even_where_the_command_cannot_reach_it(sale):
    """One person, one stretch of time -- checked where every path reaches, not just at the input.

    The command surface has no `assignees` and no `lines`, so these two can only be broken from
    inside. They are asserted here against the guard itself, because a later caller building a
    work document directly is exactly the case the guard is standing in for.
    """
    def value(assignees, lines):
        return dict(status='recorded', date='2026-01-12', lines=lines,
                    facts=dict(assignees=assignees, expires_on=None))

    _state_invariants('time_activity', value(['one'], [{'line': 1}]))

    for assignees, lines, field in ((['one', 'two'], [{'line': 1}], 'employee'),
                                    ([], [{'line': 1}], 'employee'),
                                    (['one'], [{'line': 1}, {'line': 2}], 'duration'),
                                    (['one'], [], 'duration')):
        with pytest.raises(BookflowError) as refused:
            _state_invariants('time_activity', value(assignees, lines))
        assert refused.value.code == 'E_VALIDATION'
        assert refused.value.details['fields'][0]['field'] == field


def test_recorded_time_waits_in_the_unbilled_costs_report_and_leaves_when_billed(client, worker, sale):
    entry = record(client, worker, sale, duration='2')
    report = read(client, 'report unbilled-costs', as_of='2026-01-31')
    lines = [row for row in report['rows'] if row['kind'] == 'line']
    assert [row['source_kind'] for row in lines] == ['time_activity']
    assert lines[0]['source_id'] == entry['id']
    assert lines[0]['remaining']['minor_units'] == TWO_HOURS

    write(client, 'time-activity invoice', reason='Billing the visit',
          time_activity=entry['id'], expected_version=entry['version'],
          conversion_key='first', date='2026-01-13')
    after = read(client, 'report unbilled-costs', as_of='2026-01-31')
    assert [row for row in after['rows'] if row['kind'] == 'line'] == []

    # Non-billable time is the job's cost, never a debt anyone is waiting to charge for.
    record(client, worker, sale, duration='1', billable=False, note='Warranty callback')
    still = read(client, 'report unbilled-costs', as_of='2026-01-31')
    assert [row for row in still['rows'] if row['kind'] == 'line'] == []


def test_recorded_time_pages_by_status_oldest_first(client, worker, sale):
    first = record(client, worker, sale, duration='1')
    second = record(client, worker, sale, duration='2')
    write(client, 'time-activity void', reason='Logged against the wrong job',
          time_activity=second['id'], expected_version=1)

    live = read(client, 'time-activity query', status='recorded')
    assert [row['id'] for row in live['items']] == [first['id']]
    # Withdrawn time is inactive, and the page lists what is live unless asked otherwise --
    # the same default `estimate query` has, so one habit covers both.
    assert read(client, 'time-activity query', status='voided')['items'] == []
    withdrawn = read(client, 'time-activity query', status='voided', active=False)
    assert [row['id'] for row in withdrawn['items']] == [second['id']]
    # A state belonging to another kind is refused rather than quietly matching nothing.
    with pytest.raises(BookflowError) as refused:
        read(client, 'time-activity query', status='in_progress')
    assert refused.value.code == 'E_VALIDATION'
