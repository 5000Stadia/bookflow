"""Permanent journal-entry cancellation receipts; original entries remain intact."""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    table = T('journal_deletions',
        C('transaction_id', sa.String(26), 'Deleted journal entry identity.', primary_key=True),
        C('family', sa.Text, 'Business family, independent of journal storage.', nullable=False),
        C('revision_id', sa.String(26), 'Captured revision cancelled or already voided.', nullable=False),
        C('from_status', sa.Text, 'Journal state before deletion.', nullable=False),
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
        sa.UniqueConstraint('created_by', 'operation_key', name='uq_journal_delete_operation'),
        sa.CheckConstraint("family IN ('journal_entry')", name='ck_journal_delete_family'),
        sa.CheckConstraint("from_status IN ('posted','voided')", name='ck_journal_delete_status'),
        sa.CheckConstraint("typeof(from_version)='integer' AND from_version>0 AND typeof(result_version)='integer' AND result_version=from_version+1", name='ck_journal_delete_version'),
        sa.CheckConstraint('length(trim(reason)) BETWEEN 1 AND 140', name='ck_journal_delete_reason'),
        *(sa.CheckConstraint(f"json_valid({name}) AND json_type({name})='object'", name='ck_journal_delete_'+name) for name in ('request_snapshot','result_snapshot')),
        description='Immutable journal-entry deletion and permanent authorized retry receipt.')
    return {'journal_deletions': table}


# The owner clause is the whole graph a journal entry can be held by, not only its header.
#
# The first arm is the one every family shares: the transaction must be the voided entry
# this row describes, at this revision and this version, carrying exactly this cancellation
# batch, and that batch must be the reversal it claims to be.
#
# The second arm is this family's own, and it is the whole reason this table is separate
# from `purchase_deletions`. A check, a card charge and a transfer are *stored* as
# `journal_entry` and are told apart only by a row in `money_out_documents`. Two of those
# three already delete through `purchase_deletions`, so a journal tombstone written over one
# of them would be a second, conflicting receipt on one document. The storage refuses it
# outright, independently of the refusal the writer raises above it -- no marker of any kind
# may carry a journal deletion, and an inventory document may not either, because its stock
# movements are part of the same fact and `inventory void` owns them.
ALIAS_DOCUMENT = ("EXISTS (SELECT 1 FROM money_out_documents m WHERE m.transaction_id=NEW.transaction_id)"
    " OR EXISTS (SELECT 1 FROM inventory_documents i WHERE i.transaction_id=NEW.transaction_id)")
# A live settlement still answers this entry: a journal line may sit in a customer or vendor
# ledger and be claimed by a payment or a bill payment, and deletion cannot detach it.
LIVE_SETTLEMENT = ("EXISTS (SELECT 1 FROM applications a WHERE (a.paid_transaction_id=NEW.transaction_id"
    " OR a.paying_transaction_id=NEW.transaction_id) AND a.kind='apply'"
    " AND NOT EXISTS (SELECT 1 FROM applications i WHERE i.reverses_application_id=a.id))"
    " OR EXISTS (SELECT 1 FROM ap_applications p WHERE (p.obligation_transaction_id=NEW.transaction_id"
    " OR p.source_transaction_id=NEW.transaction_id) AND p.kind='apply'"
    " AND NOT EXISTS (SELECT 1 FROM ap_applications v WHERE v.reverses_application_id=p.id))")
# A bank reconciliation still holds one of this entry's statement effects.
HELD_STATEMENT = ("EXISTS (SELECT 1 FROM reconciliation_keys k JOIN reconciliation_current_members c"
    " ON c.key_id=k.id WHERE k.transaction_id=NEW.transaction_id)")


def guards():
    return (
        "CREATE TRIGGER journal_deletions_owner BEFORE INSERT ON journal_deletions WHEN NOT EXISTS (SELECT 1 FROM transactions t WHERE t.id=NEW.transaction_id AND t.type=NEW.family AND t.status='voided' AND t.current_revision_id=NEW.revision_id AND t.version=NEW.result_version AND t.void_posting_batch_id IS NEW.cancellation_batch_id) OR (NEW.cancellation_batch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM posting_batches b WHERE b.id=NEW.cancellation_batch_id AND b.transaction_id=NEW.transaction_id AND b.revision_id=NEW.revision_id AND b.kind='reversal')) OR "
        + ALIAS_DOCUMENT + " OR " + LIVE_SETTLEMENT + " OR " + HELD_STATEMENT
        + " BEGIN SELECT RAISE(ABORT,'journal deletion owner mismatch'); END",
        *(f"CREATE TRIGGER journal_deletions_no_{action.lower()} BEFORE {action} ON journal_deletions BEGIN SELECT RAISE(ABORT,'journal deletion is immutable'); END" for action in ('UPDATE','DELETE')),
        "CREATE TRIGGER journal_deletions_transaction_fence BEFORE UPDATE ON transactions WHEN EXISTS (SELECT 1 FROM journal_deletions d WHERE d.transaction_id=OLD.id) BEGIN SELECT RAISE(ABORT,'deleted journal entry is immutable'); END",
    )
