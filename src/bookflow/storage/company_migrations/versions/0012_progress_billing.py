"""Preserve co0011 stored tables while adding exact progress entitlement guards.

All SQL contracts are frozen here. No application metadata or arithmetic is used.
The runner supplies one transaction and disables foreign keys during both rebuilds.
"""
import re

from alembic import op

revision = 'co0012'
down_revision = 'co0011'
branch_labels = None
depends_on = None

KNOWN_GUARDS = {'work_billing_allocation_active': "CREATE TRIGGER work_billing_allocation_active BEFORE INSERT ON work_billing_allocations\nWHEN EXISTS (SELECT 1 FROM work_billing_allocations a JOIN transactions t\nON t.id = a.transaction_id AND t.current_revision_id = a.revision_id AND t.status = 'posted'\nWHERE a.root_document_id = NEW.root_document_id AND a.root_line_id = NEW.root_line_id\nAND a.transaction_id <> NEW.transaction_id)\nBEGIN SELECT RAISE(ABORT, 'work billing root already consumed'); END", 'work_billing_transaction_insert': "CREATE TRIGGER work_billing_transaction_insert BEFORE INSERT ON transactions\nWHEN NEW.status = 'posted' AND EXISTS (\nSELECT 1 FROM work_billing_allocations pending JOIN work_billing_allocations active\nON active.root_document_id = pending.root_document_id AND active.root_line_id = pending.root_line_id\nJOIN transactions t ON t.id = active.transaction_id AND t.current_revision_id = active.revision_id AND t.status = 'posted'\nWHERE pending.transaction_id = NEW.id AND pending.revision_id = NEW.current_revision_id AND active.transaction_id <> NEW.id)\nBEGIN SELECT RAISE(ABORT, 'work billing root already consumed'); END", 'work_billing_transaction_update': "CREATE TRIGGER work_billing_transaction_update BEFORE UPDATE OF current_revision_id, status ON transactions\nWHEN NEW.status = 'posted' AND EXISTS (\nSELECT 1 FROM work_billing_allocations pending JOIN work_billing_allocations active\nON active.root_document_id = pending.root_document_id AND active.root_line_id = pending.root_line_id\nJOIN transactions t ON t.id = active.transaction_id AND t.current_revision_id = active.revision_id AND t.status = 'posted'\nWHERE pending.transaction_id = NEW.id AND pending.revision_id = NEW.current_revision_id AND active.transaction_id <> NEW.id)\nBEGIN SELECT RAISE(ABORT, 'work billing root already consumed'); END"}

SALES_PRICE = "(pricing_basis = 'unit' AND typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0) OR (pricing_basis = 'amount' AND unit_price_minor_units IS NULL) OR (pricing_basis = 'allocated' AND (unit_price_minor_units IS NULL OR (typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0)))"
ALLOCATION_QUANTITY = "(typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0) OR (allocation_version = 2 AND quantity_microunits IS NULL)"
ALLOCATION_VERSION = "typeof(allocation_version) = 'integer' AND allocation_version IN (1,2)"
ALLOCATION_PROOF = "(allocation_version = 1 AND source_basis_hash IS NULL AND denominator_hex IS NULL AND spans_json IS NULL) OR (allocation_version = 2 AND typeof(source_basis_hash) = 'text' AND length(source_basis_hash) = 64 AND length(CAST(source_basis_hash AS BLOB)) = 64 AND source_basis_hash NOT GLOB '*[^0-9a-f]*' AND typeof(denominator_hex) = 'text' AND length(denominator_hex) = 40 AND length(CAST(denominator_hex AS BLOB)) = 40 AND denominator_hex NOT GLOB '*[^0-9a-f]*' AND denominator_hex > '0000000000000000000000000000000000000000' COLLATE BINARY AND typeof(spans_json) = 'text' AND CASE WHEN json_valid(spans_json) THEN json_type(spans_json) = 'array' AND json_array_length(spans_json) BETWEEN 1 AND 200 ELSE 0 END)"

