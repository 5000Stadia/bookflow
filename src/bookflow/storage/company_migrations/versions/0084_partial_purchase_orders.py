"""Billing part of a purchase order and closing an order line by hand (R153).

Three new append-only tables: ``purchase_order_bill_claims`` (what a bill entered from an order
took from each ordered line), ``purchase_order_bill_releases`` (a claim given back when its bill
is voided or deleted) and ``purchase_order_line_closures`` (a line closed or reopened by hand).
The receipt capacity trigger is replaced so a line's capacity counts receipts and bills
together and a closed line takes nothing more. DDL below is frozen: this migration never
imports current application metadata.
"""
from alembic import op

revision = 'co0084'
down_revision = 'co0080'
branch_labels = depends_on = None
DDL = (
    "\nCREATE TABLE purchase_order_bill_claims (\n\tid VARCHAR(26) NOT NULL, \n\torder_id VARCHAR(26) NOT NULL, \n\torder_line_id VARCHAR(26) NOT NULL, \n\tbill_id VARCHAR(26) NOT NULL, \n\tbill_revision_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT, \n\tamount_minor_units BIGINT NOT NULL, \n\tcreated_at TEXT NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via TEXT NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_po_bill_claim_quantity CHECK (quantity_microunits IS NULL OR (typeof(quantity_microunits)='integer' AND quantity_microunits>0)), \n\tCONSTRAINT ck_po_bill_claim_amount CHECK (typeof(amount_minor_units)='integer' AND amount_minor_units>=0), \n\tFOREIGN KEY(order_id) REFERENCES purchase_orders (id), \n\tFOREIGN KEY(order_line_id) REFERENCES purchase_order_line_identities (id), \n\tFOREIGN KEY(bill_id) REFERENCES transactions (id), \n\tFOREIGN KEY(bill_revision_id) REFERENCES transaction_revisions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n",
    '\nCREATE TABLE purchase_order_bill_releases (\n\tid VARCHAR(26) NOT NULL, \n\tclaim_id VARCHAR(26) NOT NULL, \n\tcreated_at TEXT NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via TEXT NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (claim_id), \n\tFOREIGN KEY(claim_id) REFERENCES purchase_order_bill_claims (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n',
    '\nCREATE TABLE purchase_order_line_closures (\n\tid VARCHAR(26) NOT NULL, \n\torder_id VARCHAR(26) NOT NULL, \n\torder_line_id VARCHAR(26) NOT NULL, \n\tclosed BOOLEAN NOT NULL, \n\tsequence INTEGER NOT NULL, \n\tcreated_at TEXT NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via TEXT NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (order_line_id, sequence), \n\tFOREIGN KEY(order_id) REFERENCES purchase_orders (id), \n\tFOREIGN KEY(order_line_id) REFERENCES purchase_order_line_identities (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n',
)
INDEXES = (
    'CREATE INDEX ix_po_bill_claims_bill ON purchase_order_bill_claims (bill_id)',
    'CREATE INDEX ix_po_bill_claims_line ON purchase_order_bill_claims (order_line_id)',
    'CREATE INDEX ix_po_bill_claims_order ON purchase_order_bill_claims (order_id)',
)
GUARDS = (
    "CREATE TRIGGER purchase_order_receipt_claim_capacity BEFORE INSERT ON purchase_order_receipt_claims WHEN NOT EXISTS (SELECT 1 FROM purchase_order_lines l JOIN purchase_orders o ON o.id=l.document_id JOIN item_receipt_lines r ON r.id=NEW.receipt_line_id WHERE l.line_id=NEW.order_line_id AND l.revision_id=NEW.order_revision_id AND o.current_revision_id=l.revision_id AND o.status<>'voided' AND NOT EXISTS (SELECT 1 FROM purchase_order_conversions v WHERE v.source_document_id=o.id) AND coalesce((SELECT z.closed FROM purchase_order_line_closures z WHERE z.order_line_id=l.line_id ORDER BY z.sequence DESC LIMIT 1),0)=0 AND l.item_id=r.item_id AND r.quantity_microunits=NEW.quantity_microunits AND NEW.quantity_microunits+coalesce((SELECT sum(x.quantity_microunits) FROM purchase_order_receipt_claims x WHERE x.order_line_id=l.line_id AND NOT EXISTS (SELECT 1 FROM purchase_order_receipt_releases z WHERE z.claim_id=x.id)),0)+coalesce((SELECT sum(b.quantity_microunits) FROM purchase_order_bill_claims b WHERE b.order_line_id=l.line_id AND NOT EXISTS (SELECT 1 FROM purchase_order_bill_releases z WHERE z.claim_id=b.id)),0)<=l.quantity_microunits) BEGIN SELECT RAISE(ABORT, 'physical order claim exceeds current ordered capacity'); END",
    "CREATE TRIGGER purchase_order_bill_claim_capacity BEFORE INSERT ON purchase_order_bill_claims WHEN NOT EXISTS (SELECT 1 FROM purchase_order_lines l JOIN purchase_orders o ON o.id=l.document_id WHERE l.line_id=NEW.order_line_id AND o.id=NEW.order_id AND o.current_revision_id=l.revision_id AND o.status<>'voided' AND NOT EXISTS (SELECT 1 FROM purchase_order_conversions v WHERE v.source_document_id=o.id) AND coalesce((SELECT z.closed FROM purchase_order_line_closures z WHERE z.order_line_id=l.line_id ORDER BY z.sequence DESC LIMIT 1),0)=0 AND ((l.quantity_microunits IS NOT NULL AND NEW.quantity_microunits IS NOT NULL AND NEW.quantity_microunits+coalesce((SELECT sum(x.quantity_microunits) FROM purchase_order_receipt_claims x WHERE x.order_line_id=l.line_id AND NOT EXISTS (SELECT 1 FROM purchase_order_receipt_releases z WHERE z.claim_id=x.id)),0)+coalesce((SELECT sum(b.quantity_microunits) FROM purchase_order_bill_claims b WHERE b.order_line_id=l.line_id AND NOT EXISTS (SELECT 1 FROM purchase_order_bill_releases z WHERE z.claim_id=b.id)),0)<=l.quantity_microunits) OR (l.quantity_microunits IS NULL AND NEW.quantity_microunits IS NULL AND NEW.amount_minor_units+coalesce((SELECT sum(b.amount_minor_units) FROM purchase_order_bill_claims b WHERE b.order_line_id=l.line_id AND NOT EXISTS (SELECT 1 FROM purchase_order_bill_releases z WHERE z.claim_id=b.id)),0)<=l.amount_minor_units))) BEGIN SELECT RAISE(ABORT, 'order bill claim exceeds what remains on the ordered line'); END",
    "CREATE TRIGGER purchase_order_bill_claims_immutable_update BEFORE UPDATE ON purchase_order_bill_claims BEGIN SELECT RAISE(ABORT, 'immutable purchase_order_bill_claims'); END",
    "CREATE TRIGGER purchase_order_bill_claims_immutable_delete BEFORE DELETE ON purchase_order_bill_claims BEGIN SELECT RAISE(ABORT, 'immutable purchase_order_bill_claims'); END",
    "CREATE TRIGGER purchase_order_bill_releases_immutable_update BEFORE UPDATE ON purchase_order_bill_releases BEGIN SELECT RAISE(ABORT, 'immutable purchase_order_bill_releases'); END",
    "CREATE TRIGGER purchase_order_bill_releases_immutable_delete BEFORE DELETE ON purchase_order_bill_releases BEGIN SELECT RAISE(ABORT, 'immutable purchase_order_bill_releases'); END",
    "CREATE TRIGGER purchase_order_line_closures_immutable_update BEFORE UPDATE ON purchase_order_line_closures BEGIN SELECT RAISE(ABORT, 'immutable purchase_order_line_closures'); END",
    "CREATE TRIGGER purchase_order_line_closures_immutable_delete BEFORE DELETE ON purchase_order_line_closures BEGIN SELECT RAISE(ABORT, 'immutable purchase_order_line_closures'); END",
)
RESERVED = {'purchase_order_bill_claims', 'purchase_order_bill_releases', 'purchase_order_line_closures'}


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & RESERVED:
        raise RuntimeError('co0084 reserved object exists')
    for statement in DDL + INDEXES:
        db.exec_driver_sql(statement)
    db.exec_driver_sql('DROP TRIGGER purchase_order_receipt_claim_capacity')
    for statement in GUARDS:
        db.exec_driver_sql(statement)
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0084 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
