"""Receipt source ownership, immutable settlement history and shared drafts."""
import sqlalchemy as sa


def define_tables(metadata, column, table, common):
    C, T = column, table

    def text(name, description, size=None, nullable=False):
        return C(name, sa.String(size) if size else sa.Text, description, nullable=nullable)

    def ident(name, description, nullable=False, primary_key=False):
        return C(name, sa.String(26), description, nullable=nullable, primary_key=primary_key)

    def integer(name, description, nullable=False):
        return C(name, sa.BigInteger, description, nullable=nullable)

    def check(expression, name):
        return sa.CheckConstraint(expression, name='ck_payment_' + name)

    def positive(name):
        return check(f"typeof({name}) = 'integer' AND {name} > 0", name)

    def obj(name):
        return check(f"CASE WHEN json_valid({name}) THEN json_type({name}) = 'object' ELSE 0 END", name)

    def owner(columns, targets, name, deferred=False):
        return sa.ForeignKeyConstraint(columns, targets, name='fk_payment_' + name,
            deferrable=True if deferred else None, initially='DEFERRED' if deferred else None)

    def created():
        return [text('created_at', 'UTC recorded timestamp.', 32),
                ident('created_by', 'Principal creating this record.'),
                text('created_via', 'Interface creating this record.', 16),
                C('audit_event_id', sa.String(26), 'Owned creation audit event.', sa.ForeignKey('audit_events.id'), nullable=False)]

    payment_profiles = T('payment_profiles',
        ident('revision_id', 'Payment revision owning this profile.', primary_key=True),
        ident('transaction_id', 'Stable payment identity.'),
        text('type', 'Fixed payment type discriminator.', 32),
        C('payer_id', sa.String(26), 'Customer supplying the new cash.', sa.ForeignKey('customers.id'), nullable=False),
        C('ar_account_id', sa.String(26), 'Compatible receivable account.', sa.ForeignKey('accounts.id'), nullable=False),
        C('deposit_account_id', sa.String(26), 'Cash destination account.', sa.ForeignKey('accounts.id'), nullable=False),
        C('payment_method_id', sa.String(26), 'Captured payment method.', sa.ForeignKey('payment_methods.id'), nullable=False),
        text('reference', 'Entered remittance reference.', 128, True),
        text('profile_snapshot', 'Captured payer, lineage, accounts, method and effective preferences.'),
        *created(), obj('profile_snapshot'), check("type = 'payment'", 'profile_type'),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_payment_profile_owner'),
        owner(['transaction_id', 'revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id'], 'profile_revision'),
        owner(['transaction_id', 'type'], ['transactions.id', 'transactions.type'], 'profile_type'),
        description='Immutable revision-owned receipt header facts.')

    payment_component_keys = T('payment_component_keys',
        ident('id', 'Permanent exact-party source identity.', primary_key=True),
        ident('transaction_id', 'Owning payment.'), ident('line_id', 'Stable receipt line.'),
        C('party_id', sa.String(26), 'Permanent credit owner.', sa.ForeignKey('customers.id'), nullable=False),
        C('ar_account_id', sa.String(26), 'Permanent receivable account.', sa.ForeignKey('accounts.id'), nullable=False),
        text('currency', 'Permanent source currency.', 3), *created(),
        sa.UniqueConstraint('transaction_id', 'party_id', 'ar_account_id', 'currency', name='uq_payment_component_dimensions'),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_payment_component_key_owner'),
        owner(['transaction_id', 'line_id'], ['document_line_identities.transaction_id', 'document_line_identities.id'], 'key_line'),
        description='Immutable receipt component ownership; never reassigned after new cash derivation.')

    payment_components = T('payment_components',
        ident('id', 'Revision-local receipt capacity.', primary_key=True),
        ident('transaction_id', 'Owning payment.'), ident('revision_id', 'Owning payment revision.'),
        ident('document_line_id', 'Owned payment envelope.'), ident('component_key_id', 'Permanent component identity.'),
        integer('amount_minor_units', 'Positive capacity of this revision component.'),
        text('currency', 'Home currency.', 3), text('component_snapshot', 'Captured exact-party and account facts.'),
        *created(), positive('amount_minor_units'), obj('component_snapshot'),
        sa.UniqueConstraint('revision_id', 'component_key_id', name='uq_payment_revision_component'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'document_line_id', 'id', name='uq_payment_component_attribution'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'id', name='uq_payment_component_revision'),
        owner(['transaction_id', 'revision_id'], ['payment_profiles.transaction_id', 'payment_profiles.revision_id'], 'component_profile'),
        owner(['transaction_id', 'component_key_id'], ['payment_component_keys.transaction_id', 'payment_component_keys.id'], 'component_key'),
        owner(['transaction_id', 'revision_id', 'document_line_id'], ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], 'component_line'),
        description='Positive immutable capacities for a receipt revision; zero capacity has no row.')

    applications = T('applications',
        ident('id', 'Immutable apply or unapply identity.', primary_key=True),
        text('kind', 'apply or unapply.', 16),
        ident('paying_transaction_id', 'Payment providing capacity.'),
        ident('paid_transaction_id', 'Invoice consuming capacity.'),
        ident('source_component_key_id', 'Exact-party permanent source.'),
        integer('amount_minor_units', 'Positive settlement amount.'), text('currency', 'Settlement currency.', 3),
        text('effective_date', 'Settlement accounting date.', 10),
        ident('reverses_application_id', 'Original apply being fully unapplied.', True), *created(),
        positive('amount_minor_units'),
        check("(kind = 'apply' AND reverses_application_id IS NULL) OR (kind = 'unapply' AND reverses_application_id IS NOT NULL AND reverses_application_id <> id)", 'application_kind'),
        sa.UniqueConstraint('reverses_application_id', name='uq_payment_unapply'),
        owner(['paying_transaction_id', 'source_component_key_id'], ['payment_component_keys.transaction_id', 'payment_component_keys.id'], 'application_source'),
        owner(['paid_transaction_id'], ['transactions.id'], 'application_target'),
        owner(['reverses_application_id'], ['applications.id'], 'application_inverse'),
        sa.Index('ix_applications_payment', 'paying_transaction_id', 'effective_date', 'id'),
        sa.Index('ix_applications_invoice', 'paid_transaction_id', 'effective_date', 'id'),
        description='Immutable dated settlement edges and exact whole-edge inverses; no ledger posting.')

    settlement_line_keys = T('settlement_line_keys',
        ident('id', 'Permanent settlement line ordering identity.', primary_key=True),
        ident('transaction_id', 'Owning invoice.'), ident('line_id', 'Stable invoice line, including retired lines.'),
        integer('ordinal', 'Permanent positive tie-order ordinal.'), *created(), positive('ordinal'),
        sa.UniqueConstraint('transaction_id', 'line_id', name='uq_settlement_line_identity'),
        sa.UniqueConstraint('transaction_id', 'ordinal', name='uq_settlement_line_ordinal'),
        sa.UniqueConstraint('transaction_id', 'line_id', 'ordinal', name='uq_settlement_line_owner'),
        owner(['transaction_id', 'line_id'], ['document_line_identities.transaction_id', 'document_line_identities.id'], 'settlement_line'),
        description='Immutable logical tie order independent of display order and regenerated physical IDs.')

    application_allocations = T('application_allocations',
        ident('id', 'Immutable allocation or reversal identity.', primary_key=True),
        C('application_id', sa.String(26), 'Original apply identity.', sa.ForeignKey('applications.id'), nullable=False),
        text('kind', 'allocation or reversal.', 16), ident('reverses_allocation_id', 'Exact allocation reversed.', True),
        ident('source_transaction_id', 'Source payment.'), ident('source_revision_id', 'Captured payment revision.'),
        ident('source_component_id', 'Captured revision-local source capacity.'), ident('source_posting_source_id', 'Exact source AR attribution.'),
        ident('target_transaction_id', 'Target invoice.'), ident('target_revision_id', 'Captured invoice revision.'),
        ident('target_document_line_id', 'Captured invoice envelope.'), ident('target_line_id', 'Stable target invoice line.'),
        integer('target_ordinal', 'Permanent logical target ordinal.'),
        text('logical_kind', 'net or tax.', 8), ident('tax_item_id', 'Logical tax item; null for net.', True),
        ident('tax_component_id', 'Physical target tax component; null for net.', True),
        ident('target_ar_source_id', 'Exact target receivable attribution.'),
        ident('target_recognition_source_id', 'Exact target income or tax attribution.'),
        text('recognition_role', 'sales_net or tax_liability.', 24),
        integer('amount_minor_units', 'Positive allocated amount.'), text('currency', 'Allocation currency.', 3),
        text('effective_date', 'Original application accounting date.', 10),
        text('facts_snapshot', 'Semantic component facts used to compare restatements.'), *created(),
        positive('amount_minor_units'), positive('target_ordinal'), obj('facts_snapshot'),
        check("(kind = 'allocation' AND reverses_allocation_id IS NULL) OR (kind = 'reversal' AND reverses_allocation_id IS NOT NULL AND reverses_allocation_id <> id)", 'allocation_kind'),
        check("(logical_kind = 'net' AND tax_item_id IS NULL AND tax_component_id IS NULL AND recognition_role = 'sales_net') OR (logical_kind = 'tax' AND tax_item_id IS NOT NULL AND tax_component_id IS NOT NULL AND recognition_role = 'tax_liability')", 'allocation_role'),
        sa.UniqueConstraint('reverses_allocation_id', name='uq_payment_allocation_inverse'),
        owner(['reverses_allocation_id'], ['application_allocations.id'], 'allocation_inverse'),
        owner(['source_transaction_id', 'source_revision_id', 'source_component_id'], ['payment_components.transaction_id', 'payment_components.revision_id', 'payment_components.id'], 'allocation_source'),
        owner(['source_transaction_id', 'source_posting_source_id'], ['posting_line_sources.transaction_id', 'posting_line_sources.id'], 'allocation_source_posting'),
        owner(['target_transaction_id', 'target_revision_id', 'target_document_line_id'], ['sales_line_profiles.transaction_id', 'sales_line_profiles.revision_id', 'sales_line_profiles.document_line_id'], 'allocation_target'),
        owner(['target_transaction_id', 'target_line_id', 'target_ordinal'], ['settlement_line_keys.transaction_id', 'settlement_line_keys.line_id', 'settlement_line_keys.ordinal'], 'allocation_ordinal'),
        owner(['target_transaction_id', 'target_revision_id', 'target_document_line_id', 'tax_component_id'], ['sales_tax_components.transaction_id', 'sales_tax_components.revision_id', 'sales_tax_components.document_line_id', 'sales_tax_components.id'], 'allocation_tax'),
        owner(['target_transaction_id', 'target_ar_source_id'], ['posting_line_sources.transaction_id', 'posting_line_sources.id'], 'allocation_target_ar'),
        owner(['target_transaction_id', 'target_recognition_source_id'], ['posting_line_sources.transaction_id', 'posting_line_sources.id'], 'allocation_target_recognition'),
        sa.Index('ix_application_allocations_application', 'application_id', 'id'),
        description='Immutable exact source/target posting attribution, corrections and inverses of settlement cents.')

    payment_operations = T('payment_operations',
        ident('id', 'Permanent successful operation receipt.', primary_key=True),
        text('operation_key', 'Company-unique caller intent key.', 128),
        text('command', 'Canonical participating financial command.', 64),
        integer('request_schema_version', 'Canonical request envelope version.'),
        text('request_hash', 'SHA256 of canonical original intent.', 64),
        text('request_snapshot', 'Replayable original request, omission state and resolved ownership.'),
        text('effect_snapshot', 'Typed original effect, with large collections stored as operation items.'),
        text('execution_snapshot', 'Original execution provenance, distinct from canonical business intent.'),
        *created(), positive('request_schema_version'), obj('request_snapshot'), obj('effect_snapshot'), obj('execution_snapshot'),
        sa.UniqueConstraint('operation_key', name='uq_payment_operation_key'),
        description='Permanent audited operation receipts, including first executions with no financial effect.')

    payment_operation_items = T('payment_operation_items',
        ident('id', 'Immutable operation item.', primary_key=True),
        C('operation_id', sa.String(26), 'Owning permanent operation.', sa.ForeignKey('payment_operations.id'), nullable=False),
        text('kind', 'request_applications, effect_applications, allocations or document_changes.', 32),
        integer('ordinal', 'Stable item page order.'), text('item_snapshot', 'Typed canonical operation item.'), *created(),
        positive('ordinal'), obj('item_snapshot'),
        check("kind IN ('request_applications','effect_applications','allocations','document_changes','source_components')", 'operation_item_kind'),
        sa.UniqueConstraint('operation_id', 'kind', 'ordinal', name='uq_payment_operation_item'),
        description='Immutable paged request/effect details owned by a successful operation.')

    payment_selections = T('payment_selections', *common(),
        text('state', 'open or consumed.', 16), ident('current_revision_id', 'Current immutable selection revision.'),
        ident('consumed_operation_id', 'Successful consuming operation; null while open.', True),
        check("(state = 'open' AND consumed_operation_id IS NULL) OR (state = 'consumed' AND consumed_operation_id IS NOT NULL)", 'selection_state'),
        positive('version'),
        owner(['id', 'current_revision_id'], ['payment_selection_revisions.selection_id', 'payment_selection_revisions.id'], 'selection_current', True),
        owner(['consumed_operation_id'], ['payment_operations.id'], 'selection_consumed'),
        description='Shared audited nonposting draft with an immutable revision manifest and one consuming operation.')

    payment_selection_revisions = T('payment_selection_revisions',
        ident('id', 'Immutable draft revision.', primary_key=True), ident('selection_id', 'Owning shared draft.'),
        integer('version', 'Positive draft version.'), text('context_snapshot', 'Typed context, label and captured calculation policy.'),
        integer('amount_minor_units', 'Resolved header amount; absent while unresolved.', True),
        text('amount_origin', 'entered, selection_total or unresolved.', 24), text('currency', 'Draft currency.', 3),
        text('manifest_hash', 'Canonical complete manifest and origin-state SHA256.', 64),
        integer('item_count', 'Complete selected invoice count, independent of page bounds.'), *created(),
        positive('version'), obj('context_snapshot'),
        check("typeof(item_count) = 'integer' AND item_count >= 0", 'selection_count'),
        check("amount_origin IN ('entered','selection_total','unresolved') AND ((amount_origin = 'entered' AND amount_minor_units IS NOT NULL) OR amount_origin <> 'entered') AND (amount_origin <> 'unresolved' OR amount_minor_units IS NULL) AND (amount_minor_units IS NULL OR (typeof(amount_minor_units) = 'integer' AND amount_minor_units >= 0))", 'selection_amount'),
        sa.UniqueConstraint('selection_id', 'version', name='uq_payment_selection_version'),
        sa.UniqueConstraint('selection_id', 'id', name='uq_payment_selection_revision_owner'),
        owner(['selection_id'], ['payment_selections.id'], 'selection_revision'),
        description='Immutable shared draft header origins, captured policy and complete manifest identity.')

    payment_selection_items = T('payment_selection_items',
        ident('id', 'Immutable selection item event.', primary_key=True),
        ident('selection_id', 'Owning draft.'), ident('revision_id', 'Revision recording this event.'),
        text('kind', 'set, remove or clear.', 16),
        ident('invoice_id', 'Selected invoice; null only for clear.', True),
        integer('ordinal', 'Permanent append order of this invoice occurrence.', True),
        integer('expected_version', 'Captured invoice concurrency version.', True),
        integer('due_minor_units', 'Captured invoice available due.', True),
        integer('amount_minor_units', 'Entered or derived amount; null while unresolved.', True),
        text('amount_origin', 'entered, calculated or unresolved; null for remove/clear.', 24, True),
        *created(),
        owner(['selection_id', 'revision_id'], ['payment_selection_revisions.selection_id', 'payment_selection_revisions.id'], 'selection_item_revision'),
        owner(['invoice_id'], ['transactions.id'], 'selection_item_invoice'),
        check("(kind = 'clear' AND invoice_id IS NULL AND ordinal IS NULL AND expected_version IS NULL AND due_minor_units IS NULL AND amount_minor_units IS NULL AND amount_origin IS NULL) OR (kind = 'remove' AND invoice_id IS NOT NULL AND ordinal IS NULL AND expected_version IS NULL AND due_minor_units IS NULL AND amount_minor_units IS NULL AND amount_origin IS NULL) OR (kind = 'set' AND invoice_id IS NOT NULL AND typeof(ordinal) = 'integer' AND ordinal > 0 AND typeof(expected_version) = 'integer' AND expected_version > 0 AND typeof(due_minor_units) = 'integer' AND due_minor_units >= 0 AND amount_origin IS NOT NULL AND ((amount_origin = 'unresolved' AND amount_minor_units IS NULL) OR (amount_origin IN ('entered','calculated') AND typeof(amount_minor_units) = 'integer' AND amount_minor_units >= 0)))", 'selection_item_shape'),
        sa.UniqueConstraint('revision_id', 'invoice_id', name='uq_payment_selection_item_event'),
        sa.Index('ix_payment_selection_items_revision', 'selection_id', 'revision_id'),
        description='Immutable set/remove/clear events reconstructing any shared draft version without truncation.')
    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}
