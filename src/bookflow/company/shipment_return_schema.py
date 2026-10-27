"""Shipment returns (co0103, R181 increment 2; design/pick-pack-ship.md §6, §8).

A **shipment return** brings back units a shipment took that no invoice has billed yet. It is a small dated stock
document of its own, so the shipment's history stays as shipped: one ``receipt`` movement per line naming the
shipment line's issue (``returns_movement_id``), worth the replay's share of that issue, posted Dr Inventory Asset,
Cr Shipped Not Invoiced in a journal entry the inventory ledger owns (``inventory_documents`` kind
``shipment_return``).

- ``shipment_returns``: the immutable header -- number, shipment, order, customer, date, whether the units are to
  ship again (``reopen``) or were cancelled on the order, and the journal entry it posts as.
- ``shipment_return_revisions``: append-only; revision 1 is ``returned``, a void appends ``voided``. The newest is
  current, its ``revision_number`` the return's version.
- ``shipment_return_lines``: immutable. The shipment line given back (``shipment_line_identity``), the base
  quantity, and the receipt movement it posted.
"""
import sqlalchemy as sa

STATUSES = ('returned', 'voided')
SEQUENCE = 'shipment_return'


def define_tables(metadata, C, T):
    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    def provenance(what):
        return (C('created_at', sa.String(32), f'UTC time this {what} was recorded.', nullable=False),
                C('created_by', sa.String(26), f'Company principal that recorded this {what}.', nullable=False),
                C('created_via', sa.String(16), f'Interface that recorded this {what}.', nullable=False),
                ident('audit_event_id', f'Audit event that committed this {what}.', 'audit_events.id'))

    returns = T('shipment_returns',
        ident('id', 'Stable ULID of this shipment return.', primary_key=True),
        C('number', sa.String(64), 'Return number, from the shipment_return sequence.', nullable=False),
        ident('shipment_id', 'Shipment whose units came back.', 'shipments.id'),
        ident('sales_order_id', 'Sales order of that shipment.', 'work_documents.id'),
        ident('customer_id', 'Customer or job that sent the units back: the shipment\'s.', 'customers.id'),
        C('return_date', sa.String(10), 'Date the units came back into stock, YYYY-MM-DD: the entry\'s date.',
          nullable=False),
        C('reopen', sa.Boolean, 'True: the units are to ship again on the order; false: they were cancelled on it.',
          nullable=False),
        ident('transaction_id', 'Journal entry the return posts as (inventory_documents kind shipment_return).',
              'transactions.id'),
        *provenance('shipment return'),
        sa.CheckConstraint("return_date LIKE '____-__-__'", name='ck_shipment_return_date'),
        sa.UniqueConstraint('number', name='uq_shipment_returns_number'),
        sa.UniqueConstraint('transaction_id', name='uq_shipment_returns_transaction'),
        sa.Index('ix_shipment_returns_shipment', 'shipment_id'),
        description='Shipment returns: units a shipment took, not yet invoiced, back into stock. Immutable; its '
                    'state is its newest revision.')

    revisions = T('shipment_return_revisions',
        ident('id', 'Stable ULID of this revision.', primary_key=True),
        ident('return_id', 'Shipment return this revision restates.', 'shipment_returns.id'),
        C('revision_number', sa.Integer, 'One-based revision number; the newest is current and is the version.',
          nullable=False),
        C('status', sa.String(10), 'returned, or voided.', nullable=False),
        C('reason', sa.Text, 'Why this revision was written, as the caller gave it.', nullable=True),
        *provenance('shipment return revision'),
        sa.CheckConstraint("status IN ('returned', 'voided')", name='ck_shipment_return_revision_status'),
        sa.CheckConstraint("typeof(revision_number) = 'integer' AND revision_number > 0",
                           name='ck_shipment_return_revision_number'),
        sa.UniqueConstraint('return_id', 'revision_number', name='uq_shipment_return_revision_number'),
        description='Append-only shipment return revisions: returned, then voided; the newest is current.')

    lines = T('shipment_return_lines',
        ident('id', 'Stable ULID of this line.', primary_key=True),
        ident('return_id', 'Shipment return this line belongs to.', 'shipment_returns.id'),
        C('position', sa.Integer, 'One-based position within the return.', nullable=False),
        ident('shipment_line_identity', 'Stable identity of the shipment line given back.'),
        ident('item_id', 'Item returned.', 'items.id'),
        C('quantity_microunits', sa.BigInteger, 'Quantity returned in the item\'s base unit, microunits.',
          nullable=False),
        ident('movement_id', 'Receipt movement the line posted, naming the shipment line\'s issue.',
              'inventory_movements.id'),
        *provenance('shipment return line'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0",
                           name='ck_shipment_return_line_quantity'),
        sa.UniqueConstraint('return_id', 'position', name='uq_shipment_return_line_position'),
        sa.Index('ix_shipment_return_lines_identity', 'shipment_line_identity'),
        sa.Index('ix_shipment_return_lines_movement', 'movement_id'),
        description='Immutable lines of a shipment return: which shipment line, how many, and the receipt movement.')
    return {'shipment_returns': returns, 'shipment_return_revisions': revisions, 'shipment_return_lines': lines}


TABLES = ('shipment_returns', 'shipment_return_revisions', 'shipment_return_lines')
# Every row is immutable: a void appends a revision. No guard counts rows.
GUARDS = tuple(
    statement for table in TABLES for statement in (
        f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END",
        f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END"))
