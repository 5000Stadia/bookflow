"""Intercompany registry metadata; no account, amount, balance or description payloads."""
import sqlalchemy as sa


def define_tables(metadata, C, T, common):
    identity = T('intercompany_identity',
        C('id', sa.Integer, 'Singleton registry identity.', primary_key=True),
        C('key', sa.String(64), 'Private random hub key for opaque local peer references; not an authentication credential.', nullable=False),
        sa.CheckConstraint('id = 1', name='ck_intercompany_identity'),
        description='Root-local reference key; company backups cannot translate opaque peer references into company identities.')
    relationships = T('intercompany_relationships', *common(),
        C('organization_id', sa.String(26), 'Shared organization identifier.', sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
        C('company_a', sa.String(26), 'Lexically first company ID in the unordered pair.', sa.ForeignKey('companies.id', ondelete='RESTRICT'), nullable=False),
        C('company_b', sa.String(26), 'Lexically second company ID in the unordered pair.', sa.ForeignKey('companies.id', ondelete='RESTRICT'), nullable=False),
        C('enabled', sa.Boolean, 'Whether new linked work may use this relationship; no posting door exists yet.', nullable=False),
        C('mapping_a_digest', sa.String(64), 'Digest of company A local mapping revision.', nullable=False),
        C('mapping_b_digest', sa.String(64), 'Digest of company B local mapping revision.', nullable=False),
        sa.UniqueConstraint('company_a', 'company_b', name='uq_intercompany_pair'),
        sa.CheckConstraint('company_a < company_b', name='ck_intercompany_pair_order'),
        sa.CheckConstraint('enabled IN (0,1)', name='ck_intercompany_enabled'),
        description='Relationship registry, unique per unordered pair. Contains only coordination metadata.')
    revisions = T('intercompany_relationship_revisions',
        C('relationship_id', sa.String(26), 'Stable registry relationship.', sa.ForeignKey('intercompany_relationships.id', ondelete='RESTRICT'), primary_key=True),
        C('version', sa.Integer, 'Immutable relationship revision number.', primary_key=True),
        C('enabled', sa.Boolean, 'Captured enabled state.', nullable=False),
        C('mapping_a_digest', sa.String(64), 'Captured local revision digest for A.', nullable=False),
        C('mapping_b_digest', sa.String(64), 'Captured local revision digest for B.', nullable=False),
        description='Immutable setup revisions; future pairs capture this version.')
    return {'intercompany_identity': identity, 'intercompany_relationships': relationships, 'intercompany_relationship_revisions': revisions}
