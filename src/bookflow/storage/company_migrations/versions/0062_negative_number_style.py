"""How the company shows a negative amount: with a minus sign or in parentheses.

``company_info`` gains ``negative_number_style``; every existing company keeps the minus sign
it has always shown. Additive: no table is rebuilt and no value is rewritten.
"""
from alembic import op
import sqlalchemy as sa

revision = 'co0062'
down_revision = 'co0061'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    columns = {row[1].lower() for row in db.exec_driver_sql('PRAGMA table_xinfo(company_info)')}
    if 'negative_number_style' in columns:
        raise RuntimeError('co0062 negative number column already exists')
    op.add_column('company_info', sa.Column(
        'negative_number_style', sa.String(12),
        sa.CheckConstraint("negative_number_style IN ('minus','parentheses')", name='ck_company_negative_number_style'),
        nullable=False, server_default='minus'))


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
