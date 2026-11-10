"""co0130: revision-bound simple bill approval and atomic reversible commitments."""
from alembic import op
import sqlalchemy as sa
revision = 'co0130'
down_revision = 'co0129'
branch_labels = depends_on = None


def upgrade():
    for trigger in ('purchase_drafts_no_delete','purchase_draft_revisions_no_update','purchase_draft_revisions_no_delete'):
        op.execute('DROP TRIGGER ' + trigger)
    with op.batch_alter_table('purchase_drafts') as batch:
        batch.drop_constraint('ck_purchase_draft_scope', type_='check')
        batch.create_check_constraint('ck_purchase_draft_scope', "kind = 'bill' AND operation IN ('create','correct','restore') AND state IN ('draft','pending','approved','rejected','committed','cancelled')")
        batch.add_column(sa.Column('original_id',sa.String(26),nullable=True))
        batch.add_column(sa.Column('original_version',sa.Integer,nullable=True))
        batch.add_column(sa.Column('snapshot',sa.Text,nullable=True))
        batch.create_foreign_key('fk_purchase_draft_original','transactions',['original_id'],['id'],ondelete='RESTRICT')
    with op.batch_alter_table('purchase_draft_revisions') as batch:
        batch.drop_constraint('ck_purchase_draft_revision_state', type_='check')
        batch.create_check_constraint('ck_purchase_draft_revision_state', "state IN ('draft','pending','approved','rejected','committed','cancelled') AND version >= 1")
    op.execute('\nCREATE TABLE approval_policies (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\ttarget VARCHAR(24) NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tdefinition TEXT NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n')
    op.execute('\nCREATE TABLE approval_policy_revisions (\n\tid VARCHAR(26) NOT NULL, \n\tpublication_sequence INTEGER NOT NULL, \n\tpolicy_id VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tdefinition TEXT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (policy_id, version), \n\tUNIQUE (publication_sequence), \n\tFOREIGN KEY(policy_id) REFERENCES approval_policies (id) ON DELETE RESTRICT\n)\n\n')
    op.execute('\nCREATE TABLE approval_requests (\n\tid VARCHAR(26) NOT NULL, \n\tdraft_id VARCHAR(26) NOT NULL, \n\tdraft_version INTEGER NOT NULL, \n\tpolicy_revision_id VARCHAR(26) NOT NULL, \n\tsteps TEXT NOT NULL, \n\trequesters TEXT NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcurrent_step INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (draft_id, draft_version), \n\tFOREIGN KEY(draft_id) REFERENCES purchase_drafts (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(policy_revision_id) REFERENCES approval_policy_revisions (id) ON DELETE RESTRICT\n)\n\n')
    op.execute('CREATE INDEX ix_approval_requests_state ON approval_requests (state, id)')
    op.execute('\nCREATE TABLE approval_events (\n\tid VARCHAR(26) NOT NULL, \n\trequest_id VARCHAR(26), \n\tdraft_id VARCHAR(26) NOT NULL, \n\tsequence INTEGER NOT NULL, \n\taction VARCHAR(32) NOT NULL, \n\tactor VARCHAR(26) NOT NULL, \n\tprincipal VARCHAR(26), \n\tinterface VARCHAR(16) NOT NULL, \n\treason VARCHAR(140) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tdata TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (draft_id, sequence), \n\tFOREIGN KEY(request_id) REFERENCES approval_requests (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(draft_id) REFERENCES purchase_drafts (id) ON DELETE RESTRICT\n)\n\n')
    op.execute('\nCREATE TABLE approval_commits (\n\tid VARCHAR(26) NOT NULL, \n\trequest_id VARCHAR(26), \n\tdraft_id VARCHAR(26) NOT NULL, \n\tdraft_version INTEGER NOT NULL, \n\tdocument_id VARCHAR(26) NOT NULL, \n\tdocument_version INTEGER NOT NULL, \n\toperation VARCHAR(16) NOT NULL, \n\tbatch_id VARCHAR(26) NOT NULL, \n\tbefore_snapshot TEXT, \n\tbefore_payload TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(request_id) REFERENCES approval_requests (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(draft_id) REFERENCES purchase_drafts (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(document_id) REFERENCES transactions (id) ON DELETE RESTRICT, \n\tUNIQUE (batch_id), \n\tFOREIGN KEY(batch_id) REFERENCES batches (id) ON DELETE RESTRICT\n)\n\n')
    op.execute('\nCREATE TABLE approval_operations (\n\tid VARCHAR(26) NOT NULL, \n\tcommand VARCHAR(64) NOT NULL, \n\tactor VARCHAR(26) NOT NULL, \n\toperation_key VARCHAR(128) NOT NULL, \n\trequest TEXT NOT NULL, \n\tresult TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (command, actor, operation_key)\n)\n\n')
    op.execute("CREATE TRIGGER purchase_drafts_no_delete BEFORE DELETE ON purchase_drafts BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER purchase_draft_revisions_no_update BEFORE UPDATE ON purchase_draft_revisions BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER purchase_draft_revisions_no_delete BEFORE DELETE ON purchase_draft_revisions BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_policies_no_delete BEFORE DELETE ON approval_policies BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_requests_no_delete BEFORE DELETE ON approval_requests BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_policy_revisions_no_update BEFORE UPDATE ON approval_policy_revisions BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_policy_revisions_no_delete BEFORE DELETE ON approval_policy_revisions BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_events_no_update BEFORE UPDATE ON approval_events BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_events_no_delete BEFORE DELETE ON approval_events BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_commits_no_update BEFORE UPDATE ON approval_commits BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_commits_no_delete BEFORE DELETE ON approval_commits BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_operations_no_update BEFORE UPDATE ON approval_operations BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    op.execute("CREATE TRIGGER approval_operations_no_delete BEFORE DELETE ON approval_operations BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")


def downgrade():
    raise RuntimeError("Company migrations are forward-only")
