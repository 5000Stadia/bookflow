"""Which document a posted register entry was written as.

A check, a credit card charge and a funds transfer all post the entry ``register post``
posts -- the same batch, the same lines, the same money. What separates them is not the
accounting but the document a person filled in, and that fact lives nowhere in the posting
because the posting is identical. ``money_out_documents`` is that fact and nothing else: one
immutable row per transaction, written once when the document is entered, saying which of
the three it was.

Without it ``check query`` has nothing to answer with. Every alternative infers the document
from the shape of its lines -- a credit to a bank account with expense debits behind it --
and a hand-typed journal entry has exactly that shape, so the inference would list entries
nobody wrote as a check. A stored kind is the only one of the two that can be right.

The row carries the transaction's ``type`` and is joined to ``transactions`` on both columns,
so the marker can only ever attach to the journal entry these documents post as; a later
document family with its own transaction type cannot borrow it by accident.

**The cheque identity is a second fact, and it is not the document number.**
``transactions.number`` identifies a document inside this system and comes from the shared
journal series. A check number identifies a piece of paper in one bank account's chequebook.
They answer different questions, and the two tables below keep them apart:

``check_instrument_revisions`` is immutable and carries the funding account and the number
each revision of a check was written with, so correcting either one cannot rewrite what an
earlier revision said. ``check_instruments`` is the current projection of that -- one row per
cheque, replaced when a correction gives the cheque a different account or number -- and it
carries the one constraint the revisions cannot: ``uq_check_instrument_number`` over
``(account_id, check_number_key)``, which is where two cheques claiming one number on one
bank account are refused. That index covers the numbers Bookflow issued and not the ones a
migration carried over, because a file upgraded from the shared journal series, or from the
free-text number a bill payment used to hold, can already hold one number written twice and
has to open so that the report can say so.

**Both documents that print a cheque keep their number here.** A cheque written from Pay
Bills is the same piece of paper as one written from Write Checks, so a bill payment carries
a ``check_instruments`` row exactly as a check does and draws its number from the same
chequebook. ``ap_payment_profiles.check_number`` still records what that payment was written
with, but it is now what the allocator handed out rather than free text nothing could place.
Which document types may carry a cheque is ``CHEQUE_DOCUMENT_TYPES``, declared below.

``check_number_key`` is the canonical form that uniqueness compares: a run of digits with
leading zeros removed, so ``1001`` and ``01001`` are one cheque and not two; anything that is
not a plain run of digits is case-folded and compared whole. ``check_sequence`` is the same
number as an integer when it has a place in a sequence, and null when it does not -- ``EFT``
and ``1001-A`` are real cheque numbers that sit between no two others.
"""

import sqlalchemy as sa

# The three documents this marker distinguishes, in their stored spelling.
KINDS = ('check', 'card_charge', 'transfer')

# Every document that can carry a cheque, in the transaction type it posts as: the journal
# entry ``check post`` writes, and the bill payment ``bill pay`` writes. Declared once and
# read by ``ck_check_instrument_type`` below, by ``company/check_numbers.py`` -- which refuses
# to hand a number to anything else -- and by ``company/check_reports.py``, so whatever prints
# a cheque next joins the chequebook by adding itself here and nowhere else.
CHEQUE_DOCUMENT_TYPES = ('journal_entry', 'bill_payment')


def _one_of(column, values):
    """A CHECK over a set that is declared once, spelled the way the dialect compiles it."""
    return f'{column} IN (' + ', '.join(f"'{value}'" for value in values) + ')'


