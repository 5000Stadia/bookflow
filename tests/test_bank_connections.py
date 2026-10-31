"""Connection capabilities, keyring references, exact first-page consent and restore safety."""
import json
import sqlite3

import pytest

from bookflow import BookflowError
from bookflow.company import bank_sources as sources
from bookflow.company.statement_files import Line
from tests.test_bank_feeds import run, database_path, table
from tests.test_row3_host import hosted  # noqa: F401


class ConnectionFake:
    kind = 'connection-test'

    def validate_settings(self, settings):
        if settings:
            raise ValueError('unknown settings')
        return {}

    def check(self, conn, *, timeout=10):
        return sources.CheckResult(True)

    def accounts(self, conn, *, limit=1000, timeout=10):
        return [sources.RemoteAccount('remote-checking', 'Checking', '5678', 'bank', 'USD')]

    def fetch(self, conn, remote_account, since_cursor, **kwargs):
        return first_page()


def first_page():
    return sources.FeedPage((Line(line_id='fitid:connection-1', fitid='connection-1', date='2026-10-10',
        amount=-1234, payee='Connection test', memo='', number='', kind='DEBIT'),), 'next', 'USD')


@pytest.fixture
def provider(monkeypatch):
    fake = ConnectionFake()
    monkeypatch.setitem(sources.SOURCES, fake.kind, fake)
    return fake


def add(client, provider, **kw):
    return run(client, 'bank-feed-connection add', dict(name='Checking feed', kind=provider.kind,
        account='Checking', remote_account='remote-checking', **kw))


def staged(client, connection):
    page = first_page()
    with sqlite3.connect(database_path(client)) as db:
        db.execute('UPDATE bank_feed_connection_accounts SET staged_page=?, staged_fingerprint=? WHERE connection_id=?',
                   (sources.serialize_page(page), sources.fingerprint(page), connection))
    return sources.fingerprint(page)


def test_no_provider_and_secret_references(client, monkeypatch):
    monkeypatch.setattr(sources, 'SOURCES', {})
    out = run(client, 'bank-feed-connection query', {})
    assert out['rows'] == [] and out['kinds_available'] == []
    with pytest.raises(BookflowError) as exc:
        run(client, 'bank-feed-connection add', dict(name='No source', kind='not-wired', account='Checking', remote_account='remote'))
    assert exc.value.code == 'E_VALIDATION' and exc.value.details['available'] == []


def test_first_backfill_preview_requires_exact_confirmation(client, provider):
    added = add(client, provider, secret_ref='keyring:bookflow/bank-test')
    assert not added['enabled'] and not added['backfill_approved'] and added['cursor'] is None
    with pytest.raises(BookflowError) as exc:
        run(client, 'bank-feed-connection enable', {'connection': added['id']})
    assert exc.value.code == 'E_VALIDATION'
    checked = run(client, 'bank-feed-connection check', {'connection': added['id']})
    assert checked['check_requested'] and not checked['enabled']
    fingerprint = staged(client, added['id'])
    preview = run(client, 'bank-feed fetch', {'connection': added['id']}, dry_run=True)
    assert preview['preview']['lines'][0]['fitid'] == 'connection-1'
    assert preview['import_preview']['counts']['new'] == 1
    assert preview['import_preview']['account_check']['account_id'] == added['account_id']
    assert table(client, 'SELECT count(*) FROM bank_lines') == [(0,)]
    with pytest.raises(BookflowError):
        run(client, 'bank-feed fetch', {'connection': added['id'], 'confirm': '0' * 64})
    enabled = run(client, 'bank-feed fetch', {'connection': added['id'], 'confirm': fingerprint})
    assert enabled['enabled'] and enabled['enabled_here'] and enabled['approved_fingerprint'] == fingerprint
    disabled = run(client, 'bank-feed-connection disable', {'connection': added['id']})
    assert not disabled['enabled'] and not disabled['check_requested'] and not disabled['approved_fingerprint']


