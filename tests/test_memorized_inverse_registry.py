"""Memorized writes resolve the owning document, never its implementation journal."""
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bookflow.company import reversal_registry as inverses


@pytest.fixture
def session():
    engine = sa.create_engine('sqlite://')
    with engine.connect() as conn:
        for table, columns in (
            ('transactions', 'id TEXT PRIMARY KEY, type TEXT'),
            ('item_receipts', 'id TEXT PRIMARY KEY, transaction_id TEXT'),
            ('shipments', 'id TEXT PRIMARY KEY, transaction_id TEXT, number TEXT'),
            ('money_out_documents', 'transaction_id TEXT PRIMARY KEY, kind TEXT'),
            ('inventory_documents', 'transaction_id TEXT PRIMARY KEY, kind TEXT'),
            ('shipment_revisions', 'shipment_id TEXT, revision_number INTEGER, status TEXT, '
                                   'created_by TEXT, created_at TEXT'),
        ):
            conn.exec_driver_sql(f'CREATE TABLE {table} ({columns})')
        yield SimpleNamespace(company=SimpleNamespace(conn=conn))
    engine.dispose()


@pytest.mark.parametrize('kind', ['check', 'card_charge', 'card_credit', 'transfer'])
def test_money_out_journal_uses_its_own_inverse(session, kind):
    conn = session.company.conn
    conn.exec_driver_sql("INSERT INTO transactions VALUES ('journal', 'journal_entry')")
    conn.exec_driver_sql('INSERT INTO money_out_documents VALUES (?, ?)', ('journal', kind))
    assert inverses.transaction_record(session, 'journal') == (kind, 'journal')
    assert inverses.DOCUMENTS[kind].inverse != 'journal delete'


@pytest.mark.parametrize('family, table', [('item_receipt', 'item_receipts'), ('shipment', 'shipments')])
def test_stock_owner_replaces_underlying_journal(session, family, table):
    conn = session.company.conn
    conn.exec_driver_sql("INSERT INTO transactions VALUES ('journal', 'journal_entry')")
    conn.exec_driver_sql(f"INSERT INTO {table} (id, transaction_id) VALUES ('owner', 'journal')")
    assert inverses.transaction_record(session, 'journal') == (family, 'owner')


def test_derived_stock_journal_is_not_an_independent_document(session):
    conn = session.company.conn
    conn.exec_driver_sql("INSERT INTO transactions VALUES ('derived', 'journal_entry')")
    conn.exec_driver_sql("INSERT INTO inventory_documents VALUES ('derived', 'recost')")
    assert inverses.transaction_record(session, 'derived') is None
    conn.exec_driver_sql("INSERT INTO transactions VALUES ('ordinary', 'journal_entry')")
    assert inverses.transaction_record(session, 'ordinary') == ('journal_entry', 'ordinary')


def test_shipment_current_state_comes_from_newest_revision(session):
    conn = session.company.conn
    conn.exec_driver_sql("INSERT INTO shipments VALUES ('owner', 'journal', 'SHIP-1')")
    conn.exec_driver_sql("INSERT INTO shipment_revisions VALUES ('owner', 1, 'shipped', 'first', '2026-01-01')")
    conn.exec_driver_sql("INSERT INTO shipment_revisions VALUES ('owner', 2, 'voided', 'second', '2026-01-02')")
    current = inverses.record_current(session, inverses.DOCUMENTS['shipment'], ['owner'])
    assert current == {'owner': dict(id='owner', number='SHIP-1', version=2, status='voided',
                                    updated_by='second', updated_at='2026-01-02', active=True)}
    assert inverses.record_current(session, inverses.DOCUMENTS['shipment'], ['missing']) == {}
