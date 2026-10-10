"""Opening 1099 amounts: one `vendor_1099_openings` row per vendor and year (R179).

What a vendor was paid in a year before the company's books began here, set by `vendor 1099-opening`
and by the move-in from the old books' 1099 Summary, so the year's 1099 summary is whole. One new
table; nothing existing changes. DDL below is frozen: this migration never imports current
application metadata.
"""
from alembic import op

revision = 'co0071'
down_revision = 'co0068'
branch_labels = depends_on = None
DDL = (
    "\nCREATE TABLE vendor_1099_openings (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tvendor_id VARCHAR(26) NOT NULL, \n\tyear INTEGER NOT NULL, \n\tas_of VARCHAR(10) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_vendor_1099_opening_year UNIQUE (vendor_id, year), \n\tCONSTRAINT ck_vendor_1099_opening_year CHECK (year BETWEEN 1900 AND 9999), \n\tCONSTRAINT ck_vendor_1099_opening_as_of_year CHECK (substr(as_of, 1, 4) = printf('%04d', year)), \n\tCONSTRAINT ck_vendor_1099_opening_amount CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units >= 0), \n\tCONSTRAINT ck_vendor_1099_opening_version CHECK (version >= 1), \n\tFOREIGN KEY(vendor_id) REFERENCES vendors (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n",
    'CREATE INDEX ix_vendor_1099_openings_as_of ON vendor_1099_openings (as_of)',
)


def upgrade():
    connection = op.get_bind()
    for statement in DDL:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0071 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
