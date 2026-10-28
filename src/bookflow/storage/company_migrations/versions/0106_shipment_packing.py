"""Packing and shipping facts (R181 increment 4, design/pick-pack-ship.md §5, §6, §8).

Six new tables -- ``shipment_drafts``, ``shipment_draft_revisions``, ``shipment_draft_lines``,
``shipment_packages``, ``shipment_package_contents`` and ``shipment_freight_billings`` -- every row immutable
(revisions append, each carrying its packing). Four nullable or defaulted columns, moving no stored row:
``shipment_revisions.freight_minor_units`` and ``freight_item_id`` (null: no freight), ``company_info.weight_unit``
(``lb``) and ``company_info.shipping_item_id`` (null). DDL below is frozen: this migration never imports current
application metadata.
"""
import sqlalchemy as sa
from alembic import op

revision = 'co0106'
down_revision = 'co0105'
branch_labels = depends_on = None

DDL = ('\nCREATE TABLE shipment_drafts (\n\tid VARCHAR(26) NOT NULL, \n\tnumber VARCHAR(64) NOT NULL, \n\tsales_order_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\tsite_id VARCHAR(26) NOT NULL, \n\tpick_list_id VARCHAR(26), \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_shipment_drafts_number UNIQUE (number), \n\tFOREIGN KEY(sales_order_id) REFERENCES work_documents (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(pick_list_id) REFERENCES pick_lists (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n', "\nCREATE TABLE shipment_draft_revisions (\n\tid VARCHAR(26) NOT NULL, \n\tdraft_id VARCHAR(26) NOT NULL, \n\trevision_number INTEGER NOT NULL, \n\tstatus VARCHAR(10) NOT NULL, \n\tship_date VARCHAR(10), \n\tcount_position VARCHAR(8) NOT NULL, \n\tship_method_id VARCHAR(26), \n\tcarrier TEXT, \n\ttracking_numbers TEXT NOT NULL, \n\tmemo TEXT, \n\tfreight_minor_units BIGINT, \n\tfreight_item_id VARCHAR(26), \n\treason TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_draft_revision_status CHECK (status IN ('draft', 'cancelled', 'shipped')), \n\tCONSTRAINT ck_shipment_draft_revision_position CHECK (count_position IN ('before', 'after')), \n\tCONSTRAINT ck_shipment_draft_revision_date CHECK (ship_date IS NULL OR ship_date LIKE '____-__-__'), \n\tCONSTRAINT ck_shipment_draft_revision_number CHECK (typeof(revision_number) = 'integer' AND revision_number > 0), \n\tCONSTRAINT ck_shipment_draft_revision_tracking CHECK (json_valid(tracking_numbers) AND json_type(tracking_numbers) = 'array'), \n\tCONSTRAINT ck_shipment_draft_revision_freight CHECK (freight_minor_units IS NULL OR freight_minor_units > 0), \n\tCONSTRAINT uq_shipment_draft_revision_number UNIQUE (draft_id, revision_number), \n\tFOREIGN KEY(draft_id) REFERENCES shipment_drafts (id), \n\tFOREIGN KEY(ship_method_id) REFERENCES ship_methods (id), \n\tFOREIGN KEY(freight_item_id) REFERENCES items (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE shipment_draft_lines (\n\tid VARCHAR(26) NOT NULL, \n\tdraft_id VARCHAR(26) NOT NULL, \n\tline_identity VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\troot_document_id VARCHAR(26) NOT NULL, \n\troot_line_id VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tbin_id VARCHAR(26), \n\tlot TEXT, \n\tserial_numbers TEXT, \n\tdescription TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_draft_line_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT ck_shipment_draft_line_serials CHECK (serial_numbers IS NULL OR (json_valid(serial_numbers) AND json_type(serial_numbers) = 'array')), \n\tCONSTRAINT uq_shipment_draft_line_position UNIQUE (draft_id, position), \n\tFOREIGN KEY(draft_id) REFERENCES shipment_drafts (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(bin_id) REFERENCES inventory_bins (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE shipment_packages (\n\tid VARCHAR(26) NOT NULL, \n\tshipment_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tbox_number INTEGER NOT NULL, \n\tpackage_type TEXT, \n\ttare_microunits BIGINT, \n\tweight_microunits BIGINT, \n\tdimensions TEXT, \n\ttracking_number TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_package_box CHECK (typeof(box_number) = 'integer' AND box_number > 0), \n\tCONSTRAINT ck_shipment_package_tare CHECK (tare_microunits IS NULL OR tare_microunits >= 0), \n\tCONSTRAINT ck_shipment_package_weight CHECK (weight_microunits IS NULL OR weight_microunits > 0), \n\tCONSTRAINT uq_shipment_package_box UNIQUE (revision_id, box_number), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE shipment_package_contents (\n\tid VARCHAR(26) NOT NULL, \n\tpackage_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tline_identity VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tserial_numbers TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_package_content_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT ck_shipment_package_content_serials CHECK (serial_numbers IS NULL OR (json_valid(serial_numbers) AND json_type(serial_numbers) = 'array')), \n\tCONSTRAINT uq_shipment_package_content UNIQUE (package_id, line_identity), \n\tFOREIGN KEY(package_id) REFERENCES shipment_packages (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", '\nCREATE TABLE shipment_freight_billings (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tline_id VARCHAR(26) NOT NULL, \n\tshipment_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_shipment_freight_billing UNIQUE (transaction_id, line_id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(line_id) REFERENCES document_line_identities (id), \n\tFOREIGN KEY(shipment_id) REFERENCES shipments (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n')
INDEXES = ('CREATE INDEX ix_shipment_drafts_order ON shipment_drafts (sales_order_id)', 'CREATE INDEX ix_shipment_packages_shipment ON shipment_packages (shipment_id)', 'CREATE INDEX ix_shipment_package_contents_revision ON shipment_package_contents (revision_id)', 'CREATE INDEX ix_shipment_freight_billings_shipment ON shipment_freight_billings (shipment_id)')
TABLES = ('shipment_freight_billings', 'shipment_package_contents', 'shipment_packages', 'shipment_draft_lines',
          'shipment_draft_revisions', 'shipment_drafts')
GUARDS = tuple(
    statement for table in reversed(TABLES) for statement in (
        f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END",
        f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END"))
COMPANY_COLUMNS = ('weight_unit',)
# Columns with a foreign key, which DROP COLUMN refuses (a test rewinding this head takes their text out instead).
COMPANY_REFERENCES = ('shipping_item_id',)
REVISION_REFERENCES = ('freight_item_id',)
REVISION_COLUMNS = ('freight_minor_units',)
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    connection = op.get_bind()
    reserved = set(TABLES) | set(TRIGGERS)
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0106 reserved object already exists')
    op.add_column('company_info', sa.Column('weight_unit', sa.String(16), nullable=False, server_default='lb'))
    connection.exec_driver_sql('ALTER TABLE company_info ADD COLUMN shipping_item_id VARCHAR(26) REFERENCES items (id)')
    connection.exec_driver_sql('ALTER TABLE shipment_revisions ADD COLUMN freight_minor_units BIGINT')
    connection.exec_driver_sql('ALTER TABLE shipment_revisions ADD COLUMN freight_item_id VARCHAR(26) '
                               'REFERENCES items (id)')
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0106 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
