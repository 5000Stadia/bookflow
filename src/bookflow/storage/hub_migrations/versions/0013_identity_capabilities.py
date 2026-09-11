"""Seed the membership and user capabilities the identity commands already declare.

`user add|list|set-password` and `membership grant|revoke|list` shipped without a seed,
so `role_capabilities` sits six rows behind what the command registry declares. Nothing
fails today because roots run in legacy mode and never read the table; the moment one
does, every role - owners included - would be denied the commands that administer users
and memberships.
"""
import sqlalchemy as sa
from alembic import op

revision = 'hub0013'
down_revision = 'hub0012'
branch_labels = None
depends_on = None

ROLE_CAPABILITY_SEED = (
    ('admin', 'membership', 'authenticated'),
    ('hub_admin', 'membership', 'authenticated'),
    ('owner', 'membership', 'authenticated'),
    ('readonly', 'membership', 'authenticated'),
    ('standard', 'membership', 'authenticated'),
    ('hub_admin', 'user', 'hub_admin'),
)


def upgrade() -> None:
    table = sa.table('role_capabilities', sa.column('role'), sa.column('capability'), sa.column('required_role'), schema='main')
    # A root upgraded twice, or one already carrying a row, must not collide: the primary
    # key is (role, capability, required_role), so insert only what is absent. Read and
    # write are both bound to main: SQLite resolves an unqualified name to TEMP first,
    # so a same-named local table would otherwise take the insert the read never saw.
    existing = {tuple(row) for row in op.get_bind().exec_driver_sql(
        'SELECT role, capability, required_role FROM main.role_capabilities').fetchall()}
    missing = [dict(role=role, capability=capability, required_role=required)
               for role, capability, required in ROLE_CAPABILITY_SEED
               if (role, capability, required) not in existing]
    if missing:
        op.bulk_insert(table, missing)


def downgrade() -> None:
    raise NotImplementedError
