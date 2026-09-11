"""Bill payments: a settlement document type, its money source and the applications it makes.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0026'
down_revision = 'co0025'
branch_labels = None
depends_on = None

NEW_TABLES = ('ap_payment_profiles', 'ap_source_keys', 'ap_source_components', 'ap_applications')
OBJECTS = ('ap_applications', 'ap_applications_exact_inverse', 'ap_applications_exact_party', 'ap_applications_immutable_delete', 'ap_applications_immutable_update', 'ap_applications_obligation_transaction_id_type', 'ap_applications_source_transaction_id_type', 'ap_payment_profiles', 'ap_payment_profiles_immutable_delete', 'ap_payment_profiles_immutable_update', 'ap_source_components', 'ap_source_components_immutable_delete', 'ap_source_components_immutable_update', 'ap_source_keys', 'ap_source_keys_immutable_delete', 'ap_source_keys_immutable_update', 'ap_source_keys_transaction_id_type', 'ix_ap_applications_obligation', 'ix_ap_applications_source', 'ix_ap_payment_profiles_vendor', 'ix_ap_source_components_key', 'ix_ap_source_keys_vendor')
DDL = (
    "CREATE TABLE ap_payment_profiles (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tvendor_id VARCHAR(26) NOT NULL, \n\tap_account_id VARCHAR(26) NOT NULL, \n\tfunding_account_id VARCHAR(26) NOT NULL, \n\tfunding_kind VARCHAR(16) NOT NULL, \n\tpayment_method_id VARCHAR(26) NOT NULL, \n\tcheck_number VARCHAR(64), \n\treference VARCHAR(128), \n\tamount_minor_units BIGINT NOT NULL, \n\tprofile_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT uq_ap_payment_profile_owner UNIQUE (transaction_id, revision_id), \n\tCONSTRAINT fk_ap_payment_profile_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT fk_ap_payment_profile_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tCONSTRAINT ck_ap_payment_profile_type CHECK (type = 'bill_payment'), \n\tCONSTRAINT ck_ap_payment_funding_kind CHECK (funding_kind IN ('bank_cash', 'card_liability')), \n\tCONSTRAINT ck_ap_payment_check_number CHECK (check_number IS NULL OR funding_kind = 'bank_cash'), \n\tCONSTRAINT ck_ap_amount_minor_units_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_ap_profile_snapshot_object CHECK (json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(vendor_id) REFERENCES vendors (id), \n\tFOREIGN KEY(ap_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(funding_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(payment_method_id) REFERENCES payment_methods (id)\n)",
    "CREATE TABLE ap_source_keys (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tordinal BIGINT NOT NULL, \n\tsource_type VARCHAR(32) NOT NULL, \n\tvendor_id VARCHAR(26) NOT NULL, \n\tap_account_id VARCHAR(26) NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_ap_source_owner UNIQUE (transaction_id, id), \n\tCONSTRAINT uq_ap_source_ordinal UNIQUE (transaction_id, ordinal), \n\tCONSTRAINT ck_ap_source_type CHECK (source_type = 'bill_payment'), \n\tCONSTRAINT ck_ap_ordinal_positive CHECK (typeof(ordinal) = 'integer' AND ordinal > 0), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(vendor_id) REFERENCES vendors (id), \n\tFOREIGN KEY(ap_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    "CREATE TABLE ap_source_components (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tkey_id VARCHAR(26) NOT NULL, \n\tdocument_line_id VARCHAR(26) NOT NULL, \n\tordinal BIGINT NOT NULL, \n\tposting_source_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_ap_source_component_occurrence UNIQUE (revision_id, document_line_id, ordinal), \n\tCONSTRAINT uq_ap_source_component_revision UNIQUE (transaction_id, revision_id, id), \n\tCONSTRAINT uq_ap_source_component_owner UNIQUE (transaction_id, id), \n\tCONSTRAINT fk_ap_source_component_key FOREIGN KEY(transaction_id, key_id) REFERENCES ap_source_keys (transaction_id, id), \n\tCONSTRAINT fk_ap_source_component_envelope FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT fk_ap_source_component_attribution FOREIGN KEY(transaction_id, posting_source_id) REFERENCES posting_line_sources (transaction_id, id), \n\tCONSTRAINT ck_ap_ordinal_positive CHECK (typeof(ordinal) = 'integer' AND ordinal > 0), \n\tCONSTRAINT ck_ap_amount_minor_units_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    "CREATE TABLE ap_applications (\n\tid VARCHAR(26) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tsource_transaction_id VARCHAR(26) NOT NULL, \n\tsource_key_id VARCHAR(26) NOT NULL, \n\tsource_component_id VARCHAR(26) NOT NULL, \n\tobligation_transaction_id VARCHAR(26) NOT NULL, \n\tobligation_key_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\treverses_application_id VARCHAR(26), \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_ap_amount_minor_units_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_ap_application_kind CHECK ((kind = 'apply' AND reverses_application_id IS NULL) OR (kind = 'unapply' AND reverses_application_id IS NOT NULL AND reverses_application_id <> id)), \n\tCONSTRAINT uq_ap_application_inverse UNIQUE (reverses_application_id), \n\tCONSTRAINT fk_ap_application_source FOREIGN KEY(source_transaction_id, source_key_id) REFERENCES ap_source_keys (transaction_id, id), \n\tCONSTRAINT fk_ap_application_component FOREIGN KEY(source_transaction_id, source_component_id) REFERENCES ap_source_components (transaction_id, id), \n\tCONSTRAINT fk_ap_application_target FOREIGN KEY(obligation_transaction_id, obligation_key_id) REFERENCES ap_obligation_keys (transaction_id, id), \n\tFOREIGN KEY(reverses_application_id) REFERENCES ap_applications (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    'CREATE INDEX ix_ap_applications_obligation ON ap_applications (obligation_key_id, effective_date, id)',
    'CREATE INDEX ix_ap_applications_source ON ap_applications (source_key_id, effective_date, id)',
    'CREATE INDEX ix_ap_payment_profiles_vendor ON ap_payment_profiles (vendor_id, ap_account_id, transaction_id)',
    'CREATE INDEX ix_ap_source_components_key ON ap_source_components (key_id, revision_id)',
    'CREATE INDEX ix_ap_source_keys_vendor ON ap_source_keys (vendor_id, ap_account_id, id)',
)
GUARDS = (
    "CREATE TRIGGER ap_payment_profiles_immutable_update BEFORE UPDATE ON ap_payment_profiles BEGIN SELECT RAISE(ABORT, 'immutable settlement history'); END",
    "CREATE TRIGGER ap_payment_profiles_immutable_delete BEFORE DELETE ON ap_payment_profiles BEGIN SELECT RAISE(ABORT, 'immutable settlement history'); END",
    "CREATE TRIGGER ap_source_keys_immutable_update BEFORE UPDATE ON ap_source_keys BEGIN SELECT RAISE(ABORT, 'immutable settlement history'); END",
    "CREATE TRIGGER ap_source_keys_immutable_delete BEFORE DELETE ON ap_source_keys BEGIN SELECT RAISE(ABORT, 'immutable settlement history'); END",
    "CREATE TRIGGER ap_source_components_immutable_update BEFORE UPDATE ON ap_source_components BEGIN SELECT RAISE(ABORT, 'immutable settlement history'); END",
    "CREATE TRIGGER ap_source_components_immutable_delete BEFORE DELETE ON ap_source_components BEGIN SELECT RAISE(ABORT, 'immutable settlement history'); END",
    "CREATE TRIGGER ap_applications_immutable_update BEFORE UPDATE ON ap_applications BEGIN SELECT RAISE(ABORT, 'immutable settlement history'); END",
    "CREATE TRIGGER ap_applications_immutable_delete BEFORE DELETE ON ap_applications BEGIN SELECT RAISE(ABORT, 'immutable settlement history'); END",
    "CREATE TRIGGER ap_source_keys_transaction_id_type BEFORE INSERT ON ap_source_keys\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND type = 'bill_payment')\nBEGIN SELECT RAISE(ABORT, 'settlement reference has wrong document type'); END",
    "CREATE TRIGGER ap_applications_source_transaction_id_type BEFORE INSERT ON ap_applications\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.source_transaction_id AND type = 'bill_payment')\nBEGIN SELECT RAISE(ABORT, 'settlement reference has wrong document type'); END",
    "CREATE TRIGGER ap_applications_obligation_transaction_id_type BEFORE INSERT ON ap_applications\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.obligation_transaction_id AND type = 'bill')\nBEGIN SELECT RAISE(ABORT, 'settlement reference has wrong document type'); END",
    "CREATE TRIGGER ap_applications_exact_inverse BEFORE INSERT ON ap_applications\nWHEN NEW.kind = 'unapply' AND NOT EXISTS (SELECT 1 FROM ap_applications a\nWHERE a.id = NEW.reverses_application_id AND a.kind = 'apply' AND a.source_transaction_id IS NEW.source_transaction_id AND a.source_key_id IS NEW.source_key_id AND a.source_component_id IS NEW.source_component_id AND a.obligation_transaction_id IS NEW.obligation_transaction_id AND a.obligation_key_id IS NEW.obligation_key_id AND a.amount_minor_units IS NEW.amount_minor_units AND a.currency IS NEW.currency AND a.effective_date IS NEW.effective_date)\nBEGIN SELECT RAISE(ABORT, 'unapply must exactly reverse an original application'); END",
    "CREATE TRIGGER ap_applications_exact_party BEFORE INSERT ON ap_applications\nWHEN NOT EXISTS (SELECT 1 FROM ap_source_keys s JOIN ap_obligation_keys o\nON o.id = NEW.obligation_key_id AND o.transaction_id = NEW.obligation_transaction_id\nWHERE s.id = NEW.source_key_id AND s.transaction_id = NEW.source_transaction_id\nAND s.vendor_id = o.vendor_id AND s.ap_account_id = o.ap_account_id\nAND s.currency = o.currency AND s.currency = NEW.currency)\nBEGIN SELECT RAISE(ABORT, 'application source and target ownership differ'); END",
    "CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines\nWHEN EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND\n((type = 'journal_entry' AND NEW.kind <> 'journal') OR\n(type IN ('invoice', 'sales_receipt') AND NEW.kind <> 'sale') OR\n(type = 'payment' AND NEW.kind <> 'payment') OR\n(type = 'deposit' AND NEW.kind <> 'deposit') OR\n(type = 'bill' AND NEW.kind <> 'purchase') OR\n(type = 'bill_payment' AND NEW.kind <> 'bill_payment')))\nBEGIN SELECT RAISE(ABORT, 'document line kind does not match transaction type'); END",
)
CHANGED = ('transactions', 'document_lines')
# The exact co0025 constraint text this migration expects, and what it becomes. A bill payment
# is a seventh document type and its selected bills are a sixth envelope kind; both live in a
# CHECK, and SQLite only widens a CHECK by rebuilding the table.
REPLACEMENTS = {
    'transactions': (
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill'))",
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment'))"),
    'document_lines': (
        "kind IN ('sale', 'payment', 'deposit', 'purchase')",
        "kind IN ('sale', 'payment', 'deposit', 'purchase', 'bill_payment')"),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0026 cannot preserve custom row identity')
    old, new = REPLACEMENTS[table]
    found = [i for i, part in enumerate(parts) if old in part]
    if len(found) != 1 or parts[found[0]].count(old) != 1:
        raise RuntimeError('co0026 unknown constraint: ' + table)
    parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0026_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = set(OBJECTS) | {'_co0026_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0026 reserved object already exists')
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        attached = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + attached + '.sqlite_schema'):
            if row[0].casefold() in {value.casefold() for value in OBJECTS}:
                raise RuntimeError('co0026 settlement storage name collision')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name IN ('transactions','document_lines'))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    bills = importlib.import_module('bookflow.storage.company_migrations.versions.0025_bills')
    old_guard = next(statement for statement in bills.GUARDS if statement.startswith('CREATE TRIGGER document_lines_type_insert '))
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    if stored.get('document_lines_type_insert') != old_guard:
        raise RuntimeError('co0026 unknown document type guard')
    # Keep unknown local objects verbatim; do not guess through competing guards.
    for name, sql in stored.items():
        if name != 'document_lines_type_insert' and re.search(r'(?i)\b(?:NEW|OLD)\s*\.\s*["`\[]?type\b', sql) and re.search(r'(?i)\bON\s+["`\[]?transactions\b', sql):
            raise RuntimeError('co0026 unknown competing document type guard')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0026_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0026 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for statement in DDL:
        connection.exec_driver_sql(statement)
    for _, name, statement in retained:
        if name != 'document_lines_type_insert':
            connection.exec_driver_sql(statement)
    for statement in GUARDS:
        connection.exec_driver_sql(statement)
    for name in NEW_TABLES:
        if connection.exec_driver_sql('SELECT 1 FROM main."' + name + '" LIMIT 1').fetchone():
            raise RuntimeError('co0026 settlement storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0026 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
