"""Customer credits: a credit document type, the capacity it carries, and the edge it settles on.

Six new tables, and two existing ones widened. ``applications`` and ``application_allocations``
are the one receivable settlement edge, and they gain one nullable credit-source column each so
that a credit settles an invoice through exactly the rows a receipt does -- same ordinals, same
inverse rules, same allocation function. Nothing is backfilled: NULL is the true value of both
new columns for every row ever written, and the kind of source is derived from which column is
present rather than stored.

An invoice and a credit memo also share one number series from this revision on, so a partial
unique index refuses the same number on both -- ``uq_transaction_type_number`` is per type and
cannot say that.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0027'
down_revision = 'co0026'
branch_labels = None
depends_on = None

NEW_TABLES = ('credit_profiles', 'credit_line_profiles', 'credit_tax_components',
              'credit_source_keys', 'credit_components', 'credit_source_claims')
OBJECTS = ('applications_one_source', 'credit_components', 'credit_components_immutable_delete', 'credit_components_immutable_update', 'credit_components_owned_attribution', 'credit_line_profiles', 'credit_line_profiles_immutable_delete', 'credit_line_profiles_immutable_update', 'credit_profiles', 'credit_profiles_immutable_delete', 'credit_profiles_immutable_update', 'credit_source_claims', 'credit_source_claims_credit_transaction_id_type', 'credit_source_claims_exact_release', 'credit_source_claims_immutable_delete', 'credit_source_claims_immutable_update', 'credit_source_claims_source_transaction_id_type', 'credit_source_keys', 'credit_source_keys_immutable_delete', 'credit_source_keys_immutable_update', 'credit_source_keys_transaction_id_type', 'credit_tax_components', 'credit_tax_components_immutable_delete', 'credit_tax_components_immutable_update', 'ix_credit_components_key', 'ix_credit_profiles_customer', 'ix_credit_source_claims_line', 'ix_credit_source_keys_party', 'uq_transaction_receivable_number')
DDL = (
    "CREATE TABLE credit_profiles (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\tar_account_id VARCHAR(26) NOT NULL, \n\torigin VARCHAR(16) NOT NULL, \n\tsubtotal_minor_units BIGINT NOT NULL, \n\ttax_minor_units BIGINT NOT NULL, \n\tprofile_snapshot TEXT NOT NULL, \n\ttax_attribution_snapshot TEXT, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT uq_credit_profile_owner UNIQUE (transaction_id, revision_id), \n\tCONSTRAINT fk_credit_profile_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT fk_credit_profile_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tCONSTRAINT ck_credit_profile_type CHECK (type = 'credit_memo'), \n\tCONSTRAINT ck_credit_profile_origin CHECK (origin IN ('standalone', 'return')), \n\tCONSTRAINT ck_credit_subtotal_minor_units_nonnegative CHECK (typeof(subtotal_minor_units) = 'integer' AND subtotal_minor_units >= 0), \n\tCONSTRAINT ck_credit_tax_minor_units_nonnegative CHECK (typeof(tax_minor_units) = 'integer' AND tax_minor_units >= 0), \n\tCONSTRAINT ck_credit_profile_snapshot_object CHECK (json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'), \n\tCONSTRAINT ck_credit_attribution_object CHECK (tax_attribution_snapshot IS NULL OR (json_valid(tax_attribution_snapshot) AND json_type(tax_attribution_snapshot) = 'object')), \n\tCONSTRAINT ck_credit_attribution_origin CHECK ((origin = 'return') = (tax_attribution_snapshot IS NULL)), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(ar_account_id) REFERENCES accounts (id)\n)",
    "CREATE TABLE credit_line_profiles (\n\tdocument_line_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tunit_id VARCHAR(26), \n\tunit_factor_nanounits BIGINT NOT NULL, \n\tbase_quantity_microunits BIGINT NOT NULL, \n\tunit_price_minor_units BIGINT, \n\tpricing_basis VARCHAR(16) NOT NULL, \n\tnet_minor_units BIGINT NOT NULL, \n\ttax_minor_units BIGINT NOT NULL, \n\tgross_minor_units BIGINT NOT NULL, \n\titem_snapshot TEXT NOT NULL, \n\tposting_source_id VARCHAR(26), \n\tsource_transaction_id VARCHAR(26), \n\tsource_revision_id VARCHAR(26), \n\tsource_document_line_id VARCHAR(26), \n\tsource_line_id VARCHAR(26), \n\tPRIMARY KEY (document_line_id), \n\tCONSTRAINT uq_credit_line_profile_owner UNIQUE (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_credit_line_profile_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES credit_profiles (transaction_id, revision_id), \n\tCONSTRAINT fk_credit_line_profile_line FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT fk_credit_line_profile_attribution FOREIGN KEY(transaction_id, posting_source_id) REFERENCES posting_line_sources (transaction_id, id), \n\tCONSTRAINT fk_credit_line_profile_source FOREIGN KEY(source_transaction_id, source_revision_id, source_document_line_id) REFERENCES sales_line_profiles (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_credit_line_profile_source_identity FOREIGN KEY(source_transaction_id, source_line_id) REFERENCES document_line_identities (transaction_id, id), \n\tCONSTRAINT ck_credit_quantity_microunits_positive CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT ck_credit_unit_factor_nanounits_positive CHECK (typeof(unit_factor_nanounits) = 'integer' AND unit_factor_nanounits > 0), \n\tCONSTRAINT ck_credit_base_quantity_microunits_positive CHECK (typeof(base_quantity_microunits) = 'integer' AND base_quantity_microunits > 0), \n\tCONSTRAINT ck_credit_pricing_basis CHECK (pricing_basis IN ('unit', 'amount')), \n\tCONSTRAINT ck_credit_line_price CHECK ((pricing_basis = 'unit' AND typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0) OR (pricing_basis = 'amount' AND unit_price_minor_units IS NULL)), \n\tCONSTRAINT ck_credit_line_source_pair CHECK ((source_transaction_id IS NULL) = (source_revision_id IS NULL) AND (source_transaction_id IS NULL) = (source_document_line_id IS NULL) AND (source_transaction_id IS NULL) = (source_line_id IS NULL)), \n\tCONSTRAINT ck_credit_net_minor_units_nonnegative CHECK (typeof(net_minor_units) = 'integer' AND net_minor_units >= 0), \n\tCONSTRAINT ck_credit_tax_minor_units_nonnegative CHECK (typeof(tax_minor_units) = 'integer' AND tax_minor_units >= 0), \n\tCONSTRAINT ck_credit_gross_minor_units_nonnegative CHECK (typeof(gross_minor_units) = 'integer' AND gross_minor_units >= 0), \n\tCONSTRAINT ck_credit_line_attribution CHECK ((net_minor_units = 0) = (posting_source_id IS NULL)), \n\tCONSTRAINT ck_credit_item_snapshot_object CHECK (json_valid(item_snapshot) AND json_type(item_snapshot) = 'object'), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(unit_id) REFERENCES unit_conversions (id)\n)",
    "CREATE TABLE credit_tax_components (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tdocument_line_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\ttax_item_id VARCHAR(26) NOT NULL, \n\tagency_id VARCHAR(26) NOT NULL, \n\tliability_account_id VARCHAR(26) NOT NULL, \n\trate_percent_millionths BIGINT NOT NULL, \n\ttaxable_minor_units BIGINT NOT NULL, \n\ttax_minor_units BIGINT NOT NULL, \n\tcomponent_snapshot TEXT NOT NULL, \n\tposting_source_id VARCHAR(26), \n\tsource_tax_component_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_credit_tax_component_owner UNIQUE (transaction_id, revision_id, document_line_id, id), \n\tCONSTRAINT fk_credit_tax_component_line FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES credit_line_profiles (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_credit_tax_component_attribution FOREIGN KEY(transaction_id, posting_source_id) REFERENCES posting_line_sources (transaction_id, id), \n\tCONSTRAINT ck_credit_rate_percent_millionths_nonnegative CHECK (typeof(rate_percent_millionths) = 'integer' AND rate_percent_millionths >= 0), \n\tCONSTRAINT ck_credit_taxable_minor_units_nonnegative CHECK (typeof(taxable_minor_units) = 'integer' AND taxable_minor_units >= 0), \n\tCONSTRAINT ck_credit_tax_minor_units_nonnegative CHECK (typeof(tax_minor_units) = 'integer' AND tax_minor_units >= 0), \n\tCONSTRAINT ck_credit_tax_attribution CHECK ((tax_minor_units = 0) = (posting_source_id IS NULL)), \n\tCONSTRAINT ck_credit_component_snapshot_object CHECK (json_valid(component_snapshot) AND json_type(component_snapshot) = 'object'), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(tax_item_id) REFERENCES items (id), \n\tFOREIGN KEY(agency_id) REFERENCES vendors (id), \n\tFOREIGN KEY(liability_account_id) REFERENCES accounts (id)\n)",
    'CREATE TABLE credit_source_keys (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tparty_id VARCHAR(26) NOT NULL, \n\tar_account_id VARCHAR(26) NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_credit_source_dimensions UNIQUE (transaction_id, party_id, ar_account_id, currency), \n\tCONSTRAINT uq_credit_source_owner UNIQUE (transaction_id, id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(party_id) REFERENCES customers (id), \n\tFOREIGN KEY(ar_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)',
    "CREATE TABLE credit_components (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tkey_id VARCHAR(26) NOT NULL, \n\tdocument_line_id VARCHAR(26) NOT NULL, \n\tposting_source_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_credit_component_occurrence UNIQUE (revision_id, document_line_id), \n\tCONSTRAINT uq_credit_component_revision UNIQUE (transaction_id, revision_id, id), \n\tCONSTRAINT uq_credit_component_owner UNIQUE (transaction_id, id), \n\tCONSTRAINT fk_credit_component_key FOREIGN KEY(transaction_id, key_id) REFERENCES credit_source_keys (transaction_id, id), \n\tCONSTRAINT fk_credit_component_envelope FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT fk_credit_component_attribution FOREIGN KEY(transaction_id, posting_source_id) REFERENCES posting_line_sources (transaction_id, id), \n\tCONSTRAINT ck_credit_amount_minor_units_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    "CREATE TABLE credit_source_claims (\n\tid VARCHAR(26) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\treverses_claim_id VARCHAR(26), \n\tcredit_transaction_id VARCHAR(26) NOT NULL, \n\tcredit_revision_id VARCHAR(26) NOT NULL, \n\tcredit_document_line_id VARCHAR(26) NOT NULL, \n\tsource_transaction_id VARCHAR(26) NOT NULL, \n\tsource_revision_id VARCHAR(26) NOT NULL, \n\tsource_document_line_id VARCHAR(26) NOT NULL, \n\tsource_line_id VARCHAR(26) NOT NULL, \n\tstart_microunits BIGINT NOT NULL, \n\tend_microunits BIGINT NOT NULL, \n\tsource_base_quantity_microunits BIGINT NOT NULL, \n\tsource_net_minor_units BIGINT NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_credit_claim_release UNIQUE (reverses_claim_id), \n\tCONSTRAINT fk_credit_claim_line FOREIGN KEY(credit_transaction_id, credit_revision_id, credit_document_line_id) REFERENCES credit_line_profiles (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_credit_claim_source FOREIGN KEY(source_transaction_id, source_revision_id, source_document_line_id) REFERENCES sales_line_profiles (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_credit_claim_source_identity FOREIGN KEY(source_transaction_id, source_line_id) REFERENCES document_line_identities (transaction_id, id), \n\tCONSTRAINT ck_credit_claim_kind CHECK ((kind = 'claim' AND reverses_claim_id IS NULL) OR (kind = 'release' AND reverses_claim_id IS NOT NULL AND reverses_claim_id <> id)), \n\tCONSTRAINT ck_credit_claim_interval CHECK (typeof(start_microunits) = 'integer' AND start_microunits >= 0 AND typeof(end_microunits) = 'integer' AND end_microunits > start_microunits AND end_microunits <= source_base_quantity_microunits), \n\tCONSTRAINT ck_credit_source_base_quantity_microunits_positive CHECK (typeof(source_base_quantity_microunits) = 'integer' AND source_base_quantity_microunits > 0), \n\tCONSTRAINT ck_credit_source_net_minor_units_nonnegative CHECK (typeof(source_net_minor_units) = 'integer' AND source_net_minor_units >= 0), \n\tFOREIGN KEY(reverses_claim_id) REFERENCES credit_source_claims (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    'CREATE INDEX ix_credit_components_key ON credit_components (key_id, revision_id)',
    'CREATE INDEX ix_credit_profiles_customer ON credit_profiles (customer_id, ar_account_id, transaction_id)',
    'CREATE INDEX ix_credit_source_claims_line ON credit_source_claims (source_transaction_id, source_line_id, id)',
    'CREATE INDEX ix_credit_source_keys_party ON credit_source_keys (party_id, ar_account_id, id)',
    "CREATE UNIQUE INDEX uq_transaction_receivable_number ON transactions (number) WHERE type IN ('invoice', 'credit_memo')",
)
GUARDS = (
    "CREATE TRIGGER credit_profiles_immutable_update BEFORE UPDATE ON credit_profiles BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_profiles_immutable_delete BEFORE DELETE ON credit_profiles BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_line_profiles_immutable_update BEFORE UPDATE ON credit_line_profiles BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_line_profiles_immutable_delete BEFORE DELETE ON credit_line_profiles BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_tax_components_immutable_update BEFORE UPDATE ON credit_tax_components BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_tax_components_immutable_delete BEFORE DELETE ON credit_tax_components BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_source_keys_immutable_update BEFORE UPDATE ON credit_source_keys BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_source_keys_immutable_delete BEFORE DELETE ON credit_source_keys BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_components_immutable_update BEFORE UPDATE ON credit_components BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_components_immutable_delete BEFORE DELETE ON credit_components BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_source_claims_immutable_update BEFORE UPDATE ON credit_source_claims BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_source_claims_immutable_delete BEFORE DELETE ON credit_source_claims BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END",
    "CREATE TRIGGER credit_source_keys_transaction_id_type BEFORE INSERT ON credit_source_keys\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND type = 'credit_memo')\nBEGIN SELECT RAISE(ABORT, 'credit reference has wrong document type'); END",
    "CREATE TRIGGER credit_source_claims_credit_transaction_id_type BEFORE INSERT ON credit_source_claims\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.credit_transaction_id AND type = 'credit_memo')\nBEGIN SELECT RAISE(ABORT, 'credit reference has wrong document type'); END",
    "CREATE TRIGGER credit_source_claims_source_transaction_id_type BEFORE INSERT ON credit_source_claims\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.source_transaction_id AND type = 'invoice')\nBEGIN SELECT RAISE(ABORT, 'credit reference has wrong document type'); END",
    "CREATE TRIGGER credit_source_claims_exact_release BEFORE INSERT ON credit_source_claims\nWHEN NEW.kind = 'release' AND NOT EXISTS (SELECT 1 FROM credit_source_claims a\nWHERE a.id = NEW.reverses_claim_id AND a.kind = 'claim' AND a.credit_transaction_id IS NEW.credit_transaction_id AND a.credit_revision_id IS NEW.credit_revision_id AND a.credit_document_line_id IS NEW.credit_document_line_id AND a.source_transaction_id IS NEW.source_transaction_id AND a.source_revision_id IS NEW.source_revision_id AND a.source_document_line_id IS NEW.source_document_line_id AND a.source_line_id IS NEW.source_line_id AND a.start_microunits IS NEW.start_microunits AND a.end_microunits IS NEW.end_microunits AND a.source_base_quantity_microunits IS NEW.source_base_quantity_microunits AND a.source_net_minor_units IS NEW.source_net_minor_units AND a.effective_date IS NEW.effective_date)\nBEGIN SELECT RAISE(ABORT, 'release must exactly undo an original claim'); END",
    "CREATE TRIGGER credit_components_owned_attribution BEFORE INSERT ON credit_components\nWHEN NOT EXISTS (SELECT 1 FROM credit_source_keys k\nJOIN posting_line_sources ps ON ps.transaction_id = NEW.transaction_id AND ps.id = NEW.posting_source_id\nJOIN posting_lines pl ON pl.id = ps.posting_line_id\nWHERE k.id = NEW.key_id AND k.transaction_id = NEW.transaction_id\nAND k.currency = NEW.currency AND ps.revision_id = NEW.revision_id\nAND ps.document_line_id = NEW.document_line_id AND ps.reversed_source_id IS NULL\nAND ps.amount_minor_units = NEW.amount_minor_units\nAND pl.account_id = k.ar_account_id AND pl.credit_minor_units > 0\nAND pl.name_type = 'customer' AND pl.name_id = k.party_id)\nBEGIN SELECT RAISE(ABORT, 'credit capacity is not an owned receivable credit'); END",
    "CREATE TRIGGER applications_paying_transaction_id_type BEFORE INSERT ON applications\nWHEN NEW.paying_transaction_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.paying_transaction_id AND type IN ('payment', 'credit_memo'))\nBEGIN SELECT RAISE(ABORT, 'payment reference has wrong document type'); END",
    "CREATE TRIGGER applications_one_source BEFORE INSERT ON applications\nWHEN (NEW.source_component_key_id IS NOT NULL) + (NEW.credit_source_key_id IS NOT NULL) <> 1\nOR (NEW.source_component_key_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM payment_component_keys k\nWHERE k.id = NEW.source_component_key_id AND k.transaction_id = NEW.paying_transaction_id))\nOR (NEW.credit_source_key_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM credit_source_keys k\nWHERE k.id = NEW.credit_source_key_id AND k.transaction_id = NEW.paying_transaction_id))\nBEGIN SELECT RAISE(ABORT, 'an application has exactly one source, owned by the paying document'); END",
    "CREATE TRIGGER applications_exact_party BEFORE INSERT ON applications\nWHEN NOT EXISTS (SELECT 1 FROM transactions t\nJOIN sales_profiles p ON p.revision_id = t.current_revision_id\nJOIN transaction_revisions r ON r.id = p.revision_id\nWHERE t.id = NEW.paid_transaction_id AND r.currency = NEW.currency\nAND (EXISTS (SELECT 1 FROM payment_component_keys k WHERE k.id = NEW.source_component_key_id\nAND k.party_id = p.customer_id AND k.ar_account_id = p.control_account_id AND k.currency = NEW.currency)\nOR EXISTS (SELECT 1 FROM credit_source_keys k WHERE k.id = NEW.credit_source_key_id\nAND k.party_id = p.customer_id AND k.ar_account_id = p.control_account_id AND k.currency = NEW.currency)))\nBEGIN SELECT RAISE(ABORT, 'application source and target ownership differ'); END",
    "CREATE TRIGGER applications_exact_inverse BEFORE INSERT ON applications\nWHEN NEW.kind = 'unapply' AND NOT EXISTS (SELECT 1 FROM applications a\nWHERE a.id = NEW.reverses_application_id AND a.kind = 'apply' AND a.paying_transaction_id IS NEW.paying_transaction_id AND a.paid_transaction_id IS NEW.paid_transaction_id AND a.source_component_key_id IS NEW.source_component_key_id AND a.credit_source_key_id IS NEW.credit_source_key_id AND a.amount_minor_units IS NEW.amount_minor_units AND a.currency IS NEW.currency AND a.effective_date IS NEW.effective_date)\nBEGIN SELECT RAISE(ABORT, 'unapply must exactly reverse original application'); END",
    "CREATE TRIGGER application_allocations_exact_inverse BEFORE INSERT ON application_allocations\nWHEN NEW.kind = 'reversal' AND NOT EXISTS (SELECT 1 FROM application_allocations a\nWHERE a.id = NEW.reverses_allocation_id AND a.kind = 'allocation' AND a.application_id IS NEW.application_id AND a.source_transaction_id IS NEW.source_transaction_id AND a.source_revision_id IS NEW.source_revision_id AND a.source_component_id IS NEW.source_component_id AND a.credit_source_component_id IS NEW.credit_source_component_id AND a.source_posting_source_id IS NEW.source_posting_source_id AND a.target_transaction_id IS NEW.target_transaction_id AND a.target_revision_id IS NEW.target_revision_id AND a.target_document_line_id IS NEW.target_document_line_id AND a.target_line_id IS NEW.target_line_id AND a.target_ordinal IS NEW.target_ordinal AND a.logical_kind IS NEW.logical_kind AND a.tax_item_id IS NEW.tax_item_id AND a.tax_component_id IS NEW.tax_component_id AND a.target_ar_source_id IS NEW.target_ar_source_id AND a.target_recognition_source_id IS NEW.target_recognition_source_id AND a.recognition_role IS NEW.recognition_role AND a.amount_minor_units IS NEW.amount_minor_units AND a.currency IS NEW.currency AND a.effective_date IS NEW.effective_date AND a.facts_snapshot IS NEW.facts_snapshot)\nBEGIN SELECT RAISE(ABORT, 'allocation inverse must preserve original attribution'); END",
    "CREATE TRIGGER application_allocations_owned_sources BEFORE INSERT ON application_allocations\nWHEN NOT EXISTS (\nSELECT 1 FROM applications a\nJOIN payment_components c ON c.id = NEW.source_component_id\nJOIN posting_line_sources ps ON ps.id = NEW.source_posting_source_id\nJOIN posting_lines pl ON pl.id = ps.posting_line_id\nJOIN payment_component_keys k ON k.id = c.component_key_id\nJOIN posting_line_sources ar ON ar.id = NEW.target_ar_source_id\nJOIN posting_lines arl ON arl.id = ar.posting_line_id\nJOIN posting_line_sources rec ON rec.id = NEW.target_recognition_source_id\nJOIN posting_lines recl ON recl.id = rec.posting_line_id\nJOIN document_lines d ON d.id = NEW.target_document_line_id\nWHERE a.id = NEW.application_id AND a.kind = 'apply'\nAND a.paying_transaction_id = NEW.source_transaction_id AND a.paid_transaction_id = NEW.target_transaction_id\nAND a.source_component_key_id = c.component_key_id AND a.currency = NEW.currency AND a.effective_date = NEW.effective_date\nAND ps.payment_component_id = c.id AND ps.revision_id = NEW.source_revision_id\nAND ps.reversed_source_id IS NULL AND pl.account_id = k.ar_account_id AND pl.credit_minor_units > 0\nAND pl.name_type = 'customer' AND pl.name_id = k.party_id\nAND ar.transaction_id = NEW.target_transaction_id AND rec.transaction_id = NEW.target_transaction_id\nAND ar.revision_id = NEW.target_revision_id AND rec.revision_id = NEW.target_revision_id\nAND ar.document_line_id = d.id AND rec.document_line_id = d.id AND d.line_id = NEW.target_line_id\nAND ar.tax_component_id IS NEW.tax_component_id AND rec.tax_component_id IS NEW.tax_component_id\nAND ar.reversed_source_id IS NULL AND rec.reversed_source_id IS NULL\nAND arl.account_id = k.ar_account_id AND arl.debit_minor_units > 0 AND recl.credit_minor_units > 0\nAND arl.name_type = 'customer' AND arl.name_id = k.party_id\nAND (NEW.logical_kind = 'net' OR EXISTS (SELECT 1 FROM sales_tax_components tc\nWHERE tc.id = NEW.tax_component_id AND tc.tax_item_id = NEW.tax_item_id AND tc.liability_account_id = recl.account_id)))\nAND NOT EXISTS (\nSELECT 1 FROM applications a\nJOIN credit_components c ON c.id = NEW.credit_source_component_id\nJOIN posting_line_sources ps ON ps.id = NEW.source_posting_source_id\nJOIN posting_lines pl ON pl.id = ps.posting_line_id\nJOIN credit_source_keys k ON k.id = c.key_id\nJOIN posting_line_sources ar ON ar.id = NEW.target_ar_source_id\nJOIN posting_lines arl ON arl.id = ar.posting_line_id\nJOIN posting_line_sources rec ON rec.id = NEW.target_recognition_source_id\nJOIN posting_lines recl ON recl.id = rec.posting_line_id\nJOIN document_lines d ON d.id = NEW.target_document_line_id\nWHERE a.id = NEW.application_id AND a.kind = 'apply'\nAND a.paying_transaction_id = NEW.source_transaction_id AND a.paid_transaction_id = NEW.target_transaction_id\nAND a.credit_source_key_id = c.key_id AND a.currency = NEW.currency AND a.effective_date = NEW.effective_date\nAND ps.id = c.posting_source_id AND ps.transaction_id = c.transaction_id AND ps.revision_id = NEW.source_revision_id\nAND ps.reversed_source_id IS NULL AND pl.account_id = k.ar_account_id AND pl.credit_minor_units > 0\nAND pl.name_type = 'customer' AND pl.name_id = k.party_id\nAND ar.transaction_id = NEW.target_transaction_id AND rec.transaction_id = NEW.target_transaction_id\nAND ar.revision_id = NEW.target_revision_id AND rec.revision_id = NEW.target_revision_id\nAND ar.document_line_id = d.id AND rec.document_line_id = d.id AND d.line_id = NEW.target_line_id\nAND ar.tax_component_id IS NEW.tax_component_id AND rec.tax_component_id IS NEW.tax_component_id\nAND ar.reversed_source_id IS NULL AND rec.reversed_source_id IS NULL\nAND arl.account_id = k.ar_account_id AND arl.debit_minor_units > 0 AND recl.credit_minor_units > 0\nAND arl.name_type = 'customer' AND arl.name_id = k.party_id\nAND (NEW.logical_kind = 'net' OR EXISTS (SELECT 1 FROM sales_tax_components tc\nWHERE tc.id = NEW.tax_component_id AND tc.tax_item_id = NEW.tax_item_id AND tc.liability_account_id = recl.account_id)))\nBEGIN SELECT RAISE(ABORT, 'allocation references unrelated application or accounting source'); END",
    "CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines\nWHEN EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND\n((type = 'journal_entry' AND NEW.kind <> 'journal') OR\n(type IN ('invoice', 'sales_receipt') AND NEW.kind <> 'sale') OR\n(type = 'payment' AND NEW.kind <> 'payment') OR\n(type = 'deposit' AND NEW.kind <> 'deposit') OR\n(type = 'bill' AND NEW.kind <> 'purchase') OR\n(type = 'bill_payment' AND NEW.kind <> 'bill_payment') OR\n(type = 'credit_memo' AND NEW.kind <> 'credit')))\nBEGIN SELECT RAISE(ABORT, 'document line kind does not match transaction type'); END",
)
CHANGED = ('transactions', 'document_lines', 'applications', 'application_allocations')
# The triggers this revision deliberately rewrites, each verified against the exact text the
# revision that wrote it froze. Anything else stored under these names is a local guard whose
# meaning this migration cannot know, and it stops rather than guessing.
REPLACED = ('applications_paying_transaction_id_type', 'applications_exact_party',
            'applications_exact_inverse', 'application_allocations_exact_inverse',
            'application_allocations_owned_sources', 'document_lines_type_insert')
# The exact co0026 text this migration expects, and what it becomes. A credit memo is an eighth
# document type and its credited lines are a seventh envelope kind; both live in a CHECK, and
# SQLite only widens a CHECK by rebuilding the table. The settlement pair is widened the same
# way: a nullable column and one added foreign key cannot be an ALTER either.
REPLACEMENTS = {
    'transactions': ((
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment'))",
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo'))"),),
    'document_lines': ((
        "kind IN ('sale', 'payment', 'deposit', 'purchase', 'bill_payment')",
        "kind IN ('sale', 'payment', 'deposit', 'purchase', 'bill_payment', 'credit')"),),
    'applications': ((
        'source_component_key_id VARCHAR(26) NOT NULL',
        'source_component_key_id VARCHAR(26)'),),
    'application_allocations': ((
        'source_component_id VARCHAR(26) NOT NULL',
        'source_component_id VARCHAR(26)'),),
}
ADDITIONS = {
    'applications': ('\n\tcredit_source_key_id VARCHAR(26)',),
    'application_allocations': ('\n\tcredit_source_component_id VARCHAR(26)',),
}
CONSTRAINTS = {
    'applications': ('\n\tCONSTRAINT fk_payment_application_credit_source FOREIGN KEY(paying_transaction_id, credit_source_key_id) REFERENCES credit_source_keys (transaction_id, id)',),
    'application_allocations': ('\n\tCONSTRAINT fk_payment_allocation_credit_source FOREIGN KEY(source_transaction_id, source_revision_id, credit_source_component_id) REFERENCES credit_components (transaction_id, revision_id, id)',),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0027 cannot preserve custom row identity')
    for name in (column.strip().split()[0] for addition in ADDITIONS.get(table, ()) for column in (addition,)):
        if any(row[1] == name for row in columns):
            raise RuntimeError('co0027 credit column already exists: ' + name)
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0027 unknown constraint or column: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    # New columns go after the existing columns and before the table constraints, so every
    # pre-existing column keeps its ordinal position and a local `SELECT *` keeps its prefix.
    boundary = next((i for i, part in enumerate(parts) if re.match(
        r'^(?:CONSTRAINT\s|PRIMARY\s+KEY|UNIQUE\s*\(|CHECK\s*\(|FOREIGN\s+KEY)', part.strip(), re.I)), len(parts))
    parts = parts[:boundary] + list(ADDITIONS.get(table, ())) + parts[boundary:] + list(CONSTRAINTS.get(table, ()))
    create = 'CREATE TABLE ' + quote('_co0027_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


# Every trigger any shipped revision has ever put on the settlement pair, with the exact text
# that revision froze. The rebuild drops all of them and puts them back, so anything stored on
# those two tables that is not in here is a local guard whose meaning this migration cannot
# know: it stops rather than dropping it silently or restoring it over a widened table.
SETTLEMENT_TABLES = ('applications', 'application_allocations')


def _expected_guards():
    payments = importlib.import_module('bookflow.storage.company_migrations.versions.0014_customer_payments')
    bill_payments = importlib.import_module('bookflow.storage.company_migrations.versions.0026_bill_payments')
    known = {}
    for module in (payments, bill_payments):
        for statement in module.GUARDS:
            known[statement.split()[2]] = statement
    return {name: sql for name, sql in known.items()
            if name in REPLACED or re.search(r'(?i)\bON\s+(?:%s)\b' % '|'.join(SETTLEMENT_TABLES), sql)}


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = set(OBJECTS) | {'_co0027_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0027 reserved object already exists')
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        attached = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + attached + '.sqlite_schema'):
            if row[0].casefold() in {value.casefold() for value in OBJECTS}:
                raise RuntimeError('co0027 credit storage name collision')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join(f"'{name}'" for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        f'OR (type = \'index\' AND tbl_name IN ({changed}))) '
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    expected = _expected_guards()
    if set(REPLACED) - set(expected) or any(stored.get(name) != sql for name, sql in expected.items()):
        raise RuntimeError('co0027 unknown settlement or document type guard')
    # Keep unknown local objects verbatim; do not guess through competing guards.
    for name, sql in stored.items():
        if name in expected:
            continue
        if re.search(r'(?i)\b(?:NEW|OLD)\s*\.\s*["`\[]?type\b', sql) and re.search(r'(?i)\bON\s+["`\[]?transactions\b', sql):
            raise RuntimeError('co0027 unknown competing document type guard')
        if re.search(r'(?i)\bON\s+["`\[]?(?:%s)\b' % '|'.join(SETTLEMENT_TABLES), sql):
            raise RuntimeError('co0027 unknown competing settlement guard')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0027_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0027 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for statement in DDL:
        connection.exec_driver_sql(statement)
    for _, name, statement in retained:
        if name not in REPLACED:
            connection.exec_driver_sql(statement)
    for statement in GUARDS:
        connection.exec_driver_sql(statement)
    for name in NEW_TABLES:
        if connection.exec_driver_sql('SELECT 1 FROM main."' + name + '" LIMIT 1').fetchone():
            raise RuntimeError('co0027 credit storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0027 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
