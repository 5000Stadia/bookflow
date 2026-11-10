"""co0134: membership plans and dated customer enrollments; the pricing layer starts off (R188 increment 1)."""
from alembic import op
import sqlalchemy as sa
revision = 'co0134'
down_revision = 'co0133'
branch_labels = depends_on = None
TABLES = ('membership_plans', 'customer_memberships')
DDL = ['\nCREATE TABLE membership_plans (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tdescription VARCHAR(500), \n\tprice_level_id VARCHAR(26) NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_membership_plan_name_key UNIQUE (name_key), \n\tCONSTRAINT ck_membership_plan_name CHECK (length(trim(name)) > 0), \n\tCONSTRAINT ck_membership_plan_active CHECK (active IN (0,1)), \n\tFOREIGN KEY(price_level_id) REFERENCES price_levels (id) ON DELETE RESTRICT\n)\n\n', "\nCREATE TABLE customer_memberships (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\tplan_id VARCHAR(26) NOT NULL, \n\tstart_date VARCHAR(10) NOT NULL, \n\tend_date VARCHAR(10), \n\tstatus VARCHAR(10) NOT NULL, \n\tmember_number VARCHAR(40), \n\tmember_number_key VARCHAR(80), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_customer_membership_dates CHECK (end_date IS NULL OR start_date <= end_date), \n\tCONSTRAINT ck_customer_membership_status CHECK (status IN ('active','suspended','ended')), \n\tCONSTRAINT ck_customer_membership_ended CHECK (status != 'ended' OR end_date IS NOT NULL), \n\tCONSTRAINT ck_customer_membership_number CHECK ((member_number IS NULL) = (member_number_key IS NULL)), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(plan_id) REFERENCES membership_plans (id) ON DELETE RESTRICT\n)\n\n"]
INDEXES = ['CREATE INDEX ix_customer_memberships_customer_interval ON customer_memberships (customer_id, start_date, end_date)', 'CREATE INDEX ix_customer_memberships_plan ON customer_memberships (plan_id, id)', 'CREATE UNIQUE INDEX ux_customer_memberships_member_number ON customer_memberships (member_number_key) WHERE member_number_key IS NOT NULL']


def upgrade():
    conn = op.get_bind()
    op.add_column('company_info', sa.Column('membership_pricing_enabled', sa.Boolean(), nullable=False, server_default='0'))
    for statement in DDL + INDEXES:
        conn.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
