"""The company preference `available_deducts_pending_builds` (R101/R102 with R103), on by default.

When it is on, quantity available deducts what pending assembly builds have reserved, as the anchor
deducts "Quantity reserved for Pending Builds" when that setting is chosen. One column appended to
`company_info`; nothing existing changes. DDL below is frozen.
"""
from alembic import op

revision = 'co0082'
down_revision = 'co0086'
branch_labels = depends_on = None
DDL = ("ALTER TABLE company_info ADD COLUMN available_deducts_pending_builds BOOLEAN DEFAULT '1' NOT NULL",)


def upgrade():
    connection = op.get_bind()
    for statement in DDL:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
