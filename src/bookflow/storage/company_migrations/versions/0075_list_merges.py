"""Merged accounts and items: `list_merges` and the references each merge re-pointed (R117).

The sibling of co0068's `party_merges`: one row per "M is merged into S" for an account or an
item, and one `list_merge_references` row per editable list reference the merge moved to the
survivor, so an undo can put it back. Append-only triggers: neither table loses a row, a merge
changes only by its one undo stamp, and a reference never changes. Insert triggers keep both
entries real and merges one level deep. Nothing existing changes. DDL below is frozen: this
migration never imports current application metadata.
"""
from alembic import op

revision = 'co0075'
down_revision = 'co0071'
branch_labels = depends_on = None
DDL = ("\nCREATE TABLE list_merges (\n\tid VARCHAR(26) NOT NULL, \n\tlist_kind VARCHAR(16) NOT NULL, \n\tmerged_id VARCHAR(26) NOT NULL, \n\tsurvivor_id VARCHAR(26) NOT NULL, \n\tdeactivated_merged BOOLEAN NOT NULL, \n\treason VARCHAR(500) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tundone_at VARCHAR(32), \n\tundone_by VARCHAR(26), \n\tundone_via VARCHAR(16), \n\tundo_reason VARCHAR(500), \n\tundo_audit_event_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_list_merge_kind CHECK (list_kind IN ('account','item')), \n\tCONSTRAINT ck_list_merge_distinct CHECK (merged_id <> survivor_id), \n\tCONSTRAINT ck_list_merge_reason CHECK (length(trim(reason)) > 0), \n\tCONSTRAINT ck_list_merge_undo CHECK ((undone_at IS NULL AND undone_by IS NULL AND undone_via IS NULL AND undo_reason IS NULL AND undo_audit_event_id IS NULL) OR (undone_at IS NOT NULL AND undone_by IS NOT NULL AND undone_via IS NOT NULL AND length(trim(undo_reason)) > 0 AND undo_audit_event_id IS NOT NULL)), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(undo_audit_event_id) REFERENCES audit_events (id)\n)\n\n", 'CREATE INDEX ix_list_merges_live_survivor ON list_merges (list_kind, survivor_id) WHERE undone_at IS NULL', 'CREATE UNIQUE INDEX ux_list_merges_live_merged ON list_merges (list_kind, merged_id) WHERE undone_at IS NULL', "\nCREATE TABLE list_merge_references (\n\tid VARCHAR(26) NOT NULL, \n\tmerge_id VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\ttable_name VARCHAR(64) NOT NULL, \n\trecord_id VARCHAR(26) NOT NULL, \n\tcolumn_name VARCHAR(64) NOT NULL, \n\taction VARCHAR(16) NOT NULL, \n\tbefore_value VARCHAR(26), \n\tafter_value VARCHAR(26), \n\towner_kind VARCHAR(32) NOT NULL, \n\towner_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_list_merge_reference_action CHECK (action IN ('repoint','end')), \n\tCONSTRAINT uq_list_merge_reference_position UNIQUE (merge_id, position), \n\tFOREIGN KEY(merge_id) REFERENCES list_merges (id)\n)\n\n")
TRIGGERS = (
    "CREATE TRIGGER list_merges_no_delete BEFORE DELETE ON list_merges BEGIN SELECT RAISE(ABORT,'list_merges is append-only'); END",
    "CREATE TRIGGER list_merges_undo_only BEFORE UPDATE ON list_merges WHEN OLD.undone_at IS NOT NULL OR NEW.undone_at IS NULL OR NEW.merged_id IS NOT OLD.merged_id OR NEW.survivor_id IS NOT OLD.survivor_id OR NEW.list_kind IS NOT OLD.list_kind OR NEW.deactivated_merged IS NOT OLD.deactivated_merged OR NEW.reason IS NOT OLD.reason OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by OR NEW.created_via IS NOT OLD.created_via OR NEW.audit_event_id IS NOT OLD.audit_event_id OR NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'list_merges changes only by its one undo stamp'); END",
    "CREATE TRIGGER list_merges_entries_exist BEFORE INSERT ON list_merges WHEN (NEW.list_kind='account' AND (NOT EXISTS (SELECT 1 FROM accounts WHERE id=NEW.merged_id) OR NOT EXISTS (SELECT 1 FROM accounts WHERE id=NEW.survivor_id))) OR (NEW.list_kind='item' AND (NOT EXISTS (SELECT 1 FROM items WHERE id=NEW.merged_id) OR NOT EXISTS (SELECT 1 FROM items WHERE id=NEW.survivor_id))) BEGIN SELECT RAISE(ABORT,'list merge names an entry that does not exist'); END",
    "CREATE TRIGGER list_merges_one_level BEFORE INSERT ON list_merges WHEN EXISTS (SELECT 1 FROM list_merges m WHERE m.list_kind=NEW.list_kind AND m.undone_at IS NULL AND (m.merged_id=NEW.survivor_id OR m.survivor_id=NEW.merged_id)) BEGIN SELECT RAISE(ABORT,'a merge never chains: its survivor is not merged away and nothing is merged into the entry it merges'); END",
    "CREATE TRIGGER list_merge_references_no_update BEFORE UPDATE ON list_merge_references BEGIN SELECT RAISE(ABORT,'list_merge_references is append-only'); END",
    "CREATE TRIGGER list_merge_references_no_delete BEFORE DELETE ON list_merge_references BEGIN SELECT RAISE(ABORT,'list_merge_references is append-only'); END",
)


def upgrade():
    connection = op.get_bind()
    for statement in (*DDL, *TRIGGERS):
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0075 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
