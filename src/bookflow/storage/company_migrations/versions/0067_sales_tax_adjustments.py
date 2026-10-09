"""Sales tax adjustments: a thirteenth document type and the header naming the agency it adjusts.

One new table and two widened CHECK constraints. ``sales_tax_adjustment_profiles`` is the
one-to-one header of an Adjust Sales Tax Due revision; ``transactions`` gains
``sales_tax_adjustment`` as a document type and ``document_lines`` gains ``tax_adjustment`` as
an envelope kind, both of which live in a CHECK that SQLite only widens by rebuilding the table.
The document-line kind guard gains one branch pairing the two; the rest of its stored text is
kept as it is.

Nothing is backfilled. A company upgraded here owes exactly what it owed before: widening a
CHECK admits new rows and never rewrites old ones.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0067'
down_revision = 'co0066'
branch_labels = None
depends_on = None

NEW_TABLES = ('sales_tax_adjustment_profiles',)
OBJECTS = ('ix_sales_tax_adjustment_profiles_agency', 'ix_sales_tax_adjustment_profiles_attribution', 'sales_tax_adjustment_profiles', 'sales_tax_adjustment_profiles_agency_is_flagged', 'sales_tax_adjustment_profiles_immutable_delete', 'sales_tax_adjustment_profiles_immutable_update')
DDL = (
    "CREATE TABLE sales_tax_adjustment_profiles (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tagency_id VARCHAR(26) NOT NULL, \n\tliability_account_id VARCHAR(26) NOT NULL, \n\toffset_account_id VARCHAR(26) NOT NULL, \n\tdirection VARCHAR(8) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tliability_posting_source_id VARCHAR(26) NOT NULL, \n\tprofile_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT uq_sales_tax_adjustment_profile_owner UNIQUE (transaction_id, revision_id), \n\tCONSTRAINT fk_sales_tax_adjustment_profile_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT fk_sales_tax_adjustment_profile_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tCONSTRAINT fk_sales_tax_adjustment_profile_attribution FOREIGN KEY(transaction_id, liability_posting_source_id) REFERENCES posting_line_sources (transaction_id, id), \n\tCONSTRAINT ck_sales_tax_adjustment_profile_type CHECK (type = 'sales_tax_adjustment'), \n\tCONSTRAINT ck_sales_tax_adjustment_direction CHECK (direction IN ('increase', 'reduce')), \n\tCONSTRAINT ck_sales_tax_adjustment_offset CHECK (offset_account_id <> liability_account_id), \n\tCONSTRAINT ck_sales_tax_adjustment_amount_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_sales_tax_adjustment_profile_snapshot_object CHECK (json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(agency_id) REFERENCES vendors (id), \n\tFOREIGN KEY(liability_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(offset_account_id) REFERENCES accounts (id)\n)",
    'CREATE INDEX ix_sales_tax_adjustment_profiles_agency ON sales_tax_adjustment_profiles (agency_id, transaction_id)',
    'CREATE INDEX ix_sales_tax_adjustment_profiles_attribution ON sales_tax_adjustment_profiles (liability_posting_source_id)',
)
GUARDS = (
    "CREATE TRIGGER sales_tax_adjustment_profiles_immutable_update BEFORE UPDATE ON sales_tax_adjustment_profiles BEGIN SELECT RAISE(ABORT, 'immutable sales tax adjustment history'); END",
    "CREATE TRIGGER sales_tax_adjustment_profiles_immutable_delete BEFORE DELETE ON sales_tax_adjustment_profiles BEGIN SELECT RAISE(ABORT, 'immutable sales tax adjustment history'); END",
    "CREATE TRIGGER sales_tax_adjustment_profiles_agency_is_flagged BEFORE INSERT ON sales_tax_adjustment_profiles\nWHEN NOT EXISTS (SELECT 1 FROM vendors WHERE id = NEW.agency_id AND is_tax_agency = 1)\nBEGIN SELECT RAISE(ABORT, 'sales tax is adjusted for a flagged tax agency'); END",
)
CHANGED = ('transactions', 'document_lines')
# The document-line kind guard is rewritten by one targeted edit rather than replaced whole:
# its last branch, exactly once, gains the new pairing after it.
TRIGGERS = ('document_lines_type_insert',)
GUARD_TARGET = "(type = 'customer_refund' AND NEW.kind <> 'refund')))"
GUARD_REPLACEMENT = ("(type = 'customer_refund' AND NEW.kind <> 'refund') OR\n"
                     "(type = 'sales_tax_adjustment' AND NEW.kind <> 'tax_adjustment')))")
# The exact text this migration expects, and what it becomes.
REPLACEMENTS = {
    'transactions': ((
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo', 'sales_tax_payment', 'customer_refund', 'vendor_credit', 'statement_charge'))",
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo', 'sales_tax_payment', 'customer_refund', 'vendor_credit', 'statement_charge', 'sales_tax_adjustment'))"),),
    'document_lines': ((
        "kind IN ('sale', 'payment', 'deposit', 'purchase', 'bill_payment', 'credit', 'sales_tax_payment', 'refund')",
        "kind IN ('sale', 'payment', 'deposit', 'purchase', 'bill_payment', 'credit', 'sales_tax_payment', 'refund', 'tax_adjustment')"),),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0067 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0067 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0067_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = {value.casefold() for value in set(OBJECTS) | {'_co0067_' + name for name in CHANGED}}
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        attached = '"' + name.replace('"', '""') + '"'
        if any(row[0].casefold() in reserved
               for row in connection.exec_driver_sql('SELECT name FROM ' + attached + '.sqlite_schema')):
            raise RuntimeError('co0067 reserved object already exists')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join(f"'{name}'" for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        f'OR (type = \'index\' AND tbl_name IN ({changed}))) '
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    if stored.get('document_lines_type_insert', '').count(GUARD_TARGET) != 1:
        raise RuntimeError('co0067 unknown document type guard')
    # Keep unknown local objects verbatim; do not guess through competing guards.
    for name, sql in stored.items():
        if name in TRIGGERS:
            continue
        if re.search(r'(?i)\b(?:NEW|OLD)\s*\.\s*["`\[]?(?:type|kind)\b', sql) and re.search(
                r'(?i)\bON\s+["`\[]?(?:transactions|document_lines)\b', sql):
            raise RuntimeError('co0067 unknown competing document type guard')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0067_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0067 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for statement in DDL:
        connection.exec_driver_sql(statement)
    for _, name, statement in retained:
        if name == 'document_lines_type_insert':
            statement = statement.replace(GUARD_TARGET, GUARD_REPLACEMENT, 1)
        connection.exec_driver_sql(statement)
    for statement in GUARDS:
        connection.exec_driver_sql(statement)
    for name in NEW_TABLES:
        if connection.exec_driver_sql('SELECT 1 FROM main."' + name + '" LIMIT 1').fetchone():
            raise RuntimeError('co0067 adjustment storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0067 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
