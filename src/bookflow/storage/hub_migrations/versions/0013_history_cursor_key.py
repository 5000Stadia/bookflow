"""Hub-owned continuation signing material; no public cursor activation."""
from alembic import op
import sqlalchemy as sa

revision = 'hub0013'
down_revision = 'hub0012'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('history_cursor_keys',
        sa.Column('key_id', sa.Integer, primary_key=True),
        sa.Column('key_material', sa.LargeBinary, nullable=False),
        sa.CheckConstraint('key_id = 1', name='ck_history_cursor_key_slot'),
        sa.CheckConstraint("typeof(key_material) = 'blob' AND length(key_material) = 32", name='ck_history_cursor_key_material'),
        schema='main')
    op.get_bind().exec_driver_sql('INSERT INTO main.history_cursor_keys(key_id,key_material) VALUES (1,randomblob(32))')


def downgrade():
    raise NotImplementedError('History continuation signing material cannot be discarded')