# Proof shape is checked in ordered statements: malformed JSON never reaches a
# JSON traversal, and scalar JSON elements never reach json_extract as documents.
SHAPE_GUARD = """CREATE TRIGGER work_billing_allocation_shape
BEFORE INSERT ON work_billing_allocations WHEN NEW.allocation_version = 2
BEGIN
 SELECT CASE WHEN typeof(NEW.spans_json) <> 'text' OR NOT json_valid(NEW.spans_json)
 THEN RAISE(ABORT, 'invalid work billing spans') END;
 SELECT CASE WHEN json_type(NEW.spans_json) <> 'array'
 OR json_array_length(NEW.spans_json) NOT BETWEEN 1 AND 200
 THEN RAISE(ABORT, 'invalid work billing spans') END;
 SELECT CASE WHEN EXISTS (
 SELECT 1 FROM json_each(NEW.spans_json) s WHERE CASE
 WHEN s.type <> 'array' THEN 1
 WHEN json_array_length(s.value) <> 2 THEN 1
 ELSE NOT coalesce(
 json_type(s.value, '$[0]') = 'text' AND json_type(s.value, '$[1]') = 'text'
 AND length(CAST(json_extract(s.value, '$[0]') AS BLOB)) = 40
 AND length(CAST(json_extract(s.value, '$[1]') AS BLOB)) = 40
 AND length(json_extract(s.value, '$[0]')) = 40
 AND length(json_extract(s.value, '$[1]')) = 40
 AND json_extract(s.value, '$[0]') NOT GLOB '*[^0-9a-f]*'
 AND json_extract(s.value, '$[1]') NOT GLOB '*[^0-9a-f]*'
 AND json_extract(s.value, '$[0]') COLLATE BINARY < json_extract(s.value, '$[1]')
 AND json_extract(s.value, '$[1]') COLLATE BINARY <= NEW.denominator_hex, 0)
 END) THEN RAISE(ABORT, 'invalid work billing spans') END;
 SELECT CASE WHEN EXISTS (
 SELECT 1 FROM json_each(NEW.spans_json) p JOIN json_each(NEW.spans_json) n ON n.key = p.key + 1
 WHERE json_extract(p.value, '$[1]') COLLATE BINARY >= json_extract(n.value, '$[0]'))
 THEN RAISE(ABORT, 'noncanonical work billing spans') END;
END"""


def _conflict(left, right):
    # A v1 allocation occupies the full root. All active v2 owners must agree on
    # the captured basis even when their intervals do not intersect.
    return f"""({left}.allocation_version = 1 OR {right}.allocation_version = 1
 OR {left}.source_basis_hash COLLATE BINARY <> {right}.source_basis_hash
 OR {left}.denominator_hex COLLATE BINARY <> {right}.denominator_hex
 OR EXISTS (SELECT 1 FROM json_each({left}.spans_json) l
 CROSS JOIN json_each({right}.spans_json) r
 WHERE json_extract(l.value, '$[0]') COLLATE BINARY < json_extract(r.value, '$[1]')
 AND json_extract(r.value, '$[0]') COLLATE BINARY < json_extract(l.value, '$[1]')))"""


def _guards():
    # The shape guard is created last (SQLite currently executes it first), but
    # correctness does not depend on trigger order: sanitize traversal inputs in
    # the insert conflict predicate as well. Full shape validation is separate.
    conflict = _conflict('a', 'NEW')
    conflict = conflict.replace('json_each(NEW.spans_json)',
        "json_each(CASE WHEN json_valid(NEW.spans_json) THEN NEW.spans_json ELSE '[]' END)")
    conflict = conflict.replace("json_extract(r.value,", "json_extract(CASE WHEN r.type = 'array' THEN r.value ELSE '[]' END,")
    yield f"""CREATE TRIGGER work_billing_allocation_active BEFORE INSERT ON work_billing_allocations
WHEN EXISTS (SELECT 1 FROM work_billing_allocations a JOIN transactions t
ON t.id = a.transaction_id AND t.current_revision_id = a.revision_id AND t.status = 'posted'
WHERE a.root_document_id = NEW.root_document_id AND a.root_line_id = NEW.root_line_id
AND a.transaction_id <> NEW.transaction_id AND {conflict})
BEGIN SELECT RAISE(ABORT, 'work billing root already consumed or incompatible basis'); END"""
    for name, event in (('insert', 'INSERT'), ('update', 'UPDATE OF current_revision_id, status')):
        yield f"""CREATE TRIGGER work_billing_transaction_{name} BEFORE {event} ON transactions
WHEN NEW.status = 'posted' AND EXISTS (
SELECT 1 FROM work_billing_allocations pending JOIN work_billing_allocations active
ON active.root_document_id = pending.root_document_id AND active.root_line_id = pending.root_line_id
JOIN transactions t ON t.id = active.transaction_id AND t.current_revision_id = active.revision_id AND t.status = 'posted'
WHERE pending.transaction_id = NEW.id AND pending.revision_id = NEW.current_revision_id
AND active.transaction_id <> NEW.id AND {_conflict('active', 'pending')})
BEGIN SELECT RAISE(ABORT, 'work billing root already consumed or incompatible basis'); END"""
    yield SHAPE_GUARD


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _definitions(sql, table):
    """Split only top-level definitions, retaining their exact original spelling.

    Unrecognized headers/comments fail before DDL changes. Quotes in defaults,
    checks and generated expressions cannot impersonate a target definition.
    """
    header = re.match(r'CREATE TABLE\s+(?:"' + table + r'"|' + table + r')\s*\(', sql)
    if not header:
        raise RuntimeError('co0012 unknown table header: ' + table)
    depth, quote, start, parts = 1, None, header.end(), []
    i = start
    while i < len(sql):
        char = sql[i]
        if quote:
            if char == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote and quote != ']':
                    i += 2
                    continue
                quote = None
        elif sql[i:i + 2] in ('--', '/*'):
            raise RuntimeError('co0012 cannot safely parse commented table DDL')
        elif char in ('"', "'", '`', '['):
            quote = ']' if char == '[' else char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                parts.append(sql[start:i])
                return header, parts, sql[i:]
        elif char == ',' and depth == 1:
            parts.append(sql[start:i])
            start = i + 1
        i += 1
    raise RuntimeError('co0012 cannot safely parse table DDL')


