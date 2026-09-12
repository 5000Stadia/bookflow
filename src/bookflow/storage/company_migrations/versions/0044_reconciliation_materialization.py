"""Statement effect materialization: the posting-write fence, the money-out key, the backfill.

``co0022`` shipped the reconciliation tables empty and said so: nothing wrote them, and a company
file could hold years of posted history with not one effect row. This revision installs what
changes that, and repairs one thing ``co0022`` could not have known.

**The fence.** More than fifty modules under ``company/`` write ``posting_batches``, each through
its own persist loop, so there is no Python function every posting write passes through -- but
there is one table, and a trigger on it records the owning document in
``statement_effect_pending``. ``reconciliation_materialization.drain`` empties that queue inside
the writing command. A row that outlives a command is a posting write that reached the database
without materializing, which is the defect this arrangement exists to make loud. A writer added
tomorrow is covered without being told: what makes something a posting write is that it inserts a
posting batch.

**The money-out key.** ``co0022`` froze ``reconciliation_keys`` admitting five producers, each
naming an entered commercial line. The statement adapters that landed afterwards gave the
money-out family -- a bill payment, a customer refund, a sales tax remittance -- a statement
effect whose component is the *document*, because what a bill payment splits across is bills and
every one of them is attributed to the same single funding leg. Those effects could therefore be
derived and not stored: the CHECK refused the producer, and the commercial foreign key pointed at
a document line that does not exist for them. Left alone, every bank account that has ever paid a
bill would fail ``reconciliation_preparation``'s general-ledger equality and be unreconcilable.

So ``reconciliation_keys`` and ``reconciliation_effect_versions`` are rebuilt with a third key
shape: no commercial line, no bank effect key, the document itself. Both tables are empty in every
shipped file -- ``co0022`` asserted it on creation and nothing has written them since -- and this
migration checks that before it drops anything rather than assuming it. Their indexes and triggers
go with them and come back, including one new guard proving a funding version's source identity
names the document and the role, which is what the commercial subtype row proves for a line.

**The backfill.** This revision enqueues every document that has ever posted, and
``migrate_company`` drains the queue as soon as the chain finishes, so an existing company file is
materialized by exactly the code that will materialize its next posting -- not by a frozen copy of
the adapters, which would be a second derivation to keep in step with the first. Interrupting the
upgrade costs nothing: the queue survives, and the next command drains it.

Every document is enqueued, not only those with a leg on a bank or credit card account. The
narrower query would be a claim about which producers can carry a statement effect, and a claim
like that written here is the hand-listed set this codebase has got wrong more than a dozen times.
The drain already knows -- it asks the adapters -- and it deletes what owes nothing. What this
costs is one pass over the documents, once, during an upgrade that is already copying the whole
file to a backup.

No existing table gains a column and no row is rewritten. DDL below is frozen: this migration
never imports current application metadata.
"""
from alembic import op

revision = 'co0044'
# co0043 is the head this was written against. The chain is not monotonic -- co0034 -> co0037 ->
# co0035 -> co0036 -> co0038 -> co0039 -> co0040 -> co0041 -> co0043 is what the shipped
# `down_revision` pairs actually say -- so this is read from those pairs, not from the file
# numbering. co0042 is a number claimed on a branch that is not in this tree.
down_revision = 'co0043'
branch_labels = None
depends_on = None

NEW_TABLES = ('statement_effect_pending',)
# The two empty tables this revision replaces, and everything that has to be gone before they can
# be dropped. Dropping a table takes its own indexes and triggers with it.
REBUILT = ('reconciliation_keys', 'reconciliation_effect_versions')
OBJECTS = ('statement_effect_pending', 'statement_effect_pending_posting_batch',
           'statement_effect_pending_bank_effect_version', 'uq_reconciliation_funding_key',
           'reconciliation_effect_versions_funding_anchor')
