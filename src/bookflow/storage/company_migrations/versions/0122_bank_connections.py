"""co0122: live feed connections, account cursors and first-backfill approval."""
from alembic import op

revision = "co0122"
down_revision = "co0121"
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE bank_feed_connections (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tname_key VARCHAR(100) NOT NULL, \n\tkind VARCHAR(64) NOT NULL, \n\tsettings TEXT NOT NULL, \n\tsecret_ref VARCHAR(200), \n\tenabled BOOLEAN NOT NULL, \n\tenabled_host VARCHAR(64), \n\testablished_by VARCHAR(26) NOT NULL, \n\testablished_for VARCHAR(26), \n\testablished_epoch INTEGER, \n\tcheck_requested BOOLEAN NOT NULL, \n\tlast_check_ok BOOLEAN, \n\tlast_check_at VARCHAR(32), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_bank_connection_name CHECK (length(trim(name)) BETWEEN 1 AND 100), \n\tCONSTRAINT ck_bank_connection_settings CHECK (json_valid(settings) AND json_type(settings)='object'), \n\tCONSTRAINT ck_bank_connection_secret CHECK (secret_ref IS NULL OR secret_ref GLOB 'keyring:?*/?*'), \n\tCONSTRAINT ck_bank_connection_host CHECK ((enabled = 0 AND check_requested = 0) OR enabled_host IS NOT NULL), \n\tUNIQUE (name_key)\n)\n\n", '\nCREATE TABLE bank_feed_connection_accounts (\n\tconnection_id VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tremote_account VARCHAR(255) NOT NULL, \n\tbackfill_approved BOOLEAN NOT NULL, \n\tcursor TEXT, \n\tstaged_page TEXT, \n\tstaged_fingerprint VARCHAR(64), \n\tapproved_fingerprint VARCHAR(64), \n\tPRIMARY KEY (connection_id), \n\tCONSTRAINT ck_bank_connection_page CHECK (staged_page IS NULL OR json_valid(staged_page)), \n\tFOREIGN KEY(connection_id) REFERENCES bank_feed_connections (id), \n\tUNIQUE (account_id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id)\n)\n\n')

def upgrade():
    for sql in DDL:
        op.get_bind().exec_driver_sql(sql)
    op.get_bind().exec_driver_sql(
        "ALTER TABLE bank_feed_imports ADD COLUMN connection_id VARCHAR(26) "
        "REFERENCES bank_feed_connections(id)")

def downgrade():
    raise RuntimeError("Company migrations are forward-only")
