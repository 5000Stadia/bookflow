"""Document delivery, increment 3: flags, the customer's method and batches (R112, design/document-delivery.md §5, §8).

Two new tables and one column. ``delivery_flags`` holds the anchor's *Email later*: one row per document a person
flagged (``wanted`` true) or opted out of the company's auto-flag date (``wanted`` false). ``delivery_batches`` holds a
batch of drafted messages: the selection as asked, the messages planned (fixed when it began), who was skipped and why,
how far drafting has gone, and its state; a batch is cancelled as one unit. ``delivery_messages.batch_id`` names the
batch a message was drafted in (null for a single draft). Moves no stored row and adds no guard. DDL below is frozen:
this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0108'
down_revision = 'co0107'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE delivery_flags (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tsource_kind VARCHAR(16) NOT NULL, \n\tsource_id VARCHAR(26) NOT NULL, \n\twanted BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_delivery_flag_kind CHECK (source_kind IN ('invoice', 'sales-receipt', 'estimate')), \n\tCONSTRAINT uq_delivery_flag_source UNIQUE (source_kind, source_id)\n)\n\n", "\nCREATE TABLE delivery_batches (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tnumber VARCHAR(64) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tselection TEXT NOT NULL, \n\ttargets TEXT NOT NULL, \n\tskipped TEXT NOT NULL, \n\tplanned INTEGER NOT NULL, \n\tposition INTEGER NOT NULL, \n\tsend BOOLEAN NOT NULL, \n\tstate VARCHAR(10) NOT NULL, \n\tschedule_run_id VARCHAR(26), \n\tcancelled_reason VARCHAR(500), \n\tgo_by VARCHAR(26), \n\tfingerprint VARCHAR(64) NOT NULL, \n\tdrafted TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_delivery_batch_state CHECK (state IN ('drafting', 'done', 'cancelled')), \n\tCONSTRAINT ck_delivery_batch_cancelled CHECK ((state = 'cancelled') = (cancelled_reason IS NOT NULL)), \n\tCONSTRAINT ck_delivery_batch_json CHECK (json_valid(selection) AND json_type(selection) = 'object' AND json_valid(targets) AND json_type(targets) = 'array' AND json_valid(skipped) AND json_type(skipped) = 'array' AND json_valid(drafted) AND json_type(drafted) = 'object'), \n\tCONSTRAINT ck_delivery_batch_position CHECK (planned >= 0 AND position BETWEEN 0 AND planned), \n\tCONSTRAINT uq_delivery_batch_number UNIQUE (number)\n)\n\n")
COLUMNS = ('ALTER TABLE delivery_messages ADD COLUMN batch_id VARCHAR(26) REFERENCES delivery_batches (id)',)
INDEXES = ('CREATE INDEX ix_delivery_messages_batch ON delivery_messages (batch_id)',)
GUARDS = ()
TABLES = ('delivery_flags', 'delivery_batches')
# What a test rewinding a head file drops: the index and the column, then the tables.
TRIGGERS = ()


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & (set(TABLES) | {'ix_delivery_messages_batch'}):
        raise RuntimeError('co0108 reserved object exists')
    if any(row[1] == 'batch_id' for row in db.exec_driver_sql('PRAGMA table_info(delivery_messages)')):
        raise RuntimeError('co0108 reserved column exists')
    for statement in DDL + COLUMNS + INDEXES:
        db.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
