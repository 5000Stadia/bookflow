"""Policy through the actual CLI/HTTP contracts and company role boundaries."""
import pytest
from bookflow import BookflowError
from tests.conftest import make_actor, as_user
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_work_billing_lifecycle import accepted
from tests.test_row8_journal import hosted


def test_cli_policy_and_http_final_closure(client, sale, cli, request):
    source = accepted(client, sale)
    cid = client.company.show(company=COMPANY)['id']
    host = request.getfixturevalue('hosted')
    changed = cli.json('company', 'update', '--no-progress-billing-enabled',
        '--close-estimates-after-billing', '--company', COMPANY)
    assert 'progress_billing_enabled' in changed['changed_fields']
    values = dict(estimate=source['id'], expected_version=source['version'], date='2026-01-13', conversion_key='http-close')
    preview = cli.json('estimate', 'invoice', source['id'], '--expected-version', str(source['version']),
        '--date', '2026-01-13', '--conversion-key', 'http-close', '--company', COMPANY, '--dry-run')
    assert preview['source_effect']['automatically_closed']
    saved = host.ok('estimate.invoice', dict(values, expected_facts_fingerprint=preview['facts_fingerprint']), company=cid)
    assert saved['updated_via'] == 'http' and saved['source_effect']['automatically_closed']
    assert not host.ok('estimate.show', dict(estimate=source['id']), company=cid)['active']


def test_policy_cannot_grant_readonly_or_foreign_authority(client, sale, root):
    source = accepted(client, sale)
    cid = client.company.show(company=COMPANY)['id']
    client.company.update(company=COMPANY, estimates_enabled=False, progress_billing_enabled=False)
    make_actor(root, 'preference-reader', company_role=(cid, 'readonly'))
    reader = as_user(root, 'preference-reader')
    assert not reader.company.show(company=COMPANY)['info']['estimates_enabled']
    for command, values in [('company update', dict(estimates_enabled=True)), ('estimate invoice',
        dict(estimate=source['id'], expected_version=source['version'], date='2026-01-13', conversion_key='readonly', percent='25'))]:
        with pytest.raises(BookflowError) as caught:
            reader.run(command, values, company=COMPANY)
        assert caught.value.code == 'E_PERMISSION' and 'preference_changes' not in caught.value.details
    other = client.company.new(organization='Demo Holdings LLC', legal_name='Other preference company', home_currency='USD')
    with pytest.raises(BookflowError) as caught:
        reader.company.show(company=other['company_id'])
    assert caught.value.code == 'E_COMPANY_NOT_FOUND'


@pytest.mark.parametrize('field', ['estimates_enabled', 'progress_billing_enabled', 'close_estimates_after_billing'])
@pytest.mark.parametrize('value', [None, 1, 'false'])
def test_new_company_strict_settings(client, field, value):
    with pytest.raises(BookflowError) as caught:
        client.company.new(organization='Demo Holdings LLC', legal_name='Invalid preferences', home_currency='USD', **{field:value})
    assert caught.value.code == 'E_VALIDATION'
