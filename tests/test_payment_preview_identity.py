# Independent Gate A witness copied unchanged from the frozen review report.
"""Prospective identity is not a persisted payment identity."""
import sqlite3
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method
from tests.test_row8_journal import database_path


def test_uncommitted_payment_ids_are_null(client,sale):
    args=dict(customer=sale['customer'],date='2026-06-02',amount='1.00',payment_method=method(client),operation_key='critic-preview-identity')
    first=client.run('payment receive',args,company=COMPANY,dry_run=True)
    second=client.run('payment receive',args,company=COMPANY,dry_run=True)
    assert first['facts_fingerprint']==second['facts_fingerprint']
    ids={'id': first['id'], 'effect.payment_id':first['effect']['payment_id'], 'current.payment_id':first['current']['payment_id'], 'current.revision_id':first['current']['revision_id']}
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT count(*) FROM transactions WHERE id=?',(first['id'],)).fetchone()==(0,)
    assert all(value is None for value in ids.values()), f'Unpersisted generated IDs returned: {ids}; repeat id differs: {first["id"] != second["id"]}'
