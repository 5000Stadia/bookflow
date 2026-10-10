"""Bounced customer checks: one `payment_bounces` row binds a receipt to the documents its return made (R176).

One new table, append-only by triggers, whose insert trigger keeps each reference the kind of
document its column says. Nothing existing changes. DDL below is frozen: this migration never
imports current application metadata.
"""
from alembic import op

revision = 'co0069'
down_revision = 'co0068'
branch_labels = depends_on = None
DDL = ("\nCREATE TABLE payment_bounces (\n\tid VARCHAR(26) NOT NULL, \n\tpayment_id VARCHAR(26) NOT NULL, \n\toperation_key VARCHAR(128) NOT NULL, \n\trefund_id VARCHAR(26) NOT NULL, \n\tbounce_date VARCHAR(10) NOT NULL, \n\treturned_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tbank_fee_journal_id VARCHAR(26), \n\tbank_fee_minor_units BIGINT, \n\tcustomer_fee_invoice_id VARCHAR(26), \n\tcustomer_fee_minor_units BIGINT, \n\treason VARCHAR(500) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_payment_bounce_returned CHECK (typeof(returned_minor_units) = 'integer' AND returned_minor_units > 0), \n\tCONSTRAINT ck_payment_bounce_bank_fee CHECK ((bank_fee_journal_id IS NULL) = (bank_fee_minor_units IS NULL) AND (bank_fee_minor_units IS NULL OR (typeof(bank_fee_minor_units) = 'integer' AND bank_fee_minor_units > 0))), \n\tCONSTRAINT ck_payment_bounce_customer_fee CHECK ((customer_fee_invoice_id IS NULL) = (customer_fee_minor_units IS NULL) AND (customer_fee_minor_units IS NULL OR (typeof(customer_fee_minor_units) = 'integer' AND customer_fee_minor_units > 0))), \n\tCONSTRAINT ck_payment_bounce_reason CHECK (length(trim(reason)) > 0), \n\tCONSTRAINT uq_payment_bounce_refund UNIQUE (refund_id), \n\tCONSTRAINT uq_payment_bounce_operation_key UNIQUE (operation_key), \n\tFOREIGN KEY(payment_id) REFERENCES transactions (id), \n\tFOREIGN KEY(refund_id) REFERENCES transactions (id), \n\tFOREIGN KEY(bank_fee_journal_id) REFERENCES transactions (id), \n\tFOREIGN KEY(customer_fee_invoice_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", 'CREATE INDEX ix_payment_bounces_payment ON payment_bounces (payment_id, bounce_date)')
TRIGGERS = ("CREATE TRIGGER payment_bounces_no_update BEFORE UPDATE ON payment_bounces BEGIN SELECT RAISE(ABORT, 'payment_bounces is append-only'); END", "CREATE TRIGGER payment_bounces_no_delete BEFORE DELETE ON payment_bounces BEGIN SELECT RAISE(ABORT, 'payment_bounces is append-only'); END", "CREATE TRIGGER payment_bounces_document_types BEFORE INSERT ON payment_bounces\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.payment_id AND type = 'payment')\nOR NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.refund_id AND type = 'customer_refund')\nOR (NEW.bank_fee_journal_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.bank_fee_journal_id AND type = 'journal_entry'))\nOR (NEW.customer_fee_invoice_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.customer_fee_invoice_id AND type = 'invoice'))\nBEGIN SELECT RAISE(ABORT, 'payment bounce references a document of the wrong type'); END")


def upgrade():
    connection = op.get_bind()
    for statement in (*DDL, *TRIGGERS):
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0069 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
