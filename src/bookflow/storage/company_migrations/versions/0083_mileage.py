"""Mileage (R118): vehicles, mileage rates, trips, reimbursements, and a sixth customer-work kind.

Four new tables: ``vehicles`` (the Vehicle List), ``mileage_rates`` (a deduction rate per mile by
effective date), ``mileage_trips`` (trips driven; non-posting) and ``mileage_reimbursements`` (which
check paid somebody back for which trip).

A billable trip is billed through a one-line customer-work document of kind ``mileage``, as recorded
time is through kind ``time_activity``. So the customer-work family's enumerations admit it:

- ``work_documents`` admits the kind and its two states (recorded, voided);
- ``work_billing_conversions`` admits ``mileage_invoice`` and ``mileage_sales_receipt``;
- the guards one table away (``work_revision_kind``, ``work_billing_conversion_birth``) move with them.

SQLite cannot alter a CHECK in place, so the two tables are rebuilt with their constraints respelled
and every value, row identity, index, view and trigger carried across verbatim, exactly as co0077
did. A stored definition this migration does not recognize is somebody's local edit, and the
migration stops rather than guess. DDL below is frozen: this migration never imports current
application metadata.
"""
import importlib
from alembic import op

revision = 'co0083'
down_revision = 'co0079'
branch_labels = None
depends_on = None

DDL = (
    '\nCREATE TABLE vehicles (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tdescription VARCHAR(256), \n\tactive BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_vehicle_name_key UNIQUE (name_key), \n\tCONSTRAINT ck_vehicle_name_nonblank CHECK (length(trim(name)) > 0), \n\tCONSTRAINT ck_vehicle_active CHECK (active IN (0,1))\n)\n\n',
    '\nCREATE TABLE mileage_rates (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\trate VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_mileage_rate_date UNIQUE (effective_date), \n\tCONSTRAINT ck_mileage_rate_date CHECK (length(effective_date) = 10)\n)\n\n',
    "\nCREATE TABLE mileage_trips (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tvehicle_id VARCHAR(26) NOT NULL, \n\ttrip_start_date VARCHAR(10) NOT NULL, \n\ttrip_end_date VARCHAR(10) NOT NULL, \n\todometer_start_microunits BIGINT, \n\todometer_end_microunits BIGINT, \n\tmiles_microunits BIGINT NOT NULL, \n\tbillable BOOLEAN NOT NULL, \n\tcustomer_id VARCHAR(26), \n\titem_id VARCHAR(26), \n\tbilling_rate VARCHAR(32), \n\tclass_id VARCHAR(26), \n\tnotes TEXT, \n\tstatus VARCHAR(16) NOT NULL, \n\tvoid_reason VARCHAR(140), \n\twork_document_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_mileage_trip_dates CHECK (trip_start_date <= trip_end_date), \n\tCONSTRAINT ck_mileage_trip_miles CHECK (typeof(miles_microunits) = 'integer' AND miles_microunits > 0), \n\tCONSTRAINT ck_mileage_trip_odometer_pair CHECK ((odometer_start_microunits IS NULL) = (odometer_end_microunits IS NULL)), \n\tCONSTRAINT ck_mileage_trip_odometer CHECK (odometer_start_microunits IS NULL OR (odometer_start_microunits >= 0 AND odometer_end_microunits - odometer_start_microunits = miles_microunits)), \n\tCONSTRAINT ck_mileage_trip_billable CHECK (billable IN (0,1) AND (billable = 0 OR (customer_id IS NOT NULL AND item_id IS NOT NULL))), \n\tCONSTRAINT ck_mileage_trip_status CHECK (status IN ('recorded','voided')), \n\tFOREIGN KEY(vehicle_id) REFERENCES vehicles (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id), \n\tFOREIGN KEY(work_document_id) REFERENCES work_documents (id)\n)\n\n",
    "\nCREATE TABLE mileage_reimbursements (\n\tid VARCHAR(26) NOT NULL, \n\ttrip_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tmiles_microunits BIGINT NOT NULL, \n\trate VARCHAR(16) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_mileage_reimbursement_trip_check UNIQUE (trip_id, transaction_id), \n\tCONSTRAINT ck_mileage_reimbursement_amount CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tFOREIGN KEY(trip_id) REFERENCES mileage_trips (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id)\n)\n\n",
)
INDEXES = (
    'CREATE INDEX ix_mileage_trips_customer ON mileage_trips (customer_id, trip_end_date)',
    'CREATE INDEX ix_mileage_trips_vehicle ON mileage_trips (vehicle_id, trip_end_date)',
    'CREATE UNIQUE INDEX ux_mileage_trips_work_document ON mileage_trips (work_document_id) WHERE work_document_id IS NOT NULL',
    'CREATE INDEX ix_mileage_reimbursements_transaction ON mileage_reimbursements (transaction_id)',
)
NEW_OBJECTS = ('vehicles', 'mileage_rates', 'mileage_trips', 'mileage_reimbursements', 'ix_mileage_trips_customer',
               'ix_mileage_trips_vehicle', 'ux_mileage_trips_work_document', 'ix_mileage_reimbursements_transaction')

