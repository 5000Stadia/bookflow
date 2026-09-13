"""co0045 captures check/card purchase items without changing existing documents."""
from alembic import op
revision = 'co0045'
down_revision = 'co0044'
branch_labels = None
depends_on = None
NEW_TABLES = ('money_out_item_lines',)
DDL = ("\nCREATE TABLE money_out_item_lines (\n\tdocument_line_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tline_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (document_line_id), \n\tFOREIGN KEY(transaction_id) REFERENCES money_out_documents (transaction_id), \n\tFOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT ck_money_out_item_snapshot CHECK (json_valid(line_snapshot) AND json_type(line_snapshot) = 'object'), \n\tFOREIGN KEY(item_id) REFERENCES items (id)\n)\n\n", 'CREATE INDEX ix_money_out_item_revision ON money_out_item_lines (revision_id, document_line_id)')
GUARDS = ("CREATE TRIGGER money_out_item_lines_no_update BEFORE UPDATE ON money_out_item_lines BEGIN SELECT RAISE(ABORT, 'captured purchase items are immutable'); END", "CREATE TRIGGER money_out_item_lines_no_delete BEFORE DELETE ON money_out_item_lines BEGIN SELECT RAISE(ABORT, 'captured purchase items are immutable'); END")

def upgrade():
    connection = op.get_bind()
    reserved = {'money_out_item_lines', 'ix_money_out_item_revision',
                'money_out_item_lines_no_update', 'money_out_item_lines_no_delete'}
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        quoted = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + quoted + '.sqlite_schema'):
            if row[0].casefold() in reserved:
                raise RuntimeError('co0045 reserved object already exists')
    for statement in (*DDL, *GUARDS):
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0045 foreign key check failed')

def downgrade():
    raise RuntimeError('Company migrations are forward-only')
