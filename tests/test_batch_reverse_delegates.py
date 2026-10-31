"""The shared reversal door keeps native run lifecycle and aggregate inverse grants."""
import pytest

from bookflow import BookflowError
from tests.test_imports import books, company_db, rows_file  # noqa: F401
from tests.test_row3_host import hosted  # noqa: F401


def _snapshot(books):
    with company_db(books) as db:
        return tuple(db.iterdump())


def _bank(books):
    return books.run('bank-feed import', {
        'account': 'Checking', 'content': 'Date,Description,Amount\n2026-10-01,Unknown shop,-10.00\n'},
        reason='Load statement')


def test_generic_bank_import_reverses_inbox_only_and_checks_its_fingerprint(books):
    imported = _bank(books)
    body = {'batch': imported['batch_id']}
    before = _snapshot(books)
    plan = books.run('batch reverse', body, dry_run=True, reason='Wrong statement')
    assert len(plan['steps']) == 1 and plan['steps'][0]['record_type'] == 'bank_feed_import'
    assert _snapshot(books) == before
    with pytest.raises(BookflowError) as stale:
        books.run('batch reverse', dict(body, fingerprint='0' * 64), reason='Wrong statement')
    assert stale.value.code == 'E_VERSION_CONFLICT'
    assert _snapshot(books) == before
    out = books.run('batch reverse', dict(body, fingerprint=plan['fingerprint']), reason='Wrong statement')
    assert out['state'] == 'reversed' and out['remaining'] == 0
    with company_db(books) as db:
        assert db.execute('SELECT live FROM bank_lines').fetchall() == [(0,)]
        assert db.execute('SELECT count(*) FROM transactions').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['bank-import', 'bank-sort'])
def test_generic_bank_inverse_requires_native_write_grant(books, kind):
    from tests.conftest import as_user, make_actor
    imported = _bank(books)
    batch_id = imported['batch_id']
    if kind == 'bank-sort':
        out = books.run('bank-line ignore', {'lines': [row['id'] for row in imported['lines']],
                                           'ignore_reason': 'not_ours'}, reason='Ignore selected lines')
        batch_id = out['batch_id']
    make_actor(books.root, 'reader', company_role=(books.company, 'readonly'))
    reader = as_user(books.root, 'reader')
    # The caller may inspect the batch and the inbox; write authority is distinct.
    reader.run('batch show', {'batch': batch_id}, company=books.company)
    before = _snapshot(books)
    for dry_run in (True, False):
        with pytest.raises(BookflowError) as denied:
            reader.run('batch reverse', {'batch': batch_id}, company=books.company,
                       dry_run=dry_run, reason='Undo statement act')
        assert denied.value.code == 'E_BATCH_BLOCKED'
        assert any(row['code'] == 'E_PERMISSION' for row in denied.value.details['blockers'])
        assert _snapshot(books) == before
    assert books.run('batch reverse', {'batch': batch_id}, reason='Undo statement act')['state'] == 'reversed'
    if kind == 'bank-sort':
        with company_db(books) as db:
            assert db.execute('SELECT state, live FROM bank_lines').fetchall() == [('waiting', 1)]


def test_generic_bank_reversal_disables_live_feed(hosted, monkeypatch):
    from bookflow.delivery import transport
    from tests.test_bank_feed_tick import Fake, approve, call, connection, sql
    monkeypatch.setattr(transport, 'resolve_secret', lambda ref: None)
    conn = connection(hosted, Fake(), monkeypatch)
    approve(hosted, conn)
    hosted.handle.host.schedule_now()
    batch_id = sql(hosted, 'SELECT batch_id FROM bank_feed_imports')[0][0]
    call(hosted, 'batch reverse', {'batch': batch_id}, dry_run=True)
    assert sql(hosted, 'SELECT enabled FROM bank_feed_connections') == [(1,)]
    call(hosted, 'batch reverse', {'batch': batch_id})
    assert sql(hosted, 'SELECT enabled, check_requested FROM bank_feed_connections') == [(0, 0)]
    assert sql(hosted, 'SELECT count(*) FROM bank_lines WHERE live=1') == [(0,)]
    hosted.handle.host.schedule_now()
    assert sql(hosted, 'SELECT count(*) FROM bank_lines WHERE live=1') == [(0,)]


def test_generic_import_reversal_stops_the_unfinished_run(books):
    preview = books.run('import preview', {'kind': 'customer', 'content': rows_file(3)})
    run_id = preview['run']['id']
    applied = books.run('import run', {'run': run_id, 'limit': 1})
    assert applied['run']['state'] == 'running'
    batch_id = applied['run']['batch']['id']
    assert books.run('batch reverse', {'batch': batch_id}, reason='Wrong file')['state'] == 'reversed'
    assert books.run('import show', {'run': run_id})['run']['state'] == 'stopped'
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM customers WHERE active=1').fetchone()[0] == 0


def test_generic_bulk_edit_refuses_an_unfinished_apply(books):
    for name in ('First', 'Second'):
        books.run('customer create', {'name': name})
    preview = books.run('bulk-edit preview', {'kind': 'customer',
                                             'operations': [{'op': 'set', 'field': 'phone', 'value': '555'}]})
    run_id = preview['run']['id']
    applied = books.run('bulk-edit apply', {'run': run_id, 'limit': 1})
    assert applied['run']['state'] == 'applying'
    batch_id = applied['run']['batch']['id']
    before = _snapshot(books)
    with pytest.raises(BookflowError) as denied:
        books.run('batch reverse', {'batch': batch_id}, reason='Wrong edit')
    assert denied.value.code == 'E_BATCH_BLOCKED' and 'still applying' in str(denied.value.details)
    assert _snapshot(books) == before
    books.run('bulk-edit apply', {'run': run_id})
    assert books.run('batch reverse', {'batch': batch_id}, reason='Wrong edit')['state'] == 'reversed'
    assert all(row['phone'] is None for row in books.run('customer query')['items'])


def test_generic_opening_reversal_updates_the_draft_state(books):
    member = next(row for row in books.client.membership.list(company=books.company)['items']
                  if row['scope_type'] == 'company' and row['role'] == 'owner')
    books.client.membership.grant(user=member['user_id'], company=books.company,
                                   expected_version=member['version'], grants=['transaction.journal_entry.delete'])
    draft = books.run('opening-draft create', {'as_of': '2026-09-30',
                                              'balances': {'content': 'Account,Balance\nChecking,100.00\n'}})
    applied = books.run('opening-draft apply', {'draft': draft['id']}, reason='Opening balance')
    batch_id = applied['draft']['batch']
    out = books.run('batch reverse', {'batch': batch_id}, reason='Wrong opening')
    assert out['state'] == 'reversed'
    assert books.run('opening-draft show', {'draft': draft['id']})['state'] == 'reversed'
