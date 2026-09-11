"""Immutable vendor-credit facts, and the settlement capacity they carry.

A vendor credit is what a vendor owes back: a return, an overcharge put right, a rebate. It
is the bill read backwards -- Accounts Payable is debited and each captured purchase account
is credited -- and the two tables here are the bill's own two, mirrored.

**What was credited.** ``vendor_credit_profiles`` is the one-to-one header of a vendor-credit
revision; ``vendor_credit_expense_lines`` is its Expenses grid, one row per envelope in
``document_lines``. The envelope kind is ``purchase``, the same family a bill's lines use,
because it is the same grid: what makes this document a credit is its type and the direction
of its legs, never the shape of a line. That also means the Items tab attaches here exactly as
it attaches to a bill, as a sibling profile table on the same envelope.

**What it can settle.** A vendor credit creates **no** ``ap_obligation_keys`` row. It is not a
payable -- nobody owes it -- and ``payable_reports._UNPAID`` lists bills, so an obligation key
would put a credit on the unpaid-bills list. What it creates instead is an ``ap_source_keys``
row of kind ``vendor_credit``, carrying the same ``(vendor_id, ap_account_id, currency)``
triple a bill payment's source carries, and one ``ap_source_components`` row per credited line
naming the exact ``posting_line_sources`` row that debited Accounts Payable for it. From there
it settles through the same ``ap_applications`` edge a bill payment uses, and every reader of
that edge -- the aging, the unpaid list, ``bills.applied_totals`` -- answers without a branch.
"""

import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    def created():
        return [C('created_at', sa.String(32), 'UTC time this vendor-credit history was written.', nullable=False),
                identifier('created_by', 'Company principal that wrote this vendor-credit history.'),
                C('created_via', sa.String(16), 'Interface that wrote this vendor-credit history.', nullable=False)]

    def integer(name, description):
        return C(name, sa.BigInteger, description, nullable=False)

    def positive(name):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} > 0",
                                  name=f'ck_vendor_credit_{name}_positive')

    def object_check(name):
        return sa.CheckConstraint(f"json_valid({name}) AND json_type({name}) = 'object'",
                                  name=f'ck_vendor_credit_{name}_object')

    vendor_credit_profiles = T('vendor_credit_profiles',
        identifier('revision_id', 'Immutable revision owning this one-to-one vendor-credit header.', primary_key=True),
        identifier('transaction_id', 'Stable vendor-credit document owning this revision.'),
        *created(),
        C('type', sa.String(32), 'Credit document type; vendor_credit is the only implemented one.', nullable=False),
        identifier('vendor_id', 'Vendor that owes this credit back.', 'vendors.id'),
        identifier('ap_account_id', 'Accounts payable account debited for the credited total.', 'accounts.id'),
        C('supplier_reference', sa.String(128), "The supplier's own credit-note number, as entered; null when blank.", nullable=True),
        C('supplier_reference_key', sa.String(256), 'NFC-normalized, trimmed and case-folded reference used to detect a repeat; null when blank.', nullable=True),
        integer('expense_total_minor_units', 'Home-currency sum of the credited expense lines.'),
        C('profile_snapshot', sa.Text, 'Versioned typed JSON object of resolved header facts and input origins.', nullable=False),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_vendor_credit_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['transaction_revisions.transaction_id', 'transaction_revisions.id'], name='fk_vendor_credit_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'],
            ['transactions.id', 'transactions.type'], name='fk_vendor_credit_profile_type'),
        sa.CheckConstraint("type = 'vendor_credit'", name='ck_vendor_credit_profile_type'),
        sa.CheckConstraint('(supplier_reference IS NULL) = (supplier_reference_key IS NULL)',
                           name='ck_vendor_credit_profile_reference_pair'),
        positive('expense_total_minor_units'), object_check('profile_snapshot'),
        # Deliberately not unique, exactly as on the bill: a repeated credit-note number is
        # reported by the read and never refused in storage.
        sa.Index('ix_vendor_credit_profiles_reference', 'vendor_id', 'supplier_reference_key'),
        description='Immutable one-to-one vendor-credit revision headers with captured vendor, payable and reference facts.')

    vendor_credit_expense_lines = T('vendor_credit_expense_lines',
        identifier('document_line_id', 'Revision-local purchase envelope owning this one-to-one credited expense profile.', primary_key=True),
        identifier('transaction_id', 'Stable document owning this credited line.'),
        identifier('revision_id', 'Exact immutable vendor-credit revision owning this line.'),
        *created(),
        identifier('account_id', 'Expense, cost or asset account credited back by this line.', 'accounts.id'),
        integer('amount_minor_units', 'Positive home-currency amount credited to the account.'),
        identifier('customer_id', 'Customer or job the original cost was attributed to; null when unattributed.', 'customers.id', nullable=True),
        C('line_snapshot', sa.Text, 'Versioned typed JSON object of the resolved account, job and class facts.', nullable=False),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'document_line_id', name='uq_vendor_credit_expense_line_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['vendor_credit_profiles.transaction_id', 'vendor_credit_profiles.revision_id'], name='fk_vendor_credit_expense_line_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], name='fk_vendor_credit_expense_line_envelope'),
        positive('amount_minor_units'), object_check('line_snapshot'),
        description='Immutable one-to-one credited expense lines of a vendor credit, with captured account and job facts.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


IMMUTABLE = ('vendor_credit_profiles', 'vendor_credit_expense_lines')


def guard_statements():
    """Storage fences for vendor-credit history: immutable rows, owned payable attribution."""
    for table in IMMUTABLE:
        for event in ('UPDATE', 'DELETE'):
            yield (f'CREATE TRIGGER {table}_immutable_{event.lower()} BEFORE {event} ON {table} '
                   "BEGIN SELECT RAISE(ABORT, 'immutable vendor credit history'); END")
    yield ('CREATE TRIGGER vendor_credit_profiles_transaction_id_type BEFORE INSERT ON vendor_credit_profiles\n'
           'WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id '
           "AND type = 'vendor_credit')\n"
           "BEGIN SELECT RAISE(ABORT, 'vendor credit reference has wrong document type'); END")
