"""Customer refunds: a tenth document type, its header, and the credit capacity it spends.

Two new tables and two widened CHECK constraints. ``customer_refund_profiles`` is the
one-to-one header of a refund revision and ``customer_refund_consumptions`` is what stops one
credit being spent twice; ``transactions`` gains ``customer_refund`` as a document type and
``document_lines`` gains ``refund`` as an envelope kind, both of which live in a CHECK, and
SQLite only widens a CHECK by rebuilding the table. The document-type guard on
``document_lines`` is rewritten for the same reason and is the only trigger this revision
replaces.

Nothing is backfilled. A company upgraded here owes and is owed exactly what it was before:
the two new tables are empty, and a credit memo written before this revision is worth what it
was worth, because available capacity is capacity less applications less consumptions and
there are no consumptions.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0031'
down_revision = 'co0030'
branch_labels = None
depends_on = None

NEW_TABLES = ('customer_refund_profiles', 'customer_refund_consumptions')
OBJECTS = ('customer_refund_consumptions', 'customer_refund_consumptions_exact_party', 'customer_refund_consumptions_exact_release', 'customer_refund_consumptions_immutable_delete', 'customer_refund_consumptions_immutable_update', 'customer_refund_consumptions_transaction_id_type', 'customer_refund_profiles', 'customer_refund_profiles_immutable_delete', 'customer_refund_profiles_immutable_update', 'customer_refund_profiles_owned_attribution', 'ix_customer_refund_consumptions_component', 'ix_customer_refund_consumptions_source', 'ix_customer_refund_profiles_attribution', 'ix_customer_refund_profiles_party')
DDL = (
    "CREATE TABLE customer_refund_profiles (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tparty_id VARCHAR(26) NOT NULL, \n\tar_account_id VARCHAR(26) NOT NULL, \n\tfunding_account_id VARCHAR(26) NOT NULL, \n\tpayment_method_id VARCHAR(26) NOT NULL, \n\tcheck_number VARCHAR(64), \n\treference VARCHAR(128), \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tar_posting_source_id VARCHAR(26) NOT NULL, \n\tprofile_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT uq_customer_refund_profile_owner UNIQUE (transaction_id, revision_id), \n\tCONSTRAINT fk_customer_refund_profile_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT fk_customer_refund_profile_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tCONSTRAINT fk_customer_refund_profile_attribution FOREIGN KEY(transaction_id, ar_posting_source_id) REFERENCES posting_line_sources (transaction_id, id), \n\tCONSTRAINT ck_customer_refund_profile_type CHECK (type = 'customer_refund'), \n\tCONSTRAINT ck_customer_refund_amount_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_customer_refund_profile_snapshot_object CHECK (json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(party_id) REFERENCES customers (id), \n\tFOREIGN KEY(ar_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(funding_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(payment_method_id) REFERENCES payment_methods (id)\n)",
    "CREATE TABLE customer_refund_consumptions (\n\tid VARCHAR(26) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\treverses_consumption_id VARCHAR(26), \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tcredit_source_key_id VARCHAR(26) NOT NULL, \n\tcredit_source_component_id VARCHAR(26) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_customer_refund_release UNIQUE (reverses_consumption_id), \n\tCONSTRAINT fk_customer_refund_consumption_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES customer_refund_profiles (transaction_id, revision_id), \n\tCONSTRAINT ck_customer_refund_consumption_kind CHECK ((kind = 'consume' AND reverses_consumption_id IS NULL) OR (kind = 'release' AND reverses_consumption_id IS NOT NULL AND reverses_consumption_id <> id)), \n\tCONSTRAINT ck_customer_refund_consumption_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tFOREIGN KEY(reverses_consumption_id) REFERENCES customer_refund_consumptions (id), \n\tFOREIGN KEY(credit_source_key_id) REFERENCES credit_source_keys (id), \n\tFOREIGN KEY(credit_source_component_id) REFERENCES credit_components (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    'CREATE INDEX ix_customer_refund_consumptions_component ON customer_refund_consumptions (credit_source_component_id, id)',
    'CREATE INDEX ix_customer_refund_consumptions_source ON customer_refund_consumptions (credit_source_key_id, id)',
    'CREATE INDEX ix_customer_refund_profiles_attribution ON customer_refund_profiles (ar_posting_source_id)',
    'CREATE INDEX ix_customer_refund_profiles_party ON customer_refund_profiles (party_id, ar_account_id, transaction_id)',
)
GUARDS = (
    "CREATE TRIGGER customer_refund_profiles_immutable_update BEFORE UPDATE ON customer_refund_profiles BEGIN SELECT RAISE(ABORT, 'immutable refund history'); END",
    "CREATE TRIGGER customer_refund_profiles_immutable_delete BEFORE DELETE ON customer_refund_profiles BEGIN SELECT RAISE(ABORT, 'immutable refund history'); END",
    "CREATE TRIGGER customer_refund_consumptions_immutable_update BEFORE UPDATE ON customer_refund_consumptions BEGIN SELECT RAISE(ABORT, 'immutable refund history'); END",
    "CREATE TRIGGER customer_refund_consumptions_immutable_delete BEFORE DELETE ON customer_refund_consumptions BEGIN SELECT RAISE(ABORT, 'immutable refund history'); END",
    "CREATE TRIGGER customer_refund_consumptions_transaction_id_type BEFORE INSERT ON customer_refund_consumptions\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND type = 'customer_refund')\nBEGIN SELECT RAISE(ABORT, 'refund reference has wrong document type'); END",
    "CREATE TRIGGER customer_refund_consumptions_exact_release BEFORE INSERT ON customer_refund_consumptions\nWHEN NEW.kind = 'release' AND NOT EXISTS (SELECT 1 FROM customer_refund_consumptions a\nWHERE a.id = NEW.reverses_consumption_id AND a.kind = 'consume' AND a.transaction_id IS NEW.transaction_id AND a.revision_id IS NEW.revision_id AND a.credit_source_key_id IS NEW.credit_source_key_id AND a.credit_source_component_id IS NEW.credit_source_component_id AND a.amount_minor_units IS NEW.amount_minor_units AND a.currency IS NEW.currency AND a.effective_date IS NEW.effective_date)\nBEGIN SELECT RAISE(ABORT, 'release must exactly undo an original consumption'); END",
    "CREATE TRIGGER customer_refund_consumptions_exact_party BEFORE INSERT ON customer_refund_consumptions\nWHEN NOT EXISTS (SELECT 1 FROM customer_refund_profiles p\nJOIN credit_source_keys k ON k.id = NEW.credit_source_key_id\nJOIN credit_components c ON c.id = NEW.credit_source_component_id\nWHERE p.transaction_id = NEW.transaction_id AND p.revision_id = NEW.revision_id\nAND c.key_id = k.id AND c.transaction_id = k.transaction_id\nAND c.currency = NEW.currency AND k.currency = NEW.currency AND p.currency = NEW.currency\nAND p.party_id = k.party_id AND p.ar_account_id = k.ar_account_id)\nBEGIN SELECT RAISE(ABORT, 'refund and credit source ownership differ'); END",
    "CREATE TRIGGER customer_refund_profiles_owned_attribution BEFORE INSERT ON customer_refund_profiles\nWHEN NOT EXISTS (SELECT 1 FROM posting_line_sources ps\nJOIN posting_lines pl ON pl.id = ps.posting_line_id\nWHERE ps.transaction_id = NEW.transaction_id AND ps.id = NEW.ar_posting_source_id\nAND ps.revision_id = NEW.revision_id AND ps.reversed_source_id IS NULL\nAND ps.amount_minor_units = NEW.amount_minor_units\nAND pl.account_id = NEW.ar_account_id AND pl.debit_minor_units > 0\nAND pl.name_type = 'customer' AND pl.name_id = NEW.party_id)\nBEGIN SELECT RAISE(ABORT, 'refund attribution is not an owned receivable debit'); END",
    "CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines\nWHEN EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND\n((type = 'journal_entry' AND NEW.kind <> 'journal') OR\n(type IN ('invoice', 'sales_receipt') AND NEW.kind <> 'sale') OR\n(type = 'payment' AND NEW.kind <> 'payment') OR\n(type = 'deposit' AND NEW.kind <> 'deposit') OR\n(type = 'bill' AND NEW.kind <> 'purchase') OR\n(type = 'bill_payment' AND NEW.kind <> 'bill_payment') OR\n(type = 'credit_memo' AND NEW.kind <> 'credit') OR\n(type = 'sales_tax_payment' AND NEW.kind <> 'sales_tax_payment') OR\n(type = 'customer_refund' AND NEW.kind <> 'refund')))\nBEGIN SELECT RAISE(ABORT, 'document line kind does not match transaction type'); END",
)


CHANGED = ('transactions', 'document_lines')
# The one trigger this revision deliberately rewrites, verified against the exact text co0030
# froze. Anything else stored on the rebuilt tables is a local guard whose meaning this
# migration cannot know, and it stops rather than guessing.
REPLACED = ('document_lines_type_insert',)
# The exact co0030 text this migration expects, and what it becomes. A customer refund is a
# tenth document type and its single refunded line is a ninth envelope kind.
REPLACEMENTS = {
    'transactions': ((
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo', 'sales_tax_payment'))",
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo', 'sales_tax_payment', 'customer_refund'))"),),
    'document_lines': ((
        "kind IN ('sale', 'payment', 'deposit', 'purchase', 'bill_payment', 'credit', 'sales_tax_payment')",
        "kind IN ('sale', 'payment', 'deposit', 'purchase', 'bill_payment', 'credit', 'sales_tax_payment', 'refund')"),),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0031 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0031 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0031_' + table) + ' (' + ','.join(parts) + suffix
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
    for name in ('0028_customer_credits', '0030_sales_tax_payments'):
        module = importlib.import_module('bookflow.storage.company_migrations.versions.' + name)
        for statement in module.GUARDS:
            known[statement.split()[2]] = statement
    return {name: known[name] for name in REPLACED if name in known}


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = set(OBJECTS) | {'_co0031_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0031 reserved object already exists')
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        attached = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + attached + '.sqlite_schema'):
            if row[0].casefold() in {value.casefold() for value in OBJECTS}:
                raise RuntimeError('co0031 refund storage name collision')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join(f"'{name}'" for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        f'OR (type = \'index\' AND tbl_name IN ({changed}))) '
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    expected = _expected_guards()
    if set(REPLACED) - set(expected) or any(stored.get(name) != sql for name, sql in expected.items()):
        raise RuntimeError('co0031 unknown document type guard')
    # Keep unknown local objects verbatim; do not guess through competing guards.
    for name, sql in stored.items():
        if name in expected:
            continue
        if re.search(r'(?i)\b(?:NEW|OLD)\s*\.\s*["`\[]?type\b', sql) and re.search(r'(?i)\bON\s+["`\[]?transactions\b', sql):
            raise RuntimeError('co0031 unknown competing document type guard')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0031_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0031 rebuilt values differ: ' + table)
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
            raise RuntimeError('co0031 refund storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0031 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
