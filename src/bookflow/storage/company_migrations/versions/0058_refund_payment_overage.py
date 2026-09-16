"""Let a customer refund draw on a payment's unapplied overage, not only on a credit memo.

One table widened. ``customer_refund_consumptions`` gains a nullable payment-source column
pair beside its credit-source pair and drops the NOT NULL from the credit pair, so a refund
spends a receipt's standing capacity through exactly the rows it already spends a credit
memo's -- same releases, same immutability, same exact-party fence. This is the shape
``applications`` and ``application_allocations`` took in co0028 for the same reason.

Nothing is backfilled: NULL is the true value of both new columns for every consumption ever
written, and the kind of source is derived from which pair is present rather than stored.

SQLite cannot drop a NOT NULL, so the table is rebuilt. DDL below is frozen: this migration
never imports current application metadata.
"""
import re

from alembic import op

revision = 'co0058'
down_revision = 'co0057'
branch_labels = None
depends_on = None

# The one table this migration rebuilds, and the guards it owns from here on. The new
# payment-party fence is listed with the four it rewrites because the list answers "which
# revision is the authority on this object now", and from this revision that is co0058.
CHANGED = ('customer_refund_consumptions',)
REPLACED = ('customer_refund_consumptions_immutable_update',
            'customer_refund_consumptions_immutable_delete',
            'customer_refund_consumptions_transaction_id_type',
            'customer_refund_consumptions_exact_release',
            'customer_refund_consumptions_exact_party',
            'customer_refund_consumptions_exact_payment_party')

# Names this revision brings into existence; a database that already has one is not ours.
RESERVED = ('_co0058_customer_refund_consumptions',
            'customer_refund_consumptions_exact_payment_party',
            'ix_customer_refund_consumptions_payment_source',
            'ix_customer_refund_consumptions_payment_component')

# Every column the previous shape held, in its own order: what is copied, and what the
# comparison below reads back. The two new columns are absent from both by construction.
COPIED = ('id', 'kind', 'reverses_consumption_id', 'transaction_id', 'revision_id',
          'credit_source_key_id', 'credit_source_component_id', 'amount_minor_units',
          'currency', 'effective_date', 'created_at', 'created_by', 'created_via',
          'audit_event_id')

DDL = (
    "CREATE TABLE customer_refund_consumptions (\n\tid VARCHAR(26) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\treverses_consumption_id VARCHAR(26), \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tcredit_source_key_id VARCHAR(26), \n\tcredit_source_component_id VARCHAR(26), \n\tpayment_source_key_id VARCHAR(26), \n\tpayment_source_component_id VARCHAR(26), \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_customer_refund_release UNIQUE (reverses_consumption_id), \n\tCONSTRAINT fk_customer_refund_consumption_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES customer_refund_profiles (transaction_id, revision_id), \n\tCONSTRAINT ck_customer_refund_consumption_kind CHECK ((kind = 'consume' AND reverses_consumption_id IS NULL) OR (kind = 'release' AND reverses_consumption_id IS NOT NULL AND reverses_consumption_id <> id)), \n\tCONSTRAINT ck_customer_refund_consumption_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_customer_refund_consumption_one_source CHECK ((credit_source_key_id IS NOT NULL) + (payment_source_key_id IS NOT NULL) = 1 AND (credit_source_key_id IS NULL) = (credit_source_component_id IS NULL) AND (payment_source_key_id IS NULL) = (payment_source_component_id IS NULL)), \n\tFOREIGN KEY(reverses_consumption_id) REFERENCES customer_refund_consumptions (id), \n\tFOREIGN KEY(credit_source_key_id) REFERENCES credit_source_keys (id), \n\tFOREIGN KEY(credit_source_component_id) REFERENCES credit_components (id), \n\tFOREIGN KEY(payment_source_key_id) REFERENCES payment_component_keys (id), \n\tFOREIGN KEY(payment_source_component_id) REFERENCES payment_components (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)",
    'CREATE INDEX ix_customer_refund_consumptions_component ON customer_refund_consumptions (credit_source_component_id, id)',
    'CREATE INDEX ix_customer_refund_consumptions_payment_component ON customer_refund_consumptions (payment_source_component_id, id)',
    'CREATE INDEX ix_customer_refund_consumptions_payment_source ON customer_refund_consumptions (payment_source_key_id, id)',
    'CREATE INDEX ix_customer_refund_consumptions_source ON customer_refund_consumptions (credit_source_key_id, id)',
)

