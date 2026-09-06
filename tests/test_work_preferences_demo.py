"""Exact old prefixes and isolated voided preference demonstrations."""
import subprocess
import tomllib
from importlib.resources import files
import pytest
from tests.test_reference_year import reference_client, reference_template


@pytest.mark.parametrize('resource,count', [('seed.toml',283), ('reference.toml',189)])
def test_exact_prior_prefix(resource, count):
    current = files('bookflow.demo').joinpath(resource).read_bytes()
    old = subprocess.check_output(['git','show','3644f3fb50a9b74203a366228f4285c24e39c978:src/bookflow/demo/'+resource])
    assert current.startswith(old)
    old_commands = tomllib.loads(old.decode())['commands']
    commands = tomllib.loads(current.decode())['commands']
    assert len(old_commands) == count and commands[:count] == old_commands
    assert len(commands) == count + 12


@pytest.mark.parametrize('company,prefix', [('Demo Plumbing Co','DEMO'), ('Reference Plumbing Co','REF')])
def test_preference_examples_retain_history_and_zero_balance(reference_client, company, prefix):
    client, _ = reference_client
    customer = client.customer.show(customer='Preference Example Customer', company=company)
    assert customer['current_balance']['minor_units'] == 0
    invoice = client.run('invoice query', dict(customer=customer['id']), company=company)['items']
    assert len(invoice) == 1 and invoice[0]['number'] == prefix+'-PREF-INV-1' and invoice[0]['status'] == 'voided'
    assert invoice[0]['subtotal_minor_units'] == invoice[0]['total_minor_units'] == 1000
    source = client.run('estimate query', dict(customer=customer['id']), company=company)['items'][0]
    assert source['status'] == 'accepted' and source['active'] and source['version'] == 4
    history = client.run('estimate history', dict(estimate=source['id']), company=company)['items']
    assert [v['active'] for v in history] == [True,True,False,True]
    info = client.company.show(company=company)['info']
    assert [info[f] for f in ('estimates_enabled','progress_billing_enabled','close_estimates_after_billing')] == [True,True,False]
