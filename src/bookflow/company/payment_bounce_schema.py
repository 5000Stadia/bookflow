"""Bounced customer checks: one row binds a receipt to the documents its return made (co0069).

A bank returns a deposited check. Recording that is one step in the books -- the receipt's
invoices reopen, the returned amount leaves the bank, the bank's fee is expensed and the
customer is billed a fee -- and each of those effects is an ordinary document: a ``payment
unapply``, a ``customer-refund`` that takes the receipt's cash back out of the bank, a register
entry for the bank's fee and an invoice for the customer's fee. This table is the one place that
says those documents belong together, so the receipt can read "bounced on ..." and the pieces can
be found from it.

A row is written once and never changed. The receipt is bounced while the refund it names is
posted: voiding that refund (the normal correction) ends the bounce without touching this row.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    bounces = T('payment_bounces',
        C('id', sa.String(26), 'Stable ULID of this bounced-check record.', primary_key=True),
        C('payment_id', sa.String(26), 'Customer receipt whose check the bank returned.', sa.ForeignKey('transactions.id'), nullable=False),
        C('operation_key', sa.String(128), 'Caller-chosen key of the bounce; asking again with it returns this record instead of bouncing twice.', nullable=False),
        C('refund_id', sa.String(26), 'Customer refund that took the returned amount back out of the bank; the receipt is bounced while it is posted.', sa.ForeignKey('transactions.id'), nullable=False),
        C('bounce_date', sa.String(10), 'Date the check came back, YYYY-MM-DD.', nullable=False),
        C('returned_minor_units', sa.BigInteger, 'Cash the bank took back, in home-currency minor units.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the amounts.', nullable=False),
        C('bank_fee_journal_id', sa.String(26), 'Register entry that expensed the bank\'s fee; null when the bank charged none.', sa.ForeignKey('transactions.id'), nullable=True),
        C('bank_fee_minor_units', sa.BigInteger, 'Bank fee in home-currency minor units; null when none.', nullable=True),
        C('customer_fee_invoice_id', sa.String(26), 'Invoice that billed the customer\'s fee; null when none was charged.', sa.ForeignKey('transactions.id'), nullable=True),
        C('customer_fee_minor_units', sa.BigInteger, 'Fee billed to the customer before tax, in home-currency minor units; null when none.', nullable=True),
        C('reason', sa.String(500), 'Why the check was recorded as returned, as the writer gave it.', nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp the bounce was recorded.', nullable=False),
        C('created_by', sa.String(26), 'Principal that recorded the bounce.', nullable=False),
        C('created_via', sa.String(16), 'Interface the bounce came through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that committed the bounce record.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.CheckConstraint("typeof(returned_minor_units) = 'integer' AND returned_minor_units > 0", name='ck_payment_bounce_returned'),
        sa.CheckConstraint("(bank_fee_journal_id IS NULL) = (bank_fee_minor_units IS NULL)"
                           " AND (bank_fee_minor_units IS NULL OR (typeof(bank_fee_minor_units) = 'integer' AND bank_fee_minor_units > 0))",
                           name='ck_payment_bounce_bank_fee'),
        sa.CheckConstraint("(customer_fee_invoice_id IS NULL) = (customer_fee_minor_units IS NULL)"
                           " AND (customer_fee_minor_units IS NULL OR (typeof(customer_fee_minor_units) = 'integer' AND customer_fee_minor_units > 0))",
                           name='ck_payment_bounce_customer_fee'),
        sa.CheckConstraint("length(trim(reason)) > 0", name='ck_payment_bounce_reason'),
        sa.UniqueConstraint('refund_id', name='uq_payment_bounce_refund'),
        sa.UniqueConstraint('operation_key', name='uq_payment_bounce_operation_key'),
        sa.Index('ix_payment_bounces_payment', 'payment_id', 'bounce_date'),
        description='Bounced customer checks: the receipt, the refund that took the cash back out of the bank, and the fee documents.')
    return {'payment_bounces': bounces}

