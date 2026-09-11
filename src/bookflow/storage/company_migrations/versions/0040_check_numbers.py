"""co0040 gives a check its own account-scoped number, separate from the document series.

Until now a check drew its number from the shared journal series through
``document_effects.allocate``, so a number that series handed a journal entry, a transfer or
a card charge read as a hole in a chequebook and ``report missing-checks`` named false gaps on
any real company file. This migration adds the two tables that hold a cheque identity --
immutable per revision, projected per check -- and carries every check already in the file
into them.

**Nothing already stored changes.** Transaction ids, types and numbers are preserved exactly;
no table is rebuilt; no cheque is renumbered. Each historical check's number is copied from
the revision that captured it, and its account is the account that revision's first entered
line actually credits, so a check that was corrected onto another bank account lands in that
account's sequence and not in the one it was first written on.

**What carried over is marked ``migrated`` and is not the same evidence as what Bookflow
issues.** A hole between two migrated numbers cannot be told apart from a number the shared
series gave to something that was never a cheque, so the report discloses that rather than
naming those gaps as missing cheques. Nothing here guesses an account's next check number:
``accounts.next_check_number`` is left exactly as the person set it.

DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0040'
down_revision = 'co0038'
branch_labels = None
depends_on = None

NEW_TABLES = ('check_instrument_revisions', 'check_instruments')
DDL = ("CREATE TABLE check_instrument_revisions (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tcheck_number VARCHAR(64) NOT NULL, \n\tcheck_number_key VARCHAR(64) NOT NULL, \n\tcheck_sequence BIGINT, \n\torigin VARCHAR(16) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT ck_check_instrument_revision_origin CHECK (origin IN ('issued', 'migrated')), \n\tCONSTRAINT ck_check_instrument_revision_sequence CHECK (check_sequence IS NULL OR (typeof(check_sequence) = 'integer' AND check_sequence > 0)), \n\tCONSTRAINT ck_check_instrument_revision_number_length CHECK (length(check_number) BETWEEN 1 AND 64 AND length(check_number_key) BETWEEN 1 AND 64), \n\tCONSTRAINT fk_check_instrument_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT uq_check_instrument_revision_owner UNIQUE (transaction_id, revision_id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)", "CREATE TABLE check_instruments (\n\ttransaction_id VARCHAR(26) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tcheck_number VARCHAR(64) NOT NULL, \n\tcheck_number_key VARCHAR(64) NOT NULL, \n\tcheck_sequence BIGINT, \n\torigin VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (transaction_id), \n\tCONSTRAINT ck_check_instrument_origin CHECK (origin IN ('issued', 'migrated')), \n\tCONSTRAINT ck_check_instrument_sequence CHECK (check_sequence IS NULL OR (typeof(check_sequence) = 'integer' AND check_sequence > 0)), \n\tCONSTRAINT ck_check_instrument_number_length CHECK (length(check_number) BETWEEN 1 AND 64 AND length(check_number_key) BETWEEN 1 AND 64), \n\tCONSTRAINT ck_check_instrument_type CHECK (type = 'journal_entry'), \n\tCONSTRAINT fk_check_instrument_document_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tCONSTRAINT fk_check_instrument_revision_held FOREIGN KEY(transaction_id, revision_id) REFERENCES check_instrument_revisions (transaction_id, revision_id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)", 'CREATE INDEX ix_check_instrument_revisions_number ON check_instrument_revisions (account_id, check_number_key)', 'CREATE INDEX ix_check_instruments_sequence ON check_instruments (account_id, check_sequence)', "CREATE UNIQUE INDEX uq_check_instrument_number ON check_instruments (account_id, check_number_key) WHERE origin = 'issued'")
GUARDS = ("CREATE TRIGGER check_instrument_revisions_immutable_update BEFORE UPDATE ON check_instrument_revisions BEGIN SELECT RAISE(ABORT, 'immutable cheque identity'); END",
          "CREATE TRIGGER check_instrument_revisions_immutable_delete BEFORE DELETE ON check_instrument_revisions BEGIN SELECT RAISE(ABORT, 'immutable cheque identity'); END")
OBJECTS = ('check_instrument_revisions', 'check_instruments',
           'ix_check_instrument_revisions_number', 'ix_check_instruments_sequence',
           'uq_check_instrument_number',
           'check_instrument_revisions_immutable_update', 'check_instrument_revisions_immutable_delete')

# The stored spelling of the money-out marker this migration reads, frozen at co0040.
CHECK_KIND = 'check'
# How wide a run of digits still reads back as the integer it spells; longer is a cheque
# number with no place in a sequence. Frozen here so a later change to the application's
# constant cannot silently rewrite what this migration already wrote.
SEQUENCE_DIGITS = 18

# Every revision of every entered check, with the account its own first line credits.
_HISTORY = """
SELECT r.id, r.transaction_id, r.number, r.created_at, r.created_by, r.created_via,
       r.audit_event_id, l.account_id, t.current_revision_id, t.type
FROM money_out_documents m
JOIN transactions t ON t.id = m.transaction_id AND t.type = m.type
JOIN transaction_revisions r ON r.transaction_id = t.id
JOIN document_lines l ON l.revision_id = r.id AND l.position = 1
WHERE m.kind = '%s' AND l.account_id IS NOT NULL AND r.number IS NOT NULL AND r.number <> ''
ORDER BY r.transaction_id, r.revision_number
""" % CHECK_KIND


def canonical(number):
    """The number as typed, the key uniqueness compares, and the place it takes in a run.

    A copy of the rule as it stood at co0040, deliberately not imported: what this migration
    wrote must stay readable as what it wrote, whatever the application decides later.
    """
    literal = number.strip()
    if literal.isascii() and literal.isdigit() and 1 <= len(literal) <= SEQUENCE_DIGITS:
        value = int(literal)
        if value > 0:
            return literal, str(value), value
    return literal, literal.casefold(), None


def upgrade():
    connection = op.get_bind()
    reserved = {name.casefold() for name in OBJECTS}
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        quoted = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + quoted + '.sqlite_schema'):
            if row[0].casefold() in reserved:
                raise RuntimeError('co0040 reserved object already exists')
    for statement in (*DDL, *GUARDS):
        connection.exec_driver_sql(statement)
    revisions, instruments = [], []
    for row in connection.exec_driver_sql(_HISTORY).fetchall():
        (revision_id, transaction_id, number, created_at, created_by, created_via,
         event, account_id, current_revision_id, transaction_type) = row
        literal, key, sequence = canonical(number)
        if not literal or len(literal) > 64:
            continue
        revisions.append((revision_id, transaction_id, account_id, literal, key, sequence,
                          'migrated', created_at, created_by, created_via, event))
        if revision_id == current_revision_id:
            instruments.append((transaction_id, transaction_type, revision_id, account_id,
                                literal, key, sequence, 'migrated', created_at, created_by,
                                created_via, event))
    if revisions:
        connection.exec_driver_sql(
            'INSERT INTO check_instrument_revisions (revision_id, transaction_id, account_id,'
            ' check_number, check_number_key, check_sequence, origin, created_at, created_by,'
            ' created_via, audit_event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)', revisions)
    if instruments:
        connection.exec_driver_sql(
            'INSERT INTO check_instruments (transaction_id, type, revision_id, account_id,'
            ' check_number, check_number_key, check_sequence, origin, updated_at, updated_by,'
            ' updated_via, audit_event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', instruments)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0040 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
