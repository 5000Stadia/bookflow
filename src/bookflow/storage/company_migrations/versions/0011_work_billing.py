"""Frozen work billing schema and preserving sales price-mode widening."""
import re

from alembic import op

revision = 'co0011'
down_revision = 'co0010'
branch_labels = None
depends_on = None

# Literal revision-local DDL; independent of all future application metadata.
DDL = (
    "CREATE TABLE work_billing_conversions (\n\tid VARCHAR(26) NOT NULL, \n\tsource_document_id VARCHAR(26) NOT NULL, \n\tsource_revision_id VARCHAR(26) NOT NULL, \n\tsource_version BIGINT NOT NULL, \n\tdestination_transaction_id VARCHAR(26) NOT NULL, \n\tdestination_revision_id VARCHAR(26) NOT NULL, \n\tdestination_type VARCHAR(32) NOT NULL, \n\trelation VARCHAR(32) NOT NULL, \n\tconversion_key_hash VARCHAR(64) NOT NULL, \n\trequest_hash VARCHAR(64) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_work_billing_conversion_key UNIQUE (conversion_key_hash), \n\tCONSTRAINT uq_work_billing_destination_birth UNIQUE (destination_transaction_id), \n\tCONSTRAINT fk_work_billing_conversion_source FOREIGN KEY(source_document_id, source_revision_id) REFERENCES work_revisions (document_id, id), \n\tCONSTRAINT fk_work_billing_conversion_destination FOREIGN KEY(destination_transaction_id, destination_revision_id) REFERENCES sales_profiles (transaction_id, revision_id), \n\tCONSTRAINT fk_work_billing_conversion_type FOREIGN KEY(destination_transaction_id, destination_type) REFERENCES transactions (id, type), \n\tCONSTRAINT ck_work_billing_source_version CHECK (typeof(source_version) = 'integer' AND source_version > 0), \n\tCONSTRAINT ck_work_billing_destination_type CHECK (destination_type IN ('invoice','sales_receipt')), \n\tCONSTRAINT ck_work_billing_relation CHECK (relation IN ('estimate_invoice','estimate_sales_receipt','work_order_invoice','work_order_sales_receipt')), \n\tCONSTRAINT ck_work_billing_hashes CHECK (length(conversion_key_hash) = 64 AND length(request_hash) = 64)\n)",
    "CREATE TABLE work_billing_allocations (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tdocument_line_id VARCHAR(26) NOT NULL, \n\tsource_document_id VARCHAR(26) NOT NULL, \n\tsource_revision_id VARCHAR(26) NOT NULL, \n\tsource_line_id VARCHAR(26) NOT NULL, \n\troot_document_id VARCHAR(26) NOT NULL, \n\troot_line_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tnet_minor_units BIGINT NOT NULL, \n\ttax_minor_units BIGINT NOT NULL, \n\tgross_minor_units BIGINT NOT NULL, \n\tfacts_snapshot TEXT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_work_billing_revision_root UNIQUE (revision_id, root_document_id, root_line_id), \n\tCONSTRAINT fk_work_billing_allocation_destination FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES sales_line_profiles (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_work_billing_allocation_source FOREIGN KEY(source_document_id, source_revision_id, source_line_id) REFERENCES work_lines (document_id, revision_id, id), \n\tCONSTRAINT fk_work_billing_allocation_root FOREIGN KEY(root_document_id, root_line_id) REFERENCES work_line_identities (document_id, id), \n\tCONSTRAINT ck_work_billing_quantity_microunits CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT ck_work_billing_net_minor_units CHECK (typeof(net_minor_units) = 'integer' AND net_minor_units >= 0), \n\tCONSTRAINT ck_work_billing_tax_minor_units CHECK (typeof(tax_minor_units) = 'integer' AND tax_minor_units >= 0), \n\tCONSTRAINT ck_work_billing_gross_minor_units CHECK (typeof(gross_minor_units) = 'integer' AND gross_minor_units >= 0), \n\tCONSTRAINT ck_work_billing_total CHECK (gross_minor_units = net_minor_units + tax_minor_units), \n\tCONSTRAINT ck_work_billing_facts CHECK (json_valid(facts_snapshot) AND json_type(facts_snapshot) = 'object')\n)",
    'CREATE INDEX ix_work_billing_allocation_root ON work_billing_allocations (root_document_id, root_line_id)',
    'CREATE INDEX ix_work_billing_allocation_source ON work_billing_allocations (source_document_id, source_revision_id)',
    "CREATE TRIGGER work_billing_conversions_immutable_update BEFORE UPDATE ON work_billing_conversions BEGIN SELECT RAISE(ABORT, 'immutable work billing history'); END",
    "CREATE TRIGGER work_billing_conversions_immutable_delete BEFORE DELETE ON work_billing_conversions BEGIN SELECT RAISE(ABORT, 'immutable work billing history'); END",
    "CREATE TRIGGER work_billing_allocations_immutable_update BEFORE UPDATE ON work_billing_allocations BEGIN SELECT RAISE(ABORT, 'immutable work billing history'); END",
    "CREATE TRIGGER work_billing_allocations_immutable_delete BEFORE DELETE ON work_billing_allocations BEGIN SELECT RAISE(ABORT, 'immutable work billing history'); END",
    "CREATE TRIGGER work_billing_conversion_birth BEFORE INSERT ON work_billing_conversions\nWHEN NOT EXISTS (SELECT 1 FROM work_documents s JOIN transactions d ON d.id = NEW.destination_transaction_id\nJOIN transaction_revisions r ON r.transaction_id = d.id AND r.id = NEW.destination_revision_id\nWHERE s.id = NEW.source_document_id AND s.kind IN ('estimate','work_order')\nAND d.type = NEW.destination_type AND NEW.relation = s.kind || '_' || d.type AND r.revision_number = 1)\nBEGIN SELECT RAISE(ABORT, 'invalid work billing conversion lineage'); END",
    "CREATE TRIGGER work_billing_conversion_key BEFORE INSERT ON work_billing_conversions\nWHEN EXISTS (SELECT 1 FROM work_links WHERE conversion_key_hash = NEW.conversion_key_hash)\nBEGIN SELECT RAISE(ABORT, 'work conversion key already used'); END",
    "CREATE TRIGGER work_link_billing_key BEFORE INSERT ON work_links\nWHEN EXISTS (SELECT 1 FROM work_billing_conversions WHERE conversion_key_hash = NEW.conversion_key_hash)\nBEGIN SELECT RAISE(ABORT, 'work conversion key already used'); END",
    "CREATE TRIGGER work_billing_conversion_replace BEFORE INSERT ON work_billing_conversions\nWHEN EXISTS (SELECT 1 FROM work_billing_conversions WHERE id = NEW.id\nOR destination_transaction_id = NEW.destination_transaction_id OR conversion_key_hash = NEW.conversion_key_hash)\nBEGIN SELECT RAISE(ABORT, 'immutable work billing history'); END",
    "CREATE TRIGGER work_billing_allocation_replace BEFORE INSERT ON work_billing_allocations\nWHEN EXISTS (SELECT 1 FROM work_billing_allocations WHERE id = NEW.id OR\n(revision_id = NEW.revision_id AND root_document_id = NEW.root_document_id AND root_line_id = NEW.root_line_id))\nBEGIN SELECT RAISE(ABORT, 'immutable work billing history'); END",
    "CREATE TRIGGER work_billing_allocation_root BEFORE INSERT ON work_billing_allocations\nWHEN NOT EXISTS (SELECT 1 FROM work_lines s JOIN work_line_identities i\nON i.document_id = s.document_id AND i.id = s.line_id\nWHERE s.document_id = NEW.source_document_id AND s.revision_id = NEW.source_revision_id AND s.id = NEW.source_line_id\nAND i.root_document_id = NEW.root_document_id AND i.root_line_id = NEW.root_line_id)\nBEGIN SELECT RAISE(ABORT, 'invalid work billing allocation root'); END",
    "CREATE TRIGGER work_billing_allocation_active BEFORE INSERT ON work_billing_allocations\nWHEN EXISTS (SELECT 1 FROM work_billing_allocations a JOIN transactions t\nON t.id = a.transaction_id AND t.current_revision_id = a.revision_id AND t.status = 'posted'\nWHERE a.root_document_id = NEW.root_document_id AND a.root_line_id = NEW.root_line_id\nAND a.transaction_id <> NEW.transaction_id)\nBEGIN SELECT RAISE(ABORT, 'work billing root already consumed'); END",
    "CREATE TRIGGER work_billing_transaction_insert BEFORE INSERT ON transactions\nWHEN NEW.status = 'posted' AND EXISTS (\nSELECT 1 FROM work_billing_allocations pending JOIN work_billing_allocations active\nON active.root_document_id = pending.root_document_id AND active.root_line_id = pending.root_line_id\nJOIN transactions t ON t.id = active.transaction_id AND t.current_revision_id = active.revision_id AND t.status = 'posted'\nWHERE pending.transaction_id = NEW.id AND pending.revision_id = NEW.current_revision_id AND active.transaction_id <> NEW.id)\nBEGIN SELECT RAISE(ABORT, 'work billing root already consumed'); END",
    "CREATE TRIGGER work_billing_transaction_update BEFORE UPDATE OF current_revision_id, status ON transactions\nWHEN NEW.status = 'posted' AND EXISTS (\nSELECT 1 FROM work_billing_allocations pending JOIN work_billing_allocations active\nON active.root_document_id = pending.root_document_id AND active.root_line_id = pending.root_line_id\nJOIN transactions t ON t.id = active.transaction_id AND t.current_revision_id = active.revision_id AND t.status = 'posted'\nWHERE pending.transaction_id = NEW.id AND pending.revision_id = NEW.current_revision_id AND active.transaction_id <> NEW.id)\nBEGIN SELECT RAISE(ABORT, 'work billing root already consumed'); END",
)