@pytest.mark.parametrize('secret_ref', ['actual-password', 'env:BANK_PASSWORD'])
def test_secret_value_and_environment_reference_refused(client, provider, secret_ref):
    with pytest.raises(BookflowError) as exc:
        add(client, provider, secret_ref=secret_ref)
    assert exc.value.code == 'E_VALIDATION'
    assert table(client, 'SELECT count(*) FROM bank_feed_connections') == [(0,)]


def test_provider_validation_failure_is_sanitized(client, provider, monkeypatch):
    def bad(settings):
        raise RuntimeError('PRIVATE-provider-credential')
    monkeypatch.setattr(provider, 'validate_settings', bad)
    with pytest.raises(BookflowError) as exc:
        add(client, provider)
    assert exc.value.code == 'E_VALIDATION' and 'PRIVATE' not in str(exc.value)
    monkeypatch.setattr(provider, 'validate_settings', lambda settings: {'x': float('nan')})
    with pytest.raises(BookflowError) as exc:
        add(client, provider)
    assert exc.value.code == 'E_VALIDATION'


def test_backup_restore_disables_every_connection_and_erases_pending_consent(client, provider):
    added = add(client, provider, secret_ref='keyring:bookflow/bank-test')
    fingerprint = staged(client, added['id'])
    run(client, 'bank-feed fetch', {'connection': added['id'], 'confirm': fingerprint})
    with sqlite3.connect(database_path(client)) as db:
        db.execute('UPDATE bank_feed_connection_accounts SET backfill_approved=1, cursor=?', ('prior-cursor',))
    backup = run(client, 'company backup', {})
    restored = client.run('company restore', {'archive': backup['path'], 'as_copy': True, 'name': 'Disabled feeds copy'})
    rows = client.run('bank-feed-connection query', {}, company=restored['company_id'])['rows']
    assert len(rows) == 1
    value = rows[0]
    assert not value['enabled'] and not value['enabled_here'] and not value['check_requested']
    assert not value['backfill_approved'] and value['cursor'] == 'prior-cursor'
    assert value['preview'] is None and value['approved_fingerprint'] is None and value['staged_fingerprint'] is None
    assert value['secret_ref'] == 'keyring:bookflow/bank-test'


def test_agent_connect_requires_explicit_grant_even_with_admin_role(hosted, provider):  # noqa: F811
    from tests.test_people_only import _agent, _grant
    agent, headers = _agent(hosted, role='admin')
    body = dict(name='Agent authorized feed', kind=provider.kind, account='Checking', remote_account='remote')
    refused = hosted.call('bank-feed-connection.add', body, company=hosted.company_id, headers=headers)
    assert refused.status_code == 403 and refused.json()['details']['capability'] == 'bank-feed.connect'
    _grant(hosted, agent, 'admin', grants=['bank-feed.connect'])
    added = hosted.ok('bank-feed-connection.add', body, company=hosted.company_id, headers=headers)
    assert not added['enabled']
    disabled = hosted.ok('bank-feed-connection.disable', {'connection': added['id']}, company=hosted.company_id, headers=headers)
    assert not disabled['enabled']


def test_new_check_invalidates_old_first_page_consent(client, provider):
    added = add(client, provider)
    fingerprint = staged(client, added['id'])
    run(client, 'bank-feed-connection check', {'connection': added['id']})
    with pytest.raises(BookflowError) as exc:
        run(client, 'bank-feed-connection enable', {'connection': added['id'], 'confirm': fingerprint})
    assert exc.value.code == 'E_VALIDATION'
    current = run(client, 'bank-feed fetch', {'connection': added['id']}, dry_run=True)
    assert current['preview'] is None and not current['enabled']


