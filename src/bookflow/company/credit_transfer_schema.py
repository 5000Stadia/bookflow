"""Customer credit moved from one job to another: one row binds the pair of documents (co0081).

The anchor has no ready-made transfer: its guides move a job's credit with a pair of entries
through a clearing account, a charge on the job that had the credit (which that credit then pays)
and a credit on the job that gets it. Bookflow writes exactly that pair as ordinary documents, a
charge invoice and a credit memo on one Credit Transfer item, and this table is the one place that
says they belong together, so the transfer can be found, shown and voided as one thing.

A row is written once and never changed. The transfer stands while its charge invoice is posted:
voiding the transfer (`customer-credit transfer-void`) voids both documents and leaves this row.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    transfers = T('customer_credit_transfers',
        C('id', sa.String(26), 'Stable ULID of this credit transfer.', primary_key=True),
        C('operation_key', sa.String(128), 'Caller-chosen key; asking again with it returns this transfer instead of moving the credit twice.', nullable=False),
        C('from_party_id', sa.String(26), 'Customer or job the credit was moved from.', sa.ForeignKey('customers.id'), nullable=False),
        C('to_party_id', sa.String(26), 'Customer or job the credit was moved to.', sa.ForeignKey('customers.id'), nullable=False),
        C('transfer_date', sa.String(10), 'Date of the transfer, YYYY-MM-DD.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Credit moved, in home-currency minor units.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the amount.', nullable=False),
        C('charge_invoice_id', sa.String(26), 'Invoice that charged the source job, paid by the credit it had.', sa.ForeignKey('transactions.id'), nullable=False),
        C('credit_memo_id', sa.String(26), 'Credit memo that gave the credit to the target job.', sa.ForeignKey('transactions.id'), nullable=False),
        C('reason', sa.String(500), 'Why the credit was moved, as the writer gave it.', nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp the transfer was recorded.', nullable=False),
        C('created_by', sa.String(26), 'Principal that recorded the transfer.', nullable=False),
        C('created_via', sa.String(16), 'Interface the transfer came through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that committed the transfer record.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0", name='ck_credit_transfer_amount'),
        sa.CheckConstraint('from_party_id <> to_party_id', name='ck_credit_transfer_parties'),
        sa.CheckConstraint('length(trim(reason)) > 0', name='ck_credit_transfer_reason'),
        sa.UniqueConstraint('operation_key', name='uq_credit_transfer_operation_key'),
        sa.UniqueConstraint('charge_invoice_id', name='uq_credit_transfer_charge'),
        sa.UniqueConstraint('credit_memo_id', name='uq_credit_transfer_credit_memo'),
        sa.Index('ix_credit_transfers_from', 'from_party_id', 'transfer_date'),
        sa.Index('ix_credit_transfers_to', 'to_party_id', 'transfer_date'),
        description='Customer credit moved between jobs of one customer: the charge on the source and the credit memo on the target.')
    return {'customer_credit_transfers': transfers}
