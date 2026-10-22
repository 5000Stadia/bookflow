"""R118: the mileage commands through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo. It adds a vehicle, renames it, sets and lists two mileage
rates (and removes a third), records a billable trip by odometer for Riverside Apartments at 0.70 a mile, a
plain trip by total miles, corrects one and voids another, reads them back, bills the billable one (invoice and
its billing window), pays the driver back by check, and runs both mileage reports. Every surface must return
the same figures.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset((
    'mileage-rate delete', 'mileage-rate list', 'mileage-rate set', 'report mileage-by-job', 'report mileage-by-vehicle',
    'trip billing', 'trip create', 'trip invoice', 'trip query', 'trip reimburse', 'trip sales-receipt', 'trip show',
    'trip update', 'trip void', 'vehicle create', 'vehicle list', 'vehicle show', 'vehicle update'))
CUSTOMER = 'Riverside Apartments'
SERVICE = 'Mainline Clearing'


@pytest.mark.timeout(900)
def test_the_same_mileage_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                van = await call('vehicle create', {'name': 'Van 1', 'description': 'Ford Transit'})
                await call('vehicle update', {'vehicle': van['id'], 'name': 'Service van', 'expected_version': 1})
                await call('vehicle show', {'vehicle': 'Service van'})
                await call('vehicle list', {})
                await call('mileage-rate set', {'effective_date': '2026-01-01', 'rate': '0.70'})
                await call('mileage-rate set', {'effective_date': '2025-01-01', 'rate': '0.67'})
                await call('mileage-rate set', {'effective_date': '2027-01-01', 'rate': '0.75'})
                await call('mileage-rate delete', {'effective_date': '2027-01-01'})
                rates = await call('mileage-rate list', {})
                billable = await call('trip create', {
                    'vehicle': 'Service van', 'trip_end_date': '2026-10-20', 'odometer_start': '20100',
                    'odometer_end': '20142', 'billable': True, 'customer': CUSTOMER, 'item': SERVICE,
                    'billing_rate': '0.70', 'notes': 'Riverside callout'})
                plain = await call('trip create', {'vehicle': 'Service van', 'trip_end_date': '2026-10-21',
                                                   'total_miles': '12.5', 'customer': CUSTOMER})
                corrected = await call('trip update', {'trip': plain['id'], 'expected_version': 1, 'total_miles': '15'})
                spare = await call('trip create', {'vehicle': 'Service van', 'trip_end_date': '2026-10-21',
                                                   'total_miles': '3'})
                await call('trip void', {'trip': spare['id'], 'expected_version': 1}, reason='Logged twice')
                shown = await call('trip show', {'trip': billable['id']})
                found = await call('trip query', {'vehicle': 'Service van', 'limit': 10})
                invoice = await call('trip invoice', {'trip': billable['id'], 'expected_version': shown['version'],
                                                      'conversion_key': 'riverside-miles', 'date': '2026-10-22'})
                window = await call('trip billing', {'trip': billable['id']})
                cash = await call('trip create', {
                    'vehicle': 'Service van', 'trip_end_date': '2026-10-22', 'total_miles': '42', 'billable': True,
                    'customer': CUSTOMER, 'item': SERVICE, 'billing_rate': '0.70', 'notes': 'Riverside callout'})
                gross = invoice['total_minor_units']
                receipt = await call('trip sales-receipt', {
                    'trip': cash['id'], 'expected_version': 1, 'conversion_key': 'riverside-cash', 'date': '2026-10-22',
                    'deposit_to': 'Undeposited Funds', 'amount_received': f'{gross // 100}.{gross % 100:02d}'})
                bank = await call('account create', {'name': 'Mileage Checking', 'type': 'bank'})
                expense = await call('account create', {'name': 'Travel Mileage', 'type': 'expense'})
                paid = await call('trip reimburse', {'trips': [billable['id'], plain['id']],
                                                     'pay_to': {'name_type': 'employee', 'name_id': 'Casey Worker'},
                                                     'account': bank['id'], 'expense_account': expense['id'],
                                                     'date': '2026-10-31'})
                by_vehicle = await call('report mileage-by-vehicle', {'date_from': '2026-01-01', 'date_to': '2026-12-31',
                                                                       'detail': True})
                by_job = await call('report mileage-by-job', {'date_from': '2026-01-01', 'date_to': '2026-12-31'})
                results[surface] = (rates, billable, corrected, shown, found, invoice, window, paid, by_vehicle, by_job, receipt)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (rates, billable, corrected, shown, found, invoice, window, paid, by_vehicle, by_job, receipt) in results.items():
        assert [r['rate'] for r in rates['rates']] == ['0.70', '0.67'], surface
        assert (billable['miles'], billable['billing_status']) == ('42', 'unbilled'), surface
        assert corrected['miles'] == '15' and shown['deduction']['amount'] == '29.40', surface
        assert len(found['trips']) == 2, surface
        assert receipt['total_minor_units'] == invoice['total_minor_units'], surface
        assert invoice['revision']['lines'][0]['description'] == 'Riverside callout', surface
        assert window['lines'][0]['state'] == 'billed', surface
        assert paid['total']['amount'] == '39.90' and paid['check_id'], surface  # 42 x 0.70 + 15 x 0.70
        assert by_vehicle['total_amount']['amount'] == '69.30', surface  # 99 miles x 0.70
        assert (by_job['rows'][0]['miles'], by_job['rows'][0]['billable_miles']) == ('99', '84'), surface
        shapes.add(repr((invoice['total_minor_units'], window['remaining_net_minor_units'], paid['total'],
                         [r['amount'] for r in by_vehicle['rows']], [r['amount'] for r in by_job['rows']])))
    assert len(shapes) == 1
