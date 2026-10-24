"""Accountant review (R121): designations, accountant's copies, imported changes, adjustments, review marks.

Five new tables and one partial unique index (at most one copy out, or one working copy, at a time).
Additive: nothing existing changes. DDL below is frozen: this migration never imports current
application metadata.
"""
from alembic import op

revision = 'co0088'
down_revision = 'co0090'
branch_labels = depends_on = None
DDL = ('\nCREATE TABLE accountant_designations (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tuser_id VARCHAR(26) NOT NULL, \n\tthrough_date VARCHAR(10) NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tnote VARCHAR(500), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_accountant_designation_user UNIQUE (user_id), \n\tCONSTRAINT ck_accountant_designation_active CHECK (active IN (0,1))\n)\n\n', "\nCREATE TABLE accountant_copies (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tside VARCHAR(10) NOT NULL, \n\tstatus VARCHAR(12) NOT NULL, \n\tdividing_date VARCHAR(10) NOT NULL, \n\torigin_company_id VARCHAR(26) NOT NULL, \n\taccountant_user_id VARCHAR(26), \n\tmac_key VARCHAR(64) NOT NULL, \n\tbase_audit_seq INTEGER, \n\tfile_name VARCHAR(255), \n\tarchive_sha256 VARCHAR(64), \n\tclosed_at VARCHAR(32), \n\tclosed_by VARCHAR(26), \n\tclose_reason VARCHAR(140), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_accountant_copy_side CHECK (side IN ('owner','accountant')), \n\tCONSTRAINT ck_accountant_copy_status CHECK (status IN ('out','returned','cancelled','working')), \n\tCONSTRAINT ck_accountant_copy_side_status CHECK ((side = 'owner' AND status IN ('out','returned','cancelled')) OR (side = 'accountant' AND status = 'working'))\n)\n\n", "\nCREATE TABLE accountant_imports (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tcopy_id VARCHAR(26) NOT NULL, \n\tchange_key VARCHAR(64) NOT NULL, \n\tkind VARCHAR(20) NOT NULL, \n\tresult_type VARCHAR(32), \n\tresult_id VARCHAR(26), \n\taccountant_user_id VARCHAR(26) NOT NULL, \n\taccountant_name VARCHAR(200) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_accountant_import_change UNIQUE (copy_id, change_key), \n\tCONSTRAINT ck_accountant_import_kind CHECK (kind IN ('journal','reclassification','note','review_mark','reviewed_through')), \n\tFOREIGN KEY(copy_id) REFERENCES accountant_copies (id)\n)\n\n", "\nCREATE TABLE accountant_adjustments (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tkind VARCHAR(20) NOT NULL, \n\tsource_transaction_id VARCHAR(26), \n\tfrom_account_id VARCHAR(26), \n\tto_account_id VARCHAR(26), \n\taccountant_user_id VARCHAR(26) NOT NULL, \n\tcopy_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_accountant_adjustment_transaction UNIQUE (transaction_id), \n\tCONSTRAINT ck_accountant_adjustment_kind CHECK (kind IN ('adjusting','reclassification')), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id)\n)\n\n", '\nCREATE TABLE accountant_review_marks (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tthrough_date VARCHAR(10) NOT NULL, \n\tnote VARCHAR(1000), \n\taccountant_user_id VARCHAR(26) NOT NULL, \n\tcopy_id VARCHAR(26), \n\tPRIMARY KEY (id)\n)\n\n')
INDEXES = ("CREATE UNIQUE INDEX ux_accountant_copy_out ON accountant_copies (side) WHERE status IN ('out','working')",)
NEW_OBJECTS = ('accountant_designations', 'accountant_copies', 'accountant_imports', 'accountant_adjustments',
               'accountant_review_marks', 'ux_accountant_copy_out')


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(NEW_OBJECTS):
        raise RuntimeError('co0088 reserved object exists')
    for statement in DDL + INDEXES:
        db.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
