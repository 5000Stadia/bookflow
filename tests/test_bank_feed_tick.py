"""Real host boundaries for the deliberately empty live-feed provider seam."""
import threading
from pathlib import Path
import sqlite3

import pytest

from tests.test_row3_host import hosted  # noqa: F401
from bookflow.company import bank_sources as sources
from bookflow.company.statement_files import Line


class Fake:
    kind = 'test-bank'

    def __init__(self):
        self.calls = []
        self.failure = None
        self.entered = None
        self.release = None
        self.page = sources.FeedPage((Line(line_id='fitid:live-one', date='2026-10-14', amount=-4317,
            payee='LIVE SHOP', memo='', number='', fitid='live-one', kind='OTHER'),), 'page-one', 'USD')

    def validate_settings(self, value):
        if value:
            raise ValueError()
        return {}

    def check(self, conn, *, timeout):
        self.calls.append(('check', threading.current_thread().name))
        return sources.CheckResult(True)

    def accounts(self, conn, *, limit, timeout):
        self.calls.append(('accounts', threading.current_thread().name))
        return [sources.RemoteAccount('remote-checking', 'Checking', '5678', 'bank', 'USD')]

    def fetch(self, conn, account, cursor, *, limit, timeout):
        self.calls.append(('fetch', threading.current_thread().name, cursor, conn.secret, limit, timeout))
        if self.entered:
            self.entered.set()
            assert self.release.wait(10)
        if self.failure:
            raise self.failure
        return self.page


def call(h, name, data=None, *, dry_run=False):
    from bookflow.core.registry import get
    headers = {'X-Bookflow-Reason': 'Live feed test'} if get(name).is_write else {}
    return h.ok(name.replace(' ', '.') + ('?dry_run=true' if dry_run else ''), data or {},
                company=h.company_id, headers=headers)


