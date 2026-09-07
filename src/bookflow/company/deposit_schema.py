"""Private deposit storage: owned provenance, immutable events and current claims."""
import sqlalchemy as sa


def define_tables(metadata, column, table, common):
    C, T = column, table
    def ident(name, description, nullable=False, primary_key=False):
        return C(name, sa.String(26), description, nullable=nullable, primary_key=primary_key)
    def text(name, description, nullable=False):
        return C(name, sa.Text, description, nullable=nullable)
    def integer(name, description):
        return C(name, sa.BigInteger, description, nullable=False)
    def check(sql, name):
        return sa.CheckConstraint(sql, name='ck_deposit_' + name)
    def positive(name):
        return check(f"typeof({name}) = 'integer' AND {name} > 0", name)
    def obj(name):
        return check(f"CASE WHEN json_valid({name}) THEN json_type({name}) = 'object' ELSE 0 END", name)
    def fk(columns, targets, name):
        return sa.ForeignKeyConstraint(columns, targets, name='fk_deposit_' + name)
    def created():
        return [text('created_at', 'UTC recorded time.'), ident('created_by', 'Execution principal.'),
                text('created_via', 'Execution interface.'), C('audit_event_id', sa.String(26), 'Owning audit event.', sa.ForeignKey('audit_events.id'), nullable=False)]
    profiles = T('deposit_profiles',
        ident('revision_id', 'Owning immutable deposit revision.', primary_key=True), ident('transaction_id', 'Stable deposit.'),
        text('type', 'Fixed deposit discriminator.'), ident('bank_account_id', 'Captured main bank account.'),
        integer('posting_total', 'Positive funding P.'), integer('subtotal', 'Net subtotal T.'), integer('bank_total', 'Net bank B.'),
        integer('cash_back', 'Explicit cash back C.'), text('facts_snapshot', 'Complete typed deposit effect and captured facts.'),
        *created(), positive('posting_total'), positive('subtotal'), obj('facts_snapshot'),
        check("type = 'deposit'", 'profile_type'),
        check("typeof(bank_total) = 'integer' AND bank_total >= 0 AND typeof(cash_back) = 'integer' AND cash_back >= 0 AND bank_total = subtotal - cash_back", 'totals'),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_deposit_profile_owner'),
        fk(['transaction_id', 'revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id'], 'profile_revision'),
        fk(['transaction_id', 'type'], ['transactions.id', 'transactions.type'], 'profile_type'),
        fk(['bank_account_id'], ['accounts.id'], 'profile_bank'), description='Immutable complete deposit header facts; no lifecycle activation.')
    row_keys = T('deposit_row_keys', ident('id', 'Durable row occurrence.', primary_key=True), ident('transaction_id', 'Owning deposit.'),
        ident('line_id', 'Owned commercial envelope identity.'), integer('ordinal', 'Permanent noncompacting row order.'),
        text('kind', 'source, additional or header.'), *created(), positive('ordinal'),
        check("kind IN ('source','additional','header')", 'row_kind'),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_deposit_row_owner'),
        sa.UniqueConstraint('transaction_id', 'ordinal', name='uq_deposit_row_ordinal'),
        fk(['transaction_id', 'line_id'], ['document_line_identities.transaction_id', 'document_line_identities.id'], 'row_line'),
        description='Permanent deposit-owned row occurrences; removing and re-adding creates a new identity.')
    component_keys = T('deposit_component_keys', ident('id', 'Permanent semantic occurrence.', primary_key=True),
        ident('transaction_id', 'Owning deposit.'), ident('row_id', 'Owned deposit row occurrence.'),
        integer('ordinal', 'Additional row component is zero; source and header occurrences are positive.'), text('kind', 'payment, sale_net, sale_tax, additional or header.'),
        text('semantic_identity', 'Permanent payment key, sale line, additional row or header identity.'),
        text('tax_item_id', 'Tax item for sale_tax; empty otherwise.'), *created(),
        check("typeof(ordinal) = 'integer' AND ((kind = 'additional' AND ordinal = 0) OR (kind <> 'additional' AND ordinal > 0))", 'ordinal'),
        check("kind IN ('payment','sale_net','sale_tax','additional','header') AND ((kind = 'sale_tax' AND length(tax_item_id)>0) OR (kind <> 'sale_tax' AND tax_item_id = ''))", 'component_key'),
        sa.UniqueConstraint('transaction_id','row_id','ordinal',name='uq_deposit_component_key_order'),
        fk(['transaction_id','row_id'],['deposit_row_keys.transaction_id','deposit_row_keys.id'],'component_key_row'),
        description='Immutable semantic occurrences; retained zero identities keep order and reintroduced identities append.')
    components = T('deposit_components', ident('id', 'Revision-local funding component.', primary_key=True),
        ident('transaction_id', 'Owning deposit.'), ident('revision_id', 'Owning revision.'), ident('document_line_id', 'Deposit-owned envelope.'),
        ident('row_id', 'Durable deposit row occurrence.'), integer('component_ordinal', 'Owned component occurrence: zero for additional rows; positive for source and header.'),
        text('role', 'funding, offset or cash_back owned component.'),
        integer('capacity', 'Positive component amount; only funding supplies allocation capacity.'), text('currency', 'Home currency.'),
        text('facts_snapshot', 'Typed cash provenance, semantic key, captured source and occurrence facts.'),
        ident('source_transaction_id', 'Source receipt, null for additional funding.', True),
        ident('source_revision_id', 'Exact captured source revision.', True),
        ident('source_document_line_id', 'Receipt-owned envelope, never the deposit envelope.', True),
        ident('source_posting_line_id', 'Captured UF debit line.', True),
        ident('source_attribution_id', 'Captured UF source attribution.', True),
        *created(), positive('capacity'),
        check("typeof(component_ordinal) = 'integer' AND component_ordinal >= 0 AND (source_transaction_id IS NULL OR component_ordinal > 0) AND (role <> 'cash_back' OR component_ordinal > 0)", 'component_ordinal'), obj('facts_snapshot'),
        check("role IN ('funding','offset','cash_back') AND (source_transaction_id IS NULL OR role = 'funding')", 'component_role'),
        check('(source_transaction_id IS NULL AND source_revision_id IS NULL AND source_document_line_id IS NULL AND source_posting_line_id IS NULL AND source_attribution_id IS NULL) OR (source_transaction_id IS NOT NULL AND source_revision_id IS NOT NULL AND source_document_line_id IS NOT NULL AND source_posting_line_id IS NOT NULL AND source_attribution_id IS NOT NULL)', 'component_source_presence'),
        fk(['source_transaction_id', 'source_revision_id', 'source_document_line_id'], ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], 'component_source_line'),
        fk(['source_transaction_id', 'source_posting_line_id'], ['posting_lines.transaction_id', 'posting_lines.id'], 'component_source_posting'),
        fk(['source_transaction_id', 'source_attribution_id'], ['posting_line_sources.transaction_id', 'posting_line_sources.id'], 'component_source_attribution'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'document_line_id', 'id', name='uq_deposit_component_attribution'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'id', name='uq_deposit_component_owner'),
        sa.UniqueConstraint('revision_id', 'row_id', 'component_ordinal', name='uq_deposit_component_occurrence'),
        fk(['transaction_id', 'revision_id'], ['deposit_profiles.transaction_id', 'deposit_profiles.revision_id'], 'component_profile'),
        fk(['transaction_id', 'row_id'], ['deposit_row_keys.transaction_id', 'deposit_row_keys.id'], 'component_row'),
        fk(['transaction_id','row_id','component_ordinal'], ['deposit_component_keys.transaction_id','deposit_component_keys.row_id','deposit_component_keys.ordinal'], 'component_order'),
        fk(['transaction_id', 'revision_id', 'document_line_id'], ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], 'component_envelope'),
        description='Owned funding attribution; foreign receipt line IDs live only in captured provenance.')
    cells = T('deposit_cash_cells', ident('id', 'Immutable allocation.', primary_key=True), ident('transaction_id', 'Owning deposit.'),
        ident('revision_id', 'Owning revision.'), ident('component_id', 'Owned positive funding component.'),
        ident('bucket_row_id', 'Owned destination header or additional row.'), text('bucket', 'main_bank, cash_back or additional.'),
        integer('amount_minor_units', 'Positive allocation.'), text('currency', 'Home currency.'), *created(), positive('amount_minor_units'),
        check("bucket IN ('main_bank','cash_back','additional')", 'bucket'),
        sa.UniqueConstraint('revision_id', 'component_id', 'bucket', 'bucket_row_id', name='uq_deposit_cell'),
        fk(['transaction_id', 'revision_id', 'component_id'], ['deposit_components.transaction_id', 'deposit_components.revision_id', 'deposit_components.id'], 'cell_component'),
        fk(['transaction_id', 'bucket_row_id'], ['deposit_row_keys.transaction_id', 'deposit_row_keys.id'], 'cell_bucket'),
        description='Exact sequential largest-remainder funding allocations with retained source dimensions.')
    membership = T('deposit_memberships', ident('id', 'Immutable claim or exact release.', primary_key=True),
        text('kind', 'claim or release.'), ident('transaction_id', 'Owning deposit.'), ident('revision_id', 'Deposit revision.'),
        ident('batch_id', 'Deposit accounting batch.'), ident('row_id', 'Deposit source occurrence.'),
        ident('source_transaction_id', 'Whole receipt identity.'), ident('source_revision_id', 'Exact source revision.'),
        ident('source_batch_id', 'Exact source business batch.'), integer('amount_minor_units', 'Whole source cash.'),
        text('currency', 'Home currency.'), text('source_date', 'Captured receipt date.'), text('facts_snapshot', 'Typed captured source facts.'),
        ident('reverses_membership_id', 'Exact claim released.', True), *created(), positive('amount_minor_units'), obj('facts_snapshot'),
        check("(kind = 'claim' AND reverses_membership_id IS NULL) OR (kind = 'release' AND reverses_membership_id IS NOT NULL AND reverses_membership_id <> id)", 'membership_kind'),
        sa.UniqueConstraint('reverses_membership_id', name='uq_deposit_membership_release'),
        sa.UniqueConstraint('id', 'source_transaction_id', 'transaction_id', name='uq_deposit_membership_projection'),
        fk(['transaction_id', 'revision_id'], ['deposit_profiles.transaction_id', 'deposit_profiles.revision_id'], 'membership_profile'),
        fk(['transaction_id', 'batch_id'], ['posting_batches.transaction_id', 'posting_batches.id'], 'membership_batch'),
        fk(['transaction_id', 'row_id'], ['deposit_row_keys.transaction_id', 'deposit_row_keys.id'], 'membership_row'),
        fk(['source_transaction_id', 'source_revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id'], 'membership_source_revision'),
        fk(['source_transaction_id', 'source_batch_id'], ['posting_batches.transaction_id', 'posting_batches.id'], 'membership_source_batch'),
        fk(['reverses_membership_id'], ['deposit_memberships.id'], 'membership_inverse'),
        description='Immutable whole-receipt claim history and exact once-only releases.')
    current = T('deposit_current_memberships', ident('source_transaction_id', 'Uniquely claimed receipt.', primary_key=True),
        ident('transaction_id', 'Current owning deposit.'), ident('membership_id', 'Current unreleased claim.'),
        sa.UniqueConstraint('membership_id', name='uq_deposit_current_claim'),
        fk(['membership_id', 'source_transaction_id', 'transaction_id'], ['deposit_memberships.id', 'deposit_memberships.source_transaction_id', 'deposit_memberships.transaction_id'], 'current_claim'),
        description='Transactional current projection; deletion requires the exact owned release.')
    bank_keys = T('bank_effect_keys', ident('id', 'Stable statement identity.', primary_key=True), ident('transaction_id', 'Owning deposit.'),
        text('role', 'main_bank, cash_back or additional.'), ident('row_id', 'Permanent owned header/additional occurrence.'), *created(),
        check("role IN ('main_bank','cash_back','additional')", 'bank_role'),
        sa.UniqueConstraint('transaction_id', 'role', 'row_id', name='uq_deposit_bank_identity'),
        sa.Index('uq_deposit_main_bank_key','transaction_id',unique=True,sqlite_where=sa.text("role = 'main_bank'")),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_deposit_bank_owner'),
        fk(['transaction_id', 'row_id'], ['deposit_row_keys.transaction_id', 'deposit_row_keys.id'], 'bank_row'),
        description='Account-independent bank-effect identities; inverse batches never create statement keys.')
    bank_versions = T('bank_effect_versions', ident('id', 'Immutable statement version.', primary_key=True), ident('transaction_id', 'Owning deposit.'),
        ident('revision_id', 'Owning deposit revision.'), ident('key_id', 'Stable bank-effect key.'), integer('version', 'Monotonic key version.'),
        ident('batch_id', 'Exact deposit business batch.'),
        text('number', 'Captured document number.'), text('memo', 'Captured document memo.', True),
        ident('account_id', 'Versioned account.'), text('effective_date', 'Business date.'),
        C('active', sa.Boolean, 'Whether this version is a nonzero current business effect.', nullable=False),
        integer('signed_debit', 'Exact signed GL debit.'), integer('statement_amount', 'Account-normal signed amount.'),
        text('currency', 'Home currency.'), *created(), positive('version'),
        check("typeof(signed_debit) = 'integer' AND typeof(statement_amount) = 'integer' AND active IN (0,1) AND ((active = 1 AND signed_debit <> 0 AND statement_amount <> 0) OR (active = 0 AND signed_debit = 0 AND statement_amount = 0))", 'bank_amount'),
        sa.UniqueConstraint('key_id', 'version', name='uq_deposit_bank_version'),
        sa.UniqueConstraint('key_id', 'id', name='uq_deposit_bank_version_owner'),
        fk(['transaction_id', 'batch_id'], ['posting_batches.transaction_id', 'posting_batches.id'], 'bank_batch'),
        fk(['transaction_id', 'key_id'], ['bank_effect_keys.transaction_id', 'bank_effect_keys.id'], 'bank_key'),
        fk(['transaction_id', 'revision_id'], ['deposit_profiles.transaction_id', 'deposit_profiles.revision_id'], 'bank_profile'),
        fk(['account_id'], ['accounts.id'], 'bank_account'), description='Versioned business effects, including inactive zero/void states; no reconciliation certificates.')
    bank_current = T('bank_effect_current', ident('key_id', 'One current version per stable key.', primary_key=True),
        ident('version_id', 'Current immutable version, including inactive state.'),
        fk(['key_id', 'version_id'], ['bank_effect_versions.key_id', 'bank_effect_versions.id'], 'bank_current_version'),
        description='Single current bank-effect version per key; old versions remain immutable.')
    operations = T('deposit_operations', ident('id', 'Permanent deposit-family operation.', primary_key=True),
        text('operation_key', 'Company-wide key shared by every deposit financial verb.'),
        text('command', 'Original private lifecycle command.'), ident('transaction_id', 'Owning deposit.'),
        text('request_hash', 'Canonical typed intent and context hash.'),
        text('request_snapshot', 'Complete immutable submitted request and omission provenance.'),
        text('effect_snapshot', 'Complete typed original lifecycle output.'),
        *created(), obj('request_snapshot'), obj('effect_snapshot'),
        sa.UniqueConstraint('operation_key', name='uq_deposit_operation_key'),
        sa.UniqueConstraint('id','transaction_id', name='uq_deposit_operation_owner'),
        check("command IN ('deposit post','deposit update','deposit void')", 'operation_command'),
        check("length(operation_key) BETWEEN 1 AND 128 AND length(request_hash) = 64", 'operation_request'),
        fk(['transaction_id'], ['transactions.id'], 'operation_deposit'),
        description='Permanent exact request/effect receipts, including first successful no-effect operations.')
    operation_targets = T('deposit_operation_targets', ident('operation_id', 'Owning permanent receipt.', primary_key=True),
        ident('transaction_id', 'Complete historical authority target.', primary_key=True),
        fk(['operation_id'], ['deposit_operations.id'], 'operation_target_receipt'),
        fk(['transaction_id'], ['transactions.id'], 'operation_target_transaction'),
        description='Immutable complete transaction-root authority index; never a selected-page subset.')
    operation_items = T('deposit_operation_items', ident('operation_id', 'Owning receipt.'),
        text('kind', 'Typed complete effect or request collection.'), integer('ordinal', 'Zero-based position in the immutable collection.'),
        text('facts_snapshot', 'One complete object in the original collection.'), obj('facts_snapshot'),
        check("typeof(ordinal) = 'integer' AND ordinal >= 0", 'operation_item_ordinal'),
        check("kind IN ('request_sources','request_additional','memberships','document_changes','cash_allocations','bank_changes')", 'operation_item_kind'),
        sa.PrimaryKeyConstraint('operation_id','kind','ordinal'),
        fk(['operation_id'], ['deposit_operations.id'], 'operation_item_receipt'),
        description='All immutable request/effect rows, independent of mutable drafts and current labels.')
    return {t.name: t for t in (profiles,row_keys,component_keys,components,cells,membership,current,bank_keys,bank_versions,bank_current,operations,operation_targets,operation_items)}
