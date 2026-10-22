"""Customer credit moved between jobs: one `customer_credit_transfers` row binds the pair of documents (R148), and
`payment_write_offs`, the underpayment part of a receipt discount with its own account.

Two new tables, append-only by triggers, whose insert trigger keeps each document reference the
kind its column says. Nothing existing changes. DDL below is frozen: this migration never imports
current application metadata.
"""
from alembic import op

revision = 'co0081'
down_revision = 'co0075'
branch_labels = depends_on = None
DDL = ("\nCREATE TABLE customer_credit_transfers (\n\tid VARCHAR(26) NOT NULL, \n\toperation_key VARCHAR(128) NOT NULL, \n\trequest_hash VARCHAR(64) NOT NULL, \n\tfrom_party_id VARCHAR(26) NOT NULL, \n\tto_party_id VARCHAR(26) NOT NULL, \n\ttransfer_date VARCHAR(10) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcharge_invoice_id VARCHAR(26) NOT NULL, \n\tcredit_memo_id VARCHAR(26) NOT NULL, \n\treason VARCHAR(500) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_credit_transfer_amount CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_credit_transfer_parties CHECK (from_party_id <> to_party_id), \n\tCONSTRAINT ck_credit_transfer_reason CHECK (length(trim(reason)) > 0), \n\tCONSTRAINT uq_credit_transfer_operation_key UNIQUE (operation_key), \n\tCONSTRAINT uq_credit_transfer_charge UNIQUE (charge_invoice_id), \n\tCONSTRAINT uq_credit_transfer_credit_memo UNIQUE (credit_memo_id), \n\tFOREIGN KEY(from_party_id) REFERENCES customers (id), \n\tFOREIGN KEY(to_party_id) REFERENCES customers (id), \n\tFOREIGN KEY(charge_invoice_id) REFERENCES transactions (id), \n\tFOREIGN KEY(credit_memo_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", 'CREATE INDEX ix_credit_transfers_from ON customer_credit_transfers (from_party_id, transfer_date)', 'CREATE INDEX ix_credit_transfers_to ON customer_credit_transfers (to_party_id, transfer_date)', "\nCREATE TABLE payment_write_offs (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tdiscount_id VARCHAR(26) NOT NULL, \n\tapplication_id VARCHAR(26) NOT NULL, \n\tinvoice_id VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_payment_write_off_amount CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT uq_payment_write_off_discount UNIQUE (discount_id), \n\tCONSTRAINT uq_payment_write_off_application UNIQUE (application_id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(discount_id) REFERENCES payment_discounts (id), \n\tFOREIGN KEY(application_id) REFERENCES applications (id), \n\tFOREIGN KEY(invoice_id) REFERENCES transactions (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", 'CREATE INDEX ix_payment_write_offs_payment ON payment_write_offs (transaction_id, id)')
TRIGGERS = (
    "CREATE TRIGGER customer_credit_transfers_no_update BEFORE UPDATE ON customer_credit_transfers BEGIN SELECT RAISE(ABORT, 'customer_credit_transfers is append-only'); END",
    "CREATE TRIGGER customer_credit_transfers_no_delete BEFORE DELETE ON customer_credit_transfers BEGIN SELECT RAISE(ABORT, 'customer_credit_transfers is append-only'); END",
    "CREATE TRIGGER customer_credit_transfers_document_types BEFORE INSERT ON customer_credit_transfers\n"
    "WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.charge_invoice_id AND type = 'invoice')\n"
    "OR NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.credit_memo_id AND type = 'credit_memo')\n"
    "BEGIN SELECT RAISE(ABORT, 'credit transfer references a document of the wrong type'); END",
    "CREATE TRIGGER payment_write_offs_no_update BEFORE UPDATE ON payment_write_offs BEGIN SELECT RAISE(ABORT, 'payment_write_offs is append-only'); END",
    "CREATE TRIGGER payment_write_offs_no_delete BEFORE DELETE ON payment_write_offs BEGIN SELECT RAISE(ABORT, 'payment_write_offs is append-only'); END",
    "CREATE TRIGGER payment_write_offs_edge BEFORE INSERT ON payment_write_offs\n"
    "WHEN NOT EXISTS (SELECT 1 FROM payment_discounts d WHERE d.id = NEW.discount_id AND d.application_id = NEW.application_id\n"
    "AND d.transaction_id = NEW.transaction_id AND d.invoice_id = NEW.invoice_id AND d.amount_minor_units >= NEW.amount_minor_units)\n"
    "BEGIN SELECT RAISE(ABORT, 'a write-off is part of its receipt discount on the same edge'); END",
)


def upgrade():
    connection = op.get_bind()
    for statement in (*DDL, *TRIGGERS):
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0081 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
