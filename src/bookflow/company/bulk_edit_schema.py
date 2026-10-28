"""Grid edits (co0115, R107 increment 6; design/bulk-import.md §7, §8a).

- ``bulk_edit_runs``: one grid edit of one list kind: what was asked (the selection, the cell changes and column
  operations), previewed, then applied as one batch. Only ``state``, ``batch_id`` (once), ``version`` and
  ``updated_at`` change; never deleted.
- ``bulk_edit_rows``: the diff, one row per record the edit changes or refuses at preview: each field's value before
  and after, and the version the preview read. Its apply outcome is written once (``outcome`` and the columns beside
  it, from null); nothing else ever changes, and no row is deleted. "What did that edit change" is one query here.

Reversal is the shared batch ledger's (``batches``, ``batch_effects``): each applied record is one ``updated`` effect
with its prior values, written in the update's own transaction.
"""
import sqlalchemy as sa

KINDS = ('customer', 'vendor', 'item', 'account')
RUN_STATES = ('previewed', 'applying', 'applied', 'applied_partly', 'rolled_back', 'cancelled', 'failed')
ROW_STATUSES = ('change', 'refused')
OUTCOMES = ('updated', 'merged', 'conflict', 'refused', 'not_run', 'rolled_back')


def define_tables(metadata, C, T):
    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    runs = T('bulk_edit_runs',
        ident('id', 'Stable ULID of this grid edit.', primary_key=True),
        C('kind', sa.String(32), 'List edited: customer, vendor, item or account.', nullable=False),
        C('state', sa.String(16), 'previewed, applying, applied, applied_partly (some records refused), rolled_back '
                                  '(all or nothing, undone at a refusal), cancelled (stopped part way, what it '
                                  'changed put back) or failed (that undo was refused).',
          nullable=False),
        C('atomic', sa.Boolean, 'True when the first refusal undoes the whole edit.', nullable=False),
        C('selection', sa.Text, 'JSON of the records chosen: the list filter, search text or named records.',
          nullable=False),
        C('operations', sa.Text, 'JSON of what was asked: cell changes, pasted text and column operations, in order.',
          nullable=False),
        C('label', sa.String(200), 'What the edit does, as a person reads it.', nullable=False),
        C('record_count', sa.Integer, 'Records selected.', nullable=False),
        C('unchanged_count', sa.Integer, 'Selected records the edit leaves as they are (no row is stored for them).',
          nullable=False),
        ident('batch_id', 'The batch its writes belong to; null until it is applied.', 'batches.id', nullable=True),
        C('reason', sa.Text, 'Why, as given at preview.', nullable=True),
        C('version', sa.Integer, 'Optimistic-concurrency version of the run.', nullable=False),
        C('created_at', sa.String(32), 'UTC time of the preview.', nullable=False),
        C('created_by', sa.String(26), 'Principal that previewed it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it was previewed through.', nullable=False),
        C('updated_at', sa.String(32), 'UTC time of the last state change.', nullable=False),
        sa.CheckConstraint(f'state IN ({quoted(RUN_STATES)})', name='ck_bulk_edit_run_state'),
        sa.CheckConstraint(f'kind IN ({quoted(KINDS)})', name='ck_bulk_edit_run_kind'),
        sa.Index('ix_bulk_edit_runs_created', 'created_at'),
        description='Grid edits: a selection of one list and the changes asked of it, previewed as a diff, then '
                    'applied as one reversible batch. Only state, batch and version change.')

    rows = T('bulk_edit_rows',
        ident('run_id', 'The grid edit.', 'bulk_edit_runs.id', primary_key=True),
        C('seq', sa.Integer, 'Position of the record in the selection, from 1.', primary_key=True),
        C('record_type', sa.String(64), 'Record type (customer, vendor, item, account).', nullable=False),
        C('record_id', sa.String(26), 'The record.', nullable=False),
        C('label', sa.String(1004), 'Its full name when previewed.', nullable=False),
        C('version', sa.Integer, 'Its version when previewed: the apply sends it as expected_version.', nullable=False),
        C('status', sa.String(16), 'change (the update dry-ran clean) or refused (it did not).', nullable=False),
        C('changes', sa.Text, 'JSON list of field, before and after (input-shaped), and each as shown.',
          nullable=False),
        C('code', sa.String(64), 'Stable code of a refusal at preview; null otherwise.', nullable=True),
        C('message', sa.Text, 'Why it was refused at preview; null otherwise.', nullable=True),
        C('outcome', sa.String(16), 'At apply: updated, merged (over another edit to other fields), conflict (the '
                                    'same field changed meanwhile), refused, not_run or rolled_back; null before.',
          nullable=True),
        C('outcome_code', sa.String(64), 'Stable code of a conflict or refusal at apply; null otherwise.',
          nullable=True),
        C('outcome_message', sa.Text, 'What happened at apply, as a person reads it; null when nothing needs saying.',
          nullable=True),
        C('version_after', sa.Integer, 'The version the update left; null when it did not run.', nullable=True),
        C('merged_over', sa.Text, 'JSON list of the versions an apply merged over; null when none.', nullable=True),
        ident('event_id', 'Audit event of the update; null when it did not run.', 'audit_events.id', nullable=True),
        C('applied_at', sa.String(32), 'UTC time of the outcome; null before.', nullable=True),
        sa.CheckConstraint(f'status IN ({quoted(ROW_STATUSES)})', name='ck_bulk_edit_row_status'),
        sa.CheckConstraint(f'outcome IS NULL OR outcome IN ({quoted(OUTCOMES)})', name='ck_bulk_edit_row_outcome'),
        sa.Index('ix_bulk_edit_rows_record', 'record_type', 'record_id'),
        description='The diff of a grid edit, one row per record changed or refused, with its outcome written once '
                    'at apply. Never deleted.')
    return {'bulk_edit_runs': runs, 'bulk_edit_rows': rows}


