"""Finance charges: the company's late-charge policy and one row per charge assessed (R120).

``company_info`` gains the seven finance charge preferences -- annual rate, minimum charge, grace
days, the income account, whether overdue finance charges are charged again, whether days count
from the due date or the invoice date, and whether charges are marked to be printed. Every existing
company starts with finance charges off: no rate and no account. One new immutable table,
``finance_charges``, says which statement charges are finance charges. Additive: nothing is
rebuilt or rewritten. DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op
import sqlalchemy as sa

revision = 'co0074'
down_revision = 'co0072'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE finance_charges (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\tassessment_date VARCHAR(10) NOT NULL, \n\toverdue_balance_minor_units BIGINT NOT NULL, \n\tcomputed_minor_units BIGINT NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tannual_rate VARCHAR(16) NOT NULL, \n\tgrace_days INTEGER NOT NULL, \n\tcalculated_from VARCHAR(12) NOT NULL, \n\ton_overdue_charges BOOLEAN NOT NULL, \n\tto_be_printed BOOLEAN NOT NULL, \n\tbasis TEXT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_finance_charge_transaction UNIQUE (transaction_id), \n\tCONSTRAINT ck_finance_charge_amount CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_finance_charge_computed CHECK (typeof(computed_minor_units) = 'integer' AND computed_minor_units >= 0), \n\tCONSTRAINT ck_finance_charge_overdue CHECK (typeof(overdue_balance_minor_units) = 'integer'), \n\tCONSTRAINT ck_finance_charge_grace CHECK (grace_days >= 0), \n\tCONSTRAINT ck_finance_charge_from CHECK (calculated_from IN ('due_date','invoice_date')), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n",)
INDEXES = ('CREATE INDEX ix_finance_charges_customer ON finance_charges (customer_id, assessment_date)',
           'CREATE INDEX ix_company_info_finance_charge_account_id ON company_info (finance_charge_account_id)')
GUARDS = (
    "CREATE TRIGGER finance_charges_immutable_update BEFORE UPDATE ON finance_charges BEGIN SELECT RAISE(ABORT, 'immutable finance charge history'); END",
    "CREATE TRIGGER finance_charges_immutable_delete BEFORE DELETE ON finance_charges BEGIN SELECT RAISE(ABORT, 'immutable finance charge history'); END",
)
COLUMNS = (
    'finance_charge_annual_rate', 'finance_charge_minimum', 'finance_charge_grace_days', 'finance_charge_account_id',
    'finance_charge_on_overdue_charges', 'finance_charge_from', 'finance_charge_mark_to_print',
)


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & {'finance_charges', 'ix_company_info_finance_charge_account_id'}:
        raise RuntimeError('co0074 reserved object exists')
    columns = {row[1].lower() for row in db.exec_driver_sql('PRAGMA table_xinfo(company_info)')}
    if columns & set(COLUMNS):
        raise RuntimeError('co0074 finance charge column already exists')
    op.add_column('company_info', sa.Column('finance_charge_annual_rate', sa.String(16), nullable=True))
    op.add_column('company_info', sa.Column('finance_charge_minimum', sa.String(24), nullable=True))
    op.add_column('company_info', sa.Column(
        'finance_charge_grace_days', sa.Integer,
        sa.CheckConstraint('finance_charge_grace_days >= 0', name='ck_company_finance_charge_grace'),
        nullable=False, server_default='0'))
    db.exec_driver_sql('ALTER TABLE company_info ADD COLUMN finance_charge_account_id VARCHAR(26) '
                       'REFERENCES accounts (id) ON DELETE RESTRICT')
    op.add_column('company_info', sa.Column('finance_charge_on_overdue_charges', sa.Boolean, nullable=False, server_default='0'))
    op.add_column('company_info', sa.Column(
        'finance_charge_from', sa.String(12),
        sa.CheckConstraint("finance_charge_from IN ('due_date','invoice_date')", name='ck_company_finance_charge_from'),
        nullable=False, server_default='due_date'))
    op.add_column('company_info', sa.Column('finance_charge_mark_to_print', sa.Boolean, nullable=False, server_default='0'))
    for statement in DDL + INDEXES + GUARDS:
        db.exec_driver_sql(statement)
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0074 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