# Every reconciliation table must be empty for the rebuild to be a rebuild and not a loss.
EMPTY = ('reconciliation_keys', 'reconciliation_effect_versions', 'reconciliation_commercial_versions',
         'reconciliation_deposit_versions', 'reconciliation_effect_legs', 'reconciliation_effect_sources',
         'reconciliation_effect_heads', 'reconciliation_opening_members', 'reconciliation_certificate_members',
         'reconciliation_claims', 'reconciliation_draft_members', 'reconciliation_event_effects',
         'reconciliation_attempt_members')
DDL = ['CREATE TABLE statement_effect_pending (\n\ttransaction_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (transaction_id)\n)']
GUARDS = (
    "CREATE TRIGGER statement_effect_pending_posting_batch AFTER INSERT ON posting_batches "
    "BEGIN INSERT OR IGNORE INTO statement_effect_pending (transaction_id) VALUES (NEW.transaction_id); END",
    "CREATE TRIGGER statement_effect_pending_bank_effect_version AFTER INSERT ON bank_effect_versions "
    "BEGIN INSERT OR IGNORE INTO statement_effect_pending (transaction_id) VALUES (NEW.transaction_id); END",
)
BACKFILL = ('INSERT OR IGNORE INTO statement_effect_pending (transaction_id) '
            'SELECT DISTINCT transaction_id FROM posting_batches')

