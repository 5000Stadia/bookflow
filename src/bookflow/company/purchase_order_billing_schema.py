"""Billing part of a purchase order, and closing an order line by hand (co0084, R153).

``purchase_order_bill_claims`` records what a bill entered from an order took from each
ordered line: an exact quantity of an item line, or an exact amount of an account line. A
bill entered this way also receives what it bills (the anchor's Enter Bills against an order
for goods that came with the bill), so a claim counts against the line's capacity together
with the physical claims receipts make. ``purchase_order_bill_releases`` gives a claim back,
once, when its bill is voided or deleted; nothing is rewritten.

``purchase_order_line_closures`` is the anchor's per-line Closed box: each row closes or
reopens one ordered line, the latest row deciding. A closed line stops counting as on order
and takes no further receipts or bills.

All three are append-only: guards refuse an update or a delete.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    def ident(name, target=None, nullable=False, primary_key=False, doc=None):
        return C(name, sa.String(26), doc or name.replace('_', ' '),
                 *([sa.ForeignKey(target)] if target else []), nullable=nullable, primary_key=primary_key)

    def history():
        return [C('created_at', sa.Text, 'When the row was written.', nullable=False),
                ident('created_by', doc='Who wrote it.'),
                C('created_via', sa.Text, 'Through which interface.', nullable=False),
                ident('audit_event_id', 'audit_events.id', doc='The audit event it belongs to.')]

    claims = T('purchase_order_bill_claims',
        ident('id', primary_key=True),
        ident('order_id', 'purchase_orders.id', doc='Purchase order billed from.'),
        ident('order_line_id', 'purchase_order_line_identities.id', doc='Ordered line billed from.'),
        ident('bill_id', 'transactions.id', doc='Bill that took this part of the line.'),
        ident('bill_revision_id', 'transaction_revisions.id', doc='Bill revision that took it.'),
        C('quantity_microunits', sa.BigInteger, 'Quantity of an item line taken, in microunits; '
          'null for an account line, which is billed by amount.', nullable=True),
        C('amount_minor_units', sa.BigInteger, 'Amount the bill line carries for this part.', nullable=False),
        *history(),
        sa.CheckConstraint("quantity_microunits IS NULL OR (typeof(quantity_microunits)='integer' AND quantity_microunits>0)",
                           name='ck_po_bill_claim_quantity'),
        sa.CheckConstraint("typeof(amount_minor_units)='integer' AND amount_minor_units>=0",
                           name='ck_po_bill_claim_amount'),
        sa.Index('ix_po_bill_claims_line', 'order_line_id'),
        sa.Index('ix_po_bill_claims_bill', 'bill_id'),
        sa.Index('ix_po_bill_claims_order', 'order_id'),
        description='What a bill entered from a purchase order took from each ordered line.')
    releases = T('purchase_order_bill_releases',
        ident('id', primary_key=True),
        ident('claim_id', 'purchase_order_bill_claims.id', doc='Claim given back.'),
        *history(), sa.UniqueConstraint('claim_id'),
        description='A claim given back once, when its bill was voided or deleted.')
    closures = T('purchase_order_line_closures',
        ident('id', primary_key=True),
        ident('order_id', 'purchase_orders.id', doc='Purchase order the line belongs to.'),
        ident('order_line_id', 'purchase_order_line_identities.id', doc='Ordered line closed or reopened.'),
        C('closed', sa.Boolean, 'True closes the line; false reopens it.', nullable=False),
        C('sequence', sa.Integer, 'Order of closures on this line; the highest decides.', nullable=False),
        *history(),
        sa.UniqueConstraint('order_line_id', 'sequence'),
        description='Ordered lines closed or reopened by hand; the latest row per line decides.')
    return {'purchase_order_bill_claims': claims, 'purchase_order_bill_releases': releases,
            'purchase_order_line_closures': closures}
