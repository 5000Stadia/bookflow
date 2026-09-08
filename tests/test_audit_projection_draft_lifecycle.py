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


@pytest.fixture(scope='module')
def financial_history(_seeded_template, tmp_path_factory):
    from bookflow.company import deposit_coordination as coordinate, deposit_coordinate_persistence as persistence
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.publication import OSBinding
    c = _history.__wrapped__(_seeded_template, tmp_path_factory)
    client = bookflow.connect(data_root=str(c['root']))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('BOOKFLOW_DATA_ROOT', str(c['root']))
        patch.delenv('BOOKFLOW_COMPANY', raising=False)
        run = run_private.__wrapped__(client, patch)
        def command(verb, **body):
            return run(lambda s, ctx: deposit_drafts.run(s, ctx, deposit_drafts.INPUTS[verb].model_validate(body), verb))
        deposit, version = run(lambda s, ctx: s.company.raw.execute(
            "SELECT id,version FROM transactions WHERE type='deposit' LIMIT 1").fetchone())
        # Both no-financial-effect and changed updates still consume their exact draft.
        for changed in (False, True):
            draft = command('create', from_deposit=deposit, expected_version=version)
            if changed:
                draft = command('update', draft=draft.id, expected_version=draft.version,
                                header=dict(memo='Saved draft audit update'))
            result = financial(run, dict(deposit=deposit, expected_version=version,
                operation_key='audit-draft-update-' + str(changed),
                document=dict(mode='draft', draft=draft.id, expected_version=draft.version)), 'update')
            assert result.changed is changed
            assert result.current_draft.state == 'consumed' and result.current_draft.id == draft.id
            version = result.current.version
        draft = command('create', from_deposit=deposit, expected_version=version)
        source, source_version = run(lambda s, ctx: s.company.raw.execute(
            "SELECT t.id,t.version FROM transactions t JOIN deposit_draft_sources d ON d.source_transaction_id=t.id WHERE d.revision_id=?", (draft.revision_id,)).fetchone())
        inp = CoordinateInput.model_validate(dict(deposit=deposit, expected_version=version,
            operation_key='audit-coordinate-draft-consume',
            source_action=dict(kind='sales_receipt_update', input=dict(sales_receipt=source,
                expected_version=source_version, memo='Retained source correction')),
            replacement=dict(mode='document', document=dict(mode='draft', draft=draft.id,
                expected_version=draft.version), draft_source_result='retain')))
        def execute(s, ctx):
            binding = OSBinding.from_session(s)
            prepared = coordinate.prepare(s, ctx, inp, binding=binding)
            prepared = coordinate.prepare(s, ctx, inp.model_copy(update={'dependency_guard': prepared.dependency_guard}), binding=binding)
            return persistence.execute(s, ctx, prepared)
        result = run(execute)
        assert result.current_draft.state == 'consumed' and result.current_draft.id == draft.id
        assert result.current.revision_bank_total == 6025
        c['rows'] = run(lambda s, ctx: cursor_to_dicts(s.company.raw.execute(
            'SELECT a.command,e.* FROM audit_entries e JOIN audit_events a ON a.id=e.event_id ORDER BY a.seq,e.id')))
        c['rows'] = [r for r in c['rows'] if r['record_type'] in codec.MODELS]
    return c


@pytest.mark.parametrize('command', ('deposit update', 'deposit coordinate'))
def test_financial_draft_consumption_events_project_completely(financial_history, command):
    _check_event(financial_history, command)
