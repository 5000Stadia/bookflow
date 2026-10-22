"""Customer work gains a fifth kind: the sales order (R101, sales-orders design increment 2).

A sales order is stored as a customer-work document, so it needs no table of its own. What it
needs is for the family's enumerations to admit it:

- `work_documents` admits the kind and the three states it holds (open, closed, voided);
- `work_links` admits the conversion `estimate_sales_order`, and the partial unique index that
  keeps one work order per estimate widens to keep one operational destination per estimate,
  a work order or a sales order;
- `work_billing_conversions` admits `sales_order_invoice`, which the invoicing increment writes;
- the guards one table away (`work_revision_kind`, `work_billing_conversion_birth`,
  `work_link_relation`, and `work_identity_root`, which lets an order inherit its estimate's
  billing roots as a work order does) move with them.

Two things are appended rather than rebuilt: `work_lines.cancelled_quantity_microunits` (zero on
every existing line, nonzero only on sales-order and work-order lines, guarded by a new trigger)
and two company preferences, `sales_orders_enabled` and `available_deducts_sales_orders`, both on.

SQLite cannot alter a CHECK in place, so the three tables are rebuilt with their constraints
respelled and every value, row identity, index, view and trigger carried across verbatim, as
co0029 and co0055 did. A stored definition this migration does not recognize is somebody's local
edit, and the migration stops rather than guess. DDL below is frozen: this migration never
imports current application metadata.
"""
import importlib
from alembic import op

revision = 'co0077'
down_revision = 'co0075'
branch_labels = None
depends_on = None

REPLACEMENTS = {
    'work_documents': (
        ("kind IN ('proposal','estimate','work_order','time_activity')",
         "kind IN ('proposal','estimate','work_order','time_activity','sales_order')"),
        (" OR (kind = 'time_activity' AND status IN ('recorded','voided'))",
         " OR (kind = 'time_activity' AND status IN ('recorded','voided'))"
         " OR (kind = 'sales_order' AND status IN ('open','closed','voided'))"),
    ),
    'work_links': (
        ("relation IN ('proposal_estimate','estimate_work_order')",
         "relation IN ('proposal_estimate','estimate_work_order','estimate_sales_order')"),
    ),
    'work_billing_conversions': (
        ("'time_activity_invoice','time_activity_sales_receipt')",
         "'time_activity_invoice','time_activity_sales_receipt','sales_order_invoice')"),
    ),
}
CHANGED = tuple(REPLACEMENTS)

_KIND_BEFORE = (
    "CREATE TRIGGER work_revision_kind BEFORE INSERT ON work_revisions WHEN NOT EXISTS "
    "(SELECT 1 FROM work_documents d WHERE d.id = NEW.document_id AND ((d.kind IN "
    "('proposal','estimate') AND NEW.status IN ('draft','open','accepted','declined',"
    "'superseded','cancelled','voided')) OR (d.kind = 'work_order' AND NEW.status IN ('draft',"
    "'scheduled','in_progress','on_hold','complete','cancelled')) OR (d.kind = "
    "'time_activity' AND NEW.status IN ('recorded','voided')))) BEGIN SELECT "
    "RAISE(ABORT, 'invalid work revision state'); END")
_BIRTH_BEFORE = (
    "CREATE TRIGGER work_billing_conversion_birth BEFORE INSERT ON work_billing_conversions\n"
    "WHEN NOT EXISTS (SELECT 1 FROM work_documents s JOIN transactions d ON d.id = NEW.destination_transaction_id\n"
    "JOIN transaction_revisions r ON r.transaction_id = d.id AND r.id = NEW.destination_revision_id\n"
    "WHERE s.id = NEW.source_document_id AND s.kind IN ('estimate','work_order','time_activity')\n"
    "AND d.type = NEW.destination_type AND NEW.relation = s.kind || '_' || d.type AND r.revision_number = 1)\n"
    "BEGIN SELECT RAISE(ABORT, 'invalid work billing conversion lineage'); END")
_ACCEPTED = ("s.kind = 'estimate' AND s.status = 'accepted' AND d.kind = 'work_order' AND EXISTS "
             "(SELECT 1 FROM work_revisions a WHERE a.id = NEW.source_revision_id AND a.status = "
             "'accepted' AND a.accepted_revision_id IS NOT NULL)")
_LINK_BEFORE = (
    "CREATE TRIGGER work_link_relation BEFORE INSERT ON work_links WHEN NOT EXISTS (SELECT 1 FROM "
    "work_documents s JOIN work_documents d JOIN work_revisions r ON r.id = NEW.destination_revision_id "
    "WHERE s.id = NEW.source_document_id AND d.id = NEW.destination_document_id AND r.revision_number = 1 "
    "AND ((NEW.relation = 'copy' AND s.kind = d.kind AND (d.kind <> 'estimate' OR d.estimate_group_id "
    "IN (d.id,s.estimate_group_id))) OR (NEW.relation = 'proposal_estimate' AND s.kind = 'proposal' AND "
    "d.kind = 'estimate' AND d.estimate_group_id = s.id) OR (NEW.relation = 'estimate_work_order' AND "
    + _ACCEPTED + "))) BEGIN SELECT RAISE(ABORT, 'invalid work conversion lineage'); END")
