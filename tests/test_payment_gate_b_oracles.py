"""Independent accounting/history witnesses. No permission changes or guard changes."""
import sqlite3
from datetime import timedelta
import pytest
from bookflow import BookflowError
from bookflow.core import clock
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method
from tests.test_row8_journal import database_path


def run(c, verb, args, **kw):
    return c.run(verb,args,company=COMPANY,**kw)


def allrows(c):
    with sqlite3.connect(database_path(c)) as db:
        return {name:db.execute('SELECT * FROM "'+name+'" ORDER BY rowid').fetchall() for (name,) in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def receive(c,sale,invoice=None,amount='1.00',key='critic-cash',date='2026-06-02',payment_method=None):
    args=dict(customer=sale['customer'],date=date,amount=amount,operation_key=key,payment_method=payment_method or method(c))
    if invoice:
        args['applications']=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=invoice['version'],amount=amount)])
    return run(c,'payment receive',args)


def test_voided_invoice_as_of_is_voided_not_paid(client,sale):
    invoice=posted(client,sale['customer'],sale['item'],'1.00','CRITIC-VOID-ASOF')
    run(client,'invoice void',dict(invoice=invoice['id'],expected_version=1),reason='Cancel mistaken invoice')
    state=run(client,'invoice settlement',dict(invoice=invoice['id'],as_of='2026-06-30'))
    assert state['gross_minor_units']==state['due_minor_units']==0
    assert state['status']=='voided',state


def test_guard_changes_name_actual_commercial_field(client,sale):
    invoice=posted(client,sale['customer'],sale['item'],'1.00','CRITIC-MEMO-CHANGE')
    paid=receive(client,sale,invoice,'1.00')
    baseline=run(client,'invoice settlement',dict(invoice=invoice['id']))['settlement_guard']
    run(client,'invoice update',dict(invoice=invoice['id'],expected_version=2,memo='Corrected commercial memo',
        operation_key='critic-memo-edit',settlement_versions=[dict(payment=paid['id'],expected_version=1)]),reason='Correct invoice memo')
    changed=run(client,'payment settlement changes',dict(guard=baseline))
    own=[row for row in changed['items'] if row['record_id']==invoice['id']]
    assert len(own)==1 and not changed['unknown_history']
    assert 'memo' in own[0]['fields'],own


def test_invoice_correction_effect_recorded_and_current_after_void(client,sale,monkeypatch):
    invoice=posted(client,sale['customer'],sale['item'],'2.00','CRITIC-RECOVERY')
    paid=receive(client,sale,invoice,'1.00')
    args=dict(invoice=invoice['id'],expected_version=2,memo='Saved correction',operation_key='critic-permanent-edit',
        settlement_versions=[dict(payment=paid['id'],expected_version=1)])
    preview=run(client,'invoice update',args,reason='Correct invoice note',dry_run=True)
    committed=run(client,'invoice update',dict(args,expected_facts_fingerprint=preview['facts_fingerprint']),reason='Correct invoice note',idempotency_key='critic-transport')
    run(client,'payment unapply',dict(payment=paid['id'],expected_version=1,operation_key='critic-unapply',applications=[
        dict(application_id=paid['effect']['applications'][0]['application_id'],invoice_expected_version=3)]),reason='Cancel allocation')
    run(client,'invoice void',dict(invoice=invoice['id'],expected_version=4),reason='Cancel invoice')
    client.customer.update(customer=sale['customer'],expected_version=1,name='Critic renamed',company=COMPANY)
    then=clock.now()+timedelta(days=45)
    monkeypatch.setattr(clock,'now',lambda:then)
    before=allrows(client)
    original=run(client,'payment operation show',dict(operation_key='critic-permanent-edit'))['request']
    assert original['input']['expected_facts_fingerprint']==preview['facts_fingerprint']
    recovered=run(client,original['command'],original['input'],reason=original['context']['reason'])
    assert recovered['idempotent_replay'] and not recovered['changed']
    assert recovered['settlement']['effect']==committed['settlement']['effect']
    assert recovered['settlement']['current']['status']=='voided'
    assert recovered['settlement']['current']['due_minor_units']==0
    assert allrows(client)==before


