"""Saved import mappings and decisions (R107 increment 2, design/bulk-import.md §4, §2 "References that do not exist").

Three new tables, moving no stored row: ``import_sources`` (a named origin of files for one kind), ``import_mappings``
(append-only versions of a source's recipe) and ``import_decisions`` (what was decided about an unknown reference in a
run). Guards: nothing here is ever deleted; mappings and decisions never change; a source changes only its active flag,
version (never backwards) and who and when. DDL below is frozen: this migration never imports current application
metadata.
"""
from alembic import op

revision = 'co0109'
down_revision = 'co0108'
branch_labels = depends_on = None

DDL = ('\nCREATE TABLE import_sources (\n\tid VARCHAR(26) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(200) NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n', '\nCREATE TABLE import_mappings (\n\tid VARCHAR(26) NOT NULL, \n\tsource_id VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\theader_fingerprint VARCHAR(64) NOT NULL, \n\theaders TEXT NOT NULL, \n\tcolumns TEXT NOT NULL, \n\tformats TEXT NOT NULL, \n\tkey_paths TEXT NOT NULL, \n\tmode VARCHAR(8) NOT NULL, \n\ton_unknown VARCHAR(8) NOT NULL, \n\tvalue_maps TEXT NOT NULL, \n\tdefaults TEXT NOT NULL, \n\trun_id VARCHAR(26), \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(source_id) REFERENCES import_sources (id), \n\tFOREIGN KEY(run_id) REFERENCES import_runs (id)\n)\n\n', "\nCREATE TABLE import_decisions (\n\tid VARCHAR(26) NOT NULL, \n\trun_id VARCHAR(26) NOT NULL, \n\tfield VARCHAR(200) NOT NULL, \n\tvalue TEXT NOT NULL, \n\tvalue_key VARCHAR(400) NOT NULL, \n\taction VARCHAR(8) NOT NULL, \n\ttarget TEXT, \n\tinput TEXT, \n\tremembered BOOLEAN NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_import_decision_action CHECK (action IN ('map', 'create', 'skip')), \n\tFOREIGN KEY(run_id) REFERENCES import_runs (id)\n)\n\n")
INDEXES = ('CREATE INDEX ix_import_mappings_fingerprint ON import_mappings (header_fingerprint)', 'CREATE UNIQUE INDEX ux_import_decisions_value ON import_decisions (run_id, field, value_key)', 'CREATE UNIQUE INDEX ux_import_mappings_version ON import_mappings (source_id, version)', 'CREATE UNIQUE INDEX ux_import_sources_name_key ON import_sources (name_key)')
GUARDS = ("CREATE TRIGGER import_sources_no_delete BEFORE DELETE ON import_sources BEGIN SELECT RAISE(ABORT, 'immutable import sources'); END", "CREATE TRIGGER import_mappings_no_delete BEFORE DELETE ON import_mappings BEGIN SELECT RAISE(ABORT, 'immutable import mappings'); END", "CREATE TRIGGER import_decisions_no_delete BEFORE DELETE ON import_decisions BEGIN SELECT RAISE(ABORT, 'immutable import decisions'); END", "CREATE TRIGGER import_mappings_immutable_update BEFORE UPDATE ON import_mappings BEGIN SELECT RAISE(ABORT, 'a mapping changes only by saving a new version'); END", "CREATE TRIGGER import_decisions_immutable_update BEFORE UPDATE ON import_decisions BEGIN SELECT RAISE(ABORT, 'immutable import decisions'); END", "CREATE TRIGGER import_sources_frozen BEFORE UPDATE ON import_sources WHEN NEW.id IS NOT OLD.id OR NEW.name IS NOT OLD.name OR NEW.name_key IS NOT OLD.name_key OR NEW.kind IS NOT OLD.kind OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by OR NEW.version < OLD.version BEGIN SELECT RAISE(ABORT, 'an import source changes only its active flag and version'); END")
TABLES = ('import_decisions', 'import_mappings', 'import_sources')
# What a test rewinding a head file drops: the guards, then the tables (children first).
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    connection = op.get_bind()
    reserved = set(TABLES) | set(TRIGGERS)
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0109 reserved object already exists')
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
