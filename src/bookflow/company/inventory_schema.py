"""The inventory ledger: one append-only signed row per item per posting that moves stock.

``inventory_movements`` is the truth about what a company holds. On-hand quantity is the
running sum of ``quantity_microunits`` and asset value is the running sum of
``value_minor_units``; there is no stored per-item state to drift out of step with them,
because there is no second copy of either number.

Every row is tied to the posting line that carries its value, one movement per line and one
line per movement, so the inventory asset on the balance sheet and the total on the stock
reports are the same figure read two ways. That is the whole point of the ``posting_line_id``
uniqueness: an asset posting nobody attributed to an item would break the tie silently, and
a second movement on one line would double-count it.

**The five kinds, and why replay needs to tell them apart.**

- ``receipt`` -- quantity in at a stated value. An input to costing.
- ``issue`` -- quantity out; its value is what the weighted average consumed. An input to
  costing in quantity, an *output* of costing in value.
- ``value`` -- a value-only write-up or write-down; quantity does not move. An input.
- ``recost`` -- a zero-quantity value delta produced by the recalculation path, linked by
  ``corrects_movement_id`` to the issue whose cost it corrects, and dated at that issue's own
  effective date. **Never an input**: treating a correction as a new purchase or sale is
  exactly the mistake the costing decision forbids, and the discriminator is what stops it.
- ``reversal`` -- the exact inverse of one named movement, written when the document that
  caused it is voided. Never an input either; it retires the movement it names.

**Ordering.** ``effective_date`` is what reports and the general ledger both read, and
``sequence`` is a company-wide monotonic counter that decides same-day order. Two movements
on one day are replayed in the order they were recorded, which is a defined, stable order
that does not change when a later backdated entry lands in front of them.

**Attribution.** ``asset_account_id``, ``offset_account_id`` and ``class_id`` are the
dimensions the original posting used. A correction reads them from the movement it corrects
rather than resolving them again, so recosting a historical movement cannot silently move it
to an account or a class the original never touched.

``inventory_documents`` is the same fact ``money_out_documents`` records for a cheque: an
inventory adjustment and an inventory recost post as journal entries, the posting alone
cannot say which, and a stored kind is the only thing that can be right.
"""

import sqlalchemy as sa

# The documents that write movements, in their stored spelling.
DOCUMENT_KINDS = ('adjustment', 'recost')

# The movement kinds. ``INPUT_KINDS`` is what a replay walks; the other two are produced by
# replay or by a void and are never re-read as new stock activity. Anything that needs "every
# movement kind" derives it from here rather than writing the words again.
INPUT_KINDS = ('receipt', 'issue', 'value')
DERIVED_KINDS = ('recost', 'reversal')
MOVEMENT_KINDS = INPUT_KINDS + DERIVED_KINDS


