"""The CLI's text fields preserve boolean versus text defaults and null clears."""


def test_boolean_default_update_has_explicit_kind_and_clear(cli, client):
    company = client.company.list()['items'][0]['company_id']
    made = cli.json('custom-field', 'create', '--company', company, '--name', 'CLI typed default',
                    '--kind', 'bool', '--scopes', '["customer"]', '--default', 'true')
    assert made['default'] is True
    changed = cli.json('custom-field', 'update', made['id'], '--company', company,
                       '--kind', 'bool', '--default', 'false', '--expected-version', str(made['version']))
    assert changed['default'] is False
    cleared = cli.json('custom-field', 'update', made['id'], '--company', company,
                       '--clear', 'default', '--expected-version', str(changed['version']))
    assert cleared['default'] is None
    current = client.run('custom-field show', {'custom_field': made['id']}, company=company)
    assert current['default'] is None and current['version'] == cleared['version']
