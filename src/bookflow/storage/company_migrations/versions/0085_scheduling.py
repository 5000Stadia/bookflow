"""Unattended scheduling and reminders (R111).

``company_info`` gains five preferences: how many days of missed automatic memorized entries the
scheduler still enters (null: all of them, as the anchor does), and the overdue notice policy --
days overdue (null: notices off), weekly or monthly, a minimum overdue balance, and whether a
notice is a statement or a short letter. Three new tables: ``todos`` (to-do notes assigned to a
person or an agent), ``scheduled_revision_authority`` (the authority an agent held when it saved a memorized
revision), ``scheduled_group_authority`` (who last set each memorized group's schedule), ``scheduled_holds``
(automatic entries left for a person) and ``overdue_notices`` (the queue of generated notices, one per customer and
notice period). Additive: nothing existing changes. DDL below is frozen: this migration never
imports current application metadata.
"""
from alembic import op
import sqlalchemy as sa

revision = 'co0085'
down_revision = 'co0087'
branch_labels = depends_on = None
DDL = (
    "\nCREATE TABLE todos (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\ttitle VARCHAR(160) NOT NULL, \n\tnotes TEXT, \n\tdue_date VARCHAR(10) NOT NULL, \n\tpriority VARCHAR(8) NOT NULL, \n\tassignee_id VARCHAR(26), \n\tstatus VARCHAR(10) NOT NULL, \n\tlink_type VARCHAR(16), \n\tlink_id VARCHAR(26), \n\tcompleted_at VARCHAR(32), \n\tcompleted_by VARCHAR(26), \n\tsource_ref VARCHAR(64), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_todo_title CHECK (length(trim(title)) > 0), \n\tCONSTRAINT ck_todo_due_date CHECK (length(due_date) = 10), \n\tCONSTRAINT ck_todo_priority CHECK (priority IN ('high', 'medium', 'low')), \n\tCONSTRAINT ck_todo_status CHECK (status IN ('open', 'done', 'inactive')), \n\tCONSTRAINT ck_todo_link_type CHECK (link_type IS NULL OR link_type IN ('customer', 'vendor', 'transaction', 'memorized')), \n\tCONSTRAINT ck_todo_link_pair CHECK ((link_type IS NULL) = (link_id IS NULL)), \n\tCONSTRAINT ck_todo_completed CHECK ((status = 'done') = (completed_at IS NOT NULL))\n)\n\n",
    "\nCREATE TABLE overdue_notices (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\tperiod_key VARCHAR(10) NOT NULL, \n\tas_of VARCHAR(10) NOT NULL, \n\ttemplate VARCHAR(12) NOT NULL, \n\tstatement_from VARCHAR(10) NOT NULL, \n\toverdue_minor_units BIGINT NOT NULL, \n\tbalance_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\toldest_due_date VARCHAR(10) NOT NULL, \n\tdays_overdue INTEGER NOT NULL, \n\tdocuments TEXT NOT NULL, \n\tstatus VARCHAR(10) NOT NULL, \n\tsent_at VARCHAR(32), \n\tsent_by VARCHAR(26), \n\tsent_method VARCHAR(16), \n\tdismissed_reason VARCHAR(280), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_overdue_notice_period UNIQUE (customer_id, period_key), \n\tCONSTRAINT ck_overdue_notice_dates CHECK (length(period_key) = 10 AND length(as_of) = 10 AND length(statement_from) = 10 AND length(oldest_due_date) = 10), \n\tCONSTRAINT ck_overdue_notice_template CHECK (template IN ('statement', 'letter')), \n\tCONSTRAINT ck_overdue_notice_status CHECK (status IN ('ready', 'sent', 'dismissed')), \n\tCONSTRAINT ck_overdue_notice_sent_method CHECK (sent_method IS NULL OR sent_method IN ('print', 'email', 'other')), \n\tCONSTRAINT ck_overdue_notice_sent CHECK ((status = 'sent') = (sent_at IS NOT NULL)), \n\tCONSTRAINT ck_overdue_notice_overdue CHECK (typeof(overdue_minor_units) = 'integer' AND overdue_minor_units > 0), \n\tCONSTRAINT ck_overdue_notice_balance CHECK (typeof(balance_minor_units) = 'integer'), \n\tCONSTRAINT ck_overdue_notice_days CHECK (days_overdue >= 1), \n\tCONSTRAINT ck_overdue_notice_documents CHECK (json_valid(documents) AND json_type(documents) = 'array'), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)\n\n",
    '\nCREATE TABLE scheduled_revision_authority (\n\trevision_id VARCHAR(26) NOT NULL, \n\tagent_id VARCHAR(26) NOT NULL, \n\tprincipal_id VARCHAR(26), \n\tauthority_epoch INTEGER, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (revision_id)\n)\n\n',
    '\nCREATE TABLE scheduled_group_authority (\n\tgroup_id VARCHAR(26) NOT NULL, \n\tset_by VARCHAR(26) NOT NULL, \n\tprincipal_id VARCHAR(26), \n\tauthority_epoch INTEGER, \n\tset_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (group_id)\n)\n\n',
    '\nCREATE TABLE scheduled_holds (\n\toccurrence_id VARCHAR(26) NOT NULL, \n\treason VARCHAR(400) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (occurrence_id)\n)\n\n',
)
INDEXES = (
    'CREATE INDEX ix_todos_assignee_status_due ON todos (assignee_id, status, due_date)',
    'CREATE INDEX ix_todos_status_due ON todos (status, due_date)',
    'CREATE UNIQUE INDEX ux_todos_source_ref ON todos (source_ref) WHERE source_ref IS NOT NULL',
    'CREATE INDEX ix_overdue_notices_status_as_of ON overdue_notices (status, as_of)',
)
RESERVED = {'todos', 'overdue_notices', 'scheduled_revision_authority', 'scheduled_group_authority', 'scheduled_holds'}
COLUMNS = ('scheduler_catch_up_days', 'overdue_notice_days', 'overdue_notice_frequency', 'overdue_notice_minimum',
           'overdue_notice_template')


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & RESERVED:
        raise RuntimeError('co0085 reserved object exists')
    columns = {row[1].lower() for row in db.exec_driver_sql('PRAGMA table_xinfo(company_info)')}
    if columns & set(COLUMNS):
        raise RuntimeError('co0085 scheduling column already exists')
    op.add_column('company_info', sa.Column(
        'scheduler_catch_up_days', sa.Integer,
        sa.CheckConstraint('scheduler_catch_up_days IS NULL OR scheduler_catch_up_days BETWEEN 0 AND 3650',
                           name='ck_company_scheduler_catch_up_days'), nullable=True))
    op.add_column('company_info', sa.Column(
        'overdue_notice_days', sa.Integer,
        sa.CheckConstraint('overdue_notice_days IS NULL OR overdue_notice_days BETWEEN 1 AND 3650',
                           name='ck_company_overdue_notice_days'), nullable=True))
    op.add_column('company_info', sa.Column(
        'overdue_notice_frequency', sa.String(12),
        sa.CheckConstraint("overdue_notice_frequency IN ('weekly','monthly')", name='ck_company_overdue_notice_frequency'),
        nullable=False, server_default='monthly'))
    op.add_column('company_info', sa.Column('overdue_notice_minimum', sa.String(24), nullable=True))
    op.add_column('company_info', sa.Column(
        'overdue_notice_template', sa.String(12),
        sa.CheckConstraint("overdue_notice_template IN ('statement','letter')", name='ck_company_overdue_notice_template'),
        nullable=False, server_default='statement'))
    for statement in DDL + INDEXES:
        db.exec_driver_sql(statement)
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0085 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