GUARDS = (
    "CREATE TRIGGER bulk_edit_runs_no_delete BEFORE DELETE ON bulk_edit_runs BEGIN "
    "SELECT RAISE(ABORT, 'immutable grid edits'); END",
    "CREATE TRIGGER bulk_edit_rows_no_delete BEFORE DELETE ON bulk_edit_rows BEGIN "
    "SELECT RAISE(ABORT, 'immutable grid edit rows'); END",
    "CREATE TRIGGER bulk_edit_runs_frozen BEFORE UPDATE ON bulk_edit_runs WHEN NEW.id IS NOT OLD.id "
    "OR NEW.kind IS NOT OLD.kind OR NEW.atomic IS NOT OLD.atomic OR NEW.selection IS NOT OLD.selection "
    "OR NEW.operations IS NOT OLD.operations OR NEW.label IS NOT OLD.label OR NEW.record_count IS NOT OLD.record_count "
    "OR NEW.unchanged_count IS NOT OLD.unchanged_count OR NEW.reason IS NOT OLD.reason "
    "OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by "
    "OR NEW.created_via IS NOT OLD.created_via "
    "OR (OLD.batch_id IS NOT NULL AND NEW.batch_id IS NOT OLD.batch_id) BEGIN "
    "SELECT RAISE(ABORT, 'a grid edit changes only its state and batch'); END",
    "CREATE TRIGGER bulk_edit_rows_outcome_once BEFORE UPDATE ON bulk_edit_rows WHEN OLD.outcome IS NOT NULL "
    "OR NEW.outcome IS NULL OR NEW.run_id IS NOT OLD.run_id OR NEW.seq IS NOT OLD.seq "
    "OR NEW.record_type IS NOT OLD.record_type OR NEW.record_id IS NOT OLD.record_id OR NEW.label IS NOT OLD.label "
    "OR NEW.version IS NOT OLD.version OR NEW.status IS NOT OLD.status OR NEW.changes IS NOT OLD.changes "
    "OR NEW.code IS NOT OLD.code OR NEW.message IS NOT OLD.message BEGIN "
    "SELECT RAISE(ABORT, 'a grid edit row takes its outcome once'); END",
)