# The exact replacement text for the two rebuilt tables, their indexes and their triggers,
# compiled once from the metadata this revision shipped with and frozen here.
REBUILT_DDL = ["CREATE TABLE reconciliation_keys (\n\tid TEXT NOT NULL, \n\tproducer TEXT NOT NULL, \n\ttransaction_id TEXT NOT NULL, \n\trole TEXT NOT NULL, \n\tcommercial_line_id TEXT, \n\tdeposit_key_id TEXT, \n\tPRIMARY KEY (id), \n\tUNIQUE (id, transaction_id, producer), \n\tFOREIGN KEY(transaction_id, producer) REFERENCES transactions (id, type), \n\tFOREIGN KEY(transaction_id, commercial_line_id) REFERENCES document_line_identities (transaction_id, id), \n\tFOREIGN KEY(transaction_id, deposit_key_id) REFERENCES bank_effect_keys (transaction_id, id), \n\tCHECK (COALESCE(((producer='journal_entry' AND role IN ('entered')) OR (producer='payment' AND role IN ('cash')) OR (producer='sales_receipt' AND role IN ('control','net')) OR (producer='invoice' AND role IN ('net')) OR (producer='deposit' AND role IN ('main_bank','cash_back','additional')) OR (producer='bill_payment' AND role IN ('funding')) OR (producer='customer_refund' AND role IN ('funding')) OR (producer='sales_tax_payment' AND role IN ('funding'))),0)), \n\tCHECK (COALESCE(((producer IN ('deposit') AND deposit_key_id IS NOT NULL AND commercial_line_id IS NULL) OR (producer IN ('bill_payment','customer_refund','sales_tax_payment') AND deposit_key_id IS NULL AND commercial_line_id IS NULL) OR (producer IN ('journal_entry','payment','sales_receipt','invoice') AND deposit_key_id IS NULL AND commercial_line_id IS NOT NULL)),0)), \n\tCHECK (COALESCE((typeof(id)='text' AND length(id)=26 AND id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'),0)), \n\tCHECK (COALESCE((typeof(producer)='text'),0)), \n\tCHECK (COALESCE((typeof(transaction_id)='text' AND length(transaction_id)=26 AND transaction_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'),0)), \n\tCHECK (COALESCE((typeof(role)='text'),0)), \n\tCHECK (COALESCE((commercial_line_id IS NULL OR (typeof(commercial_line_id)='text' AND length(commercial_line_id)=26 AND commercial_line_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*')),0)), \n\tCHECK (COALESCE((deposit_key_id IS NULL OR (typeof(deposit_key_id)='text' AND length(deposit_key_id)=26 AND deposit_key_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*')),0))\n)", 'CREATE INDEX ix_reconciliation_keys_commercial_line_id ON reconciliation_keys (commercial_line_id)', 'CREATE INDEX ix_reconciliation_keys_transaction_id ON reconciliation_keys (transaction_id)', 'CREATE UNIQUE INDEX uq_reconciliation_commercial_key ON reconciliation_keys (producer, transaction_id, role, commercial_line_id) WHERE commercial_line_id IS NOT NULL', 'CREATE UNIQUE INDEX uq_reconciliation_deposit_key ON reconciliation_keys (deposit_key_id) WHERE deposit_key_id IS NOT NULL', 'CREATE UNIQUE INDEX uq_reconciliation_funding_key ON reconciliation_keys (producer, transaction_id, role) WHERE commercial_line_id IS NULL AND deposit_key_id IS NULL', "CREATE TABLE reconciliation_effect_versions (\n\tid TEXT NOT NULL, \n\tkey_id TEXT NOT NULL, \n\ttransaction_id TEXT NOT NULL, \n\tproducer TEXT NOT NULL, \n\tsource_version TEXT NOT NULL, \n\trevision_id TEXT NOT NULL, \n\tbusiness_batch_id TEXT NOT NULL, \n\ttransition_batch_id TEXT, \n\tsource_audit_event_id TEXT NOT NULL, \n\taccount_id TEXT NOT NULL, \n\taccount_type TEXT NOT NULL, \n\tcurrency TEXT NOT NULL, \n\teffective_date TEXT NOT NULL, \n\tsigned_debit BIGINT NOT NULL, \n\tactive BIGINT NOT NULL, \n\tmovement_snapshot TEXT NOT NULL, \n\tdisplay_snapshot TEXT NOT NULL, \n\tprovenance_snapshot TEXT NOT NULL, \n\tformat_version BIGINT NOT NULL, \n\tcommercial_link_id TEXT, \n\tdeposit_link_id TEXT, \n\tPRIMARY KEY (id), \n\tUNIQUE (key_id, source_version), \n\tUNIQUE (key_id, id), \n\tUNIQUE (id, key_id, account_id), \n\tUNIQUE (id, transaction_id), \n\tUNIQUE (id, transaction_id, producer), \n\tFOREIGN KEY(key_id, transaction_id, producer) REFERENCES reconciliation_keys (id, transaction_id, producer), \n\tFOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tFOREIGN KEY(transaction_id, business_batch_id) REFERENCES posting_batches (transaction_id, id), \n\tFOREIGN KEY(transaction_id, transition_batch_id) REFERENCES posting_batches (transaction_id, id), \n\tFOREIGN KEY(source_audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tCHECK (COALESCE((account_type IN ('bank','credit_card')),0)), \n\tCHECK (COALESCE((format_version=1),0)), \n\tCHECK (COALESCE(((active=1 AND signed_debit<>0) OR (active=0 AND signed_debit=0)),0)), \n\tCHECK (COALESCE(((producer IN ('deposit') AND deposit_link_id=id AND commercial_link_id IS NULL) OR (producer IN ('bill_payment','customer_refund','sales_tax_payment') AND deposit_link_id IS NULL AND commercial_link_id IS NULL) OR (producer IN ('journal_entry','payment','sales_receipt','invoice') AND commercial_link_id=id AND deposit_link_id IS NULL)),0)), \n\tFOREIGN KEY(commercial_link_id) REFERENCES reconciliation_commercial_versions (id) DEFERRABLE INITIALLY DEFERRED, \n\tFOREIGN KEY(deposit_link_id) REFERENCES reconciliation_deposit_versions (id) DEFERRABLE INITIALLY DEFERRED, \n\tCHECK (COALESCE((typeof(id)='text' AND length(id)=26 AND id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'),0)), \n\tCHECK (COALESCE((typeof(key_id)='text' AND length(key_id)=26 AND key_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'),0)), \n\tCHECK (COALESCE((typeof(transaction_id)='text' AND length(transaction_id)=26 AND transaction_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'),0)), \n\tCHECK (COALESCE((typeof(producer)='text'),0)), \n\tCHECK (COALESCE((typeof(source_version)='text'),0)), \n\tCHECK (COALESCE((typeof(revision_id)='text' AND length(revision_id)=26 AND revision_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'),0)), \n\tCHECK (COALESCE((typeof(business_batch_id)='text' AND length(business_batch_id)=26 AND business_batch_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'),0)), \n\tCHECK (COALESCE((transition_batch_id IS NULL OR (typeof(transition_batch_id)='text' AND length(transition_batch_id)=26 AND transition_batch_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*')),0)), \n\tCHECK (COALESCE((typeof(source_audit_event_id)='text' AND length(source_audit_event_id)=26 AND source_audit_event_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'),0)), \n\tCHECK (COALESCE((typeof(account_id)='text' AND length(account_id)=26 AND account_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'),0)), \n\tCHECK (COALESCE((typeof(account_type)='text'),0)), \n\tCHECK (COALESCE((typeof(currency)='text'),0)), \n\tCHECK (COALESCE((typeof(effective_date)='text'),0)), \n\tCHECK (COALESCE((typeof(signed_debit)='integer'),0)), \n\tCHECK (COALESCE((typeof(active)='integer' AND active IN (0,1)),0)), \n\tCHECK (COALESCE((CASE WHEN json_valid(movement_snapshot) THEN json_type(movement_snapshot)='object' ELSE 0 END),0)), \n\tCHECK (COALESCE((CASE WHEN json_valid(display_snapshot) THEN json_type(display_snapshot)='object' ELSE 0 END),0)), \n\tCHECK (COALESCE((CASE WHEN json_valid(provenance_snapshot) THEN json_type(provenance_snapshot)='object' ELSE 0 END),0)), \n\tCHECK (COALESCE((typeof(format_version)='integer' AND format_version>0),0)), \n\tCHECK (COALESCE((commercial_link_id IS NULL OR (typeof(commercial_link_id)='text' AND length(commercial_link_id)=26 AND commercial_link_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*')),0)), \n\tCHECK (COALESCE((deposit_link_id IS NULL OR (typeof(deposit_link_id)='text' AND length(deposit_link_id)=26 AND deposit_link_id NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*')),0))\n)", 'CREATE INDEX ix_reconciliation_effect_versions_account_id ON reconciliation_effect_versions (account_id)', 'CREATE INDEX ix_reconciliation_effect_versions_business_batch_id ON reconciliation_effect_versions (business_batch_id)', 'CREATE INDEX ix_reconciliation_effect_versions_commercial_link_id ON reconciliation_effect_versions (commercial_link_id)', 'CREATE INDEX ix_reconciliation_effect_versions_deposit_link_id ON reconciliation_effect_versions (deposit_link_id)', 'CREATE INDEX ix_reconciliation_effect_versions_key_id ON reconciliation_effect_versions (key_id)', 'CREATE INDEX ix_reconciliation_effect_versions_producer ON reconciliation_effect_versions (producer)', 'CREATE INDEX ix_reconciliation_effect_versions_revision_id ON reconciliation_effect_versions (revision_id)', 'CREATE INDEX ix_reconciliation_effect_versions_source_audit_event_id ON reconciliation_effect_versions (source_audit_event_id)', 'CREATE INDEX ix_reconciliation_effect_versions_transaction_id ON reconciliation_effect_versions (transaction_id)', 'CREATE INDEX ix_reconciliation_effect_versions_transition_batch_id ON reconciliation_effect_versions (transition_batch_id)']