def test_dated_invoice_correction_and_cash_replacement_have_exact_nets(client,sale):
    invoice=posted(client,sale['customer'],sale['item'],'2.00','CRITIC-DATES')
    bank=client.account.create(name='Critic Original Bank',type='bank',company=COMPANY)['id']
    bank2=client.account.create(name='Critic Corrected Bank',type='bank',company=COMPANY)['id']
    paid=run(client,'payment receive',dict(customer=sale['customer'],date='2026-06-04',amount='1.50',deposit_to=bank,
        payment_method=method(client),operation_key='critic-dated-cash'))
    applied=run(client,'payment apply',dict(payment=paid['id'],expected_version=1,date='2026-06-10',operation_key='critic-dated-apply',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='1.00')])))
    corrected=run(client,'payment update',dict(payment=paid['id'],expected_version=2,date='2026-06-08',amount='1.75',deposit_to=bank2,
        operation_key='critic-dated-correction',invoice_versions=[dict(invoice=invoice['id'],expected_version=2)]),reason='Correct remittance date bank and amount')
    for cutoff,received,applied_amount,available in [('2026-06-07',0,0,0),('2026-06-08',175,0,175),('2026-06-10',175,100,75)]:
        state=run(client,'payment settlement',dict(payment=paid['id'],as_of=cutoff))
        assert (state['received_minor_units'],state['applied_minor_units'],state['unapplied_minor_units'])==(received,applied_amount,available)
    ar=invoice['revision']['profile']['control_account']['id']
    with sqlite3.connect(database_path(client)) as db:
        net=dict(db.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY account_id',(paid['id'],)))
        assert net=={bank:0,bank2:175,ar:-175}
        assert db.execute('SELECT count(*) FROM applications WHERE paying_transaction_id=?',(paid['id'],)).fetchone()==(1,)
    before=allrows(client)
    args=dict(payment=paid['id'],expected_version=3,date='2026-06-11',operation_key='critic-invalid-late-date',invoice_versions=[dict(invoice=invoice['id'],expected_version=3)])
    with pytest.raises(BookflowError) as error:
        run(client,'payment update',args,reason='Attempt later date')
    assert error.value.code=='E_HAS_APPLICATIONS' and allrows(client)==before


def test_fixture_5401_restatement_final_residue_and_exact_ledger(client,sale):
    agency=client.vendor.create(name='Critic tax agency',is_tax_agency=True,company=COMPANY)['id']
    with sqlite3.connect(database_path(client)) as db:
        liability=db.execute("SELECT id FROM accounts WHERE system_role='sales_tax_payable'").fetchone()[0]
    codes=run(client,'sales-tax-code list',{})['items']
    taxable=next(r['id'] for r in codes if r['taxable']); exempt=next(r['id'] for r in codes if not r['taxable'])
    run(client,'company update',dict(sales_tax_enabled=True))
    tax=run(client,'item create',dict(name='Critic eight percent',type='sales_tax_item',tax_percent='8',tax_agency_vendor_id=agency,liability_account_id=liability))['id']
    invoice=run(client,'invoice post',dict(customer=sale['customer'],date='2026-06-01',sales_tax_item=tax,
        lines=[dict(item=sale['item'],quantity='2',net_amount='100.00',tax_code=taxable)]))
    bank=client.account.create(name='Critic Fixture Bank',type='bank',company=COMPANY)['id']
    pm=method(client)
    first=run(client,'payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='54.01',deposit_to=bank,payment_method=pm,
        operation_key='critic-fixture-first',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='54.01')])))
    args=dict(invoice=invoice['id'],expected_version=2,operation_key='critic-fixture-correct',settlement_versions=[dict(payment=first['id'],expected_version=1)],
        lines=[dict(item=sale['item'],line_id=invoice['revision']['lines'][0]['line_id']),dict(item=sale['item'],net_amount='20.00',tax_code=exempt)])
    preview=run(client,'invoice update',args,reason='Add missing exempt work',dry_run=True)
    corrected=run(client,'invoice update',dict(args,expected_facts_fingerprint=preview['facts_fingerprint']),reason='Add missing exempt work')
    app_id=first['effect']['applications'][0]['application_id']
    live=run(client,'application show',dict(application=app_id))['current_allocations']
    assert {(r['target_ordinal'],r['logical_kind']):r['amount_minor_units'] for r in live}=={(1,'net'):4219,(1,'tax'):338,(2,'net'):844}
    final=run(client,'payment receive',dict(customer=sale['customer'],date='2026-06-03',amount='73.99',deposit_to=bank,payment_method=pm,
        operation_key='critic-fixture-final',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=3,amount='73.99')])))
    assert {(r['target_ordinal'],r['logical_kind']):r['amount']['minor_units'] for r in final['effect']['allocations']}=={(1,'net'):5781,(1,'tax'):462,(2,'net'):1156}
    ar=invoice['revision']['profile']['control_account']['id']
    with sqlite3.connect(database_path(client)) as db:
        net=dict(db.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id IN (?,?,?) GROUP BY account_id',(invoice['id'],first['id'],final['id'])))
        assert net=={bank:12800,ar:0,sale['income']:-12000,liability:-800}
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert run(client,'invoice settlement',dict(invoice=invoice['id']))['due_minor_units']==0


def test_unchanged_method_preserves_captured_label(client,sale):
    pm=method(client)
    paid=receive(client,sale,payment_method=pm)
    before=run(client,'payment show',dict(payment=paid['id']))['revision']['profile']['payment_method']
    run(client,'payment-method update',dict(payment_method=pm,expected_version=1,name='Renamed method',kind='cash'))
    changed=run(client,'payment update',dict(payment=paid['id'],expected_version=1,operation_key='critic-method-same',payment_method=pm,memo='Memo only'),reason='Correct memo')
    after=run(client,'payment show',dict(payment=paid['id']))['revision']['profile']['payment_method']
    assert after==before,(before,after)


def test_invoice_recovery_normalizes_exact_line_money(client,sale):
    invoice=posted(client,sale['customer'],sale['item'],'1.00','CRITIC-CANONICAL')
    args=dict(invoice=invoice['id'],expected_version=1,operation_key='critic-canonical-edit',
        lines=[dict(line_id=invoice['revision']['lines'][0]['line_id'],item=sale['item'],quantity='1',net_amount='2.00')])
    saved=run(client,'invoice update',args,reason='Correct charge')
    args['lines'][0]['net_amount']={'minor_units':200,'currency':'USD'}
    replay=run(client,'invoice update',args,reason='Correct charge')
    assert replay['idempotent_replay'] and replay['settlement']['effect']==saved['settlement']['effect']


@pytest.mark.timeout(600)
def test_invoice_correction_preview_is_bounded_on_all_lists(client,sale):
    invoice=posted(client,sale['customer'],sale['item'],'10.00','CRITIC-103-BOUNDED')
    pm=method(client)
    for ordinal in range(103):
        current=dict(invoice,version=ordinal+1)
        receive(client,sale,current,'0.01',key=f'critic-bounded-{ordinal}',payment_method=pm)
    guard=run(client,'invoice settlement',dict(invoice=invoice['id']))['settlement_guard']
    args=dict(invoice=invoice['id'],expected_version=104,operation_key='critic-bounded-edit',settlement_guard=guard,
        lines=[dict(line_id=invoice['revision']['lines'][0]['line_id'],item=sale['item'],quantity='1',unit_price='10.00'),dict(item=sale['item'],quantity='1',unit_price='10.00')])
    preview=run(client,'invoice update',args,reason='Add omitted work',dry_run=True)
    lengths={k:len(v) for k,v in preview['settlement']['effect'].items() if isinstance(v,list)}
    assert max(lengths.values())<=50,repr(lengths)


@pytest.mark.timeout(600)
def test_one_receipt_corrected_across_complete_403_invoice_graph(client,sale):
    invoices=[posted(client,sale['customer'],sale['item'],'0.03',f'CRITIC-403-{n}') for n in range(403)]
    selection=run(client,'payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-02',amount='12.09'))
    for offset in range(0,403,137):
        selection=run(client,'payment selection update',dict(selection=selection['id'],expected_version=selection['version'],
            set_items=[dict(invoice=i['id'],expected_version=1,amount='0.03',amount_origin='entered') for i in invoices[offset:offset+137]]))
    bank=client.account.create(name='Critic 403 Old Bank',type='bank',company=COMPANY)['id']
    bank2=client.account.create(name='Critic 403 New Bank',type='bank',company=COMPANY)['id']
    paid=run(client,'payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='12.09',deposit_to=bank,payment_method=method(client),
        operation_key='critic-403-receipt',applications=dict(mode='selection',selection=selection['id'],expected_version=selection['version'])))
    guard=run(client,'payment show',dict(payment=paid['id']))['settlement_guard']
    args=dict(payment=paid['id'],expected_version=1,amount='12.10',deposit_to=bank2,operation_key='critic-403-correction',settlement_guard=guard)
    before=allrows(client)
    preview=run(client,'payment update',args,reason='Correct bank and omitted cent',dry_run=True)
    full={}
    for descriptor in preview['prospective_pages']:
        rows=list(preview['effect'][descriptor['kind']]); cursor=descriptor['next_cursor']
        while cursor:
            page=run(client,'payment preview items',dict(request=descriptor['request'],kind=descriptor['kind'],facts_fingerprint=preview['facts_fingerprint'],cursor=cursor))
            assert page['facts_fingerprint']==preview['facts_fingerprint'] and not page['committed']
            rows+=page['items']; cursor=page['next_cursor']
        assert len(rows)==descriptor['total_count']
        full[descriptor['kind']]=rows
    assert len(full['allocations'])==806 and len(full['document_changes'])==403
    assert sum(r['amount']['minor_units'] for r in full['allocations'] if r['kind']=='allocation')==1209
    assert sum(r['amount']['minor_units'] for r in full['allocations'] if r['kind']=='reversal')==1209
    assert allrows(client)==before
    result=run(client,'payment update',dict(args,expected_facts_fingerprint=preview['facts_fingerprint']),reason='Correct bank and omitted cent')
    assert result['current']['received_minor_units']==1210 and result['current']['applied_minor_units']==1209 and result['current']['available_minor_units']==1
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT count(*) FROM applications WHERE paying_transaction_id=?',(paid['id'],)).fetchone()==(403,)
        assert db.execute("SELECT count(*) FROM applications WHERE kind='unapply'").fetchone()==(0,)
        versions=db.execute("SELECT version,count(*) FROM transactions WHERE type='invoice' AND number LIKE 'CRITIC-403-%' GROUP BY version").fetchall()
        assert versions==[(3,403)],versions
        live=db.execute("SELECT application_id,amount_minor_units FROM application_allocations a WHERE source_transaction_id=? AND kind='allocation' AND NOT EXISTS (SELECT 1 FROM application_allocations r WHERE r.reverses_allocation_id=a.id)",(paid['id'],)).fetchall()
        assert len(live)==403 and len({r[0] for r in live})==403 and {r[1] for r in live}=={3}
        net=dict(db.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY account_id',(paid['id'],)))
        assert net=={bank:0,bank2:1210,invoices[0]['revision']['profile']['control_account']['id']:-1210},net
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_invoice_preparation_cursor_binds_selected_party_lineage(client,sale):
    job=client.customer.create(name='Critic child',parent_id=sale['customer'],company=COMPANY)['id']
    sibling=client.customer.create(name='Critic branch',parent_id=sale['customer'],company=COMPANY)['id']
    for n in range(2):
        posted(client,job,sale['item'],'1.00',f'CRITIC-LINEAGE-{n}')
    args=dict(mode='new_receipt',customer=sale['customer'],date='2026-06-02',limit=1)
    page=run(client,'payment invoices',args)
    assert page['next_cursor'] and page['total_count']==2
    client.customer.update(customer=job,expected_version=1,parent_id=sibling,company=COMPANY)
    before=allrows(client)
    with pytest.raises(BookflowError) as error:
        run(client,'payment invoices',dict(args,cursor=page['next_cursor']))
    assert error.value.code=='E_QUERY_STALE'
    assert allrows(client)==before


def test_same_inactive_method_does_not_block_unrelated_correction(client,sale):
    pm=method(client)
    paid=receive(client,sale,payment_method=pm)
    run(client,'payment-method deactivate',dict(payment_method=pm,expected_version=1))
    corrected=run(client,'payment update',dict(payment=paid['id'],expected_version=1,operation_key='critic-inactive-same',payment_method=pm,memo='Correct note'),reason='Correct receipt note')
    assert corrected['version']==2


def test_unapply_two_occurrences_one_invoice_versions_dates_and_key_order(client,sale):
    invoice=posted(client,sale['customer'],sale['item'],'2.00','CRITIC-TWO-APPLICATIONS')
    paid=receive(client,sale,invoice,'1.00')
    run(client,'payment update',dict(payment=paid['id'],expected_version=1,operation_key='critic-increase-two',amount='2.00',invoice_versions=[dict(invoice=invoice['id'],expected_version=2)]),reason='Correct total received')
    second=run(client,'payment apply',dict(payment=paid['id'],expected_version=2,date='2026-06-03',operation_key='critic-second-application',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=3,amount='1.00')])))
    first_id=paid['effect']['applications'][0]['application_id']; second_id=second['effect']['applications'][0]['application_id']
    base=dict(payment=paid['id'],expected_version=3,operation_key='critic-two-unapply')
    before=allrows(client)
    for items in ([dict(application_id=first_id,invoice_expected_version=4)]*2,
                  [dict(application_id=first_id,invoice_expected_version=3),dict(application_id=second_id,invoice_expected_version=4)]):
        with pytest.raises(BookflowError):
            run(client,'payment unapply',dict(base,applications=items),reason='Remove duplicate selection')
        assert allrows(client)==before
    refs=[dict(application_id=second_id,invoice_expected_version=4),dict(application_id=first_id,invoice_expected_version=4)]
    result=run(client,'payment unapply',dict(base,applications=refs),reason='Remove allocations')
    assert result['version']==4
    state=run(client,'invoice settlement',dict(invoice=invoice['id']))
    assert state['version']==5 and state['applied_minor_units']==0 and state['due_minor_units']==200
    with sqlite3.connect(database_path(client)) as db:
        dates=dict(db.execute("SELECT reverses_application_id,effective_date FROM applications WHERE kind='unapply'"))
        assert dates=={first_id:'2026-06-02',second_id:'2026-06-03'}
    stable=allrows(client)
    with pytest.raises(BookflowError) as error:
        run(client,'payment unapply',dict(base,applications=list(reversed(refs))),reason='Remove allocations')
    assert error.value.code=='E_PAYMENT_OPERATION_KEY_REUSED' and allrows(client)==stable
    replay=run(client,'payment unapply',dict(base,applications=refs),reason='Remove allocations')
    assert replay['idempotent_replay'] and allrows(client)==stable


