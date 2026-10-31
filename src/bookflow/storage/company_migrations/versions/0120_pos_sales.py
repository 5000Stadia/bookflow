"""co0120. POS sales (R114 increment 5, design/point-of-sale.md §4, §13).

One new table, ``pos_sales``: a completed POS sale's ticket (its operation key, unique), register, clerk, posted
document and tender, written beside the sales receipt it posted, moving no stored row. Guards: a POS sale is never
updated or deleted. DDL below is frozen: this migration never imports current application metadata.

Numbered co0120 because the bank rules work holds co0119; it sits on co0118 until that lands, and is re-chained (and
renumbered if need be) at the merge.
"""
from alembic import op

revision = 'co0120'
down_revision = 'co0118'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE pos_sales (\n\tid VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tticket VARCHAR(64) NOT NULL, \n\tinput_sha256 VARCHAR(64) NOT NULL, \n\tregister_id VARCHAR(26) NOT NULL, \n\tdocument_type VARCHAR(24) NOT NULL, \n\tdocument_id VARCHAR(26) NOT NULL, \n\tcustomer_named BOOLEAN NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\ttotal_minor_units BIGINT NOT NULL, \n\ttendered_minor_units BIGINT NOT NULL, \n\tchange_minor_units BIGINT NOT NULL, \n\ttenders TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_pos_sales_document_type CHECK (document_type IN ('sales_receipt', 'invoice')), \n\tCONSTRAINT ck_pos_sales_tender CHECK (change_minor_units >= 0 AND tendered_minor_units >= total_minor_units), \n\tCONSTRAINT uq_pos_sales_ticket UNIQUE (ticket), \n\tFOREIGN KEY(register_id) REFERENCES pos_registers (id), \n\tFOREIGN KEY(document_id) REFERENCES transactions (id)\n)\n\n",)
INDEXES = ('CREATE INDEX ix_pos_sales_document_id ON pos_sales (document_id)',
           'CREATE INDEX ix_pos_sales_register_id ON pos_sales (register_id)')
GUARDS = ("CREATE TRIGGER pos_sales_no_update BEFORE UPDATE ON pos_sales BEGIN SELECT RAISE(ABORT, 'a POS sale is never changed'); END",
          "CREATE TRIGGER pos_sales_no_delete BEFORE DELETE ON pos_sales BEGIN SELECT RAISE(ABORT, 'a POS sale is never deleted'); END")
TABLES = ('pos_sales',)
# What a test rewinding this head drops: the guards, then the table.
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    connection = op.get_bind()
    reserved = set(TABLES) | set(TRIGGERS)
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0120 reserved object already exists')
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
