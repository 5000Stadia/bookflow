"""Barcodes (R104 increment 3): look-up indexes and the code counter.

Two indexes, ``ix_items_barcode`` and ``ix_inventory_bins_barcode``, so a scanned code finds its item
or bin without a table scan, and a ``sequences`` row named ``barcode`` from which `barcode generate`
numbers new EAN-13 codes in the restricted-circulation range (prefix 2). Nothing existing changes:
every barcode already recorded stays, duplicates included (only a new or changed one is checked).
"""
from alembic import op

revision = 'co0094'
down_revision = 'co0092'
branch_labels = depends_on = None
INDEXES = ('CREATE INDEX ix_items_barcode ON items (barcode)',
           'CREATE INDEX ix_inventory_bins_barcode ON inventory_bins (barcode)')
RESERVED = ('ix_items_barcode', 'ix_inventory_bins_barcode')


def upgrade():
    connection = op.get_bind()
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in RESERVED} & existing:
        raise RuntimeError('co0094 reserved object already exists')
    for statement in INDEXES:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql("INSERT INTO sequences (name, next_number, prefix) VALUES ('barcode', 1, '')")


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
