"""Many-record journal audits remain complete and atomic on an insert failure."""
from collections import Counter

import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.core.audit import decode_snapshot
from bookflow.storage.engine import open_database
from tests.test_row8_atomicity import state
from tests.test_row8_journal import COMPANY, database_path, journal_accounts, lines  # noqa: F401


def test_twenty_line_audit_retains_every_created_record(client, journal_accounts):
    result = client.journal.post(
        date='2026-01-12', lines=lines(journal_accounts)*10, company=COMPANY)
    with open_database(database_path(client), writable=False) as db:
        event = db.raw.execute('SELECT audit_event_id FROM transaction_revisions WHERE id=?',
                               (result['current_revision_id'],)).fetchone()[0]
        records = db.raw.execute('SELECT record_type,record_id,action,version_before,version_after,"before","after" FROM audit_entries WHERE event_id=?', (event,)).fetchall()
        assert Counter(row[0] for row in records) == {
            'transaction':1, 'transaction_revision':1, 'document_line_identity':20,
            'document_line':20, 'posting_batch':1, 'posting_line':20, 'posting_line_source':20}
        assert len({row[1] for row in records}) == len(records) == 83
        for kind, ident, action, before_version, after_version, before, after in records:
            assert (action, before_version, after_version, before) == ('create', None, 1, None)
            assert decode_snapshot(after)['id'] == ident


def test_mid_audit_entry_failure_rolls_back_every_effect_and_retry(client, journal_accounts):
    with open_database(database_path(client), writable=True) as db:
        db.raw.execute("CREATE TRIGGER fail_ledger_audit BEFORE INSERT ON audit_entries WHEN NEW.record_type='posting_line' BEGIN SELECT RAISE(ABORT,'injected late audit entry'); END")
        db.conn.commit()
    before = state(client)
    try:
        with pytest.raises((BookflowError, sa.exc.IntegrityError)):
            client.journal.post(date='2026-01-12', lines=lines(journal_accounts)*10,
                                company=COMPANY, idempotency_key='late-audit-failure')
        assert state(client) == before
    finally:
        with open_database(database_path(client), writable=True) as db:
            db.raw.execute('DROP TRIGGER fail_ledger_audit')
            db.conn.commit()
    saved = client.journal.post(date='2026-01-12', lines=lines(journal_accounts)*10,
                                company=COMPANY, idempotency_key='late-audit-failure')
    retry = client.journal.post(date='2026-01-12', lines=lines(journal_accounts)*10,
                                company=COMPANY, idempotency_key='late-audit-failure')
    assert saved['id'] == retry['id'] and retry['idempotent_replay']
