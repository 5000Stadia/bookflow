"""co0122: stored POS tap boxes and home style. Frozen DDL, on co0121."""
from alembic import op

revision = 'co0122'
down_revision = 'co0121'
branch_labels = depends_on = None
TABLES = ('pos_boxes',)
TRIGGERS = ()
COLUMNS = ('pos_home_view_style', 'pos_layout_version')
DDL = ("\nCREATE TABLE pos_boxes (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tparent_id VARCHAR(26), \n\tposition INTEGER NOT NULL, \n\tkind VARCHAR(8) NOT NULL, \n\titem_id VARCHAR(26), \n\tlabel VARCHAR(400), \n\tcolour VARCHAR(7), \n\tpicture_attachment_id VARCHAR(26), \n\tview_style VARCHAR(12) DEFAULT 'block' NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_pos_boxes_kind CHECK (kind IN ('group','item')), \n\tCONSTRAINT ck_pos_boxes_contents CHECK ((kind='group' AND item_id IS NULL AND label IS NOT NULL) OR (kind='item' AND item_id IS NOT NULL AND picture_attachment_id IS NULL)), \n\tCONSTRAINT ck_pos_boxes_style CHECK (view_style IN ('block','list','compact','photo')), \n\tCONSTRAINT ck_pos_boxes_position CHECK (position >= 0), \n\tFOREIGN KEY(parent_id) REFERENCES pos_boxes (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(picture_attachment_id) REFERENCES attachments (id) ON DELETE RESTRICT\n)\n\n", 'CREATE INDEX ix_pos_boxes_parent_position ON pos_boxes (parent_id, position)', 'CREATE INDEX ix_pos_boxes_picture ON pos_boxes (picture_attachment_id)')


def upgrade():
    conn = op.get_bind()
    existing = {r[0] for r in conn.exec_driver_sql('SELECT name FROM sqlite_schema')}
    columns = {r[1] for r in conn.exec_driver_sql('PRAGMA table_info(company_info)')}
    if 'pos_boxes' in existing or set(COLUMNS) & columns:
        raise RuntimeError('co0122 reserved object already exists')
    for statement in DDL:
        conn.exec_driver_sql(statement)
    conn.exec_driver_sql("ALTER TABLE company_info ADD COLUMN pos_home_view_style VARCHAR(12) NOT NULL DEFAULT 'block'")
    conn.exec_driver_sql("ALTER TABLE company_info ADD COLUMN pos_layout_version INTEGER NOT NULL DEFAULT 0")


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
