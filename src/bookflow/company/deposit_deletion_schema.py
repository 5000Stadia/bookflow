"""Permanent deposit cancellation receipts; original deposits remain intact."""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    table = T('deposit_deletions',
        C('transaction_id', sa.String(26), 'Deleted deposit identity.', primary_key=True),
        C('family', sa.Text, 'Business family, independent of journal storage.', nullable=False),
        C('revision_id', sa.String(26), 'Captured revision cancelled or already voided.', nullable=False),
        C('from_status', sa.Text, 'Deposit state before deletion.', nullable=False),
        C('from_version', sa.Integer, 'Exact caller-observed version.', nullable=False),
        C('result_version', sa.Integer, 'Version after deletion.', nullable=False),
        C('cancellation_batch_id', sa.String(26), 'Exact new or retained prior void batch.', nullable=True),
        C('operation_id', sa.String(26), 'Permanent deposit operation this deletion ran as.', nullable=True),
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
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id']),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id']),
        sa.ForeignKeyConstraint(['cancellation_batch_id'], ['posting_batches.id']),
        sa.ForeignKeyConstraint(['operation_id'], ['deposit_operations.id']),
        sa.UniqueConstraint('created_by', 'operation_key', name='uq_deposit_delete_operation'),
        sa.CheckConstraint("family IN ('deposit')", name='ck_deposit_delete_family'),
        sa.CheckConstraint("from_status IN ('posted','voided')", name='ck_deposit_delete_status'),
        sa.CheckConstraint("typeof(from_version)='integer' AND from_version>0 AND typeof(result_version)='integer' AND result_version=from_version+1", name='ck_deposit_delete_version'),
        sa.CheckConstraint('length(trim(reason)) BETWEEN 1 AND 140', name='ck_deposit_delete_reason'),
        *(sa.CheckConstraint(f"json_valid({name}) AND json_type({name})='object'", name='ck_deposit_delete_'+name) for name in ('request_snapshot','result_snapshot')),
        description='Immutable deposit deletion and permanent authorized retry receipt.')
    return {'deposit_deletions': table}


# The owner clause is the whole released graph, not only the header. A deposit banks
# receipts it does not own: every claim it holds must already be released back to
# Undeposited Funds before this row may exist, so the storage refuses a deleted deposit
# that is still standing on somebody's payment. A reconciled bank effect is the other
# arm -- a statement line that was ticked off is cancelled by the reconciliation that
# holds it, never by a deletion behind its back.
HELD_RECEIPT = ("EXISTS (SELECT 1 FROM deposit_current_memberships m"
    " WHERE m.transaction_id=NEW.transaction_id)")
HELD_RECONCILIATION = ("EXISTS (SELECT 1 FROM reconciliation_keys k JOIN reconciliation_current_members c"
    " ON c.key_id=k.id WHERE k.transaction_id=NEW.transaction_id)")


def guards():
    return (
        "CREATE TRIGGER deposit_deletions_owner BEFORE INSERT ON deposit_deletions WHEN NOT EXISTS (SELECT 1 FROM transactions t WHERE t.id=NEW.transaction_id AND t.type=NEW.family AND t.status='voided' AND t.current_revision_id=NEW.revision_id AND t.version=NEW.result_version AND t.void_posting_batch_id IS NEW.cancellation_batch_id) OR (NEW.cancellation_batch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM posting_batches b WHERE b.id=NEW.cancellation_batch_id AND b.transaction_id=NEW.transaction_id AND b.revision_id=NEW.revision_id AND b.kind='reversal')) OR "
        + HELD_RECEIPT + " OR " + HELD_RECONCILIATION
        + " BEGIN SELECT RAISE(ABORT,'deposit deletion owner mismatch'); END",
        *(f"CREATE TRIGGER deposit_deletions_no_{action.lower()} BEFORE {action} ON deposit_deletions BEGIN SELECT RAISE(ABORT,'deposit deletion is immutable'); END" for action in ('UPDATE','DELETE')),
        "CREATE TRIGGER deposit_deletions_transaction_fence BEFORE UPDATE ON transactions WHEN EXISTS (SELECT 1 FROM deposit_deletions d WHERE d.transaction_id=OLD.id) BEGIN SELECT RAISE(ABORT,'deleted deposit is immutable'); END",
    )
