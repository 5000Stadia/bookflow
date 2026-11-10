"""co0138: job membership inheritance settings and the membership a held POS cart was priced with (R188 increment 2).

Both are additive: no job has a setting row (every job inherits, which prices nothing differently while the
membership layer is off) and every existing held ticket has no captured membership (null: not checked).
"""
from alembic import op
import sqlalchemy as sa
revision = 'co0138'
down_revision = 'co0137'
branch_labels = depends_on = None
DDL = ['\nCREATE TABLE customer_membership_inheritance (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\tinherit BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_customer_membership_inheritance_customer UNIQUE (customer_id), \n\tCONSTRAINT ck_customer_membership_inheritance_inherit CHECK (inherit IN (0,1)), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE RESTRICT\n)\n\n']


def upgrade():
    conn = op.get_bind()
    names = {row[0] for row in conn.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if 'customer_membership_inheritance' in names:
        raise RuntimeError('co0138 reserved object exists')
    for statement in DDL:
        conn.exec_driver_sql(statement)
    op.add_column('pos_tickets', sa.Column('membership_token', sa.String(200), nullable=True))


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