GUARDS = (
    "CREATE TRIGGER customer_refund_consumptions_immutable_update BEFORE UPDATE ON customer_refund_consumptions BEGIN SELECT RAISE(ABORT, 'immutable refund history'); END",
    "CREATE TRIGGER customer_refund_consumptions_immutable_delete BEFORE DELETE ON customer_refund_consumptions BEGIN SELECT RAISE(ABORT, 'immutable refund history'); END",
    "CREATE TRIGGER customer_refund_consumptions_transaction_id_type BEFORE INSERT ON customer_refund_consumptions\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND type = 'customer_refund')\nBEGIN SELECT RAISE(ABORT, 'refund reference has wrong document type'); END",
    "CREATE TRIGGER customer_refund_consumptions_exact_release BEFORE INSERT ON customer_refund_consumptions\nWHEN NEW.kind = 'release' AND NOT EXISTS (SELECT 1 FROM customer_refund_consumptions a\nWHERE a.id = NEW.reverses_consumption_id AND a.kind = 'consume' AND a.transaction_id IS NEW.transaction_id AND a.revision_id IS NEW.revision_id AND a.credit_source_key_id IS NEW.credit_source_key_id AND a.credit_source_component_id IS NEW.credit_source_component_id AND a.payment_source_key_id IS NEW.payment_source_key_id AND a.payment_source_component_id IS NEW.payment_source_component_id AND a.amount_minor_units IS NEW.amount_minor_units AND a.currency IS NEW.currency AND a.effective_date IS NEW.effective_date)\nBEGIN SELECT RAISE(ABORT, 'release must exactly undo an original consumption'); END",
    "CREATE TRIGGER customer_refund_consumptions_exact_party BEFORE INSERT ON customer_refund_consumptions\nWHEN NEW.credit_source_key_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM customer_refund_profiles p\nJOIN credit_source_keys k ON k.id = NEW.credit_source_key_id\nJOIN credit_components c ON c.id = NEW.credit_source_component_id\nWHERE p.transaction_id = NEW.transaction_id AND p.revision_id = NEW.revision_id\nAND c.key_id = k.id AND c.transaction_id = k.transaction_id\nAND c.currency = NEW.currency AND k.currency = NEW.currency AND p.currency = NEW.currency\nAND p.party_id = k.party_id AND p.ar_account_id = k.ar_account_id)\nBEGIN SELECT RAISE(ABORT, 'refund and credit source ownership differ'); END",
    "CREATE TRIGGER customer_refund_consumptions_exact_payment_party BEFORE INSERT ON customer_refund_consumptions\nWHEN NEW.payment_source_key_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM customer_refund_profiles p\nJOIN payment_component_keys k ON k.id = NEW.payment_source_key_id\nJOIN payment_components pc ON pc.id = NEW.payment_source_component_id\nWHERE p.transaction_id = NEW.transaction_id AND p.revision_id = NEW.revision_id\nAND pc.component_key_id = k.id AND pc.transaction_id = k.transaction_id\nAND pc.currency = NEW.currency AND k.currency = NEW.currency AND p.currency = NEW.currency\nAND p.party_id = k.party_id AND p.ar_account_id = k.ar_account_id)\nBEGIN SELECT RAISE(ABORT, 'refund and payment source ownership differ'); END",
)

