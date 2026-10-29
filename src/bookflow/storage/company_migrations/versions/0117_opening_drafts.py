"""co0117. Guided opening balances (R107 increment 7, design/bulk-import.md §6).

One new table, moving no stored row: ``opening_drafts`` (what an owner typed toward an opening: the cutover date, each
step's rows, the totals given, its state and, once applied, the batch its records belong to). Guards: a draft is never
deleted, keeps its identity, and every change moves its version forward. The postings themselves are the cutover
engine's, through the ordinary commands; the batch ledger is co0108's. DDL below is frozen: this migration never imports
current application metadata.
"""
from alembic import op

revision = 'co0117'
down_revision = 'co0116'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE opening_drafts (\n\tid VARCHAR(26) NOT NULL, \n\tlabel VARCHAR(200) NOT NULL, \n\tas_of VARCHAR(10) NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tfiles TEXT NOT NULL, \n\tgiven TEXT NOT NULL, \n\tmake_items TEXT NOT NULL, \n\tclearing_account VARCHAR(200), \n\tbatch_id VARCHAR(26), \n\tsummary TEXT, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tapplied_at VARCHAR(32), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_opening_draft_state CHECK (state IN ('draft', 'planned', 'applying', 'applied', 'rolled_back', 'failed', 'reversed', 'discarded')), \n\tFOREIGN KEY(batch_id) REFERENCES batches (id)\n)\n\n",)
INDEXES = ('CREATE INDEX ix_opening_drafts_created ON opening_drafts (created_at)',)
GUARDS = ("CREATE TRIGGER opening_drafts_no_delete BEFORE DELETE ON opening_drafts BEGIN SELECT RAISE(ABORT, 'opening drafts are never deleted'); END", "CREATE TRIGGER opening_drafts_frozen BEFORE UPDATE ON opening_drafts WHEN NEW.id IS NOT OLD.id OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by OR NEW.version <= OLD.version BEGIN SELECT RAISE(ABORT, 'an opening draft keeps its identity and moves its version forward'); END")
TABLES = ('opening_drafts',)
# What a test rewinding this head drops: the guards, then the table.
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    connection = op.get_bind()
    reserved = set(TABLES) | set(TRIGGERS) | {statement.split()[2] for statement in INDEXES}
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0117 reserved object already exists')
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
