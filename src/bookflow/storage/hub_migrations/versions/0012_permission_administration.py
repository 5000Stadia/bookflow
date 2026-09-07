"""Inert preserving permission-administration storage; no policy activation."""
from alembic import op
import sqlalchemy as sa
from bookflow.core.errors import BookflowError

revision = 'hub0012'
down_revision = 'hub0011'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Admission and updates use the caller's same BEGIN IMMEDIATE connection,
    # including its TEMP schema. Never inspect or execute local trigger bodies.
    needed = connection.exec_driver_sql('SELECT EXISTS(SELECT 1 FROM agent_authority WHERE suspended_at IS NOT NULL)').scalar_one()
    if needed:
        for schema in ('sqlite_master', 'sqlite_temp_master'):
            attached = connection.execute(sa.text(f"SELECT EXISTS(SELECT 1 FROM {schema} WHERE type='trigger' AND tbl_name = :table COLLATE NOCASE)"), {'table': 'agent_authority'}).scalar_one()
            if attached:
                raise BookflowError('E_MIGRATION_FAILED', details={'cause': 'unsupported_authority_backfill_trigger'})
    for table in ('memberships', 'agent_authority'):
        op.add_column(table, sa.Column('version', sa.Integer, sa.CheckConstraint('version >= 1', name=f'ck_{table}_version'), nullable=False, server_default=sa.text('1')))
        for name, size in (('updated_at', 32), ('updated_by', 26), ('updated_via', 16)):
            op.add_column(table, sa.Column(name, sa.String(size), nullable=True))
    for name, size in (('authorized_at', 32), ('authorized_by', 26), ('permitted_use_at', 32), ('fresh_context_ack_at', 32)):
        op.add_column('agent_authority', sa.Column(name, sa.String(size), nullable=True))
    op.add_column('agent_authority', sa.Column('fresh_context_required', sa.Boolean, sa.CheckConstraint('fresh_context_required IN (0,1)', name='ck_agent_authority_fresh_context'), nullable=False, server_default=sa.text('0')))
    if needed:
        connection.exec_driver_sql('UPDATE agent_authority SET fresh_context_required=1 WHERE suspended_at IS NOT NULL')
    op.create_table('permission_state',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('generation', sa.Integer, nullable=False),
        sa.Column('mode', sa.String(16), nullable=False),
        sa.Column('catalog_version', sa.Text, nullable=True),
        sa.Column('catalog_sha256', sa.String(64), nullable=True),
        sa.Column('catalog_json', sa.Text, nullable=True),
        sa.Column('updated_at', sa.String(32), nullable=True),
        sa.Column('updated_by', sa.String(26), nullable=True),
        sa.Column('updated_via', sa.String(16), nullable=True),
        sa.CheckConstraint('id = 1', name='ck_permission_state_singleton'),
        sa.CheckConstraint('generation >= 1', name='ck_permission_state_generation'),
        sa.CheckConstraint("mode IN ('legacy','policy_v1')", name='ck_permission_state_mode'),
        sa.CheckConstraint("(mode='legacy' AND catalog_version IS NULL AND catalog_sha256 IS NULL AND catalog_json IS NULL) OR (mode='policy_v1' AND catalog_version IS NOT NULL AND catalog_sha256 IS NOT NULL AND length(catalog_sha256)=64 AND catalog_json IS NOT NULL)", name='ck_permission_state_catalog'))
    connection.exec_driver_sql("INSERT INTO permission_state(id,generation,mode) VALUES (1,1,'legacy')")


def downgrade():
    raise NotImplementedError('Permission administration history cannot be discarded')
