"""Item conversions: one `item_conversions` row per item converted to an inventory part (R154).

Written by `item convert`. The item keeps its id and its history; the row records the type it was
and the first day it carries stock. One new table; nothing existing changes. DDL below is frozen:
this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0072'
down_revision = 'co0071'
branch_labels = depends_on = None
DDL = (
    "\nCREATE TABLE item_conversions (\n\tid VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tfrom_type VARCHAR(24) NOT NULL, \n\tto_type VARCHAR(24) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\treason VARCHAR(140) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_item_conversion_item UNIQUE (item_id), \n\tCONSTRAINT ck_item_conversion_from CHECK (from_type IN ('non_inventory_part','other_charge')), \n\tCONSTRAINT ck_item_conversion_to CHECK (to_type = 'inventory_part'), \n\tCONSTRAINT ck_item_conversion_date CHECK (length(effective_date) = 10), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n",
)


def upgrade():
    connection = op.get_bind()
    for statement in DDL:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0072 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