def _price_rebuild(connection):
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name='sales_line_profiles'"
    ).scalar_one()
    name = re.match(r'CREATE TABLE\s+(?:"sales_line_profiles"|sales_line_profiles)(?=\s*\()', sql)
    constraint = "CONSTRAINT ck_sales_unit_price_minor_units_nonnegative CHECK (typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0)"
    column = re.compile(r'(?m)^(\s*unit_price_minor_units BIGINT) NOT NULL(?=\s*,)')
    columns = connection.exec_driver_sql('PRAGMA table_xinfo(sales_line_profiles)').all()
    if (not name or sql.count(constraint) != 1 or len(column.findall(sql)) != 1
            or any(row[1] == 'pricing_basis' for row in columns)):
        raise RuntimeError('co0011 cannot safely identify the existing sales price schema')
    widened = "CONSTRAINT ck_sales_pricing_basis CHECK ((pricing_basis = 'unit' AND typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0) OR (pricing_basis = 'amount' AND unit_price_minor_units IS NULL))"
    create = 'CREATE TABLE _co0011_sales_line_profiles' + sql[name.end():]
    create = column.sub(r'\1', create, count=1).replace(constraint, widened, 1)
    # SQLite requires columns before table constraints. Insert the new column first;
    # every existing definition and table suffix remains byte-for-byte intact.
    start = create.index('(') + 1
    create = create[:start] + "\n pricing_basis VARCHAR(16) DEFAULT 'unit' NOT NULL," + create[start:]
    quote = lambda value: '"' + value.replace('"', '""') + '"'
    writable = ','.join(quote(row[1]) for row in columns if row[6] == 0)
    selected = ','.join(quote(row[1]) for row in columns)
    return create, writable, selected


