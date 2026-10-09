"""Adjusting sales tax due: the one-to-one header of an adjustment to one agency's balance.

A sales tax adjustment is its own document type, the anchor's Adjust Sales Tax Due. It moves
the sales tax payable account against one other account -- an income or expense account for a
rounding difference, a discount or a penalty, or a clearing account when a balance is brought
in from older books -- and it says which agency's balance moved. A journal entry at the
liability says nothing about the agency, so the liability read cannot place it; this table is
what places it.

``sales_tax_adjustment_profiles`` carries:

- ``agency_id`` -- the flagged tax-agency vendor whose balance moved.
- ``direction`` -- ``increase`` credits the liability (the agency is owed more); ``reduce``
  debits it (the agency is owed less).
- ``liability_posting_source_id`` -- the exact ``posting_line_sources`` row of the liability
  leg. The liability read follows it, and a void's reversal follows it back through
  ``reversed_source_id``, the way it follows a remittance's.

The header is written once and never rewritten: a void is a reversal batch on the document, not
a change to this row.
"""

import sqlalchemy as sa

DIRECTIONS = ('increase', 'reduce')


def define_tables(metadata, column, table):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    sales_tax_adjustment_profiles = T('sales_tax_adjustment_profiles',
        identifier('revision_id', 'Immutable revision owning this one-to-one adjustment header.', primary_key=True),
        identifier('transaction_id', 'Stable sales-tax-adjustment document owning this revision.'),
        C('created_at', sa.String(32), 'UTC time this adjustment header was written.', nullable=False),
        identifier('created_by', 'Company principal that wrote this adjustment.'),
        C('created_via', sa.String(16), 'Interface that wrote this adjustment.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that committed this record.',
          sa.ForeignKey('audit_events.id'), nullable=False),
        C('type', sa.String(32), 'Document type; sales_tax_adjustment is the only one.', nullable=False),
        identifier('agency_id', 'Flagged tax-agency vendor whose sales tax balance this adjusts.', 'vendors.id'),
        identifier('liability_account_id', 'Sales tax payable account the adjustment posts to.', 'accounts.id'),
        identifier('offset_account_id', 'Adjustment account on the other side of the liability.', 'accounts.id'),
        C('direction', sa.String(8), 'increase when the agency is owed more (liability credited); reduce when it is owed less (liability debited).', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Positive home-currency amount of the adjustment.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the adjusted amount.', nullable=False),
        identifier('liability_posting_source_id', 'Exact attribution row of the sales tax liability leg.'),
        C('profile_snapshot', sa.Text, 'Versioned typed JSON object of resolved agency and account facts.', nullable=False),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_sales_tax_adjustment_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['transaction_revisions.transaction_id', 'transaction_revisions.id'],
            name='fk_sales_tax_adjustment_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'],
            ['transactions.id', 'transactions.type'], name='fk_sales_tax_adjustment_profile_type'),
        sa.ForeignKeyConstraint(['transaction_id', 'liability_posting_source_id'],
            ['posting_line_sources.transaction_id', 'posting_line_sources.id'],
            name='fk_sales_tax_adjustment_profile_attribution'),
        sa.CheckConstraint("type = 'sales_tax_adjustment'", name='ck_sales_tax_adjustment_profile_type'),
        sa.CheckConstraint("direction IN ('increase', 'reduce')", name='ck_sales_tax_adjustment_direction'),
        sa.CheckConstraint('offset_account_id <> liability_account_id', name='ck_sales_tax_adjustment_offset'),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0",
                           name='ck_sales_tax_adjustment_amount_positive'),
        sa.CheckConstraint('json_valid(profile_snapshot) AND json_type(profile_snapshot) = \'object\'',
                           name='ck_sales_tax_adjustment_profile_snapshot_object'),
        sa.Index('ix_sales_tax_adjustment_profiles_agency', 'agency_id', 'transaction_id'),
        sa.Index('ix_sales_tax_adjustment_profiles_attribution', 'liability_posting_source_id'),
        description='Immutable one-to-one sales tax adjustment headers naming the agency whose balance moved.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


def guard_statements():
    """An adjustment header is history: it is written once and never rewritten."""
    for action in ('UPDATE', 'DELETE'):
        yield (f'CREATE TRIGGER sales_tax_adjustment_profiles_immutable_{action.lower()} '
               f'BEFORE {action} ON sales_tax_adjustment_profiles '
               "BEGIN SELECT RAISE(ABORT, 'immutable sales tax adjustment history'); END")
    yield ('CREATE TRIGGER sales_tax_adjustment_profiles_agency_is_flagged BEFORE INSERT ON sales_tax_adjustment_profiles\n'
           'WHEN NOT EXISTS (SELECT 1 FROM vendors WHERE id = NEW.agency_id AND is_tax_agency = 1)\n'
           "BEGIN SELECT RAISE(ABORT, 'sales tax is adjusted for a flagged tax agency'); END")
