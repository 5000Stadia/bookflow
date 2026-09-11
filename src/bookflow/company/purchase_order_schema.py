"""A purchase order: what was ordered from a vendor, before anything is owed.

A purchase order posts nothing. It is a commitment to buy, not a liability, so none of this
storage touches ``posting_batches``, ``posting_lines`` or ``ap_obligation_keys`` and the trial
balance cannot move because a purchase order was written. That is a structural fact rather
than a rule somebody has to remember: a purchase order is not a ``transactions`` row at all,
so there is no batch for it to own.

**Why its own family rather than the estimate's.** ``work_documents`` is the other non-posting
document with lines, and its shape -- stable document, immutable whole revisions, stable line
identities, a permanent conversion link -- is copied here deliberately. What is not copied is
its content: every column of ``work_revisions`` and ``work_lines`` is sell-side (a customer,
an item that must exist, a price level, a pricing basis, quoted tax, a billing root that a
later invoice consumes in fractions). A purchase order has a vendor, a line that may be an
expense account instead of an item, a cost instead of a price, and no tax. Putting it in
``work_documents`` would mean making ``customer_id`` and ``item_id`` nullable for every
estimate ever written and teaching every customer-work read to filter a kind out. Those reads
are what a bookkeeper's quotes list is made of, and one missed filter is a purchase order
appearing in it.

``purchase_order_lines`` carries item lines and expense-account lines in one table, exactly
one of the two per row, because a purchase order's two tabs are one ordered grid: line 3 is
line 3 whichever tab it was typed on, and a separate table per family would lose that order.

``purchase_order_conversions`` is where the order stops being live: one row, written when a
bill is entered from it, naming the exact revision the bill was made from and the bill it
became. It is unique on both sides, so an order becomes at most one bill and a bill comes
from at most one order -- which is what makes "already consumed" a fact in storage rather
than a status somebody could edit around.
"""

import sqlalchemy as sa

STATES = ('open', 'partly_received', 'closed', 'voided')


