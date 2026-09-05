"""Zero-effect failures at journal audit, row and retry persistence boundaries."""
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import schema as c, journals
from bookflow.core import audit, idempotency
from bookflow.storage.engine import open_database
from tests.test_row8_journal import COMPANY, database_path, journal_accounts, lines, post


TABLES = ('transactions', 'transaction_revisions', 'document_line_identities', 'document_lines',
          'posting_batches', 'posting_lines', 'posting_line_sources', 'audit_events', 'audit_entries', 'sequences', 'idempotency_keys', 'principals')


def state(client):
    with open_database(database_path(client), writable=False) as db:
        return {name: list(db.raw.execute('SELECT * FROM "' + name + '" ORDER BY 1'))
                for name in TABLES if name in c.metadata.tables}


def test_closed_old_and_new_dates_have_zero_effects(client, journal_accounts):
    first = post(client, journal_accounts)
    client.company.update(closing_date='2026-01-31', company=COMPANY)
    before = state(client)
    for invoke in (
        lambda: post(client, journal_accounts),
        lambda: client.journal.update(journal=first['id'], date='2026-02-15', expected_version=1, company=COMPANY),
        lambda: client.journal.void(journal=first['id'], expected_version=1, reason='Closed correction', company=COMPANY),
    ):
        with pytest.raises(BookflowError) as err:
            invoke()
        assert err.value.code == 'E_PERIOD_CLOSED'
        assert state(client) == before


@pytest.mark.parametrize('failure', ['audit', 'row', 'idempotency'])
def test_post_rollback_injection(client, journal_accounts, monkeypatch, failure):
    before = state(client)
    with monkeypatch.context() as patch:
        if failure == 'audit':
            original = audit.write_event_to
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError('injected audit failure')
            patch.setattr(audit, 'write_event_to', fail)
        elif failure == 'idempotency':
            original = idempotency.store
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError('injected retry failure')
            patch.setattr(idempotency, 'store', fail)
        else:
            from sqlalchemy.engine import Connection
            original = Connection.execute
            def fail(self, statement, *args, **kwargs):
                result = original(self, statement, *args, **kwargs)
                if getattr(getattr(statement, 'table', None), 'name', None) == 'posting_line_sources' and getattr(statement, 'is_insert', False):
                    raise RuntimeError('injected source failure')
                return result
            patch.setattr(Connection, 'execute', fail)
        with pytest.raises((RuntimeError, BookflowError)):
            post(client, journal_accounts, idempotency_key='rollback-post')
    assert state(client) == before
    first = post(client, journal_accounts, idempotency_key='rollback-post')
    assert first['version'] == 1


def test_apply_rechecks_period_after_preview(client, journal_accounts, monkeypatch):
    before = state(client)
    original = journals.prepare
    calls = 0
    def prepare(s, ctx, inp, operation):
        nonlocal calls
        calls += 1
        if calls == 2:
            s.company.conn.execute(c.company_info.update().values(closing_date='2026-01-31'))
        return original(s, ctx, inp, operation)
    monkeypatch.setattr(journals, 'prepare', prepare)
    with pytest.raises(BookflowError) as err:
        post(client, journal_accounts)
    assert err.value.code == 'E_PERIOD_CLOSED'
    assert state(client) == before


@pytest.mark.parametrize('operation', ['update', 'void'])
def test_correction_and_void_roll_back_after_sources(client, journal_accounts, monkeypatch, operation):
    first = post(client, journal_accounts)
    before = state(client)
    from sqlalchemy.engine import Connection
    original = Connection.execute
    def fail(self, statement, *args, **kwargs):
        result = original(self, statement, *args, **kwargs)
        if getattr(getattr(statement, 'table', None), 'name', None) == 'posting_line_sources' and getattr(statement, 'is_insert', False):
            raise RuntimeError('injected correction source failure')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(Connection, 'execute', fail)
        with pytest.raises((RuntimeError, BookflowError)):
            if operation == 'update':
                client.journal.update(journal=first['id'], date='2026-03-01', expected_version=1, company=COMPANY)
            else:
                client.journal.void(journal=first['id'], reason='Rollback witness', expected_version=1, company=COMPANY)
    assert state(client) == before


