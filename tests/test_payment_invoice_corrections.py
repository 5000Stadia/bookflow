"""Applied invoice edits use durable ordinals and preserve unchanged provenance."""
import sqlite3
import pytest
from bookflow import BookflowError

from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method, snapshots
from tests.test_row8_journal import database_path


def test_applied_invoice_add_reorder_and_permanent_correction_replay(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'RESTATE-INVOICE')
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='50.00',
        payment_method=method(client), operation_key='restate-receive', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='50.00')])), company=COMPANY)
    args = dict(invoice=invoice['id'], expected_version=2, operation_key='restate-add-line',
        settlement_versions=[dict(payment=payment['id'], expected_version=1)], lines=[
            dict(line_id=invoice['revision']['lines'][0]['line_id'], item=sale['item'], quantity='1', unit_price='100.00'),
            dict(item=sale['item'], quantity='1', unit_price='100.00')])
    before = snapshots(client)
    a = client.run('invoice update', args, reason='Add omitted work', company=COMPANY, dry_run=True)
    b = client.run('invoice update', args, reason='Add omitted work', company=COMPANY, dry_run=True)
    assert a['facts_fingerprint'] == b['facts_fingerprint'] and snapshots(client) == before
    corrected = client.run('invoice update', dict(args, expected_facts_fingerprint=a['facts_fingerprint']), reason='Add omitted work', company=COMPANY)
    assert corrected['version'] == 3 and corrected['revision']['revision_number'] == 2
    assert client.run('payment show', dict(payment=payment['id']), company=COMPANY)['version'] == 2
    with sqlite3.connect(database_path(client)) as db:
        rows = db.execute("SELECT target_ordinal,amount_minor_units FROM application_allocations a WHERE a.kind='allocation' AND a.target_transaction_id=? AND NOT EXISTS (SELECT 1 FROM application_allocations r WHERE r.reverses_allocation_id=a.id) ORDER BY target_ordinal", (invoice['id'],)).fetchall()
        assert rows == [(1, 2500), (2, 2500)]
        allocations_before = db.execute('SELECT * FROM application_allocations ORDER BY rowid').fetchall()
    reordered = client.run('invoice update', dict(invoice=invoice['id'], expected_version=3, operation_key='restate-reorder',
        settlement_versions=[dict(payment=payment['id'], expected_version=2)], lines=[
            dict(line_id=row['line_id'], item=sale['item'], quantity='1', unit_price='100.00') for row in reversed(corrected['revision']['lines'])]),
        reason='Reorder display only', company=COMPANY)
    assert reordered['version'] == 4
    assert client.run('payment show', dict(payment=payment['id']), company=COMPANY)['version'] == 2
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT * FROM application_allocations ORDER BY rowid').fetchall() == allocations_before
    before = snapshots(client)
    replay = client.run('invoice update', args, reason='Add omitted work', company=COMPANY)
    assert replay['idempotent_replay'] and replay['settlement']['current']['version'] == 4
    assert replay['settlement']['effect'] == corrected['settlement']['effect'] and snapshots(client) == before


def test_wrong_balanced_invoice_restatement_rejected_independently(client, sale, monkeypatch):
    from bookflow.company import payment_calculations
    invoice = posted(client, sale['customer'], sale['item'], '1.00', 'MUTATE-RESTATE')
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='0.01',
        payment_method=method(client), operation_key='mutate-restate-receive', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='0.01')])), company=COMPANY)
    def wrong(amount, capacities):
        return {max(capacities): amount}
    monkeypatch.setattr(payment_calculations, 'allocate', wrong)
    before = snapshots(client)
    with pytest.raises(BookflowError) as caught:
        client.run('invoice update', dict(invoice=invoice['id'], expected_version=2, operation_key='mutate-restate-edit',
            settlement_versions=[dict(payment=payment['id'], expected_version=1)], lines=[
                dict(line_id=invoice['revision']['lines'][0]['line_id'], item=sale['item'], quantity='1', unit_price='1.00'),
                dict(item=sale['item'], quantity='1', unit_price='1.00')]), reason='Add equal work', company=COMPANY)
    assert caught.value.code == 'E_INTERNAL' and snapshots(client) == before
