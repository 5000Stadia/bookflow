"""Permanent purchase cancellation receipts; original documents remain intact."""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    table = T('purchase_deletions',
        C('transaction_id', sa.String(26), 'Deleted purchase identity.', primary_key=True),
        C('family', sa.Text, 'Business family, independent of journal storage.', nullable=False),
        C('revision_id', sa.String(26), 'Captured revision cancelled or already voided.', nullable=False),
        C('from_status', sa.Text, 'Purchase state before deletion.', nullable=False),
        C('from_version', sa.Integer, 'Exact caller-observed version.', nullable=False),
        C('result_version', sa.Integer, 'Version after deletion.', nullable=False),
        C('cancellation_batch_id', sa.String(26), 'Exact new or retained prior void batch.', nullable=True),
        C('operation_key', sa.Text, 'Permanent actor-scoped retry identity.', nullable=False),
        C('request_hash', sa.Text, 'Canonical original request digest.', nullable=False),
        C('request_snapshot', sa.Text, 'Original request and retained dependency graph.', nullable=False),
        C('result_snapshot', sa.Text, 'Original typed deletion result.', nullable=False),
        C('created_at', sa.Text, 'Deletion timestamp.', nullable=False),
        C('created_by', sa.String(26), 'Authenticated actor.', nullable=False),
        C('principal_id', sa.String(26), 'Authenticated bound principal when present.', nullable=True),
        C('created_via', sa.Text, 'Calling interface.', nullable=False),
        C('reason', sa.Text, 'Required cancellation reason.', nullable=False),
        C('audit_event_id', sa.String(26), 'Deletion audit event.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.ForeignKeyConstraint(['transaction_id'], ['money_out_documents.transaction_id']),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id']),
        sa.ForeignKeyConstraint(['cancellation_batch_id'], ['posting_batches.id']),
        sa.UniqueConstraint('created_by', 'operation_key', name='uq_purchase_delete_operation'),
        sa.CheckConstraint("family IN ('check','card_charge')", name='ck_purchase_delete_family'),
        sa.CheckConstraint("from_status IN ('posted','voided')", name='ck_purchase_delete_status'),
        sa.CheckConstraint("typeof(from_version)='integer' AND from_version>0 AND typeof(result_version)='integer' AND result_version=from_version+1", name='ck_purchase_delete_version'),
        sa.CheckConstraint('length(trim(reason)) BETWEEN 1 AND 140', name='ck_purchase_delete_reason'),
        *(sa.CheckConstraint(f"json_valid({name}) AND json_type({name})='object'", name='ck_purchase_delete_'+name) for name in ('request_snapshot','result_snapshot')),
        description='Immutable check/card deletion and permanent authorized retry receipt.')
    return {'purchase_deletions': table}


def guards():
    return (
        "CREATE TRIGGER purchase_deletions_owner BEFORE INSERT ON purchase_deletions WHEN NOT EXISTS (SELECT 1 FROM money_out_documents m JOIN transactions t ON t.id=m.transaction_id WHERE m.transaction_id=NEW.transaction_id AND m.kind=NEW.family AND t.type='journal_entry' AND t.status='voided' AND t.current_revision_id=NEW.revision_id AND t.version=NEW.result_version AND t.void_posting_batch_id IS NEW.cancellation_batch_id) OR (NEW.cancellation_batch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM posting_batches b WHERE b.id=NEW.cancellation_batch_id AND b.transaction_id=NEW.transaction_id AND b.revision_id=NEW.revision_id AND b.kind='reversal')) BEGIN SELECT RAISE(ABORT,'purchase deletion owner mismatch'); END",
        *(f"CREATE TRIGGER purchase_deletions_no_{action.lower()} BEFORE {action} ON purchase_deletions BEGIN SELECT RAISE(ABORT,'purchase deletion is immutable'); END" for action in ('UPDATE','DELETE')),
        "CREATE TRIGGER purchase_deletions_transaction_fence BEFORE UPDATE ON transactions WHEN EXISTS (SELECT 1 FROM purchase_deletions d WHERE d.transaction_id=OLD.id) BEGIN SELECT RAISE(ABORT,'deleted purchase is immutable'); END",
    )
