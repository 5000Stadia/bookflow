"""hub0016 mirrors each company's external-accountant designations in the hub (R121).

Its frozen DDL is what the metadata declares; upgrading a hub from hub0015 reads each company's books and
backfills the designations, marking a company whose books cannot be read unverified rather than "no accountant".
"""
import importlib
import sqlite3

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.core.ids import new_id
from bookflow.hub import schema as h
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.conftest import make_actor
from tests.test_service_sales_lifecycle import COMPANY

M = importlib.import_module('bookflow.storage.hub_migrations.versions.0016_company_accountants')


def test_the_frozen_ddl_is_what_the_metadata_declares():
    assert M.revision == 'hub0016' and M.down_revision == 'hub0015' and HEADS['hub'] >= 'hub0016'
    assert M.DDL == tuple(str(CreateTable(t).compile(dialect=sqlite.dialect())) for t in (h.company_accountants, h.company_accountant_sync))
    assert M.INDEXES == tuple(str(CreateIndex(i).compile(dialect=sqlite.dialect())) for i in h.company_accountants.indexes)


def test_upgrading_backfills_designations_and_marks_unreadable_books_unverified(root, client, tmp_path):
    company = client.run('company show', {}, company=COMPANY)['company_id']
    cpa = make_actor(root, 'outside-cpa', company_role=(company, 'standard'))
    client.run('accountant add', {'user': 'outside-cpa', 'through': '2026-09-30'}, company=COMPANY, reason='Q3')
    lost = new_id()
    with sqlite3.connect(root / 'hub.db') as hub:
        columns = [r[1] for r in hub.execute('PRAGMA table_info(companies)')]
        row = dict(zip(columns, hub.execute('SELECT * FROM companies WHERE id=?', (company,)).fetchone()))
        row.update(id=lost, display_name='Lost Co', name_key='lost co', path='nowhere/lost-co')
        hub.execute(f"INSERT INTO companies ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
        # Back to hub0015, as a hub upgraded from before R121 would be.
        hub.execute('DROP TABLE company_accountants')
        hub.execute('DROP TABLE company_accountant_sync')
        hub.execute('DROP TABLE user_homes')  # hub0017 (R191) comes after it
        hub.execute("UPDATE alembic_version SET version_num='hub0015'")
    with open_database(root / 'hub.db', writable=True) as db:
        assert migrate_to_head(db, 'hub', tmp_path) == ('hub0015', HEADS['hub'])
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
    with sqlite3.connect(root / 'hub.db') as hub:
        assert hub.execute('SELECT company_id, user_id, through_date FROM company_accountants').fetchall() == [(company, cpa, '2026-09-30')]
        states = dict(hub.execute('SELECT company_id, state FROM company_accountant_sync').fetchall())
    assert states[company] == 'verified' and states[lost] == 'unverified'
