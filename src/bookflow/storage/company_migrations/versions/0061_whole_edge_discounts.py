"""Let an early-payment discount be the whole of its settlement edge.

The anchor's Receive Payments and Pay Bills windows accept a discount on a document the payment
gives no money -- a customer short-pays one invoice and takes the discount on another. co0060's
edge guards required each discount to be strictly smaller than its edge; this revision replaces
those two triggers with ones that allow equality. No table, column or row changes. DDL below is
frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0061'
down_revision = 'co0060'
branch_labels = depends_on = None

REPLACED = ('payment_discounts_edge', 'bill_payment_discounts_edge')
GUARDS = ("CREATE TRIGGER payment_discounts_edge BEFORE INSERT ON payment_discounts WHEN NOT EXISTS (SELECT 1 FROM applications a WHERE a.id = NEW.application_id AND a.kind = 'apply' AND a.paying_transaction_id = NEW.transaction_id AND a.paid_transaction_id = NEW.invoice_id AND a.source_component_key_id = NEW.component_key_id AND a.currency = NEW.currency AND a.amount_minor_units >= NEW.amount_minor_units) BEGIN SELECT RAISE(ABORT, 'discount must be part of its own receipt settlement'); END", "CREATE TRIGGER bill_payment_discounts_edge BEFORE INSERT ON bill_payment_discounts WHEN NOT EXISTS (SELECT 1 FROM ap_applications a WHERE a.id = NEW.application_id AND a.kind = 'apply' AND a.source_transaction_id = NEW.transaction_id AND a.source_component_id = NEW.source_component_id AND a.obligation_transaction_id = NEW.bill_id AND a.currency = NEW.currency AND a.amount_minor_units >= NEW.amount_minor_units) BEGIN SELECT RAISE(ABORT, 'discount must be part of its own bill settlement'); END")


def upgrade():
    c = op.get_bind()
    present = {row[0] for row in c.exec_driver_sql("SELECT name FROM sqlite_schema WHERE type='trigger'")}
    if not set(REPLACED) <= present:
        raise RuntimeError('co0061 expects the co0060 discount edge guards')
    for name in REPLACED:
        c.exec_driver_sql('DROP TRIGGER ' + name)
    for statement in GUARDS:
        c.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
