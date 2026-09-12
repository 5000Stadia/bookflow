"""co0041 carries the cheque numbers already typed on bill payments into the chequebook.

Until this revision a bill payment held its check number as free text on
``ap_payment_profiles``: nothing allocated it, nothing made it unique, and
``report missing-checks`` could not place it. This migration widens the one CHECK that kept
``check_instruments`` to journal entries and copies every typed number in, so the two
documents that print a cheque keep their numbers in one place.

The preservation questions are the interesting ones. A pre-upgrade file can already hold one
number twice -- the same number typed on two payments, or typed on a payment and written on a
check -- because nothing ever stopped it, and such a file has to open so the report can say
so. And ``check_instruments`` is rebuilt rather than altered, so everything it held, and
every index and trigger around it, has to come out the other side unchanged.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable

from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.payment_raw_evidence import table
from tests.test_check_number_migration import _populate as _populate_checks
from tests.test_vendor_credit_migration import _at, _insert

M = importlib.import_module('bookflow.storage.company_migrations.versions.0041_bill_payment_cheques')
CO0040 = importlib.import_module('bookflow.storage.company_migrations.versions.0040_check_numbers')

# The revision a company file sits at before any of this: the last one before cheques had a
# table of their own. Upgrading from here runs co0040 and then co0041, which is the path a
# real file takes and the only one that produces both kinds of carried-over number.
BEFORE_CHEQUES = CO0040.down_revision


def _chain_after(revision):
    """Every revision reachable from `revision` by following down_revision links forward."""
    import re
    from pathlib import Path
    versions = Path(__file__).resolve().parents[1] / 'src/bookflow/storage/company_migrations/versions'
    links = {}
    for path in sorted(versions.glob('0*.py')):
        text = path.read_text()
        rev = re.search(r"^revision = '([^']+)'", text, re.M)
        down = re.search(r"^down_revision = (?:'([^']+)'|None)", text, re.M)
        if rev:
            links[rev.group(1)] = down.group(1) if down else None
    forward, current = set(), revision
    successors = {down: rev for rev, down in links.items() if down}
    while current in successors:
        current = successors[current]
        forward.add(current)
    return forward


def test_the_migration_sits_on_the_chain_and_follows_the_one_it_amends():
    """Derived from the files, never a second copy of the number."""
    assert M.revision in known_revisions('company')
    assert M.down_revision == CO0040.revision
    # Deliberately NOT `HEADS['company'] == M.revision`. Being the newest migration is something
    # every migration can claim exactly until the next one lands -- this one stopped being head
    # the moment co0043 was repointed onto it. What matters permanently is that it is on the chain
    # and that it follows the migration it amends, which the two assertions above pin.
    assert M.revision in _chain_after(CO0040.revision)


def test_the_substitution_lands_on_the_shape_the_application_declares():
    """The rebuild edits stored text, so there is no frozen DDL here to compare against.

    What has to hold instead is that the clause it puts in is the clause today's metadata
    compiles, and the clause it takes out is one nothing declares any more. A file upgraded
    through the rebuild is compared against a freshly created one below.
    """
    old, new = M.REPLACEMENTS['check_instruments']
    compiled = str(CreateTable(c.metadata.tables['check_instruments']).compile(dialect=dialect())).strip()
    assert old not in compiled and new in compiled


def test_the_canonical_rule_is_the_one_co0040_froze():
    """One canonical form per company file: two migrations writing two would be two numbers."""
    from bookflow.company import check_numbers
    for number in ('1001', '01001', '0001', 'EFT', '1001-A', '9' * 18, '9' * 19):
        assert M.canonical(number) == CO0040.canonical(number) == check_numbers.canonical(number)


def test_a_fresh_database_reaches_the_head_and_admits_both_documents(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        stored = db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='check_instruments'").fetchone()[0]
        assert "CHECK (type IN ('journal_entry', 'bill_payment'))" in stored
        assert db.raw.execute('SELECT count(*) FROM check_instruments').fetchone() == (0,)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


# ---------------------------------------------------------------- a populated company file

# The bill payments this file already holds, and the number a person typed on each. `P2`
# deliberately types `01001`, which is the number check `T1` already carries on Checking: the
# file predates any rule against that, and it has to open anyway.
#
# payment, revision, funding account, funding kind, typed number
PAYMENTS = (
    ('P1', 'RB1', 'A1', 'bank_cash', '1005'),
    ('P2', 'RB2', 'A1', 'bank_cash', '01001'),
    ('P3', 'RB3', 'A2', 'bank_cash', None),
    ('P4', 'RB4', 'A8', 'card_liability', None),
    ('P5', 'RB5', 'A1', 'bank_cash', '   '),
)


def _populate(raw):
    _populate_checks(raw)
    for account, name, kind in (('A7', 'Accounts Payable', 'accounts_payable'),
                                ('A8', 'Company Card', 'credit_card')):
        _insert(raw, 'accounts', id=account, name=name, name_key=name.lower(), full_name=name,
                full_name_key=name.lower(), depth=1, path=name, active=1, currency='USD', type=kind)
    _insert(raw, 'vendors', id='V1', name='Northside Supply', name_key='northside supply',
            active=1)
    _insert(raw, 'payment_methods', id='M1', name='Check', name_key='check', kind='check', active=1)
    for payment, revision, funding, funding_kind, number in PAYMENTS:
        _insert(raw, 'transactions', id=payment, version=1, type='bill_payment',
                number=payment, current_revision_id=revision, status='posted')
        _insert(raw, 'transaction_revisions', id=revision, transaction_id=payment,
                revision_number=1, supersedes_revision_id=None, date='2026-03-06', number=payment,
                name_type='vendor', name_id='V1', memo=None, total_minor_units=500,
                currency='USD', issuer_snapshot='{}', custom_fields_snapshot='{}',
                audit_event_id='E1')
        _insert(raw, 'ap_payment_profiles', revision_id=revision, transaction_id=payment,
                created_at='now', created_by='U', created_via='cli', audit_event_id='E1',
                type='bill_payment', vendor_id='V1', ap_account_id='A7',
                funding_account_id=funding, funding_kind=funding_kind, payment_method_id='M1',
                check_number=number, reference=None, amount_minor_units=500,
                profile_snapshot='{}')


def _upgraded(tmp_path, name='company.db'):
    path = tmp_path / name
    _at(path, BEFORE_CHEQUES)
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')
        _populate(raw)
    before = {}
    with sqlite3.connect(path) as raw:
        for stored in ('transactions', 'transaction_revisions', 'document_lines',
                       'money_out_documents', 'ap_payment_profiles', 'accounts'):
            before[stored] = table(raw, stored)
    with open_database(path, writable=True, create=False) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == (BEFORE_CHEQUES, HEADS['company'])
    return path, before


def test_every_stored_value_survives_and_no_cheque_is_renumbered(tmp_path):
    path, before = _upgraded(tmp_path)
    with sqlite3.connect(path) as raw:
        for name, rows in before.items():
            assert table(raw, name) == rows, name
        assert raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        # The chequebook pointer is still what the person set: nothing here infers one, and
        # the allocator walks past whatever is occupied anyway.
        assert raw.execute('SELECT next_check_number FROM accounts ORDER BY id').fetchall() == [
            ('5000',), (None,), (None,), (None,), (None,)]


def test_every_number_typed_on_a_bill_payment_is_in_the_chequebook_it_was_drawn_on(tmp_path):
    path, _ = _upgraded(tmp_path)
    with sqlite3.connect(path) as raw:
        carried = raw.execute(
            "SELECT transaction_id, type, account_id, check_number, check_number_key,"
            " check_sequence, origin FROM check_instruments WHERE type='bill_payment'"
            " ORDER BY transaction_id").fetchall()
    # P3 and P4 typed no number and P5 typed blanks, so none of them is a cheque. P4 could not
    # have been one anyway: a credit card has no cheque to write a number on.
    assert carried == [
        ('P1', 'bill_payment', 'A1', '1005', '1005', 1005, 'migrated'),
        ('P2', 'bill_payment', 'A1', '01001', '1001', 1001, 'migrated'),
    ]


def test_the_identity_is_on_the_revision_as_well_as_on_the_projection(tmp_path):
    path, _ = _upgraded(tmp_path)
    with sqlite3.connect(path) as raw:
        written = raw.execute(
            "SELECT revision_id, transaction_id, account_id, check_number, origin,"
            " audit_event_id FROM check_instrument_revisions WHERE transaction_id LIKE 'P%'"
            " ORDER BY revision_id").fetchall()
    assert written == [('RB1', 'P1', 'A1', '1005', 'migrated', 'E1'),
                       ('RB2', 'P2', 'A1', '01001', 'migrated', 'E1')]


def test_a_number_typed_twice_before_the_upgrade_does_not_stop_it(tmp_path):
    """`01001` on a bill payment and `1001` on a check are one number on one chequebook.

    Nothing ever stopped a person typing that, so the file has to open -- the alternative is
    refusing to upgrade a real company and stranding them. Both rows are kept, both are
    marked `migrated`, and `report missing-checks` names the number as a duplicate.
    """
    path, _ = _upgraded(tmp_path)
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT transaction_id, origin FROM check_instruments"
                           " WHERE account_id='A1' AND check_number_key='1001'"
                           " ORDER BY transaction_id").fetchall() == [
            ('P2', 'migrated'), ('T1', 'migrated')]


def test_the_rebuilt_table_is_the_one_a_fresh_company_gets(tmp_path):
    """An upgraded file and a new one have to be the same file, or one of them is wrong."""
    upgraded, _ = _upgraded(tmp_path)
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        fresh = db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='check_instruments'").fetchone()[0]
    with sqlite3.connect(upgraded) as raw:
        rebuilt = raw.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='check_instruments'").fetchone()[0]
    # Fresh setup traverses the same migration, including SQLite's quoting on rename.
    # Compare both stored definitions verbatim; changing only one invents a difference.
    assert rebuilt == fresh


def test_the_rebuild_keeps_the_indexes_the_triggers_and_the_duplicate_refusal(tmp_path):
    path, _ = _upgraded(tmp_path)
    with sqlite3.connect(path) as raw:
        indexes = {name for (name,) in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='index' AND tbl_name='check_instruments'"
            " AND sql IS NOT NULL")}
        assert indexes == {'ix_check_instruments_sequence', 'uq_check_instrument_number'}
        assert {name for (name,) in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger'")} >= {
            'check_instrument_revisions_immutable_update',
            'check_instrument_revisions_immutable_delete'}
        # What the chequebook issues from here on still cannot collide, on either document.
        raw.execute('PRAGMA foreign_keys=OFF')
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("INSERT INTO check_instruments (transaction_id, type, revision_id,"
                        " account_id, check_number, check_number_key, check_sequence, origin,"
                        " updated_at, updated_by, updated_via, audit_event_id) VALUES"
                        " ('X1','journal_entry','RX1','A1','7','7',7,'issued','now','U','cli','E1'),"
                        " ('X2','bill_payment','RX2','A1','07','7',7,'issued','now','U','cli','E1')")
        # And nothing but the two documents that print a cheque may carry one.
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("INSERT INTO check_instruments (transaction_id, type, revision_id,"
                        " account_id, check_number, check_number_key, check_sequence, origin,"
                        " updated_at, updated_by, updated_via, audit_event_id) VALUES"
                        " ('X3','invoice','RX3','A1','8','8',8,'issued','now','U','cli','E1')")


def test_a_number_on_a_superseded_revision_is_history_and_not_the_current_cheque(tmp_path):
    """The projection follows the revision the payment stands at; the rest stay revisions."""
    path = tmp_path / 'superseded.db'
    _at(path, BEFORE_CHEQUES)
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')
        _populate(raw)
        _insert(raw, 'transaction_revisions', id='RB1B', transaction_id='P1', revision_number=2,
                supersedes_revision_id='RB1', date='2026-03-07', number='P1', name_type='vendor',
                name_id='V1', memo=None, total_minor_units=500, currency='USD',
                issuer_snapshot='{}', custom_fields_snapshot='{}', audit_event_id='E1')
        _insert(raw, 'ap_payment_profiles', revision_id='RB1B', transaction_id='P1',
                created_at='now', created_by='U', created_via='cli', audit_event_id='E1',
                type='bill_payment', vendor_id='V1', ap_account_id='A7', funding_account_id='A1',
                funding_kind='bank_cash', payment_method_id='M1', check_number='1006',
                reference=None, amount_minor_units=500, profile_snapshot='{}')
        raw.execute("UPDATE transactions SET current_revision_id='RB1B' WHERE id='P1'")
    with open_database(path, writable=True, create=False) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == (BEFORE_CHEQUES, HEADS['company'])
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT check_number FROM check_instruments WHERE transaction_id='P1'"
                           ).fetchall() == [('1006',)]
        assert raw.execute("SELECT revision_id, check_number FROM check_instrument_revisions"
                           " WHERE transaction_id='P1' ORDER BY revision_id").fetchall() == [
            ('RB1', '1005'), ('RB1B', '1006')]
        assert raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
