"""Opening 1099 amounts: what a vendor was paid in a year before the company's books began here (co0071).

One row per vendor and year, set by `vendor 1099-opening` (the move-in sets it from the old books'
1099 Summary). The 1099 summary adds the amount to the vendor's payments when the report's dates
include `as_of`, as it would an opening balance dated the cutover. Setting it again changes the row
and its version; each change is its own audit event.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    openings = T('vendor_1099_openings',
        C('id', sa.String(26), 'Stable ULID of this opening amount.', primary_key=True),
        C('version', sa.Integer, 'Optimistic concurrency version; 1 when set first, one more with each change.', nullable=False),
        C('vendor_id', sa.String(26), 'Vendor the amount was paid to.', sa.ForeignKey('vendors.id'), nullable=False),
        C('year', sa.Integer, 'Calendar year the payments were made in.', nullable=False),
        C('as_of', sa.String(10), 'Last day the amount covers, in that year: what was paid from January 1 through this day.',
          nullable=False),
        C('amount_minor_units', sa.BigInteger, 'What was paid, in home-currency minor units; zero when cleared.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the amount.', nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp the amount was first set.', nullable=False),
        C('created_by', sa.String(26), 'Principal who first set it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it was first set through.', nullable=False),
        C('updated_at', sa.String(32), 'UTC timestamp of its last change.', nullable=False),
        C('updated_by', sa.String(26), 'Principal who last changed it.', nullable=False),
        C('updated_via', sa.String(16), 'Interface it was last changed through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event of its last change.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.UniqueConstraint('vendor_id', 'year', name='uq_vendor_1099_opening_year'),
        sa.CheckConstraint("year BETWEEN 1900 AND 9999", name='ck_vendor_1099_opening_year'),
        sa.CheckConstraint("substr(as_of, 1, 4) = printf('%04d', year)", name='ck_vendor_1099_opening_as_of_year'),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units >= 0",
                           name='ck_vendor_1099_opening_amount'),
        sa.CheckConstraint("version >= 1", name='ck_vendor_1099_opening_version'),
        sa.Index('ix_vendor_1099_openings_as_of', 'as_of'),
        description='Opening 1099 amounts: what each vendor was paid in a year before the books began here.')
    return {'vendor_1099_openings': openings}
