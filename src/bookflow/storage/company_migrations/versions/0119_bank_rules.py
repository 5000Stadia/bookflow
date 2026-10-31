"""co0119. Bank feeds, increment 4: rules and learning (R108, design/bank-feeds.md §5, §9).

Three new tables, moving no stored row: ``bank_rules`` (one rule: which lines it fits and what it proposes, its
priority, source, status and, for an auto-add or ignore rule, the automation columns of design/principal-directives.md
§3; never deleted), ``bank_rule_versions`` (each saved version, append-only) and ``bank_rule_declines`` (payee keys
declined as learned rules, per account; append-only). Two columns on ``bank_lines``: ``rule_id`` (the rule that
proposed for or acted on the line) and ``answer`` (what it was added as, JSON). One on ``company_info``:
``bank_rule_learning`` (ask, the default; always; never). Numbered after main's head co0116 with co0117 and co0118
taken by branches in flight; renumbered at merge as needed. DDL below is frozen: this migration never imports current
application metadata.
"""
from alembic import op

revision = 'co0119'
down_revision = 'co0116'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE bank_rules (\n\tid VARCHAR(26) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(200) NOT NULL, \n\taccount_id VARCHAR(26), \n\tdirection VARCHAR(3) NOT NULL, \n\tconditions TEXT NOT NULL, \n\taction VARCHAR(10) NOT NULL, \n\tpayee_type VARCHAR(16), \n\tpayee_id VARCHAR(26), \n\tpayee VARCHAR(1000), \n\tcategory_account_id VARCHAR(26), \n\tclass_id VARCHAR(26), \n\tmemo TEXT, \n\ttransfer_account_id VARCHAR(26), \n\tignore_reason VARCHAR(16), \n\tauto_add INTEGER DEFAULT '0' NOT NULL, \n\tpriority INTEGER NOT NULL, \n\tsource VARCHAR(8) NOT NULL, \n\tstatus VARCHAR(10) NOT NULL, \n\tlearned_from_batch VARCHAR(26), \n\tlearned_key VARCHAR(200), \n\tedited INTEGER DEFAULT '0' NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tstate VARCHAR(8) DEFAULT 'draft' NOT NULL, \n\testablished_by VARCHAR(26), \n\testablished_for VARCHAR(26), \n\testablished_epoch INTEGER, \n\testablished_at VARCHAR(32), \n\tpaused_reason VARCHAR(500), \n\tpaused_at VARCHAR(32), \n\tdraft_reason VARCHAR(500), \n\tfired_slot VARCHAR(32), \n\tfailed_firings INTEGER DEFAULT '0' NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_bank_rule_direction CHECK (direction IN ('in', 'out', 'any')), \n\tCONSTRAINT ck_bank_rule_action CHECK (action IN ('categorise', 'transfer', 'ignore')), \n\tCONSTRAINT ck_bank_rule_source CHECK (source IN ('written', 'learned')), \n\tCONSTRAINT ck_bank_rule_status CHECK (status IN ('active', 'suggested', 'disabled', 'retired')), \n\tCONSTRAINT ck_bank_rule_state CHECK (state IN ('draft', 'live', 'paused')), \n\tCONSTRAINT ck_bank_rule_payee_type CHECK (payee_type IS NULL OR payee_type IN ('customer', 'vendor', 'employee', 'other_name')), \n\tCONSTRAINT ck_bank_rule_ignore_reason CHECK (ignore_reason IS NULL OR ignore_reason IN ('already_entered', 'not_ours', 'duplicate', 'other')), \n\tCONSTRAINT ck_bank_rule_conditions CHECK (json_valid(conditions) AND json_type(conditions) = 'array'), \n\tCONSTRAINT ck_bank_rule_flags CHECK (auto_add IN (0, 1) AND edited IN (0, 1) AND version >= 1), \n\tCONSTRAINT ck_bank_rule_auto_add CHECK (auto_add = 0 OR action = 'categorise'), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(category_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id), \n\tFOREIGN KEY(transfer_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(learned_from_batch) REFERENCES batches (id)\n)\n\n", "\nCREATE TABLE bank_rule_versions (\n\tid VARCHAR(26) NOT NULL, \n\trule_id VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tdefinition TEXT NOT NULL, \n\tchange VARCHAR(16) NOT NULL, \n\tchanged_by VARCHAR(26) NOT NULL, \n\tchanged_via VARCHAR(16) NOT NULL, \n\tchanged_at VARCHAR(32) NOT NULL, \n\treason VARCHAR(500), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_bank_rule_version_json CHECK (json_valid(definition) AND json_type(definition) = 'object'), \n\tFOREIGN KEY(rule_id) REFERENCES bank_rules (id)\n)\n\n", '\nCREATE TABLE bank_rule_declines (\n\tid VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tpayee_key VARCHAR(200) NOT NULL, \n\trule_id VARCHAR(26) NOT NULL, \n\tdeclined_by VARCHAR(26) NOT NULL, \n\tdeclined_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(rule_id) REFERENCES bank_rules (id)\n)\n\n')
INDEXES = ('CREATE INDEX ix_bank_rules_account ON bank_rules (account_id, status)', 'CREATE INDEX ix_bank_rules_learned ON bank_rules (learned_from_batch)', 'CREATE UNIQUE INDEX ux_bank_rules_name ON bank_rules (name_key)', 'CREATE UNIQUE INDEX ux_bank_rule_versions ON bank_rule_versions (rule_id, version)', 'CREATE UNIQUE INDEX ux_bank_rule_declines ON bank_rule_declines (account_id, payee_key)')
GUARDS = ("CREATE TRIGGER bank_rules_no_delete BEFORE DELETE ON bank_rules BEGIN SELECT RAISE(ABORT, 'bank rules are never deleted'); END", "CREATE TRIGGER bank_rules_fixed_update BEFORE UPDATE ON bank_rules WHEN NEW.id IS NOT OLD.id OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by OR NEW.created_via IS NOT OLD.created_via OR NEW.source IS NOT OLD.source OR NEW.learned_from_batch IS NOT OLD.learned_from_batch OR NEW.learned_key IS NOT OLD.learned_key BEGIN SELECT RAISE(ABORT, 'a bank rule keeps how it came'); END", "CREATE TRIGGER bank_rule_versions_no_update BEFORE UPDATE ON bank_rule_versions BEGIN SELECT RAISE(ABORT, 'bank rule versions are append-only'); END", "CREATE TRIGGER bank_rule_versions_no_delete BEFORE DELETE ON bank_rule_versions BEGIN SELECT RAISE(ABORT, 'bank rule versions are append-only'); END", "CREATE TRIGGER bank_rule_declines_no_update BEFORE UPDATE ON bank_rule_declines BEGIN SELECT RAISE(ABORT, 'bank rule declines are append-only'); END", "CREATE TRIGGER bank_rule_declines_no_delete BEFORE DELETE ON bank_rule_declines BEGIN SELECT RAISE(ABORT, 'bank rule declines are append-only'); END")
COLUMNS = (
    ('bank_lines', 'ALTER TABLE bank_lines ADD COLUMN rule_id VARCHAR(26) REFERENCES bank_rules (id)'),
    ('bank_lines', "ALTER TABLE bank_lines ADD COLUMN answer TEXT CONSTRAINT ck_bank_line_answer CHECK (answer IS NULL "
                   "OR (json_valid(answer) AND json_type(answer) = 'object'))"),
    ('company_info', "ALTER TABLE company_info ADD COLUMN bank_rule_learning VARCHAR(8) NOT NULL DEFAULT 'ask' "
                     "CONSTRAINT ck_company_info_bank_rule_learning CHECK (bank_rule_learning IN ('ask', 'always', "
                     "'never'))"),
)
TABLES = ('bank_rule_declines', 'bank_rule_versions', 'bank_rules')
# What a test rewinding this head drops: the guards, the indexes, the columns (rule_id, a foreign key, which DROP
# COLUMN refuses, goes with co0112's bank_lines), then the tables (children first).
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)
INDEX_NAMES = tuple(statement.split(' ON ')[0].split()[-1] for statement in INDEXES)
NAMES = tuple((statement[0], statement[1].split()[5]) for statement in COLUMNS)


def upgrade():
    connection = op.get_bind()
    reserved = set(TABLES) | set(TRIGGERS) | set(INDEX_NAMES)
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0119 reserved object already exists')
    for table, column in NAMES:
        if column in {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info('{table}')").fetchall()}:
            raise RuntimeError('co0119 reserved column already exists')
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)
    for _, statement in COLUMNS:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
