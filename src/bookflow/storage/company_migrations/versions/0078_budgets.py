"""Budgets, their amounts, and memorized reports (R109).

Three new tables: ``budgets`` (a named plan for one fiscal year), ``budget_amounts`` (one nonzero
amount per budget, account, customer or class, and month) and ``memorized_reports`` (a report and
its options kept under a name). Additive: nothing existing changes. Budgets never post. DDL below
is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0078'
down_revision = 'co0073'
branch_labels = depends_on = None
DDL = (
    "\nCREATE TABLE budgets (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tfiscal_year INTEGER NOT NULL, \n\tfirst_month VARCHAR(7) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tdimension VARCHAR(8) NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_budget_name_key UNIQUE (name_key), \n\tCONSTRAINT ck_budget_name_nonblank CHECK (length(trim(name)) > 0), \n\tCONSTRAINT ck_budget_kind CHECK (kind IN ('profit_and_loss','balance_sheet')), \n\tCONSTRAINT ck_budget_dimension CHECK (dimension IN ('none','customer','class')), \n\tCONSTRAINT ck_budget_balance_sheet_company CHECK (kind = 'profit_and_loss' OR dimension = 'none'), \n\tCONSTRAINT ck_budget_fiscal_year CHECK (typeof(fiscal_year) = 'integer' AND fiscal_year BETWEEN 1 AND 9998), \n\tCONSTRAINT ck_budget_first_month CHECK (length(first_month) = 7)\n)\n\n",
    "\nCREATE TABLE budget_amounts (\n\tid VARCHAR(26) NOT NULL, \n\tbudget_id VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26), \n\tclass_id VARCHAR(26), \n\tdimension_key VARCHAR(26) NOT NULL, \n\tmonth VARCHAR(7) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_budget_amount_cell UNIQUE (budget_id, account_id, dimension_key, month), \n\tCONSTRAINT ck_budget_amount_integer CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units != 0), \n\tCONSTRAINT ck_budget_amount_dimension_key CHECK (dimension_key = coalesce(customer_id, class_id, '')), \n\tCONSTRAINT ck_budget_amount_one_dimension CHECK (customer_id IS NULL OR class_id IS NULL), \n\tCONSTRAINT ck_budget_amount_month CHECK (length(month) = 7), \n\tFOREIGN KEY(budget_id) REFERENCES budgets (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id)\n)\n\n",
    "\nCREATE TABLE memorized_reports (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tgroup_name VARCHAR(200), \n\treport VARCHAR(64) NOT NULL, \n\tfilters TEXT NOT NULL, \n\tdate_range VARCHAR(32), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_memorized_report_name_key UNIQUE (name_key), \n\tCONSTRAINT ck_memorized_report_name_nonblank CHECK (length(trim(name)) > 0), \n\tCONSTRAINT ck_memorized_report_filters CHECK (json_valid(filters) AND json_type(filters) = 'object')\n)\n\n",
)
INDEXES = (
    'CREATE INDEX ix_budget_amounts_account ON budget_amounts (account_id)',
)


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & {'budgets', 'budget_amounts', 'memorized_reports', 'ix_budget_amounts_account'}:
        raise RuntimeError('co0078 reserved object exists')
    for statement in DDL + INDEXES:
        db.exec_driver_sql(statement)
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0078 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
