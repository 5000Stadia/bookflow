"""Preserving additive captured sales-tax policy and immutable tax attribution."""
from alembic import op
import sqlalchemy as sa

revision = 'co0015'
down_revision = 'co0014'


def upgrade():
    db = op.get_bind()
    objects = set(db.exec_driver_sql("SELECT name FROM sqlite_master").scalars())
    if objects.intersection(NAMES) or any(name.startswith(tuple(n + '_' for n in NAMES)) for name in objects):
        raise RuntimeError('co0015 reserved tax object already exists')
    columns = {row[1] for row in db.exec_driver_sql('PRAGMA table_info(company_info)')}
    if 'sales_tax_calculation' in columns:
        raise RuntimeError('co0015 policy column already exists')
    op.add_column('company_info', sa.Column('sales_tax_calculation', sa.String(32), sa.CheckConstraint("sales_tax_calculation IN ('line_component_half_even','line_combined_half_up','invoice_combined_half_up')", name='ck_company_tax_calculation'), nullable=False,
        server_default='line_component_half_even'))
    for statement in DDL + GUARDS:
        db.exec_driver_sql(statement)
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0015 foreign key check failed')


def downgrade():
    raise NotImplementedError

NAMES = ('sales_tax_line_keys', 'sales_tax_attributions', 'sales_tax_attribution_lines', 'work_tax_line_keys', 'work_tax_attributions', 'work_tax_attribution_lines')

DDL = ("CREATE TABLE sales_tax_line_keys (\n\tline_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\ttax_ordinal BIGINT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (line_id), \n\tCONSTRAINT ck_sales_tax_line_keys_ordinal CHECK (typeof(tax_ordinal) = 'integer' AND tax_ordinal > 0), \n\tCONSTRAINT uq_sales_tax_line_keys_ordinal UNIQUE (transaction_id, tax_ordinal), \n\tCONSTRAINT uq_sales_tax_line_keys_owner UNIQUE (transaction_id, line_id, tax_ordinal), \n\tFOREIGN KEY(transaction_id, line_id) REFERENCES document_line_identities (transaction_id, id)\n)", "CREATE TABLE sales_tax_attributions (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tfacts_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT ck_sales_tax_attributions_facts CHECK (CASE WHEN json_valid(facts_snapshot) THEN json_type(facts_snapshot) = 'object' ELSE 0 END), \n\tCONSTRAINT uq_sales_tax_attributions_owner UNIQUE (transaction_id, revision_id), \n\tFOREIGN KEY(transaction_id, revision_id) REFERENCES sales_profiles (transaction_id, revision_id)\n)", 'CREATE TABLE sales_tax_attribution_lines (\n\tdocument_line_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tline_id VARCHAR(26) NOT NULL, \n\ttax_ordinal BIGINT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (document_line_id), \n\tCONSTRAINT uq_sales_tax_attribution_lines_ordinal UNIQUE (revision_id, tax_ordinal), \n\tFOREIGN KEY(transaction_id, revision_id) REFERENCES sales_tax_attributions (transaction_id, revision_id), \n\tFOREIGN KEY(transaction_id, line_id, tax_ordinal) REFERENCES sales_tax_line_keys (transaction_id, line_id, tax_ordinal), \n\tFOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id)\n)', "CREATE TABLE work_tax_line_keys (\n\tline_id VARCHAR(26) NOT NULL, \n\tdocument_id VARCHAR(26) NOT NULL, \n\ttax_ordinal BIGINT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (line_id), \n\tCONSTRAINT ck_work_tax_line_keys_ordinal CHECK (typeof(tax_ordinal) = 'integer' AND tax_ordinal > 0), \n\tCONSTRAINT uq_work_tax_line_keys_ordinal UNIQUE (document_id, tax_ordinal), \n\tCONSTRAINT uq_work_tax_line_keys_owner UNIQUE (document_id, line_id, tax_ordinal), \n\tFOREIGN KEY(document_id, line_id) REFERENCES work_line_identities (document_id, id)\n)", "CREATE TABLE work_tax_attributions (\n\trevision_id VARCHAR(26) NOT NULL, \n\tdocument_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tfacts_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT ck_work_tax_attributions_facts CHECK (CASE WHEN json_valid(facts_snapshot) THEN json_type(facts_snapshot) = 'object' ELSE 0 END), \n\tCONSTRAINT uq_work_tax_attributions_owner UNIQUE (document_id, revision_id), \n\tFOREIGN KEY(document_id, revision_id) REFERENCES work_revisions (document_id, id)\n)", 'CREATE TABLE work_tax_attribution_lines (\n\twork_line_id VARCHAR(26) NOT NULL, \n\tdocument_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tline_id VARCHAR(26) NOT NULL, \n\ttax_ordinal BIGINT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (work_line_id), \n\tCONSTRAINT uq_work_tax_attribution_lines_ordinal UNIQUE (revision_id, tax_ordinal), \n\tFOREIGN KEY(document_id, revision_id) REFERENCES work_tax_attributions (document_id, revision_id), \n\tFOREIGN KEY(document_id, line_id, tax_ordinal) REFERENCES work_tax_line_keys (document_id, line_id, tax_ordinal), \n\tFOREIGN KEY(document_id, revision_id, work_line_id) REFERENCES work_lines (document_id, revision_id, id)\n)')

GUARDS = ("CREATE TRIGGER sales_tax_line_keys_no_update BEFORE UPDATE ON sales_tax_line_keys BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER sales_tax_line_keys_no_delete BEFORE DELETE ON sales_tax_line_keys BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER sales_tax_attributions_no_update BEFORE UPDATE ON sales_tax_attributions BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER sales_tax_attributions_no_delete BEFORE DELETE ON sales_tax_attributions BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER sales_tax_attribution_lines_no_update BEFORE UPDATE ON sales_tax_attribution_lines BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER sales_tax_attribution_lines_no_delete BEFORE DELETE ON sales_tax_attribution_lines BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER work_tax_line_keys_no_update BEFORE UPDATE ON work_tax_line_keys BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER work_tax_line_keys_no_delete BEFORE DELETE ON work_tax_line_keys BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER work_tax_attributions_no_update BEFORE UPDATE ON work_tax_attributions BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER work_tax_attributions_no_delete BEFORE DELETE ON work_tax_attributions BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER work_tax_attribution_lines_no_update BEFORE UPDATE ON work_tax_attribution_lines BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END", "CREATE TRIGGER work_tax_attribution_lines_no_delete BEFORE DELETE ON work_tax_attribution_lines BEGIN SELECT RAISE(ABORT, 'immutable tax history'); END")

# A revision-local envelope must name the very same stable line as its tax key.
GUARDS += tuple(
    f"CREATE TRIGGER {prefix}_tax_attribution_lines_owner BEFORE INSERT ON {prefix}_tax_attribution_lines "
    f"WHEN NOT EXISTS (SELECT 1 FROM {lines} WHERE id=NEW.{key} AND {owner}=NEW.{owner} "
    f"AND revision_id=NEW.revision_id AND line_id=NEW.line_id) "
    "BEGIN SELECT RAISE(ABORT, 'tax attribution line identity differs'); END"
    for prefix, lines, key, owner in (
        ('sales', 'document_lines', 'document_line_id', 'transaction_id'),
        ('work', 'work_lines', 'work_line_id', 'document_id')))
