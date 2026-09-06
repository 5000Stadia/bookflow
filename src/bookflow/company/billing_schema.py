"""Immutable work-to-sale birth relations and revision-owned full-line allocations."""

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
        integer('quantity_microunits', 'Full source quantity denominator in millionths.'),
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
        exact('quantity_microunits', True),
        *(exact(n + '_minor_units') for n in ('net', 'tax', 'gross')),
        check('gross_minor_units = net_minor_units + tax_minor_units', 'total'),
        check("json_valid(facts_snapshot) AND json_type(facts_snapshot) = 'object'", 'facts'),
        description='Immutable full-line consumption; active only on a posted sale current revision.')
    sa.Index('ix_work_billing_allocation_root', work_billing_allocations.c.root_document_id,
             work_billing_allocations.c.root_line_id)
    sa.Index('ix_work_billing_allocation_source', work_billing_allocations.c.source_document_id,
             work_billing_allocations.c.source_revision_id)
    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}
