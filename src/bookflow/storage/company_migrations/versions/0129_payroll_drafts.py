"""co0129: private payroll calculator and immutable draft history."""
from alembic import op

revision = "co0129"
down_revision = "co0128"
branch_labels = depends_on = None
TABLES = ('payroll_settings', 'payroll_drafts', 'payroll_draft_revisions', 'payroll_operations')
DDL = ('\nCREATE TABLE payroll_settings (\n\tid INTEGER NOT NULL, \n\tversion INTEGER NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\twage_account VARCHAR(26), \n\temployer_tax_account VARCHAR(26), \n\tnet_pay_account VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_payroll_settings CHECK (id = 1 AND version > 0), \n\tFOREIGN KEY(wage_account) REFERENCES accounts (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(employer_tax_account) REFERENCES accounts (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(net_pay_account) REFERENCES accounts (id) ON DELETE RESTRICT\n)\n\n', "\nCREATE TABLE payroll_drafts (\n\tid VARCHAR(26) NOT NULL, \n\temployee_id VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tpay_date VARCHAR(10), \n\tpayload TEXT NOT NULL, \n\tcalculation TEXT, \n\tsettings_version INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_payroll_draft CHECK (version > 0 AND state IN ('draft', 'prepared') AND currency = 'USD'), \n\tFOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT\n)\n\n", 'CREATE INDEX ix_payroll_draft_employee_date ON payroll_drafts (employee_id, pay_date)', '\nCREATE TABLE payroll_draft_revisions (\n\tdraft_id VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tsnapshot TEXT NOT NULL, \n\treason TEXT NOT NULL, \n\tactor_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (draft_id, version), \n\tFOREIGN KEY(draft_id) REFERENCES payroll_drafts (id) ON DELETE RESTRICT\n)\n\n', '\nCREATE TABLE payroll_operations (\n\toperation_key VARCHAR(128) NOT NULL, \n\tactor_id VARCHAR(26) NOT NULL, \n\tcommand VARCHAR(60) NOT NULL, \n\tinput_hash VARCHAR(64) NOT NULL, \n\toutput TEXT NOT NULL, \n\treason TEXT NOT NULL, \n\tPRIMARY KEY (operation_key)\n)\n\n')
TRIGGERS = ("CREATE TRIGGER payroll_draft_revisions_no_update BEFORE UPDATE ON payroll_draft_revisions BEGIN SELECT RAISE(ABORT, 'payroll history is immutable'); END", "CREATE TRIGGER payroll_draft_revisions_no_delete BEFORE DELETE ON payroll_draft_revisions BEGIN SELECT RAISE(ABORT, 'payroll history is immutable'); END", "CREATE TRIGGER payroll_operations_no_update BEFORE UPDATE ON payroll_operations BEGIN SELECT RAISE(ABORT, 'payroll history is immutable'); END", "CREATE TRIGGER payroll_operations_no_delete BEFORE DELETE ON payroll_operations BEGIN SELECT RAISE(ABORT, 'payroll history is immutable'); END", "CREATE TRIGGER payroll_drafts_no_delete BEFORE DELETE ON payroll_drafts BEGIN SELECT RAISE(ABORT, 'payroll drafts are retained'); END")

def upgrade():
    for statement in DDL + TRIGGERS:
        op.get_bind().exec_driver_sql(statement)

def downgrade():
    raise RuntimeError("Company migrations are forward-only")
