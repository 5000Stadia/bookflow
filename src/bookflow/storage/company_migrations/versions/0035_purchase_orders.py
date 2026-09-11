"""Purchase orders: a non-posting vendor document with its own lines and one conversion record.

Five new tables and nothing else. A purchase order is not a ``transactions`` row -- it posts
nothing, so it has no batch, no posting line and no payable -- which is why no CHECK is widened
here, no table is rebuilt and no existing trigger is rewritten. That is the whole difference
between this revision and the ones around it.

``purchase_order_conversions`` is the only table that reaches into what was already there: it
names a bill and the bill revision an order became, so an order becomes at most one bill and a
bill comes from at most one order. Both sides are UNIQUE, which is what makes "already billed"
a fact in storage rather than a status somebody could edit around.

Nothing is backfilled and no company owes anything different after upgrading: the five tables
arrive empty, every bill that stood before stands unchanged, and no bill gains a source it
did not have.

DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0035'
# co0033 and co0034 are allocated to purchase item lines and statement charges on their own
# branches. This revision chains from what actually exists here; the integrator re-points
# `down_revision` at whatever is genuinely last when the branches meet.
down_revision = 'co0032'
branch_labels = None
depends_on = None

NEW_TABLES = ('purchase_orders', 'purchase_order_revisions', 'purchase_order_line_identities', 'purchase_order_lines', 'purchase_order_conversions')
OBJECTS = ('ix_purchase_order_revision_query', 'ix_purchase_order_revision_vendor', 'ix_purchase_orders_status_number', 'purchase_order_conversions', 'purchase_order_conversions_destination_type', 'purchase_order_conversions_immutable_delete', 'purchase_order_conversions_immutable_update', 'purchase_order_line_identities', 'purchase_order_line_identities_immutable_delete', 'purchase_order_line_identities_immutable_update', 'purchase_order_lines', 'purchase_order_lines_immutable_delete', 'purchase_order_lines_immutable_update', 'purchase_order_revisions', 'purchase_order_revisions_immutable_delete', 'purchase_order_revisions_immutable_update', 'purchase_orders', 'purchase_orders_no_delete')
DDL = (
    "CREATE TABLE purchase_orders (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tnumber VARCHAR(64) NOT NULL, \n\tcurrent_revision_id VARCHAR(26) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tvoided_at VARCHAR(32), \n\tvoided_by VARCHAR(26), \n\tvoid_reason VARCHAR(140), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_purchase_order_number UNIQUE (number), \n\tCONSTRAINT ck_purchase_order_status CHECK (status IN ('open', 'partly_received', 'closed', 'voided')), \n\tCONSTRAINT ck_purchase_order_number CHECK (length(trim(number)) BETWEEN 1 AND 64), \n\tCONSTRAINT ck_purchase_order_version CHECK (typeof(version) = 'integer' AND version > 0), \n\tCONSTRAINT ck_purchase_order_void CHECK ((status = 'voided' AND voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(trim(void_reason)) > 0) OR (status <> 'voided' AND voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)), \n\tCONSTRAINT fk_purchase_order_current_revision FOREIGN KEY(id, current_revision_id) REFERENCES purchase_order_revisions (document_id, id) DEFERRABLE INITIALLY DEFERRED\n)",
    "CREATE TABLE purchase_order_revisions (\n\tid VARCHAR(26) NOT NULL, \n\tdocument_id VARCHAR(26) NOT NULL, \n\trevision_number BIGINT NOT NULL, \n\tsupersedes_revision_id VARCHAR(26), \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tdate VARCHAR(10) NOT NULL, \n\tnumber VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tvendor_id VARCHAR(26) NOT NULL, \n\texpected_date VARCHAR(10), \n\tship_to VARCHAR(2000), \n\tterms_id VARCHAR(26), \n\treference VARCHAR(128), \n\tmemo VARCHAR(2000), \n\tclass_id VARCHAR(26), \n\tcurrency VARCHAR(3) NOT NULL, \n\ttotal_minor_units BIGINT NOT NULL, \n\tprofile_snapshot TEXT NOT NULL, \n\tcustom_fields_snapshot TEXT NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_purchase_order_revision_owner UNIQUE (document_id, id), \n\tCONSTRAINT uq_purchase_order_revision_number UNIQUE (document_id, revision_number), \n\tCONSTRAINT uq_purchase_order_revision_successor UNIQUE (supersedes_revision_id), \n\tCONSTRAINT fk_purchase_order_supersedes FOREIGN KEY(document_id, supersedes_revision_id) REFERENCES purchase_order_revisions (document_id, id), \n\tCONSTRAINT ck_purchase_order_revision_number CHECK (typeof(revision_number) = 'integer' AND revision_number > 0), \n\tCONSTRAINT ck_purchase_order_total_minor_units CHECK (typeof(total_minor_units) = 'integer' AND total_minor_units > 0), \n\tCONSTRAINT ck_purchase_order_revision_status CHECK (status IN ('open', 'partly_received', 'closed', 'voided')), \n\tCONSTRAINT ck_purchase_order_currency CHECK (length(currency) = 3), \n\tCONSTRAINT ck_purchase_order_expected_date CHECK (expected_date IS NULL OR expected_date >= date), \n\tCONSTRAINT ck_purchase_order_profile_snapshot CHECK (json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'), \n\tCONSTRAINT ck_purchase_order_custom_fields_snapshot CHECK (json_valid(custom_fields_snapshot) AND json_type(custom_fields_snapshot) = 'object'), \n\tFOREIGN KEY(document_id) REFERENCES purchase_orders (id), \n\tFOREIGN KEY(vendor_id) REFERENCES vendors (id), \n\tFOREIGN KEY(terms_id) REFERENCES terms (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    'CREATE TABLE purchase_order_line_identities (\n\tid VARCHAR(26) NOT NULL, \n\tdocument_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_purchase_order_identity_owner UNIQUE (document_id, id), \n\tFOREIGN KEY(document_id) REFERENCES purchase_orders (id)\n)',
    "CREATE TABLE purchase_order_lines (\n\tid VARCHAR(26) NOT NULL, \n\tdocument_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tline_id VARCHAR(26) NOT NULL, \n\tposition BIGINT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\titem_id VARCHAR(26), \n\taccount_id VARCHAR(26), \n\tdescription VARCHAR(2000), \n\tquantity_microunits BIGINT, \n\trate_minor_units BIGINT, \n\tamount_minor_units BIGINT NOT NULL, \n\tcustomer_id VARCHAR(26), \n\tbillable BOOLEAN NOT NULL, \n\tclass_id VARCHAR(26), \n\tline_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_purchase_order_line_owner UNIQUE (document_id, revision_id, id), \n\tCONSTRAINT uq_purchase_order_line_identity UNIQUE (revision_id, line_id), \n\tCONSTRAINT uq_purchase_order_line_position UNIQUE (revision_id, position), \n\tCONSTRAINT fk_purchase_order_line_revision FOREIGN KEY(document_id, revision_id) REFERENCES purchase_order_revisions (document_id, id), \n\tCONSTRAINT fk_purchase_order_line_identity FOREIGN KEY(document_id, line_id) REFERENCES purchase_order_line_identities (document_id, id), \n\tCONSTRAINT ck_purchase_order_position CHECK (typeof(position) = 'integer' AND position > 0), \n\tCONSTRAINT ck_purchase_order_amount_minor_units CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_purchase_order_quantity_microunits CHECK (quantity_microunits IS NULL OR (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0)), \n\tCONSTRAINT ck_purchase_order_rate_minor_units CHECK (rate_minor_units IS NULL OR (typeof(rate_minor_units) = 'integer' AND rate_minor_units >= 0)), \n\tCONSTRAINT ck_purchase_order_line_source CHECK ((item_id IS NULL) <> (account_id IS NULL)), \n\tCONSTRAINT ck_purchase_order_line_extension CHECK ((quantity_microunits IS NULL) = (rate_minor_units IS NULL)), \n\tCONSTRAINT ck_purchase_order_billable_job CHECK (billable IN (0, 1) AND (billable = 0 OR customer_id IS NOT NULL)), \n\tCONSTRAINT ck_purchase_order_line_snapshot CHECK (json_valid(line_snapshot) AND json_type(line_snapshot) = 'object'), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id)\n)",
    "CREATE TABLE purchase_order_conversions (\n\tid VARCHAR(26) NOT NULL, \n\tsource_document_id VARCHAR(26) NOT NULL, \n\tsource_revision_id VARCHAR(26) NOT NULL, \n\tsource_version BIGINT NOT NULL, \n\tdestination_transaction_id VARCHAR(26) NOT NULL, \n\tdestination_revision_id VARCHAR(26) NOT NULL, \n\tdestination_type VARCHAR(32) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_purchase_order_conversion_source UNIQUE (source_document_id), \n\tCONSTRAINT uq_purchase_order_conversion_destination UNIQUE (destination_transaction_id), \n\tCONSTRAINT fk_purchase_order_conversion_source FOREIGN KEY(source_document_id, source_revision_id) REFERENCES purchase_order_revisions (document_id, id), \n\tCONSTRAINT fk_purchase_order_conversion_destination FOREIGN KEY(destination_transaction_id, destination_revision_id) REFERENCES purchase_profiles (transaction_id, revision_id), \n\tCONSTRAINT fk_purchase_order_conversion_type FOREIGN KEY(destination_transaction_id, destination_type) REFERENCES transactions (id, type), \n\tCONSTRAINT ck_purchase_order_source_version CHECK (typeof(source_version) = 'integer' AND source_version > 0), \n\tCONSTRAINT ck_purchase_order_destination_type CHECK (destination_type = 'bill'), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    'CREATE INDEX ix_purchase_order_revision_query ON purchase_order_revisions (date, document_id)',
    'CREATE INDEX ix_purchase_order_revision_vendor ON purchase_order_revisions (vendor_id)',
    'CREATE INDEX ix_purchase_orders_status_number ON purchase_orders (status, number)',
)
GUARDS = (
    "CREATE TRIGGER purchase_order_revisions_immutable_update BEFORE UPDATE ON purchase_order_revisions BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END",
    "CREATE TRIGGER purchase_order_revisions_immutable_delete BEFORE DELETE ON purchase_order_revisions BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END",
    "CREATE TRIGGER purchase_order_line_identities_immutable_update BEFORE UPDATE ON purchase_order_line_identities BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END",
    "CREATE TRIGGER purchase_order_line_identities_immutable_delete BEFORE DELETE ON purchase_order_line_identities BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END",
    "CREATE TRIGGER purchase_order_lines_immutable_update BEFORE UPDATE ON purchase_order_lines BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END",
    "CREATE TRIGGER purchase_order_lines_immutable_delete BEFORE DELETE ON purchase_order_lines BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END",
    "CREATE TRIGGER purchase_order_conversions_immutable_update BEFORE UPDATE ON purchase_order_conversions BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END",
    "CREATE TRIGGER purchase_order_conversions_immutable_delete BEFORE DELETE ON purchase_order_conversions BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END",
    "CREATE TRIGGER purchase_orders_no_delete BEFORE DELETE ON purchase_orders BEGIN SELECT RAISE(ABORT, 'immutable purchase order history'); END",
    "CREATE TRIGGER purchase_order_conversions_destination_type BEFORE INSERT ON purchase_order_conversions\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.destination_transaction_id AND type = NEW.destination_type)\nBEGIN SELECT RAISE(ABORT, 'purchase order conversion names the wrong document type'); END",
)


def upgrade():
    connection = op.get_bind()
    reserved = {name.casefold() for name in OBJECTS}
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        quoted = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + quoted + '.sqlite_schema'):
            if row[0].casefold() in reserved:
                raise RuntimeError('co0035 purchase order storage name collision')
    for statement in (*DDL, *GUARDS):
        connection.exec_driver_sql(statement)
    for name in NEW_TABLES:
        if connection.exec_driver_sql('SELECT 1 FROM main."' + name + '" LIMIT 1').fetchone():
            raise RuntimeError('co0035 purchase order storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0035 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