def _rebuild(connection, table):
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    header, parts, suffix = _definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({_quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0012 cannot safely preserve custom row identity')
    replacements = {}
    quantity_names = ['quantity_microunits']
    if table == 'sales_line_profiles':
        quantity_names.append('base_quantity_microunits')
        old_price = "(pricing_basis = 'unit' AND typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0) OR (pricing_basis = 'amount' AND unit_price_minor_units IS NULL)"
        replacements[f'CONSTRAINT ck_sales_pricing_basis CHECK ({old_price})'] = f'CONSTRAINT ck_sales_pricing_basis CHECK ({SALES_PRICE})'
        for name in quantity_names:
            constraint = f'CONSTRAINT ck_sales_{name}_positive CHECK '
            replacements[constraint + f"(typeof({name}) = 'integer' AND {name} > 0)"] = constraint + f"((typeof({name}) = 'integer' AND {name} > 0) OR (pricing_basis = 'allocated' AND {name} IS NULL))"
        additions = []
    else:
        additions = ["\n allocation_version INTEGER DEFAULT '1' NOT NULL", '\n source_basis_hash VARCHAR(64)',
                     '\n denominator_hex VARCHAR(40)', '\n spans_json TEXT']
        if any(row[1] in ('allocation_version', 'source_basis_hash', 'denominator_hex', 'spans_json') for row in columns):
            raise RuntimeError('co0012 proof column already exists')
        constraint = 'CONSTRAINT ck_work_billing_quantity_microunits CHECK '
        replacements[constraint + "(typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0)"] = constraint + f'({ALLOCATION_QUANTITY})'
    for name in quantity_names:
        replacements[f'{name} BIGINT NOT NULL'] = f'{name} BIGINT'
    for old, new in replacements.items():
        found = [i for i, part in enumerate(parts) if part.strip() == old]
        if len(found) != 1:
            raise RuntimeError('co0012 unknown constraint or column: ' + old)
        i = found[0]
        parts[i] = parts[i].replace(old, new, 1)
    if table == 'work_billing_allocations':
        parts.extend([f'\n CONSTRAINT ck_work_billing_allocation_version CHECK ({ALLOCATION_VERSION})',
                      f'\n CONSTRAINT ck_work_billing_proof CHECK ({ALLOCATION_PROOF})'])
    create = 'CREATE TABLE ' + _quote('_co0012_' + table) + ' (' + ','.join(additions + parts) + suffix
    writable = ','.join(['rowid'] + [_quote(row[1]) for row in columns if row[6] == 0])
    # Quote plus storage class compares exact stored bytes, including blobs and
    # generated values; retaining rowid preserves ordering and row identity.
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({_quote(row[1])})', f'quote({_quote(row[1])})', f'CAST({_quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    plans = {table: _rebuild(connection, table) for table in ('sales_line_profiles', 'work_billing_allocations')}
    retained = connection.exec_driver_sql(
        "SELECT type, name, sql FROM sqlite_schema WHERE sql IS NOT NULL AND ("
        "type IN ('view', 'trigger') OR (type = 'index' AND tbl_name IN "
        "('sales_line_profiles', 'work_billing_allocations'))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name"
    ).all()
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    if any(stored.get(name) != sql for name, sql in KNOWN_GUARDS.items()):
        raise RuntimeError('co0012 unknown work billing root guard')
    if 'work_billing_allocation_shape' in stored:
        raise RuntimeError('co0012 proof guard already exists')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{_quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0012_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {_quote(temporary)} ({writable}) SELECT {writable} FROM {_quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {_quote(left)} EXCEPT SELECT {selected} FROM {_quote(right)}').fetchone() is not None:
                raise RuntimeError('co0012 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {_quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {_quote(temporary)} RENAME TO {_quote(table)}')
    for kind, name, statement in retained:
        if kind != 'trigger' or name not in KNOWN_GUARDS:
            connection.exec_driver_sql(statement)
    for statement in _guards():
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0012 foreign key check failed')


def downgrade():
    raise NotImplementedError