@pytest.mark.parametrize('verb',['invoice update','payment update'])
@pytest.mark.parametrize('table',['application_allocations','payment_operations','payment_operation_items'])
def test_correction_fault_rolls_back_all_company_rows_and_key_retry(client,sale,verb,table):
    invoice=posted(client,sale['customer'],sale['item'],'1.00','CRITIC-CORRECTION-FAULT')
    paid=receive(client,sale,invoice,'0.01')
    if verb=='invoice update':
        args=dict(invoice=invoice['id'],expected_version=2,operation_key='critic-fault-edit',settlement_versions=[dict(payment=paid['id'],expected_version=1)],
            lines=[dict(line_id=invoice['revision']['lines'][0]['line_id'],item=sale['item'],quantity='1',unit_price='1.00'),dict(item=sale['item'],quantity='1',unit_price='2.00')])
    else:
        args=dict(payment=paid['id'],expected_version=1,operation_key='critic-fault-edit',memo='Correct remittance note',invoice_versions=[dict(invoice=invoice['id'],expected_version=2)])
    before=allrows(client)
    with sqlite3.connect(database_path(client)) as db:
        db.execute(f"CREATE TRIGGER critic_fault BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'critic injected persistence interruption'); END")
    with pytest.raises(BookflowError):
        run(client,verb,args,reason='Correct omitted work')
    assert allrows(client)==before
    with sqlite3.connect(database_path(client)) as db:
        db.execute('DROP TRIGGER critic_fault')
    committed=run(client,verb,args,reason='Correct omitted work')
    assert committed['changed']
    stable=allrows(client)
    recovered=run(client,verb,args,reason='Correct omitted work')
    assert recovered['idempotent_replay'] and allrows(client)==stable
