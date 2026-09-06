"""Company-local journal identities, immutable revisions and balanced posting records."""

import sqlalchemy as sa


def define_tables(metadata, column, table, common):
    C, T = column, table

    def created():
        return [C('id', sa.String(26), 'Stable ULID of this immutable record.', primary_key=True),
                C('created_at', sa.String(32), 'UTC time this history record was written.', nullable=False),
                C('created_by', sa.String(26), 'Company principal that wrote this history record.', nullable=False),
                C('created_via', sa.String(16), 'Interface that wrote this history record.', nullable=False)]

    def text(name, description, nullable=False, size=200):
        return C(name, sa.String(size), description, nullable=nullable)

    def identifier(name, description, nullable=False):
        return text(name, description, nullable, 26)

    def integer(name, description, nullable=False):
        return C(name, sa.BigInteger, description, nullable=nullable)

    def json_text(name, description):
        return C(name, sa.Text, description, nullable=False)

    def fk(columns, targets, name, deferred=False):
        return sa.ForeignKeyConstraint(columns, targets, name=name,
                                       deferrable=True if deferred else None,
                                       initially='DEFERRED' if deferred else None)

    def positive(name):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} > 0", name=f'ck_ledger_{name}_positive')

    def json_check(name):
        return sa.CheckConstraint(f"json_valid({name}) AND json_type({name}) = 'object'", name=f'ck_ledger_{name}_object')

    def money_facts():
        return [integer('original_minor_units', 'Original foreign amount before one-time conversion; null for domestic input.', True),
                text('original_currency', 'Original foreign currency; null for domestic input.', True, 3),
                text('rate_used', 'Exact decimal exchange rate captured at posting; null for domestic input.', True, 128),
                text('rate_source', 'Source of the captured exchange rate; null for domestic input.', True, 64)]

    def facts_check():
        return sa.CheckConstraint(
            '(original_minor_units IS NULL AND original_currency IS NULL AND rate_used IS NULL AND rate_source IS NULL) OR '
            "(typeof(original_minor_units) = 'integer' AND original_minor_units > 0 AND original_currency IS NOT NULL "
            'AND rate_used IS NOT NULL AND rate_source IS NOT NULL)', name='ck_ledger_foreign_facts')

    def dimensions():
        return [text('name_type', 'Party type: customer, vendor, employee or other_name; null with name_id.', True, 16),
                identifier('name_id', 'Company-local party id; null with name_type.', True),
                text('party_name', 'Party label captured for this revision or posting.', True),
                C('class_id', sa.String(26), 'Company class id captured for this line.', sa.ForeignKey('classes.id'), nullable=True),
                text('class_name', 'Class label captured for this revision or posting.', True),
                text('description', 'Entered line explanation.', True, 2000)]

    def party_check():
        return sa.CheckConstraint(
            '(name_type IS NULL AND name_id IS NULL) OR '
            "(name_type IN ('customer','vendor','employee','other_name') AND name_id IS NOT NULL)",
            name='ck_ledger_party_pair')

    transactions = T('transactions', *common(),
        text('type', 'Business document type: journal_entry, invoice, sales_receipt or payment.', size=32),
        text('number', 'Unique editable number within the document type.', size=64),
        identifier('current_revision_id', 'Immutable revision currently displayed.'),
        text('status', 'Current workflow state: posted or voided.', size=16),
        text('voided_at', 'UTC recorded time of the final void.', True, 32),
        identifier('voided_by', 'Principal that voided this document.', True),
        text('void_reason', 'Reason supplied for the final void.', True, 140),
        identifier('void_posting_batch_id', 'Final reversal batch; no separate business number.', True),
        sa.UniqueConstraint('type', 'number', name='uq_transaction_type_number'),
        sa.CheckConstraint("type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment')", name='ck_transaction_type'),
        sa.UniqueConstraint('id', 'type', name='uq_transaction_id_type'),
        sa.CheckConstraint("length(trim(number)) BETWEEN 1 AND 64", name='ck_transaction_number'),
        sa.CheckConstraint("(status = 'posted' AND voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL AND void_posting_batch_id IS NULL) OR "
                           "(status = 'voided' AND voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(trim(void_reason)) > 0 AND void_posting_batch_id IS NOT NULL)", name='ck_transaction_void'),
        fk(['id', 'current_revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id'], 'fk_transaction_current_revision', True),
        fk(['id', 'void_posting_batch_id'], ['posting_batches.transaction_id', 'posting_batches.id'], 'fk_transaction_void_batch', True),
        sa.Index('ix_transactions_status_number', 'status', 'number', 'id'),
        description='Stable business documents with versioned current-revision pointers and retained void history.')

    transaction_revisions = T('transaction_revisions', *created(),
        C('transaction_id', sa.String(26), 'Stable parent business document.', sa.ForeignKey('transactions.id'), nullable=False),
        integer('revision_number', 'Sequential revision number within this document.'),
        identifier('supersedes_revision_id', 'Previous immutable revision, or null for the first.', True),
        text('date', 'Accounting date in YYYY-MM-DD form.', size=10),
        text('number', 'Business number captured for this revision.', size=64),
        text('name_type', 'Optional header party type.', True, 16),
        identifier('name_id', 'Optional header party id.', True),
        text('memo', 'Entered journal memo for this revision.', True, 2000),
        integer('total_minor_units', 'One balanced side of the journal, equal to home debits.'),
        text('currency', 'Home currency of the revision total.', size=3),
        json_text('issuer_snapshot', 'JSON object of issuer identity and displayed company facts.'),
        json_text('custom_fields_snapshot', 'JSON object of revision-owned custom values and displayed definition facts.'),
        C('audit_event_id', sa.String(26), 'Audit event that committed this revision.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_revision_document_id'),
        sa.UniqueConstraint('transaction_id', 'revision_number', name='uq_revision_number'),
        sa.UniqueConstraint('supersedes_revision_id', name='uq_revision_successor'),
        fk(['transaction_id', 'supersedes_revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id'], 'fk_revision_supersedes'),
        positive('revision_number'), positive('total_minor_units'), json_check('issuer_snapshot'), json_check('custom_fields_snapshot'),
        description='Immutable rendered journal revisions and their original accounting dates.')

    document_line_identities = T('document_line_identities', *created(),
        C('transaction_id', sa.String(26), 'Document that permanently owns this line identity.', sa.ForeignKey('transactions.id'), nullable=False),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_line_identity_document'),
        description='Stable entered-line identities; identities omitted by later revisions are never reused.')

    document_lines = T('document_lines', *created(),
        identifier('transaction_id', 'Stable document owning this entered line.'),
        identifier('revision_id', 'Immutable document revision containing this line.'),
        identifier('line_id', 'Stable line identity carried across revisions.'),
        integer('position', 'One-based entered line position within the revision.'),
        text('kind', 'Entered line kind: journal, sale or payment.', size=16),
        C('account_id', sa.String(26), 'Posting account selected for a journal; null for a sale.', sa.ForeignKey('accounts.id'), nullable=True),
        text('side', 'Journal side: debit or credit; null for a sale.', True, size=6),
        integer('amount_minor_units', 'Positive journal amount; null for a sale.', True),
        text('currency', 'Home currency of the entered amount.', size=3),
        C('account_snapshot', sa.Text, 'Journal account facts as a JSON object; null for a sale.', nullable=True),
        *dimensions(), *money_facts(),
        fk(['transaction_id', 'revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id'], 'fk_document_line_revision'),
        fk(['transaction_id', 'line_id'], ['document_line_identities.transaction_id', 'document_line_identities.id'], 'fk_document_line_identity'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'id', name='uq_document_line_owner'),
        sa.UniqueConstraint('revision_id', 'line_id', name='uq_document_line_identity'),
        sa.UniqueConstraint('revision_id', 'position', name='uq_document_line_position'),
        positive('position'), party_check(), facts_check(),
        sa.CheckConstraint(
            "(kind = 'journal' AND account_id IS NOT NULL AND side IS NOT NULL AND side IN ('debit', 'credit') "
            "AND typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0 "
            "AND account_snapshot IS NOT NULL AND json_valid(account_snapshot) AND json_type(account_snapshot) = 'object') OR "
            "(kind IN ('sale', 'payment') AND account_id IS NULL AND side IS NULL AND amount_minor_units IS NULL AND account_snapshot IS NULL "
            "AND original_minor_units IS NULL AND original_currency IS NULL AND rate_used IS NULL AND rate_source IS NULL)",
            name='ck_document_line_kind_side'),
        description='Immutable ordered journal or sale envelopes, dimensions and original journal currency facts.')

    posting_batches = T('posting_batches', *created(),
        identifier('transaction_id', 'Stable business document that owns this accounting effect.'),
        identifier('revision_id', 'Immutable revision that supplies this effect.'),
        text('kind', 'Accounting effect kind: original, reversal or replacement.', size=16),
        text('effective_date', 'Accounting date used by financial reports.', size=10),
        identifier('reverses_batch_id', 'Exactly inverted business batch; null except on reversal.', True),
        identifier('replaces_batch_id', 'Prior business batch replaced; null except on replacement.', True),
        C('audit_event_id', sa.String(26), 'Audit event that committed this accounting effect.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_batch_document'),
        sa.UniqueConstraint('reverses_batch_id', name='uq_batch_reversal'),
        sa.UniqueConstraint('replaces_batch_id', name='uq_batch_replacement'),
        sa.Index('uq_revision_business_batch', 'revision_id', unique=True, sqlite_where=sa.text("kind IN ('original', 'replacement')")),
        fk(['transaction_id', 'revision_id'], ['transaction_revisions.transaction_id', 'transaction_revisions.id'], 'fk_batch_revision'),
        fk(['transaction_id', 'reverses_batch_id'], ['posting_batches.transaction_id', 'posting_batches.id'], 'fk_batch_reverses'),
        fk(['transaction_id', 'replaces_batch_id'], ['posting_batches.transaction_id', 'posting_batches.id'], 'fk_batch_replaces'),
        sa.CheckConstraint("(kind = 'original' AND reverses_batch_id IS NULL AND replaces_batch_id IS NULL) OR "
                           "(kind = 'reversal' AND reverses_batch_id IS NOT NULL AND replaces_batch_id IS NULL AND reverses_batch_id != id) OR "
                           "(kind = 'replacement' AND reverses_batch_id IS NULL AND replaces_batch_id IS NOT NULL AND replaces_batch_id != id)", name='ck_batch_kind_links'),
        sa.Index('ix_posting_batches_date', 'effective_date', 'id'),
        sa.Index('ix_posting_batches_document', 'transaction_id', 'id'),
        description='Immutable independently balanced effects, including every exact reversal and replacement.')

    posting_lines = T('posting_lines', *created(),
        identifier('transaction_id', 'Stable document owning this posting line.'),
        identifier('batch_id', 'Accounting batch containing this line.'),
        integer('line_no', 'One-based line number within the posting batch.'),
        C('account_id', sa.String(26), 'Account receiving this accounting effect.', sa.ForeignKey('accounts.id'), nullable=False),
        integer('debit_minor_units', 'Positive home debit, or zero for a credit line.'),
        integer('credit_minor_units', 'Positive home credit, or zero for a debit line.'),
        text('currency', 'Home currency of the posting.', size=3),
        json_text('account_snapshot', 'JSON object of the historical account display facts.'),
        *dimensions(), *money_facts(),
        identifier('reversed_line_id', 'Exactly inverted original posting line; null on business lines.', True),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_posting_line_document'),
        sa.UniqueConstraint('batch_id', 'line_no', name='uq_posting_line_number'),
        sa.UniqueConstraint('reversed_line_id', name='uq_posting_line_reversed'),
        fk(['transaction_id', 'batch_id'], ['posting_batches.transaction_id', 'posting_batches.id'], 'fk_posting_line_batch'),
        fk(['transaction_id', 'reversed_line_id'], ['posting_lines.transaction_id', 'posting_lines.id'], 'fk_posting_line_reversed'),
        positive('line_no'), json_check('account_snapshot'), party_check(), facts_check(),
        sa.CheckConstraint("typeof(debit_minor_units) = 'integer' AND typeof(credit_minor_units) = 'integer' AND "
                           '((debit_minor_units > 0 AND credit_minor_units = 0) OR (credit_minor_units > 0 AND debit_minor_units = 0))', name='ck_posting_line_one_side'),
        sa.Index('ix_posting_lines_account_batch', 'account_id', 'batch_id'),
        description='Immutable home-currency accounting legs; exactly one positive debit or credit per line.')

    posting_line_sources = T('posting_line_sources', *created(),
        identifier('transaction_id', 'Stable document owning this source attribution.'),
        identifier('posting_line_id', 'Posting line receiving this attributed amount.'),
        identifier('revision_id', 'Exact immutable source revision.'),
        identifier('document_line_id', 'Revision-local entered line that produced this effect.'),
        integer('amount_minor_units', 'Positive home amount attributed to the entered source line.'),
        text('currency', 'Home currency of this attributed amount.', size=3),
        identifier('reversed_source_id', 'Exactly retained attribution from an inverted posting line.', True),
        identifier('tax_component_id', 'Exact sale tax component; null for journal or sale net attribution.', True),
        identifier('payment_component_id', 'Exact owned payment revision component; null for all earlier document types.', True),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_posting_source_document'),
        sa.UniqueConstraint('reversed_source_id', name='uq_posting_source_reversed'),
        fk(['transaction_id', 'posting_line_id'], ['posting_lines.transaction_id', 'posting_lines.id'], 'fk_source_posting_line'),
        fk(['transaction_id', 'revision_id', 'document_line_id'], ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], 'fk_source_document_line'),
        fk(['transaction_id', 'revision_id', 'document_line_id', 'tax_component_id'],
           ['sales_tax_components.transaction_id', 'sales_tax_components.revision_id', 'sales_tax_components.document_line_id', 'sales_tax_components.id'], 'fk_source_tax_component'),
        fk(['transaction_id', 'reversed_source_id'], ['posting_line_sources.transaction_id', 'posting_line_sources.id'], 'fk_source_reversed'),
        fk(['transaction_id', 'revision_id', 'document_line_id', 'payment_component_id'],
           ['payment_components.transaction_id', 'payment_components.revision_id', 'payment_components.document_line_id', 'payment_components.id'], 'fk_source_payment_component'),
        positive('amount_minor_units'),
        sa.Index('ix_posting_sources_line', 'posting_line_id'),
        sa.Index('ix_posting_sources_document_line', 'document_line_id'),
        description='Immutable exact source allocations and inverse links for every posting line.')

    report_cursor_keys = T('report_cursor_keys',
        C('key_id', sa.Integer, 'Singleton report cursor authentication key slot.', primary_key=True),
        C('key_material', sa.LargeBinary(32), 'Private random authentication key; never returned by commands or included in audit snapshots.', nullable=False),
        sa.CheckConstraint('key_id = 1', name='ck_report_cursor_key_slot'),
        sa.CheckConstraint("typeof(key_material) = 'blob' AND length(key_material) = 32", name='ck_report_cursor_key_material'),
        description='Company-local secret authenticating report continuation state; copied with the company and excluded from annotation targets.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}
