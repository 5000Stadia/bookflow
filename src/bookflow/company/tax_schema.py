"""Company-local immutable tax ordering and revision attribution, separate from settlement."""
import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table
    result = {}
    def text(name, description, size=26, **kw):
        return C(name, sa.String(size) if size else sa.Text, description, nullable=False, **kw)
    def created():
        return [text('created_at', 'UTC creation timestamp.', 32),
                text('created_by', 'Principal creating the tax facts.'),
                text('created_via', 'Interface creating the tax facts.', 16)]
    for prefix, owner, identities, revisions, lines, revision_key, line_key in (
        ('sales', 'transaction_id', 'document_line_identities', 'sales_profiles', 'document_lines', 'revision_id', 'document_line_id'),
        ('work', 'document_id', 'work_line_identities', 'work_revisions', 'work_lines', 'id', 'work_line_id')):
        keys = prefix + '_tax_line_keys'
        result[keys] = T(keys,
            text('line_id', 'Immutable document-local commercial line identity.', primary_key=True),
            text(owner, 'Owning business document.'),
            C('tax_ordinal', sa.BigInteger, 'Permanent positive tax order; never a payment settlement ordinal.', nullable=False),
            *created(),
            sa.CheckConstraint("typeof(tax_ordinal) = 'integer' AND tax_ordinal > 0", name='ck_'+keys+'_ordinal'),
            sa.UniqueConstraint(owner, 'tax_ordinal', name='uq_'+keys+'_ordinal'),
            sa.UniqueConstraint(owner, 'line_id', 'tax_ordinal', name='uq_'+keys+'_owner'),
            sa.ForeignKeyConstraint([owner, 'line_id'], [identities+'.'+owner, identities+'.id']),
            description='Immutable tax ordering including retired commercial line identities.')
        attribution = prefix + '_tax_attributions'
        result[attribution] = T(attribution,
            text('revision_id', 'Exact immutable commercial revision.', primary_key=True),
            text(owner, 'Owning business document.'), *created(),
            text('facts_snapshot', 'TaxAttribution v1: captured policy/origin and complete exact economic buckets and cells.', None),
            sa.CheckConstraint("CASE WHEN json_valid(facts_snapshot) THEN json_type(facts_snapshot) = 'object' ELSE 0 END", name='ck_'+attribution+'_facts'),
            sa.UniqueConstraint(owner, 'revision_id', name='uq_'+attribution+'_owner'),
            sa.ForeignKeyConstraint([owner, 'revision_id'], [revisions+'.'+owner, revisions+'.'+revision_key]),
            description='Revision-owned exact tax attribution; never rewrites historical pricing facts.')
        mapping = prefix + '_tax_attribution_lines'
        result[mapping] = T(mapping,
            text(line_key, 'Exact revision-local commercial line.', primary_key=True),
            text(owner, 'Owning business document.'), text('revision_id', 'Owning immutable revision.'),
            text('line_id', 'Stable commercial line identity.'),
            C('tax_ordinal', sa.BigInteger, 'Captured immutable tax ordinal used for this revision.', nullable=False), *created(),
            sa.UniqueConstraint('revision_id', 'tax_ordinal', name='uq_'+mapping+'_ordinal'),
            sa.ForeignKeyConstraint([owner, 'revision_id'], [attribution+'.'+owner, attribution+'.revision_id']),
            sa.ForeignKeyConstraint([owner, 'line_id', 'tax_ordinal'], [keys+'.'+owner, keys+'.line_id', keys+'.tax_ordinal']),
            sa.ForeignKeyConstraint([owner, 'revision_id', line_key], [lines+'.'+owner, lines+'.revision_id', lines+'.id']),
            description='Revision tax order with composite document, line and ordinal ownership.')
    return result