REPLACEMENTS = {
    'work_documents': (
        ("kind IN ('proposal','estimate','work_order','time_activity','sales_order')",
         "kind IN ('proposal','estimate','work_order','time_activity','sales_order','mileage')"),
        (" OR (kind = 'sales_order' AND status IN ('open','closed','voided'))",
         " OR (kind = 'sales_order' AND status IN ('open','closed','voided'))"
         " OR (kind = 'mileage' AND status IN ('recorded','voided'))"),
    ),
    'work_billing_conversions': (
        ("'time_activity_invoice','time_activity_sales_receipt','sales_order_invoice')",
         "'time_activity_invoice','time_activity_sales_receipt','sales_order_invoice','mileage_invoice',"
         "'mileage_sales_receipt')"),
    ),
}
CHANGED = tuple(REPLACEMENTS)

# The two guards as co0077 left them.
_KIND_BEFORE = (
    "CREATE TRIGGER work_revision_kind BEFORE INSERT ON work_revisions WHEN NOT EXISTS "
    "(SELECT 1 FROM work_documents d WHERE d.id = NEW.document_id AND ((d.kind IN "
    "('proposal','estimate') AND NEW.status IN ('draft','open','accepted','declined',"
    "'superseded','cancelled','voided')) OR (d.kind = 'work_order' AND NEW.status IN ('draft',"
    "'scheduled','in_progress','on_hold','complete','cancelled')) OR (d.kind = "
    "'time_activity' AND NEW.status IN ('recorded','voided')) OR (d.kind = 'sales_order' "
    "AND NEW.status IN ('open','closed','voided')))) BEGIN SELECT "
    "RAISE(ABORT, 'invalid work revision state'); END")
_BIRTH_BEFORE = (
    "CREATE TRIGGER work_billing_conversion_birth BEFORE INSERT ON work_billing_conversions\n"
    "WHEN NOT EXISTS (SELECT 1 FROM work_documents s JOIN transactions d ON d.id = NEW.destination_transaction_id\n"
    "JOIN transaction_revisions r ON r.transaction_id = d.id AND r.id = NEW.destination_revision_id\n"
    "WHERE s.id = NEW.source_document_id AND s.kind IN ('estimate','work_order','time_activity','sales_order')\n"
    "AND d.type = NEW.destination_type AND NEW.relation = s.kind || '_' || d.type AND r.revision_number = 1)\n"
    "BEGIN SELECT RAISE(ABORT, 'invalid work billing conversion lineage'); END")
TRIGGERS = {
    'work_revision_kind': (
        _KIND_BEFORE,
        _KIND_BEFORE.replace(
            "AND NEW.status IN ('open','closed','voided')))) BEGIN",
            "AND NEW.status IN ('open','closed','voided')) OR (d.kind = 'mileage' "
            "AND NEW.status IN ('recorded','voided')))) BEGIN")),
    'work_billing_conversion_birth': (
        _BIRTH_BEFORE,
        _BIRTH_BEFORE.replace("s.kind IN ('estimate','work_order','time_activity','sales_order')",
                              "s.kind IN ('estimate','work_order','time_activity','sales_order','mileage')")),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0083 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0083 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0083_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = {'_co0083_' + name for name in CHANGED} | set(NEW_OBJECTS)
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0083 reserved object already exists')
    stored = dict(connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_schema WHERE name IN (" + ','.join('?' * len(TRIGGERS)) + ")",
        tuple(TRIGGERS)).all())
    for name, (before, _) in TRIGGERS.items():
        if stored.get(name) != before:
            raise RuntimeError('co0083 unknown work guard: ' + name)
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name IN ('work_documents','work_billing_conversions'))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0083_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0083 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for _, name, statement in retained:
        connection.exec_driver_sql(TRIGGERS[name][1] if name in TRIGGERS else statement)
    for statement in DDL + INDEXES:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0083 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