def upgrade() -> None:
    connection = op.get_bind()
    create, writable, selected = _price_rebuild(connection)
    retained = connection.exec_driver_sql(
        "SELECT type, name, sql FROM sqlite_schema WHERE sql IS NOT NULL AND ("
        "type IN ('view', 'trigger') OR (type = 'index' AND tbl_name = "
        "'sales_line_profiles')) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name"
    ).all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                quoted = '"' + name.replace('"', '""') + '"'
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quoted}')
    connection.exec_driver_sql(create)
    connection.exec_driver_sql(f'INSERT INTO _co0011_sales_line_profiles ({writable}) SELECT {writable} FROM sales_line_profiles')
    for left, right in (('sales_line_profiles', '_co0011_sales_line_profiles'),
                        ('_co0011_sales_line_profiles', 'sales_line_profiles')):
        if connection.exec_driver_sql(f'SELECT {selected} FROM {left} EXCEPT SELECT {selected} FROM {right}').fetchone() is not None:
            raise RuntimeError('co0011 rebuilt sales line values differ')
    connection.exec_driver_sql('DROP TABLE sales_line_profiles')
    connection.exec_driver_sql('ALTER TABLE _co0011_sales_line_profiles RENAME TO sales_line_profiles')
    for statement in DDL:
        op.execute(statement)
    for _, _, statement in retained:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0011 foreign key check failed')


def downgrade() -> None:
    raise NotImplementedError
