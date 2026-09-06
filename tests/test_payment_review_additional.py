# Independent additional Gate A witnesses copied unchanged from the frozen review report.
"""Public accounting-only oracles on supported stored amount/progress facts."""
import sqlite3
import pytest
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method, posted, snapshots
from tests.test_row8_journal import database_path
from tests.test_work_billing_lifecycle import accepted, bill


def run(client, command, data, **kw):
    return client.run(command, data, company=COMPANY, **kw)


def test_stored_amount_and_fractional_progress_settle_exactly(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item'], quantity='0.000001', net_amount='1.00')])
    invoice = bill(client, source, selections=[dict(line_id=source['revision']['lines'][0]['line_id'], net_amount='0.40')])
    assert invoice['revision']['lines'][0]['quantity_microunits'] is None
    assert invoice['revision']['lines'][0]['net_minor_units'] == 40
    amount_invoice = run(client, 'invoice post', dict(customer=sale['customer'], date='2026-06-01',
        lines=[dict(item=sale['item'], quantity='2', net_amount='10.01')]))
    payment_method = method(client)
    paid = run(client, 'payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='10.41',
        payment_method=payment_method, operation_key='critic-stored-amounts', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='0.40'),
            dict(invoice=amount_invoice['id'], expected_version=1, amount='10.01')])))
    assert {r['invoice_id']: r['amount']['minor_units'] for r in paid['effect']['allocations']} == {invoice['id']:40, amount_invoice['id']:1001}
    for target in (invoice, amount_invoice):
        assert run(client,'invoice settlement',dict(invoice=target['id']))['due_minor_units'] == 0
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT sum(debit_minor_units),sum(credit_minor_units) FROM posting_lines WHERE transaction_id=?',(paid['id'],)).fetchone() == (1041,1041)
        assert db.execute('SELECT count(*) FROM posting_lines WHERE transaction_id=? AND account_id=?',(paid['id'],sale['income'])).fetchone() == (0,)


def test_closed_old_invoice_accepts_new_open_receipt_and_apply(client, sale):
    invoice = posted(client,sale['customer'],sale['item'],'2.00','CRITIC-CLOSED')
    payment_method = method(client)
    info = run(client,'company show',{})
    run(client,'company update',dict(expected_version=info['info_version'],closing_date='2026-06-01'))
    data=dict(customer=sale['customer'],date='2026-06-02',amount='2.00',payment_method=payment_method,operation_key='critic-open-cash')
    paid=run(client,'payment receive',data)
    before=snapshots(client)
    with pytest.raises(BookflowError) as error:
        run(client,'payment receive',dict(data,date='2026-06-01',operation_key='critic-closed-cash'))
    assert error.value.code == 'E_PERIOD_CLOSED' and snapshots(client)==before
    applied=run(client,'payment apply',dict(payment=paid['id'],expected_version=1,date='2026-06-02',operation_key='critic-open-apply',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='2.00')])))
    assert applied['current']['available_minor_units']==0
    for table in ('posting_lines','posting_batches','posting_line_sources','transaction_revisions'):
        assert snapshots(client)[table]==before[table]
    retry_before=snapshots(client)
    recovered=run(client,'payment apply',dict(payment=paid['id'],expected_version=1,date='2026-06-02',operation_key='critic-open-apply',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='2.00')])))
    assert recovered['idempotent_replay'] and recovered['effect']==applied['effect']
    assert snapshots(client)==retry_before


@pytest.mark.timeout(300)
def test_403_nonuniform_jobs_one_receipt_all_four_prospective_collections(client,sale):
    expected,targets={},[]
    for i in range(403):
        job=client.customer.create(name=f'Critic 403 job {i:03}',parent_id=sale['customer'],company=COMPANY)['id']
        cents=1+i%7
        invoice=posted(client,job,sale['item'],f'0.0{cents}',f'CRITIC-403-{i:03}')
        expected[job]=cents
        targets.append(dict(invoice=invoice['id'],expected_version=1,amount=f'0.0{cents}'))
    assert sum(expected.values())==1606
    bank=client.account.create(name='Critic 403 Bank',type='bank',company=COMPANY)['id']
    draft=run(client,'payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-02',amount='16.06'))
    for start in range(0,403,137):
        draft=run(client,'payment selection update',dict(selection=draft['id'],expected_version=draft['version'],set_items=targets[start:start+137]))
    data=dict(customer=sale['customer'],date='2026-06-02',amount='16.06',deposit_to=bank,payment_method=method(client),
        operation_key='critic-403-cash',applications=dict(mode='selection',selection=draft['id'],expected_version=draft['version']))
    before=snapshots(client)
    preview=run(client,'payment receive',data,dry_run=True)
    page_evidence={}
    for d in preview['prospective_pages']:
        items=list(preview['effect'][d['kind']])
        cursor=d['next_cursor']
        while cursor:
            page=run(client,'payment preview items',dict(request=d['request'],facts_fingerprint=d['facts_fingerprint'],kind=d['kind'],cursor=cursor))
            assert page['total_count']==403
            items.extend(page['items'])
            cursor=page['next_cursor']
        assert len(items)==403
        if d['kind']=='source_components':
            assert {r['party_id']:r['received_minor_units'] for r in items}==expected
        elif d['kind'] in ('applications','allocations'):
            assert sum(r['amount']['minor_units'] for r in items)==1606
        else:
            assert {r['invoice_id'] for r in items}=={r['invoice'] for r in targets}
            assert all(r['due_minor_units']==0 for r in items)
        page_evidence[d['kind']]=len(items)
    assert snapshots(client)==before
    paid=run(client,'payment receive',dict(data,expected_facts_fingerprint=preview['facts_fingerprint']))
    with sqlite3.connect(database_path(client)) as db:
        legs=db.execute('SELECT account_id,name_id,debit_minor_units,credit_minor_units FROM posting_lines WHERE transaction_id=?',(paid['id'],)).fetchall()
        ar=db.execute("SELECT id FROM accounts WHERE system_role='accounts_receivable'").fetchone()[0]
        assert len(legs)==404
        assert [(account,debit) for account,party,debit,credit in legs if debit]==[(bank,1606)]
        assert {(account,party):credit for account,party,debit,credit in legs if credit}=={(ar,party):cents for party,cents in expected.items()}
        assert db.execute('SELECT count(*),sum(amount_minor_units) FROM applications WHERE paying_transaction_id=?',(paid['id'],)).fetchone()==(403,1606)
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    print('403-job independent oracle:', page_evidence, 'one bank debit 1606; 403 exact-party AR credits; 404 legs')
