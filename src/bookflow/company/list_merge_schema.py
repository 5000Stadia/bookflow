"""Merged accounts and items: "M is merged into S" for an account or an item (co0075).

The sibling of ``party_merges`` (customers and vendors). One row per merge; nothing posted changes
when a merge is made, and every read that groups or filters by account or item resolves a
merged-away id to its survivor through this table. Unlike a customer, an account or item is also
named by editable list rows (an item's income account, a group's members, a price level's rows),
so a merge re-points those to the survivor and records each one in ``list_merge_references`` for
undo. Both tables are append-only except for the merge's one undo stamp.

A sibling table rather than a wider ``party_merges``: widening its kind CHECK and its
entries-exist trigger means rebuilding an append-only, triggered table in SQLite, and accounts
and items need the re-pointed references table beside it in any case.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    merges = T('list_merges',
        C('id', sa.String(26), 'Stable ULID of this merge.', primary_key=True),
        C('list_kind', sa.String(16), 'List the two entries belong to: account or item.', nullable=False),
        C('merged_id', sa.String(26), 'Entry merged away; inactive and resolved to the survivor while the merge stands.', nullable=False),
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
        sa.CheckConstraint("list_kind IN ('account','item')", name='ck_list_merge_kind'),
        sa.CheckConstraint("merged_id <> survivor_id", name='ck_list_merge_distinct'),
        sa.CheckConstraint("length(trim(reason)) > 0", name='ck_list_merge_reason'),
        sa.CheckConstraint(
            "(undone_at IS NULL AND undone_by IS NULL AND undone_via IS NULL AND undo_reason IS NULL AND undo_audit_event_id IS NULL)"
            " OR (undone_at IS NOT NULL AND undone_by IS NOT NULL AND undone_via IS NOT NULL"
            " AND length(trim(undo_reason)) > 0 AND undo_audit_event_id IS NOT NULL)",
            name='ck_list_merge_undo'),
        sa.Index('ux_list_merges_live_merged', 'list_kind', 'merged_id', unique=True,
                 sqlite_where=sa.text('undone_at IS NULL')),
        sa.Index('ix_list_merges_live_survivor', 'list_kind', 'survivor_id',
                 sqlite_where=sa.text('undone_at IS NULL')),
        description='Account and item merges: each merged-away entry and the survivor it reads as.')
    references = T('list_merge_references',
        C('id', sa.String(26), 'Stable ULID of this re-pointed reference.', primary_key=True),
        C('merge_id', sa.String(26), 'The merge that re-pointed it.', sa.ForeignKey('list_merges.id'), nullable=False),
        C('position', sa.Integer, 'Order the merge wrote it in; undo puts them back in reverse.', nullable=False),
        C('table_name', sa.String(64), 'Table of the editable list row that named the merged entry.', nullable=False),
        C('record_id', sa.String(26), 'That row.', nullable=False),
        C('column_name', sa.String(64), 'Its column that named the merged entry.', nullable=False),
        C('action', sa.String(16), 'repoint: the column now names the survivor; end: the row was made inactive because the survivor already has its own.', nullable=False),
        C('before_value', sa.String(26), 'What the column held before the merge (the merged entry).', nullable=True),
        C('after_value', sa.String(26), 'What the merge wrote (the survivor), or null for an ended row.', nullable=True),
        C('owner_kind', sa.String(32), 'List record the row belongs to, whose version the merge advanced.', nullable=False),
        C('owner_id', sa.String(26), 'That record.', nullable=False),
        sa.CheckConstraint("action IN ('repoint','end')", name='ck_list_merge_reference_action'),
        sa.UniqueConstraint('merge_id', 'position', name='uq_list_merge_reference_position'),
        description='Editable references an account or item merge re-pointed to the survivor, kept so an undo puts each back.')
    return {'list_merges': merges, 'list_merge_references': references}
