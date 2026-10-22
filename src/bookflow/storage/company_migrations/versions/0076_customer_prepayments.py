"""Customer prepayments: `prepayment_links` and `prepayment_draws` (R124).

Written by the `prepayment` commands and by `<kind> invoice` when it uses held money. A link says
what a receipt is held for; a draw says which receipt a credit memo line drew from and where the
money went. Two new append-only tables; nothing existing changes. The Customer Prepayments account
and item are created on first use, not here. DDL below is frozen: this migration never imports
current application metadata.
"""
from alembic import op

revision = 'co0076'
# Chained on main's head when this branch was cut; re-chained at merge.
down_revision = 'co0078'
branch_labels = depends_on = None
DDL = (
    '\nCREATE TABLE prepayment_links (\n\tid VARCHAR(26) NOT NULL, \n\treceipt_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\twork_document_id VARCHAR(26), \n\tsupersedes_link_id VARCHAR(26), \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_prepayment_link_supersedes UNIQUE (supersedes_link_id), \n\tFOREIGN KEY(receipt_id) REFERENCES transactions (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(work_document_id) REFERENCES work_documents (id), \n\tFOREIGN KEY(supersedes_link_id) REFERENCES prepayment_links (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n',
    'CREATE INDEX ix_prepayment_links_customer ON prepayment_links (customer_id)',
    'CREATE INDEX ix_prepayment_links_document ON prepayment_links (work_document_id)',
    'CREATE INDEX ix_prepayment_links_receipt ON prepayment_links (receipt_id)',
    '\nCREATE TABLE prepayment_draws (\n\tid VARCHAR(26) NOT NULL, \n\tcredit_memo_id VARCHAR(26) NOT NULL, \n\tline_id VARCHAR(26) NOT NULL, \n\treceipt_id VARCHAR(26) NOT NULL, \n\twork_document_id VARCHAR(26), \n\tinvoice_id VARCHAR(26), \n\trefund_id VARCHAR(26), \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_prepayment_draw_line UNIQUE (credit_memo_id, line_id), \n\tCONSTRAINT ck_prepayment_draw_target CHECK ((invoice_id IS NULL) <> (refund_id IS NULL)), \n\tFOREIGN KEY(credit_memo_id) REFERENCES transactions (id), \n\tFOREIGN KEY(receipt_id) REFERENCES transactions (id), \n\tFOREIGN KEY(work_document_id) REFERENCES work_documents (id), \n\tFOREIGN KEY(invoice_id) REFERENCES transactions (id), \n\tFOREIGN KEY(refund_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n',
    'CREATE INDEX ix_prepayment_draws_document ON prepayment_draws (work_document_id)',
    'CREATE INDEX ix_prepayment_draws_invoice ON prepayment_draws (invoice_id)',
    'CREATE INDEX ix_prepayment_draws_receipt ON prepayment_draws (receipt_id)',
)
TRIGGERS = (
    "CREATE TRIGGER prepayment_links_no_update BEFORE UPDATE ON prepayment_links BEGIN SELECT RAISE(ABORT,'prepayment_links is append-only'); END",
    "CREATE TRIGGER prepayment_links_no_delete BEFORE DELETE ON prepayment_links BEGIN SELECT RAISE(ABORT,'prepayment_links is append-only'); END",
    "CREATE TRIGGER prepayment_draws_no_update BEFORE UPDATE ON prepayment_draws BEGIN SELECT RAISE(ABORT,'prepayment_draws is append-only'); END",
    "CREATE TRIGGER prepayment_draws_no_delete BEFORE DELETE ON prepayment_draws BEGIN SELECT RAISE(ABORT,'prepayment_draws is append-only'); END",
)


def upgrade():
    connection = op.get_bind()
    for statement in (*DDL, *TRIGGERS):
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0076 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
