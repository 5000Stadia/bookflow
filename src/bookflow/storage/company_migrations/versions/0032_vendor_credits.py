"""Vendor credits: an eleventh document type, its header and lines, and a second settlement source.

Two new tables and two widened CHECK constraints. ``vendor_credit_profiles`` is the one-to-one
header of a vendor-credit revision and ``vendor_credit_expense_lines`` is its Expenses grid;
``transactions`` gains ``vendor_credit`` as a document type and ``ap_source_keys`` gains it as
a settlement source kind, both of which live in a CHECK, and SQLite only widens a CHECK by
rebuilding the table. Three triggers are rewritten for the same reason: the document-line kind
guard now admits a credit's purchase envelope, the source-key type guard now checks the
document against the source's own declared kind rather than one hard-coded word, and the
settlement edge now admits either kind of paying source.

``document_lines`` is deliberately **not** rebuilt. A vendor credit's line is a ``purchase``
envelope, the same family a bill's line is, so the stored kind CHECK already admits it; only
the trigger that pairs a document type with its kind had to learn the new pairing.

Nothing is backfilled. A company upgraded here owes exactly what it owed before: the two new
tables are empty, no ``ap_source_keys`` row changes, and every settlement that stood before
stands unchanged, because widening a CHECK admits new rows and never rewrites old ones.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0032'
down_revision = 'co0031'
branch_labels = None
depends_on = None

NEW_TABLES = ('vendor_credit_profiles', 'vendor_credit_expense_lines')
OBJECTS = ('ix_vendor_credit_profiles_reference', 'vendor_credit_expense_lines', 'vendor_credit_expense_lines_immutable_delete', 'vendor_credit_expense_lines_immutable_update', 'vendor_credit_profiles', 'vendor_credit_profiles_immutable_delete', 'vendor_credit_profiles_immutable_update', 'vendor_credit_profiles_transaction_id_type')
DDL = (
    "CREATE TABLE vendor_credit_profiles (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tvendor_id VARCHAR(26) NOT NULL, \n\tap_account_id VARCHAR(26) NOT NULL, \n\tsupplier_reference VARCHAR(128), \n\tsupplier_reference_key VARCHAR(256), \n\texpense_total_minor_units BIGINT NOT NULL, \n\tprofile_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT uq_vendor_credit_profile_owner UNIQUE (transaction_id, revision_id), \n\tCONSTRAINT fk_vendor_credit_profile_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT fk_vendor_credit_profile_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tCONSTRAINT ck_vendor_credit_profile_type CHECK (type = 'vendor_credit'), \n\tCONSTRAINT ck_vendor_credit_profile_reference_pair CHECK ((supplier_reference IS NULL) = (supplier_reference_key IS NULL)), \n\tCONSTRAINT ck_vendor_credit_expense_total_minor_units_positive CHECK (typeof(expense_total_minor_units) = 'integer' AND expense_total_minor_units > 0), \n\tCONSTRAINT ck_vendor_credit_profile_snapshot_object CHECK (json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'), \n\tFOREIGN KEY(vendor_id) REFERENCES vendors (id), \n\tFOREIGN KEY(ap_account_id) REFERENCES accounts (id)\n)",
    "CREATE TABLE vendor_credit_expense_lines (\n\tdocument_line_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcustomer_id VARCHAR(26), \n\tline_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (document_line_id), \n\tCONSTRAINT uq_vendor_credit_expense_line_owner UNIQUE (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_vendor_credit_expense_line_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES vendor_credit_profiles (transaction_id, revision_id), \n\tCONSTRAINT fk_vendor_credit_expense_line_envelope FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT ck_vendor_credit_amount_minor_units_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_vendor_credit_line_snapshot_object CHECK (json_valid(line_snapshot) AND json_type(line_snapshot) = 'object'), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)",
    'CREATE INDEX ix_vendor_credit_profiles_reference ON vendor_credit_profiles (vendor_id, supplier_reference_key)',
)
GUARDS = (
    "CREATE TRIGGER vendor_credit_profiles_immutable_update BEFORE UPDATE ON vendor_credit_profiles BEGIN SELECT RAISE(ABORT, 'immutable vendor credit history'); END",
    "CREATE TRIGGER vendor_credit_profiles_immutable_delete BEFORE DELETE ON vendor_credit_profiles BEGIN SELECT RAISE(ABORT, 'immutable vendor credit history'); END",
    "CREATE TRIGGER vendor_credit_expense_lines_immutable_update BEFORE UPDATE ON vendor_credit_expense_lines BEGIN SELECT RAISE(ABORT, 'immutable vendor credit history'); END",
    "CREATE TRIGGER vendor_credit_expense_lines_immutable_delete BEFORE DELETE ON vendor_credit_expense_lines BEGIN SELECT RAISE(ABORT, 'immutable vendor credit history'); END",
    "CREATE TRIGGER vendor_credit_profiles_transaction_id_type BEFORE INSERT ON vendor_credit_profiles\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND type = 'vendor_credit')\nBEGIN SELECT RAISE(ABORT, 'vendor credit reference has wrong document type'); END",
    "CREATE TRIGGER ap_source_keys_transaction_id_type BEFORE INSERT ON ap_source_keys\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND type = NEW.source_type)\nBEGIN SELECT RAISE(ABORT, 'settlement reference has wrong document type'); END",
    "CREATE TRIGGER ap_applications_source_transaction_id_type BEFORE INSERT ON ap_applications\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.source_transaction_id AND type IN ('bill_payment', 'vendor_credit'))\nBEGIN SELECT RAISE(ABORT, 'settlement reference has wrong document type'); END",
    "CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines\nWHEN EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND\n((type = 'journal_entry' AND NEW.kind <> 'journal') OR\n(type IN ('invoice', 'sales_receipt') AND NEW.kind <> 'sale') OR\n(type = 'payment' AND NEW.kind <> 'payment') OR\n(type = 'deposit' AND NEW.kind <> 'deposit') OR\n(type IN ('bill', 'vendor_credit') AND NEW.kind <> 'purchase') OR\n(type = 'bill_payment' AND NEW.kind <> 'bill_payment') OR\n(type = 'credit_memo' AND NEW.kind <> 'credit') OR\n(type = 'sales_tax_payment' AND NEW.kind <> 'sales_tax_payment') OR\n(type = 'customer_refund' AND NEW.kind <> 'refund')))\nBEGIN SELECT RAISE(ABORT, 'document line kind does not match transaction type'); END",
)


CHANGED = ('transactions', 'ap_source_keys')
# The three triggers this revision deliberately rewrites, each verified against the exact text
# the revision that last froze it wrote. Anything else stored on the rebuilt tables is a local
# guard whose meaning this migration cannot know, and it stops rather than guessing.
REPLACED = ('ap_applications_source_transaction_id_type', 'ap_source_keys_transaction_id_type',
            'document_lines_type_insert')
# The exact text this migration expects, and what it becomes. A vendor credit is an eleventh
# document type and the second kind of money that can settle a payable.
REPLACEMENTS = {
    'transactions': ((
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo', 'sales_tax_payment', 'customer_refund'))",
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo', 'sales_tax_payment', 'customer_refund', 'vendor_credit'))"),),
    'ap_source_keys': ((
        "CONSTRAINT ck_ap_source_type CHECK (source_type = 'bill_payment')",
        "CONSTRAINT ck_ap_source_type CHECK (source_type IN ('bill_payment', 'vendor_credit'))"),),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0032 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0032 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0032_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def _expected_guards():
    """The exact text of each replaced trigger, taken from the revision that last froze it.

    Every other trigger is dropped and restored verbatim by the rebuild, so only the ones this
    revision rewrites have to be recognised -- and those have to match to the byte, or what is
    stored is a local guard whose meaning this migration cannot know.
    """
    known = {}
    for name in ('0026_bill_payments', '0031_customer_refunds'):
        module = importlib.import_module('bookflow.storage.company_migrations.versions.' + name)
        for statement in module.GUARDS:
            known[statement.split()[2]] = statement
    return {name: known[name] for name in REPLACED if name in known}


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = set(OBJECTS) | {'_co0032_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0032 reserved object already exists')
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        attached = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + attached + '.sqlite_schema'):
            if row[0].casefold() in {value.casefold() for value in OBJECTS}:
                raise RuntimeError('co0032 vendor credit storage name collision')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join(f"'{name}'" for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        f'OR (type = \'index\' AND tbl_name IN ({changed}))) '
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    expected = _expected_guards()
    if set(REPLACED) - set(expected) or any(stored.get(name) != sql for name, sql in expected.items()):
        raise RuntimeError('co0032 unknown document type guard')
    # Keep unknown local objects verbatim; do not guess through competing guards.
    for name, sql in stored.items():
        if name in expected:
            continue
        if re.search(r'(?i)\b(?:NEW|OLD)\s*\.\s*["`\[]?(?:type|source_type)\b', sql) and re.search(
                r'(?i)\bON\s+["`\[]?(?:transactions|document_lines|ap_source_keys|ap_applications)\b', sql):
            raise RuntimeError('co0032 unknown competing document type guard')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0032_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0032 rebuilt values differ: ' + table)
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
            raise RuntimeError('co0032 vendor credit storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0032 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
