"""Shipments and the books (co0102, R181 increment 1; design/pick-pack-ship.md §3, §6, §8).

A **shipment** is a dated stock document of its own: it issues the units an order line ships, at the replay's
cost, posting Dr Shipped Not Invoiced, Cr Inventory Asset in a journal entry the inventory ledger owns
(``inventory_documents`` kind ``shipment``). The invoice that later bills it moves no stock; the cost waits in
Shipped Not Invoiced until then and moves to cost of goods sold by a derived balance (``shipment_recognitions``).

- ``shipments``: the immutable header -- number, order, customer and the journal entry it posts as.
- ``shipment_revisions``: append-only; each restatement or void is a new revision. The newest is current, its
  ``revision_number`` is the shipment's version and its ``status`` the shipment's.
- ``shipment_lines``: immutable, per revision. ``line_identity`` is stable across revisions; ``movement_id`` is the
  issue the line stands behind in that revision.
- ``shipment_billings``: immutable. An invoice line (``transaction_id``, ``line_id``) that billed shipped units of one
  shipment line. Live while that invoice is posted and its current revision still carries the line.
- ``shipment_recognitions``: immutable. Each Shipped Not Invoiced -> cost of goods sold movement posted for one
  shipment line, dated, signed (positive moves cost into cost of goods sold), in the journal entry
  (``inventory_documents`` kind ``shipment_cost``) that carries it.
"""
import sqlalchemy as sa

STATUSES = ('draft', 'shipped', 'cancelled', 'voided')
POSITIONS = ('before', 'after')
WHEN_SHIPPING = ('ask', 'always', 'never')
ROLE = 'shipped_not_invoiced'
SEQUENCE = 'shipment'


