"""Non-posting customer work identities and immutable commercial history."""

import sqlalchemy as sa


def define_tables(metadata, column, table, common):
    C, T = column, table

    def ident(name, description, target=None, nullable=False, primary_key=False):
        return C(name, sa.String(26), description,
                 *([sa.ForeignKey(target)] if target else []),
                 nullable=nullable, primary_key=primary_key)

    def text(name, description, size=None, nullable=False):
        return C(name, sa.String(size) if size else sa.Text, description, nullable=nullable)

    def integer(name, description, nullable=False):
        return C(name, sa.BigInteger, description, nullable=nullable)

    def check(expression, name):
        return sa.CheckConstraint(expression, name='ck_work_' + name)

    def exact(name, positive=False, nullable=False):
        expression = f"typeof({name}) = 'integer' AND {name} {'>' if positive else '>='} 0"
        return check(f'{name} IS NULL OR ({expression})' if nullable else expression, name)

    def owner(columns, targets, name, deferred=False):
        return sa.ForeignKeyConstraint(columns, targets, name='fk_work_' + name,
            **({'deferrable': True, 'initially': 'DEFERRED'} if deferred else {}))

    def created():
        return [text('created_at', 'UTC creation timestamp.', 32),
                ident('created_by', 'Principal recorded as creating this history.'),
                text('created_via', 'Interface that created this history.', 16)]

    def totals():
        return [integer(n + '_minor_units', 'Exact home-currency ' + n + ' amount.')
                for n in ('net', 'tax', 'gross')]

    def total_checks():
        return [*(exact(n + '_minor_units') for n in ('net', 'tax', 'gross')),
                check('gross_minor_units = net_minor_units + tax_minor_units', 'total')]

    def snapshot(name, description):
        return [text(name, description),
                check(f"json_valid({name}) AND json_type({name}) = 'object'", name)]

    work_documents = T('work_documents', *common(),
        text('kind', 'Document kind: proposal, estimate or work_order.', 16),
        text('number', 'Visible number unique within kind.', 64),
        ident('current_revision_id', 'Current immutable revision owned by this document.'),
        text('status', 'Current commercial decision or operational state.', 16),
        C('active', sa.Boolean, 'Availability for new operations.', nullable=False, default=True),
        ident('estimate_group_id', 'Standalone estimate root or source proposal grouping alternatives.',
              'work_documents.id', nullable=True),
        sa.UniqueConstraint('kind', 'number', name='uq_work_kind_number'),
        check("kind IN ('proposal','estimate','work_order')", 'kind'),
        check("(kind = 'estimate' AND estimate_group_id IS NOT NULL) OR (kind <> 'estimate' AND estimate_group_id IS NULL)", 'group'),
        check("(kind IN ('proposal','estimate') AND status IN ('draft','open','accepted','declined','superseded','cancelled','voided')) OR (kind = 'work_order' AND status IN ('draft','scheduled','in_progress','on_hold','complete','cancelled'))", 'status'),
        check('active IN (0,1)', 'active'), exact('version', positive=True),
        check('length(trim(number)) BETWEEN 1 AND 64', 'number'),
        owner(['id', 'current_revision_id'], ['work_revisions.document_id', 'work_revisions.id'], 'current_revision', True),
        description='Stable non-posting customer work documents and current state.')
    sa.Index('uq_work_accepted_group', work_documents.c.estimate_group_id, unique=True,
             sqlite_where=sa.text("kind = 'estimate' AND status = 'accepted'"))

    work_revisions = T('work_revisions',
        ident('id', 'Immutable revision ULID.', primary_key=True),
        ident('document_id', 'Owning work document.', 'work_documents.id'),
        integer('revision_number', 'Positive document-local revision number.'),
        ident('supersedes_revision_id', 'Prior revision of the same document.', nullable=True),
        text('date', 'ISO issue date.', 10), text('number', 'Captured visible number.', 64),
        text('title', 'Captured work title.', 200), text('status', 'Captured state.', 16),
        C('active', sa.Boolean, 'Captured availability.', nullable=False),
        ident('customer_id', 'Captured company customer or job.', 'customers.id'),
        text('currency', 'Home currency code.', 3), *totals(),
        ident('accepted_revision_id', 'Revision that first accepted these facts.', nullable=True),
        text('accepted_at', 'UTC acceptance timestamp.', 32, True),
        ident('accepted_by', 'Principal recording acceptance.', nullable=True),
        text('decision_note', 'Explicit decision evidence.', 2000, True),
        *snapshot('facts_snapshot', 'Versioned typed captured WorkFacts object.'),
        *snapshot('custom_fields_snapshot', 'Captured typed custom field values and definitions.'),
        ident('audit_event_id', 'Audit event committing this revision.', 'audit_events.id'), *created(),
        sa.UniqueConstraint('document_id', 'id', name='uq_work_revision_owner'),
        sa.UniqueConstraint('document_id', 'revision_number', name='uq_work_revision_number'),
        owner(['document_id', 'supersedes_revision_id'], ['work_revisions.document_id', 'work_revisions.id'], 'supersedes'),
        owner(['document_id', 'accepted_revision_id'], ['work_revisions.document_id', 'work_revisions.id'], 'acceptance', True),
        exact('revision_number', positive=True), *total_checks(),
        check('active IN (0,1)', 'revision_active'),
        check('length(trim(title)) BETWEEN 1 AND 200', 'title'),
        check('length(trim(number)) BETWEEN 1 AND 64', 'revision_number_text'),
        check('length(currency) = 3', 'currency'),
        check("(accepted_revision_id IS NULL AND accepted_at IS NULL AND accepted_by IS NULL AND status <> 'accepted') OR (accepted_revision_id IS NOT NULL AND accepted_at IS NOT NULL AND accepted_by IS NOT NULL AND status = 'accepted')", 'acceptance_evidence'),
        description='Immutable whole work revisions with queryable commercial totals and acceptance evidence.')
    sa.Index('ix_work_revision_query', work_revisions.c.date, work_revisions.c.document_id)
    sa.Index('ix_work_revision_customer', work_revisions.c.customer_id)

    work_line_identities = T('work_line_identities',
        ident('id', 'Stable document-local line identity.', primary_key=True),
        ident('document_id', 'Owning document.', 'work_documents.id'),
        ident('root_document_id', 'Document owning the billing root.'),
        ident('root_line_id', 'Permanent billing root identity; self at independent birth.'),
        ident('source_line_id', 'Exact historical source line.', 'work_lines.id', nullable=True),
        sa.UniqueConstraint('document_id', 'id', name='uq_work_identity_owner'),
        owner(['root_document_id', 'root_line_id'], ['work_line_identities.document_id', 'work_line_identities.id'], 'root', True),
        description='Immutable line ownership, billing roots and historical source ancestry.')

    work_lines = T('work_lines',
        ident('id', 'Revision-local line ULID.', primary_key=True),
        ident('document_id', 'Owning document.'), ident('revision_id', 'Owning revision.'),
        ident('line_id', 'Stable line identity in this document.'),
        integer('position', 'Positive ordered position within revision.'),
        ident('item_id', 'Captured selling item.', 'items.id'),
        ident('unit_id', 'Captured selected unit.', 'unit_conversions.id', nullable=True),
        integer('quantity_microunits', 'Positive ordered quantity in millionths.'),
        integer('completed_quantity_microunits', 'Completed quantity in millionths.'),
        integer('unit_factor_nanounits', 'Captured base units per selected unit in billionths.'),
        integer('base_quantity_microunits', 'Rounded base quantity in millionths.'),
        integer('unit_price_minor_units', 'Captured unit price; null in amount mode.', True), *totals(),
        integer('estimated_unit_cost_minor_units', 'Estimated selected-unit cost; null when unknown.', True),
        integer('estimated_cost_minor_units', 'Extended estimated cost; null when unknown.', True),
        text('pricing_basis', 'Authoritative catalog, manual, markup or amount price mode.', 16),
        integer('markup_percent_millionths', 'Signed markup percentage in millionths.', True),
        C('billable', sa.Boolean, 'Eligibility for later billing.', nullable=False),
        *snapshot('facts_snapshot', 'Versioned typed WorkLineFacts with captured profile and taxes.'), *created(),
        sa.UniqueConstraint('document_id', 'revision_id', 'id', name='uq_work_line_owner'),
        sa.UniqueConstraint('revision_id', 'line_id', name='uq_work_line_identity'),
        sa.UniqueConstraint('revision_id', 'position', name='uq_work_line_position'),
        owner(['document_id', 'revision_id'], ['work_revisions.document_id', 'work_revisions.id'], 'line_revision'),
        owner(['document_id', 'line_id'], ['work_line_identities.document_id', 'work_line_identities.id'], 'line_identity'),
        *(exact(n, positive=True) for n in ('position','quantity_microunits','unit_factor_nanounits','base_quantity_microunits')),
        exact('completed_quantity_microunits'),
        check('completed_quantity_microunits <= quantity_microunits', 'completed'),
        *(exact(n, nullable=True) for n in ('unit_price_minor_units','estimated_unit_cost_minor_units','estimated_cost_minor_units')),
        *total_checks(), check('billable IN (0,1)', 'billable'),
        check('(estimated_unit_cost_minor_units IS NULL) = (estimated_cost_minor_units IS NULL)', 'known_cost'),
        check("(pricing_basis = 'amount' AND unit_price_minor_units IS NULL AND markup_percent_millionths IS NULL) OR (pricing_basis IN ('catalog','manual') AND unit_price_minor_units IS NOT NULL AND markup_percent_millionths IS NULL) OR (pricing_basis = 'markup' AND unit_price_minor_units IS NOT NULL AND estimated_unit_cost_minor_units IS NOT NULL AND typeof(markup_percent_millionths) = 'integer' AND markup_percent_millionths BETWEEN -100000000 AND 1000000000000)", 'pricing'),
        description='Immutable ordered work line quantities, prices, estimated costs and captured commercial facts.')

    work_links = T('work_links',
        ident('id', 'Permanent ancestry link ULID.', primary_key=True),
        ident('source_document_id', 'Consumed source document.'),
        ident('source_revision_id', 'Exact consumed source revision.'),
        ident('destination_document_id', 'Created destination document.'),
        ident('destination_revision_id', 'Initial destination revision.'),
        text('relation', 'copy, proposal_estimate or estimate_work_order.', 24),
        text('conversion_key_hash', 'Permanent company-wide conversion key hash.', 64, True),
        text('request_hash', 'Canonical conversion intent hash.', 64, True),
        integer('source_version', 'Consumed source concurrency version.'), *created(),
        sa.UniqueConstraint('conversion_key_hash', name='uq_work_conversion_key'),
        sa.UniqueConstraint('destination_document_id', name='uq_work_destination_birth'),
        owner(['source_document_id', 'source_revision_id'], ['work_revisions.document_id', 'work_revisions.id'], 'link_source'),
        owner(['destination_document_id', 'destination_revision_id'], ['work_revisions.document_id', 'work_revisions.id'], 'link_destination'),
        exact('source_version', positive=True),
        check('source_document_id <> destination_document_id', 'link_distinct'),
        check("(relation = 'copy' AND conversion_key_hash IS NULL AND request_hash IS NULL) OR (relation IN ('proposal_estimate','estimate_work_order') AND conversion_key_hash IS NOT NULL AND request_hash IS NOT NULL AND length(conversion_key_hash) = 64 AND length(request_hash) = 64)", 'link_key'),
        description='Immutable birth ancestry, consumed source facts and permanent conversion replay keys.')
    sa.Index('uq_work_estimate_order', work_links.c.source_document_id, unique=True,
             sqlite_where=sa.text("relation = 'estimate_work_order'"))
    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}
