"""Accountant review (co0088, R121): the external accountant, the accountant's copy and what comes back.

``accountant_designations`` names the members of this company who are its external accountant, the
anchor's External Accountant user: they read everything, and write only adjusting entries,
reclassifications, review marks and notes dated on or before ``through_date``.

``accountant_copies`` is the anchor's Accountant's Copy. The owner's row (``side`` owner) is written
when the copy is exported and, while ``status`` is out, locks every posting dated on or before the
dividing date. The exported database carries the same row turned to ``side`` accountant, with the
audit cursor it was taken at: that copy is the accountant's Bookflow, which posts only on or before
the dividing date and only the accountant's kinds of change. ``mac_key`` signs the change file the
copy writes; the owner's books hold the same key and refuse a file it does not verify.

``accountant_imports`` records each change taken back from a change file, once (idempotent by
copy and change key). ``accountant_adjustments`` names each journal entry an accountant made by
reclassifying or that came back from a copy. ``accountant_review_marks`` holds the accountant's
"reviewed through" marks; the newest is the current one.
"""
import sqlalchemy as sa

SIDES = ('owner', 'accountant')
STATUSES = ('out', 'returned', 'cancelled', 'working')
ADJUSTMENT_KINDS = ('adjusting', 'reclassification')
CHANGE_KINDS = ('journal', 'reclassification', 'note', 'review_mark', 'reviewed_through')


def _in(column, values):
    return f"{column} IN ({','.join(repr(v) for v in values)})"


def define_tables(metadata, C, T, common):
    designations = T('accountant_designations',
        *common(),
        C('user_id', sa.String(26), 'Member (a person) who is this company\'s external accountant.', nullable=False),
        C('through_date', sa.String(10), 'End of the review period, YYYY-MM-DD: the accountant posts only on or before it.', nullable=False),
        C('active', sa.Boolean, 'Whether the designation holds; a removed one is kept for history.', nullable=False, default=True),
        C('note', sa.String(500), 'What the engagement is, as the person wrote it.', nullable=True),
        sa.UniqueConstraint('user_id', name='uq_accountant_designation_user'),
        sa.CheckConstraint('active IN (0,1)', name='ck_accountant_designation_active'),
        description="Members designated as the company's external accountant, with the end of their review period.")

    copies = T('accountant_copies',
        *common(),
        C('side', sa.String(10), 'owner: the books the copy was made from; accountant: the copy itself.', nullable=False),
        C('status', sa.String(12), 'owner side: out, returned or cancelled; accountant side: working.', nullable=False),
        C('dividing_date', sa.String(10), 'YYYY-MM-DD: the accountant works on or before it, the owner after it.', nullable=False),
        C('origin_company_id', sa.String(26), 'Company id of the books the copy was made from.', nullable=False),
        C('mac_key', sa.String(64), 'Hex HMAC-SHA256 key that signs and verifies this copy\'s change file; never shown.', nullable=False),
        C('base_audit_seq', sa.Integer, 'Accountant side: the audit cursor when the copy was taken; later events are the accountant\'s.', nullable=True),
        C('file_name', sa.String(255), 'Owner side: the exported archive\'s file name.', nullable=True),
        C('archive_sha256', sa.String(64), 'Owner side: SHA-256 of the exported archive.', nullable=True),
        C('closed_at', sa.String(32), 'When the copy was returned or cancelled.', nullable=True),
        C('closed_by', sa.String(26), 'Who returned or cancelled the copy.', nullable=True),
        C('close_reason', sa.String(140), 'Why the copy was cancelled.', nullable=True),
        sa.CheckConstraint(_in('side', SIDES), name='ck_accountant_copy_side'),
        sa.CheckConstraint(_in('status', STATUSES), name='ck_accountant_copy_status'),
        sa.CheckConstraint("(side = 'owner' AND status IN ('out','returned','cancelled')) OR (side = 'accountant' AND status = 'working')",
                           name='ck_accountant_copy_side_status'),
        sa.Index('ux_accountant_copy_out', 'side', unique=True, sqlite_where=sa.text("status IN ('out','working')")),
        description="Accountant's copies: the owner's record of each one sent out, or, in a copy, the copy's own terms.")

    imports = T('accountant_imports',
        *common(),
        C('copy_id', sa.String(26), 'Copy the change came back from.', sa.ForeignKey('accountant_copies.id'), nullable=False),
        C('change_key', sa.String(64), 'The change\'s key in its change file: imported once.', nullable=False),
        C('kind', sa.String(20), 'journal, reclassification, note, review_mark or reviewed_through.', nullable=False),
        C('result_type', sa.String(32), 'Record type the change produced, such as transaction or note.', nullable=True),
        C('result_id', sa.String(26), 'Record the change produced.', nullable=True),
        C('accountant_user_id', sa.String(26), 'The accountant who made the change, as their copy recorded them.', nullable=False),
        C('accountant_name', sa.String(200), 'The accountant\'s name, as their copy recorded it.', nullable=False),
        sa.UniqueConstraint('copy_id', 'change_key', name='uq_accountant_import_change'),
        sa.CheckConstraint(_in('kind', CHANGE_KINDS), name='ck_accountant_import_kind'),
        description="Changes taken back from an accountant's change file, each once.")

    adjustments = T('accountant_adjustments',
        *common(),
        C('transaction_id', sa.String(26), 'The journal entry the accountant made.', sa.ForeignKey('transactions.id'), nullable=False),
        C('kind', sa.String(20), 'adjusting or reclassification.', nullable=False),
        C('source_transaction_id', sa.String(26), 'Reclassification: the entry whose amount was moved.', nullable=True),
        C('from_account_id', sa.String(26), 'Reclassification: the account the amount left.', nullable=True),
        C('to_account_id', sa.String(26), 'Reclassification: the account the amount went to.', nullable=True),
        C('accountant_user_id', sa.String(26), 'The accountant who made it.', nullable=False),
        C('copy_id', sa.String(26), 'The copy it came back from; null when made in these books.', nullable=True),
        sa.UniqueConstraint('transaction_id', name='uq_accountant_adjustment_transaction'),
        sa.CheckConstraint(_in('kind', ADJUSTMENT_KINDS), name='ck_accountant_adjustment_kind'),
        description="Journal entries an accountant made by reclassifying, or that came back from an accountant's copy.")

    marks = T('accountant_review_marks',
        *common(),
        C('through_date', sa.String(10), 'The accountant has reviewed the books through this date.', nullable=False),
        C('note', sa.String(1000), 'What the accountant found.', nullable=True),
        C('accountant_user_id', sa.String(26), 'The accountant who set the mark.', nullable=False),
        C('copy_id', sa.String(26), 'The copy the mark came back from; null when set in these books.', nullable=True),
        description='"Reviewed through" marks the accountant sets; the newest is the current one.')
    return {'accountant_designations': designations, 'accountant_copies': copies, 'accountant_imports': imports,
            'accountant_adjustments': adjustments, 'accountant_review_marks': marks}
