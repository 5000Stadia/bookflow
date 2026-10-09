"""Merged list entries: "B is merged into A" for a customer or a vendor (co0068).

One row per merge. Nothing posted changes when a merge is made: every read that groups or
filters by customer or vendor resolves a merged-away id to its survivor through this table.
A row is append-only except for one undo stamp, which ends the merge and is never cleared.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    merges = T('party_merges',
        C('id', sa.String(26), 'Stable ULID of this merge.', primary_key=True),
        C('party_kind', sa.String(16), 'List the two entries belong to: customer or vendor.', nullable=False),
        C('merged_id', sa.String(26), 'Entry merged away; hidden and resolved to the survivor while the merge stands.', nullable=False),
        C('survivor_id', sa.String(26), 'Entry the merged one now reads as.', nullable=False),
        C('deactivated_merged', sa.Boolean, 'Whether the merge made the merged entry inactive, so an undo makes it active again.', nullable=False),
        C('reason', sa.String(500), 'Why the person merged the two entries.', nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp of the merge.', nullable=False),
        C('created_by', sa.String(26), 'Person who merged the entries.', nullable=False),
        C('created_via', sa.String(16), 'Interface the merge came through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event of the merge.', sa.ForeignKey('audit_events.id'), nullable=False),
        C('undone_at', sa.String(32), 'UTC timestamp the merge was undone; null while it stands.', nullable=True),
        C('undone_by', sa.String(26), 'Person who undid the merge; null while it stands.', nullable=True),
        C('undone_via', sa.String(16), 'Interface the undo came through; null while it stands.', nullable=True),
        C('undo_reason', sa.String(500), 'Why the merge was undone; null while it stands.', nullable=True),
        C('undo_audit_event_id', sa.String(26), 'Audit event of the undo; null while it stands.', sa.ForeignKey('audit_events.id'), nullable=True),
        sa.CheckConstraint("party_kind IN ('customer','vendor')", name='ck_party_merge_kind'),
        sa.CheckConstraint("merged_id <> survivor_id", name='ck_party_merge_distinct'),
        sa.CheckConstraint("length(trim(reason)) > 0", name='ck_party_merge_reason'),
        sa.CheckConstraint(
            "(undone_at IS NULL AND undone_by IS NULL AND undone_via IS NULL AND undo_reason IS NULL AND undo_audit_event_id IS NULL)"
            " OR (undone_at IS NOT NULL AND undone_by IS NOT NULL AND undone_via IS NOT NULL"
            " AND length(trim(undo_reason)) > 0 AND undo_audit_event_id IS NOT NULL)",
            name='ck_party_merge_undo'),
        sa.Index('ux_party_merges_live_merged', 'party_kind', 'merged_id', unique=True,
                 sqlite_where=sa.text('undone_at IS NULL')),
        sa.Index('ix_party_merges_live_survivor', 'party_kind', 'survivor_id',
                 sqlite_where=sa.text('undone_at IS NULL')),
        description='Customer and vendor merges: each merged-away entry and the survivor it reads as.')
    return {'party_merges': merges}