REBUILT_GUARDS = ["CREATE TRIGGER reconciliation_keys_no_update BEFORE UPDATE ON reconciliation_keys WHEN 1 BEGIN SELECT RAISE(ABORT, 'invalid reconciliation storage transition'); END", "CREATE TRIGGER reconciliation_keys_no_delete BEFORE DELETE ON reconciliation_keys WHEN 1 BEGIN SELECT RAISE(ABORT, 'invalid reconciliation storage transition'); END", "CREATE TRIGGER reconciliation_effect_versions_no_update BEFORE UPDATE ON reconciliation_effect_versions WHEN 1 BEGIN SELECT RAISE(ABORT, 'invalid reconciliation storage transition'); END", "CREATE TRIGGER reconciliation_effect_versions_no_delete BEFORE DELETE ON reconciliation_effect_versions WHEN 1 BEGIN SELECT RAISE(ABORT, 'invalid reconciliation storage transition'); END", "CREATE TRIGGER reconciliation_keys_deposit_role BEFORE INSERT ON reconciliation_keys WHEN NEW.producer='deposit' AND NOT EXISTS (SELECT 1 FROM bank_effect_keys k WHERE k.id=NEW.deposit_key_id AND k.transaction_id=NEW.transaction_id AND k.role=NEW.role) BEGIN SELECT RAISE(ABORT, 'invalid reconciliation storage transition'); END", "CREATE TRIGGER reconciliation_effect_versions_funding_anchor BEFORE INSERT ON reconciliation_effect_versions WHEN NEW.producer IN ('bill_payment','customer_refund','sales_tax_payment') AND NOT EXISTS (SELECT 1 FROM reconciliation_keys k WHERE k.id=NEW.key_id AND k.commercial_line_id IS NULL AND k.deposit_key_id IS NULL AND NEW.source_version=COALESCE(NEW.transition_batch_id,NEW.business_batch_id)||':'||k.role||':'||NEW.transaction_id) BEGIN SELECT RAISE(ABORT, 'invalid reconciliation storage transition'); END", "CREATE TRIGGER reconciliation_effect_versions_business_anchor BEFORE INSERT ON reconciliation_effect_versions WHEN NOT EXISTS (SELECT 1 FROM posting_batches b WHERE b.id=NEW.business_batch_id AND b.transaction_id=NEW.transaction_id AND b.revision_id=NEW.revision_id AND b.kind IN ('original','replacement')) OR (NEW.transition_batch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM posting_batches b WHERE b.id=NEW.transition_batch_id AND b.transaction_id=NEW.transaction_id AND b.kind='reversal' AND b.audit_event_id=NEW.source_audit_event_id)) BEGIN SELECT RAISE(ABORT, 'invalid reconciliation storage transition'); END"]


