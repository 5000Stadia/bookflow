"""Validated exact reads survive unavailable auxiliary inspection history."""
from contextlib import contextmanager
import copy
import sqlite3

import pytest

from bookflow.company import deposit_queries as q, deposit_read_models as m
from bookflow.company import deposit_read_authority as authority, deposit_dependency_history as history
from bookflow.core import audit
from bookflow.core.errors import BookflowError
from bookflow.core.publication import OSBinding
from tests.test_deposit_dependency_binding import observe
from tests.test_deposit_lifecycle import additional_document, driver
from tests.test_service_sales_lifecycle import sale
from tests.test_row8_journal import database_path


@pytest.fixture
def posted(client, sale, driver):
    document = additional_document(client, sale)
    return driver.run('post', dict(operation_key='inspection-A', document=document)), document


@contextmanager
def fault_storage(path, *tables):
    """Fault only the copied fixture; restore every guard before reader entry."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        original = [tuple(r) for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")]
        guards = [r for r in db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'") if r['tbl_name'] in tables]
        db.execute('BEGIN IMMEDIATE')
        for r in guards:
            db.execute('DROP TRIGGER "' + r['name'].replace('"', '""') + '"')
        yield db
        for r in guards:
            db.execute(r['sql'])
        assert [tuple(r) for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")] == original
        db.commit()
    finally:
        db.close()


def financial_show(value):
    result = copy.deepcopy(value.model_dump(mode='json'))
    result.pop('current_observed_at')
    result['dependencies'].pop('guard')
    result['dependencies'].pop('history')
    return result


def test_disjoint_deposit_history_failure_preserves_exact_reads(client, posted, driver, monkeypatch):
    a, document = posted
    b = driver.run('post', dict(operation_key='inspection-B', document=document))
    path = database_path(client)
    binding_seen = []

    def read(s, stage):
        binding = OSBinding.from_session(s)
        binding_seen.append(binding)
        assert all(value == binding for value in binding_seen)
        assert not s.company.writable and not s.hub.writable
        before = (s.company.raw.total_changes, s.hub.raw.total_changes)
        if stage == 'clean':
            ae = authority.admit(s, [a.current.id], binding=binding)
            be = authority.admit(s, [b.current.id], binding=binding)
            for field in ('roots', 'events', 'drafts'):
                assert not set(getattr(ae, field)) & set(getattr(be, field))
        show = q.show(s, m.ShowInput(deposit=a.current.id), binding=binding)
        items = {kind: q.items(s, m.ItemsInput(deposit=a.current.id, kind=kind), binding=binding).model_dump(mode='json', exclude={'current_observed_at'})
                 for kind in ('sources', 'additional', 'cash_allocations')}
        if stage == 'clean':
            page = q.query(s, m.QueryInput(), binding=binding)
            assert page.total_count == 2 and page.totals.bank_total.minor_units == 2000
        else:
            for query in (m.QueryInput(), m.QueryInput(number=a.current.number)):
                with pytest.raises(BookflowError) as error:
                    q.query(s, query, binding=binding)
                assert error.value.code == 'E_DEPOSIT_SOURCE_INVALID' and error.value.details == {}
        assert before == (s.company.raw.total_changes, s.hub.raw.total_changes)
        return show, items

    clean, items = observe(client, monkeypatch, lambda s: read(s, 'clean'))
    assert clean.dependencies.history == 'complete' and clean.dependencies.guard
    with fault_storage(path, 'deposit_cash_cells') as db:
        cell = dict(db.execute('SELECT * FROM deposit_cash_cells WHERE transaction_id=?', (b.current.id,)).fetchone())
        entry = dict(db.execute('SELECT a.* FROM audit_entries a JOIN audit_events e ON e.id=a.event_id '
                               'WHERE a.record_type=? AND a.record_id=? ORDER BY e.seq DESC,a.id DESC LIMIT 1',
                               ('deposit_cash_cell', cell['id'])).fetchone())
        assert history._audit_image(entry['after'])['transaction_id'] == b.current.id
        assert db.execute('DELETE FROM deposit_cash_cells WHERE id=?', (cell['id'],)).rowcount == 1
    valid, valid_items = observe(client, monkeypatch, lambda s: read(s, 'missing-valid'))
    assert valid.dependencies == clean.dependencies
    assert financial_show(valid) == financial_show(clean) and valid_items == items
    with fault_storage(path, 'audit_entries') as db:
        assert db.execute('SELECT "after" FROM audit_entries WHERE id=?', (entry['id'],)).fetchone()[0] == entry['after']
        assert db.execute('UPDATE audit_entries SET "after"=? WHERE id=?', (audit.RAW + b'{', entry['id'])).rowcount == 1

    def verify_cause(s):
        # Pin the actual cross-owner recovery path, independently of the fallback.
        with pytest.raises(history.MissingHistory, match='malformed audit payload') as error:
            history.History(s).find_owners('deposit_cash_cell', 'transaction_id', (a.current.id,))
        assert error.value.__cause__ is not None
        return read(s, 'missing-malformed')

    degraded, degraded_items = observe(client, monkeypatch, verify_cause)
    assert degraded.dependencies.history == 'unknown_history' and degraded.dependencies.guard is None
    assert financial_show(degraded) == financial_show(clean) and degraded_items == items


class MissingHistorySubtype(history.MissingHistory):
    pass


@pytest.mark.parametrize('error', [history.MissingHistory(message) for message in (
    'invalid audit encoding', 'malformed audit payload', 'audit image is not an object',
    'missing owned identity', 'foreign financial snapshot')]
    + [MissingHistorySubtype('owned subtype')], ids=['encoding', 'payload', 'nonobject', 'missing', 'foreign', 'subtype'])
def test_capture_missing_history_never_issues_guard(client, posted, monkeypatch, error):
    a, _ = posted
    def read(s):
        binding = OSBinding.from_session(s)
        clean = q.show(s, m.ShowInput(deposit=a.current.id), binding=binding)
        calls = []
        def capture(*args):
            calls.append(True)
            raise error
        def issue(*args):
            pytest.fail('capture failure must not issue an edit guard')
        with monkeypatch.context() as patch:
            patch.setattr(history, 'capture', capture)
            patch.setattr(history, 'issue', issue)
            result = q.show(s, m.ShowInput(deposit=a.current.id), binding=binding)
        assert calls == [True]
        assert result.dependencies.guard is None and result.dependencies.history == 'unknown_history'
        assert financial_show(result) == financial_show(clean)
    observe(client, monkeypatch, read)


@pytest.mark.parametrize('fault', ['financial_json', 'foreign_audit_owner'])
def test_own_financial_damage_rejected_before_capture(client, posted, monkeypatch, fault):
    a, _ = posted
    with fault_storage(database_path(client), 'deposit_profiles', 'audit_entries') as db:
        if fault == 'financial_json':
            assert db.execute('UPDATE deposit_profiles SET facts_snapshot=? WHERE transaction_id=?', ('{"invalid":true}', a.current.id)).rowcount == 1
        else:
            entry = db.execute("SELECT * FROM audit_entries WHERE record_type='deposit_profile' AND record_id=?", (a.current.revision_id,)).fetchone()
            image = audit.decode_snapshot(entry['after'])
            image['transaction_id'] = 'foreign'
            db.execute('UPDATE audit_entries SET "after"=? WHERE id=?', (audit.encode_snapshot(image), entry['id']))
    def read(s):
        def capture(*args):
            pytest.fail('required financial damage must fail before auxiliary capture')
        with monkeypatch.context() as patch:
            patch.setattr(history, 'capture', capture)
            with pytest.raises(BookflowError) as error:
                q.show(s, m.ShowInput(deposit=a.current.id), binding=OSBinding.from_session(s))
        assert error.value.code == 'E_DEPOSIT_SOURCE_INVALID' and error.value.details == {}
    observe(client, monkeypatch, read)


@pytest.mark.parametrize('site,error', [
    ('capture', BookflowError('E_PERMISSION', details={'owned': 'permission'})),
    ('capture', OSError('owned capture I/O')),
    ('issue', BookflowError('E_PERMISSION', details={'owned': 'permission'})),
    ('issue', OSError('owned issue I/O')),
    ('issue', history.MissingHistory('owned issue failure')),
], ids=['capture-permission', 'capture-io', 'issue-permission', 'issue-io', 'issue-missing-history'])
def test_other_errors_and_issue_failures_propagate_identity(client, posted, monkeypatch, site, error):
    a, _ = posted
    def read(s):
        def fail(*args):
            raise error
        with monkeypatch.context() as patch:
            patch.setattr(history, site, fail)
            with pytest.raises(type(error)) as caught:
                q.show(s, m.ShowInput(deposit=a.current.id), binding=OSBinding.from_session(s))
        assert caught.value is error
    observe(client, monkeypatch, read)
