"""The inventory ledger: signed stock movements and the documents that write them.

Additive. Two new tables, their indexes and their guards; no existing table is rebuilt,
because an inventory adjustment and an inventory cost correction post as journal entries and
this migration changes nothing about how a journal entry posts. ``transactions`` keeps the
eleven document types it had, ``document_lines`` keeps the kinds it had, and no CHECK is
widened, so there is no table to rebuild and no trigger to rewrite.

Nothing is backfilled, and there is nothing that could be: before this revision no row
anywhere recorded a quantity or a cost, so a company upgraded here holds exactly what it held
-- nothing -- until an adjustment says otherwise. Opening stock is entered, not inferred.

The two triggers past immutability are the ones the reports depend on. ``match_posting``
requires every movement to be the posting line it claims: same batch, same account, same
effective date, and a signed value equal to that line's debit less its credit. That is the
inventory asset on the balance sheet and the total on the stock reports being the same
number, enforced where the rows are rather than only where they are written.

There is deliberately **no** trigger enumerating which item types carry stock. A migration's
DDL is frozen text and cannot read the item master, so such a list would be a copy that a
later stock-carrying item type silently falsifies -- and widening it would mean rebuilding
this table for a rule the command layer already applies from the one registry that declares
it.

DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0037'
down_revision = 'co0034'
branch_labels = None
depends_on = None

NEW_TABLES = ('inventory_movements', 'inventory_documents')
DDL = (
    "CREATE TABLE inventory_movements (\n\tid VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tposting_batch_id VARCHAR(26) NOT NULL, \n\tposting_line_id VARCHAR(26) NOT NULL, \n\tdocument_line_id VARCHAR(26) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\tsequence BIGINT NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tvalue_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tasset_account_id VARCHAR(26) NOT NULL, \n\toffset_account_id VARCHAR(26) NOT NULL, \n\tclass_id VARCHAR(26), \n\tcorrects_movement_id VARCHAR(26), \n\treverses_movement_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_inventory_movement_batch FOREIGN KEY(transaction_id, posting_batch_id) REFERENCES posting_batches (transaction_id, id), \n\tCONSTRAINT fk_inventory_movement_posting_line FOREIGN KEY(transaction_id, posting_line_id) REFERENCES posting_lines (transaction_id, id), \n\tCONSTRAINT fk_inventory_movement_document_line FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT fk_inventory_movement_corrects FOREIGN KEY(corrects_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT fk_inventory_movement_reverses FOREIGN KEY(reverses_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT uq_inventory_movement_posting_line UNIQUE (posting_line_id), \n\tCONSTRAINT uq_inventory_movement_sequence UNIQUE (sequence), \n\tCONSTRAINT uq_inventory_movement_reversal UNIQUE (reverses_movement_id), \n\tCONSTRAINT ck_inventory_movement_kind CHECK (kind IN ('receipt', 'issue', 'value', 'recost', 'reversal')), \n\tCONSTRAINT ck_inventory_movement_integers CHECK (typeof(quantity_microunits) = 'integer' AND typeof(value_minor_units) = 'integer' AND typeof(sequence) = 'integer' AND sequence > 0), \n\tCONSTRAINT ck_inventory_movement_date CHECK (effective_date LIKE '____-__-__'), \n\tCONSTRAINT ck_inventory_movement_shape CHECK ((kind = 'receipt' AND quantity_microunits > 0 AND value_minor_units > 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'issue' AND quantity_microunits < 0 AND value_minor_units < 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'value' AND quantity_microunits = 0 AND value_minor_units != 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'recost' AND quantity_microunits = 0 AND value_minor_units != 0 AND corrects_movement_id IS NOT NULL AND reverses_movement_id IS NULL) OR (kind = 'reversal' AND reverses_movement_id IS NOT NULL AND corrects_movement_id IS NULL AND (quantity_microunits != 0 OR value_minor_units != 0))), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(asset_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(offset_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id)\n)",
    'CREATE INDEX ix_inventory_movements_corrects ON inventory_movements (corrects_movement_id)',
    'CREATE INDEX ix_inventory_movements_document ON inventory_movements (transaction_id, sequence)',
    'CREATE INDEX ix_inventory_movements_item ON inventory_movements (item_id, effective_date, sequence, id)',
    "CREATE TABLE inventory_documents (\n\ttransaction_id VARCHAR(26) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (transaction_id), \n\tCONSTRAINT ck_inventory_document_kind CHECK (kind IN ('adjustment', 'recost')), \n\tCONSTRAINT ck_inventory_document_type CHECK (type = 'journal_entry'), \n\tCONSTRAINT fk_inventory_document_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    'CREATE INDEX ix_inventory_documents_kind ON inventory_documents (kind, transaction_id)',
)
GUARDS = (
    "CREATE TRIGGER inventory_movements_immutable_update BEFORE UPDATE ON inventory_movements BEGIN SELECT RAISE(ABORT, 'immutable inventory movement'); END",
    "CREATE TRIGGER inventory_movements_immutable_delete BEFORE DELETE ON inventory_movements BEGIN SELECT RAISE(ABORT, 'immutable inventory movement'); END",
    "CREATE TRIGGER inventory_documents_immutable_update BEFORE UPDATE ON inventory_documents BEGIN SELECT RAISE(ABORT, 'immutable inventory document'); END",
    "CREATE TRIGGER inventory_documents_immutable_delete BEFORE DELETE ON inventory_documents BEGIN SELECT RAISE(ABORT, 'immutable inventory document'); END",
    "CREATE TRIGGER inventory_movements_match_posting BEFORE INSERT ON inventory_movements\nWHEN NOT EXISTS (SELECT 1 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id\nWHERE l.id = NEW.posting_line_id AND l.batch_id = NEW.posting_batch_id\nAND l.account_id = NEW.asset_account_id AND b.effective_date = NEW.effective_date\nAND l.debit_minor_units - l.credit_minor_units = NEW.value_minor_units)\nBEGIN SELECT RAISE(ABORT, 'inventory movement does not match its posting line'); END",
    "CREATE TRIGGER inventory_documents_transaction_id_type BEFORE INSERT ON inventory_documents\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND type = 'journal_entry')\nBEGIN SELECT RAISE(ABORT, 'inventory document reference has wrong document type'); END",
)
OBJECTS = ('inventory_documents', 'inventory_documents_immutable_delete', 'inventory_documents_immutable_update', 'inventory_documents_transaction_id_type', 'inventory_movements', 'inventory_movements_immutable_delete', 'inventory_movements_immutable_update', 'inventory_movements_match_posting', 'ix_inventory_documents_kind', 'ix_inventory_movements_corrects', 'ix_inventory_movements_document', 'ix_inventory_movements_item')


def upgrade():
    connection = op.get_bind()
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if set(OBJECTS).intersection(existing):
        raise RuntimeError('co0037 reserved object already exists')
    for statement in (*DDL, *GUARDS):
        connection.exec_driver_sql(statement)
    for name in NEW_TABLES:
        if connection.exec_driver_sql('SELECT 1 FROM main."' + name + '" LIMIT 1').fetchone():
            raise RuntimeError('co0037 inventory storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0037 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
