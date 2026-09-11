"""Remitting sales tax: the one-to-one header of a payment made to a tax agency.

A sales tax payment is its own document type, not a check written to a vendor who happens to
be an agency. The reason is the liability read: what is owed to an agency is derived from the
tax the books actually recorded, and every effect on a sales-tax-payable account has to name
the agency it belongs to or the read cannot be trusted. A check names an account, not an
agency, so a check to an agency is money leaving the liability with nothing saying whose
liability fell. This table is what says it.

``sales_tax_payment_profiles`` therefore carries three things nothing else can supply:

- ``agency_id`` -- the flagged tax-agency vendor this remittance answers. One agency per
  document, so the whole of the document's effect on the liability belongs to it.
- ``liability_posting_source_id`` -- the exact ``posting_line_sources`` row that debited the
  sales-tax liability. That is the same outward-pointing attribution ``ap_source_components``
  and ``credit_tax_components`` use, and it is what lets the liability read attribute a
  remittance and its own reversal without either table knowing the other exists.
- ``through_date`` -- the period end the remittance was computed against, which is what the
  amount was checked against when it was written. It is a captured fact, never a filter.

The accounting is one debit to the captured liability account and one credit to the funding
account, and that is the whole ledger effect. Nothing here settles a document: there is no
invoice on the other side of a remittance, only a balance, so a partial remittance leaves the
remainder owed because the liability read nets the postings -- not because an edge says so.
"""

import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    def created():
        return [C('created_at', sa.String(32), 'UTC time this remittance header was written.', nullable=False),
                identifier('created_by', 'Company principal that wrote this remittance.'),
                C('created_via', sa.String(16), 'Interface that wrote this remittance.', nullable=False),
                C('audit_event_id', sa.String(26), 'Audit event that committed this record.',
                  sa.ForeignKey('audit_events.id'), nullable=False)]

    sales_tax_payment_profiles = T('sales_tax_payment_profiles',
        identifier('revision_id', 'Immutable revision owning this one-to-one remittance header.', primary_key=True),
        identifier('transaction_id', 'Stable sales-tax-payment document owning this revision.'),
        *created(),
        C('type', sa.String(32), 'Document type; sales_tax_payment is the only one.', nullable=False),
        identifier('agency_id', 'Flagged tax-agency vendor this remittance is paid to.', 'vendors.id'),
        identifier('liability_account_id', 'Sales tax payable account debited for the remitted amount.', 'accounts.id'),
        identifier('funding_account_id', 'Bank or credit-card account credited for the amount remitted.', 'accounts.id'),
        C('funding_kind', sa.String(16), 'bank_cash when a bank account paid it; card_liability when a credit card did.', nullable=False),
        identifier('payment_method_id', 'Captured payment method.', 'payment_methods.id'),
        C('check_number', sa.String(64), 'Number written on the paper check, as entered; null unless the method is a check.', nullable=True),
        C('reference', sa.String(128), 'Entered remittance reference; null when blank.', nullable=True),
        C('through_date', sa.String(10), 'Inclusive period end the remitted liability was computed through, YYYY-MM-DD.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Positive home-currency amount remitted to the agency.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the remitted amount.', nullable=False),
        identifier('liability_posting_source_id', 'Exact attribution row that debited the sales tax liability.'),
        C('profile_snapshot', sa.Text, 'Versioned typed JSON object of resolved agency, account, method and origin facts.', nullable=False),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_sales_tax_payment_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['transaction_revisions.transaction_id', 'transaction_revisions.id'],
            name='fk_sales_tax_payment_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'],
            ['transactions.id', 'transactions.type'], name='fk_sales_tax_payment_profile_type'),
        sa.ForeignKeyConstraint(['transaction_id', 'liability_posting_source_id'],
            ['posting_line_sources.transaction_id', 'posting_line_sources.id'],
            name='fk_sales_tax_payment_profile_attribution'),
        sa.CheckConstraint("type = 'sales_tax_payment'", name='ck_sales_tax_payment_profile_type'),
        sa.CheckConstraint("funding_kind IN ('bank_cash', 'card_liability')", name='ck_sales_tax_payment_funding_kind'),
        # A credit card has no check to write a number on; the service also refuses a number
        # whose method is not a check, which is a fact about the method list rather than the shape.
        sa.CheckConstraint("check_number IS NULL OR funding_kind = 'bank_cash'", name='ck_sales_tax_payment_check_number'),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0",
                           name='ck_sales_tax_payment_amount_positive'),
        sa.CheckConstraint("through_date LIKE '____-__-__'", name='ck_sales_tax_payment_through_date'),
        sa.CheckConstraint('json_valid(profile_snapshot) AND json_type(profile_snapshot) = \'object\'',
                           name='ck_sales_tax_payment_profile_snapshot_object'),
        sa.Index('ix_sales_tax_payment_profiles_agency', 'agency_id', 'through_date', 'transaction_id'),
        sa.Index('ix_sales_tax_payment_profiles_attribution', 'liability_posting_source_id'),
        description='Immutable one-to-one sales tax remittance headers naming the agency whose liability fell.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


def guard_statements():
    """A remittance header is history: it is written once and never rewritten."""
    for action in ('UPDATE', 'DELETE'):
        yield (f'CREATE TRIGGER sales_tax_payment_profiles_immutable_{action.lower()} '
               f'BEFORE {action} ON sales_tax_payment_profiles '
               "BEGIN SELECT RAISE(ABORT, 'immutable remittance history'); END")
    yield ('CREATE TRIGGER sales_tax_payment_profiles_agency_is_flagged BEFORE INSERT ON sales_tax_payment_profiles\n'
           'WHEN NOT EXISTS (SELECT 1 FROM vendors WHERE id = NEW.agency_id AND is_tax_agency = 1)\n'
           "BEGIN SELECT RAISE(ABORT, 'sales tax is remitted to a flagged tax agency'); END")
