"""Customer prepayments: what each held receipt is for, and which receipt each draw came from (co0076).

A prepayment is money a customer paid before the work bills: a deposit, a down payment, a
retainer. It is held on the Customer Prepayments liability until an invoice uses it or it is
paid back. Every effect is an ordinary document with its own number (a sales receipt takes the
money, a credit memo draws it, `customer-credit apply` puts it on an invoice, a customer refund
pays it back); these two tables are the one place that says what the documents are for.

``prepayment_links`` says who or what a receipt is held for: its customer, or one customer-work
document (an accepted estimate, a work order, or later a sales order). Moving the money appends a
link that supersedes the old one, so a receipt's current link is the one nothing supersedes.

``prepayment_draws`` names, for each Customer Prepayment line of a drawing credit memo, the
receipt it draws from and the invoice or refund it went to. A draw counts while its credit memo
is posted; its amount is that line's amount in the memo's current revision.

Both are written once and never changed or deleted (triggers refuse it).
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    def provenance(what):
        return [C('created_at', sa.String(32), f'UTC timestamp the {what} was recorded.', nullable=False),
                C('created_by', sa.String(26), f'Principal that recorded the {what}.', nullable=False),
                C('created_via', sa.String(16), f'Interface the {what} came through.', nullable=False),
                C('audit_event_id', sa.String(26), f'Audit event that committed the {what}.',
                  sa.ForeignKey('audit_events.id'), nullable=False)]

    links = T('prepayment_links',
        C('id', sa.String(26), 'Stable ULID of this link.', primary_key=True),
        C('receipt_id', sa.String(26), 'Sales receipt whose Customer Prepayment line took the money.',
          sa.ForeignKey('transactions.id'), nullable=False),
        C('customer_id', sa.String(26), 'Customer or job the money is held for: the receipt\'s own.',
          sa.ForeignKey('customers.id'), nullable=False),
        C('work_document_id', sa.String(26), 'Customer-work document the money is held on (an accepted estimate, a '
          'work order or a sales order); null when it is held for the customer.', sa.ForeignKey('work_documents.id'),
          nullable=True),
        C('supersedes_link_id', sa.String(26), 'Earlier link of the same receipt this one replaces; null for the '
          'receipt\'s first.', sa.ForeignKey('prepayment_links.id'), nullable=True),
        *provenance('link'),
        sa.UniqueConstraint('supersedes_link_id', name='uq_prepayment_link_supersedes'),
        sa.Index('ix_prepayment_links_receipt', 'receipt_id'),
        sa.Index('ix_prepayment_links_document', 'work_document_id'),
        sa.Index('ix_prepayment_links_customer', 'customer_id'),
        description='What each customer prepayment receipt is held for; the current link is the one nothing supersedes.')

    draws = T('prepayment_draws',
        C('id', sa.String(26), 'Stable ULID of this draw.', primary_key=True),
        C('credit_memo_id', sa.String(26), 'Credit memo whose Customer Prepayment line draws the money.',
          sa.ForeignKey('transactions.id'), nullable=False),
        C('line_id', sa.String(26), 'Stable identity of that credit memo line; its amount in the memo\'s current '
          'revision is the amount drawn.', nullable=False),
        C('receipt_id', sa.String(26), 'Sales receipt the money is drawn from.', sa.ForeignKey('transactions.id'),
          nullable=False),
        C('work_document_id', sa.String(26), 'Customer-work document the money was held on when drawn; null when '
          'it was held for the customer.', sa.ForeignKey('work_documents.id'), nullable=True),
        C('invoice_id', sa.String(26), 'Invoice the draw paid; null when it was refunded.',
          sa.ForeignKey('transactions.id'), nullable=True),
        C('refund_id', sa.String(26), 'Customer refund that paid the draw back; null when it paid an invoice.',
          sa.ForeignKey('transactions.id'), nullable=True),
        *provenance('draw'),
        sa.UniqueConstraint('credit_memo_id', 'line_id', name='uq_prepayment_draw_line'),
        sa.CheckConstraint('(invoice_id IS NULL) <> (refund_id IS NULL)', name='ck_prepayment_draw_target'),
        sa.Index('ix_prepayment_draws_receipt', 'receipt_id'),
        sa.Index('ix_prepayment_draws_invoice', 'invoice_id'),
        sa.Index('ix_prepayment_draws_document', 'work_document_id'),
        description='Each Customer Prepayment credit memo line: the receipt it draws from and the invoice or refund it went to.')
    return {'prepayment_links': links, 'prepayment_draws': draws}
