"""hub capability and feature compatibility schema

Revision ID: hub0003
Revises: hub0002
"""

import sqlalchemy as sa
from alembic import op

revision = "hub0003"
down_revision = "hub0002"


# Frozen projection of the command registry. Each concrete role at or above a
# command's required role receives its capability; ``authenticated`` represents
# a registry required_role of None and admits every concrete role.
ROLE_CAPABILITY_SEED = (
    ("admin", "audit", "member"),
    ("admin", "company", "admin"),
    ("admin", "company", "authenticated"),
    ("admin", "company", "member"),
    ("admin", "directive", "member"),
    ("admin", "directive", "standard"),
    ("admin", "hub", "authenticated"),
    ("admin", "init", "authenticated"),
    ("admin", "organization", "authenticated"),
    ("admin", "presence", "standard"),
    ("admin", "serve", "authenticated"),
    ("admin", "token", "authenticated"),
    ("admin", "upgrade", "authenticated"),
    ("admin", "user", "authenticated"),
    ("hub_admin", "audit", "member"),
    ("hub_admin", "company", "admin"),
    ("hub_admin", "company", "authenticated"),
    ("hub_admin", "company", "hub_admin"),
    ("hub_admin", "company", "member"),
    ("hub_admin", "demo", "hub_admin"),
    ("hub_admin", "directive", "member"),
    ("hub_admin", "directive", "standard"),
    ("hub_admin", "hub", "authenticated"),
    ("hub_admin", "init", "authenticated"),
    ("hub_admin", "organization", "authenticated"),
    ("hub_admin", "organization", "hub_admin"),
    ("hub_admin", "presence", "standard"),
    ("hub_admin", "serve", "authenticated"),
    ("hub_admin", "token", "authenticated"),
    ("hub_admin", "upgrade", "authenticated"),
    ("hub_admin", "user", "authenticated"),
    ("owner", "audit", "member"),
    ("owner", "company", "admin"),
    ("owner", "company", "authenticated"),
    ("owner", "company", "member"),
    ("owner", "directive", "member"),
    ("owner", "directive", "standard"),
    ("owner", "hub", "authenticated"),
    ("owner", "init", "authenticated"),
    ("owner", "organization", "authenticated"),
    ("owner", "presence", "standard"),
    ("owner", "serve", "authenticated"),
    ("owner", "token", "authenticated"),
    ("owner", "upgrade", "authenticated"),
    ("owner", "user", "authenticated"),
    ("readonly", "audit", "member"),
    ("readonly", "company", "authenticated"),
    ("readonly", "company", "member"),
    ("readonly", "directive", "member"),
    ("readonly", "hub", "authenticated"),
    ("readonly", "init", "authenticated"),
    ("readonly", "organization", "authenticated"),
    ("readonly", "serve", "authenticated"),
    ("readonly", "token", "authenticated"),
    ("readonly", "upgrade", "authenticated"),
    ("readonly", "user", "authenticated"),
    ("standard", "audit", "member"),
    ("standard", "company", "authenticated"),
    ("standard", "company", "member"),
    ("standard", "directive", "member"),
    ("standard", "directive", "standard"),
    ("standard", "hub", "authenticated"),
    ("standard", "init", "authenticated"),
    ("standard", "organization", "authenticated"),
    ("standard", "presence", "standard"),
    ("standard", "serve", "authenticated"),
    ("standard", "token", "authenticated"),
    ("standard", "upgrade", "authenticated"),
    ("standard", "user", "authenticated"),
)


def upgrade() -> None:
    with op.batch_alter_table("memberships") as batch:
        batch.add_column(sa.Column("grants", sa.Text, nullable=True))
        batch.add_column(sa.Column("denies", sa.Text, nullable=True))

    role_capabilities = op.create_table(
        "role_capabilities",
        sa.Column("role", sa.String(12), primary_key=True),
        sa.Column("capability", sa.String(128), primary_key=True),
        sa.Column("required_role", sa.String(16), primary_key=True),
    )
    op.bulk_insert(
        role_capabilities,
        [
            {"role": role, "capability": capability, "required_role": required_role}
            for role, capability, required_role in ROLE_CAPABILITY_SEED
        ],
    )

    op.create_table(
        "features",
        sa.Column("scope_type", sa.String(12), primary_key=True),
        sa.Column("scope_id", sa.String(26), primary_key=True),
        sa.Column("feature", sa.String(128), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("enabled_by", sa.String(26), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("enabled_at", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError
