"""co0113. Bank feeds, increment 3: sorting the bank inbox (R108, design/bank-feeds.md §2, §4, §9).

Four columns on ``bank_lines``, moving no stored row: ``created`` (0; 1 when the line's entry was made from it, by
``bank-line add`` or a transfer it posted, so only reversing that entry takes it back), ``payee_display`` (the payee
the entry was entered under; null until then), ``proposal`` (what the matcher suggested at import, with its reason,
as a JSON object; null when nothing) and ``ignore_reason`` (why a line was set aside; null unless ignored). Every
other link column (``entry_id``, ``movement``, ``link_how``, ``match_reason``) came with co0112. The line's guards are
unchanged: what the bank printed stays fixed. DDL below is frozen: this migration never imports current application
metadata.
"""
from alembic import op

revision = 'co0113'
down_revision = 'co0112'
branch_labels = depends_on = None

IGNORE_REASONS = ('already_entered', 'not_ours', 'duplicate', 'other')
COLUMNS = (
    'ALTER TABLE bank_lines ADD COLUMN created INTEGER NOT NULL DEFAULT 0 '
    'CONSTRAINT ck_bank_line_created CHECK (created IN (0, 1))',
    'ALTER TABLE bank_lines ADD COLUMN payee_display TEXT',
    "ALTER TABLE bank_lines ADD COLUMN proposal TEXT CONSTRAINT ck_bank_line_proposal CHECK (proposal IS NULL OR "
    "(json_valid(proposal) AND json_type(proposal) = 'object'))",
    'ALTER TABLE bank_lines ADD COLUMN ignore_reason VARCHAR(16) CONSTRAINT ck_bank_line_ignore_reason CHECK '
    "(ignore_reason IS NULL OR ignore_reason IN ('already_entered', 'not_ours', 'duplicate', 'other'))",
)
# What a test rewinding this head drops: nothing of its own, since co0112's bank_lines goes whole.
NAMES = ('created', 'payee_display', 'proposal', 'ignore_reason')


def upgrade():
    connection = op.get_bind()
    existing = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info('bank_lines')").fetchall()}
    if set(NAMES) & existing:
        raise RuntimeError('co0113 reserved column already exists')
    for statement in COLUMNS:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
