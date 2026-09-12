"""co0041 lets a bill payment carry a cheque, and carries the numbers already typed into one.

A cheque written from Pay Bills is the same piece of paper as one written from Write Checks,
but co0040 gave a chequebook number only to the journal entry a check posts as.
``ap_payment_profiles.check_number`` held a number a person typed, which nothing allocated,
nothing made unique and ``report missing-checks`` could not place -- so a company that paid
bills by cheque still saw holes that were not holes. Two changes close that.

**The chequebook admits a second document.** ``ck_check_instrument_type`` allowed only
``journal_entry``. SQLite cannot alter a CHECK in place, so ``check_instruments`` is rebuilt
with that one constraint respelled and every value, row identity, index and trigger carried
across verbatim. Nothing else about the table moves: the account is still on every instrument
and ``uq_check_instrument_number`` is still unique on ``(account_id, check_number_key)`` where
``origin = 'issued'``, because a cheque number still belongs to the bank account it was
written from.

**Every number already typed on a bill payment is carried in.** One immutable
``check_instrument_revisions`` row per payment revision that carried one, and a
``check_instruments`` row for the revision the payment currently stands at, drawn on the
account that actually funded it. Nothing is renumbered and ``accounts.next_check_number`` is
left exactly as the person set it: the allocator walks past whatever is occupied, so the
pointer only ever suggests.

**What is carried in is marked ``migrated``, not ``issued``.** Those numbers were typed
rather than allocated, and nothing stopped one being typed twice -- on two bill payments, or
on a bill payment and a check. ``uq_check_instrument_number`` is partial on ``issued``
exactly so that such a file opens and ``report missing-checks`` can say it holds a number
twice, instead of the upgrade refusing and stranding the person. It also means a hole among
these cannot be told apart from one among the numbers co0040 carried over, which is what the
report's ``legacy_uncertain`` rows and its disclosure already say.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib

from alembic import op

revision = 'co0041'
down_revision = 'co0040'
branch_labels = None
depends_on = None

# The single clause of ck_check_instrument_type, matched against the exact statement co0040
# shipped. Any other spelling is somebody else's local object and stops the upgrade rather
# than being guessed at.
REPLACEMENTS = {
    'check_instruments': ("CHECK (type = 'journal_entry')",
                          "CHECK (type IN ('journal_entry', 'bill_payment'))"),
}
CHANGED = tuple(REPLACEMENTS)

# The stored spelling of a bill payment funded out of a bank account, frozen from the CHECK
# constraint co0026 put on the column. A card has no cheque to write a number on.
BANK_FUNDING = 'bank_cash'

# What a number an upgrade copied in is marked as, frozen from co0040's own enumeration.
MIGRATED = 'migrated'

# Every bill-payment revision that recorded a cheque number, with the account that funded it
# and whether it is the revision the payment currently stands at.
_TYPED = """
SELECT p.revision_id, p.transaction_id, p.funding_account_id, p.check_number,
       p.created_at, p.created_by, p.created_via, p.audit_event_id,
       t.current_revision_id, t.type
FROM ap_payment_profiles p
JOIN transactions t ON t.id = p.transaction_id AND t.type = p.type
WHERE p.check_number IS NOT NULL AND p.funding_kind = '%s'
ORDER BY p.transaction_id, p.revision_id
""" % BANK_FUNDING


def _rebuild(connection, table):
    """co0029's rebuild, with this revision's one substitution and its own reserved name."""
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0041 cannot preserve custom row identity')
    old, new = REPLACEMENTS[table]
    found = [i for i, part in enumerate(parts) if old in part]
    if len(found) != 1 or parts[found[0]].count(old) != 1:
        raise RuntimeError('co0041 unknown constraint: ' + table)
    parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0041_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    # Compared as (type, quoted literal, blob) so a rebuilt row that differs in storage class
    # or in a value SQLite would compare equal across affinities still fails the check.
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def canonical(number):
    """The number as typed, the key uniqueness compares, and the place it takes in a run.

    co0040's frozen copy of the rule, reused rather than copied a third time: what the two
    migrations write has to be the same canonical form or one company file would hold two
    spellings of one rule. Both are frozen migration text; neither is application metadata.
    """
    previous = importlib.import_module('bookflow.storage.company_migrations.versions.0040_check_numbers')
    return previous.canonical(number)


def upgrade():
    connection = op.get_bind()
    quote = importlib.import_module(
        'bookflow.storage.company_migrations.versions.0012_progress_billing')._quote
    reserved = {'_co0041_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0041 reserved object already exists')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    # Every local view and trigger, plus the indexes the rebuilt table owns: a trigger may name
    # the table from anywhere, and an index on it disappears with it. All are recreated verbatim.
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name IN ('check_instruments'))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0041_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0041 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for _, _, statement in retained:
        connection.exec_driver_sql(statement)
    revisions, instruments = [], []
    for row in connection.exec_driver_sql(_TYPED).fetchall():
        (revision_id, transaction_id, account_id, number, created_at, created_by, created_via,
         event, current_revision_id, transaction_type) = row
        literal, key, sequence = canonical(number)
        if not literal or len(literal) > 64:
            continue
        revisions.append((revision_id, transaction_id, account_id, literal, key, sequence,
                          MIGRATED, created_at, created_by, created_via, event))
        if revision_id == current_revision_id:
            instruments.append((transaction_id, transaction_type, revision_id, account_id,
                                literal, key, sequence, MIGRATED, created_at, created_by,
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
        raise RuntimeError('co0041 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
