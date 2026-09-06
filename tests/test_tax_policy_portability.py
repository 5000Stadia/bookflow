"""Company-local tax capture and payment recovery survive attachment to a fresh hub."""
from pathlib import Path
import shutil
import bookflow
from tests.test_tax_policy_sales import sale, tax_sale, request, COMPANY
from tests.test_payment_receipts import method
from tests.test_tax_policy_migration import co14_root


def attach(client, company, tmp_path):
    source = Path(client.company.show(company=company)['path'])
    root = tmp_path/'tax-portable-hub'
    target = bookflow.connect(data_root=str(root))
    target.init()
    target.organization.new(name='Tax portability')
    destination = root/'organizations'/'Tax portability'/'Imported'
    shutil.copytree(source, destination)
    identity = target.company.attach(path=str(destination))['company_id']
    return target, identity


def test_new_policy_paid_history_recovery_after_attach(client, tax_sale, tmp_path):
    original = client.run('invoice post', request(tax_sale), company=COMPANY)
    payment_input = dict(customer=tax_sale['customer'], date='2026-06-02', amount='0.10',
        payment_method=method(client), operation_key='portable-tax-pay', applications=dict(mode='inline', items=[
            dict(invoice=original['id'], expected_version=1, amount='0.10')]))
    paid = client.run('payment receive', payment_input, company=COMPANY)
    target, company = attach(client, COMPANY, tmp_path)
    copied = target.run('invoice show', dict(invoice=original['id']), company=company)
    assert copied['revision'] == original['revision']
    target.run('company update', dict(sales_tax_calculation='line_component_half_even'), company=company)
    corrected = target.run('invoice update', dict(invoice=original['id'], expected_version=2, memo='Moved company metadata',
        operation_key='portable-tax-edit', settlement_versions=[dict(payment=paid['id'], expected_version=1)]),
        reason='Retain captured invoice tax in relocated company', company=company)
    assert corrected['tax_minor_units'] == 2
    assert corrected['revision']['tax_calculation_details'] == original['revision']['tax_calculation_details']
    assert [r['tax_ordinal'] for r in corrected['revision']['lines']] == [1,2]
    assert corrected['settlement']['current']['due_minor_units'] == 12
    target.run('payment unapply', dict(payment=paid['id'], expected_version=1, operation_key='portable-tax-unapply',
        applications=[dict(application_id=paid['effect']['applications'][0]['application_id'], invoice_expected_version=3)]),
        reason='Release copied invoice payment', company=company)
    voided = target.run('invoice void', dict(invoice=original['id'], expected_version=4),
        reason='Reverse copied invoice using its stored cents', company=company)
    assert voided['status'] == 'voided'
    historical = target.run('invoice show', dict(invoice=original['id'], revision_number=1), company=company)['revision']
    assert {k:v for k,v in historical.items() if k != 'batches'} == {k:v for k,v in original['revision'].items() if k != 'batches'}
    assert [batch['kind'] for batch in historical['batches']] == ['original', 'reversal']
    assert all(batch['debit_total']['minor_units'] == batch['credit_total']['minor_units'] == 22 for batch in historical['batches'])
    recovered = target.run('payment receive', payment_input, company=company)
    assert recovered['idempotent_replay'] and recovered['effect'] == paid['effect']
    assert client.run('invoice show', dict(invoice=original['id']), company=COMPANY)['status'] == 'posted'


def test_migrated_legacy_capture_after_attach(co14_root, tmp_path):
    source, results = co14_root
    root=tmp_path/'legacy-upgrade';shutil.copytree(source,root)
    client=bookflow.connect(data_root=str(root));client.run('upgrade',{})
    target, company = attach(client, 'Demo Plumbing Co', tmp_path)
    original, paid = results['invoice'], results['payment']
    target.run('company update', dict(sales_tax_calculation='invoice_combined_half_up'), company=company)
    shown = target.run('invoice show', dict(invoice=original['id']), company=company)
    assert shown['revision']['profile'] == original['revision']['profile']
    assert shown['revision']['tax_calculation_details']['origin']['kind'] == 'legacy_implicit'
    corrected = target.run('invoice update', dict(invoice=original['id'], expected_version=2, memo='Moved legacy company',
        operation_key='portable-legacy-tax-edit', settlement_versions=[dict(payment=paid['id'], expected_version=1)]),
        reason='Preserve legacy captured policy after attach', company=company)
    assert corrected['revision']['tax_calculation_details']['origin']['kind'] == 'legacy_implicit'
    assert corrected['revision']['tax_calculation_details']['policy'] == 'line_component_half_even'
    assert corrected['total_minor_units'] == original['total_minor_units']
    assert target.run('invoice show', dict(invoice=original['id'], revision_number=1), company=company)['revision']['profile'] == original['revision']['profile']
