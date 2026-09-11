"""Record which money-out document a posted journal entry was entered as.

Additive: one new table and its immutability guards. No existing table is rebuilt, because
a check, a card charge and a transfer already post as journal entries and this migration
changes nothing about that posting.

DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0027'
down_revision = 'co0026'
branch_labels = None
depends_on = None

NEW_TABLES = ('money_out_documents',)
DDL = ("CREATE TABLE money_out_documents (\n\ttransaction_id VARCHAR(26) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (transaction_id), \n\tCONSTRAINT ck_money_out_kind CHECK (kind IN ('check', 'card_charge', 'transfer')), \n\tCONSTRAINT ck_money_out_type CHECK (type = 'journal_entry'), \n\tCONSTRAINT fk_money_out_document_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
       'CREATE INDEX ix_money_out_documents_kind ON money_out_documents (kind, transaction_id)')
GUARDS = ("CREATE TRIGGER money_out_documents_immutable_update BEFORE UPDATE ON money_out_documents BEGIN SELECT RAISE(ABORT, 'immutable money-out document'); END",
          "CREATE TRIGGER money_out_documents_immutable_delete BEFORE DELETE ON money_out_documents BEGIN SELECT RAISE(ABORT, 'immutable money-out document'); END")
OBJECTS = ('ix_money_out_documents_kind', 'money_out_documents',
           'money_out_documents_immutable_delete', 'money_out_documents_immutable_update')


def upgrade():
    connection = op.get_bind()
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if set(OBJECTS).intersection(existing):
        raise RuntimeError('co0027 reserved object already exists')
    for statement in (*DDL, *GUARDS):
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0027 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
