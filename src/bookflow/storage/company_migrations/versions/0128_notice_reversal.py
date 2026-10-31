"""co0128: preserve notice history while releasing reversed periods for regeneration.

Frozen DDL replaces the period uniqueness constraint with a partial unique index.
Existing notices retain their identities and references; none is released on upgrade.
"""
from alembic import op

revision = 'co0128'
down_revision = 'co0127'
branch_labels = depends_on = None
TABLES = TRIGGERS = ()
COLUMNS = ('period_released',)
DDL = "\nCREATE TABLE overdue_notices (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\tperiod_key VARCHAR(10) NOT NULL, \n\tas_of VARCHAR(10) NOT NULL, \n\ttemplate VARCHAR(12) NOT NULL, \n\tstatement_from VARCHAR(10) NOT NULL, \n\toverdue_minor_units BIGINT NOT NULL, \n\tbalance_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\toldest_due_date VARCHAR(10) NOT NULL, \n\tdays_overdue INTEGER NOT NULL, \n\tdocuments TEXT NOT NULL, \n\tstatus VARCHAR(10) NOT NULL, \n\tsent_at VARCHAR(32), \n\tsent_by VARCHAR(26), \n\tsent_method VARCHAR(16), \n\tdismissed_reason VARCHAR(280), \n\tperiod_released BOOLEAN DEFAULT 0 NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_overdue_notice_released CHECK (period_released IN (0, 1) AND (period_released = 0 OR status = 'dismissed')), \n\tCONSTRAINT ck_overdue_notice_dates CHECK (length(period_key) = 10 AND length(as_of) = 10 AND length(statement_from) = 10 AND length(oldest_due_date) = 10), \n\tCONSTRAINT ck_overdue_notice_template CHECK (template IN ('statement', 'letter')), \n\tCONSTRAINT ck_overdue_notice_status CHECK (status IN ('ready', 'sent', 'dismissed')), \n\tCONSTRAINT ck_overdue_notice_sent_method CHECK (sent_method IS NULL OR sent_method IN ('print', 'email', 'other')), \n\tCONSTRAINT ck_overdue_notice_sent CHECK ((status = 'sent') = (sent_at IS NOT NULL)), \n\tCONSTRAINT ck_overdue_notice_overdue CHECK (typeof(overdue_minor_units) = 'integer' AND overdue_minor_units > 0), \n\tCONSTRAINT ck_overdue_notice_balance CHECK (typeof(balance_minor_units) = 'integer'), \n\tCONSTRAINT ck_overdue_notice_days CHECK (days_overdue >= 1), \n\tCONSTRAINT ck_overdue_notice_documents CHECK (json_valid(documents) AND json_type(documents) = 'array'), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)\n\n"
INDEXES = ('CREATE INDEX ix_overdue_notices_status_as_of ON overdue_notices (status, as_of)', 'CREATE UNIQUE INDEX uq_overdue_notice_period ON overdue_notices (customer_id, period_key) WHERE period_released = 0')
COPY_COLUMNS = 'id, version, created_at, created_by, created_via, updated_at, updated_by, updated_via, customer_id, period_key, as_of, template, statement_from, overdue_minor_units, balance_minor_units, currency, oldest_due_date, days_overdue, documents, status, sent_at, sent_by, sent_method, dismissed_reason'


def upgrade():
    conn = op.get_bind()
    if 'period_released' in {row[1] for row in conn.exec_driver_sql('PRAGMA table_info(overdue_notices)')}:
        raise RuntimeError('co0128 reserved column already exists')
    conn.exec_driver_sql(DDL.replace('CREATE TABLE overdue_notices', 'CREATE TABLE overdue_notices_new', 1))
    conn.exec_driver_sql(f'INSERT INTO overdue_notices_new ({COPY_COLUMNS}) SELECT {COPY_COLUMNS} FROM overdue_notices')
    conn.exec_driver_sql('DROP TABLE overdue_notices')
    conn.exec_driver_sql('ALTER TABLE overdue_notices_new RENAME TO overdue_notices')
    for statement in INDEXES:
        conn.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