_ROOT_BEFORE = (
    "CREATE TRIGGER work_identity_root BEFORE INSERT ON work_line_identities WHEN NOT ((NEW.root_document_id = "
    "NEW.document_id AND NEW.root_line_id = NEW.id) OR EXISTS (SELECT 1 FROM work_lines s JOIN work_line_identities "
    "i ON i.id = s.line_id JOIN work_documents d ON d.id = NEW.document_id JOIN work_documents o ON o.id = "
    "s.document_id WHERE s.id = NEW.source_line_id AND d.kind = 'work_order' AND o.kind = 'estimate' AND "
    "NEW.root_document_id = i.root_document_id AND NEW.root_line_id = i.root_line_id)) BEGIN SELECT "
    "RAISE(ABORT, 'invalid work billing root'); END")
_INDEX_BEFORE = ("CREATE UNIQUE INDEX uq_work_estimate_order ON work_links (source_document_id) "
                 "WHERE relation = 'estimate_work_order'")
TRIGGERS = {
    'work_revision_kind': (
        _KIND_BEFORE,
        _KIND_BEFORE.replace(
            "'time_activity' AND NEW.status IN ('recorded','voided')))) BEGIN",
            "'time_activity' AND NEW.status IN ('recorded','voided')) OR (d.kind = 'sales_order' "
            "AND NEW.status IN ('open','closed','voided')))) BEGIN")),
    'work_billing_conversion_birth': (
        _BIRTH_BEFORE,
        _BIRTH_BEFORE.replace("s.kind IN ('estimate','work_order','time_activity')",
                              "s.kind IN ('estimate','work_order','time_activity','sales_order')")),
    'work_link_relation': (
        _LINK_BEFORE,
        _LINK_BEFORE.replace(_ACCEPTED + "))) BEGIN", _ACCEPTED + ") OR (NEW.relation = "
                             "'estimate_sales_order' AND " + _ACCEPTED.replace("'work_order'", "'sales_order'")
                             + "))) BEGIN")),
    'work_identity_root': (
        _ROOT_BEFORE,
        _ROOT_BEFORE.replace("d.kind = 'work_order' AND o.kind = 'estimate'",
                             "d.kind IN ('work_order','sales_order') AND o.kind = 'estimate'")),
    'uq_work_estimate_order': (
        _INDEX_BEFORE,
        _INDEX_BEFORE.replace("WHERE relation = 'estimate_work_order'",
                              "WHERE relation IN ('estimate_work_order','estimate_sales_order')")),
}
ADDED = (
    "ALTER TABLE work_lines ADD COLUMN cancelled_quantity_microunits BIGINT DEFAULT '0' NOT NULL "
    "CONSTRAINT ck_work_cancelled CHECK (typeof(cancelled_quantity_microunits) = 'integer' AND "
    "cancelled_quantity_microunits >= 0 AND cancelled_quantity_microunits <= quantity_microunits)",
    "CREATE TRIGGER work_line_cancellation_kind BEFORE INSERT ON work_lines WHEN "
    "NEW.cancelled_quantity_microunits <> 0 AND EXISTS (SELECT 1 FROM work_documents d WHERE "
    "d.id = NEW.document_id AND d.kind NOT IN ('work_order','sales_order')) BEGIN SELECT "
    "RAISE(ABORT, 'cancellation belongs to sales orders and work orders'); END",
    "ALTER TABLE company_info ADD COLUMN sales_orders_enabled BOOLEAN DEFAULT '1' NOT NULL",
    "ALTER TABLE company_info ADD COLUMN available_deducts_sales_orders BOOLEAN DEFAULT '1' NOT NULL",
)


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0077 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0077 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0077_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = {'_co0077_' + name for name in CHANGED} | {'work_line_cancellation_kind'}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0077 reserved object already exists')
    stored = dict(connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_schema WHERE name IN (" + ','.join('?' * len(TRIGGERS)) + ")",
        tuple(TRIGGERS)).all())
    for name, (before, _) in TRIGGERS.items():
        if stored.get(name) != before:
            raise RuntimeError('co0077 unknown work guard: ' + name)
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    # Every local view and trigger, plus the indexes the rebuilt tables own: a trigger may name
    # a rebuilt table from anywhere, and an index on it disappears with it. All are recreated
    # verbatim, except the four guards above, which are recreated respelled.
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name IN ('work_documents','work_links','work_billing_conversions'))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0077_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0077 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for _, name, statement in retained:
        connection.exec_driver_sql(TRIGGERS[name][1] if name in TRIGGERS else statement)
    for statement in ADDED:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0077 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
