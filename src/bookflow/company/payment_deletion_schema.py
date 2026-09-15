"""Permanent customer-payment cancellation receipts; original receipts stay intact."""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    table = T('payment_deletions',
        C('transaction_id', sa.String(26), 'Deleted receipt identity.', primary_key=True),
        C('family', sa.Text, 'Business family, independent of journal storage.', nullable=False),
        C('revision_id', sa.String(26), 'Captured revision cancelled or already voided.', nullable=False),
        C('from_status', sa.Text, 'Receipt state before deletion.', nullable=False),
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
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id']),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id']),
        sa.ForeignKeyConstraint(['cancellation_batch_id'], ['posting_batches.id']),
        sa.UniqueConstraint('created_by', 'operation_key', name='uq_payment_delete_operation'),
        sa.CheckConstraint("family IN ('payment')", name='ck_payment_delete_family'),
        sa.CheckConstraint("from_status IN ('posted','voided')", name='ck_payment_delete_status'),
        sa.CheckConstraint("typeof(from_version)='integer' AND from_version>0 AND typeof(result_version)='integer' AND result_version=from_version+1", name='ck_payment_delete_version'),
        sa.CheckConstraint('length(trim(reason)) BETWEEN 1 AND 140', name='ck_payment_delete_reason'),
        *(sa.CheckConstraint(f"json_valid({name}) AND json_type({name})='object'", name='ck_payment_delete_'+name) for name in ('request_snapshot','result_snapshot')),
        description='Immutable customer-payment deletion and permanent authorized retry receipt.')
    return {'payment_deletions': table}


# The owner clause is the whole released graph, not only the header: a receipt is
# deletable exactly when nothing else still holds it -- no live application, no
# live allocation and no deposit claim -- so the storage refuses a silent cascade
# even if a future writer forgot the refusal.
LIVE_APPLICATION = ("EXISTS (SELECT 1 FROM applications a WHERE a.paying_transaction_id=NEW.transaction_id"
    " AND a.kind='apply' AND NOT EXISTS (SELECT 1 FROM applications i WHERE i.reverses_application_id=a.id))")
LIVE_ALLOCATION = ("EXISTS (SELECT 1 FROM application_allocations l WHERE l.source_transaction_id=NEW.transaction_id"
    " AND l.kind='allocation' AND NOT EXISTS (SELECT 1 FROM application_allocations v WHERE v.reverses_allocation_id=l.id))")
DEPOSIT_CLAIM = "EXISTS (SELECT 1 FROM deposit_current_memberships m WHERE m.source_transaction_id=NEW.transaction_id)"


def guards():
    return (
        "CREATE TRIGGER payment_deletions_owner BEFORE INSERT ON payment_deletions WHEN NOT EXISTS (SELECT 1 FROM transactions t WHERE t.id=NEW.transaction_id AND t.type=NEW.family AND t.status='voided' AND t.current_revision_id=NEW.revision_id AND t.version=NEW.result_version AND t.void_posting_batch_id IS NEW.cancellation_batch_id) OR (NEW.cancellation_batch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM posting_batches b WHERE b.id=NEW.cancellation_batch_id AND b.transaction_id=NEW.transaction_id AND b.revision_id=NEW.revision_id AND b.kind='reversal')) OR "
        + LIVE_APPLICATION + " OR " + LIVE_ALLOCATION + " OR " + DEPOSIT_CLAIM
        + " BEGIN SELECT RAISE(ABORT,'payment deletion owner mismatch'); END",
        *(f"CREATE TRIGGER payment_deletions_no_{action.lower()} BEFORE {action} ON payment_deletions BEGIN SELECT RAISE(ABORT,'payment deletion is immutable'); END" for action in ('UPDATE','DELETE')),
        "CREATE TRIGGER payment_deletions_transaction_fence BEFORE UPDATE ON transactions WHEN EXISTS (SELECT 1 FROM payment_deletions d WHERE d.transaction_id=OLD.id) BEGIN SELECT RAISE(ABORT,'deleted payment is immutable'); END",
    )