def define_tables(metadata, column, table):
    C, T = column, table

    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    inventory_movements = T('inventory_movements',
        C('id', sa.String(26), 'Stable ULID of this immutable movement.', primary_key=True),
        C('created_at', sa.String(32), 'UTC time this movement was recorded.', nullable=False),
        C('created_by', sa.String(26), 'Company principal that recorded this movement.', nullable=False),
        C('created_via', sa.String(16), 'Interface that recorded this movement.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that committed this movement.',
          sa.ForeignKey('audit_events.id'), nullable=False),
        C('item_id', sa.String(26), 'Inventory item whose stock this movement changes.',
          sa.ForeignKey('items.id'), nullable=False),
        C('transaction_id', sa.String(26), 'Business document that caused this movement.', nullable=False),
        C('revision_id', sa.String(26), 'Immutable revision that supplies this movement.', nullable=False),
        C('posting_batch_id', sa.String(26), 'Accounting batch carrying this movement value.', nullable=False),
        C('posting_line_id', sa.String(26), 'Inventory-asset posting line carrying this movement value.', nullable=False),
        C('document_line_id', sa.String(26), 'Entered line this movement is attributed to.', nullable=False),
        C('effective_date', sa.String(10), 'Accounting date reports value this movement on.', nullable=False),
        C('sequence', sa.BigInteger, 'Company-wide recorded order; decides same-day replay order.', nullable=False),
        C('kind', sa.String(16), 'Movement kind: receipt, issue, value, recost or reversal.', nullable=False),
        C('quantity_microunits', sa.BigInteger, 'Signed quantity change in micro-units.', nullable=False),
        C('value_minor_units', sa.BigInteger, 'Signed inventory asset value change in minor units.', nullable=False),
        C('currency', sa.String(3), 'Home currency of this movement value.', nullable=False),
        C('asset_account_id', sa.String(26), 'Inventory-asset account this movement posted to.',
          sa.ForeignKey('accounts.id'), nullable=False),
        C('offset_account_id', sa.String(26), 'Account carrying the balancing side of this movement.',
          sa.ForeignKey('accounts.id'), nullable=False),
        C('class_id', sa.String(26), 'Class captured on the original posting; null when unclassified.',
          sa.ForeignKey('classes.id'), nullable=True),
        C('corrects_movement_id', sa.String(26), 'Issue whose effective cost this delta corrects; null except on recost.', nullable=True),
        C('reverses_movement_id', sa.String(26), 'Movement this row exactly retires; null except on reversal.', nullable=True),
        sa.ForeignKeyConstraint(['transaction_id', 'posting_batch_id'],
                                ['posting_batches.transaction_id', 'posting_batches.id'],
                                name='fk_inventory_movement_batch'),
        sa.ForeignKeyConstraint(['transaction_id', 'posting_line_id'],
                                ['posting_lines.transaction_id', 'posting_lines.id'],
                                name='fk_inventory_movement_posting_line'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
                                ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'],
                                name='fk_inventory_movement_document_line'),
        sa.ForeignKeyConstraint(['corrects_movement_id'], ['inventory_movements.id'],
                                name='fk_inventory_movement_corrects'),
        sa.ForeignKeyConstraint(['reverses_movement_id'], ['inventory_movements.id'],
                                name='fk_inventory_movement_reverses'),
        # One movement per asset posting line, both ways. Without the first half a movement
        # could claim a line already claimed and double the asset; without the second the
        # report total and the balance sheet would disagree with nothing to point at.
        sa.UniqueConstraint('posting_line_id', name='uq_inventory_movement_posting_line'),
        sa.UniqueConstraint('sequence', name='uq_inventory_movement_sequence'),
        sa.UniqueConstraint('reverses_movement_id', name='uq_inventory_movement_reversal'),
        sa.CheckConstraint(f'kind IN ({quoted(MOVEMENT_KINDS)})', name='ck_inventory_movement_kind'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND typeof(value_minor_units) = 'integer' "
                           "AND typeof(sequence) = 'integer' AND sequence > 0",
                           name='ck_inventory_movement_integers'),
        sa.CheckConstraint("effective_date LIKE '____-__-__'", name='ck_inventory_movement_date'),
        # Each kind's shape, written once: a receipt brings value in with quantity, an issue
        # takes both out, a value adjustment and a recost move value alone, and only the two
        # derived kinds carry a link.
        sa.CheckConstraint(
            "(kind = 'receipt' AND quantity_microunits > 0 AND value_minor_units > 0 "
            "AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR "
            "(kind = 'issue' AND quantity_microunits < 0 AND value_minor_units < 0 "
            "AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR "
            "(kind = 'value' AND quantity_microunits = 0 AND value_minor_units != 0 "
            "AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR "
            "(kind = 'recost' AND quantity_microunits = 0 AND value_minor_units != 0 "
            "AND corrects_movement_id IS NOT NULL AND reverses_movement_id IS NULL) OR "
            "(kind = 'reversal' AND reverses_movement_id IS NOT NULL AND corrects_movement_id IS NULL "
            "AND (quantity_microunits != 0 OR value_minor_units != 0))",
            name='ck_inventory_movement_shape'),
        sa.Index('ix_inventory_movements_item', 'item_id', 'effective_date', 'sequence', 'id'),
        sa.Index('ix_inventory_movements_corrects', 'corrects_movement_id'),
        sa.Index('ix_inventory_movements_document', 'transaction_id', 'sequence'),
        description='Immutable signed inventory quantity and value changes, one per inventory-asset posting line.')

    inventory_documents = T('inventory_documents',
        C('transaction_id', sa.String(26), 'The posted journal entry this inventory document was written as.',
          primary_key=True),
        C('type', sa.String(32), 'Transaction type of the marked document; always journal_entry.', nullable=False),
        C('kind', sa.String(16), 'Document a person or the recalculation wrote: adjustment or recost.', nullable=False),
        C('created_at', sa.String(32), 'UTC time this document marker was written.', nullable=False),
        C('created_by', sa.String(26), 'Company principal the document is attributed to.', nullable=False),
        C('created_via', sa.String(16), 'Interface the document was written through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that committed the document this marks.',
          sa.ForeignKey('audit_events.id'), nullable=False),
        sa.CheckConstraint(f'kind IN ({quoted(DOCUMENT_KINDS)})', name='ck_inventory_document_kind'),
        sa.CheckConstraint("type = 'journal_entry'", name='ck_inventory_document_type'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'], ['transactions.id', 'transactions.type'],
                                name='fk_inventory_document_type'),
        sa.Index('ix_inventory_documents_kind', 'kind', 'transaction_id'),
        description='Immutable record of which inventory document a posted journal entry was written as.')

    return {'inventory_movements': inventory_movements, 'inventory_documents': inventory_documents}
