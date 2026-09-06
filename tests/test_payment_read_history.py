"""Public multi-participant/no-effect history and pure revision parity."""
import sqlite3
import sqlalchemy as sa
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method, snapshots
from tests.test_row8_journal import database_path


def history(client, payment):
    items, cursor = [], None
    while True:
        page = client.run('payment history', dict(payment=payment, limit=2,
            **({'cursor':cursor} if cursor else {})), company=COMPANY)
        items.extend(page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            assert len(items) == page['total_count']
            assert len({(r['kind'],r['id']) for r in items}) == len(items)
            assert items == sorted(items,key=lambda r:(r['audit_sequence'],r['id']))
            return items


def test_secondary_payments_no_effect_and_historical_renderer(client, sale):
    invoice = posted(client,sale['customer'],sale['item'],'100','INDEX-HISTORY')
    payment_method = method(client)
    payments=[]
    for number in range(2):
        payments.append(client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',
            amount='10',payment_method=payment_method,operation_key=f'index-history-receive-{number}',
            applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=number+1,amount='10')])),company=COMPANY))
    originals={p['id']:client.run('payment show',dict(payment=p['id']),company=COMPANY)['revision'] for p in payments}
    args=dict(invoice=invoice['id'],expected_version=3,operation_key='index-history-secondary',
        settlement_versions=[dict(payment=p['id'],expected_version=1) for p in payments],
        lines=[dict(line_id=invoice['revision']['lines'][0]['line_id'],item=sale['item'],quantity='1',unit_price='120')])
    corrected=client.run('invoice update',args,reason='Correct invoice charge',company=COMPANY)
    before=snapshots(client)
    assert client.run('invoice update',args,reason='Correct invoice charge',company=COMPANY)['idempotent_replay']
    assert snapshots(client)==before
    for p in payments:
        current=client.run('payment show',dict(payment=p['id']),company=COMPANY)
        noop_args=dict(payment=p['id'],expected_version=current['version'],operation_key='index-history-noop-'+p['id'])
        noop=client.run('payment update',noop_args,reason='Confirm receipt',company=COMPANY)
        assert not noop['changed']
        rows=history(client,p['id'])
        keys={r['operation_key'] for r in rows if r['kind']=='operation'}
        assert {'index-history-secondary',noop_args['operation_key']} <= keys
        assert [r['revision'] for r in rows if r['kind']=='receipt_revision']==[originals[p['id']]]
        before=snapshots(client)
        assert client.run('payment update',noop_args,reason='Confirm receipt',company=COMPANY)['idempotent_replay']
        assert snapshots(client)==before and history(client,p['id'])==rows
        edited=client.run('payment update',dict(payment=p['id'],expected_version=current['version'],
            memo='Corrected memo',operation_key='index-history-edit-'+p['id']),reason='Correct memo',company=COMPANY)
        rows=history(client,p['id'])
        for row in (r for r in rows if r['kind']=='receipt_revision'):
            assert row['revision']==client.run('payment show',dict(payment=p['id'],revision=row['revision']['revision_number']),company=COMPANY)['revision']
        app=p['effect']['applications'][0]['application_id']
        inv=client.run('invoice show',dict(invoice=invoice['id']),company=COMPANY)
        unapplied=client.run('payment unapply',dict(payment=p['id'],expected_version=edited['version'],
            operation_key='index-history-unapply-'+p['id'],applications=[dict(application_id=app,invoice_expected_version=inv['version'])]),reason='Remove application',company=COMPANY)
        client.run('payment void',dict(payment=p['id'],expected_version=unapplied['version'],
            operation_key='index-history-void-'+p['id']),reason='Void receipt',company=COMPANY)
        assert 'index-history-secondary' in {r.get('operation_key') for r in history(client,p['id'])}
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute("SELECT name FROM sqlite_schema WHERE name LIKE 'sqlite_stat%'").fetchall()==[]


def test_history_batches_sequences_and_never_renders_current_per_revision(client,sale,monkeypatch):
    from bookflow.company import payments as service
    p=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='10',
        payment_method=method(client),operation_key='index-renderer'),company=COMPANY)
    for i in range(3):
        p=client.run('payment update',dict(payment=p['id'],expected_version=p['version'],memo=f'Memo {i}',
            operation_key=f'index-renderer-{i}'),reason='Correct memo',company=COMPANY)
    def discarded(*args,**kwargs):
        raise AssertionError('history constructed discarded current settlement')
    monkeypatch.setattr(service,'current_output',discarded)
    statements=[]
    def capture(conn,cursor,statement,params,context,many):statements.append(statement)
    sa.event.listen(sa.engine.Engine,'before_cursor_execute',capture)
    try:
        rows=client.run('payment history',dict(payment=p['id'],limit=200),company=COMPANY)['items']
    finally:
        sa.event.remove(sa.engine.Engine,'before_cursor_execute',capture)
    assert len([r for r in rows if r['kind']=='receipt_revision'])==4
    sequence_queries=[sql for sql in statements if 'audit_events.id, audit_events.seq' in sql]
    assert len(sequence_queries)==1
