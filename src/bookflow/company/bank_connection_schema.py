"""Person-authorized live-feed connections and account cursors (co0122)."""
import sqlalchemy as sa


def define_tables(metadata, C, T, common):
    connections = T('bank_feed_connections', *common(),
        C('name', sa.String(100), 'Name of this bank feed connection.', nullable=False),
        C('name_key', sa.String(100), 'Case-folded unique connection name.', nullable=False, unique=True),
        C('kind', sa.String(64), 'Registered provider kind; production registers none.', nullable=False),
        C('settings', sa.Text, 'Provider-validated non-secret settings, as a JSON object.', nullable=False),
        C('secret_ref', sa.String(200), 'OS keyring reference keyring:service/name; never a credential.', nullable=True),
        C('enabled', sa.Boolean, 'Whether this connection may import automatically on its bound host.', nullable=False),
        C('enabled_host', sa.String(64), 'Installation binding authorized to check or fetch; null when disabled.', nullable=True),
        C('established_by', sa.String(26), 'Actor authorizing this connection to contact its provider.', nullable=False),
        C('established_for', sa.String(26), 'Person represented by an agent authorizer.', nullable=True),
        C('established_epoch', sa.Integer, 'Agent authority epoch captured at authorization.', nullable=True),
        C('check_requested', sa.Boolean, 'Person requested a bounded host check and first-page preview.', nullable=False),
        C('last_check_ok', sa.Boolean, 'Whether the last host provider check succeeded; null before a check.', nullable=True),
        C('last_check_at', sa.String(32), 'UTC timestamp of the last provider check.', nullable=True),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 100", name='ck_bank_connection_name'),
        sa.CheckConstraint("json_valid(settings) AND json_type(settings)='object'", name='ck_bank_connection_settings'),
        sa.CheckConstraint("secret_ref IS NULL OR secret_ref GLOB 'keyring:?*/?*'", name='ck_bank_connection_secret'),
        sa.CheckConstraint("(enabled = 0 AND check_requested = 0) OR enabled_host IS NOT NULL", name='ck_bank_connection_host'),
        description='Bank feed providers explicitly authorized by a person or their granted agent; no secrets stored.')
    accounts = T('bank_feed_connection_accounts',
        C('connection_id', sa.String(26), 'Connection owning this mapping.', sa.ForeignKey('bank_feed_connections.id'), primary_key=True),
        C('account_id', sa.String(26), 'Local bank or card account; at most one connection per account.', sa.ForeignKey('accounts.id'), nullable=False, unique=True),
        C('remote_account', sa.String(255), 'Provider account identifier; never a credential.', nullable=False),
        C('backfill_approved', sa.Boolean, 'Whether the initial backfill has been approved and imported.', nullable=False, default=False),
        C('cursor', sa.Text, 'Opaque continuation cursor of the last committed imported page.', nullable=True),
        C('staged_page', sa.Text, 'Validated first page awaiting approval, as JSON; contains no credentials.', nullable=True),
        C('staged_fingerprint', sa.String(64), 'Fingerprint of the exact staged first page.', nullable=True),
        C('approved_fingerprint', sa.String(64), 'Person-approved first-page fingerprint consumed by the host.', nullable=True),
        sa.CheckConstraint('staged_page IS NULL OR json_valid(staged_page)', name='ck_bank_connection_page'),
        description='Per-account live feed cursor and first-backfill approval; advanced atomically with its import.')
    return {'bank_feed_connections': connections, 'bank_feed_connection_accounts': accounts}