def test_preview_and_noop_have_zero_ledger_effects(client, journal_accounts):
    before = state(client)
    preview = post(client, journal_accounts, dry_run=True)
    assert preview['dry_run'] and state(client) == before
    first = post(client, journal_accounts)
    before = state(client)
    noop = client.journal.update(journal=first['id'], expected_version=1, company=COMPANY)
    assert not noop['changed'] and state(client) == before


@pytest.mark.parametrize('corruption', ['unbalanced', 'two_sides', 'zero', 'currency', 'source_total', 'source_owner', 'duplicate_source'])
def test_independent_validator_rejects_corrupted_generated_post_before_audit(client, journal_accounts, monkeypatch, corruption):
    before = state(client)
    prepare = journals.prepare
    audited = []
    original_audit = audit.write_event_to

    def track_audit(*args, **kwargs):
        audited.append(True)
        return original_audit(*args, **kwargs)

    def corrupt(s, ctx, inp, operation):
        plan = prepare(s, ctx, inp, operation)
        if s.company.write_transaction:
            pending = plan.data['pending']
            leg, source = pending['posting_lines'][0], pending['posting_line_sources'][0]
            if corruption == 'unbalanced':
                leg['debit_minor_units'] += 1
                source['amount_minor_units'] += 1
            elif corruption == 'two_sides':
                leg['credit_minor_units'] = 1
            elif corruption == 'zero':
                leg['debit_minor_units'] = 0
            elif corruption == 'currency':
                leg['currency'] = 'JPY'
            elif corruption == 'source_total':
                source['amount_minor_units'] -= 1
            elif corruption == 'source_owner':
                source['document_line_id'] = pending['document_lines'][1]['id']
            else:
                pending['posting_line_sources'].append(dict(source))
        return plan

    with monkeypatch.context() as patch:
        patch.setattr(journals, 'prepare', corrupt)
        patch.setattr(audit, 'write_event_to', track_audit)
        with pytest.raises(BookflowError) as err:
            post(client, journal_accounts)
        assert err.value.code in ('E_INTERNAL', 'E_UNBALANCED_ENTRY')
    assert not audited and state(client) == before


@pytest.mark.parametrize('corruption', ['dimension', 'date', 'line_link', 'source_link'])
def test_independent_validator_rejects_inexact_reversal(client, journal_accounts, monkeypatch, corruption):
    first = post(client, journal_accounts)
    before = state(client)
    prepare = journals.prepare
    audited = []
    original_audit = audit.write_event_to

    def track_audit(*args, **kwargs):
        audited.append(True)
        return original_audit(*args, **kwargs)

    def corrupt(s, ctx, inp, operation):
        plan = prepare(s, ctx, inp, operation)
        if s.company.write_transaction:
            pending = plan.data['pending']
            if corruption == 'dimension':
                pending['posting_lines'][0]['description'] = 'Recomputed instead of copied'
            elif corruption == 'date':
                pending['posting_batches'][0]['effective_date'] = '2026-02-01'
            elif corruption == 'line_link':
                pending['posting_lines'][0]['reversed_line_id'] = pending['posting_lines'][1]['reversed_line_id']
            else:
                pending['posting_line_sources'][0]['reversed_source_id'] = pending['posting_line_sources'][1]['reversed_source_id']
        return plan

    with monkeypatch.context() as patch:
        patch.setattr(journals, 'prepare', corrupt)
        patch.setattr(audit, 'write_event_to', track_audit)
        with pytest.raises(BookflowError) as err:
            client.journal.void(journal=first['id'], expected_version=1, reason='Exact inverse witness', company=COMPANY)
        assert err.value.code == 'E_INTERNAL'
    assert not audited and state(client) == before


def test_corrupt_stored_source_totals_cannot_be_reversed(client, journal_accounts, monkeypatch):
    first = post(client, journal_accounts)
    before = state(client)
    original = journals.rows
    def corrupt_source_read(s, table, *where, **kwargs):
        found = original(s, table, *where, **kwargs)
        if table is c.posting_line_sources:
            return [dict(row, amount_minor_units=row['amount_minor_units'] + 1) for row in found]
        return found
    with monkeypatch.context() as patch:
        patch.setattr(journals, 'rows', corrupt_source_read)
        with pytest.raises(BookflowError) as err:
            client.journal.void(journal=first['id'], expected_version=1, reason='Corruption witness', company=COMPANY)
        assert err.value.code == 'E_INTERNAL'
    assert state(client) == before
