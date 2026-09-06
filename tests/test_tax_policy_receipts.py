"""Ordinary receipts retain their existing cash-confirmation omission contract."""
import pytest
from bookflow import BookflowError
from tests.test_tax_policy_sales import sale, tax_sale, request, cells, COMPANY
from tests.test_service_sales_lifecycle import snapshot
from tests.test_payment_receipts import method
from tests.test_row8_journal import assert_oracle


def test_ordinary_receipt_policy_changes_and_confirmation(client, tax_sale):
    bank = client.account.create(name='Tax receipt bank', type='bank', company=COMPANY)['id']
    data = dict(request(tax_sale, 'line_component_half_even'), deposit_to=bank, payment_method=method(client))
    original = client.run('sales-receipt post', data, company=COMPANY)
    assert original['total_minor_units'] == 20
    # Ordinary receipt gross changes may omit amount_received, as before Row24.
    corrected = client.run('sales-receipt update', dict(sales_receipt=original['id'], expected_version=1,
        sales_tax_calculation='invoice_combined_half_up'), company=COMPANY)
    assert corrected['total_minor_units'] == 22
    a,z = tax_sale['rules']
    assert cells(corrected) == {(0,a):1,(0,z):1,(1,a):0,(1,z):0}
    # Equal-gross redistribution also needs no extra confirmation.
    redistributed = client.run('sales-receipt update', dict(sales_receipt=original['id'], expected_version=2,
        sales_tax_calculation='line_combined_half_up'), company=COMPANY)
    assert redistributed['total_minor_units'] == 22
    assert cells(redistributed) == {(0,a):1,(0,z):0,(1,a):1,(1,z):0}
    assert_oracle(client, original['id'], {('2026-06-01',bank):22,
        ('2026-06-01',tax_sale['income']):-20,('2026-06-01',tax_sale['liability']):-2})
    before = snapshot(client)
    with pytest.raises(BookflowError) as exc:
        client.run('sales-receipt update', dict(sales_receipt=original['id'], expected_version=3,
            sales_tax_calculation='line_component_half_even', amount_received='0.22'), company=COMPANY)
    assert exc.value.code == 'E_VALIDATION' and snapshot(client) == before
    final = client.run('sales-receipt update', dict(sales_receipt=original['id'], expected_version=3,
        sales_tax_calculation='line_component_half_even', amount_received='0.20'), company=COMPANY)
    assert final['total_minor_units'] == 20
    client.run('sales-receipt void', dict(sales_receipt=original['id'], expected_version=4),
        reason='Reverse the stored receipt cents', company=COMPANY)
    assert_oracle(client, original['id'], {})