# What co0031 wrote. A stored trigger that differs is somebody else's rule wearing our name,
# and this migration refuses rather than replacing it.
PRIOR_GUARDS = (
    "CREATE TRIGGER customer_refund_consumptions_immutable_update BEFORE UPDATE ON customer_refund_consumptions BEGIN SELECT RAISE(ABORT, 'immutable refund history'); END",
    "CREATE TRIGGER customer_refund_consumptions_immutable_delete BEFORE DELETE ON customer_refund_consumptions BEGIN SELECT RAISE(ABORT, 'immutable refund history'); END",
    "CREATE TRIGGER customer_refund_consumptions_transaction_id_type BEFORE INSERT ON customer_refund_consumptions\nWHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND type = 'customer_refund')\nBEGIN SELECT RAISE(ABORT, 'refund reference has wrong document type'); END",
    "CREATE TRIGGER customer_refund_consumptions_exact_release BEFORE INSERT ON customer_refund_consumptions\nWHEN NEW.kind = 'release' AND NOT EXISTS (SELECT 1 FROM customer_refund_consumptions a\nWHERE a.id = NEW.reverses_consumption_id AND a.kind = 'consume' AND a.transaction_id IS NEW.transaction_id AND a.revision_id IS NEW.revision_id AND a.credit_source_key_id IS NEW.credit_source_key_id AND a.credit_source_component_id IS NEW.credit_source_component_id AND a.amount_minor_units IS NEW.amount_minor_units AND a.currency IS NEW.currency AND a.effective_date IS NEW.effective_date)\nBEGIN SELECT RAISE(ABORT, 'release must exactly undo an original consumption'); END",
    "CREATE TRIGGER customer_refund_consumptions_exact_party BEFORE INSERT ON customer_refund_consumptions\nWHEN NOT EXISTS (SELECT 1 FROM customer_refund_profiles p\nJOIN credit_source_keys k ON k.id = NEW.credit_source_key_id\nJOIN credit_components c ON c.id = NEW.credit_source_component_id\nWHERE p.transaction_id = NEW.transaction_id AND p.revision_id = NEW.revision_id\nAND c.key_id = k.id AND c.transaction_id = k.transaction_id\nAND c.currency = NEW.currency AND k.currency = NEW.currency AND p.currency = NEW.currency\nAND p.party_id = k.party_id AND p.ar_account_id = k.ar_account_id)\nBEGIN SELECT RAISE(ABORT, 'refund and credit source ownership differ'); END",
)


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def upgrade():
    connection = op.get_bind()
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if set(RESERVED).intersection(existing):
        raise RuntimeError('co0058 reserved object already exists')
    stored = {name: sql for name, sql in connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_schema WHERE type = 'trigger' AND sql IS NOT NULL"
        " AND tbl_name = 'customer_refund_consumptions'").all()}
    expected = {statement.split()[2]: statement for statement in PRIOR_GUARDS}
    if any(stored.get(name) != sql for name, sql in expected.items()):
        raise RuntimeError('co0058 unknown customer refund consumption guard')
    # Keep unknown local objects on this table verbatim; do not guess through a rule we did
    # not write. They are recreated after the rebuild, exactly as they were.
    local = connection.exec_driver_sql(
        "SELECT type, name, sql FROM sqlite_schema WHERE sql IS NOT NULL"
        " AND tbl_name = 'customer_refund_consumptions' AND type IN ('index', 'trigger')"
        " ORDER BY CASE type WHEN 'index' THEN 0 ELSE 1 END, name").all()
    mine = set(expected) | {statement.split()[2] for statement in GUARDS} | {
        'ix_customer_refund_consumptions_source', 'ix_customer_refund_consumptions_component'}
    for kind, name, sql in local:
        if name in mine:
            continue
        if re.search(r'(?i)\bON\s+["`\[]?customer_refund_consumptions\b', sql) and kind == 'trigger':
            raise RuntimeError('co0058 unknown competing refund consumption guard')
    for kind, name, _ in local:
        if name in mine:
            connection.exec_driver_sql('DROP ' + kind.upper() + ' main.' + _quote(name))
    # Objects that merely *name* this table from elsewhere -- co0053's `credit_deletions_owner`
    # reads it to refuse deleting a credit a refund still stands on. SQLite re-parses every
    # trigger and view during `ALTER TABLE ... RENAME TO`, and one naming a table that is
    # momentarily absent aborts the rename, so they come down for the rebuild and go back
    # verbatim afterwards. This is what co0028 does wholesale; here it is narrowed to the
    # objects that actually reference the table being rebuilt.
    foreign = [row for row in connection.exec_driver_sql(
        "SELECT type, name, sql FROM sqlite_schema WHERE sql IS NOT NULL"
        " AND type IN ('trigger', 'view') AND tbl_name <> 'customer_refund_consumptions'"
        " ORDER BY CASE type WHEN 'view' THEN 0 ELSE 1 END, name").all()
        if re.search(r'(?i)\bcustomer_refund_consumptions\b', row[2])]
    for kind, name, _ in reversed(foreign):
        connection.exec_driver_sql('DROP ' + kind.upper() + ' main.' + _quote(name))
    columns = ', '.join(COPIED)
    temporary = '_co0058_customer_refund_consumptions'
    connection.exec_driver_sql(DDL[0].replace('CREATE TABLE customer_refund_consumptions (',
                                              'CREATE TABLE ' + _quote(temporary) + ' (', 1))
    connection.exec_driver_sql('INSERT INTO ' + _quote(temporary) + ' (' + columns + ')'
                               ' SELECT ' + columns + ' FROM customer_refund_consumptions')
    for left, right in (('customer_refund_consumptions', temporary),
                        (temporary, 'customer_refund_consumptions')):
        if connection.exec_driver_sql(
                'SELECT ' + columns + ' FROM ' + _quote(left) + ' EXCEPT SELECT ' + columns
                + ' FROM ' + _quote(right)).fetchone() is not None:
            raise RuntimeError('co0058 rebuilt values differ')
    connection.exec_driver_sql('DROP TABLE customer_refund_consumptions')
    connection.exec_driver_sql('ALTER TABLE ' + _quote(temporary)
                               + ' RENAME TO customer_refund_consumptions')
    for statement in DDL[1:]:
        connection.exec_driver_sql(statement)
    for statement in GUARDS:
        connection.exec_driver_sql(statement)
    for kind, name, sql in local:
        if name not in mine:
            connection.exec_driver_sql(sql)
    for _, _, sql in foreign:
        connection.exec_driver_sql(sql)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0058 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
