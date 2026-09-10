"""Bills: a purchase document type, its expense lines and the payable it creates.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0025'
down_revision = 'co0024'
branch_labels = None
depends_on = None

NEW_TABLES = ('purchase_profiles', 'purchase_expense_lines', 'ap_obligation_keys', 'ap_obligation_components')
OBJECTS = ('ap_obligation_components', 'ap_obligation_components_immutable_delete', 'ap_obligation_components_immutable_update', 'ap_obligation_keys', 'ap_obligation_keys_immutable_delete', 'ap_obligation_keys_immutable_update', 'ix_ap_components_key', 'ix_ap_obligation_keys_vendor', 'ix_purchase_profiles_reference', 'purchase_expense_lines', 'purchase_expense_lines_immutable_delete', 'purchase_expense_lines_immutable_update', 'purchase_profiles', 'purchase_profiles_immutable_delete', 'purchase_profiles_immutable_update')
DDL = ("CREATE TABLE purchase_profiles (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tvendor_id VARCHAR(26) NOT NULL, \n\tap_account_id VARCHAR(26) NOT NULL, \n\tterms_id VARCHAR(26), \n\tdue_date VARCHAR(10) NOT NULL, \n\tsupplier_reference VARCHAR(128), \n\tsupplier_reference_key VARCHAR(256), \n\texpense_total_minor_units BIGINT NOT NULL, \n\tprofile_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT uq_purchase_profile_owner UNIQUE (transaction_id, revision_id), \n\tCONSTRAINT fk_purchase_profile_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT fk_purchase_profile_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tCONSTRAINT ck_purchase_profile_type CHECK (type = 'bill'), \n\tCONSTRAINT ck_purchase_profile_reference_pair CHECK ((supplier_reference IS NULL) = (supplier_reference_key IS NULL)), \n\tCONSTRAINT ck_purchase_expense_total_minor_units_positive CHECK (typeof(expense_total_minor_units) = 'integer' AND expense_total_minor_units > 0), \n\tCONSTRAINT ck_purchase_profile_snapshot_object CHECK (json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'), \n\tFOREIGN KEY(vendor_id) REFERENCES vendors (id), \n\tFOREIGN KEY(ap_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(terms_id) REFERENCES terms (id)\n)", "CREATE TABLE purchase_expense_lines (\n\tdocument_line_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcustomer_id VARCHAR(26), \n\tbillable BOOLEAN NOT NULL, \n\tline_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (document_line_id), \n\tCONSTRAINT uq_purchase_expense_line_owner UNIQUE (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_purchase_expense_line_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES purchase_profiles (transaction_id, revision_id), \n\tCONSTRAINT fk_purchase_expense_line_envelope FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT ck_purchase_amount_minor_units_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_purchase_line_snapshot_object CHECK (json_valid(line_snapshot) AND json_type(line_snapshot) = 'object'), \n\tCONSTRAINT ck_purchase_expense_billable_job CHECK (billable IN (0, 1) AND (billable = 0 OR customer_id IS NOT NULL)), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)", "CREATE TABLE ap_obligation_keys (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tordinal BIGINT NOT NULL, \n\tvendor_id VARCHAR(26) NOT NULL, \n\tap_account_id VARCHAR(26) NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_ap_obligation_owner UNIQUE (transaction_id, id), \n\tCONSTRAINT uq_ap_obligation_ordinal UNIQUE (transaction_id, ordinal), \n\tCONSTRAINT ck_purchase_ordinal_positive CHECK (typeof(ordinal) = 'integer' AND ordinal > 0), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(vendor_id) REFERENCES vendors (id), \n\tFOREIGN KEY(ap_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)", "CREATE TABLE ap_obligation_components (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tkey_id VARCHAR(26) NOT NULL, \n\tdocument_line_id VARCHAR(26) NOT NULL, \n\tordinal BIGINT NOT NULL, \n\tposting_source_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_ap_component_occurrence UNIQUE (revision_id, document_line_id, ordinal), \n\tCONSTRAINT uq_ap_component_owner UNIQUE (transaction_id, revision_id, id), \n\tCONSTRAINT fk_ap_component_key FOREIGN KEY(transaction_id, key_id) REFERENCES ap_obligation_keys (transaction_id, id), \n\tCONSTRAINT fk_ap_component_envelope FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT fk_ap_component_attribution FOREIGN KEY(transaction_id, posting_source_id) REFERENCES posting_line_sources (transaction_id, id), \n\tCONSTRAINT ck_purchase_ordinal_positive CHECK (typeof(ordinal) = 'integer' AND ordinal > 0), \n\tCONSTRAINT ck_purchase_amount_minor_units_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)", 'CREATE INDEX ix_purchase_profiles_reference ON purchase_profiles (vendor_id, supplier_reference_key)', 'CREATE INDEX ix_ap_obligation_keys_vendor ON ap_obligation_keys (vendor_id, ap_account_id, id)', 'CREATE INDEX ix_ap_components_key ON ap_obligation_components (key_id, revision_id)')
GUARDS = ("CREATE TRIGGER purchase_profiles_immutable_update BEFORE UPDATE ON purchase_profiles BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END", "CREATE TRIGGER purchase_profiles_immutable_delete BEFORE DELETE ON purchase_profiles BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END", "CREATE TRIGGER purchase_expense_lines_immutable_update BEFORE UPDATE ON purchase_expense_lines BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END", "CREATE TRIGGER purchase_expense_lines_immutable_delete BEFORE DELETE ON purchase_expense_lines BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END", "CREATE TRIGGER ap_obligation_keys_immutable_update BEFORE UPDATE ON ap_obligation_keys BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END", "CREATE TRIGGER ap_obligation_keys_immutable_delete BEFORE DELETE ON ap_obligation_keys BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END", "CREATE TRIGGER ap_obligation_components_immutable_update BEFORE UPDATE ON ap_obligation_components BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END", "CREATE TRIGGER ap_obligation_components_immutable_delete BEFORE DELETE ON ap_obligation_components BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END", "CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines\nWHEN EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND\n((type = 'journal_entry' AND NEW.kind <> 'journal') OR\n(type IN ('invoice', 'sales_receipt') AND NEW.kind <> 'sale') OR\n(type = 'payment' AND NEW.kind <> 'payment') OR\n(type = 'deposit' AND NEW.kind <> 'deposit') OR\n(type = 'bill' AND NEW.kind <> 'purchase')))\nBEGIN SELECT RAISE(ABORT, 'document line kind does not match transaction type'); END")
CHANGED = ('transactions', 'document_lines')
# The exact co0024 constraint text this migration expects, and what it becomes. A bill is a
# sixth document type and its entered lines are a fifth envelope kind; both live in a CHECK,
# and SQLite only widens a CHECK by rebuilding the table.
REPLACEMENTS = {
    'transactions': (
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit'))",
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill'))"),
    'document_lines': (
        "kind IN ('sale', 'payment', 'deposit')",
        "kind IN ('sale', 'payment', 'deposit', 'purchase')"),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0025 cannot preserve custom row identity')
    old, new = REPLACEMENTS[table]
    found = [i for i, part in enumerate(parts) if old in part]
    if len(found) != 1 or parts[found[0]].count(old) != 1:
        raise RuntimeError('co0025 unknown constraint: ' + table)
    parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0025_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = set(OBJECTS) | {'_co0025_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0025 reserved object already exists')
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        attached = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + attached + '.sqlite_schema'):
            if row[0].casefold() in {value.casefold() for value in OBJECTS}:
                raise RuntimeError('co0025 purchase storage name collision')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name IN ('transactions','document_lines'))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    deposits = importlib.import_module('bookflow.storage.company_migrations.versions.0020_deposits')
    old_guard = next(statement for statement in deposits.GUARDS if statement.startswith('CREATE TRIGGER document_lines_type_insert '))
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    if stored.get('document_lines_type_insert') != old_guard:
        raise RuntimeError('co0025 unknown document type guard')
    # Keep unknown local objects verbatim; do not guess through competing guards.
    for name, sql in stored.items():
        if name != 'document_lines_type_insert' and re.search(r'(?i)\b(?:NEW|OLD)\s*\.\s*["`\[]?type\b', sql) and re.search(r'(?i)\bON\s+["`\[]?transactions\b', sql):
            raise RuntimeError('co0025 unknown competing document type guard')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0025_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0025 rebuilt values differ: ' + table)
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
            raise RuntimeError('co0025 purchase storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0025 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
