"""Item conversions: a non-inventory part or other charge that became an inventory part (co0072).

One row per converted item, written by `item convert`. The conversion is one-way, as in the anchor,
so an item converts at most once. `effective_date` is the first day the item carries stock: a stock
movement dated before it is refused, so purchases and sales entered before the conversion stay as
they posted (expensed, with no cost of goods) and a document dated before it cannot be re-entered
as a stocked one.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    conversions = T('item_conversions',
        C('id', sa.String(26), 'Stable ULID of this conversion.', primary_key=True),
        C('item_id', sa.String(26), 'Item that was converted; it keeps its id, so every document still names it.',
          sa.ForeignKey('items.id'), nullable=False),
        C('from_type', sa.String(24), 'Item type before the conversion: non_inventory_part or other_charge.', nullable=False),
        C('to_type', sa.String(24), 'Item type after the conversion: inventory_part.', nullable=False),
        C('effective_date', sa.String(10), 'First ISO date the item carries stock; no stock moves before it.', nullable=False),
        C('reason', sa.String(140), 'Why the item was converted, as the person gave it.', nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp of the conversion.', nullable=False),
        C('created_by', sa.String(26), 'Principal who converted the item.', nullable=False),
        C('created_via', sa.String(16), 'Interface the conversion came through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event of the conversion.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.UniqueConstraint('item_id', name='uq_item_conversion_item'),
        sa.CheckConstraint("from_type IN ('non_inventory_part','other_charge')", name='ck_item_conversion_from'),
        sa.CheckConstraint("to_type = 'inventory_part'", name='ck_item_conversion_to'),
        sa.CheckConstraint("length(effective_date) = 10", name='ck_item_conversion_date'),
        description='Items converted to inventory parts: the type they were, and the first day they carry stock.')
    return {'item_conversions': conversions}
