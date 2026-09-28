"""Early-payment discounts taken when a payment settles an invoice or a bill.

Two new immutable tables record which settlement edge a discount was taken on --
``payment_discounts`` for a customer receipt, ``bill_payment_discounts`` for a bill payment --
and ``company_info`` gains the two nullable default discount accounts. Nothing existing is
rebuilt or backfilled: no discount was ever taken before this revision, and NULL is the true
value of both preferences for every company that exists. DDL below is frozen: this migration
never imports current application metadata.
"""
from alembic import op

revision = 'co0060'
down_revision = 'co0059'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE payment_discounts (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcomponent_key_id VARCHAR(26) NOT NULL, \n\tapplication_id VARCHAR(26) NOT NULL, \n\tinvoice_id VARCHAR(26) NOT NULL, \n\tdiscount_account_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tsuggested_minor_units BIGINT NOT NULL, \n\tdiscount_date VARCHAR(10), \n\tterms_percent_millionths BIGINT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_payment_discount_amount CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_payment_discount_suggested CHECK (typeof(suggested_minor_units) = 'integer' AND suggested_minor_units >= 0), \n\tCONSTRAINT ck_payment_discount_percent CHECK (terms_percent_millionths IS NULL OR terms_percent_millionths BETWEEN 0 AND 100000000), \n\tCONSTRAINT uq_payment_discount_application UNIQUE (application_id), \n\tCONSTRAINT fk_payment_discount_component FOREIGN KEY(transaction_id, component_key_id) REFERENCES payment_component_keys (transaction_id, id), \n\tFOREIGN KEY(application_id) REFERENCES applications (id), \n\tFOREIGN KEY(invoice_id) REFERENCES transactions (id), \n\tFOREIGN KEY(discount_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE bill_payment_discounts (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tsource_component_id VARCHAR(26) NOT NULL, \n\tapplication_id VARCHAR(26) NOT NULL, \n\tbill_id VARCHAR(26) NOT NULL, \n\tdiscount_account_id VARCHAR(26) NOT NULL, \n\tposting_source_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tsuggested_minor_units BIGINT NOT NULL, \n\tdiscount_date VARCHAR(10), \n\tterms_percent_millionths BIGINT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_bill_payment_discount_amount CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_bill_payment_discount_suggested CHECK (typeof(suggested_minor_units) = 'integer' AND suggested_minor_units >= 0), \n\tCONSTRAINT ck_bill_payment_discount_percent CHECK (terms_percent_millionths IS NULL OR terms_percent_millionths BETWEEN 0 AND 100000000), \n\tCONSTRAINT uq_bill_payment_discount_application UNIQUE (application_id), \n\tCONSTRAINT fk_bill_payment_discount_component FOREIGN KEY(transaction_id, source_component_id) REFERENCES ap_source_components (transaction_id, id), \n\tCONSTRAINT fk_bill_payment_discount_attribution FOREIGN KEY(transaction_id, posting_source_id) REFERENCES posting_line_sources (transaction_id, id), \n\tCONSTRAINT fk_bill_payment_discount_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tFOREIGN KEY(application_id) REFERENCES ap_applications (id), \n\tFOREIGN KEY(bill_id) REFERENCES transactions (id), \n\tFOREIGN KEY(discount_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n")
INDEXES = ('CREATE INDEX ix_payment_discounts_invoice ON payment_discounts (invoice_id, id)', 'CREATE INDEX ix_payment_discounts_payment ON payment_discounts (transaction_id, id)', 'CREATE INDEX ix_bill_payment_discounts_bill ON bill_payment_discounts (bill_id, id)', 'CREATE INDEX ix_bill_payment_discounts_payment ON bill_payment_discounts (transaction_id, id)')
GUARDS = ("CREATE TRIGGER payment_discounts_immutable_update BEFORE UPDATE ON payment_discounts BEGIN SELECT RAISE(ABORT, 'immutable discount history'); END", "CREATE TRIGGER payment_discounts_immutable_delete BEFORE DELETE ON payment_discounts BEGIN SELECT RAISE(ABORT, 'immutable discount history'); END", "CREATE TRIGGER bill_payment_discounts_immutable_update BEFORE UPDATE ON bill_payment_discounts BEGIN SELECT RAISE(ABORT, 'immutable discount history'); END", "CREATE TRIGGER bill_payment_discounts_immutable_delete BEFORE DELETE ON bill_payment_discounts BEGIN SELECT RAISE(ABORT, 'immutable discount history'); END", "CREATE TRIGGER payment_discounts_edge BEFORE INSERT ON payment_discounts WHEN NOT EXISTS (SELECT 1 FROM applications a WHERE a.id = NEW.application_id AND a.kind = 'apply' AND a.paying_transaction_id = NEW.transaction_id AND a.paid_transaction_id = NEW.invoice_id AND a.source_component_key_id = NEW.component_key_id AND a.currency = NEW.currency AND a.amount_minor_units > NEW.amount_minor_units) BEGIN SELECT RAISE(ABORT, 'discount must be part of its own receipt settlement'); END", "CREATE TRIGGER bill_payment_discounts_edge BEFORE INSERT ON bill_payment_discounts WHEN NOT EXISTS (SELECT 1 FROM ap_applications a WHERE a.id = NEW.application_id AND a.kind = 'apply' AND a.source_transaction_id = NEW.transaction_id AND a.source_component_id = NEW.source_component_id AND a.obligation_transaction_id = NEW.bill_id AND a.currency = NEW.currency AND a.amount_minor_units > NEW.amount_minor_units) BEGIN SELECT RAISE(ABORT, 'discount must be part of its own bill settlement'); END")
COLUMNS = (
    "ALTER TABLE company_info ADD COLUMN customer_discount_account_id VARCHAR(26) REFERENCES accounts (id) ON DELETE RESTRICT",
    "ALTER TABLE company_info ADD COLUMN vendor_discount_account_id VARCHAR(26) REFERENCES accounts (id) ON DELETE RESTRICT",
    'CREATE INDEX ix_company_info_customer_discount_account_id ON company_info (customer_discount_account_id)',
    'CREATE INDEX ix_company_info_vendor_discount_account_id ON company_info (vendor_discount_account_id)',
)
# Names this revision brings into existence; a database that already has one is not ours.
RESERVED = ('payment_discounts', 'bill_payment_discounts', 'ix_company_info_customer_discount_account_id',
            'ix_company_info_vendor_discount_account_id')


def upgrade():
    c = op.get_bind()
    names = {row[0].casefold() for row in c.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & {name.casefold() for name in RESERVED}:
        raise RuntimeError('co0060 reserved object exists')
    columns = {row[1] for row in c.exec_driver_sql('PRAGMA table_xinfo(company_info)')}
    if columns & {'customer_discount_account_id', 'vendor_discount_account_id'}:
        raise RuntimeError('co0060 column already present')
    for statement in COLUMNS + DDL + INDEXES + GUARDS:
        c.exec_driver_sql(statement)
    if c.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0060 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
