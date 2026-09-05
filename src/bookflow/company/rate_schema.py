"""Declared dated exchange-rate storage, independent of mutable list records."""
import sqlalchemy as sa


def define_tables(metadata, column, table):
    rates = table(
        'exchange_rates',
        column('id', sa.String(26), 'Stable rate ULID.', primary_key=True),
        column('version', sa.Integer, 'Positive optimistic-concurrency version.', nullable=False),
        column('date', sa.String(10), 'Exact accounting date, YYYY-MM-DD.', nullable=False),
        column('from_currency', sa.String(3), 'Original ISO currency code.', nullable=False),
        column('to_currency', sa.String(3), 'Company home ISO currency code.', nullable=False),
        column('rate', sa.String(31), 'Canonical positive home major units per original major unit.', nullable=False),
        column('source', sa.String(16), 'Rate provenance; manual.', nullable=False),
        column('entered_by', sa.String(26), 'Principal who most recently set the rate.', sa.ForeignKey('principals.user_id'), nullable=False),
        column('entered_at', sa.String(32), 'UTC timestamp when the rate was most recently set.', nullable=False),
        sa.UniqueConstraint('date', 'from_currency', 'to_currency', name='uq_exchange_rate_pair'),
        sa.CheckConstraint("typeof(version) = 'integer' AND version > 0", name='ck_exchange_rate_version'),
        sa.CheckConstraint("length(from_currency) = 3 AND from_currency NOT GLOB '*[^A-Z]*' AND length(to_currency) = 3 AND to_currency NOT GLOB '*[^A-Z]*'", name='ck_exchange_rate_currencies'),
        sa.CheckConstraint('from_currency <> to_currency', name='ck_exchange_rate_foreign'),
        sa.CheckConstraint("source = 'manual'", name='ck_exchange_rate_source'),
        sa.CheckConstraint("typeof(rate) = 'text' AND length(CAST(rate AS BLOB)) BETWEEN 1 AND 31", name='ck_exchange_rate_text'),
        description='Manually entered exact-date currency rates with stable identity and audited versions.',
    )
    return {'exchange_rates': rates}
