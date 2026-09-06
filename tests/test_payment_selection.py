"""Public shared-draft continuation with independent business-value assertions."""
import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY


def invoice(client, sale, number):
    return client.run('invoice post', dict(number=number, date='2026-06-01', customer=sale['customer'],
        lines=[dict(item=sale['item'], quantity='1', unit_price='100.00')]), company=COMPANY)


def test_public_draft_origins_history_clear_and_stale(client, sale):
    first, second = invoice(client, sale, 'PAY-DRAFT-A'), invoice(client, sale, 'PAY-DRAFT-B')
    draft = client.run('payment selection create', dict(mode='new_receipt', customer=sale['customer'],
        date='2026-06-01', amount='150.00'), company=COMPANY)
    assert draft['amount']['minor_units'] == 15000 and draft['amount_origin'] == 'entered'
    draft = client.run('payment selection update', dict(selection=draft['id'], expected_version=1,
        set_items=[dict(invoice=row['id'], expected_version=1, amount_origin='calculated') for row in (first, second)]), company=COMPANY)
    items = client.run('payment selection items', dict(selection=draft['id']), company=COMPANY)['items']
    assert [row['amount_minor_units'] for row in items] == [10000, 5000]
    resumed = client.run('payment selection show', dict(selection=draft['id']), company=COMPANY)
    assert resumed['amount_origin'] == 'entered' and resumed['amount']['minor_units'] == 15000
    draft = client.run('payment selection update', dict(selection=draft['id'], expected_version=2,
        remove_invoices=[first['id']]), company=COMPANY)
    items = client.run('payment selection items', dict(selection=draft['id']), company=COMPANY)['items']
    assert items[0]['amount_minor_units'] == 10000 and draft['unapplied_minor_units'] == 5000
    historical = client.run('payment selection items', dict(selection=draft['id'], revision=2), company=COMPANY)['items']
    assert [row['amount_minor_units'] for row in historical] == [10000, 5000]
    with pytest.raises(BookflowError) as caught:
        client.run('payment selection clear', dict(selection=draft['id'], expected_version=2), company=COMPANY)
    assert caught.value.code == 'E_VERSION_CONFLICT'
    cleared = client.run('payment selection clear', dict(selection=draft['id'], expected_version=3), company=COMPANY)
    assert cleared['item_count'] == 0 and cleared['amount']['minor_units'] == 15000
    assert client.run('invoice show', dict(invoice=first['id']), company=COMPANY)['version'] == 1
    assert client.customer.show(customer=sale['customer'], company=COMPANY)['current_balance']['minor_units'] == 20000


def test_manually_fixed_row_survives_deselect_and_live_policy_change(client, sale):
    first, second = invoice(client, sale, 'PAY-MANUAL-A'), invoice(client, sale, 'PAY-MANUAL-B')
    draft = client.run('payment selection create', dict(mode='new_receipt', customer=sale['customer'],
        date='2026-06-01', amount='150.00'), company=COMPANY)
    draft = client.run('payment selection update', dict(selection=draft['id'], expected_version=1, set_items=[
        dict(invoice=first['id'], expected_version=1, amount_origin='calculated'),
        dict(invoice=second['id'], expected_version=1, amount='50.00')]), company=COMPANY)
    info = client.run('company show', {}, company=COMPANY)
    client.run('company update', dict(expected_version=info['info_version'], automatically_calculate_payments=True), company=COMPANY)
    draft = client.run('payment selection update', dict(selection=draft['id'], expected_version=2,
        remove_invoices=[first['id']]), company=COMPANY)
    assert draft['unapplied_minor_units'] == 10000 and draft['amount']['minor_units'] == 15000
    item = client.run('payment selection items', dict(selection=draft['id']), company=COMPANY)['items'][0]
    assert (item['amount_origin'], item['amount_minor_units']) == ('entered', 5000)


@pytest.mark.parametrize('value', [None, 1, 'true', 0.0])
def test_payment_preferences_are_strict_booleans(client, value):
    info = client.run('company show', {}, company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.run('company update', dict(expected_version=info['info_version'], automatically_apply_payments=value), company=COMPANY)
    assert caught.value.code == 'E_VALIDATION'
