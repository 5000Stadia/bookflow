"""Printing cheques: the print queue, print runs, spoiled numbers and alignment (co0092, R152).

A cheque saved "to be printed" has no number yet. ``check_print_marks`` records that it was saved
that way, once, when it was written; nothing else about the queue is stored. Whether a cheque is
*in* the queue is derived every time it is read: it was marked, it still carries no number in
``check_instruments``, it stands posted, and no print run is holding it. So a number typed on a
correction, a successful print, a void or a delete each take it out of the queue without a second
fact to keep in step, and a misprinted cheque returns to it by itself.

``check_print_runs`` is one press of Print: the bank account, the first number the person confirmed
was loaded in the printer, the check style, the alignment offsets in force and how many cheques
were left on a partial first sheet. ``check_print_run_items`` is the number each cheque was given
in that run, in the order it printed. Both are immutable. ``check_print_outcomes`` answers the
anchor's "Did checks print OK?" for each item, once: printed, failed (the paper was spoiled and
the cheque goes back to the queue for a fresh number) or cancelled (nothing reached the printer).
An item with no outcome yet is on paper in the printer as far as the books know, so its number is
occupied until somebody answers.

``check_spoiled_numbers`` is every number that went onto a piece of paper that was then thrown
away: a misprint, or the old number of a cheque reprinted under a new one. ``report
missing-checks`` reads it, so a spoiled number is explained rather than counted as a hole, and the
allocator never hands it out again.

``check_print_settings`` is one row per bank account: the style it prints on by default and the
fine-alignment offsets, in hundredths of an inch, the anchor's Printer Setup keeps.

Every table here is append-only except the settings, which carry a version like any record.
"""
import sqlalchemy as sa

from bookflow.company.money_out_schema import CHEQUE_DOCUMENT_TYPES

STYLES = ('voucher', 'standard', 'wallet')
OUTCOMES = ('printed', 'failed', 'cancelled')
RUN_KINDS = ('batch', 'reprint')
SPOIL_REASONS = ('misprinted', 'reprinted')
# Fine alignment moves the whole form at most two inches either way; anything past that is a
# different form, not an alignment.
OFFSET_LIMIT = 200


def _one_of(column, values):
    return f'{column} IN (' + ', '.join(f"'{value}'" for value in values) + ')'


