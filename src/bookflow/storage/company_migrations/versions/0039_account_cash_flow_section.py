"""An account can declare which section of the statement of cash flows it belongs to.

The statement classified every account from the account-type vocabulary, which cannot say
that a fixed asset is accumulated depreciation or that an expense is depreciation expense.
``accounts.cash_flow_section`` is where an account says so for itself. It is nullable and
nothing is backfilled: null means "take the section this account's type gives", which is the
classification every company already had, so no existing statement moves. Closing cash was
never affected by the choice -- sectioning only decides which subtotal a change lands in --
so nothing about an upgraded company's reconciliation changes either.

One ``ALTER TABLE ... ADD COLUMN`` and no rebuild. SQLite appends the column to the stored
definition and carries its CHECK with it, so every existing row, index, trigger and view on
``accounts`` is untouched, and no ``_co0039_accounts`` temporary table is ever created.

DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0039'
down_revision = 'co0038'
branch_labels = None
depends_on = None

TABLE = 'accounts'
COLUMN = 'cash_flow_section'
CONSTRAINT = 'ck_accounts_cash_flow_section'
SECTIONS = ('operating', 'investing', 'financing')
DDL = (
    f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} VARCHAR(16) CONSTRAINT {CONSTRAINT} CHECK "
    f"({COLUMN} IS NULL OR {COLUMN} IN (" + ', '.join(f"'{section}'" for section in SECTIONS) + "))",
)


def upgrade():
    connection = op.get_bind()
    columns = [row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{TABLE}")')]
    if not columns:
        raise RuntimeError('co0039 has no accounts table to extend')
    if COLUMN in columns:
        raise RuntimeError('co0039 reserved column already exists')
    for statement in DDL:
        connection.exec_driver_sql(statement)
    # The column arrives empty on every existing row, which is the fact it records: an
    # account that has declared nothing takes the section its type gives.
    declared = connection.exec_driver_sql(
        f'SELECT count(*) FROM "{TABLE}" WHERE {COLUMN} IS NOT NULL').scalar_one()
    if declared:
        raise RuntimeError('co0039 added a declared cash-flow section to a stored account')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0039 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
