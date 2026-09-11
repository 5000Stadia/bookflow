"""Immutable purchase facts owned by bill revisions, and the payable they create.

Two families live here and they are deliberately separate.

**What was bought.** ``purchase_profiles`` is the one-to-one header of a bill revision, and
the two grids below it are the document's two tabs. ``purchase_expense_lines`` is the Expenses
grid and ``purchase_item_lines`` is the Items grid; each is one row per envelope in
``document_lines``, and the envelope kind is ``purchase`` for both, because what makes a line
one or the other is which profile table owns it, never the envelope. The header carries a
total per family and the revision's own total is their sum, so a bill entered wholly on one
tab stores a zero for the other rather than a special case.

An item line debits the account captured from the item's own purchase profile, which is what
makes it the same accounting as an expense line typed by hand -- and the reason an inventory
item is refused here rather than admitted: receiving stock debits Inventory Asset and needs an
owner that values it, which does not exist yet.

**What is owed.** ``ap_obligation_keys`` is the payable itself: one stable row per bill,
carrying the vendor, the AP account and the currency an application has to match exactly. It
outlives every revision, so a settlement that attaches to it is not invalidated by a
correction. ``ap_obligation_components`` is the per-revision breakdown of that obligation --
one positive amount per entered line, tied to the exact ``posting_line_sources`` row that
attributed the AP credit to it -- which is what an allocation targets when a payment has to
land on particular lines rather than on a lump. The component points at the ``document_lines``
envelope, so an item line becomes a component with no schema change at all.
"""