def define_tables(metadata, column, table):
    C, T = column, table

    money_out_documents = T('money_out_documents',
        C('transaction_id', sa.String(26), 'The posted journal entry this document was written as.',
          primary_key=True),
        C('type', sa.String(32), 'Transaction type of the marked document; always journal_entry.', nullable=False),
        C('kind', sa.String(16), 'Document a person entered: check, card_charge or transfer.', nullable=False),
        C('created_at', sa.String(32), 'UTC time this document marker was written.', nullable=False),
        C('created_by', sa.String(26), 'Company principal that entered the document.', nullable=False),
        C('created_via', sa.String(16), 'Interface the document was entered through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that committed the document this marks.',
          sa.ForeignKey('audit_events.id'), nullable=False),
        sa.CheckConstraint("kind IN ('check', 'card_charge', 'transfer')", name='ck_money_out_kind'),
        sa.CheckConstraint("type = 'journal_entry'", name='ck_money_out_type'),
        # One constraint says both things: the transaction exists, and it is the journal entry
        # these documents post as. A separate single-column reference would say half of it twice.
        sa.ForeignKeyConstraint(['transaction_id', 'type'], ['transactions.id', 'transactions.type'],
                                name='fk_money_out_document_type'),
        sa.Index('ix_money_out_documents_kind', 'kind', 'transaction_id'),
        description='Immutable record of which money-out document a posted journal entry was entered as.')

    # What a check number is, in both tables below. ``check_number`` is what is printed on
    # the paper; ``check_number_key`` is what uniqueness compares; ``check_sequence`` is the
    # place it takes in a run, or null when it takes none.
    def identity(*, immutable):
        when = 'when this revision was written' if immutable else 'of the latest correction'
        return [
            C('account_id', sa.String(26), 'Bank account whose chequebook this number belongs to.',
              sa.ForeignKey('accounts.id'), nullable=False),
            C('check_number', sa.String(64), 'Number as it is written on the face of the cheque.',
              nullable=False),
            C('check_number_key', sa.String(64),
              'Canonical form of check_number: digits without leading zeros, otherwise case-folded.',
              nullable=False),
            C('check_sequence', sa.BigInteger,
              'check_number as an integer when it is a plain run of digits; null when it has no place in a sequence.',
              nullable=True),
            C('origin', sa.String(16),
              'issued when a command allocated or accepted this number; migrated when co0040 carried it over from the shared journal series.',
              nullable=False),
            C('created_at' if immutable else 'updated_at', sa.String(32),
              f'UTC time {when}.', nullable=False),
            C('created_by' if immutable else 'updated_by', sa.String(26),
              f'Company principal {when}.', nullable=False),
            C('created_via' if immutable else 'updated_via', sa.String(16),
              f'Interface {when}.', nullable=False),
            C('audit_event_id', sa.String(26), 'Audit event that committed this cheque identity.',
              sa.ForeignKey('audit_events.id'), nullable=False),
        ]

    def shape(name):
        return [
            sa.CheckConstraint("origin IN ('issued', 'migrated')", name=f'ck_{name}_origin'),
            sa.CheckConstraint(
                "check_sequence IS NULL OR (typeof(check_sequence) = 'integer' AND check_sequence > 0)",
                name=f'ck_{name}_sequence'),
            # Neither the printed number nor the key it canonicalises to is ever empty: a
            # cheque with no number at all is a cheque with no place and no identity.
            sa.CheckConstraint("length(check_number) BETWEEN 1 AND 64 AND length(check_number_key) BETWEEN 1 AND 64",
                               name=f'ck_{name}_number_length'),
        ]

    check_instrument_revisions = T('check_instrument_revisions',
        C('revision_id', sa.String(26), 'Immutable journal revision this cheque identity belongs to.',
          primary_key=True),
        C('transaction_id', sa.String(26), 'Check this revision belongs to.', nullable=False),
        *identity(immutable=True),
        *shape('check_instrument_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
                                ['transaction_revisions.transaction_id', 'transaction_revisions.id'],
                                name='fk_check_instrument_revision'),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_check_instrument_revision_owner'),
        sa.Index('ix_check_instrument_revisions_number', 'account_id', 'check_number_key'),
        description='Immutable funding account and check number of each revision of a cheque.')

    check_instruments = T('check_instruments',
        C('transaction_id', sa.String(26), 'The check this cheque identity currently belongs to.',
          primary_key=True),
        C('type', sa.String(32), 'Transaction type of the document this cheque was written on: the journal entry a check posts as, or the bill payment Pay Bills writes.', nullable=False),
        C('revision_id', sa.String(26), 'Revision whose cheque identity this projects.', nullable=False),
        *identity(immutable=False),
        *shape('check_instrument'),
        sa.CheckConstraint(_one_of('type', CHEQUE_DOCUMENT_TYPES), name='ck_check_instrument_type'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'], ['transactions.id', 'transactions.type'],
                                name='fk_check_instrument_document_type'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
                                ['check_instrument_revisions.transaction_id', 'check_instrument_revisions.revision_id'],
                                name='fk_check_instrument_revision_held'),
        # Two cheques cannot carry one number on one bank account. This is the whole duplicate
        # policy: same-account collisions are refused rather than warned about. It covers the
        # numbers Bookflow issued, and not the ones co0040 carried over from the shared journal
        # series -- a company file can already hold `1001` and `01001` on one account, which is
        # one number written twice, and refusing to open that file would be refusing to tell
        # the person about it.
        sa.Index('uq_check_instrument_number', 'account_id', 'check_number_key', unique=True,
                 sqlite_where=sa.text("origin = 'issued'")),
        sa.Index('ix_check_instruments_sequence', 'account_id', 'check_sequence'),
        description='Current funding account and check number of each cheque, unique within a bank account.')

    return {'money_out_documents': money_out_documents,
            'check_instrument_revisions': check_instrument_revisions,
            'check_instruments': check_instruments}
