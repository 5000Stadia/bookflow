"""Active rounding examples preserve old manifests and carry a fixed ledger delta."""
from importlib.resources import files
import subprocess
import tomllib
import pytest
from tests.test_reference_year import reference_client, reference_template


@pytest.mark.parametrize('resource', ['seed.toml', 'reference.toml'])
def test_exact_prior_tax_prefix(resource):
    old = subprocess.check_output(['git','show','15f3e034a773f0b227150403e491f2c5a5edcf5e:src/bookflow/demo/'+resource])
    current = files('bookflow.demo').joinpath(resource).read_bytes()
    assert current.startswith(old)
    old_commands = tomllib.loads(old.decode())['commands']
    commands = tomllib.loads(current.decode())['commands']
    assert commands[:len(old_commands)] == old_commands
    own = commands[len(old_commands):len(old_commands)+17]
    assert len(own) == 17
    assert [entry['input']['sales_tax_calculation'] for entry in own if entry['command']=='invoice post'] == [
        'line_component_half_even','line_combined_half_up','invoice_combined_half_up']


@pytest.mark.parametrize('company,prefix', [('Demo Plumbing Co','DEMO'),('Reference Plumbing Co','REF')])
def test_active_tax_cells_and_exact_new_balances(reference_client, company, prefix):
    client,_ = reference_client
    customer = client.customer.show(customer='Tax Rounding Example Customer', company=company)
    assert customer['current_balance']['minor_units'] == 33
    for tag, tax in [('LEGACY',[0,0]),('LINE',[1,1]),('TOTAL',[1,0])]:
        output = client.run('invoice show', dict(invoice=prefix+'-TAX-'+tag), company=company)
        assert output['status'] == 'posted' and output['version'] == 1
        assert output['subtotal_minor_units'] == 10
        assert output['tax_minor_units'] == sum(tax)
        assert output['total_minor_units'] == 10+sum(tax)
        assert [line['tax_minor_units'] for line in output['revision']['lines']] == tax
        assert [line['tax_ordinal'] for line in output['revision']['lines']] == [1,2]
        assert output['revision']['tax_calculation_details']['origin']['kind'] == 'explicit'


@pytest.mark.parametrize('company,prefix',[('Demo Plumbing Co','DEMO'),('Reference Plumbing Co','REF')])
def test_active_work_policy_chain(reference_client,company,prefix):
    client,_=reference_client
    for tag,tax in [('LEGACY',0),('LINE',2),('TOTAL',1)]:
        proposal=client.run('proposal show',dict(proposal=prefix+'-TAX-PROP-'+tag),company=company)
        assert proposal['tax_minor_units']==tax and proposal['active']
        assert proposal['revision']['facts']['schema_version']==2
    order=client.run('work-order show',dict(work_order=prefix+'-TAX-WO'),company=company)
    assert order['status']=='complete' and order['tax_minor_units']==1
    state=client.run('work-order billing',dict(work_order=order['id']),company=company)
    assert state['remaining_net_minor_units']==5 and state['remaining_tax_minor_units']==1 and state['can_bill_together']
    invoice=client.run('invoice show',dict(invoice=prefix+'-TAX-WORK-INV'),company=company)
    assert invoice['status']=='posted' and invoice['total_minor_units']==6
    assert invoice['revision']['billing_sources'][0]['allocation_version']==3
    assert client.customer.show(customer='Tax Work Example Customer',company=company)['current_balance']['minor_units']==6
