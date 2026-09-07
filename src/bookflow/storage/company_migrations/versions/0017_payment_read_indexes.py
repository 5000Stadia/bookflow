"""Additive bounded payment read indexes; frozen co0016 predecessor contract."""
import sqlite3
from alembic import op

revision = 'co0017'
down_revision = 'co0016'
branch_labels = None
depends_on = None

DDL = (
    'CREATE INDEX ix_co17_transactions_current ON transactions (current_revision_id, type, status, id, version, number);',
    'CREATE INDEX ix_co17_transactions_type ON transactions (type, status, current_revision_id, id, version, number);',
    'CREATE INDEX ix_co17_revisions_read ON transaction_revisions (id, date, currency, total_minor_units, memo);',
    'CREATE INDEX ix_co17_sales_party ON sales_profiles (customer_id, control_account_id, revision_id);',
    'CREATE INDEX ix_co17_sales_revision ON sales_profiles (revision_id, customer_id, control_account_id, due_date);',
    'CREATE INDEX ix_co17_applications_invoice ON applications (paid_transaction_id, kind, amount_minor_units, id);',
    'CREATE INDEX ix_co17_applications_payment ON applications (paying_transaction_id, kind, amount_minor_units, id);',
    'CREATE INDEX ix_co17_posting_party_ar ON posting_lines (name_type, name_id, account_id, debit_minor_units, credit_minor_units);',
    'CREATE INDEX ix_co17_operations_history ON payment_operations (request_snapshot, id, audit_event_id, operation_key, command);',
    "CREATE INDEX ix_co17_payment_profile ON payment_profiles (revision_id, payer_id, payment_method_id, reference, coalesce(json_extract(profile_snapshot, '$.payer.label'), ''));",
)
REQUIRED = {'transactions': {'current_revision_id': 'TEXT', 'type': 'TEXT', 'status': 'TEXT', 'id': 'TEXT', 'version': 'INTEGER', 'number': 'TEXT'}, 'transaction_revisions': {'id': 'TEXT', 'date': 'TEXT', 'currency': 'TEXT', 'total_minor_units': 'INTEGER', 'memo': 'TEXT'}, 'sales_profiles': {'customer_id': 'TEXT', 'control_account_id': 'TEXT', 'revision_id': 'TEXT', 'due_date': 'TEXT'}, 'applications': {'paid_transaction_id': 'TEXT', 'kind': 'TEXT', 'amount_minor_units': 'INTEGER', 'id': 'TEXT', 'paying_transaction_id': 'TEXT'}, 'posting_lines': {'name_type': 'TEXT', 'name_id': 'TEXT', 'account_id': 'TEXT', 'debit_minor_units': 'INTEGER', 'credit_minor_units': 'INTEGER'}, 'payment_operations': {'request_snapshot': 'TEXT', 'id': 'TEXT', 'audit_event_id': 'TEXT', 'operation_key': 'TEXT', 'command': 'TEXT'}, 'payment_profiles': {'revision_id': 'TEXT', 'payer_id': 'TEXT', 'payment_method_id': 'TEXT', 'reference': 'TEXT', 'profile_snapshot': 'TEXT'}}


def _affinity(declaration):
    kind = declaration.upper()
    if 'INT' in kind:
        return 'INTEGER'
    if any(token in kind for token in ('CHAR', 'CLOB', 'TEXT')):
        return 'TEXT'
    return 'OTHER'


def _preflight(connection):
    # Check every reservation before the first persistent DDL statement.
    for sql in DDL:
        name = sql.split()[2]
        if connection.exec_driver_sql(
                'SELECT 1 FROM sqlite_schema WHERE lower(name)=lower(?)', (name,)).first():
            raise RuntimeError('co0017 reserved index name already exists: ' + name)
    # SQLite itself resolves expression compatibility and inherited collations
    # on an empty, private in-memory schema. No application data or statistics
    # are copied, and the company schema is never modified by this preflight.
    probe = sqlite3.connect(':memory:')
    try:
        for table, required in REQUIRED.items():
            row = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).first()
            if not row or not row[0]:
                raise RuntimeError('co0017 missing managed table: ' + table)
            columns = {r[1]: r for r in connection.exec_driver_sql(f'PRAGMA table_xinfo("{table}")')}
            for column, affinity in required.items():
                if column not in columns or _affinity(columns[column][2]) != affinity or columns[column][6]:
                    raise RuntimeError('co0017 incompatible managed column: ' + table + '.' + column)
            probe.execute(row[0])
        for sql in DDL:
            probe.execute(sql)
            name = sql.split()[2]
            if any(r[4] != 'BINARY' or r[3] != 0 for r in probe.execute(f'PRAGMA index_xinfo("{name}")') if r[5]):
                raise RuntimeError('co0017 incompatible index collation: ' + name)
    finally:
        probe.close()


def upgrade():
    connection = op.get_bind()
    _preflight(connection)
    for sql in DDL:
        connection.exec_driver_sql(sql)


def downgrade():
    raise NotImplementedError
