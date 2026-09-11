"""Sales tax remittance: a ninth document type and the header that names the agency it paid.

One new table and two widened CHECK constraints. ``sales_tax_payment_profiles`` is the
one-to-one header of a remittance revision; ``transactions`` gains ``sales_tax_payment`` as a
document type and ``document_lines`` gains it as an envelope kind, both of which live in a
CHECK, and SQLite only widens a CHECK by rebuilding the table. The document-type guard on
``document_lines`` is rewritten for the same reason and is the only trigger this revision
replaces.

Nothing is backfilled. A company upgraded here owes exactly what it owed before: the liability
read derives from the tax components already stored, and this revision only adds the document
that can pay it.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0030'
down_revision = 'co0029'
branch_labels = None
depends_on = None

NEW_TABLES = ('sales_tax_payment_profiles',)
OBJECTS = ('ix_sales_tax_payment_profiles_agency', 'ix_sales_tax_payment_profiles_attribution', 'sales_tax_payment_profiles', 'sales_tax_payment_profiles_agency_is_flagged', 'sales_tax_payment_profiles_immutable_delete', 'sales_tax_payment_profiles_immutable_update')
DDL = (
    "CREATE TABLE sales_tax_payment_profiles (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tagency_id VARCHAR(26) NOT NULL, \n\tliability_account_id VARCHAR(26) NOT NULL, \n\tfunding_account_id VARCHAR(26) NOT NULL, \n\tfunding_kind VARCHAR(16) NOT NULL, \n\tpayment_method_id VARCHAR(26) NOT NULL, \n\tcheck_number VARCHAR(64), \n\treference VARCHAR(128), \n\tthrough_date VARCHAR(10) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tliability_posting_source_id VARCHAR(26) NOT NULL, \n\tprofile_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT uq_sales_tax_payment_profile_owner UNIQUE (transaction_id, revision_id), \n\tCONSTRAINT fk_sales_tax_payment_profile_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT fk_sales_tax_payment_profile_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tCONSTRAINT fk_sales_tax_payment_profile_attribution FOREIGN KEY(transaction_id, liability_posting_source_id) REFERENCES posting_line_sources (transaction_id, id), \n\tCONSTRAINT ck_sales_tax_payment_profile_type CHECK (type = 'sales_tax_payment'), \n\tCONSTRAINT ck_sales_tax_payment_funding_kind CHECK (funding_kind IN ('bank_cash', 'card_liability')), \n\tCONSTRAINT ck_sales_tax_payment_check_number CHECK (check_number IS NULL OR funding_kind = 'bank_cash'), \n\tCONSTRAINT ck_sales_tax_payment_amount_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_sales_tax_payment_through_date CHECK (through_date LIKE '____-__-__'), \n\tCONSTRAINT ck_sales_tax_payment_profile_snapshot_object CHECK (json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(agency_id) REFERENCES vendors (id), \n\tFOREIGN KEY(liability_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(funding_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(payment_method_id) REFERENCES payment_methods (id)\n)",
    'CREATE INDEX ix_sales_tax_payment_profiles_agency ON sales_tax_payment_profiles (agency_id, through_date, transaction_id)',
    'CREATE INDEX ix_sales_tax_payment_profiles_attribution ON sales_tax_payment_profiles (liability_posting_source_id)',
)
GUARDS = (
    "CREATE TRIGGER sales_tax_payment_profiles_immutable_update BEFORE UPDATE ON sales_tax_payment_profiles BEGIN SELECT RAISE(ABORT, 'immutable remittance history'); END",
    "CREATE TRIGGER sales_tax_payment_profiles_immutable_delete BEFORE DELETE ON sales_tax_payment_profiles BEGIN SELECT RAISE(ABORT, 'immutable remittance history'); END",
    "CREATE TRIGGER sales_tax_payment_profiles_agency_is_flagged BEFORE INSERT ON sales_tax_payment_profiles\nWHEN NOT EXISTS (SELECT 1 FROM vendors WHERE id = NEW.agency_id AND is_tax_agency = 1)\nBEGIN SELECT RAISE(ABORT, 'sales tax is remitted to a flagged tax agency'); END",
    "CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines\nWHEN EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND\n((type = 'journal_entry' AND NEW.kind <> 'journal') OR\n(type IN ('invoice', 'sales_receipt') AND NEW.kind <> 'sale') OR\n(type = 'payment' AND NEW.kind <> 'payment') OR\n(type = 'deposit' AND NEW.kind <> 'deposit') OR\n(type = 'bill' AND NEW.kind <> 'purchase') OR\n(type = 'bill_payment' AND NEW.kind <> 'bill_payment') OR\n(type = 'credit_memo' AND NEW.kind <> 'credit') OR\n(type = 'sales_tax_payment' AND NEW.kind <> 'sales_tax_payment')))\nBEGIN SELECT RAISE(ABORT, 'document line kind does not match transaction type'); END",
)
CHANGED = ('transactions', 'document_lines')
# The one trigger this revision deliberately rewrites, verified against the exact text co0028
# froze. Anything else stored on the rebuilt tables is a local guard whose meaning this
# migration cannot know, and it stops rather than guessing.
REPLACED = ('document_lines_type_insert',)
# The exact co0028 text this migration expects, and what it becomes. A sales tax payment is a
# ninth document type and its single remitted line is an eighth envelope kind.
REPLACEMENTS = {
    'transactions': ((
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo'))",
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo', 'sales_tax_payment'))"),),
    'document_lines': ((
        "kind IN ('sale', 'payment', 'deposit', 'purchase', 'bill_payment', 'credit')",
        "kind IN ('sale', 'payment', 'deposit', 'purchase', 'bill_payment', 'credit', 'sales_tax_payment')"),),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0030 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0030 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0030_' + table) + ' (' + ','.join(parts) + suffix
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
    for name in ('0026_bill_payments', '0028_customer_credits'):
        module = importlib.import_module('bookflow.storage.company_migrations.versions.' + name)
        for statement in module.GUARDS:
            known[statement.split()[2]] = statement
    return {name: known[name] for name in REPLACED if name in known}


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = set(OBJECTS) | {'_co0030_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0030 reserved object already exists')
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        attached = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + attached + '.sqlite_schema'):
            if row[0].casefold() in {value.casefold() for value in OBJECTS}:
                raise RuntimeError('co0030 remittance storage name collision')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join(f"'{name}'" for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        f'OR (type = \'index\' AND tbl_name IN ({changed}))) '
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    expected = _expected_guards()
    if set(REPLACED) - set(expected) or any(stored.get(name) != sql for name, sql in expected.items()):
        raise RuntimeError('co0030 unknown document type guard')
    # Keep unknown local objects verbatim; do not guess through competing guards.
    for name, sql in stored.items():
        if name in expected:
            continue
        if re.search(r'(?i)\b(?:NEW|OLD)\s*\.\s*["`\[]?type\b', sql) and re.search(r'(?i)\bON\s+["`\[]?transactions\b', sql):
            raise RuntimeError('co0030 unknown competing document type guard')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0030_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0030 rebuilt values differ: ' + table)
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
            raise RuntimeError('co0030 remittance storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0030 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