def define_tables(metadata, column, table, common):
    C, T = column, table

    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        return C(name, sa.String(26), description, *([sa.ForeignKey(target)] if target else []),
                 nullable=nullable, primary_key=primary_key)

    def text(name, description, size=None, nullable=False):
        return C(name, sa.String(size) if size else sa.Text, description, nullable=nullable)

    def integer(name, description, nullable=False):
        return C(name, sa.BigInteger, description, nullable=nullable)

    def check(expression, name):
        return sa.CheckConstraint(expression, name='ck_purchase_order_' + name)

    def exact(name, *, positive=False, nullable=False):
        expression = f"typeof({name}) = 'integer' AND {name} {'>' if positive else '>='} 0"
        return check(f'{name} IS NULL OR ({expression})' if nullable else expression, name)

    def owner(columns, targets, name, deferred=False):
        return sa.ForeignKeyConstraint(columns, targets, name='fk_purchase_order_' + name,
            **({'deferrable': True, 'initially': 'DEFERRED'} if deferred else {}))

    def created():
        return [text('created_at', 'UTC time this purchase order history was written.', 32),
                ident('created_by', 'Company principal that wrote this history.'),
                text('created_via', 'Interface that wrote this history.', 16)]

    def object_check(name):
        return check(f"json_valid({name}) AND json_type({name}) = 'object'", name)

    states = ', '.join(f"'{state}'" for state in STATES)

    purchase_orders = T('purchase_orders', *common(),
        text('number', 'Visible purchase order number, unique across purchase orders.', 64),
        ident('current_revision_id', 'Immutable revision currently displayed.'),
        text('status', 'Receiving state: open, partly_received, closed, or voided.', 16),
        text('voided_at', 'UTC time this order was withdrawn.', 32, True),
        ident('voided_by', 'Principal that withdrew this order.', nullable=True),
        text('void_reason', 'Reason supplied for withdrawing this order.', 140, True),
        sa.UniqueConstraint('number', name='uq_purchase_order_number'),
        check(f'status IN ({states})', 'status'),
        check('length(trim(number)) BETWEEN 1 AND 64', 'number'),
        exact('version', positive=True),
        check("(status = 'voided' AND voided_at IS NOT NULL AND voided_by IS NOT NULL "
              'AND length(trim(void_reason)) > 0) OR '
              "(status <> 'voided' AND voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)",
              'void'),
        owner(['id', 'current_revision_id'],
              ['purchase_order_revisions.document_id', 'purchase_order_revisions.id'],
              'current_revision', True),
        description='Stable non-posting purchase orders and their current receiving state.')
    sa.Index('ix_purchase_orders_status_number', purchase_orders.c.status, purchase_orders.c.number)

    purchase_order_revisions = T('purchase_order_revisions',
        ident('id', 'Immutable revision ULID.', primary_key=True),
        ident('document_id', 'Owning purchase order.', 'purchase_orders.id'),
        integer('revision_number', 'Positive document-local revision number.'),
        ident('supersedes_revision_id', 'Prior revision of this order; null for the first.', nullable=True),
        *created(),
        text('date', 'ISO order date.', 10),
        text('number', 'Visible number captured for this revision.', 64),
        text('status', 'Receiving state captured for this revision.', 16),
        ident('vendor_id', 'Vendor the goods or services were ordered from.', 'vendors.id'),
        text('expected_date', 'ISO date the order is expected; null when not promised.', 10, True),
        text('ship_to', 'Where the order is to be delivered, as entered; null when blank.', 2000, True),
        ident('terms_id', 'Captured payment terms; null when the order carries none.', 'terms.id', nullable=True),
        text('reference', "The vendor's own quotation or contract reference, as entered; null when blank.", 128, True),
        text('memo', 'Entered order memo; null when blank.', 2000, True),
        ident('class_id', 'Class captured for the order header; null when unclassified.', 'classes.id', nullable=True),
        text('currency', 'Home currency of the ordered total.', 3),
        integer('total_minor_units', 'Exact home-currency sum of the ordered lines.'),
        text('profile_snapshot', 'Versioned typed JSON object of resolved header facts and input origins.'),
        text('custom_fields_snapshot', 'JSON object of revision-owned custom values and displayed definition facts.'),
        ident('audit_event_id', 'Audit event that committed this revision.', 'audit_events.id'),
        sa.UniqueConstraint('document_id', 'id', name='uq_purchase_order_revision_owner'),
        sa.UniqueConstraint('document_id', 'revision_number', name='uq_purchase_order_revision_number'),
        sa.UniqueConstraint('supersedes_revision_id', name='uq_purchase_order_revision_successor'),
        owner(['document_id', 'supersedes_revision_id'],
              ['purchase_order_revisions.document_id', 'purchase_order_revisions.id'], 'supersedes'),
        exact('revision_number', positive=True), exact('total_minor_units', positive=True),
        check(f'status IN ({states})', 'revision_status'),
        check('length(currency) = 3', 'currency'),
        check("expected_date IS NULL OR expected_date >= date", 'expected_date'),
        object_check('profile_snapshot'), object_check('custom_fields_snapshot'),
        description='Immutable whole purchase order revisions with captured vendor, delivery and total facts.')
    sa.Index('ix_purchase_order_revision_query', purchase_order_revisions.c.date,
             purchase_order_revisions.c.document_id)
    sa.Index('ix_purchase_order_revision_vendor', purchase_order_revisions.c.vendor_id)

    purchase_order_line_identities = T('purchase_order_line_identities',
        ident('id', 'Stable document-local line identity.', primary_key=True),
        ident('document_id', 'Purchase order that permanently owns this line identity.', 'purchase_orders.id'),
        *created(),
        sa.UniqueConstraint('document_id', 'id', name='uq_purchase_order_identity_owner'),
        description='Stable ordered-line identities; identities omitted by a later revision never return.')

    purchase_order_lines = T('purchase_order_lines',
        ident('id', 'Revision-local line ULID.', primary_key=True),
        ident('document_id', 'Owning purchase order.'),
        ident('revision_id', 'Immutable revision containing this line.'),
        ident('line_id', 'Stable line identity carried across revisions.'),
        integer('position', 'One-based ordered position within the revision.'),
        *created(),
        ident('item_id', 'Item ordered; null on an expense-account line.', 'items.id', nullable=True),
        ident('account_id', 'Account the cost is destined for; null on an item line.', 'accounts.id', nullable=True),
        text('description', 'Entered line description; null when blank.', 2000, True),
        integer('quantity_microunits', 'Ordered quantity in millionths; null when the line is an amount alone.', True),
        integer('rate_minor_units', 'Home-currency unit cost; null when the line is an amount alone.', True),
        integer('amount_minor_units', 'Positive home-currency amount ordered on this line.'),
        ident('customer_id', 'Customer or job this cost is attributed to; null when unattributed.',
              'customers.id', nullable=True),
        C('billable', sa.Boolean, 'Whether this cost is marked to be passed on to the named customer.', nullable=False),
        ident('class_id', 'Class captured for this line; null when unclassified.', 'classes.id', nullable=True),
        text('line_snapshot', 'Versioned typed JSON object of the resolved item, account, job and class facts.'),
        sa.UniqueConstraint('document_id', 'revision_id', 'id', name='uq_purchase_order_line_owner'),
        sa.UniqueConstraint('revision_id', 'line_id', name='uq_purchase_order_line_identity'),
        sa.UniqueConstraint('revision_id', 'position', name='uq_purchase_order_line_position'),
        owner(['document_id', 'revision_id'],
              ['purchase_order_revisions.document_id', 'purchase_order_revisions.id'], 'line_revision'),
        owner(['document_id', 'line_id'],
              ['purchase_order_line_identities.document_id', 'purchase_order_line_identities.id'],
              'line_identity'),
        exact('position', positive=True), exact('amount_minor_units', positive=True),
        exact('quantity_microunits', positive=True, nullable=True),
        exact('rate_minor_units', nullable=True),
        check('(item_id IS NULL) <> (account_id IS NULL)', 'line_source'),
        check('(quantity_microunits IS NULL) = (rate_minor_units IS NULL)', 'line_extension'),
        check('billable IN (0, 1) AND (billable = 0 OR customer_id IS NOT NULL)', 'billable_job'),
        object_check('line_snapshot'),
        description='Immutable ordered purchase order lines, item or account, with captured quantities and costs.')

    purchase_order_conversions = T('purchase_order_conversions',
        ident('id', 'Permanent conversion ULID.', primary_key=True),
        ident('source_document_id', 'Purchase order consumed by the bill.'),
        ident('source_revision_id', 'Exact purchase order revision the bill was made from.'),
        integer('source_version', 'Positive purchase order concurrency version consumed at conversion.'),
        ident('destination_transaction_id', 'Bill created from the purchase order.'),
        ident('destination_revision_id', 'Initial bill revision created from the purchase order.'),
        text('destination_type', 'Financial destination type; bill is the only implemented one.', 32),
        *created(),
        ident('audit_event_id', 'Audit event that committed this conversion.', 'audit_events.id'),
        sa.UniqueConstraint('source_document_id', name='uq_purchase_order_conversion_source'),
        sa.UniqueConstraint('destination_transaction_id', name='uq_purchase_order_conversion_destination'),
        owner(['source_document_id', 'source_revision_id'],
              ['purchase_order_revisions.document_id', 'purchase_order_revisions.id'], 'conversion_source'),
        owner(['destination_transaction_id', 'destination_revision_id'],
              ['purchase_profiles.transaction_id', 'purchase_profiles.revision_id'], 'conversion_destination'),
        owner(['destination_transaction_id', 'destination_type'],
              ['transactions.id', 'transactions.type'], 'conversion_type'),
        exact('source_version', positive=True),
        check("destination_type = 'bill'", 'destination_type'),
        description='Immutable record that a purchase order became a bill; unique on both sides.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


IMMUTABLE = ('purchase_order_revisions', 'purchase_order_line_identities',
             'purchase_order_lines', 'purchase_order_conversions')


def guard_statements():
    """Storage fences for purchase order history: immutable rows, and a document never deleted."""
    for table in IMMUTABLE:
        for event in ('UPDATE', 'DELETE'):
            yield (f'CREATE TRIGGER {table}_immutable_{event.lower()} BEFORE {event} ON {table} '
                   "BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END")
    yield ('CREATE TRIGGER purchase_orders_no_delete BEFORE DELETE ON purchase_orders '
           "BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END")
    yield ('CREATE TRIGGER purchase_order_conversions_destination_type BEFORE INSERT ON purchase_order_conversions\n'
           'WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.destination_transaction_id '
           "AND type = NEW.destination_type)\n"
           "BEGIN SELECT RAISE(ABORT, 'purchase order conversion names the wrong document type'); END")