def test_standard_agent_can_stop_feed_without_connect_or_ledger_access(hosted, provider):
    from tests.test_people_only import _agent, _grant, _bearer, REASON
    from tests.test_bank_feed_tick import call, sql
    added = call(hosted, 'bank-feed-connection add', dict(name='Stop safely', kind=provider.kind,
        account='Checking', remote_account='remote-checking'))
    sql(hosted, 'UPDATE bank_feed_connection_accounts SET backfill_approved=1')
    enabled = call(hosted, 'bank-feed-connection enable', {'connection': added['id']})
    assert enabled['enabled']
    agent, headers = _agent(hosted, role='standard')
    _grant(hosted, agent, 'standard', denies=['bank-feed.connect', 'ledger.read'])
    hosted.ok('agent.authorize', {'agent': agent, 'confirm_permitted_use': True, 'acknowledge_fresh_context': True}, headers=REASON)
    headers = {**_bearer(hosted, agent, 'principal-p'), **REASON}
    stopped = hosted.ok('bank-feed-connection.disable', {'connection': added['id']},
                        company=hosted.company_id, headers=headers)
    assert not stopped['enabled'] and not stopped['check_requested']
    for command in ('add', 'enable'):
        body = dict(name='Forbidden', kind=provider.kind, account='Checking', remote_account='remote') if command == 'add' else {'connection': added['id']}
        refused = hosted.call('bank-feed-connection.' + command, body, company=hosted.company_id, headers=headers)
        assert refused.status_code == 403
    refused = hosted.call('bank-feed.fetch', {'connection': added['id'], 'confirm': '0' * 64},
                          company=hosted.company_id, headers=headers)
    assert refused.status_code == 403
    _grant(hosted, agent, 'readonly')
    hosted.ok('agent.authorize', {'agent': agent, 'confirm_permitted_use': True, 'acknowledge_fresh_context': True}, headers=REASON)
    headers = {**_bearer(hosted, agent, 'principal-p'), **REASON}
    refused = hosted.call('bank-feed-connection.disable', {'connection': added['id']},
                          company=hosted.company_id, headers=headers)
    assert refused.status_code == 403


def test_connection_query_redacts_cursor_and_staged_page_below_admin(hosted, provider):
    from tests.test_people_only import _agent, _grant, _bearer, REASON
    from tests.test_bank_feed_tick import call, sql
    added = call(hosted, 'bank-feed-connection add', dict(name='Private preview', kind=provider.kind,
        account='Checking', remote_account='remote-checking'))
    sql(hosted, 'UPDATE bank_feed_connection_accounts SET cursor=?, staged_page=?, staged_fingerprint=?',
        'opaque-cursor', sources.serialize_page(first_page()), sources.fingerprint(first_page()))
    owner = call(hosted, 'bank-feed-connection query', {})['rows'][0]
    assert owner['cursor'] == 'opaque-cursor' and owner['preview']['lines']
    agent, headers = _agent(hosted, role='standard')
    for role in ('standard', 'readonly'):
        _grant(hosted, agent, role)
        hosted.ok('agent.authorize', {'agent': agent, 'confirm_permitted_use': True, 'acknowledge_fresh_context': True}, headers=REASON)
        headers = _bearer(hosted, agent, 'principal-p')
        value = hosted.ok('bank-feed-connection.query', {}, company=hosted.company_id, headers=headers)['rows'][0]
        assert value['cursor'] is None and value['preview'] is None
    _grant(hosted, agent, 'standard')
    hosted.ok('agent.authorize', {'agent': agent, 'confirm_permitted_use': True, 'acknowledge_fresh_context': True}, headers=REASON)
    headers = {**_bearer(hosted, agent, 'principal-p'), **REASON}
    stopped = hosted.ok('bank-feed-connection.disable', {'connection': added['id']},
                        company=hosted.company_id, headers=headers)
    assert stopped['cursor'] is None and stopped['preview'] is None

    # The represented person's role also limits an admin agent's preview.
    from tests.test_automation import _users
    _grant(hosted, agent, 'admin')
    _grant(hosted, _users(hosted)['principal-p'], 'standard')
    hosted.ok('agent.authorize', {'agent': agent, 'confirm_permitted_use': True, 'acknowledge_fresh_context': True}, headers=REASON)
    headers = _bearer(hosted, agent, 'principal-p')
    value = hosted.ok('bank-feed-connection.query', {}, company=hosted.company_id, headers=headers)['rows'][0]
    assert value['cursor'] is None and value['preview'] is None