def upgrade():
    connection = op.get_bind()
    reserved = {name.casefold() for name in OBJECTS}
    # Preflight the complete local namespace before any mutation, the way co0022 does.
    # Unrelated local tables, columns, indexes, triggers and views are preserved verbatim.
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        quoted = '"' + name.replace('"', '""') + '"'
        existing = connection.exec_driver_sql('SELECT name FROM ' + quoted + '.sqlite_schema').fetchall()
        if any(row[0].casefold() in reserved for row in existing):
            raise RuntimeError('Statement effect materialization name collision')
    for name in EMPTY:
        if connection.exec_driver_sql('SELECT 1 FROM main."' + name + '" LIMIT 1').fetchone():
            raise RuntimeError('Reconciliation storage is not empty; co0044 cannot rebuild it')
    for name in REBUILT:
        connection.exec_driver_sql('DROP TABLE main."' + name + '"')
    for statement in (*REBUILT_DDL, *REBUILT_GUARDS, *DDL, *GUARDS):
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql(BACKFILL)
    owed = connection.exec_driver_sql('SELECT count(*) FROM main.statement_effect_pending').scalar()
    posted = connection.exec_driver_sql('SELECT count(DISTINCT transaction_id) FROM main.posting_batches').scalar()
    if owed != posted:
        raise RuntimeError('Statement effect backfill did not enqueue every posted document')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0044 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
