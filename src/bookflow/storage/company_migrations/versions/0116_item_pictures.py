"""co0116. Item pictures (R114 increment 1, design/point-of-sale.md §6, §13).

Two nullable columns on ``items``, moving no stored row: ``picture_attachment_id`` (the item's chosen picture, an
image attachment linked to the item) and ``picture_thumbnail_attachment_id`` (a small copy of it), each a foreign key
to ``attachments`` with RESTRICT, and an index on each. An item with neither set shows its newest linked image, read
when shown, so nothing is written for pictures already attached. Numbered after co0115 (R107 bulk edit, on its own
branch); its ``down_revision`` is re-chained to the head at merge. DDL below is frozen: this migration never imports
current application metadata.
"""
from alembic import op

revision = 'co0116'
down_revision = 'co0114'
branch_labels = depends_on = None

COLUMNS = (
    'ALTER TABLE items ADD COLUMN picture_attachment_id VARCHAR(26) REFERENCES attachments (id) ON DELETE RESTRICT',
    'ALTER TABLE items ADD COLUMN picture_thumbnail_attachment_id VARCHAR(26) REFERENCES attachments (id) '
    'ON DELETE RESTRICT',
)
INDEXES = (
    'CREATE INDEX ix_items_picture_attachment_id ON items (picture_attachment_id)',
    'CREATE INDEX ix_items_picture_thumbnail_attachment_id ON items (picture_thumbnail_attachment_id)',
)
# What a test rewinding this head drops: the indexes, then the columns.
INDEX_NAMES = ('ix_items_picture_attachment_id', 'ix_items_picture_thumbnail_attachment_id')
NAMES = ('picture_attachment_id', 'picture_thumbnail_attachment_id')


def upgrade():
    connection = op.get_bind()
    existing = {row[1] for row in connection.exec_driver_sql("PRAGMA table_xinfo('items')").fetchall()}
    if set(NAMES) & existing:
        raise RuntimeError('co0116 reserved column already exists')
    names = {row[0].casefold() for row in connection.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(INDEX_NAMES):
        raise RuntimeError('co0116 reserved index already exists')
    for statement in (*COLUMNS, *INDEXES):
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
