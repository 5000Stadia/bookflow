"""co0113. Document imports (R107 increment 4, design/bulk-import.md §2, §11).

One column, moving no stored row: ``import_rows.spans``, the file rows a document holds (each its row number and line),
so a refused invoice of three rows is reported, and written to the error file, as its own three rows. Null for a list
row, which is one file row. ``import_rows`` stays immutable (its guard is unchanged); a document's cells are stored as
a list of its rows' cells. DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0113'
down_revision = 'co0112'
branch_labels = depends_on = None

COLUMNS = (('import_rows', 'spans'),)
DDL = ('ALTER TABLE import_rows ADD COLUMN spans TEXT',)


def upgrade():
    connection = op.get_bind()
    present = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info('import_rows')").fetchall()}
    if 'spans' in present:
        raise RuntimeError('co0113 reserved column already exists')
    for statement in DDL:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
