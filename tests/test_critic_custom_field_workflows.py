import copy
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy.engine import Connection

import bookflow
from bookflow import BookflowError
from bookflow.company import custom_fields as cf
from bookflow.storage.engine import open_database
from tests.test_row8_journal import COMPANY, journal_accounts, database_path, lines, assert_oracle
from tests.test_row8_custom_field_integration import definition, post, complete_state
from tests.test_row5_undo import _event
from tests.test_row3_host import hosted


def error(code, invoke):
    with pytest.raises(BookflowError) as exc:
        invoke()
    assert exc.value.code in ((code,) if isinstance(code, str) else code), exc.value


@pytest.mark.parametrize('noun', ['customer', 'vendor', 'employee', 'other-name', 'item'])
@pytest.mark.parametrize('undo_requested', [True, False])
def test_real_shared_owner_default_choice_undo_and_inactive_workflow(client, noun, undo_requested, tmp_path):
    scope = noun.replace('-', '_')
    field = client.run('custom-field create', dict(name='Critic choice ' + scope,
        kind='choice', scopes=[scope], choices=[{'value': 'Café'}, {'value': 'Phone'}], default='Café'), company=COMPANY)
    def run(verb, **kw):
        return client.run(noun + ' ' + verb, kw, company=COMPANY)
    extra = {} if noun != 'item' else dict(type='service', sales_enabled=True,
        income_account_id=client.account.show(account='Service Income', company=COMPANY)['id'], price='1.00', description='Critic service',
        sales_tax_code_id=client.run('sales-tax-code list', {}, company=COMPANY)['items'][0]['id'])
    owner = run('create', name='Critic default owner ' + scope, **extra)
    selector = {scope: owner['id']}
    initial = run('show', **selector)
    assert next(f['value'] for f in initial['custom_fields'] if f['definition_id'] == field['id']) == 'Café'
    renamed = client.run('custom-field update', dict(custom_field=field['id'], expected_version=1,
        choices=[dict(id=c['id'], value='CAFÉ' if c['value'] == 'Café' else c['value']) for c in field['choices']]), company=COMPANY)
    assert renamed['default'] == 'CAFÉ'
    same = run('update', **selector, expected_version=owner['version'], custom_fields={field['id']: ' cafe\u0301 '})
    assert same['version'] == owner['version']
    changed = run('update', **selector, expected_version=owner['version'], custom_fields={field['id']: 'Phone'})
    change_event = _event(client, COMPANY, noun + ' update', owner['id'])
    try:
        if undo_requested:
            client.run('undo', {'event_id': change_event}, company=COMPANY, reason='Critic restore original choice')
        else:
            run('update', **selector, expected_version=changed['version'], custom_fields={field['id']: 'Café'})
    except BookflowError as exc:
        (tmp_path / ('undo-' + scope + '.json')).write_text(
            json.dumps(dict(error=exc.to_dict(), initial=initial, renamed=renamed, changed=changed,
                current=run('show', **selector)), indent=2))
        raise AssertionError(exc.to_dict()) from exc
    restored = run('show', **selector)
    assert next(f['value'] for f in restored['custom_fields'] if f['definition_id'] == field['id']) == ('Café' if undo_requested else 'CAFÉ')
    error('E_RECORD_IN_USE', lambda: client.run('custom-field update', dict(custom_field=field['id'],
        expected_version=renamed['version'], default=None, choices=[{'value': 'CAFÉ'}, {'value': 'Phone'}]), company=COMPANY))
    client.run('custom-field deactivate', {'custom_field': field['id'], 'expected_version': renamed['version']}, company=COMPANY)
    assert run('update', **selector, expected_version=restored['version'], custom_fields={field['id']: 'CAFÉ'})['version'] == restored['version']
    error('E_INACTIVE_REFERENCE', lambda: run('update', **selector, expected_version=restored['version'], custom_fields={field['id']: 'Phone'}))
    required = client.run('custom-field create', dict(name='Critic required ' + scope,
        kind='text', scopes=[scope], default='Default', required=True), company=COMPANY)
    error('E_VALIDATION', lambda: run('create', name='Critic rejected owner ' + scope,
        custom_fields={required['id']: None}, **extra))
    unrelated = run('update', **selector, expected_version=restored['version'], name='Critic renamed owner ' + scope)
    assert required['id'] not in {f['definition_id'] for f in unrelated['custom_fields']}


