"""co0124: shared versioned held POS tickets; frozen DDL on co0123."""
from alembic import op
revision = 'co0124'
down_revision = 'co0123'
branch_labels = depends_on = None
TABLES = ('pos_tickets',)
DDL = ("\nCREATE TABLE pos_tickets (\n\tid VARCHAR(64) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tregister_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26), \n\tlabel VARCHAR(400) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tlines TEXT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\ttotal_minor_units BIGINT NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_pos_tickets_status CHECK (status IN ('held','open','completed','discarded')), \n\tFOREIGN KEY(register_id) REFERENCES pos_registers (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)\n\n", 'CREATE INDEX ix_pos_tickets_status_updated ON pos_tickets (status, updated_at)')


def upgrade():
    conn = op.get_bind()
    if conn.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name='pos_tickets'").first():
        raise RuntimeError('co0124 reserved object already exists')
    for statement in DDL:
        conn.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
