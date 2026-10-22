"""Let a vendor credit settle a refund deposit's Accounts Payable line (R149).

A vendor's refund check for an open credit is banked as a deposit whose line comes from Accounts
Payable with the vendor's name, and the credit is applied to that line as it is to a bill. The
settlement edge's payable-type guard admitted bills only; this revision replaces it with one that
also admits a deposit when, and only when, the paying source is a vendor credit. No table, column
or row changes. DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0073'
down_revision = 'co0071'
branch_labels = depends_on = None

REPLACED = 'ap_applications_obligation_transaction_id_type'
GUARD = ("CREATE TRIGGER ap_applications_obligation_transaction_id_type BEFORE INSERT ON ap_applications\n"
         "WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.obligation_transaction_id AND "
         "(type = 'bill' OR (type = 'deposit' AND EXISTS (SELECT 1 FROM ap_source_keys k "
         "WHERE k.id = NEW.source_key_id AND k.source_type = 'vendor_credit'))))\n"
         "BEGIN SELECT RAISE(ABORT, 'settlement reference has wrong document type'); END")


def upgrade():
    c = op.get_bind()
    present = {row[0] for row in c.exec_driver_sql("SELECT name FROM sqlite_schema WHERE type='trigger'")}
    if REPLACED not in present:
        raise RuntimeError('co0073 expects the co0026 payable-type guard')
    c.exec_driver_sql('DROP TRIGGER ' + REPLACED)
    c.exec_driver_sql(GUARD)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
