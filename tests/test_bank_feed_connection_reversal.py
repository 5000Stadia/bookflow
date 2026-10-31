"""Feed corrections stop their source and explain strict import ordering."""
from dataclasses import replace

import pytest

from tests.test_row3_host import hosted  # noqa: F401
from tests.test_bank_feed_tick import Fake, approve, call, connection, sql


@pytest.mark.parametrize('move', [False, True])
def test_feed_correction_stops_replay_and_audits_reason(hosted, monkeypatch, move):
    from bookflow.delivery import transport
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    approve(hosted, conn)
    hosted.handle.host.schedule_now()
    imp = sql(hosted, 'SELECT id, connection_id FROM bank_feed_imports')[0]
    assert imp[1] == conn['id']
    args = {'import_id': imp[0]}
    if move:
        args['to'] = 'Payment Example Bank'
    command = 'bank-feed move' if move else 'bank-feed reverse'
    call(hosted, command, args, dry_run=True)
    assert sql(hosted, 'SELECT enabled FROM bank_feed_connections') == [(1,)]
    call(hosted, command, args)
    assert sql(hosted, 'SELECT enabled, check_requested FROM bank_feed_connections') == [(0, 0)]
    if move:
        assert sql(hosted, 'SELECT connection_id FROM bank_feed_imports WHERE moved_from=?', imp[0]) == [(conn['id'],)]
    before = sql(hosted, 'SELECT id FROM bank_lines WHERE live=1')
    assert len(before) == (1 if move else 0)
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT id FROM bank_lines WHERE live=1') == before
    assert sql(hosted, "SELECT reason FROM audit_events WHERE command='bank-feed-connection disable'") == [('Live feed test',)]


def test_later_page_refusal_names_disable_and_newest_can_reverse(hosted, monkeypatch):
    from bookflow.delivery import transport
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    fake = Fake()
    conn = connection(hosted, fake, monkeypatch)
    approve(hosted, conn)
    hosted.handle.host.schedule_now()
    first = sql(hosted, 'SELECT id FROM bank_feed_imports')[0][0]
    fake.page = replace(fake.page, lines=(replace(fake.page.lines[0], line_id='fitid:two', fitid='two'),),
                        next_cursor='two')
    hosted.handle.host.schedule_now()
    latest = sql(hosted, 'SELECT id FROM bank_feed_imports WHERE id != ?', first)[0][0]
    refused = hosted.call('bank-feed.reverse', {'import_id': first}, company=hosted.company_id,
                          headers={'X-Bookflow-Reason': 'Live feed test'})
    body = refused.json()
    assert body['code'] == 'E_BATCH_BLOCKED', body
    blocker = next(b for b in body['details']['blockers'] if b['code'] == 'later_import')
    assert f'bank-feed-connection disable --connection {conn["id"]}' in blocker['clears']
    assert sql(hosted, 'SELECT enabled FROM bank_feed_connections') == [(1,)]
    call(hosted, 'bank-feed reverse', {'import_id': latest})
    assert sql(hosted, 'SELECT enabled FROM bank_feed_connections') == [(0,)]
    call(hosted, 'bank-feed reverse', {'import_id': first})
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT count(*) FROM bank_lines WHERE live=1') == [(0,)]
