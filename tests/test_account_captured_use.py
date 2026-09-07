"""Ordinary captured-use admissions and immutable-path contract witnesses."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import accounts
from bookflow.storage.engine import open_database
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import database_path
from tests.test_row5_undo import _event


def run(client, name, data=None, **ctx):
    from bookflow.core import registry
    if registry.get(name).is_write:ctx.setdefault('reason','Captured use witness')
    return client.run(name, data or {}, company=COMPANY, **ctx)


def tax_code(client):
    return next(r['id'] for r in run(client,'sales-tax-code list')['items'] if not r['taxable'])


def zero_sale(client, sale, noun='invoice'):
    target = run(client, 'account create', dict(name='Captured zero income', type='income'))['id']
    item = run(client, 'item create', dict(name='Captured zero item', type='service', sales_enabled=True,
        description='Zero retained economics', income_account_id=target, price='0.00', sales_tax_code_id=tax_code(client)))['id']
    extra = {}
    if noun == 'sales-receipt':
        extra = dict(deposit_to=run(client,'account create',dict(name='Captured cash',type='bank'))['id'],
            payment_method=run(client,'payment-method create',dict(name='Captured method',kind='cash'))['id'])
    doc = run(client,noun+' post',dict(date='2026-01-12',customer=sale['customer'],
        lines=[dict(item=sale['item']),dict(item=item)],**extra))
    run(client,'item update',dict(item=item,income_account_id=sale['income']))
    return target,item,doc


def snapshot(client):
    with open_database(database_path(client),writable=False) as db:
        return {name: [tuple(r) for r in db.raw.execute('SELECT * FROM "'+name+'"')]
                for name, in db.raw.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")}


def denied(client, target, field, value):
    before=snapshot(client)
    with pytest.raises(BookflowError) as exc:
        run(client,'account update',dict(account=target,**{field:value}))
    expected={'record_id':target,'reason':'captured_posting_use'}
    if field=='type':expected.update({'from':'income','to':value})
    else:expected['field']='currency'
    assert exc.value.code==('E_TYPE_CHANGE' if field=='type' else 'E_RECORD_IN_USE')
    assert exc.value.details==expected
    assert snapshot(client)==before


@pytest.mark.parametrize('noun,new_type',[('invoice','bank'),('sales-receipt','credit_card')])
def test_real_zero_sale_retype_currency_and_retired_history(client,sale,noun,new_type):
    target,item,doc=zero_sale(client,sale,noun)
    denied(client,target,'type',new_type)
    denied(client,target,'currency','EUR')
    assert run(client,'account update',dict(account=target,type='income',currency='USD'))['version']==1
    run(client,'account update',dict(account=target,description='Same-type update'))
    # Positive-price account remains under the original, truthful posting guard.
    with pytest.raises(BookflowError) as exc:run(client,'account update',dict(account=sale['income'],type=new_type))
    assert exc.value.code=='E_TYPE_CHANGE' and exc.value.details['has_transactions'] is True
    with pytest.raises(BookflowError) as exc:run(client,'account update',dict(account=sale['income'],currency='EUR'))
    assert exc.value.code=='E_RECORD_IN_USE' and exc.value.details['dependents'][0]['count']>0
    key=noun.replace('-','_')
    positive=doc['revision']['lines'][0]
    changed=run(client,noun+' update',{key:doc['id'],'expected_version':1,'lines':[dict(item=sale['item'],line_id=positive['line_id'])]})
    run(client,noun+' void',{key:doc['id'],'expected_version':changed['version']},reason='Retained history witness')
    run(client,'item deactivate',dict(item=item))
    denied(client,target,'type',new_type)
    denied(client,target,'currency','EUR')


@pytest.mark.parametrize('noun',['proposal','estimate','work-order'])
def test_work_zero_nonbillable_inactive_copy_is_captured_use(client,sale,noun):
    doc=run(client,noun+' create',dict(date='2026-01-12',title='Saved zero work',customer=sale['customer'],
        lines=[dict(item=sale['item'],unit_price='0',billable=False)]))
    replacement=run(client,'account create',dict(name='New work income',type='income'))['id']
    run(client,'item update',dict(item=sale['item'],income_account_id=replacement))
    key=noun.replace('-','_')
    doc=run(client,noun+' update',{key:doc['id'],'expected_version':doc['version'],'active':False,'status':'cancelled'})
    copied=run(client,noun+' copy',{key:doc['id'],'expected_version':doc['version'],'date':'2026-01-13'})
    assert copied['revision']['lines'][0]['facts']['profile']['income_account']['id']==sale['income']
    denied(client,sale['income'],'type','bank')
    denied(client,sale['income'],'currency','EUR')


@pytest.mark.parametrize('field,initial,after',[('type','other_income','income'),('currency','EUR','USD')])
def test_real_undo_domain_target_rejects_intervening_capture(client,sale,field,initial,after):
    target=run(client,'account create',dict(name='Undo captured account',type='other_income',currency='EUR'))['id']
    run(client,'account update',dict(account=target,**{field:after}))
    event=_event(client,COMPANY,'account update',target)
    # For type witness make currency domestic before creating its work capture.
    if field=='type':run(client,'account update',dict(account=target,currency='USD'))
    item=run(client,'item create',dict(name='Undo capture item',type='service',sales_enabled=True,
        description='Saved work',income_account_id=target,price='0',sales_tax_code_id=tax_code(client)))['id']
    run(client,'estimate create',dict(date='2026-01-12',title='Intervening capture',customer=sale['customer'],lines=[dict(item=item)]))
    run(client,'item update',dict(item=item,income_account_id=sale['income']))
    before=snapshot(client)
    with pytest.raises(BookflowError) as exc:run(client,'undo',dict(event_id=event),reason='Undo original account edit')
    assert exc.value.code=='E_UNDO_CONFLICT'
    assert 'captured_posting_use' in json.dumps(exc.value.details)
    assert snapshot(client)==before


def test_unused_soft_text_preview_and_allowed_undo(client,sale):
    target=run(client,'account create',dict(name='Truly unused',type='income'))['id']
    doc=run(client,'estimate create',dict(date='2026-01-12',title='Text is not typed use',memo=target,
        customer=sale['customer'],lines=[dict(item=sale['item'],unit_price='0')]))
    run(client,'account update',dict(account=target,type='other_income',currency='EUR'))
    event=_event(client,COMPANY,'account update',target)
    run(client,'undo',dict(event_id=event),reason='Allowed uncaptured undo')
    assert run(client,'account show',dict(account=target))['type']=='income'
    run(client,'account update',dict(account=target,type='bank'),dry_run=True)
    item=run(client,'item create',dict(name='Preview intervening',type='service',sales_enabled=True,
        description='Zero',income_account_id=target,price='0',sales_tax_code_id=tax_code(client)))['id']
    run(client,'estimate create',dict(date='2026-01-12',title='After preview',customer=sale['customer'],lines=[dict(item=item)]))
    run(client,'item update',dict(item=item,income_account_id=sale['income']))
    denied(client,target,'type','bank')


@pytest.mark.parametrize('revision',['co0008','co0009','co0010','co0013','co0014','co0019','co0020'])
def test_known_old_schema_and_conn_only_undo_view(tmp_path,revision):
    from alembic import command
    from bookflow.storage.migrate import _config
    from bookflow.company.undo import _ConnectionDatabase
    with open_database(tmp_path/'old.db',writable=True,create=True) as db:
        # Finite historical fixture construction, with FK validation before reads.
        db.raw.execute('PRAGMA foreign_keys=OFF')
        db.raw.execute('BEGIN IMMEDIATE')
        command.upgrade(_config('company',db.conn),revision)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        db.raw.commit()
        db.raw.execute('PRAGMA foreign_keys=ON')
        assert db.raw.execute('PRAGMA foreign_keys').fetchone()==(1,)
        db.raw.execute('CREATE TABLE local_capture_notes(value TEXT)')
        db.raw.execute("INSERT INTO local_capture_notes VALUES ('unchanged')")
        before=list(db.raw.iterdump())
        queries=[];db.raw.set_trace_callback(queries.append)
        assert not accounts._has_captured_posting_use(_ConnectionDatabase(db.conn),'unseen')
        db.raw.set_trace_callback(None)
        assert list(db.raw.iterdump())==before
        selected='\n'.join(queries)
        assert ('FROM sales_line_profiles' in selected)==(revision!='co0008')
        assert ('FROM work_lines' in selected)==(revision not in ('co0008','co0009'))
        assert ('FROM payment_profiles' in selected)==(revision in ('co0014','co0019','co0020'))
        assert ('FROM deposit_profiles' in selected)==(revision=='co0020')


@pytest.fixture
def captured_models(client,sale):
    from bookflow.company.sales_facts import SalesProfile,SalesLineProfile,TaxRule,Reference,Account
    from bookflow.company.work_tax_facts import read_facts,read_line
    from bookflow.company import deposits
    from tests.test_deposit_g1 import intent,additional
    doc=run(client,'invoice post',dict(date='2026-01-12',customer=sale['customer'],lines=[dict(item=sale['item'])]))
    work=run(client,'estimate create',dict(date='2026-01-12',title='Model path contract',customer=sale['customer'],lines=[dict(item=sale['item'])]))
    rule=TaxRule(id='tax',label='Tax',version=1,rate_percent_millionths=0,
        agency=Reference(id='agency',label='Agency',version=1),
        liability_account=Account(id='liability',name='Tax payable',full_name='Tax payable',number=None,type='other_current_liability',normal_balance='credit'))
    sp=SalesProfile.model_validate(doc['revision']['profile']).model_copy(update={'tax_rules':[rule]})
    sl=SalesLineProfile.model_validate(doc['revision']['lines'][0]['item_snapshot'])
    wr=read_facts(work['revision']['facts'])
    wr=wr.model_copy(update={'profile':wr.profile.model_copy(update={'tax_rules':[rule]})})
    wl=read_line(work['revision']['lines'][0]['facts'])
    from bookflow.company.work_tax_facts import WorkTaxCell
    wl=wl.model_copy(update={'taxes':[WorkTaxCell(rule=rule,taxable_minor_units=wl.net_minor_units,tax_minor_units=0)]})
    deposit=deposits.prepare(intent(additional('funding',1,1000),cash=1000))
    return {'sale_line':sl,'sale_profile':sp,'work_line':wl,'work_revision':wr,'deposit':deposit}


# Explicit owned paths, independent of the production predicate's SQL spelling.
@pytest.mark.parametrize('model,table,column,path,query_index',[
    ('sale_line','sales_line_profiles','item_snapshot',('income_account',),0),
    ('sale_profile','sales_profiles','profile_snapshot',('tax_rules',0,'liability_account'),1),
    ('work_line','work_lines','facts_snapshot',('profile','income_account'),3),
    ('work_line','work_lines','facts_snapshot',('taxes',0,'rule','liability_account'),3),
    ('work_revision','work_revisions','facts_snapshot',('profile','tax_rules',0,'liability_account'),4),
    ('deposit','deposit_profiles','facts_snapshot',('intent','bank'),6),
    ('deposit','deposit_profiles','facts_snapshot',('intent','cash_back','account'),6),
    ('deposit','deposit_profiles','facts_snapshot',('intent','additional',0,'account'),6),
])
def test_owning_model_persisted_account_path_and_sibling_negative(tmp_path,captured_models,model,table,column,path,query_index):
    """Isolated SQLite projections of serialized real owning models, not business writes."""
    original=captured_models[model]
    sql=[q for _,qs in accounts._CAPTURED_ACCOUNT_QUERIES for q in qs][query_index]
    with open_database(tmp_path/'path.db',writable=True,create=True) as db:
        columns={'sales_line_profiles':'item_snapshot TEXT','sales_profiles':'profile_snapshot TEXT, control_account_id TEXT',
            'work_lines':'facts_snapshot TEXT','work_revisions':'facts_snapshot TEXT',
            'deposit_profiles':'facts_snapshot TEXT, bank_account_id TEXT'}
        db.raw.execute(f'CREATE TABLE {table} ({columns[table]})')
        for positive in (True,False):
            value=original.model_dump(mode='json')
            account=value
            for key in path:account=account[key]
            account['id']='needle' if positive else 'different'
            account['name']='sibling' if positive else 'needle'
            typed=type(original).model_validate_json(json.dumps(value))
            if model=='deposit':
                from bookflow.company import deposits
                typed=deposits.prepare(typed.intent)
            db.raw.execute(f'DELETE FROM {table}')
            db.raw.execute(f'INSERT INTO {table} ({column}) VALUES (?)',(typed.model_dump_json(),))
            assert (db.conn.execute(sa.text(sql),{'account_id':'needle'}).first() is not None)==positive


@pytest.mark.timeout(180)
def test_actual_http_mcp_captured_account_denial(root,client,sale,tmp_path):
    import anyio
    from tests.mcp_matrix_support import Matrix
    target,_,_=zero_sale(client,sale)
    async def witness():
        matrix=Matrix()
        await matrix.open(root,tmp_path/'interfaces')
        try:
            for field,value,code in [('type','bank','E_TYPE_CHANGE'),('currency','EUR','E_RECORD_IN_USE')]:
                responses=[]
                for surface in ('python','http','mcp'):
                    responses.append(await matrix.call(surface,'account update',dict(account=target,**{field:value}),rejected=True))
                assert responses[0]==responses[1]==responses[2]
                assert responses[0]['code']==code and responses[0]['details']['reason']=='captured_posting_use'
                assert set(responses[0]['details'])==({'record_id','from','to','reason'} if field=='type' else {'record_id','field','reason'})
        finally:await matrix.close()
    anyio.run(witness)


@pytest.fixture(scope='module')
def legacy_captures(tmp_path_factory):
    """Create legitimate pre-fix captures with the immutable accepted binary."""
    import subprocess,sys,os,io,tarfile
    parent=tmp_path_factory.mktemp('captured-use-legacy');source=parent/'source';source.mkdir()
    pin='ad8058659556eaac5cf167ea607df9ac5c732972'
    archive=subprocess.check_output(['git','archive',pin,'src'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    code=r'''
import bookflow,json,sys
from pathlib import Path
root=Path(sys.argv[1]);c=bookflow.connect(data_root=str(root));c.init();c.demo.reset();co='Demo Plumbing Co'
transcript=[]
def run(n,d):
 r=c.run(n,d,company=co);transcript.append(dict(command=n,input=d,output=r));return r
customer=run('customer create',dict(name='Legacy captured customer'))['id']
income=run('account create',dict(name='Legacy positive income',type='income'))['id']
code=next(r['id'] for r in run('sales-tax-code list',{})['items'] if not r['taxable'])
def item(name,inc,price):return run('item create',dict(name=name,type='service',sales_enabled=True,description=name,income_account_id=inc,price=price,sales_tax_code_id=code))['id']
positive=item('Legacy positive item',income,'10')
cases=[]
for noun,kind in [('invoice','bank'),('sales-receipt','credit_card')]:
 for posted in (False,True):
  label=noun+str(posted)
  target=run('account create',dict(name=label+' target',type='income'))['id']
  zero=item(label+' zero',target,'0')
  extra={}
  if noun=='sales-receipt':extra=dict(deposit_to=run('account create',dict(name=label+' cash',type='bank'))['id'],payment_method=run('payment-method create',dict(name=label+' method',kind='cash'))['id'])
  doc=run(noun+' post',dict(date='2026-01-12',customer=customer,lines=[dict(item=positive),dict(item=zero)],**extra))
  run('item update',dict(item=zero,income_account_id=income));run('account update',dict(account=target,type=kind))
  lines=[dict(item=r['item_id'],line_id=r['line_id']) for r in doc['revision']['lines']]
  lines[1]['unit_price']='5'
  if posted:doc=run(noun+' update',{noun.replace('-','_'):doc['id'],'expected_version':1,'lines':lines})
  cases.append(dict(noun=noun,posted=posted,target=target,doc=doc,lines=lines))
(root.parent/'legacy-cases.json').write_text(json.dumps(cases))
(root.parent/'legacy-transcript.json').write_text(json.dumps(transcript))
'''
    root=parent/'root'
    result=subprocess.run([sys.executable,'-c',code,str(root)],cwd=source,
        env=dict(os.environ,PYTHONPATH=str(source/'src'),BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    (parent/'legacy-run.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stderr
    return root,json.loads((parent/'legacy-cases.json').read_text())


@pytest.mark.parametrize('case_index',range(4))
@pytest.mark.parametrize('action',['void','refresh'])
def test_legacy_anomalies_read_noop_deny_replacement_explicit_repair_and_inverse(legacy_captures,tmp_path,monkeypatch,case_index,action):
    import shutil,bookflow
    baseline,cases=legacy_captures
    root=tmp_path/'legacy';shutil.copytree(baseline,root)
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(root))
    client=bookflow.connect(data_root=str(root));case=cases[case_index]
    noun=case['noun'];key=noun.replace('-','_');doc=case['doc']
    request={key:doc['id'],'expected_version':doc['version']}
    before=snapshot(client)
    with open_database(database_path(client),writable=False) as db:
        net=db.raw.execute('SELECT coalesce(sum(debit_minor_units-credit_minor_units),0) FROM posting_lines WHERE account_id=?',(case['target'],)).fetchone()[0]
        assert net==(-500 if case['posted'] else 0)
    assert run(client,noun+' show',{key:doc['id'],'revision_number':1})['revision']['lines'][1]['net_minor_units']==0
    assert not run(client,noun+' update',request)['changed']
    for patch in ({'memo':'New replacement metadata'},{'lines':case['lines']}):
        # Already-posted positive-price input is a no-op; request a different positive price.
        if 'lines' in patch:
            patch=json.loads(json.dumps(patch));patch['lines'][1]['unit_price']='6'
        with pytest.raises(BookflowError) as exc:run(client,noun+' update',dict(request,**patch))
        assert exc.value.code=='E_VALIDATION' and exc.value.details=={'field':'lines','reason':'captured_posting_account_type'}
        assert 'refresh' in exc.value.message
        assert snapshot(client)==before
    if action=='refresh':
        lines=json.loads(json.dumps(case['lines']));lines[1]['refresh_defaults']=True
        fixed=run(client,noun+' update',dict(request,lines=lines))
        request['expected_version']=fixed['version']
    void=run(client,noun+' void',request,reason='Exact legacy inverse')
    assert void['status']=='voided'
    assert not run(client,noun+' void',{key:doc['id'],'expected_version':void['version']},reason='Repeat void')['changed']
    after=snapshot(client)
    for table in ('transaction_revisions','document_lines','posting_batches','posting_lines','posting_line_sources','sales_line_profiles'):
        assert all(row in after[table] for row in before[table])
    with open_database(database_path(client),writable=False) as db:
        assert db.raw.execute('SELECT coalesce(sum(debit_minor_units-credit_minor_units),0) FROM posting_lines WHERE account_id=?',(case['target'],)).fetchone()[0]==0
        rows=[dict(r) for r in db.conn.execute(sa.text('SELECT * FROM posting_lines WHERE transaction_id=:id'),{'id':doc['id']}).mappings()]
        by_id={r['id']:r for r in rows}
        totals={}
        for row in rows:
            totals[row['batch_id']]=totals.get(row['batch_id'],0)+row['debit_minor_units']-row['credit_minor_units']
            if row['reversed_line_id']:
                original=by_id[row['reversed_line_id']]
                assert (row['account_id'],row['debit_minor_units'],row['credit_minor_units'],row['account_snapshot'])==(original['account_id'],original['credit_minor_units'],original['debit_minor_units'],original['account_snapshot'])
        assert totals and set(totals.values())=={0}
        batches=[dict(r) for r in db.conn.execute(sa.text('SELECT * FROM posting_batches WHERE transaction_id=:id'),{'id':doc['id']}).mappings()]
        assert {b['effective_date'] for b in batches}=={'2026-01-12'}
        originals={r['id'] for r in rows if not r['reversed_line_id']}
        inverses=[r['reversed_line_id'] for r in rows if r['reversed_line_id']]
        assert set(inverses)==originals and len(inverses)==len(originals)


def test_same_type_skips_capture_lookup_and_inactive_master_is_not_capture(client,sale,monkeypatch):
    target,item,_=zero_sale(client,sale)
    def unexpected(*args):
        pytest.fail('Same-type/currency edits must not inspect captures')
    with monkeypatch.context() as patch:
        patch.setattr(accounts,'_has_captured_posting_use',unexpected)
        run(client,'account update',dict(account=target,type='income',currency='USD'))
        run(client,'account update',dict(account=target,name='Saved account renamed'))
    unused=run(client,'account create',dict(name='Inactive master only',type='income'))['id']
    soft=run(client,'item create',dict(name='Inactive uncaptured item',type='service',sales_enabled=True,
        description='Never saved on a document',income_account_id=unused,price='0',sales_tax_code_id=tax_code(client)))['id']
    run(client,'item deactivate',dict(item=soft))
    assert run(client,'account update',dict(account=unused,type='bank'))['type']=='bank'
    with pytest.raises(BookflowError) as exc:run(client,'item activate',dict(item=soft))
    assert exc.value.code=='E_VALIDATION'


def test_hidden_work_conditional_denial_does_not_filter_account_use(client,sale,monkeypatch):
    """Existing permission seam models a deny; live granular policy is not activated."""
    from bookflow.hub import access
    work=run(client,'estimate create',dict(date='2026-01-12',title='Hidden zero work',
        customer=sale['customer'],lines=[dict(item=sale['item'],unit_price='0')]))
    other=run(client,'account create',dict(name='Visible replacement',type='income'))['id']
    run(client,'item update',dict(item=sale['item'],income_account_id=other))
    original=access.require_command_activation
    def deny_work(session,cmd):
        if cmd.capability=='customer-work':raise BookflowError('E_PERMISSION')
        return original(session,cmd)
    monkeypatch.setattr(access,'require_command_activation',deny_work)
    with pytest.raises(BookflowError) as exc:run(client,'estimate show',dict(estimate=work['id']))
    assert exc.value.code=='E_PERMISSION'
    denied(client,sale['income'],'type','bank')
    denied(client,sale['income'],'currency','EUR')


def test_payment_relational_accounts_on_actual_profile(client,sale):
    from tests.test_payment_receipts import method
    doc=run(client,'payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='1.00',
        payment_method=method(client),operation_key='captured-column-contract'))
    with open_database(database_path(client),writable=False) as db:
        row=db.raw.execute('SELECT ar_account_id,deposit_account_id FROM payment_profiles WHERE transaction_id=?',(doc['id'],)).fetchone()
        assert row and row[0]!=row[1]
        for value in row:
            assert db.conn.execute(sa.text(accounts._CAPTURED_ACCOUNT_QUERIES[2][1][0]),{'account_id':value}).first()
            assert accounts._has_captured_posting_use(db,value)
        assert not db.conn.execute(sa.text(accounts._CAPTURED_ACCOUNT_QUERIES[2][1][0]),{'account_id':sale['customer']}).first()


def test_deposit_zero_bank_actual_g1_profile_storage(client,sale):
    """Validated G1 Effect persisted with its owned relational envelope, no public G2 claim."""
    from bookflow.company import schema as c,deposits,deposit_validation
    from bookflow.company.deposit_models import Account,Additional,CashBack,Dimensions,Intent
    from bookflow.core.ids import new_id
    doc=run(client,'invoice post',dict(customer=sale['customer'],date='2026-06-02',lines=[dict(item=sale['item'])]))
    def captured(name,kind):
        out=run(client,'account create',dict(name=name,type=kind))
        return Account.model_validate({k:out[k] for k in Account.model_fields})
    bank=captured('G1 captured zero bank','bank')
    cash=captured('G1 captured cash back','other_current_asset')
    income=captured('G1 captured additional','other_income')
    effect=deposits.prepare(Intent(deposit_id=new_id(),date='2026-06-03',currency='USD',bank=bank,sources=(),
        additional=(Additional(row_id=new_id(),ordinal=1,account=income,units=1000,
            dimensions=Dimensions(party_kind='customer',party_id=sale['customer'],party_name='Captured customer',class_id=None,class_name=None)),),
        cash_back=CashBack(account=cash,units=1000)))
    deposit_validation.validate(effect)
    assert effect.bank_total==0 and all(leg.account_id!=bank.id for leg in effect.legs)
    with open_database(database_path(client),writable=True) as db:
        old=dict(db.conn.execute(sa.select(c.transactions).where(c.transactions.c.id==doc['id'])).mappings().one())
        rev=dict(db.conn.execute(sa.select(c.transaction_revisions).where(c.transaction_revisions.c.id==old['current_revision_id'])).mappings().one())
        before=list(db.raw.iterdump())
        db.raw.execute('BEGIN IMMEDIATE')
        try:
            rid=new_id();identity=effect.intent.deposit_id
            db.conn.execute(c.transactions.insert().values(**dict(old,id=identity,type='deposit',number='CAPTURE-G1',current_revision_id=rid)))
            db.conn.execute(c.transaction_revisions.insert().values(**dict(rev,id=rid,transaction_id=identity,date='2026-06-03',number='CAPTURE-G1')))
            db.conn.execute(c.deposit_profiles.insert().values(revision_id=rid,transaction_id=identity,type='deposit',bank_account_id=bank.id,
                posting_total=1000,subtotal=1000,bank_total=0,cash_back=1000,facts_snapshot=effect.model_dump_json(),
                **{k:rev[k] for k in ('created_at','created_by','created_via','audit_event_id')}))
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
            for value in (bank.id,cash.id,income.id):assert accounts._has_captured_posting_use(db,value)
            assert not accounts._has_captured_posting_use(db,sale['customer'])
        finally:db.raw.execute('ROLLBACK')
        assert list(db.raw.iterdump())==before
