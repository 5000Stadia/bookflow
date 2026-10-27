"""The verified history watermark of statement effect storage (R180 stage 2, co0101).

One row per bank or card account that a reconciliation command has proved: where in append order
that proof reached, which code proved it, and a digest of the verified prefix. A later command
re-proves only what was written after it (`reconciliation_watermark`); `company verify` re-proves
everything. The row is a record of a proof, never a source of facts: deleting it costs the next
command one full proof and changes no result.

Like `statement_effect_pending`, the table does **not** carry the `reconciliation_` prefix: that
prefix is the inventory `reconciliation_storage_validation.validate` requires a complete row set
for, and a proof's own record is not part of what it proves.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    table = T('statement_watermarks',
        C('account_id', sa.String(26), 'Bank or card account whose statement effect storage this proof covers.',
          sa.ForeignKey('accounts.id'), nullable=False),
        C('code_version', sa.String(64),
          'SHA-256 of the derivation and validation code that made the proof; any change forces a full proof.',
          nullable=False),
        C('through_seq', sa.Integer, 'Last audit event sequence the proof covered; null before any sequenced event.',
          nullable=True),
        C('census', sa.Text, 'JSON: for each append-only table, its highest row number and row count when proved.',
          nullable=False),
        C('documents', sa.Text,
          'JSON: each proved document with a fingerprint of its changeable header and current-effect pointers.',
          nullable=False),
        C('accounts', sa.Text, 'JSON: the accounts the proved snapshot reached (the requested one and any it spans).',
          nullable=False),
        C('setup', sa.Text,
          'JSON: the ledger setup the derivation reads when proved (each account\'s type and currency, the home currency).',
          nullable=False),
        C('digest', sa.String(64), 'SHA-256 over the code version, census, documents, accounts, setup and schema.',
          nullable=False),
        C('verified_at', sa.String(32), 'UTC time of the proof.', nullable=False),
        sa.PrimaryKeyConstraint('account_id'),
        description='Verified prefix of each account\'s statement effect storage; later commands re-prove only what follows it.')
    return {'statement_watermarks': table}
