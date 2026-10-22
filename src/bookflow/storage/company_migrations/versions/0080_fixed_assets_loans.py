"""Fixed-asset depreciation and loans (R119).

Five new tables: ``fixed_asset_setups`` (a book depreciation setup per fixed-asset item),
``fixed_asset_depreciations`` (each period posted, linked to its journal entry),
``fixed_asset_disposals`` (each disposal entry), ``loans`` (amortization terms) and
``loan_payments`` (how each loan check split). The three link tables are append-only. Additive:
nothing existing changes. DDL below is frozen: this migration never imports current application
metadata.
"""
from alembic import op

revision = 'co0080'
down_revision = 'co0075'
branch_labels = depends_on = None
DDL = (
    "\nCREATE TABLE fixed_asset_setups (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tmethod VARCHAR(24) NOT NULL, \n\tdeclining_percent INTEGER, \n\tsalvage_minor_units BIGINT NOT NULL, \n\tin_service_date VARCHAR(10) NOT NULL, \n\tlife_months INTEGER NOT NULL, \n\tperiod VARCHAR(8) NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_fixed_asset_setup_item UNIQUE (item_id), \n\tCONSTRAINT ck_fixed_asset_setup_method CHECK (method IN ('straight_line','declining_balance')), \n\tCONSTRAINT ck_fixed_asset_setup_declining CHECK ((method = 'straight_line' AND declining_percent IS NULL) OR (method = 'declining_balance' AND declining_percent IN (150, 200))), \n\tCONSTRAINT ck_fixed_asset_setup_salvage CHECK (typeof(salvage_minor_units) = 'integer' AND salvage_minor_units >= 0), \n\tCONSTRAINT ck_fixed_asset_setup_life CHECK (life_months BETWEEN 1 AND 1200), \n\tCONSTRAINT ck_fixed_asset_setup_period CHECK (period IN ('month','year')), \n\tCONSTRAINT ck_fixed_asset_setup_date CHECK (length(in_service_date) = 10), \n\tFOREIGN KEY(item_id) REFERENCES items (id)\n)\n\n",
    "\nCREATE TABLE fixed_asset_depreciations (\n\tid VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tperiod_number INTEGER NOT NULL, \n\tperiod_start VARCHAR(10) NOT NULL, \n\tperiod_end VARCHAR(10) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_fixed_asset_depreciation_amount CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units >= 0), \n\tCONSTRAINT ck_fixed_asset_depreciation_period CHECK (period_number >= 1), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n",
    "\nCREATE TABLE fixed_asset_disposals (\n\tid VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tdisposal_date VARCHAR(10) NOT NULL, \n\tproceeds_minor_units BIGINT NOT NULL, \n\tcosts_minor_units BIGINT NOT NULL, \n\tcost_minor_units BIGINT NOT NULL, \n\taccumulated_minor_units BIGINT NOT NULL, \n\tgain_minor_units BIGINT NOT NULL, \n\tdeposit_account_id VARCHAR(26), \n\tcurrency VARCHAR(3) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_fixed_asset_disposal_proceeds CHECK (typeof(proceeds_minor_units) = 'integer' AND proceeds_minor_units >= 0), \n\tCONSTRAINT ck_fixed_asset_disposal_costs CHECK (typeof(costs_minor_units) = 'integer' AND costs_minor_units >= 0), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(deposit_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n",
    "\nCREATE TABLE loans (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tlender_id VARCHAR(26), \n\tliability_account_id VARCHAR(26) NOT NULL, \n\tinterest_account_id VARCHAR(26) NOT NULL, \n\tescrow_account_id VARCHAR(26), \n\tpayment_account_id VARCHAR(26), \n\tprincipal_minor_units BIGINT NOT NULL, \n\tannual_rate VARCHAR(16) NOT NULL, \n\tterm_payments INTEGER NOT NULL, \n\tfrequency VARCHAR(12) NOT NULL, \n\torigination_date VARCHAR(10), \n\tfirst_payment_date VARCHAR(10) NOT NULL, \n\tpayment_minor_units BIGINT NOT NULL, \n\tballoon_minor_units BIGINT NOT NULL, \n\tescrow_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_loan_name_key UNIQUE (name_key), \n\tCONSTRAINT ck_loan_name_nonblank CHECK (length(trim(name)) > 0), \n\tCONSTRAINT ck_loan_principal CHECK (typeof(principal_minor_units) = 'integer' AND principal_minor_units > 0), \n\tCONSTRAINT ck_loan_payment CHECK (typeof(payment_minor_units) = 'integer' AND payment_minor_units > 0), \n\tCONSTRAINT ck_loan_balloon CHECK (typeof(balloon_minor_units) = 'integer' AND balloon_minor_units >= 0), \n\tCONSTRAINT ck_loan_escrow CHECK (typeof(escrow_minor_units) = 'integer' AND escrow_minor_units >= 0), \n\tCONSTRAINT ck_loan_escrow_account CHECK (escrow_minor_units = 0 OR escrow_account_id IS NOT NULL), \n\tCONSTRAINT ck_loan_term CHECK (term_payments BETWEEN 1 AND 3000), \n\tCONSTRAINT ck_loan_frequency CHECK (frequency IN ('weekly','biweekly','monthly','quarterly','semiannual','annual')), \n\tFOREIGN KEY(lender_id) REFERENCES vendors (id), \n\tFOREIGN KEY(liability_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(interest_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(escrow_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(payment_account_id) REFERENCES accounts (id)\n)\n\n",
    "\nCREATE TABLE loan_payments (\n\tid VARCHAR(26) NOT NULL, \n\tloan_id VARCHAR(26) NOT NULL, \n\tpayment_number INTEGER, \n\tpayment_date VARCHAR(10) NOT NULL, \n\tprincipal_minor_units BIGINT NOT NULL, \n\tinterest_minor_units BIGINT NOT NULL, \n\tescrow_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_loan_payment_principal CHECK (typeof(principal_minor_units) = 'integer' AND principal_minor_units >= 0), \n\tCONSTRAINT ck_loan_payment_interest CHECK (typeof(interest_minor_units) = 'integer' AND interest_minor_units >= 0), \n\tCONSTRAINT ck_loan_payment_escrow CHECK (typeof(escrow_minor_units) = 'integer' AND escrow_minor_units >= 0), \n\tFOREIGN KEY(loan_id) REFERENCES loans (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n",
)
INDEXES = (
    'CREATE INDEX ix_fixed_asset_depreciations_item ON fixed_asset_depreciations (item_id, period_number)',
    'CREATE INDEX ix_fixed_asset_depreciations_transaction ON fixed_asset_depreciations (transaction_id)',
    'CREATE INDEX ix_fixed_asset_disposals_item ON fixed_asset_disposals (item_id)',
    'CREATE INDEX ix_loan_payments_loan ON loan_payments (loan_id, payment_date)',
)
GUARDS = (
    "CREATE TRIGGER fixed_asset_depreciations_immutable_update BEFORE UPDATE ON fixed_asset_depreciations BEGIN SELECT RAISE(ABORT, 'immutable fixed_asset_depreciations'); END",
    "CREATE TRIGGER fixed_asset_depreciations_immutable_delete BEFORE DELETE ON fixed_asset_depreciations BEGIN SELECT RAISE(ABORT, 'immutable fixed_asset_depreciations'); END",
    "CREATE TRIGGER fixed_asset_disposals_immutable_update BEFORE UPDATE ON fixed_asset_disposals BEGIN SELECT RAISE(ABORT, 'immutable fixed_asset_disposals'); END",
    "CREATE TRIGGER fixed_asset_disposals_immutable_delete BEFORE DELETE ON fixed_asset_disposals BEGIN SELECT RAISE(ABORT, 'immutable fixed_asset_disposals'); END",
    "CREATE TRIGGER loan_payments_immutable_update BEFORE UPDATE ON loan_payments BEGIN SELECT RAISE(ABORT, 'immutable loan_payments'); END",
    "CREATE TRIGGER loan_payments_immutable_delete BEFORE DELETE ON loan_payments BEGIN SELECT RAISE(ABORT, 'immutable loan_payments'); END",
)
RESERVED = {'fixed_asset_setups', 'fixed_asset_depreciations', 'fixed_asset_disposals', 'loans', 'loan_payments'}


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & RESERVED:
        raise RuntimeError('co0080 reserved object exists')
    for statement in DDL + INDEXES + GUARDS:
        db.exec_driver_sql(statement)
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0080 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
