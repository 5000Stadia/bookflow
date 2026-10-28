"""Packing and shipping facts (co0106, R181 increment 4; design/pick-pack-ship.md §5, §6, §8).

Packing is an optional layer under the company switch ``pick_and_pack_enabled`` (K3): drafts and boxes. Freight to
bill, the weight unit and the shipping item are shipping facts every shipper has, switch or not.

- ``shipment_drafts``: the immutable header of a **draft** shipment -- number (from the ``shipment`` sequence, kept
  when it ships: the shipment it becomes has the draft's id and number), order, customer, site, the pick list it was
  made from. A draft moves nothing and posts nothing.
- ``shipment_draft_revisions``: append-only; ``draft``, then ``cancelled`` or ``shipped``. The newest is current and
  its ``revision_number`` the draft's version. Packing a draft writes a revision.
- ``shipment_draft_lines``: immutable; what the draft will ship -- order line, item, base quantity, bin, lot and
  serial numbers as text. A draft's lines never change; cancel it and make another.
- ``shipment_packages``: immutable, per revision (of a draft or a shipment): box number from 1, package type, tare,
  measured weight, dimensions and tracking number. Every revision carries the packing it keeps forward.
- ``shipment_package_contents``: immutable: a box's units of one shipment line (by its stable identity), with the
  serial numbers in it.
- ``shipment_freight_billings``: immutable: the invoice line that billed a shipment's freight, live while that invoice
  is posted and its current revision carries the line.
"""
import sqlalchemy as sa

