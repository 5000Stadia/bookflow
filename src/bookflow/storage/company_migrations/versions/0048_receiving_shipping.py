"""Add captured shipping components without rewriting receipt history or guards."""
from alembic import op
revision = 'co0048'
down_revision = 'co0047'
branch_labels = depends_on = None
COLUMNS = {
    'item_receipt_lines': 'shipping_minor_units BIGINT NOT NULL DEFAULT 0 CONSTRAINT ck_receipt_shipping CHECK (typeof(shipping_minor_units)=\'integer\' AND shipping_minor_units>=0 AND shipping_minor_units<=value_minor_units)',
    'receipt_bill_claims': 'shipping_minor_units BIGINT NOT NULL DEFAULT 0 CONSTRAINT ck_receipt_claim_shipping CHECK (typeof(shipping_minor_units)=\'integer\' AND shipping_minor_units>=0 AND shipping_minor_units<=original_minor_units AND shipping_minor_units<=billed_minor_units)',
}

def upgrade():
    c = op.get_bind()
    for table, column in COLUMNS.items():
        c.exec_driver_sql('ALTER TABLE ' + table + ' ADD COLUMN ' + column)
    if c.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0048 foreign key check failed')

def downgrade():
    raise RuntimeError('Company migrations are forward-only')
