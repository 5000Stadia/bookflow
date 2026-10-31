"""co0126: immutable POS refund recovery and accounting links."""
from alembic import op
revision = 'co0126'
down_revision = 'co0125'
branch_labels = depends_on = None
TABLES = ('pos_refunds',)
TRIGGERS = ('pos_refunds_no_update', 'pos_refunds_no_delete')
DDL = '''CREATE TABLE pos_refunds (
 id VARCHAR(26) PRIMARY KEY NOT NULL,
 operation_key VARCHAR(64) UNIQUE NOT NULL,
 input_sha256 VARCHAR(64) NOT NULL,
 register_id VARCHAR(26) NOT NULL REFERENCES pos_registers(id),
 sale_id VARCHAR(26) REFERENCES pos_sales(id),
 credit_memo_id VARCHAR(26) NOT NULL REFERENCES transactions(id),
 refund_id VARCHAR(26) REFERENCES transactions(id),
 customer_named BOOLEAN NOT NULL,
 created_at VARCHAR(32) NOT NULL,
 created_by VARCHAR(26) NOT NULL,
 created_via VARCHAR(16) NOT NULL,
 result_json TEXT NOT NULL
)'''


# co0028 allowed invoice returns only. Keep all ownership and exact-release guards;
# this one type guard now also admits sales receipts as return sources.
RETURN_GUARD_NAME = 'credit_source_claims_source_transaction_id_type'
PREVIOUS_RETURN_GUARD = """CREATE TRIGGER credit_source_claims_source_transaction_id_type
BEFORE INSERT ON credit_source_claims
WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.source_transaction_id AND type = 'invoice')
BEGIN SELECT RAISE(ABORT, 'credit reference has wrong document type'); END"""
RETURN_GUARD = """CREATE TRIGGER credit_source_claims_source_transaction_id_type
BEFORE INSERT ON credit_source_claims
WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.source_transaction_id AND type IN ('invoice', 'sales_receipt'))
BEGIN SELECT RAISE(ABORT, 'credit reference has wrong document type'); END"""


def upgrade():
    op.execute('DROP TRIGGER ' + RETURN_GUARD_NAME)
    op.execute(RETURN_GUARD)
    op.execute(DDL)
    op.execute('CREATE INDEX ix_pos_refunds_sale_id ON pos_refunds(sale_id)')
    for action in ('update', 'delete'):
        op.execute(f"CREATE TRIGGER pos_refunds_no_{action} BEFORE {action.upper()} ON pos_refunds BEGIN SELECT RAISE(ABORT, 'a POS refund is never changed'); END")

def downgrade():
    raise RuntimeError('Company migrations are forward-only')
