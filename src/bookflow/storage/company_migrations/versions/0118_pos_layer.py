"""co0118. The POS layer (R114 increment 4, design/point-of-sale.md §2).

``company_info.pos_enabled`` (off), ``customers.pos_walk_in`` (false: a register's walk-in customer) and one new table, ``pos_registers`` (a till's fixed facts: site, deposit account,
payment methods, walk-in customer, refund account, receipt width and message, shift counting, decoder order), moving
no stored row. Guard: a register is never deleted. DDL below is frozen: this migration never imports current
application metadata.
"""
from alembic import op
import sqlalchemy as sa

revision = 'co0118'
down_revision = 'co0117'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE pos_registers (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tsite_id VARCHAR(26) NOT NULL, \n\tdeposit_account_id VARCHAR(26) NOT NULL, \n\tpayment_method_ids TEXT NOT NULL, \n\twalk_in_customer_id VARCHAR(26) NOT NULL, \n\trefund_account_id VARCHAR(26), \n\treceipt_width INTEGER NOT NULL, \n\treceipt_message VARCHAR(500), \n\tcount_shifts BOOLEAN NOT NULL, \n\tdecoder_order VARCHAR(200), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_pos_registers_receipt_width CHECK (receipt_width IN (80, 58)), \n\tCONSTRAINT uq_pos_registers_name_key UNIQUE (name_key), \n\tFOREIGN KEY(site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(deposit_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(walk_in_customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(refund_account_id) REFERENCES accounts (id)\n)\n\n",)
INDEXES = ()
GUARDS = ("CREATE TRIGGER pos_registers_no_delete BEFORE DELETE ON pos_registers BEGIN SELECT RAISE(ABORT, 'a register is never deleted'); END",)
TABLES = ('pos_registers',)
COMPANY_COLUMNS = ('pos_enabled',)
CUSTOMER_COLUMNS = ('pos_walk_in',)
# What a test rewinding this head drops: the guards, then the table, then the columns.
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    connection = op.get_bind()
    reserved = set(TABLES) | set(TRIGGERS)
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0118 reserved object already exists')
    op.add_column('company_info', sa.Column('pos_enabled', sa.Boolean, nullable=False, server_default='0'))
    op.add_column('customers', sa.Column('pos_walk_in', sa.Boolean, nullable=False, server_default='0'))
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
