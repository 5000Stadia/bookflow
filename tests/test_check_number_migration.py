"""co0040 carries every check already in a company file into its own chequebook.

The DDL in the migration is frozen text: it never imports the application's metadata, so the
only thing holding the two in step is this file compiling the metadata and comparing. The
rest is the preservation question. A company database already at the previous revision holds
checks whose numbers came from the shared document series, and the migration has to copy each
one -- with the account its own lines actually credit -- without renumbering anything,
without touching ``accounts.next_check_number``, and without guessing which of those numbers
were ever cheques at all.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.payment_raw_evidence import table
from tests.test_bill_payment_migration import _rebuilt_since, _superseded_after
from tests.test_vendor_credit_migration import _at, _insert

M = importlib.import_module('bookflow.storage.company_migrations.versions.0040_check_numbers')
PREVIOUS = M.down_revision


def test_the_migration_sits_on_the_chain():
    """Derived from the files, never a second copy of the number.

    This revision is a link in the chain, not its end: co0041 follows it and carries the
    cheque numbers typed on bill payments into the same chequebook, so asserting this one is
    the head is what would go stale on the next revision rather than what matters here.
    """
    assert M.revision in known_revisions('company')
    assert M.down_revision in known_revisions('company')
    assert M.revision in {getattr(module, 'down_revision', None)
                          for module in _later_modules()}


def _later_modules():
    """Every company migration that names another one as the revision it follows."""
    import pkgutil
    from bookflow.storage.company_migrations import versions
    return [importlib.import_module(versions.__name__ + '.' + info.name)
            for info in pkgutil.iter_modules(versions.__path__)]


def test_frozen_ddl_is_the_current_metadata_and_every_composite_key_is_real():
    rebuilt = _rebuilt_since(M.revision)
    indexes = sorted([index for name in M.NEW_TABLES for index in c.metadata.tables[name].indexes],
                     key=lambda index: index.name)
    compiled = tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=dialect())).strip()
                     for name in M.NEW_TABLES if name not in rebuilt)
    compiled += tuple(str(CreateIndex(index).compile(dialect=dialect())).strip() for index in indexes)
    assert tuple(statement for statement in M.DDL
                 if not any(statement.startswith(f'CREATE TABLE {name} (') for name in rebuilt)) == compiled
    superseded = _superseded_after(M.revision) & {statement.split()[2] for statement in M.GUARDS}
    assert not superseded
    for name in M.NEW_TABLES:
        for key in c.metadata.tables[name].foreign_key_constraints:
            target = key.referred_table
            columns = tuple(element.column.name for element in key.elements)
            allowed = [tuple(target.primary_key.columns.keys())] + [
                tuple(constraint.columns.keys()) for constraint in target.constraints
                if constraint.__class__.__name__ == 'UniqueConstraint']
            assert columns in allowed, (name, columns)


def test_the_canonical_rule_is_frozen_here_and_matches_what_the_application_writes():
    """Same rule, two copies on purpose: the migration's must not move when the module's does."""
    from bookflow.company import check_numbers
    for number in ('1001', '01001', '0001', 'EFT', '1001-A', '9' * 18, '9' * 19):
        assert M.canonical(number) == check_numbers.canonical(number)
    assert M.canonical('01001') == ('01001', '1001', 1001)
    assert M.canonical('EFT') == ('EFT', 'eft', None)


def test_a_fresh_database_reaches_the_head_with_an_empty_chequebook(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert all(db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)
                   for name in M.NEW_TABLES)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_the_revision_history_is_immutable_once_written(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        db.raw.execute('PRAGMA foreign_keys=OFF')
        db.raw.execute("INSERT INTO check_instrument_revisions (revision_id, transaction_id,"
                       " account_id, check_number, check_number_key, check_sequence, origin,"
                       " created_at, created_by, created_via, audit_event_id)"
                       " VALUES ('R1','T1','A1','1001','1001',1001,'issued','now','U','cli','E1')")
        for statement in ("UPDATE check_instrument_revisions SET check_number='1002'",
                          'DELETE FROM check_instrument_revisions'):
            with pytest.raises(sqlite3.IntegrityError):
                db.raw.execute(statement)


# ---------------------------------------------------------------- a populated company file

# Two bank accounts, and the numbers each of their cheques carried before the upgrade. The
# numbers are deliberately interleaved across the two accounts and across a non-cheque
# journal entry, because that is exactly the shape that made the old report lie: 1002 went to
# a hand-typed journal entry, and the old report called it a missing cheque on the account
# that happened to hold 1001 and 1003.
LEGACY = (
    # transaction, revision, number, funding account, is a check
    ('T1', 'R1', '1001', 'A1', True),
    ('T2', 'R2', '1002', 'A1', False),
    ('T3', 'R3', '1003', 'A1', True),
    ('T4', 'R4', '1004', 'A2', True),
    ('T5', 'R5', 'EFT-9', 'A1', True),
)


def _populate(raw):
    raw.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind,"
                " on_behalf_of, interface, client_name, client_version, client_host,"
                " session_id, request_id, idempotency_key, reason, directive_id,"
                " directive_code, source_ref, undo_of_event_id, summary)"
                " VALUES ('E1', 1, 'now', 'check post', 'U', 'user', NULL, 'cli', 'x', '1',"
                " 'h', 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'post check 1001')")
    for account, name in (('A1', 'Checking'), ('A2', 'Payroll'), ('A9', 'Supplies')):
        _insert(raw, 'accounts', id=account, name=name, name_key=name.lower(), full_name=name,
                full_name_key=name.lower(), depth=1, path=name, active=1, currency='USD',
                type='bank' if account != 'A9' else 'expense',
                next_check_number='5000' if account == 'A1' else None)
    for transaction, revision, number, funding, is_check in LEGACY:
        _insert(raw, 'transactions', id=transaction, version=1, type='journal_entry',
                number=number, current_revision_id=revision, status='posted')
        _insert(raw, 'transaction_revisions', id=revision, transaction_id=transaction,
                revision_number=1, supersedes_revision_id=None, date='2026-03-04', number=number,
                name_type=None, name_id=None, memo=None, total_minor_units=100, currency='USD',
                issuer_snapshot='{}', custom_fields_snapshot='{}', audit_event_id='E1')
        for position, (account, side) in enumerate(((funding, 'credit'), ('A9', 'debit')), 1):
            line = f'{revision}L{position}'
            _insert(raw, 'document_line_identities', id=line, transaction_id=transaction)
            _insert(raw, 'document_lines', id=f'{line}D', transaction_id=transaction,
                    revision_id=revision, line_id=line, position=position, kind='journal',
                    account_id=account, side=side, amount_minor_units=100, currency='USD',
                    account_snapshot='{}', name_type=None, name_id=None, party_name=None,
                    class_id=None, class_name=None, description=None)
        if is_check:
            raw.execute("INSERT INTO money_out_documents (transaction_id, type, kind, created_at,"
                        " created_by, created_via, audit_event_id)"
                        f" VALUES ('{transaction}', 'journal_entry', 'check', 'now', 'U', 'cli', 'E1')")


def _upgraded(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')
        _populate(raw)
    before = {}
    with sqlite3.connect(path) as raw:
        for name in ('transactions', 'transaction_revisions', 'document_lines',
                     'money_out_documents', 'accounts'):
            before[name] = table(raw, name)
    with open_database(path, writable=True, create=False) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == (PREVIOUS, HEADS['company'])
    return path, before


def test_every_stored_value_survives_and_no_cheque_is_renumbered(tmp_path):
    path, before = _upgraded(tmp_path)
    with sqlite3.connect(path) as raw:
        for name, rows in before.items():
            assert table(raw, name) == rows, name
        assert raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        # The account's own next check number is what the person set, not something inferred
        # from journal entries that were never cheques.
        assert raw.execute("SELECT next_check_number FROM accounts ORDER BY id").fetchall() == [
            ('5000',), (None,), (None,)]


def test_each_cheque_lands_in_the_chequebook_its_own_lines_credit(tmp_path):
    path, _ = _upgraded(tmp_path)
    with sqlite3.connect(path) as raw:
        carried = raw.execute("SELECT transaction_id, account_id, check_number,"
                              " check_number_key, check_sequence, origin FROM check_instruments"
                              " ORDER BY transaction_id").fetchall()
    # T2 is the hand-typed journal entry that took 1002 from the shared series: it carries no
    # cheque marker, so it gets no cheque number and can never read as a hole again.
    assert carried == [
        ('T1', 'A1', '1001', '1001', 1001, 'migrated'),
        ('T3', 'A1', '1003', '1003', 1003, 'migrated'),
        ('T4', 'A2', '1004', '1004', 1004, 'migrated'),
        ('T5', 'A1', 'EFT-9', 'eft-9', None, 'migrated'),
    ]


def test_the_identity_is_on_the_revision_as_well_as_on_the_projection(tmp_path):
    path, _ = _upgraded(tmp_path)
    with sqlite3.connect(path) as raw:
        written = raw.execute("SELECT revision_id, transaction_id, account_id, check_number,"
                              " origin, audit_event_id FROM check_instrument_revisions"
                              " ORDER BY revision_id").fetchall()
    assert written == [
        ('R1', 'T1', 'A1', '1001', 'migrated', 'E1'),
        ('R3', 'T3', 'A1', '1003', 'migrated', 'E1'),
        ('R4', 'T4', 'A2', '1004', 'migrated', 'E1'),
        ('R5', 'T5', 'A1', 'EFT-9', 'migrated', 'E1'),
    ]


def test_two_carried_numbers_that_are_one_place_do_not_stop_the_upgrade(tmp_path):
    """`1001` and `01001` on one account are one number written twice, and a real file can
    already hold both. Refusing to open that file would be refusing to tell the person."""
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')
        _populate(raw)
        _insert(raw, 'transactions', id='T6', version=1, type='journal_entry', number='01001',
                current_revision_id='R6', status='posted')
        _insert(raw, 'transaction_revisions', id='R6', transaction_id='T6', revision_number=1,
                supersedes_revision_id=None, date='2026-03-04', number='01001', name_type=None,
                name_id=None, memo=None, total_minor_units=100, currency='USD',
                issuer_snapshot='{}', custom_fields_snapshot='{}', audit_event_id='E1')
        for position, (account, side) in enumerate((('A1', 'credit'), ('A9', 'debit')), 1):
            line = f'R6L{position}'
            _insert(raw, 'document_line_identities', id=line, transaction_id='T6')
            _insert(raw, 'document_lines', id=f'{line}D', transaction_id='T6', revision_id='R6',
                    line_id=line, position=position, kind='journal', account_id=account,
                    side=side, amount_minor_units=100, currency='USD', account_snapshot='{}',
                    name_type=None, name_id=None, party_name=None, class_id=None,
                    class_name=None, description=None)
        raw.execute("INSERT INTO money_out_documents (transaction_id, type, kind, created_at,"
                    " created_by, created_via, audit_event_id)"
                    " VALUES ('T6', 'journal_entry', 'check', 'now', 'U', 'cli', 'E1')")
    with open_database(path, writable=True, create=False) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == (PREVIOUS, HEADS['company'])
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT count(*) FROM check_instruments WHERE check_number_key='1001'"
                           " AND account_id='A1'").fetchone() == (2,)
        # What Bookflow issues from here on still cannot collide: the constraint covers those.
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("INSERT INTO check_instruments (transaction_id, type, revision_id,"
                        " account_id, check_number, check_number_key, check_sequence, origin,"
                        " updated_at, updated_by, updated_via, audit_event_id) VALUES"
                        " ('T7','journal_entry','R7','A1','7','7',7,'issued','now','U','cli','E1'),"
                        " ('T8','journal_entry','R8','A1','07','7',7,'issued','now','U','cli','E1')")
