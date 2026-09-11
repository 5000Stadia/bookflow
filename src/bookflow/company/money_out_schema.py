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
"""

import sqlalchemy as sa

# The three documents this marker distinguishes, in their stored spelling.
KINDS = ('check', 'card_charge', 'transfer')


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

    return {'money_out_documents': money_out_documents}
