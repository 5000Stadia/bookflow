"""The bank inbox (co0111, R108 increment 1; design/bank-feeds.md §2, §3, §9).

- ``bank_feed_imports``: one statement file brought into an account's inbox. Never updated or deleted; its batch (in
  the shared ledger) carries whether it was reversed.
- ``bank_lines``: one transaction line as the bank printed it, in the bank's own sign. Only ``state``, the link
  columns, ``live`` and ``version`` ever change, and a row is never deleted. ``line_key`` (``fitid:<FITID>`` or
  ``hash:<24 hex>``) is unique per account among live lines, so an overlapping download never duplicates a line, and a
  reversal (which clears ``live``) releases the keys.
- ``bank_line_events``: append-only history of each line (imported, seen again in a later file, and later sorting).
- ``bank_feed_accounts``: what an account last received from an OFX file (bank number, last four digits of the
  account id, statement or card), checked against each new file.
"""
import sqlalchemy as sa

LINE_STATES = ('waiting', 'matched', 'added', 'transfer', 'ignored', 'settled')
LINK_HOWS = ('auto', 'person', 'agent', 'rule')
FORMATS = ('ofx', 'qfx', 'csv', 'feed')
EVENT_KINDS = ('imported', 'seen_again', 'matched', 'unmatched', 'added', 'transferred', 'ignored', 'unignored',
               'released', 'reversed')
KINDS = ('bank', 'card')


