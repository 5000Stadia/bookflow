"""Remaining real draft lifecycle producers through the complete event reader."""
import pytest
import bookflow
from bookflow.company import deposit_drafts, deposit_selection, deposit_source_queries
from bookflow.company import deposit_draft_models as models
from bookflow.hub import audit_projection_deposit_drafts as codec
from tests.test_audit_projection_draft_owners import draft_history as _history
from tests.test_audit_projection_draft_events import test_each_stored_draft_producer_event_is_complete as _check_event
from tests.test_deposit_draft_financial import run_private, financial


@pytest.fixture(scope='module')
def lifecycle_history(_seeded_template, tmp_path_factory):
    c = _history.__wrapped__(_seeded_template, tmp_path_factory)
    client = bookflow.connect(data_root=str(c['root']))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('BOOKFLOW_DATA_ROOT', str(c['root']))
        patch.delenv('BOOKFLOW_COMPANY', raising=False)
        run = run_private.__wrapped__(client, patch)
        def command(owner, verb, **body):
            return run(lambda s, ctx: owner.run(s, ctx, owner.INPUTS[verb].model_validate(body), verb))
        deposit, version = run(lambda s, ctx: s.company.raw.execute(
            "SELECT id,version FROM transactions WHERE type='deposit' LIMIT 1").fetchone())
        financial(run, dict(operation_key='audit-lifecycle-void', deposit=deposit, expected_version=version), 'void')
        copied = command(deposit_drafts, 'create', copy_from_voided=deposit, expected_version=version+1)
        assert copied.copy_transaction_id == deposit and copied.summary.source_count == 1
        selection = command(deposit_selection, 'create', draft=copied.id, expected_version=copied.version)
        filters = models.SourceFilter(date='2026-06-03')
        page = run(lambda s, ctx: deposit_source_queries.query(s, models.SourceQuery(**filters.model_dump()), ctx=ctx))
        selection = command(deposit_selection, 'select-matching', selection=selection.id,
            expected_version=selection.version, filter=filters.model_dump(), facts_fingerprint=page.facts_fingerprint)
        assert selection.source_count == 1
        selection = command(deposit_selection, 'abandon', selection=selection.id, expected_version=selection.version)
        assert selection.state == 'abandoned'
        cleared = command(deposit_drafts, 'clear', draft=copied.id, expected_version=copied.version)
        assert cleared.summary.source_count == 0
        abandoned = command(deposit_drafts, 'abandon', draft=cleared.id, expected_version=cleared.version)
        assert abandoned.state == 'abandoned'
        def capture(s, ctx):
            cursor = s.company.raw.execute('SELECT a.command,e.* FROM audit_entries e JOIN audit_events a ON a.id=e.event_id ORDER BY a.seq,e.id')
            return [dict(row) for row in cursor_to_dicts(cursor)]
        c['rows'] = [r for r in run(capture) if r['record_type'] in codec.MODELS]
    return c


def cursor_to_dicts(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


@pytest.mark.parametrize('command', (
    'deposit draft create', 'deposit draft clear', 'deposit draft abandon',
    'deposit selection select-matching', 'deposit selection abandon',
))
def test_remaining_lifecycle_events_project_completely(lifecycle_history, command):
    _check_event(lifecycle_history, command)
