"""Receipt ownership, physical order claims, and separate financial interval claims."""
import sqlalchemy as sa


def define_tables(metadata, column, table, common):
    C, T = column, table

    def ident(name, target=None, nullable=False, primary_key=False):
        return C(name, sa.String(26), name.replace('_', ' '),
                 *([sa.ForeignKey(target)] if target else []), nullable=nullable, primary_key=primary_key)

    def integer(name):
        return C(name, sa.BigInteger, name.replace('_', ' '), nullable=False)

    def text(name):
        return C(name, sa.Text, name.replace('_', ' '), nullable=False)

    def exact(name, positive=False):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} {'>' if positive else '>='} 0",
                                  name='ck_receiving_' + name)

    def history():
        return [text('created_at'), ident('created_by'), text('created_via'),
                ident('audit_event_id', 'audit_events.id')]

    receipts = T('item_receipts', *common(), text('number'), text('status'),
        ident('current_revision_id'), ident('transaction_id', 'transactions.id'),
        sa.UniqueConstraint('number'), sa.UniqueConstraint('transaction_id'),
        sa.CheckConstraint("status IN ('posted', 'voided')", name='ck_receipt_status'),
        sa.ForeignKeyConstraint(['id', 'current_revision_id'],
            ['item_receipt_revisions.receipt_id', 'item_receipt_revisions.id'],
            deferrable=True, initially='DEFERRED'),
        description='Owning item receipts; their accounting journal is never a payable bill.')
    revisions = T('item_receipt_revisions', ident('id', primary_key=True),
        ident('receipt_id', 'item_receipts.id'), integer('revision_number'),
        ident('financial_revision_id', 'transaction_revisions.id'), text('date'),
        ident('vendor_id', 'vendors.id'), ident('ap_account_id', 'accounts.id'),
        text('snapshot'), *history(),
        sa.UniqueConstraint('receipt_id', 'id'), sa.UniqueConstraint('receipt_id', 'revision_number'),
        exact('revision_number', True),
        sa.CheckConstraint("json_valid(snapshot) AND json_type(snapshot)='object'", name='ck_receipt_snapshot'),
        description='Immutable captured commercial revisions; metadata can retain physical identities.')
    lines = T('item_receipt_lines', ident('id', 'document_lines.id', primary_key=True),
        ident('receipt_id', 'item_receipts.id'), ident('financial_revision_id', 'transaction_revisions.id'),
        ident('movement_id', 'inventory_movements.id'), ident('item_id', 'items.id'),
        integer('quantity_microunits'), integer('value_minor_units'),
        C('shipping_minor_units', sa.BigInteger, 'Captured shipping included in received value; product value is the remainder.', nullable=False, server_default='0'),
        sa.CheckConstraint("typeof(shipping_minor_units)='integer' AND shipping_minor_units>=0 AND shipping_minor_units<=value_minor_units", name='ck_receipt_shipping'), text('snapshot'), *history(),
        sa.UniqueConstraint('movement_id'), exact('quantity_microunits', True), exact('value_minor_units'),
        sa.CheckConstraint("json_valid(snapshot) AND json_type(snapshot)='object'", name='ck_receipt_line_snapshot'),
        description='Original physical line quantity and value; the financial interval basis never changes.')
    po_claims = T('purchase_order_receipt_claims', ident('id', primary_key=True),
        ident('order_line_id', 'purchase_order_line_identities.id'),
        ident('order_revision_id', 'purchase_order_revisions.id'),
        ident('receipt_line_id', 'item_receipt_lines.id'), integer('quantity_microunits'), *history(),
        sa.UniqueConstraint('receipt_line_id'), exact('quantity_microunits', True),
        description='Physical quantity received against an exact ordered line, independently of bills.')
    po_releases = T('purchase_order_receipt_releases', ident('id', primary_key=True),
        ident('claim_id', 'purchase_order_receipt_claims.id'), *history(),
        sa.UniqueConstraint('claim_id'), description='Exact physical-claim releases on allowed receipt correction or void.')
    claims = T('receipt_bill_claims', ident('id', primary_key=True),
        ident('receipt_line_id', 'item_receipt_lines.id'),
        ident('bill_id', 'transactions.id'), ident('bill_revision_id', 'transaction_revisions.id'),
        ident('bill_line_id', 'document_lines.id'), integer('start_microunits'), integer('end_microunits'),
        integer('original_minor_units'), integer('billed_minor_units'),
        C('shipping_minor_units', sa.BigInteger, 'Shipping retained by this exact interval, included in both original and billed totals.', nullable=False, server_default='0'),
        sa.CheckConstraint("typeof(shipping_minor_units)='integer' AND shipping_minor_units>=0 AND shipping_minor_units<=original_minor_units AND shipping_minor_units<=billed_minor_units", name='ck_receipt_claim_shipping'), *history(),
        exact('start_microunits'), exact('end_microunits', True), exact('original_minor_units'), exact('billed_minor_units'),
        sa.CheckConstraint('end_microunits > start_microunits', name='ck_receipt_bill_interval'),
        sa.Index('ix_receipt_bill_claims_line', 'receipt_line_id'),
        sa.Index('ix_receipt_bill_claims_bill', 'bill_id'),
        description='Disjoint immutable financial claims against original received quantity intervals.')
    releases = T('receipt_bill_releases', ident('id', primary_key=True),
        ident('claim_id', 'receipt_bill_claims.id'), *history(), sa.UniqueConstraint('claim_id'),
        description='Release exactly the interval and original value this bill previously claimed.')
    adjustments = T('receipt_bill_adjustments', ident('id', primary_key=True),
        ident('bill_id', 'transactions.id'), ident('bill_revision_id', 'transaction_revisions.id'),
        ident('receipt_line_id', 'item_receipt_lines.id'),
        ident('movement_id', 'inventory_movements.id'), *history(), sa.UniqueConstraint('movement_id'),
        description='Bill-owned acquisition-cost corrections, separate from the physical receipt and AP transfer.')
    return {t.name: t for t in (receipts, revisions, lines, po_claims, po_releases, claims, releases, adjustments)}
