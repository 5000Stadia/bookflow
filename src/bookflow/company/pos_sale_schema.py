"""POS sales (co0120, R114 increment 5; design/point-of-sale.md §4, §13).

``pos_sales``: one row per completed POS sale, written in the same transaction as the sales receipt it posted. It
records what a sales receipt has no column for: the ticket (whose key is the sale's operation key, so a retry finds
the sale already posted instead of posting a second), the register, the clerk, and the tender (tendered and change
are facts here, never postings: the receipt debits the register's deposit account for the sale total). Insert-only.
"""
import sqlalchemy as sa

TICKET_PATTERN = r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$'


def define_tables(metadata, C, T):
    sales = T('pos_sales',
        C('id', sa.String(26), 'Stable ULID of this POS sale.', primary_key=True),
        C('created_at', sa.String(32), 'UTC timestamp when the sale was completed.', nullable=False),
        C('created_by', sa.String(26), 'The clerk: user id that completed the sale.', nullable=False),
        C('created_via', sa.String(16), 'Interface the sale was completed through.', nullable=False),
        C('ticket', sa.String(64), "The ticket's key, which is also the sale's operation key: a retry with the same "
          'key returns this sale and never posts a second.', nullable=False),
        C('input_sha256', sa.String(64), 'SHA-256 of the completed ticket as sent, so a retry that changed the ticket '
          'is refused rather than answered with a different sale.', nullable=False),
        C('register_id', sa.String(26), 'Register the sale was rung up on.', sa.ForeignKey('pos_registers.id'),
          nullable=False),
        C('document_type', sa.String(24), "Document the sale posted: sales_receipt for one method, invoice for split tenders.", nullable=False),
        C('document_id', sa.String(26), 'The posted document.', sa.ForeignKey('transactions.id'), nullable=False),
        C('customer_named', sa.Boolean, "Whether a customer was chosen; false when the register's walk-in customer "
          'was used (never shown on the POS screen or the receipt).', nullable=False),
        C('currency', sa.String(3), 'Currency of the amounts.', nullable=False),
        C('total_minor_units', sa.BigInteger, 'Sale total in minor units, as posted.', nullable=False),
        C('tendered_minor_units', sa.BigInteger, 'Amount tendered in minor units.', nullable=False),
        C('change_minor_units', sa.BigInteger, 'Change given in minor units (tendered less total; cash only).',
          nullable=False),
        C('tenders', sa.Text, 'JSON tenders: payment_method_id, kind, amount_minor_units, applied_minor_units, change_minor_units, reference; split tenders also carry payment_id.',
          nullable=False),
        sa.CheckConstraint("document_type IN ('sales_receipt', 'invoice')", name='ck_pos_sales_document_type'),
        sa.CheckConstraint('change_minor_units >= 0 AND tendered_minor_units >= total_minor_units',
                           name='ck_pos_sales_tender'),
        sa.UniqueConstraint('ticket', name='uq_pos_sales_ticket'),
        sa.Index('ix_pos_sales_register_id', 'register_id'),
        sa.Index('ix_pos_sales_document_id', 'document_id'),
        description='Completed point-of-sale sales: the ticket, register, clerk and tender of each sales receipt the '
                    'POS posted. Insert-only.')
    return {'pos_sales': sales}
