"""co0139: purchase-order proposals and typed native release links; switches stay off."""
from alembic import op
import sqlalchemy as sa

revision = 'co0139'
down_revision = 'co0138'
branch_labels = depends_on = None


def upgrade():
    op.execute('DROP TRIGGER purchase_drafts_no_delete')
    with op.batch_alter_table('purchase_drafts') as batch:
        batch.drop_constraint('ck_purchase_draft_scope', type_='check')
        batch.create_check_constraint('ck_purchase_draft_scope', "kind IN ('bill','purchase_order') AND operation IN ('create','correct','restore') AND state IN ('draft','pending','approved','rejected','committed','cancelled')")
        batch.add_column(sa.Column('original_order_id', sa.String(26), nullable=True))
        batch.create_foreign_key('fk_purchase_draft_original_order', 'purchase_orders', ['original_order_id'], ['id'], ondelete='RESTRICT')
        batch.create_check_constraint('ck_purchase_draft_original_kind', "(kind = 'bill' AND original_order_id IS NULL) OR (kind = 'purchase_order' AND original_id IS NULL)")
    for action in ('update', 'delete'):
        op.execute('DROP TRIGGER approval_commits_no_' + action)
    with op.batch_alter_table('approval_commits') as batch:
        batch.alter_column('document_id', existing_type=sa.String(26), nullable=True)
        batch.add_column(sa.Column('order_id', sa.String(26), nullable=True))
        batch.create_foreign_key('fk_approval_commit_order', 'purchase_orders', ['order_id'], ['id'], ondelete='RESTRICT')
        batch.create_check_constraint('ck_approval_commit_document', '(document_id IS NOT NULL AND order_id IS NULL) OR (document_id IS NULL AND order_id IS NOT NULL)')
    op.execute("CREATE TRIGGER purchase_drafts_no_delete BEFORE DELETE ON purchase_drafts BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")
    for action in ('update', 'delete'):
        op.execute(f"CREATE TRIGGER approval_commits_no_{action} BEFORE {action.upper()} ON approval_commits BEGIN SELECT RAISE(ABORT, 'approval history is retained'); END")


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
