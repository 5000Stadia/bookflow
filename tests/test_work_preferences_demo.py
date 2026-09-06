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
    assert len(commands[count:count + 12]) == 12  # Row19 preference block
    assert len(commands) == count + 12 + 17 + 33  # active tax and payment union


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


@pytest.mark.parametrize('resource,base_count', [('seed.toml',295), ('reference.toml',201)])
def test_complete_tax_payment_append_union_preserves_each_frozen_command(resource, base_count):
    """Both reviewed tails survive, with their original order and capture inputs."""
    import json
    import re
    def frozen(commit):
        raw = subprocess.check_output(['git','show',commit+':src/bookflow/demo/'+resource])
        return raw, tomllib.loads(raw.decode())['commands']
    base_bytes, base = frozen('3f9a307e7b8f878d613f915087f6b552a17a04da')
    _, tax = frozen('ac059ac26b0d86fa8e035ad57f876f6b0c6c9729')
    _, payment = frozen('4883e7445efa70409e9466f18f5ce92d43a36f7b')
    current = files('bookflow.demo').joinpath(resource).read_bytes()
    commands = tomllib.loads(current.decode())['commands']
    assert current.startswith(base_bytes)
    assert len(base) == base_count and tax[:base_count] == payment[:base_count] == base
    assert len(tax[base_count:]) == 17 and len(payment[base_count:]) == 33
    assert commands == base + tax[base_count:] + payment[base_count:]
    captures = {'null'}  # seed language literal, not a capture
    for entry in commands:
        references = re.findall(r'\$\{([^}.]+)(?:\.[^}]+)?\}', json.dumps(entry))
        assert set(references) <= captures, (entry['command'], set(references)-captures)
        if 'capture' in entry:
            captures.add(entry['capture'])
    tax_captures = {e['capture'] for e in tax[base_count:] if 'capture' in e}
    payment_captures = {e['capture'] for e in payment[base_count:] if 'capture' in e}
    assert tax_captures.isdisjoint(payment_captures)