def define_tables(metadata, C, T, common):
    def ident(name, target=None, nullable=False, primary_key=False, doc=None):
        return C(name, sa.String(26), doc or name.replace('_', ' '),
                 *([sa.ForeignKey(target)] if target else []), nullable=nullable, primary_key=primary_key)

    def history():
        return [C('created_at', sa.String(32), 'UTC time the row was written.', nullable=False),
                ident('created_by', doc='Company principal that wrote it.'),
                C('created_via', sa.String(16), 'Interface it was written through.', nullable=False),
                ident('audit_event_id', 'audit_events.id', doc='Audit event it belongs to.')]

    def number(prefix=''):
        return [C(f'{prefix}check_number', sa.String(64), 'Number as printed on the face of the cheque.', nullable=False),
                C(f'{prefix}check_number_key', sa.String(64), 'Canonical form compared for uniqueness.', nullable=False),
                C(f'{prefix}check_sequence', sa.BigInteger, 'The number as an integer; printed numbers always have a place in a run.', nullable=False)]

    marks = T('check_print_marks',
        ident('transaction_id', primary_key=True, doc='Cheque saved to be printed.'),
        C('type', sa.String(32), 'Transaction type of the cheque: journal_entry (a check), bill_payment, '
                                 'sales_tax_payment or customer_refund (co0096).', nullable=False),
        *history(),
        sa.CheckConstraint(_one_of('type', CHEQUE_DOCUMENT_TYPES), name='ck_check_print_mark_type'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'], ['transactions.id', 'transactions.type'],
                                name='fk_check_print_mark_document'),
        description='Cheques saved to be printed (Print Later); queue membership is derived from these.')

    runs = T('check_print_runs',
        ident('id', primary_key=True, doc='Stable ULID of the print run.'),
        ident('account_id', 'accounts.id', doc='Bank account whose cheques were printed.'),
        C('kind', sa.String(16), 'batch (Print Checks over the queue) or reprint (one cheque printed again).', nullable=False),
        C('style', sa.String(16), 'Check style: voucher, standard or wallet.', nullable=False),
        C('first_number', sa.String(64), 'First number the person confirmed was loaded in the printer.', nullable=False),
        C('checks_on_first_page', sa.Integer, 'Cheques left on a partial first sheet of standard or wallet checks; 3 is a full sheet.', nullable=False),
        C('offset_x_hundredths', sa.Integer, 'Horizontal fine alignment in force, hundredths of an inch; positive moves right.', nullable=False),
        C('offset_y_hundredths', sa.Integer, 'Vertical fine alignment in force, hundredths of an inch; positive moves up.', nullable=False),
        *history(),
        sa.CheckConstraint(_one_of('kind', RUN_KINDS), name='ck_check_print_run_kind'),
        sa.CheckConstraint(_one_of('style', STYLES), name='ck_check_print_run_style'),
        sa.CheckConstraint('checks_on_first_page BETWEEN 1 AND 3', name='ck_check_print_run_first_page'),
        sa.CheckConstraint(f'offset_x_hundredths BETWEEN -{OFFSET_LIMIT} AND {OFFSET_LIMIT} AND '
                           f'offset_y_hundredths BETWEEN -{OFFSET_LIMIT} AND {OFFSET_LIMIT}', name='ck_check_print_run_offsets'),
        sa.Index('ix_check_print_runs_account', 'account_id', 'created_at'),
        description='Each press of Print: account, first number, style, alignment and partial sheet.')

    items = T('check_print_run_items',
        ident('id', primary_key=True, doc='Stable ULID of the item.'),
        ident('run_id', 'check_print_runs.id', doc='Print run this cheque printed in.'),
        ident('transaction_id', 'transactions.id', doc='Cheque printed.'),
        C('position', sa.Integer, 'Order the cheque printed in within the run, from 1.', nullable=False),
        *number(),
        *history(),
        sa.UniqueConstraint('run_id', 'position', name='uq_check_print_item_position'),
        sa.CheckConstraint('position >= 1', name='ck_check_print_item_position'),
        sa.CheckConstraint("typeof(check_sequence) = 'integer' AND check_sequence > 0", name='ck_check_print_item_sequence'),
        sa.Index('ix_check_print_items_transaction', 'transaction_id'),
        sa.Index('ix_check_print_items_number', 'check_number_key'),
        description='The number each cheque was given in a print run, in print order.')

    outcomes = T('check_print_outcomes',
        ident('item_id', 'check_print_run_items.id', primary_key=True, doc='Print run item answered.'),
        C('outcome', sa.String(16), 'printed, failed (spoiled; back to the queue) or cancelled (never reached paper).', nullable=False),
        *history(),
        sa.CheckConstraint(_one_of('outcome', OUTCOMES), name='ck_check_print_outcome'),
        description='The answer to "Did checks print OK?" for each print run item, given once.')

    spoiled = T('check_spoiled_numbers',
        ident('id', primary_key=True, doc='Stable ULID of the spoiled number.'),
        ident('account_id', 'accounts.id', doc='Bank account whose chequebook the paper came from.'),
        *number(),
        ident('transaction_id', 'transactions.id', doc='Cheque the spoiled paper was printed for.'),
        ident('run_id', 'check_print_runs.id', doc='Print run that put the number on paper, or the reprint that replaced it.'),
        C('reason', sa.String(16), 'misprinted (the run failed) or reprinted (the cheque was printed again under a new number).', nullable=False),
        *history(),
        sa.CheckConstraint(_one_of('reason', SPOIL_REASONS), name='ck_check_spoiled_reason'),
        sa.CheckConstraint("typeof(check_sequence) = 'integer' AND check_sequence > 0", name='ck_check_spoiled_sequence'),
        sa.Index('ix_check_spoiled_numbers_account', 'account_id', 'check_number_key'),
        description='Numbers printed on paper that was thrown away; never handed out again.')

    settings = T('check_print_settings',
        *common(),
        ident('account_id', 'accounts.id', doc='Bank account these printer settings belong to.'),
        C('style', sa.String(16), 'Default check style: voucher, standard or wallet.', nullable=False),
        C('offset_x_hundredths', sa.Integer, 'Horizontal fine alignment, hundredths of an inch; positive moves right.', nullable=False),
        C('offset_y_hundredths', sa.Integer, 'Vertical fine alignment, hundredths of an inch; positive moves up.', nullable=False),
        sa.UniqueConstraint('account_id', name='uq_check_print_settings_account'),
        sa.CheckConstraint(_one_of('style', STYLES), name='ck_check_print_settings_style'),
        sa.CheckConstraint(f'offset_x_hundredths BETWEEN -{OFFSET_LIMIT} AND {OFFSET_LIMIT} AND '
                           f'offset_y_hundredths BETWEEN -{OFFSET_LIMIT} AND {OFFSET_LIMIT}', name='ck_check_print_settings_offsets'),
        description='Per bank account check printer setup: default style and fine alignment.')

    return {'check_print_marks': marks, 'check_print_runs': runs, 'check_print_run_items': items,
            'check_print_outcomes': outcomes, 'check_spoiled_numbers': spoiled,
            'check_print_settings': settings}


APPEND_ONLY = ('check_print_marks', 'check_print_runs', 'check_print_run_items', 'check_print_outcomes',
               'check_spoiled_numbers')
