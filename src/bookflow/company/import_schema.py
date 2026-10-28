"""Reversible batches and list imports (co0108, R107 increments 0 and 1; design/bulk-import.md §2, §5, §8a, §9).

**The batch ledger** (increment 0, shared with bank feeds R108 and later grid edits):

- ``batches``: one multi-record import or modification, reversible as one unit. Rows are never deleted; only
  ``state`` and the reversal columns change (a guard refuses anything else), and a reversed batch never changes again.
- ``batch_effects``: immutable, one row per record a batch touched (or, for a reversal batch, per inverse command it
  ran): the record, the action, the version the batch left and, for an update, the prior values.

**List imports** (increment 1):

- ``import_runs``: one file imported for one kind. Only ``state``, ``batch_id``, ``version`` and ``updated_at`` change;
  counts and position are read from ``import_rows``.
- ``import_rows``: immutable. A row's outcome per phase: ``preview`` (with the mapped input and the file's own cells, so
  what runs is what was previewed) and ``run``.
- ``import_keys``: the key ledger. A record an import made, by source and key hash, unique among unreleased keys; a
  reversal releases them (``released_by``), so the corrected file imports again as new.

**Saved mappings and decisions** (co0109, increment 2; ``import_mappings.py``):

- ``import_sources``: a named origin of files for one kind. Never deleted; only ``active``, ``version`` and who and
  when changed.
- ``import_mappings``: the recipe for a source's files, append-only: a mapping changes only by saving a new version.
- ``import_decisions``: what a person or agent decided about an unknown reference in a run (map, create, skip).
  Immutable.
"""
import sqlalchemy as sa

BATCH_STATES = ('live', 'reversing', 'blocked', 'reversed', 'reversed_partly')
ORDERINGS = ('strict', 'record')
EFFECT_ACTIONS = ('created', 'updated', 'linked', 'rule_learned', 'reversed')
DECISION_ACTIONS = ('map', 'create', 'skip')
RUN_STATES = ('previewed', 'running', 'stopped', 'waiting', 'done', 'cancelled', 'rolled_back', 'failed')
MODES = ('create', 'update', 'upsert')
PHASES = ('preview', 'run')


