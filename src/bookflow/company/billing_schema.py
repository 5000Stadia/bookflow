"""Immutable work-to-sale birth relations and revision-owned entitlement allocations."""

import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table

    def ident(name, description, primary_key=False):
        return C(name, sa.String(26), description, nullable=False, primary_key=primary_key)

    def text(name, description, size=None):
        return C(name, sa.String(size) if size else sa.Text, description, nullable=False)

    def integer(name, description):
        return C(name, sa.BigInteger, description, nullable=False)

    def check(expression, name):
        return sa.CheckConstraint(expression, name='ck_work_billing_' + name)

    def exact(name, positive=False):
        return check(f"typeof({name}) = 'integer' AND {name} {'>' if positive else '>='} 0", name)

    def owner(columns, targets, name):
        return sa.ForeignKeyConstraint(columns, targets, name='fk_work_billing_' + name)

    def created():
        return [text('created_at', 'UTC creation timestamp.', 32),
                ident('created_by', 'Principal creating this billing history.'),
                text('created_via', 'Interface creating this billing history.', 16)]

    work_billing_conversions = T('work_billing_conversions',
        ident('id', 'Permanent financial conversion ULID.', True),
        ident('source_document_id', 'Consumed work document.'),
        ident('source_revision_id', 'Exact consumed work revision.'),
        integer('source_version', 'Positive source concurrency version consumed at birth.'),
        ident('destination_transaction_id', 'Stable financial destination.'),
        ident('destination_revision_id', 'Initial financial sales revision.'),
        text('destination_type', 'invoice or sales_receipt.', 32),
        text('relation', 'Source kind and financial destination relation.', 32),
        text('conversion_key_hash', 'Permanent company-wide conversion key hash.', 64),
        text('request_hash', 'Canonical original conversion intent hash.', 64), *created(),
        sa.UniqueConstraint('conversion_key_hash', name='uq_work_billing_conversion_key'),
        sa.UniqueConstraint('destination_transaction_id', name='uq_work_billing_destination_birth'),
        owner(['source_document_id', 'source_revision_id'],
              ['work_revisions.document_id', 'work_revisions.id'], 'conversion_source'),
        owner(['destination_transaction_id', 'destination_revision_id'],
              ['sales_profiles.transaction_id', 'sales_profiles.revision_id'], 'conversion_destination'),
        owner(['destination_transaction_id', 'destination_type'],
              ['transactions.id', 'transactions.type'], 'conversion_type'),
        exact('source_version', True),
        check("destination_type IN ('invoice','sales_receipt')", 'destination_type'),
        check("relation IN ('estimate_invoice','estimate_sales_receipt','work_order_invoice','work_order_sales_receipt')", 'relation'),
        check('length(conversion_key_hash) = 64 AND length(request_hash) = 64', 'hashes'),
        description='Immutable financial conversion birth ancestry and permanent retry identity.')

    work_billing_allocations = T('work_billing_allocations',
        ident('id', 'Immutable allocation ULID.', True),
        ident('transaction_id', 'Stable consuming sale.'),
        ident('revision_id', 'Exact consuming sales revision.'),
        ident('document_line_id', 'Revision-local consuming sale line.'),
        ident('source_document_id', 'Captured work document.'),
        ident('source_revision_id', 'Captured work revision.'),
        ident('source_line_id', 'Captured revision-local work line.'),
        ident('root_document_id', 'Document owning the shared billing root.'),
        ident('root_line_id', 'Stable shared billing root identity.'),
        C('quantity_microunits', sa.BigInteger, 'Exact allocated quantity in millionths; null for an unrepresentable version 2 fraction.', nullable=True),
        integer('net_minor_units', 'Exact captured source net amount.'),
        integer('tax_minor_units', 'Exact captured source tax amount.'),
        integer('gross_minor_units', 'Exact captured source gross amount.'),
        text('facts_snapshot', 'Captured source document scope and typed work-line facts.'), *created(),
        sa.UniqueConstraint('revision_id', 'root_document_id', 'root_line_id', name='uq_work_billing_revision_root'),
        owner(['transaction_id', 'revision_id', 'document_line_id'],
              ['sales_line_profiles.transaction_id', 'sales_line_profiles.revision_id', 'sales_line_profiles.document_line_id'], 'allocation_destination'),
        owner(['source_document_id', 'source_revision_id', 'source_line_id'],
              ['work_lines.document_id', 'work_lines.revision_id', 'work_lines.id'], 'allocation_source'),
        owner(['root_document_id', 'root_line_id'],
              ['work_line_identities.document_id', 'work_line_identities.id'], 'allocation_root'),
        C('allocation_version', sa.Integer, '1 occupies the full root; 2 carries exact interval proof.', nullable=False, server_default='1'),
        C('source_basis_hash', sa.String(64), 'Version 2 captured economic basis SHA256 in lowercase hex.', nullable=True),
        C('denominator_hex', sa.String(40), 'Version 2 positive unsigned 160-bit denominator in fixed-width lowercase hex.', nullable=True),
        C('spans_json', sa.Text, 'Version 2 canonical array of 1–200 fixed-width hex endpoint pairs.', nullable=True),
        check("(typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0) OR (allocation_version = 2 AND quantity_microunits IS NULL)", 'quantity_microunits'),
        check("typeof(allocation_version) = 'integer' AND allocation_version IN (1,2)", 'allocation_version'),
        check("(allocation_version = 1 AND source_basis_hash IS NULL AND denominator_hex IS NULL AND spans_json IS NULL) OR (allocation_version = 2 AND typeof(source_basis_hash) = 'text' AND length(source_basis_hash) = 64 AND length(CAST(source_basis_hash AS BLOB)) = 64 AND source_basis_hash NOT GLOB '*[^0-9a-f]*' AND typeof(denominator_hex) = 'text' AND length(denominator_hex) = 40 AND length(CAST(denominator_hex AS BLOB)) = 40 AND denominator_hex NOT GLOB '*[^0-9a-f]*' AND denominator_hex > '0000000000000000000000000000000000000000' COLLATE BINARY AND typeof(spans_json) = 'text' AND CASE WHEN json_valid(spans_json) THEN json_type(spans_json) = 'array' AND json_array_length(spans_json) BETWEEN 1 AND 200 ELSE 0 END)", 'proof'),
        *(exact(n + '_minor_units') for n in ('net', 'tax', 'gross')),
        check('gross_minor_units = net_minor_units + tax_minor_units', 'total'),
        check("json_valid(facts_snapshot) AND json_type(facts_snapshot) = 'object'", 'facts'),
        description='Immutable full-root or exact interval consumption; active only on a posted sale current revision.')
    sa.Index('ix_work_billing_allocation_root', work_billing_allocations.c.root_document_id,
             work_billing_allocations.c.root_line_id)
    sa.Index('ix_work_billing_allocation_source', work_billing_allocations.c.source_document_id,
             work_billing_allocations.c.source_revision_id)
    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}
