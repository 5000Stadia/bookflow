"""Mileage (R118): vehicles, rates by date, trips that post nothing, billed exactly once, paid back once.

The money, all by hand:

    Mileage item (other charge) sells at 0.70 a mile. A 42-mile billable trip is 42 x 0.70 = 29.40.
    Rates: 0.655 from 2023-01-01, 0.67 from 2024-01-01.
    A 100-mile trip ending 2023-12-31 deducts 65.50; one ending 2024-01-02 deducts 67.00.
    The 42-mile trip ending 2024-03-05 deducts 42 x 0.67 = 28.14; 12.5 miles x 0.67 = 8.375, exactly half a
    cent, rounds half to even to 8.38 (837.5 cents: 837 is odd, so 838). Reimbursing both: 28.14 + 8.38 = 36.52.
"""
import sqlite3
from pathlib import Path

import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import COMPANY, sale  # noqa: F401  (sale is a fixture)


def write(client, command, reason='Mileage log', **data):
    return client.run(command, data, company=COMPANY, reason=reason)


def read(client, command, **data):
    return client.run(command, data, company=COMPANY)


def snapshot(client):
    path = Path(client.company.show(company=COMPANY)['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


def postings(client):
    path = Path(client.company.show(company=COMPANY)['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        return db.execute('SELECT (SELECT count(*) FROM transactions), (SELECT count(*) FROM transaction_revisions)').fetchone()


@pytest.fixture
def fleet(client, sale):
    income = client.account.create(name='Mileage income', type='income', company=COMPANY)['id']
    code = next(row['id'] for row in read(client, 'sales-tax-code list')['items'] if not row['taxable'])
    item = write(client, 'item create', name='Mileage', type='other_charge', sales_enabled=True, description='Mileage',
                 income_account_id=income, price='0.70', sales_tax_code_id=code)['id']
    van = write(client, 'vehicle create', name='Ford Transit 2019 White', description='Plate 7ABC123')
    write(client, 'mileage-rate set', effective_date='2023-01-01', rate='0.655')
    write(client, 'mileage-rate set', effective_date='2024-01-01', rate='0.67')
    return dict(sale, mileage_item=item, van=van['id'])


def trip(client, fleet, **extra):
    values = dict(vehicle='Ford Transit 2019 White', trip_end_date='2024-03-05', total_miles='42')
    values.update(extra)
    return write(client, 'trip create', **values)


def test_vehicle_list_and_rates_by_effective_date(client, fleet):
    vehicles = read(client, 'vehicle list')['vehicles']
    assert [(v['name'], v['description'], v['active']) for v in vehicles] == [
        ('Ford Transit 2019 White', 'Plate 7ABC123', True)]
    assert [(r['effective_date'], r['rate']) for r in read(client, 'mileage-rate list')['rates']] == [
        ('2024-01-01', '0.67'), ('2023-01-01', '0.655')]

    december = trip(client, fleet, trip_end_date='2023-12-31', total_miles='100')
    january = trip(client, fleet, trip_start_date='2023-12-30', trip_end_date='2024-01-02', total_miles='100')
    assert (december['deduction_rate'], december['deduction']['minor_units']) == ('0.655', 6550)
    # A trip spanning the new year takes the rate in effect on its end date.
    assert (january['deduction_rate'], january['deduction']['minor_units']) == ('0.67', 6700)

    # A rate set again for the same date replaces it, and every reader sees the new one.
    write(client, 'mileage-rate set', effective_date='2024-01-01', rate='0.70')
    assert read(client, 'trip show', trip=january['id'])['deduction']['minor_units'] == 7000
    write(client, 'mileage-rate delete', effective_date='2024-01-01')
    assert read(client, 'trip show', trip=january['id'])['deduction_rate'] == '0.655'
    with pytest.raises(BookflowError) as refused:
        write(client, 'mileage-rate set', effective_date='2024-01-01', rate='0.65555')
    assert refused.value.code == 'E_AMOUNT_PRECISION'

    # An inactive vehicle keeps its trips but takes no new ones.
    write(client, 'vehicle update', vehicle=fleet['van'], active=False)
    with pytest.raises(BookflowError) as refused:
        trip(client, fleet)
    assert refused.value.code == 'E_INACTIVE_REFERENCE'
    assert read(client, 'vehicle show', vehicle=fleet['van'])['trip_count'] == 2


def test_odometer_readings_make_the_miles_and_a_trip_posts_nothing(client, fleet):
    before = postings(client)
    driven = trip(client, fleet, total_miles=None, odometer_start='15000', odometer_end='15042.5')
    assert driven['miles'] == '42.5' and driven['odometer_start'] == '15000'
    assert postings(client) == before, 'recording a trip moves no money'
    for values, field in ((dict(odometer_start='100'), 'odometer_end'),
                          (dict(odometer_start='100', odometer_end='90'), 'odometer_end'),
                          (dict(odometer_start='100', odometer_end='142', total_miles='40'), 'total_miles'),
                          (dict(total_miles='0'), 'total_miles'),
                          (dict(billable=True, total_miles='5'), 'customer')):
        with pytest.raises(BookflowError) as refused:
            trip(client, fleet, **{'total_miles': None, **values})
        assert refused.value.code == 'E_VALIDATION'
        assert refused.value.details['fields'][0]['field'] == field
    corrected = write(client, 'trip update', trip=driven['id'], expected_version=1, total_miles='40')
    assert (corrected['miles'], corrected['odometer_start'], corrected['version']) == ('40', None, 2)


def test_a_billable_trip_is_an_unbilled_cost_and_is_invoiced_once(client, fleet):
    before = postings(client)
    billable = trip(client, fleet, billable=True, customer=fleet['customer'], item='Mileage', notes='Site visit, Smith job')
    assert postings(client) == before, 'a billable trip still posts nothing until it is invoiced'
    assert billable['billing_status'] == 'unbilled' and billable['billable_amount']['minor_units'] == 2940

    lines = [row for row in read(client, 'report unbilled-costs', as_of='2024-12-31')['rows'] if row['kind'] == 'line']
    assert [(row['source_kind'], row['remaining']['minor_units']) for row in lines] == [('mileage', 2940)]

    invoice = write(client, 'trip invoice', trip=billable['id'], expected_version=billable['version'],
                    conversion_key='smith-mileage', date='2024-03-06')
    assert invoice['total_minor_units'] == 2940
    assert invoice['revision']['lines'][0]['description'] == 'Site visit, Smith job'
    shown = read(client, 'trip show', trip=billable['id'])
    assert shown['billing_status'] == 'billed' and shown['invoices'] == [invoice['id']]
    assert [row for row in read(client, 'report unbilled-costs', as_of='2024-12-31')['rows'] if row['kind'] == 'line'] == []

    # A retry under the same key is the same invoice; a second bill finds nothing left and writes nothing.
    again = write(client, 'trip invoice', trip=billable['id'], expected_version=shown['version'],
                  conversion_key='smith-mileage', date='2024-03-06')
    assert again['id'] == invoice['id']
    state = snapshot(client)
    with pytest.raises(BookflowError) as refused:
        write(client, 'trip invoice', trip=billable['id'], expected_version=shown['version'],
              conversion_key='smith-again', date='2024-03-07')
    assert refused.value.code == 'E_WORK_DEPENDENCY'
    assert snapshot(client) == state
    assert client.customer.show(customer=fleet['customer'], company=COMPANY)['current_balance']['minor_units'] == 2940

    # A non-billable trip cannot be billed, and says how to make it billable.
    plain = trip(client, fleet, customer=fleet['customer'])
    with pytest.raises(BookflowError) as refused:
        write(client, 'trip invoice', trip=plain['id'], expected_version=1, conversion_key='nope', date='2024-03-07')
    assert refused.value.code == 'E_WORK_DEPENDENCY' and 'billable' in refused.value.details['problem']


def test_voiding_a_billed_trip_is_refused_until_its_invoice_is(client, fleet):
    billable = trip(client, fleet, billable=True, customer=fleet['customer'], item='Mileage')
    invoice = write(client, 'trip invoice', trip=billable['id'], expected_version=1, conversion_key='k', date='2024-03-06')
    state = snapshot(client)
    for command, values in (('trip void', {}), ('trip update', dict(billable=False)),
                            ('trip update', dict(total_miles='10'))):
        with pytest.raises(BookflowError) as refused:
            write(client, command, reason='Wrong job', trip=billable['id'], expected_version=1, **values)
        assert refused.value.code == 'E_WORK_DEPENDENCY'
        assert 'invoice' in refused.value.details['problem']
    assert snapshot(client) == state
    with pytest.raises(BookflowError) as refused:
        client.run('trip void', dict(trip=billable['id'], expected_version=1), company=COMPANY)
    assert refused.value.code in ('E_REASON_REQUIRED', 'E_WORK_DEPENDENCY')

    client.run('invoice void', dict(invoice=invoice['id'], expected_version=1), company=COMPANY, reason='Wrong job')
    assert read(client, 'trip show', trip=billable['id'])['billing_status'] == 'unbilled'
    voided = write(client, 'trip void', reason='Logged against the wrong job', trip=billable['id'], expected_version=1)
    assert voided['status'] == 'voided' and voided['void_reason'] == 'Logged against the wrong job'
    assert voided['billing_status'] == 'not_billable'
    assert [row for row in read(client, 'report unbilled-costs', as_of='2024-12-31')['rows'] if row['kind'] == 'line'] == []


def _accounts(client):
    bank = next(a['id'] for a in client.account.query(company=COMPANY, limit=200)['items'] if a['type'] == 'bank')
    expense = client.account.create(name='Travel mileage', type='expense', company=COMPANY)['id']
    driver = client.run('employee create', dict(name='Dana Driver'), company=COMPANY)['id']
    return bank, expense, driver


def test_reimbursing_trips_writes_one_check_and_never_pays_a_trip_twice(client, fleet):
    bank, expense, driver = _accounts(client)
    first = trip(client, fleet)
    second = trip(client, fleet, total_miles='12.5', trip_end_date='2024-03-08')
    args = dict(trips=[first['id'], second['id']], pay_to=dict(name_type='employee', name_id=driver), account=bank,
                expense_account=expense, date='2024-03-31')
    preview = client.run('trip reimburse', args, company=COMPANY, reason='March mileage', dry_run=True)
    assert [t['amount']['minor_units'] for t in preview['trips']] == [2814, 838]
    assert preview['total']['minor_units'] == 3652 and preview['check_id'] is None

    paid = write(client, 'trip reimburse', reason='March mileage', expected_facts_fingerprint=preview['facts_fingerprint'], **args)
    check = read(client, 'check show', check=paid['check_id'])
    # One check to the driver for the total: the bank is credited 36.52 and the mileage expense debited per trip.
    assert check['total_minor_units'] == 3652 and check['status'] == 'posted'
    assert (check['revision']['name_type'], check['revision']['name_id']) == ('employee', driver)
    assert check['revision']['debit_minor_units'] == check['revision']['credit_minor_units'] == 3652
    assert read(client, 'trip show', trip=first['id'])['reimbursed_by'] == paid['check_id']
    assert [t['id'] for t in read(client, 'trip query', unreimbursed=True)['trips']] == []

    with pytest.raises(BookflowError) as refused:
        write(client, 'trip reimburse', reason='Paid again', **dict(args, trips=[second['id']]))
    assert refused.value.code == 'E_WORK_DEPENDENCY' and refused.value.details['transaction_id'] == paid['check_id']
    # Nor can its miles move under the check, or the trip be withdrawn.
    with pytest.raises(BookflowError) as refused:
        write(client, 'trip update', trip=first['id'], expected_version=1, total_miles='50')
    assert refused.value.code == 'E_WORK_DEPENDENCY' and 'check' in refused.value.details['problem']

    # Voiding the check frees the trips to be paid back again.
    client.run('check void', dict(check=paid['check_id'], expected_version=1), company=COMPANY, reason='Wrong account')
    assert read(client, 'trip show', trip=first['id'])['reimbursed_by'] is None
    again = write(client, 'trip reimburse', reason='March mileage again', **dict(args, trips=[first['id']]))
    assert again['total']['minor_units'] == 2814


def test_a_reimbursement_in_a_closed_period_is_refused(client, fleet):
    bank, expense, driver = _accounts(client)
    driven = trip(client, fleet)
    write(client, 'company update', closing_date='2024-03-31', reason='Close March')
    state = snapshot(client)
    with pytest.raises(BookflowError) as refused:
        write(client, 'trip reimburse', trips=[driven['id']], pay_to=dict(name_type='employee', name_id=driver),
              account=bank, expense_account=expense, date='2024-03-31')
    assert refused.value.code == 'E_PERIOD_CLOSED'
    assert snapshot(client) == state
    # The trip itself posts nothing, so recording one in a closed period is allowed, as the anchor allows it.
    assert trip(client, fleet, trip_end_date='2024-03-01')['status'] == 'recorded'


def test_mileage_reports_by_vehicle_and_by_job(client, fleet):
    truck = write(client, 'vehicle create', name='Pickup')['id']
    trip(client, fleet, billable=True, customer=fleet['customer'], item='Mileage')       # 42 mi, 28.14 deducted
    trip(client, fleet, total_miles='12.5', trip_end_date='2024-03-08', customer=fleet['customer'])  # 8.38
    trip(client, fleet, vehicle=truck, total_miles='100', trip_end_date='2023-12-31')     # 65.50
    trip(client, fleet, vehicle=truck, total_miles='10', trip_end_date='2025-01-02')      # outside the period

    summary = read(client, 'report mileage-by-vehicle', date_from='2023-01-01', date_to='2024-12-31')
    assert [(r['name'], r['trips'], r['miles'], r['amount']['minor_units']) for r in summary['rows']] == [
        ('Ford Transit 2019 White', 2, '54.5', 3652), ('Pickup', 1, '100', 6550)]
    assert summary['total_miles'] == '154.5' and summary['total_amount']['minor_units'] == 10202

    detail = read(client, 'report mileage-by-vehicle', date_from='2023-01-01', date_to='2024-12-31', vehicle='Pickup',
                  detail=True)
    assert [(d['trip_end_date'], d['miles'], d['rate'], d['amount']['minor_units']) for r in detail['rows']
            for d in r['detail']] == [('2023-12-31', '100', '0.655', 6550)]

    jobs = read(client, 'report mileage-by-job', date_from='2024-01-01', date_to='2024-12-31', detail=True)
    assert [(r['name'], r['miles'], r['billable_miles'], r['amount']['minor_units']) for r in jobs['rows']] == [
        ('Sale witness customer', '54.5', '42', 2940)]
    assert [(d['miles'], d['billing_status'], d['rate']) for d in jobs['rows'][0]['detail']] == [
        ('42', 'unbilled', '0.70'), ('12.5', 'not_billable', None)]
