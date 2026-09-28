"""Authority generation: a token every write to a permission input table redraws.

Database triggers, not application code, maintain it, so no writer -- present or future,
any connection or process -- can change users, organizations, companies, memberships,
agent assignments, agent authority, role defaults or the permission state without the
token changing in that same transaction. The DDL is literal: this revision is frozen.
"""
import sqlalchemy as sa
from alembic import op

revision = 'hub0014'
down_revision = 'hub0013'
branch_labels = None
depends_on = None

TABLES = ('users', 'organizations', 'companies', 'memberships', 'agent_principals',
          'agent_authority', 'role_capabilities', 'permission_state')
EVENTS = ('INSERT', 'UPDATE', 'DELETE')


def trigger_sql(table, event):
    return (f'CREATE TRIGGER authority_generation_{table}_{event.lower()} AFTER {event} ON {table} '
            'BEGIN UPDATE authority_generation SET generation = generation + 1, '
            'token = lower(hex(randomblob(16))) WHERE id = 1; END')


def upgrade():
    connection = op.get_bind()
    op.create_table('authority_generation',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('generation', sa.Integer, nullable=False),
        sa.Column('token', sa.String(32), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_authority_generation_singleton'),
        sa.CheckConstraint('generation >= 1', name='ck_authority_generation_generation'), schema='main')
    connection.exec_driver_sql(
        'INSERT INTO main.authority_generation(id, generation, token) VALUES (1, 1, lower(hex(randomblob(16))))')
    for table in TABLES:
        for event in EVENTS:
            connection.exec_driver_sql(trigger_sql(table, event))


def downgrade():
    raise NotImplementedError('The authority generation is part of every permission read')