def sql(h, query, *args):
    path = Path(call(h, 'company show')['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        return db.execute(query, args).fetchall()


def connection(h, fake, monkeypatch):
    monkeypatch.setitem(sources.SOURCES, fake.kind, fake)
    return call(h, 'bank-feed-connection add', dict(name='Live Checking', kind=fake.kind,
                account='Checking', remote_account='remote-checking', secret_ref='keyring:bookflow/test'))


def approve(h, conn):
    call(h, 'bank-feed-connection check', {'connection': conn['id']})
    h.handle.host.schedule_now()
    preview = call(h, 'bank-feed fetch', {'connection': conn['id']}, dry_run=True)
    assert preview['staged_fingerprint'] and preview['preview']['lines']
    assert sql(h, 'SELECT count(*) FROM bank_feed_imports') == [(0,)]
    call(h, 'bank-feed fetch', {'connection': conn['id'], 'confirm': preview['staged_fingerprint']})
    return preview


def test_host_no_connection_never_reads_secret_or_calls_provider(hosted, monkeypatch):
    from bookflow.delivery import transport
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: pytest.fail('no connection must read no secret'))
    fake = Fake()
    monkeypatch.setitem(sources.SOURCES, fake.kind, fake)
    hosted.handle.host.schedule_now()
    assert fake.calls == []


def test_first_page_preview_keyring_off_writer_replay_and_one_step_reverse(hosted, monkeypatch):
    from bookflow.delivery import transport
    reads = []
    def secret(ref):
        reads.append((ref, threading.current_thread().name))
        return 'memory-only-credential'
    monkeypatch.setattr(transport, 'resolve_secret', secret)
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    assert fake.calls == []
    approve(hosted, conn)
    hosted.handle.host.schedule_now()
    imports = sql(hosted, 'SELECT id, format FROM bank_feed_imports')
    assert len(imports) == 1 and imports[0][1] == 'feed'
    assert sql(hosted, 'SELECT cursor FROM bank_feed_connection_accounts') == [('page-one',)]
    hosted.handle.host.schedule_now()  # Same page: no new import, batch or line.
    assert sql(hosted, 'SELECT id, format FROM bank_feed_imports') == imports
    assert sql(hosted, 'SELECT count(*) FROM bank_lines WHERE live=1') == [(1,)]
    assert all(row[1] == 'bookflow-bank-feeds' for row in fake.calls)
    assert reads and all(name == 'bookflow-bank-feeds' for _, name in reads)
    call(hosted, 'bank-feed reverse', {'import_id': imports[0][0]})
    assert sql(hosted, 'SELECT count(*) FROM bank_lines WHERE live=1') == [(0,)]
    path = Path(call(hosted, 'company show')['path'])
    assert all(b'memory-only-credential' not in p.read_bytes() for p in path.rglob('*') if p.is_file())


def test_disabled_during_fetch_discards_late_page_and_writer_is_free(hosted, monkeypatch):
    from bookflow.delivery import transport
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    approve(hosted, conn)
    hosted.handle.host.schedule_now()
    fake.page = sources.FeedPage((Line(line_id='fitid:late', fitid='late', date='2026-10-15', amount=777,
                                      payee='LATE', memo='', number='', kind='OTHER'),), 'late', 'USD')
    fake.entered, fake.release = threading.Event(), threading.Event()
    hosted.handle.host.schedule_now(wait=False)
    assert fake.entered.wait(10)
    call(hosted, 'bank-feed-connection disable', {'connection': conn['id']})
    fake.release.set()
    hosted.handle.host._feed_thread.join(10)
    assert sql(hosted, 'SELECT cursor FROM bank_feed_connection_accounts') == [('page-one',)]
    assert sql(hosted, "SELECT count(*) FROM bank_lines WHERE fitid='late'") == [(0,)]


def test_permanent_failure_disables_without_recording_provider_text(hosted, monkeypatch):
    from bookflow.delivery import transport
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    approve(hosted, conn)
    hosted.handle.host.schedule_now()
    fake.failure = sources.FeedFailure(permanent=True)
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT enabled, last_check_ok FROM bank_feed_connections') == [(0, 0)]
    assert sql(hosted, 'SELECT cursor FROM bank_feed_connection_accounts') == [('page-one',)]


def test_revoking_connection_authority_prevents_calls(hosted, monkeypatch):
    from bookflow.delivery import transport
    from tests.test_automation import _grant, _users
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    approve(hosted, conn)
    hosted.handle.host.schedule_now()
    before = list(fake.calls)
    _grant(hosted, _users(hosted)[hosted.login], 'owner', denies=['bank-feed.connect'])
    hosted.handle.host.schedule_now()
    assert fake.calls == before
    assert sql(hosted, 'SELECT cursor FROM bank_feed_connection_accounts') == [('page-one',)]


def test_fetched_rules_resume_after_slice_and_reverse_together(hosted, monkeypatch):
    from bookflow.delivery import transport
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    fake.page = sources.FeedPage(tuple(Line(line_id=f'fitid:rule-{i}', fitid=f'rule-{i}',
        date='2026-10-14', amount=-4317-i, payee='LIVE SHOP', memo='', number='', kind='OTHER')
        for i in range(27)), 'rules', 'USD')
    made = call(hosted, 'bank-rule create', dict(name='Feed ignore', account='Checking',
        conditions=[{'field': 'payee', 'op': 'equals', 'value': 'LIVE SHOP'}], action='ignore',
        ignore_reason='not_ours'))['rule']
    checked = call(hosted, 'bank-rule update', {'rule': made['id'], 'live': True}, dry_run=True)
    call(hosted, 'bank-rule update', {'rule': made['id'], 'live': True, 'confirm': checked['check']['fingerprint']})
    conn = connection(hosted, fake, monkeypatch)
    approve(hosted, conn)
    hosted.handle.host.schedule_now()
    assert sql(hosted, "SELECT count(*) FROM bank_lines WHERE state='ignored'") == [(25,)]
    assert sql(hosted, "SELECT count(*) FROM bank_lines WHERE json_extract(proposal,'$.rule.pending') IS NOT NULL") == [(2,)]
    hosted.handle.host.schedule_now()
    assert sql(hosted, "SELECT count(*) FROM bank_lines WHERE state='ignored'") == [(27,)]
    released = sql(hosted, 'SELECT id FROM bank_lines ORDER BY id LIMIT 1')[0][0]
    call(hosted, 'bank-line unmatch', {'line': released})
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT state FROM bank_lines WHERE id=?', released) == [('waiting',)]
    imported = sql(hosted, 'SELECT id FROM bank_feed_imports')[0][0]
    call(hosted, 'bank-feed reverse', {'import_id': imported})
    assert sql(hosted, 'SELECT count(*) FROM bank_lines WHERE live=1') == [(0,)]


def test_fetched_rule_uses_clerk_authority_without_upgrading_older_file(hosted, monkeypatch):
    from bookflow.delivery import transport
    from tests.test_automation import _clerk, _grant, _users
    from tests.test_bank_feeds import csv_file
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    clerk = _clerk(hosted, 'feed-clerk')
    made = call(hosted, 'bank-rule create', dict(name='Fetched supplies', account='Checking',
        conditions=[{'field': 'payee', 'op': 'equals', 'value': 'LIVE SHOP'}],
        payee='Central Supply', category='Office Supplies', auto_add=True))['rule']
    def clerk_call(name, body, dry_run=False):
        return hosted.ok(name + ('?dry_run=true' if dry_run else ''), body,
                         company=hosted.company_id, headers=clerk)
    preview = clerk_call('bank-rule.update', {'rule': made['id'], 'live': True}, True)
    clerk_call('bank-rule.update', {'rule': made['id'], 'live': True,
                                   'confirm': preview['check']['fingerprint']})
    users = _users(hosted)
    _grant(hosted, users[hosted.login], 'owner', denies=['ledger.post'])
    older = call(hosted, 'bank-feed import', dict(account='Checking', content=csv_file([
        ('2026-09-30', 'LIVE SHOP', '-12.11')])))
    assert older['lines'][0]['state'] == 'waiting'
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    call(hosted, 'bank-feed-connection check', {'connection': conn['id']})
    hosted.handle.host.schedule_now()
    preview = call(hosted, 'bank-feed fetch', {'connection': conn['id']}, dry_run=True)
    call(hosted, 'bank-feed fetch', {'connection': conn['id'], 'confirm': preview['staged_fingerprint']})
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT state FROM bank_lines WHERE id=?', older['lines'][0]['id']) == [('waiting',)]
    assert sql(hosted, "SELECT state FROM bank_lines WHERE fitid='live-one'") == [('added',)]
    assert sql(hosted, "SELECT created_by FROM transactions WHERE id=(SELECT entry_id FROM bank_lines WHERE fitid='live-one')") == [(users['feed-clerk'],)]
    event = sql(hosted, "SELECT actor_id, interface FROM audit_events WHERE command='bank-feed import' ORDER BY seq DESC LIMIT 1")
    assert event == [(users[hosted.login], 'system')]


def test_agent_established_feed_keeps_principal_in_import_and_audit(hosted, monkeypatch):
    from bookflow.delivery import transport
    from tests.test_people_only import _agent, _grant
    from tests.test_automation import _users
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    monkeypatch.setitem(sources.SOURCES, fake.kind, fake)
    agent, headers = _agent(hosted, role='admin')
    _grant(hosted, agent, 'admin', grants=['bank-feed.connect'])
    def agent_call(name, body, dry_run=False):
        return hosted.ok(name + ('?dry_run=true' if dry_run else ''), body,
                         company=hosted.company_id, headers=headers)
    conn = agent_call('bank-feed-connection.add', dict(name='Agent fetch', kind=fake.kind,
                      account='Checking', remote_account='remote-checking'))
    agent_call('bank-feed-connection.check', {'connection': conn['id']})
    hosted.handle.host.schedule_now()
    preview = agent_call('bank-feed.fetch', {'connection': conn['id']}, True)
    agent_call('bank-feed.fetch', {'connection': conn['id'], 'confirm': preview['staged_fingerprint']})
    hosted.handle.host.schedule_now()
    principal = _users(hosted)['principal-p']
    assert sql(hosted, 'SELECT created_by, on_behalf_of, created_via FROM bank_feed_imports') == [
        (agent, principal, 'system')]
    assert sql(hosted, "SELECT actor_id, actor_kind, on_behalf_of, interface FROM audit_events "
                      "WHERE command='bank-feed import' ORDER BY seq DESC LIMIT 1") == [
        (agent, 'agent', principal, 'system')]


def test_company_detached_during_fetch_discards_late_page(hosted, monkeypatch):
    from bookflow.delivery import transport
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    approve(hosted, conn)
    hosted.handle.host.schedule_now()
    path = Path(call(hosted, 'company show')['path']) / 'company.db'
    fake.page = sources.FeedPage((Line(line_id='fitid:detached-late', fitid='detached-late',
        date='2026-10-15', amount=777, payee='LATE', memo='', number='', kind='OTHER'),), 'detached-late', 'USD')
    fake.entered, fake.release = threading.Event(), threading.Event()
    hosted.handle.host.schedule_now(wait=False)
    assert fake.entered.wait(10)
    try:
        hosted.ok('company.detach', {'company': hosted.company_id})
    finally:
        fake.release.set()
    hosted.handle.host._feed_thread.join(10)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT cursor FROM bank_feed_connection_accounts').fetchall() == [('page-one',)]
        assert db.execute("SELECT count(*) FROM bank_lines WHERE fitid='detached-late'").fetchone() == (0,)


def test_empty_page_keeps_remote_identity_and_mismatched_currency_fails_closed(monkeypatch):
    from bookflow.company import bank_feed_tick
    fake = Fake()
    monkeypatch.setitem(sources.SOURCES, fake.kind, fake)
    snapshot = dict(enabled=False, staged_page=None, kind=fake.kind, settings='{}', secret_ref=None,
                    check_requested=True, backfill_approved=False, remote_account='remote-checking', cursor=None)
    fake.page = sources.FeedPage((), 'empty', 'USD')
    result = bank_feed_tick.fetch(snapshot)
    assert result['page'].lines == () and result['page'].account_last4 == '5678'
    fake.page = sources.FeedPage((), 'empty', 'EUR')
    assert bank_feed_tick.fetch(snapshot) == {'failed': True, 'permanent': True}


def test_page_import_failure_rolls_back_lines_and_cursor_together(hosted, monkeypatch):
    from bookflow.delivery import transport
    from bookflow.company import bank_feeds
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    approve(hosted, conn)
    original = bank_feeds.write
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('simulated failure after import writes')
    monkeypatch.setattr(bank_feeds, 'write', interrupted)
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT count(*) FROM bank_lines') == [(0,)]
    assert sql(hosted, 'SELECT count(*) FROM bank_feed_imports') == [(0,)]
    assert sql(hosted, 'SELECT cursor FROM bank_feed_connection_accounts') == [(None,)]
    monkeypatch.setattr(bank_feeds, 'write', original)
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT cursor FROM bank_feed_connection_accounts') == [('page-one',)]