def define_tables(metadata, C, T):
    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    imports = T('bank_feed_imports',
        ident('id', 'Stable ULID of this import.', primary_key=True),
        ident('batch_id', 'The batch (kind bank-import) this import is reversed through.', 'batches.id'),
        ident('account_id', 'Bank or credit card account the lines came into.', 'accounts.id'),
        C('format', sa.String(8), f'File format: {", ".join(FORMATS)}.', nullable=False),
        C('file_sha256', sa.String(64), 'SHA-256 of the file text as given; a pasted file is not kept.',
          nullable=False),
        ident('attachment_id', 'The attachment the file was read from; null for pasted text.', 'attachments.id',
              nullable=True),
        C('mapping_name', sa.String(80), 'Saved CSV mapping used, by name; null when none was named.', nullable=True),
        C('date_from', sa.String(10), 'Earliest line date in the file.', nullable=True),
        C('date_to', sa.String(10), 'Latest line date in the file.', nullable=True),
        C('lines_in_file', sa.Integer, 'Lines the file holds.', nullable=False),
        C('lines_new', sa.Integer, 'Lines this import brought into the inbox.', nullable=False),
        C('counts', sa.Text, 'Outcome counts as JSON: new, already in the inbox, already in a reconciliation, '
                             'repeated in the file, soft duplicates, matched, settled, waiting.', nullable=False),
        C('warnings', sa.Text, 'Warnings shown with the import, as a JSON array.', nullable=False),
        ident('moved_from', 'The reversed import this one re-imports (a move); null otherwise.',
              'bank_feed_imports.id', nullable=True),
        C('reason', sa.String(500), 'Why it was imported, as given.', nullable=True),
        C('created_by', sa.String(26), 'Principal that imported it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it came through.', nullable=False),
        C('on_behalf_of', sa.String(26), 'The person an agent imported for; null for a person.', nullable=True),
        C('created_at', sa.String(32), 'UTC time of the import.', nullable=False),
        ident('audit_event_id', 'Audit event of the import.', 'audit_events.id'),
        sa.CheckConstraint(f'format IN ({quoted(FORMATS)})', name='ck_bank_feed_import_format'),
        sa.CheckConstraint('length(file_sha256) = 64', name='ck_bank_feed_import_sha'),
        sa.CheckConstraint('lines_in_file >= 0 AND lines_new >= 0', name='ck_bank_feed_import_counts'),
        sa.CheckConstraint("json_valid(counts) AND json_type(counts) = 'object' AND json_valid(warnings) "
                           "AND json_type(warnings) = 'array'", name='ck_bank_feed_import_json'),
        sa.Index('ix_bank_feed_imports_account', 'account_id', 'created_at'),
        sa.Index('ix_bank_feed_imports_file', 'account_id', 'file_sha256'),
        description='Bank statement files brought into the bank inbox, one row per import; never changed.')
    lines = T('bank_lines',
        ident('id', 'Stable ULID of this line.', primary_key=True),
        ident('import_id', 'The import that brought the line in.', 'bank_feed_imports.id'),
        C('ordinal', sa.Integer, "Position among that import's new lines, from 0.", nullable=False),
        ident('account_id', 'Account the line belongs to.', 'accounts.id'),
        C('line_key', sa.String(120), 'fitid:<FITID> (with !<amount hash> when the bank reused the id for another '
                                      'amount), or hash:<24 hex> of the line and its occurrence in the file.',
          nullable=False),
        C('fitid', sa.String(255), "The bank's own transaction id; null when the file gave none.", nullable=True),
        C('date', sa.String(10), 'Posted date, YYYY-MM-DD.', nullable=False),
        C('amount', sa.Integer, "Minor units in the bank's sign: positive is money in, or a card payment.",
          nullable=False),
        C('payee', sa.Text, 'Payee or description as the bank printed it.', nullable=False),
        C('memo', sa.Text, 'Memo as the bank printed it.', nullable=False),
        C('number', sa.String(64), 'Check or reference number as the bank printed it.', nullable=False),
        C('kind', sa.String(32), "OFX transaction type (DEBIT, CHECK...); empty for CSV.", nullable=False),
        C('state', sa.String(10), f'Where the line stands: {", ".join(LINE_STATES)}.', nullable=False),
        C('live', sa.Integer, '1 while its import stands; 0 once the import was reversed (its key is released).',
          nullable=False),
        C('version', sa.Integer, 'Optimistic-concurrency version of this line.', nullable=False),
        ident('entry_id', 'The transaction the line is matched to (or, later, was added as); null while none.',
              'transactions.id', nullable=True),
        C('movement', sa.Text, 'The matched movement (its reconciliation key) as JSON; null while none.',
          nullable=True),
        C('link_how', sa.String(8), f'How the link was made: {", ".join(LINK_HOWS)}; null while none.',
          nullable=True),
        C('match_reason', sa.String(500), 'Why it matched, or what it was suggested against.', nullable=True),
        ident('possible_duplicate_of', 'A live line of the account this one probably repeats with cleaned-up text; '
                                       'null when none.', 'bank_lines.id', nullable=True),
        C('warnings', sa.Text, 'Warnings on the line (fitid_reused, possible_duplicate), as a JSON array.',
          nullable=False),
        sa.CheckConstraint(f'state IN ({quoted(LINE_STATES)})', name='ck_bank_line_state'),
        sa.CheckConstraint(f'link_how IS NULL OR link_how IN ({quoted(LINK_HOWS)})', name='ck_bank_line_how'),
        sa.CheckConstraint('live IN (0, 1) AND version >= 1 AND ordinal >= 0', name='ck_bank_line_values'),
        sa.CheckConstraint("typeof(amount) = 'integer'", name='ck_bank_line_amount'),
        sa.CheckConstraint("json_valid(warnings) AND json_type(warnings) = 'array'", name='ck_bank_line_warnings'),
        sa.UniqueConstraint('import_id', 'ordinal', name='uq_bank_line_import_ordinal'),
        sa.Index('uq_bank_lines_live_key', 'account_id', 'line_key', unique=True, sqlite_where=sa.text('live = 1')),
        sa.Index('ix_bank_lines_account_state', 'account_id', 'state', 'date'),
        sa.Index('ix_bank_lines_entry', 'entry_id'),
        sa.Index('ix_bank_lines_fitid', 'account_id', 'fitid'),
        description='Bank inbox lines: each transaction a statement file showed, kept in the bank\'s sign with its '
                    'state. Never deleted.')
    events = T('bank_line_events',
        ident('id', 'Stable ULID of this event.', primary_key=True),
        ident('line_id', 'The line.', 'bank_lines.id'),
        C('kind', sa.String(16), f'What happened: {", ".join(EVENT_KINDS)}.', nullable=False),
        ident('import_id', 'The import it happened in, when one.', 'bank_feed_imports.id', nullable=True),
        ident('batch_id', 'The batch it happened in, when one.', 'batches.id', nullable=True),
        C('details', sa.Text, 'What changed and why, as a JSON object.', nullable=False),
        C('created_by', sa.String(26), 'Principal that did it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it came through.', nullable=False),
        C('created_at', sa.String(32), 'UTC time it happened.', nullable=False),
        ident('audit_event_id', 'Audit event of the act.', 'audit_events.id'),
        sa.CheckConstraint(f'kind IN ({quoted(EVENT_KINDS)})', name='ck_bank_line_event_kind'),
        sa.CheckConstraint("json_valid(details) AND json_type(details) = 'object'", name='ck_bank_line_event_json'),
        sa.Index('ix_bank_line_events_line', 'line_id'),
        description='Append-only history of bank inbox lines: who did what to each line, and why.')
    accounts = T('bank_feed_accounts',
        ident('account_id', 'The account.', 'accounts.id', primary_key=True),
        C('bank_number', sa.String(64), "The bank's routing number the account's OFX files carry (BANKID); null for "
                                        'a card or when none was given.', nullable=True),
        C('account_last4', sa.String(4), 'Last four characters of the OFX ACCTID the account receives.',
          nullable=True),
        C('kind', sa.String(8), f'Statement kind the files carry: {", ".join(KINDS)}.', nullable=True),
        ident('last_import_id', "The account's newest import.", 'bank_feed_imports.id', nullable=True),
        C('updated_at', sa.String(32), 'UTC time of the last change.', nullable=False),
        C('version', sa.Integer, 'Optimistic-concurrency version.', nullable=False),
        sa.CheckConstraint(f'kind IS NULL OR kind IN ({quoted(KINDS)})', name='ck_bank_feed_account_kind'),
        description='What each feed account last received: the bank number, last four account digits and kind an OFX '
                    'file carried, checked against every later file.')
    return {'bank_feed_imports': imports, 'bank_lines': lines, 'bank_line_events': events,
            'bank_feed_accounts': accounts}


