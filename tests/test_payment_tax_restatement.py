"""Hand-fixed 100/8/8 tax cents and removal/restatement of stored tax components."""
import sqlite3

from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method
from tests.test_row8_journal import database_path


def test_tax_tie_final_installment_and_component_removal(client, sale):
    agency = client.vendor.create(name='Settlement tax agency', is_tax_agency=True, company=COMPANY)['id']
    with sqlite3.connect(database_path(client)) as db:
        liability = db.execute("SELECT id FROM accounts WHERE system_role='sales_tax_payable'").fetchone()[0]
    codes = client.run('sales-tax-code list', {}, company=COMPANY)['items']
    taxable = next(row['id'] for row in codes if row['taxable'])
    exempt = next(row['id'] for row in codes if not row['taxable'])
    client.run('company update', dict(sales_tax_enabled=True), company=COMPANY)
    taxes = sorted(client.run('item create', dict(name='Settlement tax '+name, type='sales_tax_item', tax_percent='8',
        tax_agency_vendor_id=agency, liability_account_id=liability), company=COMPANY)['id'] for name in ('A', 'Z'))
    group = client.run('item create', dict(name='Settlement two-tax group', type='sales_tax_group',
        members=[dict(component_item_id=tax, quantity='1') for tax in reversed(taxes)]), company=COMPANY)['id']
    invoice = client.run('invoice post', dict(customer=sale['customer'], date='2026-06-01', sales_tax_item=group,
        lines=[dict(item=sale['item'], quantity='1', unit_price='1.00', tax_code=taxable)]), company=COMPANY)
    payment_method = method(client)
    paid = []
    for version, amount, expected in [(1, '0.08', {None: 7, taxes[0]: 1}), (2, '1.08', {None: 93, taxes[0]: 7, taxes[1]: 8})]:
        result = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount=amount,
            payment_method=payment_method, operation_key=f'tax-tie-{version}', applications=dict(mode='inline', items=[
                dict(invoice=invoice['id'], expected_version=version, amount=amount)])), company=COMPANY)
        assert {row['tax_item_id']: row['amount']['minor_units'] for row in result['effect']['allocations']} == expected
        paid.append(result)
    client.run('payment unapply', dict(payment=paid[1]['id'], expected_version=1, operation_key='tax-remove-final', applications=[
        dict(application_id=paid[1]['effect']['applications'][0]['application_id'], invoice_expected_version=3)]),
        reason='Revisit remaining amount', company=COMPANY)
    # Removing the zero-allocated Z component leaves the first logical split and
    # its actual A recognition facts unchanged; old physical evidence survives.
    with sqlite3.connect(database_path(client)) as db:
        before = db.execute('SELECT * FROM application_allocations ORDER BY rowid').fetchall()
    client.run('invoice update', dict(invoice=invoice['id'], expected_version=4, operation_key='tax-remove-z', sales_tax_item=taxes[0],
        settlement_versions=[dict(payment=paid[0]['id'], expected_version=1)]), reason='Correct applicable tax', company=COMPANY)
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT * FROM application_allocations ORDER BY rowid').fetchall() == before
    corrected = client.run('invoice update', dict(invoice=invoice['id'], expected_version=5, operation_key='tax-exempt',
        settlement_versions=[dict(payment=paid[0]['id'], expected_version=1)], lines=[dict(line_id=invoice['revision']['lines'][0]['line_id'],
            item=sale['item'], quantity='1', unit_price='1.00', tax_code=exempt)]), reason='Customer work is exempt', company=COMPANY)
    assert corrected['settlement']['current']['due_minor_units'] == 92
    live = client.run('application show', dict(application=paid[0]['effect']['applications'][0]['application_id']), company=COMPANY)
    assert [(row['logical_kind'], row['amount_minor_units']) for row in live['current_allocations']] == [('net', 8)]
    assert live['current_payment']['version'] == 2
