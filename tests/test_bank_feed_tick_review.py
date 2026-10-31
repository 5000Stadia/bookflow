"""Regression coverage for increment 8 feed retry and authority boundaries."""
from types import SimpleNamespace

import pytest

from bookflow.company import bank_feed_tick, bank_rules, bank_sources as sources
from bookflow.core.errors import BookflowError
from tests.test_bank_feed_tick import Fake, approve, connection, sql
from tests.test_row3_host import hosted  # noqa: F401


def test_temporary_preparation_validation_retries_approved_page(hosted, monkeypatch):
    from bookflow.company import bank_feeds
    from bookflow.delivery import transport
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    approve(hosted, conn)
    prepare = bank_feeds.prepare_page
    def frozen(*args, **kwargs):
        raise BookflowError('E_VALIDATION', details={'frozen_by': 'reversal-in-progress'})
    monkeypatch.setattr(bank_feeds, 'prepare_page', frozen)
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT enabled FROM bank_feed_connections') == [(1,)]
    assert sql(hosted, 'SELECT cursor FROM bank_feed_connection_accounts') == [(None,)]
    assert sql(hosted, 'SELECT count(*) FROM bank_lines') == [(0,)]
    monkeypatch.setattr(bank_feeds, 'prepare_page', prepare)
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT cursor FROM bank_feed_connection_accounts') == [('page-one',)]
    assert sql(hosted, 'SELECT count(*) FROM bank_lines WHERE live=1') == [(1,)]


@pytest.mark.parametrize('secret_ref', ['env:BANK_PASSWORD', 'plain-secret', 'keyring:bad', '', 123])
@pytest.mark.parametrize('enabled', [False, True])
def test_tick_refuses_invalid_reference_before_secret_resolution(monkeypatch, secret_ref, enabled):
    from bookflow.delivery import transport
    reads = []
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: reads.append(ref))
    fake = Fake()
    monkeypatch.setitem(sources.SOURCES, fake.kind, fake)
    snapshot = dict(enabled=enabled, staged_page=None, kind=fake.kind, settings='{}',
                    secret_ref=secret_ref, check_requested=True, backfill_approved=False,
                    remote_account='remote-checking', cursor=None)
    assert bank_feed_tick.fetch(snapshot) == {'failed': True, 'permanent': True}
    assert reads == [] and fake.calls == []


def test_local_resume_preserves_fetched_pending_marker(monkeypatch):
    from bookflow.company import bank_sort
    pending = {'reason': 'fetched bank page waits for its live rule'}
    line = dict(id='line', possible_duplicate_of=None, proposal={'rule': {'pending': pending}})
    session = SimpleNamespace(actor=SimpleNamespace(id='local-user'))
    row = dict(id='rule', name='Rule', established_by='another-user')
    monkeypatch.setattr(bank_rules, 'acting', lambda row: True)
    monkeypatch.setattr(bank_rules, 'automation_on', lambda s: True)
    monkeypatch.setattr(bank_rules.automation, 'firing_check', lambda *a: None)
    monkeypatch.setattr(bank_rules.automation, 'name_of', lambda *a: 'Another user')
    monkeypatch.setattr(bank_rules, 'KIND', SimpleNamespace(view=lambda *a: row))
    monkeypatch.setattr(bank_sort, '_pairing', lambda *a: ([], None))
    monkeypatch.setattr(bank_rules, '_on_writer', lambda: False)
    def mark(s, ids, value):
        if line['id'] in ids:
            line['proposal']['rule']['pending'] = value
    monkeypatch.setattr(bank_rules, '_mark', mark)
    result = bank_rules._result(row)
    assert bank_rules._act(session, row, [line], importer=None, fetched=True,
                           budget=25, result=result) == 25
    assert result['outcome'] == 'skipped'
    assert line['proposal']['rule']['pending'] == pending
    assert 'importer' not in line['proposal']['rule']['pending']
