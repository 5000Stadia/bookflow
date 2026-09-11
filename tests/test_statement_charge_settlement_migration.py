"""co0043 widens three insertion fences and one CHECK so a statement charge can be settled.

Nothing is created and nothing is backfilled. ``applications``, ``payment_selection_items`` and
``settlement_line_keys`` each carried a fence admitting the invoice and nothing else, and
``payment_selection_recovery_items`` carried the same rule as a CHECK. SQLite widens a CHECK only
by rebuilding the table, so the preservation question is the same one every rebuild in this chain
has to answer: a company database already at the previous revision carries rows, local tables,
indexes, views and triggers, and the rebuild has to happen underneath all of them without
touching a byte.

What this file will not let drift is the *width*: each stored fence is read back and compared to
``SETTLEABLE_RECEIVABLE_TYPES``, the one place the settlement contract declares which receivables
a customer's money can settle. A fence that admits a different set from the queries above it is
precisely how a settled charge disappears from a report whose total still balances.
"""
import importlib
import re
import sqlite3

import pytest

from bookflow.company import schema as c
from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.payment_raw_evidence import table
from tests.test_bill_payment_migration import _rebuilt_since
from tests.test_vendor_credit_migration import _at, _insert

M = importlib.import_module(
    'bookflow.storage.company_migrations.versions.0043_settleable_statement_charges')
PREVIOUS = M.down_revision

FENCES = ('applications_paid_transaction_id_type', 'payment_selection_items_invoice_id_type',
          'settlement_line_keys_transaction_id_type')


def _chain_head():
    """The last revision on the shipped chain, walked from its own `down_revision` pairs.

    Read rather than written down, because the chain is deliberately not in number order --
    co0034 -> co0037 -> co0035 -> co0036 -> co0038 onward -- so a revision's predecessor cannot
    be guessed from its filename.
    """
    import pkgutil
    from bookflow.storage.company_migrations import versions
    pairs = {}
    for info in pkgutil.iter_modules(versions.__path__):
        module = importlib.import_module(versions.__name__ + '.' + info.name)
        if hasattr(module, 'revision'):
            pairs[module.down_revision] = module.revision
    revision = None
    while revision in pairs:
        revision = pairs[revision]
    return revision


def _admitted(sql):
    """The document types a stored fence or CHECK actually admits."""
    return tuple(re.findall(r"'([a-z_]+)'", re.search(r'IN \(([^)]*)\)', sql).group(1)))


def test_the_migration_sits_on_the_chain_and_is_its_end():
    """Derived, never a second copy of the number: the chain is the only authority."""
    assert M.revision in known_revisions('company')
    assert M.down_revision in known_revisions('company')
    assert _chain_head() == M.revision == HEADS['company']
    assert M.NEW_TABLES == () and M.DDL == () and M.OBJECTS == ()


def test_each_widened_fence_admits_exactly_what_the_settlement_contract_declares():
    """The fence and the queries above it read one declaration, or they disagree in silence."""
    assert {statement.split()[2] for statement in M.GUARDS} == set(FENCES) == set(M.REPLACED)
    for statement in M.GUARDS:
        assert _admitted(statement) == SETTLEABLE_RECEIVABLE_TYPES, statement.split()[2]
    # And the rebuilt CHECK: its new text is today's metadata and its old text is not, so a
    # pair whose halves were both stale could not rebuild the table into a constraint
    # nobody wrote.
    for name, pairs in M.REPLACEMENTS.items():
        for old, new in pairs:
            assert _admitted(new) == SETTLEABLE_RECEIVABLE_TYPES
            constraint = new.split('CONSTRAINT ')[1].split(' ')[0]
            expression = str(next(x for x in c.metadata.tables[name].constraints
                                  if x.name == constraint).sqltext)
            assert expression in new and expression not in old, (name, constraint)


def test_a_fresh_database_reaches_the_head_and_a_second_run_changes_nothing(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_the_stored_fences_are_the_ones_the_migration_wrote(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        stored = dict(db.raw.execute(
            "SELECT name, sql FROM sqlite_schema WHERE type='trigger' AND name IN (%s)"
            % ','.join('?' * len(FENCES)), FENCES).fetchall())
        assert set(stored) == set(FENCES)
        for name, sql in stored.items():
            assert _admitted(sql) == SETTLEABLE_RECEIVABLE_TYPES, name
        check = next(line for line in db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='payment_selection_recovery_items'"
        ).fetchone()[0].split('\n') if 'ck_recovery_invoice_type' in line)
        assert _admitted(check) == SETTLEABLE_RECEIVABLE_TYPES
        # Every index on the rebuilt table survived it.
        assert {row[0] for row in db.raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            " AND tbl_name='payment_selection_recovery_items'")} == {
            'ix_recovery_item_invoice', 'ix_recovery_item_selection'}


def _documents(raw):
    """One transaction of every type, so a fence can be asked about each of them."""
    _insert(raw, 'audit_events', id='E1', seq=1, at='now', command='x', actor_id='U',
            actor_kind='user', interface='cli', client_name='x', client_version='1',
            client_host='h', session_id='S1', request_id='Q1', summary='x')
    for identifier, kind in (('T_INV', 'invoice'), ('T_SC', 'statement_charge'),
                             ('T_SR', 'sales_receipt')):
        _insert(raw, 'transactions', id=identifier, version=1, type=kind, number=identifier,
                current_revision_id=None, status='posted')