DRAFT_STATUSES = ('draft', 'cancelled', 'shipped')
POSITIONS = ('before', 'after')


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

    drafts = T('shipment_drafts',
        ident('id', 'Stable ULID of this draft; the shipment it becomes keeps it.', primary_key=True),
        C('number', sa.String(64), 'Shipment number, from the shipment sequence; the shipment keeps it.', nullable=False),
        ident('sales_order_id', 'Sales order the draft will fill.', 'work_documents.id'),
        ident('customer_id', 'Customer or job the goods go to: the order\'s.', 'customers.id'),
        ident('site_id', 'Inventory site the draft will leave.', 'inventory_sites.id'),
        ident('pick_list_id', 'Pick list the draft was made from; null when none.', 'pick_lists.id', nullable=True),
        *provenance('draft shipment'),
        sa.UniqueConstraint('number', name='uq_shipment_drafts_number'),
        sa.Index('ix_shipment_drafts_order', 'sales_order_id'),
        description='Draft shipments: the lines, places and numbers that will ship, and their boxes. Move nothing and '
                    'post nothing. Immutable; the state is the newest revision.')

    revisions = T('shipment_draft_revisions',
        ident('id', 'Stable ULID of this revision.', primary_key=True),
        ident('draft_id', 'Draft this revision restates.', 'shipment_drafts.id'),
        C('revision_number', sa.Integer, 'One-based revision number; the newest is current and is the version.',
          nullable=False),
        C('status', sa.String(10), 'draft, cancelled, or shipped (the shipment with the draft\'s id posted it).',
          nullable=False),
        C('ship_date', sa.String(10), 'Planned ship date, YYYY-MM-DD; null: the day it ships.', nullable=True),
        C('count_position', sa.String(8), 'before (the default) or after a count of its site on the ship date.',
          nullable=False),
        ident('ship_method_id', 'Ship Via list entry; null when none.', 'ship_methods.id', nullable=True),
        C('carrier', sa.Text, 'Carrier or service, as text; null when blank.', nullable=True),
        C('tracking_numbers', sa.Text, 'JSON array of tracking numbers as text.', nullable=False),
        C('memo', sa.Text, 'Memo as entered; null when blank.', nullable=True),
        C('freight_minor_units', sa.BigInteger, 'Freight to bill the customer, in the order\'s currency; null: none.',
          nullable=True),
        ident('freight_item_id', 'Item the freight bills as; null when no freight.', 'items.id', nullable=True),
        C('reason', sa.Text, 'Why this revision was written, as the caller gave it.', nullable=True),
        *provenance('draft shipment revision'),
        sa.CheckConstraint(f'status IN ({quoted(DRAFT_STATUSES)})', name='ck_shipment_draft_revision_status'),
        sa.CheckConstraint(f'count_position IN ({quoted(POSITIONS)})', name='ck_shipment_draft_revision_position'),
        sa.CheckConstraint("ship_date IS NULL OR ship_date LIKE '____-__-__'", name='ck_shipment_draft_revision_date'),
        sa.CheckConstraint("typeof(revision_number) = 'integer' AND revision_number > 0",
                           name='ck_shipment_draft_revision_number'),
        sa.CheckConstraint("json_valid(tracking_numbers) AND json_type(tracking_numbers) = 'array'",
                           name='ck_shipment_draft_revision_tracking'),
        sa.CheckConstraint('freight_minor_units IS NULL OR freight_minor_units > 0',
                           name='ck_shipment_draft_revision_freight'),
        sa.UniqueConstraint('draft_id', 'revision_number', name='uq_shipment_draft_revision_number'),
        description='Append-only draft shipment revisions: packing, facts, cancel or ship each add one.')

    lines = T('shipment_draft_lines',
        ident('id', 'Stable ULID of this line.', primary_key=True),
        ident('draft_id', 'Draft this line belongs to.', 'shipment_drafts.id'),
        ident('line_identity', 'Stable identity the shipment line keeps when the draft ships.'),
        C('position', sa.Integer, 'One-based position.', nullable=False),
        ident('root_document_id', 'Billing root document of the order line.'),
        ident('root_line_id', 'Billing root line identity of the order line.'),
        ident('item_id', 'Item to ship.', 'items.id'),
        C('quantity_microunits', sa.BigInteger, 'Quantity in the item\'s base unit, microunits.', nullable=False),
        ident('bin_id', 'Bin the units will leave from; null when none was named.', 'inventory_bins.id', nullable=True),
        C('lot', sa.Text, 'Lot number as named; null when none.', nullable=True),
        C('serial_numbers', sa.Text, 'JSON array of serial numbers as named; null when none.', nullable=True),
        C('description', sa.Text, 'Line description: the order line\'s.', nullable=True),
        *provenance('draft shipment line'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0",
                           name='ck_shipment_draft_line_quantity'),
        sa.CheckConstraint("serial_numbers IS NULL OR (json_valid(serial_numbers) AND json_type(serial_numbers) = "
                           "'array')", name='ck_shipment_draft_line_serials'),
        sa.UniqueConstraint('draft_id', 'position', name='uq_shipment_draft_line_position'),
        description='Immutable lines of a draft shipment.')

    packages = T('shipment_packages',
        ident('id', 'Stable ULID of this package row.', primary_key=True),
        ident('shipment_id', 'Draft or shipment (one id: the shipment keeps its draft\'s).'),
        ident('revision_id', 'Draft or shipment revision this packing belongs to.'),
        C('box_number', sa.Integer, 'Box number, from 1.', nullable=False),
        C('package_type', sa.Text, 'Package type, as text; null when blank.', nullable=True),
        C('tare_microunits', sa.BigInteger, 'Empty package weight in the company weight unit, micro-units; null: none.',
          nullable=True),
        C('weight_microunits', sa.BigInteger, 'Measured weight in the company weight unit, micro-units; null: computed.',
          nullable=True),
        C('dimensions', sa.Text, 'Dimensions, as text; null when blank.', nullable=True),
        C('tracking_number', sa.Text, 'Tracking number of this box, as text; null when blank.', nullable=True),
        *provenance('package'),
        sa.CheckConstraint("typeof(box_number) = 'integer' AND box_number > 0", name='ck_shipment_package_box'),
        sa.CheckConstraint('tare_microunits IS NULL OR tare_microunits >= 0', name='ck_shipment_package_tare'),
        sa.CheckConstraint('weight_microunits IS NULL OR weight_microunits > 0', name='ck_shipment_package_weight'),
        sa.UniqueConstraint('revision_id', 'box_number', name='uq_shipment_package_box'),
        sa.Index('ix_shipment_packages_shipment', 'shipment_id'),
        description='Immutable boxes of a draft or shipment revision.')

    contents = T('shipment_package_contents',
        ident('id', 'Stable ULID of this content row.', primary_key=True),
        ident('package_id', 'Box the units are in.', 'shipment_packages.id'),
        ident('revision_id', 'Revision of the box.'),
        ident('line_identity', 'Stable identity of the shipment line the units belong to.'),
        C('quantity_microunits', sa.BigInteger, 'Units of the line in the box, base unit, microunits.', nullable=False),
        C('serial_numbers', sa.Text, 'JSON array of the serial numbers in the box; null when none.', nullable=True),
        *provenance('package content'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0",
                           name='ck_shipment_package_content_quantity'),
        sa.CheckConstraint("serial_numbers IS NULL OR (json_valid(serial_numbers) AND json_type(serial_numbers) = "
                           "'array')", name='ck_shipment_package_content_serials'),
        sa.UniqueConstraint('package_id', 'line_identity', name='uq_shipment_package_content'),
        sa.Index('ix_shipment_package_contents_revision', 'revision_id'),
        description='Immutable: the units of one shipment line in one box.')

    freight = T('shipment_freight_billings',
        ident('id', 'Stable ULID of this billing.', primary_key=True),
        ident('transaction_id', 'Invoice that billed the freight.', 'transactions.id'),
        ident('line_id', 'Stable identity of the invoice line that billed it.', 'document_line_identities.id'),
        ident('shipment_id', 'Shipment whose freight it billed.', 'shipments.id'),
        *provenance('freight billing'),
        sa.UniqueConstraint('transaction_id', 'line_id', name='uq_shipment_freight_billing'),
        sa.Index('ix_shipment_freight_billings_shipment', 'shipment_id'),
        description='Immutable: an invoice line that billed a shipment\'s freight, live while that invoice is posted '
                    'and its current revision carries the line.')
    return {'shipment_drafts': drafts, 'shipment_draft_revisions': revisions, 'shipment_draft_lines': lines,
            'shipment_packages': packages, 'shipment_package_contents': contents,
            'shipment_freight_billings': freight}


def company_info_columns(C):
    return (C('weight_unit', sa.String(16), 'The company\'s one weight unit, a label never converted (lb, kg, oz).',
              nullable=False, server_default='lb'),
            C('shipping_item_id', sa.String(26), 'Item freight to bill is billed as by default; null: name it.',
              sa.ForeignKey('items.id'), nullable=True))


def revision_columns(C):
    return (C('freight_minor_units', sa.BigInteger, 'Freight to bill the customer, in the order\'s currency; null: '
              'none.', nullable=True),
            C('freight_item_id', sa.String(26), 'Item the freight bills as; null when no freight.',
              sa.ForeignKey('items.id'), nullable=True))


TABLES = ('shipment_drafts', 'shipment_draft_revisions', 'shipment_draft_lines', 'shipment_packages',
          'shipment_package_contents', 'shipment_freight_billings')
# Every row is immutable: revisions append, and each revision carries its packing forward. No guard counts rows.
GUARDS = tuple(
    statement for table in TABLES for statement in (
        f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END",
        f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END"))