@pytest.mark.parametrize('channel', ['python', 'cli'])
def test_exact_values_cli_and_snapshot_copy(client, cli, journal_accounts, tmp_path, monkeypatch, channel):
    kinds = {'text': '  001 <literal>  ', 'number': '-9223372036.854775808', 'bool': False,
             'date': '2024-02-29', 'choice': 'Café'}
    fields = {kind: definition(client, 'Critic exact ' + kind, kind=kind,
        **({'choices': [{'value': 'Café'}, {'value': 'Phone'}]} if kind == 'choice' else {})) for kind in kinds}
    patch = {fields[k]['id']: value for k, value in kinds.items()}
    if channel == 'cli':
        first = cli.json('journal', 'post', '--date', '2026-01-12', '--lines', json.dumps(lines(journal_accounts)),
            '--custom-fields', json.dumps(patch), '--custom-field-kinds', json.dumps({fields[k]['id']: k for k in kinds}), '--company', COMPANY, '--reason', 'Critic exact CLI values')
    else:
        first = post(client, journal_accounts, custom_fields=patch)
    captured = first['revision']['custom_fields_snapshot']
    assert {key: raw['value'] for key, raw in captured.items()} == patch
    assert client.journal.show(journal=first['id'], company=COMPANY)['revision'] == first['revision']
    for kind, field in fields.items():
        client.run('custom-field update', dict(custom_field=field['id'], name='Renamed ' + kind,
            expected_version=field['version']), company=COMPANY)
    unchanged = client.journal.update(journal=first['id'], expected_version=1, custom_fields=patch, company=COMPANY)
    assert unchanged['changed'] is False
    corrected = client.journal.update(journal=first['id'], expected_version=1,
        custom_fields={fields['number']['id']: '9223372036.854775807'}, reason='Critic custom-only correction', company=COMPANY)
    assert corrected['version'] == 2
    assert_oracle(client, first['id'], {('2026-01-12', journal_accounts[0]): 1234, ('2026-01-12', journal_accounts[1]): -1234})
    before = complete_state(client)
    for invalid in ('9223372036.854775808', '-9223372036.854775809', '0.0000000001', 0.1, True):
        error(('E_VALIDATION', 'E_VALUE_RANGE'), lambda: client.journal.update(journal=first['id'], expected_version=2,
            custom_fields={fields['number']['id']: invalid}, company=COMPANY))
    assert complete_state(client) == before
    # Historical reads must never join current definition/choice/slot tables.
    execute = Connection.execute
    def guarded(self, statement, *args, **kwargs):
        sql = str(statement).lower()
        assert not any(name in sql for name in ('custom_field_defs', 'custom_field_choices', 'custom_field_values')), sql
        return execute(self, statement, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(Connection, 'execute', guarded)
        assert client.journal.show(journal=first['id'], revision_number=1, company=COMPANY)['revision']['custom_fields_snapshot'] == captured
    source = database_path(client)
    target = tmp_path / 'copied.db'
    shutil.copy2(source, target)
    with open_database(source, writable=False) as original, open_database(target, writable=False) as copied:
        for table in ('custom_field_defs', 'custom_field_choices', 'custom_field_scopes', 'custom_field_values',
                      'transactions', 'transaction_revisions', 'posting_lines', 'posting_line_sources', 'audit_events', 'audit_entries'):
            assert list(original.raw.execute('SELECT * FROM ' + table + ' ORDER BY id')) == list(copied.raw.execute('SELECT * FROM ' + table + ' ORDER BY id'))


def test_http_exact_patch_errors_and_attribution(hosted):
    field = hosted.ok('custom-field.create', dict(name='Critic HTTP number', kind='number', scopes=['journal_entry']), company=hosted.company_id)
    payload = dict(date='2026-02-12', lines=[dict(account='Checking', side='debit', amount='0.01'),
        dict(account='Service Income', side='credit', amount='0.01')], custom_fields={field['id']: '0.000000001'})
    first = hosted.ok('journal.post', payload, company=hosted.company_id,
        headers={'X-Bookflow-Reason': 'Critic exact HTTP value', 'Idempotency-Key': 'critic-http'})
    assert first['revision']['custom_fields_snapshot'][field['id']]['value'] == '0.000000001'
    audit = hosted.ok('audit.show', {'event': first['revision']['audit_event_id']}, company=hosted.company_id)
    assert audit['reason'] == 'Critic exact HTTP value'
    assert any(e['record_type'] == 'custom_field_value' for e in audit['entries'])
    assert first['created_via'] == 'http' and first['created_by']
    for bad in (None, {field['id']: 0.1}, {field['id']: False}):
        rejected = hosted.call('journal.post', {**payload, 'custom_fields': bad}, company=hosted.company_id)
        assert rejected.status_code == 422 and rejected.json()['code'] == 'E_VALIDATION', rejected.text


def test_undo_defaults_first_use_and_voided_journal_dependencies(client, journal_accounts):
    field = definition(client, 'Critic undo source', kind='choice', choices=[{'value': 'Web'}, {'value': 'Phone'}], default='Web')
    create_event = _event(client, COMPANY, 'custom-field create', field['id'])
    changed = client.run('custom-field update', dict(custom_field=field['id'], expected_version=1,
        scopes=['journal_entry', 'customer']), company=COMPANY)
    scope_event = _event(client, COMPANY, 'custom-field update', field['id'])
    first = post(client, journal_accounts)
    error('E_UNDO_CONFLICT', lambda: client.run('undo', {'event_id': scope_event}, company=COMPANY))
    error('E_UNDO_CONFLICT', lambda: client.run('undo', {'event_id': create_event}, company=COMPANY))
    client.journal.void(journal=first['id'], expected_version=1, reason='Critic dependency after void', company=COMPANY)
    error('E_RECORD_IN_USE', lambda: client.run('custom-field update', dict(custom_field=field['id'], expected_version=changed['version'],
        default=None, choices=[{'value': 'Phone'}]), company=COMPANY))
    error('E_UNDO_CONFLICT', lambda: client.run('undo', {'event_id': create_event}, company=COMPANY))
    unused = definition(client, 'Critic unused default', kind='choice', choices=[{'value': 'Web'}, {'value': 'Phone'}], default='Web')
    updated = client.run('custom-field update', dict(custom_field=unused['id'], expected_version=1,
        default='Phone', choices=[dict(id=c['id'], value=c['value'], active=c['value'] == 'Phone') for c in unused['choices']]), company=COMPANY)
    inverse = _event(client, COMPANY, 'custom-field update', unused['id'])
    client.run('undo', {'event_id': inverse}, company=COMPANY)
    restored = client.run('custom-field show', {'custom_field': unused['id']}, company=COMPANY)
    assert restored['default'] == 'Web'
    assert {c['value'] for c in restored['choices'] if c['active']} == {'Web', 'Phone'}