@pytest.mark.parametrize('target,accepted', [('T_SC', True), ('T_INV', True), ('T_SR', False)])
def test_the_settlement_fences_admit_a_charge_and_still_refuse_a_sales_receipt(tmp_path, target, accepted):
    """The fences themselves, exercised: the other guards on these tables are taken out of the
    way first so that what accepts or refuses the row is the one rule this revision rewrote."""
    path = tmp_path / 'fresh.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')
        for name in ('applications_exact_party', 'applications_one_source',
                     'applications_exact_inverse', 'payment_selection_recovery_items_uploading'):
            raw.execute('DROP TRIGGER ' + name)
        _documents(raw)
        writes = [
            lambda key: _insert(raw, 'applications', id='A' + key, kind='apply',
                                paying_transaction_id='T_PAY', paid_transaction_id=target,
                                amount_minor_units=100, currency='USD', effective_date='2026-06-01',
                                audit_event_id='E1'),
            lambda key: _insert(raw, 'payment_selection_items', id='I' + key, selection_id='S1',
                                revision_id='V1', kind='remove', invoice_id=target,
                                audit_event_id='E1'),
            lambda key: _insert(raw, 'settlement_line_keys', id='K' + key, transaction_id=target,
                                line_id='L' + key, ordinal=1, audit_event_id='E1'),
            lambda key: _insert(raw, 'payment_selection_recovery_items', id='R' + key,
                                selection_id='S1', recovery_id='Y1', entry_index=1,
                                invoice_id=target, invoice_type=(
                                    'invoice' if target == 'T_INV' else
                                    'statement_charge' if target == 'T_SC' else 'sales_receipt'),
                                action='remove', observed_invoice_version=1, audit_event_id='E1'),
        ]
        for index, write in enumerate(writes):
            key = target + str(index)
            if accepted:
                write(key)
                continue
            with pytest.raises(sqlite3.IntegrityError):
                write(key)


def test_a_populated_previous_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
        _documents(raw)
        _insert(raw, 'payment_selections', id='S1', version=1, state='open',
                current_revision_id='V1')
        _insert(raw, 'payment_selection_revisions', id='V1', selection_id='S1', version=1,
                context_snapshot='{}', manifest_hash='x', audit_event_id='E1')
        _insert(raw, 'payment_selection_recoveries', id='Y1', version=1, selection_id='S1',
                recovery_key='K1', request_schema_version=1,
                attempt_generation='00000000-0000-0000-0000-000000000000',
                local_baseline_revision_id='V1', anchor_revision_id='V1',
                anchor_selection_version=1, header_intent='{}', declared_entry_count=1,
                intent_hash='0' * 64, state='uploading', begin_request_snapshot='{}',
                begin_receipt_snapshot='{}', begin_request_hash='0' * 64, audit_event_id='E1')
        # The rebuilt table's own row, with an embedded NUL a quote()-only copy would truncate.
        raw.execute("INSERT INTO payment_selection_recovery_items (id, selection_id, recovery_id,"
                    " entry_index, invoice_id, invoice_type, action, observed_invoice_version,"
                    " created_at, created_by, created_via, audit_event_id)"
                    " VALUES ('R1', 'S1', 'Y1', 1, 'T_INV', 'invoice', 'remove', 1, ?, 'U',"
                    " 'cli', 'E1')", ('A\x00B',))
        raw.execute('CREATE TABLE local_ss_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_ss_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_ss_expression ON local_ss_bytes (length(t))')
        raw.execute('CREATE INDEX local_ss_on_items ON payment_selection_recovery_items (action)')
        raw.execute('CREATE VIEW local_ss_view AS SELECT id, invoice_type'
                    ' FROM payment_selection_recovery_items')
        raw.execute('CREATE TRIGGER local_ss_trigger AFTER INSERT ON local_ss_bytes'
                    ' BEGIN SELECT 1; END')
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        before = {name: table(raw, name) for name in names}
        # Everything this revision and every later one rebuilds or reissues is read from the
        # migration modules, never listed here.
        rebuilt = _rebuilt_since(PREVIOUS)
        objects = {row for row in raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall() if row[1] not in rebuilt}

    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == (PREVIOUS, HEADS['company'])
        for name in names:
            assert table(db.raw, name) == before[name], name
        after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= after
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]


@pytest.mark.parametrize('fence', FENCES)
def test_a_locally_rewritten_settlement_fence_stops_the_migration(tmp_path, fence):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('DROP TRIGGER ' + fence)
        table_name = fence.rsplit('_', 3)[0] if fence.startswith('settlement') else None
        raw.execute('CREATE TRIGGER %s BEFORE INSERT ON %s BEGIN SELECT 1; END' % (
            fence, {'applications_paid_transaction_id_type': 'applications',
                    'payment_selection_items_invoice_id_type': 'payment_selection_items',
                    'settlement_line_keys_transaction_id_type': 'settlement_line_keys'}[fence]))
        raw.commit()
        assert table_name is None or table_name
    with open_database(path, writable=True) as db:
        with pytest.raises(Exception) as caught:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        assert 'co0043' in str(caught.value) or 'co0043' in str(caught.value.__cause__)
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)


def test_a_competing_local_settlement_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute("CREATE TRIGGER local_competing_settlement BEFORE INSERT ON applications"
                    " WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE"
                    " id = NEW.paid_transaction_id AND type = 'invoice')"
                    " BEGIN SELECT RAISE(ABORT, 'local settlement contract'); END")
        raw.commit()
    with open_database(path, writable=True) as db:
        with pytest.raises(Exception) as caught:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        assert 'co0043' in str(caught.value) or 'co0043' in str(caught.value.__cause__)
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
