"""Shortfall coverage (sales-orders design §5): `purchase_coverage_links`.

One immutable row per purchase-order line written by `sales-order purchase-order` or
`work-order purchase-order`, naming the order or work-order line it covers and the base quantity.
How much a link still covers is derived from the purchase order as it stands, never stored. One
new table, its index and two guards; nothing existing changes. DDL below is frozen.
"""
from alembic import op

revision = 'co0087'
down_revision = 'co0082'
branch_labels = depends_on = None
DDL = ("\nCREATE TABLE purchase_coverage_links (\n\tid VARCHAR(26) NOT NULL, \n\twork_document_id VARCHAR(26) NOT NULL, \n\twork_line_id VARCHAR(26) NOT NULL, \n\tpurchase_order_id VARCHAR(26) NOT NULL, \n\tpurchase_order_line_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_purchase_coverage_po_line UNIQUE (purchase_order_line_id), \n\tCONSTRAINT ck_work_coverage_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tFOREIGN KEY(work_document_id) REFERENCES work_documents (id), \n\tFOREIGN KEY(work_line_id) REFERENCES work_line_identities (id), \n\tFOREIGN KEY(purchase_order_id) REFERENCES purchase_orders (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n",)
INDEXES = ('CREATE INDEX ix_purchase_coverage_document ON purchase_coverage_links (work_document_id)',)
GUARDS = (
    "CREATE TRIGGER purchase_coverage_links_immutable_update BEFORE UPDATE ON purchase_coverage_links "
    "BEGIN SELECT RAISE(ABORT, 'immutable coverage link'); END",
    "CREATE TRIGGER purchase_coverage_links_immutable_delete BEFORE DELETE ON purchase_coverage_links "
    "BEGIN SELECT RAISE(ABORT, 'immutable coverage link'); END",
)
RESERVED = ('purchase_coverage_links', 'ix_purchase_coverage_document',
            'purchase_coverage_links_immutable_update', 'purchase_coverage_links_immutable_delete')


def upgrade():
    connection = op.get_bind()
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if set(RESERVED) & set(existing):
        raise RuntimeError('co0087 reserved object already exists')
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0087 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