def define_tables(metadata, C, T):
    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    def provenance(what):
        return (C('created_at', sa.String(32), f'UTC time this {what} was recorded.', nullable=False),
                C('created_by', sa.String(26), f'Company principal that recorded this {what}.', nullable=False),
                C('created_via', sa.String(16), f'Interface that recorded this {what}.', nullable=False),
                ident('audit_event_id', f'Audit event that committed this {what}.', 'audit_events.id'))

    shipments = T('shipments',
        ident('id', 'Stable ULID of this shipment.', primary_key=True),
        C('number', sa.String(64), 'Shipment number, from the shipment sequence.', nullable=False),
        ident('sales_order_id', 'Sales order the shipment fills.', 'work_documents.id'),
        ident('customer_id', 'Customer or job the goods went to: the order\'s.', 'customers.id'),
        ident('transaction_id', 'Journal entry the shipment posts as (inventory_documents kind shipment).',
              'transactions.id'),
        *provenance('shipment'),
        sa.UniqueConstraint('number', name='uq_shipments_number'),
        sa.UniqueConstraint('transaction_id', name='uq_shipments_transaction'),
        sa.Index('ix_shipments_order', 'sales_order_id'),
        sa.Index('ix_shipments_customer', 'customer_id'),
        description='Shipments: a dated stock document issuing an order\'s units ahead of the invoice. Immutable; '
                    'its state is its newest revision.')

    revisions = T('shipment_revisions',
        ident('id', 'Stable ULID of this revision.', primary_key=True),
        ident('shipment_id', 'Shipment this revision restates.', 'shipments.id'),
        C('revision_number', sa.Integer, 'One-based revision number; the newest is current and is the version.',
          nullable=False),
        C('status', sa.String(10), 'shipped or voided (draft and cancelled arrive with packing).', nullable=False),
        C('ship_date', sa.String(10), 'Date the stock left, YYYY-MM-DD: the entry\'s date.', nullable=False),
        C('count_position', sa.String(8), 'before (the default) or after a count of its site on the same date.',
          nullable=False),
        ident('site_id', 'Inventory site the shipment leaves.', 'inventory_sites.id'),
        ident('ship_method_id', 'Ship Via list entry; null when none.', 'ship_methods.id', nullable=True),
        C('carrier', sa.Text, 'Carrier or service, as text; null when blank.', nullable=True),
        C('tracking_numbers', sa.Text, 'JSON array of tracking numbers as text, printed never fetched.', nullable=False),
        C('ship_to', sa.Text, 'Ship-to address as text; null when the order\'s.', nullable=True),
        C('memo', sa.Text, 'Memo as entered; null when blank.', nullable=True),
        C('reason', sa.Text, 'Why this revision was written, as the caller gave it.', nullable=True),
        *provenance('shipment revision'),
        sa.CheckConstraint(f'status IN ({quoted(STATUSES)})', name='ck_shipment_revision_status'),
        sa.CheckConstraint(f'count_position IN ({quoted(POSITIONS)})', name='ck_shipment_revision_position'),
        sa.CheckConstraint("ship_date LIKE '____-__-__'", name='ck_shipment_revision_date'),
        sa.CheckConstraint("typeof(revision_number) = 'integer' AND revision_number > 0",
                           name='ck_shipment_revision_number'),
        sa.CheckConstraint("json_valid(tracking_numbers) AND json_type(tracking_numbers) = 'array'",
                           name='ck_shipment_revision_tracking'),
        sa.UniqueConstraint('shipment_id', 'revision_number', name='uq_shipment_revision_number'),
        description='Append-only shipment revisions: each restatement or void adds one; the newest is current.')

    lines = T('shipment_lines',
        ident('id', 'Stable ULID of this immutable line row.', primary_key=True),
        ident('revision_id', 'Shipment revision this line belongs to.', 'shipment_revisions.id'),
        ident('shipment_id', 'Shipment of the revision.', 'shipments.id'),
        ident('line_identity', 'Stable identity of the shipment line across revisions.'),
        C('position', sa.Integer, 'One-based position within the revision.', nullable=False),
        ident('root_document_id', 'Billing root document of the order line shipped (the order, or the estimate '
                                  'it came from).'),
        ident('root_line_id', 'Billing root line identity of the order line shipped.'),
        ident('item_id', 'Item shipped.', 'items.id'),
        C('quantity_microunits', sa.BigInteger, 'Quantity shipped in the item\'s base unit, microunits.', nullable=False),
        ident('bin_id', 'Bin the units left from as the line named it; null when none was named.',
              'inventory_bins.id', nullable=True),
        C('description', sa.Text, 'Line description: the order line\'s.', nullable=True),
        ident('asset_account_id', 'Inventory asset account the units left.', 'accounts.id'),
        ident('cogs_account_id', 'Cost of goods sold account the cost moves to when billed.', 'accounts.id'),
        ident('class_id', 'Class of the order line; null when none.', 'classes.id', nullable=True),
        ident('movement_id', 'Issue movement this line stands behind in this revision; null on a void.',
              'inventory_movements.id', nullable=True),
        *provenance('shipment line'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0",
                           name='ck_shipment_line_quantity'),
        sa.UniqueConstraint('revision_id', 'position', name='uq_shipment_line_position'),
        sa.Index('ix_shipment_lines_identity', 'line_identity'),
        sa.Index('ix_shipment_lines_root', 'root_document_id', 'root_line_id'),
        sa.Index('ix_shipment_lines_movement', 'movement_id'),
        description='Immutable shipment lines per revision; line_identity is stable across revisions.')

    billings = T('shipment_billings',
        ident('id', 'Stable ULID of this billing.', primary_key=True),
        ident('transaction_id', 'Invoice that billed the shipped units.', 'transactions.id'),
        ident('line_id', 'Stable identity of the invoice line that billed them.', 'document_line_identities.id'),
        ident('shipment_id', 'Shipment billed.', 'shipments.id'),
        ident('shipment_line_identity', 'Stable identity of the shipment line billed.'),
        C('quantity_microunits', sa.BigInteger, 'Base quantity of that shipment line this invoice line billed.',
          nullable=False),
        *provenance('shipment billing'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0",
                           name='ck_shipment_billing_quantity'),
        sa.UniqueConstraint('transaction_id', 'line_id', 'shipment_line_identity', name='uq_shipment_billing'),
        sa.Index('ix_shipment_billings_line', 'shipment_line_identity'),
        sa.Index('ix_shipment_billings_shipment', 'shipment_id'),
        description='Immutable: an invoice line that billed shipped units, live while that invoice is posted and '
                    'its current revision carries the line.')

    recognitions = T('shipment_recognitions',
        ident('id', 'Stable ULID of this recognition.', primary_key=True),
        ident('shipment_id', 'Shipment whose line it is.', 'shipments.id'),
        ident('shipment_line_identity', 'Stable identity of the shipment line.'),
        ident('transaction_id', 'Journal entry carrying it (inventory_documents kind shipment_cost).', 'transactions.id'),
        C('effective_date', sa.String(10), 'Date it is recognised, YYYY-MM-DD.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Signed cost moved from Shipped Not Invoiced to cost of goods sold.',
          nullable=False),
        C('currency', sa.String(3), 'Home currency of the amount.', nullable=False),
        *provenance('shipment recognition'),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units != 0",
                           name='ck_shipment_recognition_amount'),
        sa.CheckConstraint("effective_date LIKE '____-__-__'", name='ck_shipment_recognition_date'),
        sa.Index('ix_shipment_recognitions_line', 'shipment_line_identity', 'effective_date'),
        description='Immutable: cost moved between Shipped Not Invoiced and cost of goods sold for one shipment '
                    'line, at its date, as the derived balance requires.')
    return {'shipments': shipments, 'shipment_revisions': revisions, 'shipment_lines': lines,
            'shipment_billings': billings, 'shipment_recognitions': recognitions}


def company_info_column(C):
    return C('invoice_when_shipping', sa.String(8), 'After a shipment: ask (the default), always or never offer an '
             'invoice for it.', sa.CheckConstraint(f"invoice_when_shipping IN ('ask', 'always', 'never')",
                                                   name='ck_company_info_invoice_when_shipping'),
             nullable=False, server_default='ask')


TABLES = ('shipments', 'shipment_revisions', 'shipment_lines', 'shipment_billings', 'shipment_recognitions')
# Every shipment row is immutable: revisions, billings and recognitions append.
GUARDS = tuple(
    statement for table in TABLES for statement in (
        f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END",
        f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END"))
