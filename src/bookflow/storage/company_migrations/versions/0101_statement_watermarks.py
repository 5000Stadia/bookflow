"""Verified history watermark of statement effect storage (R180 stage 2, design/performance.md "Stage 2").

One table, ``statement_watermarks``: per bank or card account, where in append order the last full
proof of its statement effect storage reached, which code made it and a digest of the verified prefix.
No existing row changes and nothing is backfilled: an account with no row is proved in full by its next
reconciliation command, which writes the row. DDL below is frozen: this migration never imports current
application metadata.

On an upgrade that changes the derivation or validation code, nothing here needs migrating either: the
stored code version no longer matches, so every account is proved in full once, and only then gets a
new row.
"""
from alembic import op

revision = 'co0101'
down_revision = 'co0100'
branch_labels = depends_on = None

DDL = ('\nCREATE TABLE statement_watermarks (\n\taccount_id VARCHAR(26) NOT NULL, \n\tcode_version VARCHAR(64) NOT NULL, \n\tthrough_seq INTEGER, \n\tcensus TEXT NOT NULL, \n\tdocuments TEXT NOT NULL, \n\taccounts TEXT NOT NULL, \n\tsetup TEXT NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\tverified_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (account_id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id)\n)\n\n',)
TABLES = ('statement_watermarks',)


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(TABLES):
        raise RuntimeError('co0101 reserved object exists')
    for statement in DDL:
        db.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