_FIXED = ('id', 'import_id', 'ordinal', 'account_id', 'line_key', 'fitid', 'date', 'amount', 'payee', 'memo',
          'number', 'kind')
GUARDS = (
    "CREATE TRIGGER bank_feed_imports_no_update BEFORE UPDATE ON bank_feed_imports "
    "BEGIN SELECT RAISE(ABORT, 'bank feed imports are never changed'); END",
    "CREATE TRIGGER bank_feed_imports_no_delete BEFORE DELETE ON bank_feed_imports "
    "BEGIN SELECT RAISE(ABORT, 'bank feed imports are never deleted'); END",
    "CREATE TRIGGER bank_lines_fixed_update BEFORE UPDATE ON bank_lines WHEN "
    + ' OR '.join(f'NEW.{name} IS NOT OLD.{name}' for name in _FIXED)
    + " OR (OLD.live = 0 AND NEW.live = 1) BEGIN SELECT RAISE(ABORT, 'a bank line keeps what the bank printed'); END",
    "CREATE TRIGGER bank_lines_no_delete BEFORE DELETE ON bank_lines "
    "BEGIN SELECT RAISE(ABORT, 'bank lines are never deleted'); END",
    "CREATE TRIGGER bank_line_events_no_update BEFORE UPDATE ON bank_line_events "
    "BEGIN SELECT RAISE(ABORT, 'bank line events are append-only'); END",
    "CREATE TRIGGER bank_line_events_no_delete BEFORE DELETE ON bank_line_events "
    "BEGIN SELECT RAISE(ABORT, 'bank line events are append-only'); END",
    "CREATE TRIGGER bank_feed_accounts_no_delete BEFORE DELETE ON bank_feed_accounts "
    "BEGIN SELECT RAISE(ABORT, 'bank feed accounts are never deleted'); END",
)
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)
