"""Company-local intercompany settings and untargeted drafts; neither stores a peer."""
import sqlalchemy as sa


def define_tables(metadata, C, T, common):
    settings = T('intercompany_settings', *common(),
        C('linked_entries_enabled', sa.Boolean, 'Local linked-entry layer; false before explicit setup.', nullable=False),
        C('templates_enabled', sa.Boolean, 'Optional local saved-entry templates switch; false by default.', nullable=False),
        C('bulk_enabled', sa.Boolean, 'Optional local bulk entry switch; false by default.', nullable=False),
        sa.CheckConstraint("id = 'settings'", name='ck_intercompany_settings_singleton'),
        sa.CheckConstraint('linked_entries_enabled IN (0,1) AND templates_enabled IN (0,1) AND bulk_enabled IN (0,1)', name='ck_intercompany_settings_booleans'),
        sa.CheckConstraint('linked_entries_enabled = 1 OR (templates_enabled = 0 AND bulk_enabled = 0)', name='ck_intercompany_settings_layers'),
        description='Local intercompany feature switches; absent singleton means every layer is off.')
    drafts = T('intercompany_drafts', *common(),
        C('date', sa.String(10), 'Proposed accounting date, YYYY-MM-DD; no posting or period reservation.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Positive proposed amount in integer home-currency minor units.', nullable=False),
        C('currency', sa.String(3), 'Company home currency captured when the local proposal was saved.', nullable=False),
        C('offset_account_id', sa.String(26), 'Optional local eligible offset account; no remote account is stored.', sa.ForeignKey('accounts.id', ondelete='RESTRICT'), nullable=True),
        C('memo', sa.String(1000), 'Optional local user-authored proposal text.', nullable=True),
        sa.CheckConstraint('amount_minor_units > 0', name='ck_intercompany_draft_amount'),
        description='Untargeted local proposals without relationship, peer, reservation, or ledger effect.')
    return {'intercompany_settings': settings, 'intercompany_drafts': drafts}
