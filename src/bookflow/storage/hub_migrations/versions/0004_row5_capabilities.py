"""Row 5 capability projection.

Revision ID: hub0004
Revises: hub0003
"""

import sqlalchemy as sa
from alembic import op

revision = "hub0004"
down_revision = "hub0003"


# Frozen command requirements for this revision. This revision-local literal is
# projected to concrete stored roles without importing the live registry.
CAPABILITY_REQUIREMENTS = (
    ("account", "member"),
    ("account", "standard"),
    ("audit", "member"),
    ("chart", "admin"),
    ("chart", "authenticated"),
    ("class", "member"),
    ("class", "standard"),
    ("company", "admin"),
    ("company", "authenticated"),
    ("company", "hub_admin"),
    ("company", "member"),
    ("custom-field", "admin"),
    ("custom-field", "member"),
    ("customer-message", "member"),
    ("customer-message", "standard"),
    ("customer-type", "member"),
    ("customer-type", "standard"),
    ("demo", "hub_admin"),
    ("directive", "member"),
    ("directive", "standard"),
    ("hub", "authenticated"),
    ("init", "authenticated"),
    ("item-category", "member"),
    ("item-category", "standard"),
    ("job-type", "member"),
    ("job-type", "standard"),
    ("organization", "authenticated"),
    ("organization", "hub_admin"),
    ("payment-method", "member"),
    ("payment-method", "standard"),
    ("presence", "standard"),
    ("profile", "admin"),
    ("profile", "authenticated"),
    ("sales-rep", "member"),
    ("sales-rep", "standard"),
    ("sales-tax-code", "member"),
    ("sales-tax-code", "standard"),
    ("serve", "authenticated"),
    ("ship-method", "member"),
    ("ship-method", "standard"),
    ("term", "member"),
    ("term", "standard"),
    ("token", "authenticated"),
    ("upgrade", "authenticated"),
    ("user", "authenticated"),
    ("vendor-type", "member"),
    ("vendor-type", "standard"),
)

_ROLE_RANK = {"readonly": 0, "standard": 1, "admin": 2, "owner": 3, "hub_admin": 4}
_REQUIRED_RANK = {"authenticated": 0, "member": 0, "standard": 1, "admin": 2, "owner": 3, "hub_admin": 4}
ROLE_CAPABILITY_SEED = tuple(
    sorted(
        (role, capability, required_role)
        for capability, required_role in CAPABILITY_REQUIREMENTS
        for role, rank in _ROLE_RANK.items()
        if rank >= _REQUIRED_RANK[required_role]
    )
)


def upgrade() -> None:
    role_capabilities = sa.table(
        "role_capabilities",
        sa.column("role", sa.String(12)),
        sa.column("capability", sa.String(128)),
        sa.column("required_role", sa.String(16)),
    )
    op.execute(role_capabilities.delete())
    op.bulk_insert(
        role_capabilities,
        [
            {"role": role, "capability": capability, "required_role": required_role}
            for role, capability, required_role in ROLE_CAPABILITY_SEED
        ],
    )


def downgrade() -> None:
    raise NotImplementedError
