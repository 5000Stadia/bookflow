"""Private local setup revisions; account identity never crosses into hub metadata."""
import sqlalchemy as sa


def define_tables(metadata, C, T, common):
    mappings = T('intercompany_mappings', *common(),
        C('peer_id', sa.String(64), 'Keyed opaque local counterpart reference; never a company ID and unusable without the original hub.', nullable=False),
        C('due_from_id', sa.String(26), 'Local dedicated receivable control account.', sa.ForeignKey('accounts.id', ondelete='RESTRICT'), nullable=False),
        C('due_to_id', sa.String(26), 'Local dedicated payable control account.', sa.ForeignKey('accounts.id', ondelete='RESTRICT'), nullable=False),
        C('currency', sa.String(3), 'Captured common home currency.', nullable=False),
        C('digest', sa.String(64), 'Canonical local mapping revision digest.', nullable=False),
        sa.UniqueConstraint('peer_id', 'version', name='uq_intercompany_mapping_revision'),
        sa.CheckConstraint('due_from_id != due_to_id', name='ck_intercompany_distinct_accounts'),
        sa.Index('ix_intercompany_mapping_peer', 'peer_id', 'version'),
        description='Immutable local mapping revisions. Staging a mapping reserves local accounts but does not activate a relationship.')
    return {'intercompany_mappings': mappings}
