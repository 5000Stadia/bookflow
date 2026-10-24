"""Unit prices and costs finer than a minor unit (R150).

Each stored unit price or cost gains a nullable ``<prefix>_fraction`` column: the signed
remainder, in thousandths of a minor unit, beyond the price rounded half to even into
``<prefix>_minor_units``. NULL is the true value for every existing row (every price before
this revision is whole minor units), so nothing is rebuilt or backfilled and old rows read
byte-identical. An issue's captured fallback cost gains one too, so a short sale's provisional cost
is rounded once at its total. DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0091'
down_revision = 'co0084'
branch_labels = depends_on = None

FRACTIONS = (
    ('items', 'price_fraction'),
    ('items', 'cost_fraction'),
    ('item_vendor_profiles', 'purchase_cost_fraction'),
    ('price_level_items', 'price_fraction'),
    ('sales_line_profiles', 'unit_price_fraction'),
    ('credit_line_profiles', 'unit_price_fraction'),
    ('work_lines', 'unit_price_fraction'),
    ('work_lines', 'estimated_unit_cost_fraction'),
    ('purchase_item_lines', 'unit_cost_fraction'),
    ('vendor_credit_item_lines', 'unit_cost_fraction'),
    ('purchase_order_lines', 'rate_fraction'),
    ('inventory_movements', 'fallback_unit_cost_fraction'),
)
COLUMNS = tuple(
    f"ALTER TABLE {table} ADD COLUMN {name} BIGINT CONSTRAINT ck_{name} CHECK ({name} IS NULL OR "
    f"(typeof({name}) = 'integer' AND {name} BETWEEN -500 AND 500 AND {name} <> 0))"
    for table, name in FRACTIONS)


def upgrade():
    c = op.get_bind()
    for table, name in FRACTIONS:
        if name in {row[1] for row in c.exec_driver_sql(f'PRAGMA table_xinfo({table})')}:
            raise RuntimeError(f'co0091 column already present: {table}.{name}')
    for statement in COLUMNS:
        c.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
