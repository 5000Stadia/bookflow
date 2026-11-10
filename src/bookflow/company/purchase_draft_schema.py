"""Separate non-posting draft records and permanent, immutable revision receipts."""
import sqlalchemy as sa

FLAGS = ('purchase_approvals_enabled', 'bill_approvals_enabled', 'approval_routing_enabled', 'approval_reminders_enabled')


def company_columns(C):
    return [C(name, sa.Boolean, 'Reserved approval layer switch; off until the approval workflow is available.',
              nullable=False, server_default=sa.text('0')) for name in FLAGS]


def define_tables(metadata, C, T, common):
    drafts = T('purchase_drafts', *common(),
        C('kind', sa.String(24), 'Target document kind; currently bill.', nullable=False),
        C('operation', sa.String(16), 'Intended native operation; currently create.', nullable=False),
        C('state', sa.String(16), 'draft or cancelled; neither has accounting effects.', nullable=False),
        C('payload', sa.Text, 'Normalized native bill fields as JSON with stable reference IDs and integer money.', nullable=False),
        C('preview', sa.Text, 'Exact calculated commercial snapshot as JSON.', nullable=False),
        C('currency', sa.String(3), 'Company currency captured by this revision.', nullable=False),
        sa.CheckConstraint("kind = 'bill' AND operation = 'create' AND state IN ('draft','cancelled')", name='ck_purchase_draft_scope'),
        sa.CheckConstraint('version >= 1', name='ck_purchase_draft_version'),
        sa.CheckConstraint('json_valid(payload) AND json_valid(preview)', name='ck_purchase_draft_json'),
        sa.Index('ix_purchase_drafts_state_id', 'state', 'id'),
        description='Saved expense-only bill drafts; no ledger, AP, inventory or source consumption effects.')
    revisions = T('purchase_draft_revisions',
        C('id', sa.String(26), 'Immutable revision ULID.', primary_key=True),
        C('draft_id', sa.String(26), 'Owning saved draft.', sa.ForeignKey('purchase_drafts.id', ondelete='RESTRICT'), nullable=False),
        C('version', sa.Integer, 'Draft version captured by this immutable revision.', nullable=False),
        C('state', sa.String(16), 'State after this act.', nullable=False),
        C('payload', sa.Text, 'Exact normalized commercial payload JSON.', nullable=False),
        C('preview', sa.Text, 'Exact calculated commercial snapshot JSON.', nullable=False),
        C('actor', sa.String(26), 'Actor who made this revision.', nullable=False),
        C('principal', sa.String(26), 'Human principal on whose behalf the actor worked.'),
        C('interface', sa.String(16), 'Command interface.', nullable=False),
        C('reason', sa.String(140), 'Required reason for this act.', nullable=False),
        C('created_at', sa.String(32), 'UTC time of the act.', nullable=False),
        sa.UniqueConstraint('draft_id', 'version', name='uq_purchase_draft_revision'),
        sa.CheckConstraint("state IN ('draft','cancelled') AND version >= 1", name='ck_purchase_draft_revision_state'),
        sa.CheckConstraint('json_valid(payload) AND json_valid(preview)', name='ck_purchase_draft_revision_json'),
        description='Insert-only draft revisions including cancelled history and actor attribution.')
    operations = T('purchase_draft_operations',
        C('id', sa.String(26), 'Permanent operation receipt ULID.', primary_key=True),
        C('draft_id', sa.String(26), 'Draft changed by this operation.', sa.ForeignKey('purchase_drafts.id', ondelete='RESTRICT'), nullable=False),
        C('command', sa.String(64), 'Exact shared command name.', nullable=False),
        C('actor', sa.String(26), 'Actor whose operation key this is.', nullable=False),
        C('operation_key', sa.String(128), 'Permanent caller operation identity, scoped to actor and command.', nullable=False),
        C('request', sa.Text, 'Canonical exact input and reason JSON.', nullable=False),
        C('result', sa.Text, 'Original typed output JSON for exact recovery.', nullable=False),
        sa.UniqueConstraint('command', 'actor', 'operation_key', name='uq_purchase_draft_operation'),
        description='Insert-only permanent draft operation receipts; retries recheck authority.')
    return {t.name: t for t in (drafts, revisions, operations)}
