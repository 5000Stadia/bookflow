"""Allow jobs to inherit their preferred delivery method.

Revision ID: co0004
Revises: co0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.util import OrderedSet

revision = "co0004"
down_revision = "co0003"

_RANK = {sa.PrimaryKeyConstraint: 0, sa.UniqueConstraint: 1, sa.CheckConstraint: 2, sa.ForeignKeyConstraint: 3}


def _constraint_key(constraint):
    columns = tuple(column.name for column in getattr(constraint, "columns", ()))
    referred = tuple(element.target_fullname for element in getattr(constraint, "elements", ()))
    text = str(getattr(constraint, "sqltext", ""))
    return (_RANK.get(type(constraint), 9), constraint.name or "", columns, referred, text)


def _in_fixed_order(table):
    """The batch rebuild writes constraints in ``Table.constraints`` iteration order.

    That is a set keyed by object identity, so the rebuilt CREATE TABLE text differed from one
    process to the next. Give the rebuild one fixed order instead.
    """
    table.constraints = OrderedSet(sorted(table.constraints, key=_constraint_key))
    return table


def upgrade() -> None:
    # Revision-local metadata only. SQLite recreates the table while preserving
    # its rows, constraints, indexes, and foreign keys.
    customers = _in_fixed_order(sa.Table("customers", sa.MetaData(), autoload_with=op.get_bind()))
    with op.batch_alter_table("customers", recreate="always", copy_from=customers) as batch:
        batch.alter_column(
            "preferred_delivery_method",
            existing_type=sa.String(8),
            nullable=True,
        )


def downgrade() -> None:
    raise NotImplementedError
