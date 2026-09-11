"""co0038 is exactly today's metadata, and it adds storage without touching what is there.

The DDL inside a migration is frozen text: it never imports the application's metadata, so the
only thing keeping the two in step is this file compiling the metadata and comparing. The rest
is the preservation question -- a company already at the previous revision carries rows,
indexes and triggers, and a purely additive migration has to leave every one of them alone.
"""
import importlib

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.company.memorized_schema import guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head

M = importlib.import_module('bookflow.storage.company_migrations.versions.0038_memorized_transactions')


def _at(path, revision):
    """A company database stopped part-way along the chain, the way the runner builds one."""
    from alembic import command
    from bookflow.storage.migrate import _config
    with open_database(path, writable=True, create=True) as db:
        db.raw.execute('PRAGMA foreign_keys=OFF')
        try:
            command.upgrade(_config('company', db.conn), revision)
            db.raw.commit()
        finally:
            db.raw.execute('PRAGMA foreign_keys=ON')


def _schema(db):
    return {row[1]: (row[0], row[2] or '') for row in db.raw.execute(
        'SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE "sqlite_%"')}


def test_the_frozen_ddl_is_the_current_metadata():
    indexes = sorted([index for name in M.NEW_TABLES for index in c.metadata.tables[name].indexes],
                     key=lambda index: index.name)
    compiled = tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=dialect())).strip()
                     for name in M.NEW_TABLES)
    compiled += tuple(str(CreateIndex(index).compile(dialect=dialect())).strip() for index in indexes)
    assert M.DDL == compiled
    assert M.GUARDS == tuple(guard_statements())
    assert set(M.NEW_TABLES) == {name for name in c.metadata.tables if name.startswith('memorized')}
    # Every composite foreign key names a real key of its target, not an arbitrary column pair.
    for name in M.NEW_TABLES:
        for key in c.metadata.tables[name].foreign_key_constraints:
            target = key.referred_table
            columns = tuple(element.column.name for element in key.elements)
            allowed = [tuple(target.primary_key.columns.keys())] + [
                tuple(constraint.columns.keys()) for constraint in target.constraints
                if constraint.__class__.__name__ == 'UniqueConstraint']
            assert columns in allowed, (name, columns)


def test_the_revision_is_the_one_reserved_and_it_follows_the_chain_head():
    """Derived from the chain itself: the predecessor is whatever nothing else supersedes."""
    revisions = known_revisions('company')
    assert M.revision == 'co0038' and M.revision in revisions
    others = {module.down_revision for name, module in _versions().items() if name != M.revision}
    # Nothing but this revision claims its predecessor, and with it claimed there is one head.
    assert M.down_revision not in others, M.down_revision
    heads = sorted(set(revisions) - others - {M.down_revision})
    assert heads == [M.revision], heads
    assert HEADS['company'] == M.revision


def _versions():
    import pkgutil
    from bookflow.storage import company_migrations
    package = company_migrations.__name__ + '.versions'
    found = {}
    for module in pkgutil.iter_modules([company_migrations.__path__[0] + '/versions']):
        loaded = importlib.import_module(package + '.' + module.name)
        found[loaded.revision] = loaded
    return found


def test_a_fresh_company_reaches_the_head_with_empty_memorized_storage(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        for name in M.NEW_TABLES:
            assert db.raw.execute(f'SELECT count(*) FROM {name}').fetchone()[0] == 0
        names = {row[0] for row in db.raw.execute("SELECT name FROM sqlite_schema WHERE type='trigger'")}
        assert {statement.split()[2] for statement in M.GUARDS} <= names


def test_the_migration_adds_storage_and_rewrites_nothing_that_was_there(tmp_path):
    """The preservation question: every object the previous revision left must survive byte for byte."""
    path = tmp_path / 'existing.db'
    _at(path, M.down_revision)
    with open_database(path, writable=True) as db:
        before = _schema(db)
        db.raw.execute("INSERT INTO sequences (name, next_number, prefix) VALUES ('probe', 7, 'P')")
        db.raw.commit()
    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', None)[1] == HEADS['company']
        after = _schema(db)
        assert {name: value for name, value in after.items() if name in before} == before
        assert set(after) - set(before) == set(M.OBJECTS)
        assert db.raw.execute("SELECT next_number, prefix FROM sequences WHERE name='probe'").fetchone() == (7, 'P')


def test_the_slot_index_refuses_a_second_scheduled_occurrence_for_one_slot(tmp_path):
    """Identity is a storage fact, not a convention the writer is trusted to keep."""
    path = tmp_path / 'slots.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        index = db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='ux_memorized_occurrence_slot'").fetchone()[0]
        assert 'UNIQUE' in index and "WHERE origin = 'scheduled'" in index