import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    def created():
        return [C('created_at', sa.String(32), 'UTC time this purchase history was written.', nullable=False),
                identifier('created_by', 'Company principal that wrote this purchase history.'),
                C('created_via', sa.String(16), 'Interface that wrote this purchase history.', nullable=False)]

    def integer(name, description):
        return C(name, sa.BigInteger, description, nullable=False)

    def positive(name):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} > 0",
                                  name=f'ck_purchase_{name}_positive')

    def nonnegative(name):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} >= 0",
                                  name=f'ck_purchase_{name}_nonnegative')

    def object_check(name):
        return sa.CheckConstraint(f"json_valid({name}) AND json_type({name}) = 'object'",
                                  name=f'ck_purchase_{name}_object')

    purchase_profiles = T('purchase_profiles',
        identifier('revision_id', 'Immutable revision owning this one-to-one purchase header.', primary_key=True),
        identifier('transaction_id', 'Stable purchase document owning this revision.'),
        *created(),
        C('type', sa.String(32), 'Purchase document type; bill is the only implemented one.', nullable=False),
        identifier('vendor_id', 'Vendor the obligation is owed to.', 'vendors.id'),
        identifier('ap_account_id', 'Accounts payable account credited for the gross.', 'accounts.id'),
        identifier('terms_id', 'Captured payment terms; null when the bill carries none.', 'terms.id', nullable=True),
        C('due_date', sa.String(10), 'Captured due date, derived from terms or entered outright.', nullable=False),
        C('supplier_reference', sa.String(128), "The supplier's own document number, as entered; null when blank.", nullable=True),
        C('supplier_reference_key', sa.String(256), 'NFC-normalized, trimmed and case-folded reference used to detect a repeat; null when blank.', nullable=True),
        integer('expense_total_minor_units', 'Home-currency sum of the expense lines; zero on a bill entered on the Items tab alone.'),
        integer('item_total_minor_units', 'Home-currency sum of the item lines; zero on a bill entered on the Expenses tab alone.'),
        C('profile_snapshot', sa.Text, 'Versioned typed JSON object of resolved header facts and input origins.', nullable=False),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_purchase_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['transaction_revisions.transaction_id', 'transaction_revisions.id'], name='fk_purchase_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'],
            ['transactions.id', 'transactions.type'], name='fk_purchase_profile_type'),
        sa.CheckConstraint("type = 'bill'", name='ck_purchase_profile_type'),
        sa.CheckConstraint('(supplier_reference IS NULL) = (supplier_reference_key IS NULL)',
                           name='ck_purchase_profile_reference_pair'),
        # Each family's total may be zero -- a bill entered wholly on one tab has nothing on the
        # other -- and it is their sum that the revision's own positive total constrains.
        nonnegative('expense_total_minor_units'), nonnegative('item_total_minor_units'),
        object_check('profile_snapshot'),
        # Deliberately not unique: a repeated supplier reference is reported by the read, and a
        # uniqueness constraint here would make the warn-and-acknowledge mode unimplementable.
        sa.Index('ix_purchase_profiles_reference', 'vendor_id', 'supplier_reference_key'),
        description='Immutable one-to-one bill revision headers with captured vendor, payable, terms and reference facts.')

    purchase_expense_lines = T('purchase_expense_lines',
        identifier('document_line_id', 'Revision-local purchase envelope owning this one-to-one expense profile.', primary_key=True),
        identifier('transaction_id', 'Stable document owning this expense line.'),
        identifier('revision_id', 'Exact immutable purchase revision owning this line.'),
        *created(),
        identifier('account_id', 'Expense, cost or asset account debited by this line.', 'accounts.id'),
        integer('amount_minor_units', 'Positive home-currency amount debited to the account.'),
        identifier('customer_id', 'Customer or job this cost is attributed to; null when unattributed.', 'customers.id', nullable=True),
        C('billable', sa.Boolean, 'Whether this cost is marked for rebilling to the named customer or job.', nullable=False),
        C('line_snapshot', sa.Text, 'Versioned typed JSON object of the resolved account, job and class facts.', nullable=False),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'document_line_id', name='uq_purchase_expense_line_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['purchase_profiles.transaction_id', 'purchase_profiles.revision_id'], name='fk_purchase_expense_line_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], name='fk_purchase_expense_line_envelope'),
        positive('amount_minor_units'), object_check('line_snapshot'),
        sa.CheckConstraint('billable IN (0, 1) AND (billable = 0 OR customer_id IS NOT NULL)',
                           name='ck_purchase_expense_billable_job'),
        description='Immutable one-to-one expense lines of a bill, with captured account, job and billable facts.')

    purchase_item_lines = T('purchase_item_lines',
        identifier('document_line_id', 'Revision-local purchase envelope owning this one-to-one item profile.', primary_key=True),
        identifier('transaction_id', 'Stable document owning this item line.'),
        identifier('revision_id', 'Exact immutable purchase revision owning this line.'),
        *created(),
        identifier('item_id', 'Item bought on this line.', 'items.id'),
        identifier('account_id', "Account debited by this line, captured from the item's own purchase account.", 'accounts.id'),
        C('quantity_microunits', sa.BigInteger, 'Positive quantity bought, in millionths of one unit.', nullable=False),
        C('unit_cost_minor_units', sa.BigInteger, 'Home-currency cost of one unit; null when the amount was entered outright.', nullable=True),
        integer('amount_minor_units', 'Positive home-currency amount debited to the account.'),
        identifier('customer_id', 'Customer or job this cost is attributed to; null when unattributed.', 'customers.id', nullable=True),
        C('billable', sa.Boolean, 'Whether this cost is marked for rebilling to the named customer or job.', nullable=False),
        C('line_snapshot', sa.Text, 'Versioned typed JSON object of the resolved item, account, job and class facts.', nullable=False),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'document_line_id', name='uq_purchase_item_line_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['purchase_profiles.transaction_id', 'purchase_profiles.revision_id'], name='fk_purchase_item_line_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], name='fk_purchase_item_line_envelope'),
        positive('quantity_microunits'), positive('amount_minor_units'), object_check('line_snapshot'),
        # A unit cost is what the amount was derived from, so a line whose amount was typed
        # outright carries none rather than a back-computed number nobody entered.
        sa.CheckConstraint("unit_cost_minor_units IS NULL OR (typeof(unit_cost_minor_units) = 'integer' "
                           'AND unit_cost_minor_units >= 0)', name='ck_purchase_item_unit_cost'),
        sa.CheckConstraint('billable IN (0, 1) AND (billable = 0 OR customer_id IS NOT NULL)',
                           name='ck_purchase_item_billable_job'),
        # What was bought of one item, newest revision last: the read an inventory owner needs
        # before it can value anything.
        sa.Index('ix_purchase_item_lines_item', 'item_id', 'revision_id'),
        description='Immutable one-to-one item lines of a bill, with captured item, account, quantity, cost, job and billable facts.')

    ap_obligation_keys = T('ap_obligation_keys',
        identifier('id', 'Stable ULID of this payable; an application names this, never a revision.', primary_key=True),
        identifier('transaction_id', 'Purchase document that permanently owns this payable.', 'transactions.id'),
        integer('ordinal', 'One-based payable ordinal within the document; a bill has exactly one.'),
        identifier('vendor_id', 'Vendor a settlement must match exactly.', 'vendors.id'),
        identifier('ap_account_id', 'Payable account a settlement must match exactly.', 'accounts.id'),
        C('currency', sa.String(3), 'Home currency a settlement must match exactly.', nullable=False),
        *created(),
        C('audit_event_id', sa.String(26), 'Audit event that committed this payable.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_ap_obligation_owner'),
        sa.UniqueConstraint('transaction_id', 'ordinal', name='uq_ap_obligation_ordinal'),
        positive('ordinal'),
        sa.Index('ix_ap_obligation_keys_vendor', 'vendor_id', 'ap_account_id', 'id'),
        description='Stable vendor payables; retired ordinals are never reused and a correction never moves one.')

    ap_obligation_components = T('ap_obligation_components',
        identifier('id', 'Stable ULID of this immutable obligation component.', primary_key=True),
        identifier('transaction_id', 'Stable document owning this component.'),
        identifier('revision_id', 'Exact immutable purchase revision owning this component.'),
        identifier('key_id', 'Payable this component belongs to.'),
        identifier('document_line_id', 'Entered line whose amount this component owes.'),
        integer('ordinal', 'One-based component ordinal within the entered line.'),
        identifier('posting_source_id', 'Exact payable attribution row this component was posted through.'),
        integer('amount_minor_units', 'Positive home-currency amount owed for this line.'),
        C('currency', sa.String(3), 'Home currency of this component.', nullable=False),
        *created(),
        C('audit_event_id', sa.String(26), 'Audit event that committed this component.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.UniqueConstraint('revision_id', 'document_line_id', 'ordinal', name='uq_ap_component_occurrence'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'id', name='uq_ap_component_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'key_id'],
            ['ap_obligation_keys.transaction_id', 'ap_obligation_keys.id'], name='fk_ap_component_key'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], name='fk_ap_component_envelope'),
        sa.ForeignKeyConstraint(['transaction_id', 'posting_source_id'],
            ['posting_line_sources.transaction_id', 'posting_line_sources.id'], name='fk_ap_component_attribution'),
        positive('ordinal'), positive('amount_minor_units'),
        sa.Index('ix_ap_components_key', 'key_id', 'revision_id'),
        description='Immutable per-line breakdown of a payable, tied to the exact posting attribution that created it.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}
