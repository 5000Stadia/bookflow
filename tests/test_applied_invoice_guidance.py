"""A caller can follow the paid-invoice correction error to a successful save."""
import pytest
from typer.testing import CliRunner

from bookflow import BookflowError
from bookflow.adapters.cli.app import build_app
from tests.test_service_sales_lifecycle import sale, COMPANY  # noqa: F401
from tests.test_payment_receipts import posted, method, snapshots


def test_paid_invoice_error_guides_guard_preview_and_save(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'GUIDANCE-INVOICE')
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='50.00',
        payment_method=method(client), operation_key='guidance-receive', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='50.00')])), company=COMPANY)
    args = dict(invoice=invoice['id'], expected_version=2, lines=[
        dict(line_id=invoice['revision']['lines'][0]['line_id'], item=sale['item'], quantity='1', unit_price='120.00')])
    before = snapshots(client)
    with pytest.raises(BookflowError) as missing:
        client.run('invoice update', args, reason='Correct price', company=COMPANY, dry_run=True)
    assert missing.value.code == 'E_VALIDATION'
    assert 'operation_key' in missing.value.message and 'reuse' in missing.value.message
    assert snapshots(client) == before
    args['operation_key'] = 'guidance-correct'
    with pytest.raises(BookflowError) as stale:
        client.run('invoice update', args, reason='Correct price', company=COMPANY, dry_run=True)
    assert stale.value.code == 'E_PREVIEW_STALE'
    assert 'invoice settlement' in stale.value.message and 'settlement_guard' in stale.value.message
    assert stale.value.details['settlement_guard']
    assert snapshots(client) == before
    settlement = client.run('invoice settlement', dict(invoice=invoice['id']), company=COMPANY)
    args['settlement_guard'] = settlement['settlement_guard']
    preview = client.run('invoice update', args, reason='Correct price', company=COMPANY, dry_run=True)
    assert snapshots(client) == before
    result = client.run('invoice update', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']), reason='Correct price', company=COMPANY)
    assert result['settlement']['current']['gross_minor_units'] == 12000
    assert result['settlement']['current']['applied_minor_units'] == 5000
    assert result['settlement']['current']['due_minor_units'] == 7000
    assert client.run('payment show', dict(payment=payment['id']), company=COMPANY)['current']['received_minor_units'] == 5000


def test_invoice_help_names_record_state_requirements_and_guard_alternative():
    result = CliRunner().invoke(build_app('invoice update'), ['invoice', 'update', '--help'], terminal_width=240, color=False)
    assert result.exit_code == 0, result.output
    text = ' '.join(result.output.split())
    assert 'Required when the invoice has active payment applications.' in text
    assert 'reuse it for preview, save and retries' in text
    assert 'invoice settlement' in text and 'settlement_guard instead' in text
    assert 'do not provide both alternatives' in text
