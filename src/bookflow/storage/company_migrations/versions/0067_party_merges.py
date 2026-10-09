"""Merged customers and vendors: one `party_merges` row per "B is merged into A" (R117).

One new table with append-only triggers: a row is never deleted, and its only change is the
single undo stamp that ends the merge. Insert triggers keep both entries real and keep merges
one level deep. Nothing existing changes. DDL below is frozen: this migration never imports
current application metadata.
"""
from alembic import op

revision = 'co0067'
down_revision = 'co0066'
branch_labels = depends_on = None
DDL = ("\nCREATE TABLE party_merges (\n\tid VARCHAR(26) NOT NULL, \n\tparty_kind VARCHAR(16) NOT NULL, \n\tmerged_id VARCHAR(26) NOT NULL, \n\tsurvivor_id VARCHAR(26) NOT NULL, \n\tdeactivated_merged BOOLEAN NOT NULL, \n\treason VARCHAR(500) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tundone_at VARCHAR(32), \n\tundone_by VARCHAR(26), \n\tundone_via VARCHAR(16), \n\tundo_reason VARCHAR(500), \n\tundo_audit_event_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_party_merge_kind CHECK (party_kind IN ('customer','vendor')), \n\tCONSTRAINT ck_party_merge_distinct CHECK (merged_id <> survivor_id), \n\tCONSTRAINT ck_party_merge_reason CHECK (length(trim(reason)) > 0), \n\tCONSTRAINT ck_party_merge_undo CHECK ((undone_at IS NULL AND undone_by IS NULL AND undone_via IS NULL AND undo_reason IS NULL AND undo_audit_event_id IS NULL) OR (undone_at IS NOT NULL AND undone_by IS NOT NULL AND undone_via IS NOT NULL AND length(trim(undo_reason)) > 0 AND undo_audit_event_id IS NOT NULL)), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(undo_audit_event_id) REFERENCES audit_events (id)\n)\n\n", 'CREATE INDEX ix_party_merges_live_survivor ON party_merges (party_kind, survivor_id) WHERE undone_at IS NULL', 'CREATE UNIQUE INDEX ux_party_merges_live_merged ON party_merges (party_kind, merged_id) WHERE undone_at IS NULL')
TRIGGERS = ("CREATE TRIGGER party_merges_no_delete BEFORE DELETE ON party_merges BEGIN SELECT RAISE(ABORT,'party_merges is append-only'); END", "CREATE TRIGGER party_merges_undo_only BEFORE UPDATE ON party_merges WHEN OLD.undone_at IS NOT NULL OR NEW.undone_at IS NULL OR NEW.merged_id IS NOT OLD.merged_id OR NEW.survivor_id IS NOT OLD.survivor_id OR NEW.party_kind IS NOT OLD.party_kind OR NEW.deactivated_merged IS NOT OLD.deactivated_merged OR NEW.reason IS NOT OLD.reason OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by OR NEW.created_via IS NOT OLD.created_via OR NEW.audit_event_id IS NOT OLD.audit_event_id OR NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'party_merges changes only by its one undo stamp'); END", "CREATE TRIGGER party_merges_entries_exist BEFORE INSERT ON party_merges WHEN (NEW.party_kind='customer' AND (NOT EXISTS (SELECT 1 FROM customers WHERE id=NEW.merged_id) OR NOT EXISTS (SELECT 1 FROM customers WHERE id=NEW.survivor_id))) OR (NEW.party_kind='vendor' AND (NOT EXISTS (SELECT 1 FROM vendors WHERE id=NEW.merged_id) OR NOT EXISTS (SELECT 1 FROM vendors WHERE id=NEW.survivor_id))) BEGIN SELECT RAISE(ABORT,'party merge names an entry that does not exist'); END", "CREATE TRIGGER party_merges_one_level BEFORE INSERT ON party_merges WHEN EXISTS (SELECT 1 FROM party_merges m WHERE m.party_kind=NEW.party_kind AND m.undone_at IS NULL AND (m.merged_id=NEW.survivor_id OR m.survivor_id=NEW.merged_id)) BEGIN SELECT RAISE(ABORT,'a merge never chains: its survivor is not merged away and nothing is merged into the entry it merges'); END")


def upgrade():
    connection = op.get_bind()
    for statement in (*DDL, *TRIGGERS):
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0067 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
