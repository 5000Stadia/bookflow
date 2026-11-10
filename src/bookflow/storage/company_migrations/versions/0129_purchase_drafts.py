"""co0129: saved expense-only bill drafts and permanent revision receipts; all approval layers off."""
from alembic import op
revision = 'co0129'
down_revision = 'co0128'
branch_labels = depends_on = None
TABLES = ('purchase_drafts', 'purchase_draft_revisions', 'purchase_draft_operations')
COLUMNS = ('purchase_approvals_enabled', 'bill_approvals_enabled', 'approval_routing_enabled', 'approval_reminders_enabled')
TRIGGERS = ('purchase_drafts_no_delete', 'purchase_draft_revisions_no_update', 'purchase_draft_revisions_no_delete', 'purchase_draft_operations_no_update', 'purchase_draft_operations_no_delete')

def upgrade():
    op.execute("\nCREATE TABLE purchase_drafts (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tkind VARCHAR(24) NOT NULL, \n\toperation VARCHAR(16) NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tpayload TEXT NOT NULL, \n\tpreview TEXT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_purchase_draft_scope CHECK (kind = 'bill' AND operation = 'create' AND state IN ('draft','cancelled')), \n\tCONSTRAINT ck_purchase_draft_version CHECK (version >= 1), \n\tCONSTRAINT ck_purchase_draft_json CHECK (json_valid(payload) AND json_valid(preview))\n)\n\n")
    op.execute('CREATE INDEX ix_purchase_drafts_state_id ON purchase_drafts (state, id)')
    op.execute("\nCREATE TABLE purchase_draft_revisions (\n\tid VARCHAR(26) NOT NULL, \n\tdraft_id VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tpayload TEXT NOT NULL, \n\tpreview TEXT NOT NULL, \n\tactor VARCHAR(26) NOT NULL, \n\tprincipal VARCHAR(26), \n\tinterface VARCHAR(16) NOT NULL, \n\treason VARCHAR(140) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_purchase_draft_revision UNIQUE (draft_id, version), \n\tCONSTRAINT ck_purchase_draft_revision_state CHECK (state IN ('draft','cancelled') AND version >= 1), \n\tCONSTRAINT ck_purchase_draft_revision_json CHECK (json_valid(payload) AND json_valid(preview)), \n\tFOREIGN KEY(draft_id) REFERENCES purchase_drafts (id) ON DELETE RESTRICT\n)\n\n")
    op.execute('\nCREATE TABLE purchase_draft_operations (\n\tid VARCHAR(26) NOT NULL, \n\tdraft_id VARCHAR(26) NOT NULL, \n\tcommand VARCHAR(64) NOT NULL, \n\tactor VARCHAR(26) NOT NULL, \n\toperation_key VARCHAR(128) NOT NULL, \n\trequest TEXT NOT NULL, \n\tresult TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_purchase_draft_operation UNIQUE (command, actor, operation_key), \n\tFOREIGN KEY(draft_id) REFERENCES purchase_drafts (id) ON DELETE RESTRICT\n)\n\n')
    op.execute('ALTER TABLE company_info ADD COLUMN purchase_approvals_enabled BOOLEAN DEFAULT 0 NOT NULL CHECK (purchase_approvals_enabled IN (0, 1))')
    op.execute('ALTER TABLE company_info ADD COLUMN bill_approvals_enabled BOOLEAN DEFAULT 0 NOT NULL CHECK (bill_approvals_enabled IN (0, 1))')
    op.execute('ALTER TABLE company_info ADD COLUMN approval_routing_enabled BOOLEAN DEFAULT 0 NOT NULL CHECK (approval_routing_enabled IN (0, 1))')
    op.execute('ALTER TABLE company_info ADD COLUMN approval_reminders_enabled BOOLEAN DEFAULT 0 NOT NULL CHECK (approval_reminders_enabled IN (0, 1))')
    op.execute("CREATE TRIGGER purchase_drafts_no_delete BEFORE DELETE ON purchase_drafts BEGIN SELECT RAISE(ABORT, 'purchase draft history is retained'); END")
    op.execute("CREATE TRIGGER purchase_draft_revisions_no_update BEFORE UPDATE ON purchase_draft_revisions BEGIN SELECT RAISE(ABORT, 'purchase draft history is retained'); END")
    op.execute("CREATE TRIGGER purchase_draft_revisions_no_delete BEFORE DELETE ON purchase_draft_revisions BEGIN SELECT RAISE(ABORT, 'purchase draft history is retained'); END")
    op.execute("CREATE TRIGGER purchase_draft_operations_no_update BEFORE UPDATE ON purchase_draft_operations BEGIN SELECT RAISE(ABORT, 'purchase draft history is retained'); END")
    op.execute("CREATE TRIGGER purchase_draft_operations_no_delete BEFORE DELETE ON purchase_draft_operations BEGIN SELECT RAISE(ABORT, 'purchase draft history is retained'); END")


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