def define_tables(metadata, C, T):
    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    batches = T('batches',
        ident('id', 'Stable ULID of this batch.', primary_key=True),
        C('kind', sa.String(32), 'What made it: import, bulk-edit, bank-import, reversal...', nullable=False),
        C('scope', sa.String(200), 'What "newest first" is measured in (an account for a bank import).', nullable=False),
        C('ordering', sa.String(8), 'strict: refused while a later live batch exists in its scope; record: judged '
                                    'per record.', nullable=False),
        C('label', sa.String(200), 'What the batch was, as a person reads it.', nullable=False),
        C('state', sa.String(16), 'live, reversing, reversed or reversed_partly.', nullable=False),
        C('source_type', sa.String(32), 'What the batch came from (import_run); null for none.', nullable=True),
        C('source_id', sa.String(26), 'The run it came from; null for none.', nullable=True),
        ident('reversal_of', 'For a reversal batch, the batch it reverses; null otherwise.', 'batches.id',
              nullable=True),
        C('actor_id', sa.String(26), 'Principal that made the batch.', nullable=False),
        C('actor_kind', sa.String(16), 'human or agent.', nullable=True),
        C('via', sa.String(16), 'Interface it was made through.', nullable=False),
        C('on_behalf_of', sa.String(26), 'The human an agent acted for; null otherwise.', nullable=True),
        C('reason', sa.Text, 'Why, as given; null when none.', nullable=True),
        C('created_at', sa.String(32), 'UTC time the batch was opened.', nullable=False),
        ident('reversed_by', 'The reversal batch that undid it; null while live.', 'batches.id', nullable=True),
        C('reversed_at', sa.String(32), 'UTC time its reversal began; null while live.', nullable=True),
        C('reversal_reason', sa.Text, 'Why it was reversed; null while live.', nullable=True),
        ident('reversal_event_id', 'Audit event that finished the reversal; null until then.', 'audit_events.id',
              nullable=True),
        sa.CheckConstraint(f'state IN ({quoted(BATCH_STATES)})', name='ck_batch_state'),
        sa.CheckConstraint(f'ordering IN ({quoted(ORDERINGS)})', name='ck_batch_ordering'),
        sa.Index('ix_batches_scope', 'scope', 'created_at'),
        description='Reversible batches: multi-record imports and modifications, each undone as one unit while nothing '
                    'later depends on it. Never deleted; only state and the reversal columns change.')

    effects = T('batch_effects',
        ident('batch_id', 'The batch.', 'batches.id', primary_key=True),
        C('seq', sa.Integer, 'Position within the batch, from 1, in the order written.', primary_key=True),
        C('record_type', sa.String(64), 'Record type touched (customer, item...).', nullable=False),
        C('record_id', sa.String(26), 'Record touched.', nullable=False),
        C('action', sa.String(16), 'created, updated, linked, rule_learned; reversed for a reversal batch.',
          nullable=False),
        C('version_after', sa.Integer, "The record's version the write left; null when it has none.", nullable=True),
        C('before', sa.Text, 'JSON of the prior values an update replaced (input-shaped); null otherwise.',
          nullable=True),
        ident('event_id', 'Audit event of the write.', 'audit_events.id', nullable=True),
        C('created_at', sa.String(32), 'UTC time the effect was recorded.', nullable=False),
        sa.CheckConstraint(f'action IN ({quoted(EFFECT_ACTIONS)})', name='ck_batch_effect_action'),
        sa.CheckConstraint('seq > 0', name='ck_batch_effect_seq'),
        sa.Index('ix_batch_effects_record', 'record_type', 'record_id'),
        description='What each batch touched, written in the same transaction as the write and its audit event. '
                    'Immutable.')

    runs = T('import_runs',
        ident('id', 'Stable ULID of this run.', primary_key=True),
        C('kind', sa.String(32), 'Import kind (customer, vendor, item, account, term...).', nullable=False),
        C('mode', sa.String(8), 'create, update or upsert.', nullable=False),
        C('atomic', sa.Boolean, 'True when the first refusal undoes the whole run.', nullable=False),
        C('state', sa.String(16), 'previewed, running, stopped, waiting, done, cancelled, rolled_back or failed (an atomic undo refused).',
          nullable=False),
        C('source', sa.String(200), 'The named origin of the file; keys are unique within it.', nullable=False),
        C('mapping', sa.Text, 'JSON of the mapping used: columns to input paths, key columns, formats.',
          nullable=False),
        C('attachment_id', sa.String(26), 'Attachment the file came from; null for pasted text.', nullable=True),
        C('file_name', sa.String(255), 'File name or label as given.', nullable=True),
        C('file_sha256', sa.String(64), 'SHA-256 of the file as read.', nullable=False),
        C('encoding', sa.String(32), 'Encoding the file was read in.', nullable=False),
        C('delimiter', sa.String(8), 'Cell delimiter the file was read with.', nullable=False),
        C('row_count', sa.Integer, 'Data rows (a document counts once).', nullable=False),
        C('stop_after_refusals', sa.Integer, 'Refusals in one call after which the run stops itself.',
          nullable=False),
        ident('batch_id', 'The batch its writes belong to; null until it first writes.', 'batches.id',
              nullable=True),
        C('reason', sa.Text, 'Why, as given at preview.', nullable=True),
        C('version', sa.Integer, 'Optimistic-concurrency version of the run.', nullable=False),
        C('created_at', sa.String(32), 'UTC time of the preview.', nullable=False),
        C('created_by', sa.String(26), 'Principal that previewed it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it was previewed through.', nullable=False),
        C('updated_at', sa.String(32), 'UTC time of the last state change.', nullable=False),
        sa.CheckConstraint(f'state IN ({quoted(RUN_STATES)})', name='ck_import_run_state'),
        sa.CheckConstraint(f'mode IN ({quoted(MODES)})', name='ck_import_run_mode'),
        description='Import runs: one file imported for one kind, previewed then run in slices. Only state, '
                    'batch and version change.')

    rows = T('import_rows',
        ident('run_id', 'The run.', 'import_runs.id', primary_key=True),
        C('row_no', sa.Integer, 'Position of the row (or document) in the file, from 1.', primary_key=True),
        C('phase', sa.String(8), 'preview or run.', primary_key=True),
        C('line', sa.Integer, 'Line of the file the row starts on.', nullable=False),
        C('key_hash', sa.String(64), 'SHA-256 of source, kind and the normalized key; null when it has none.',
          nullable=True),
        C('status', sa.String(16), 'create, update, created, updated, unchanged, differs, already_in, skipped or '
                                   'refused.', nullable=False),
        C('code', sa.String(64), 'Stable code of a refusal or skip; null otherwise.', nullable=True),
        C('message', sa.Text, 'What happened, as a person reads it; null when nothing needs saying.', nullable=True),
        C('column_name', sa.String(200), 'File column a refusal is about; null when none.', nullable=True),
        C('record_type', sa.String(64), 'Record the row made, matched or updated; null when none.', nullable=True),
        C('record_id', sa.String(26), 'That record; null when none.', nullable=True),
        C('payload', sa.Text, 'JSON of the mapped command input, references as the file wrote them (preview only).',
          nullable=True),
        C('cells', sa.Text, "JSON of the row's own cells (preview only), for the error file.", nullable=True),
        C('created_at', sa.String(32), 'UTC time the outcome was recorded.', nullable=False),
        sa.CheckConstraint(f'phase IN ({quoted(PHASES)})', name='ck_import_row_phase'),
        description='Per-row outcomes of an import run, per phase. Immutable.')

    keys = T('import_keys',
        ident('id', 'Stable ULID of this key.', primary_key=True),
        C('source', sa.String(200), 'The source the key is unique in.', nullable=False),
        C('key_hash', sa.String(64), 'SHA-256 of source, kind and the normalized key.', nullable=False),
        C('record_type', sa.String(64), 'Record the key made.', nullable=False),
        C('record_id', sa.String(26), 'That record.', nullable=False),
        ident('run_id', 'The run that made it.', 'import_runs.id'),
        ident('batch_id', 'The batch that made it.', 'batches.id'),
        C('created_at', sa.String(32), 'UTC time it was written, with the record.', nullable=False),
        ident('released_by', 'Reversal batch that released it; null while it stands.', 'batches.id', nullable=True),
        C('released_at', sa.String(32), 'UTC time it was released; null while it stands.', nullable=True),
        sa.Index('ux_import_keys_standing', 'source', 'key_hash', unique=True,
                 sqlite_where=sa.text('released_by IS NULL')),
        sa.Index('ix_import_keys_batch', 'batch_id'),
        sa.Index('ix_import_keys_record', 'record_type', 'record_id'),
        description='The import key ledger: which record each keyed row made, so a re-run never duplicates. Only the '
                    'release columns change, once.')
    sources = T('import_sources',
        ident('id', 'Stable ULID of this source.', primary_key=True),
        C('name', sa.String(200), 'Its name as given ("Square customer export").', nullable=False),
        C('name_key', sa.String(200), 'The name trimmed, case-folded and with spaces collapsed; unique.', nullable=False),
        C('kind', sa.String(32), 'The import kind its files hold.', nullable=False),
        C('active', sa.Boolean, 'False once deactivated: its mapping no longer matches files.', nullable=False),
        C('version', sa.Integer, 'Latest mapping version (and optimistic-concurrency version of the source).',
          nullable=False),
        C('created_at', sa.String(32), 'UTC time the source was first saved.', nullable=False),
        C('created_by', sa.String(26), 'Principal that first saved it.', nullable=False),
        C('updated_at', sa.String(32), 'UTC time of the last new version or deactivation.', nullable=False),
        C('updated_by', sa.String(26), 'Principal that made that change.', nullable=False),
        sa.Index('ux_import_sources_name_key', 'name_key', unique=True),
        description='Named origins of import files, one kind each. Never deleted; only active, version and who and '
                    'when change.')

    mappings = T('import_mappings',
        ident('id', 'Stable ULID of this mapping version.', primary_key=True),
        ident('source_id', 'The source it belongs to.', 'import_sources.id'),
        C('version', sa.Integer, 'Version within the source, from 1.', nullable=False),
        C('header_fingerprint', sa.String(64), 'SHA-256 of the sorted set of normalized headers.', nullable=False),
        C('headers', sa.Text, 'JSON list of the file headers as saved, in file order.', nullable=False),
        C('columns', sa.Text, 'JSON of file header to input path, for the mapped headers.', nullable=False),
        C('formats', sa.Text, 'JSON of decimal mark, thousands separator, date order, sheet and delimiter.',
          nullable=False),
        C('key_paths', sa.Text, 'JSON list of the input paths that key a row; empty when keyed by content.',
          nullable=False),
        C('mode', sa.String(8), 'create, update or upsert.', nullable=False),
        C('on_unknown', sa.String(8), 'What an unknown reference does: ask.', nullable=False),
        C('value_maps', sa.Text, 'JSON of input path to {file text: replacement}, applied before reading a cell.',
          nullable=False),
        C('defaults', sa.Text, 'JSON of input path to a value given to rows that have none.', nullable=False),
        ident('run_id', 'The run it was saved from; null when none.', 'import_runs.id', nullable=True),
        C('created_at', sa.String(32), 'UTC time it was saved.', nullable=False),
        C('created_by', sa.String(26), 'Principal that saved it.', nullable=False),
        sa.Index('ux_import_mappings_version', 'source_id', 'version', unique=True),
        sa.Index('ix_import_mappings_fingerprint', 'header_fingerprint'),
        description='Saved import mappings, append-only: each save is a new version of its source.')

    decisions = T('import_decisions',
        ident('id', 'Stable ULID of this decision.', primary_key=True),
        ident('run_id', 'The run it decides for.', 'import_runs.id'),
        C('field', sa.String(200), 'Input path of the reference (terms_id).', nullable=False),
        C('value', sa.Text, 'The text the file wrote.', nullable=False),
        C('value_key', sa.String(400), 'That text normalized (trimmed, case-folded, spaces collapsed).',
          nullable=False),
        C('action', sa.String(8), 'map (to an existing record), create (a new one) or skip (its rows).',
          nullable=False),
        C('target', sa.Text, 'The name mapped to or created; null for skip.', nullable=True),
        C('input', sa.Text, "JSON of the create command's further input; null otherwise.", nullable=True),
        C('remembered', sa.Boolean, "True when the map was also saved in the source's value maps.", nullable=False),
        C('created_at', sa.String(32), 'UTC time it was decided.', nullable=False),
        C('created_by', sa.String(26), 'Principal that decided it.', nullable=False),
        sa.CheckConstraint(f'action IN ({quoted(DECISION_ACTIONS)})', name='ck_import_decision_action'),
        sa.Index('ux_import_decisions_value', 'run_id', 'field', 'value_key', unique=True),
        description='Decisions about unknown references in an import run. Immutable.')
    return {'batches': batches, 'batch_effects': effects, 'import_runs': runs, 'import_rows': rows,
            'import_keys': keys, 'import_sources': sources, 'import_mappings': mappings,
            'import_decisions': decisions}
