"""Exact publication relations over ordinary books and pure SQL graph seams."""
from types import SimpleNamespace, ModuleType
from pathlib import Path
import hashlib
import json
import subprocess
import sqlalchemy as sa
import pytest
from bookflow.company import payment_authority as pa, schema
from bookflow.core import publication_payment as pp
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale

BASE = '3e5a0bdf047f664cf056c4ea0d885ec07f022e4a'


def original_checker():
    source = subprocess.check_output(['git','show',BASE+':src/bookflow/core/publication_payment.py'], cwd=Path(__file__).resolve().parents[1])
    module = ModuleType('frozen_publication_payment')
    exec(compile(source, BASE+'/publication_payment.py','exec'), module.__dict__)
    module.source_sha256 = hashlib.sha256(source).hexdigest()
    return module


def outcome(fn):
    try:
        assert fn() is None
        return {'status':'ok'}
    except BookflowError as e:
        return {'status':'error', **e.to_dict()}


def relation(name, columns, rows):
    selects = [sa.select(*(sa.literal(value).label(column) for column,value in zip(columns,row))) for row in rows]
    if not selects:
        return sa.select(*(sa.literal(None).label(column) for column in columns)).where(sa.false()).cte(name)
    return (selects[0].union_all(*selects[1:]) if len(selects)>1 else selects[0]).cte(name)


@pytest.fixture
def graph(monkeypatch):
    """Only SELECT/VALUES CTEs, never malformed company storage or disabled FKs."""
    engine = sa.create_engine('sqlite://')
    connection = engine.connect()
    def build(*, roots=('p0','j0','k0','zero0','x','y','z','other'), work=(), extra_posts=(), apps=None, missing_payer=False, postings=None):
        rows = {
            'customers': (('id','parent_id'), [('P',None),('J','P'),('K','J'),('U',None)] if not missing_payer else [('U',None)]),
            'accounts': (('id','type'), [('ar','accounts_receivable'),('bank','bank')]),
            'transactions': (('id',), [(x,) for x in roots]),
            'posting_lines': (('transaction_id','account_id','name_type','name_id'),
                postings if postings is not None else [('p0','ar','customer','P'),('j0','ar','customer','J'),('k0','ar','customer','K'),
                 ('zero0','ar','customer','P'),('zero0','ar','customer','P'), # original and reversal remain contributors
                 ('other','bank','customer','P'),('other','ar','vendor','P'),('other','ar','customer','U'), *extra_posts]),
            'applications': (('id','paying_transaction_id','paid_transaction_id','kind'), apps if apps is not None else
                [('a','j0','x','apply'),('b','j0','x','unapply'),('c','k0','y','apply'),('d','x','z','apply')]),
            'work_billing_allocations': (('id','transaction_id'), [(str(i),value) for i,value in enumerate(work)]),
        }
        for name,(columns,values) in rows.items():
            monkeypatch.setattr(schema,name,relation(name,columns,values))
        families={'P':['P','J','K'] if not missing_payer else [],'J':['J','K'],'K':['K'],'U':['U']}
        def raw(sql, args):
            assert sql.lstrip().startswith('WITH RECURSIVE family')
            return [(x,) for x in families.get(args[0],[])]
        return SimpleNamespace(company=SimpleNamespace(conn=connection,raw=SimpleNamespace(execute=raw)))
    yield build
    connection.close();engine.dispose()


@pytest.mark.parametrize('write',[False,True])
@pytest.mark.parametrize('work,expected', [(('p0',),True),(('x',),True),(('y',),True),(('z',),False),((),False)])
def test_hand_enumerated_one_hop_sets(graph, monkeypatch, tmp_path, work, expected, write):
    s=graph(work=work)
    F={'P','J','K'};B={'p0','j0','k0','zero0'};H={'x','y'};C=B|H
    family=pa._publication_payer_family('P')
    selected=pa._publication_payer_transactions('P')
    assert set(s.company.conn.execute(sa.select(family.c.id)).scalars())==F
    assert set(s.company.conn.execute(selected).scalars())==B
    h=sa.select(schema.applications.c.paid_transaction_id).where(schema.applications.c.paying_transaction_id.in_(selected))
    assert set(s.company.conn.execute(h).scalars())==H
    assert set(s.company.conn.execute(selected.union(h)).scalars())==C
    assert pa.payer_transactions(s.company,'P')==B
    assert pa.linked_work_required(s.company,B) is expected
    assert set(s.company.conn.execute(pa._publication_payer_transactions('J')).scalars())=={'j0','k0'}
    calls=[];monkeypatch.setattr(pa,'require_resource',lambda s,r,role:calls.append((r,role)))
    old=original_checker(); roots=[('payer','P',write)]
    previous=outcome(lambda:old.check(s,roots));old_calls=calls[:];calls.clear()
    current=outcome(lambda:pp.check(s,roots))
    assert previous==current=={'status':'ok'}
    role='standard' if write else 'member'
    assert calls==old_calls==[('ledger.post' if write else 'ledger.read',role)]+([('customer-work',role)] if expected else [])
    (tmp_path/'exact-relations.json').write_text(json.dumps({'F':sorted(F),'B':sorted(B),'H':sorted(H),'C':sorted(C),'M':[], 'W':expected,'old':previous,'new':current,'calls':calls,'old_source':old.source_sha256},indent=2))


@pytest.mark.parametrize('target_kind', ['transaction','payer'])
@pytest.mark.parametrize('shape', ['root','base','mixed_base','target','target_work','payer'])
def test_approved_missing_evidence_corrections(graph,monkeypatch,tmp_path,shape,target_kind):
    missing='x' if shape.startswith('target') else 'p0'
    roots=tuple(x for x in ('p0','j0','k0','zero0','x','y','z','other') if x!=missing)
    s=graph(roots=roots,work=('x',) if shape=='target_work' else (),missing_payer=shape=='payer',postings=[('p0','ar','customer','P')] if shape=='base' else None)
    supplied=[('transaction','p0',False)] if shape=='root' else [(target_kind,'j0' if target_kind=='transaction' else 'P',False)] if shape.startswith('target') else [('payer','P',False)]
    calls=[];monkeypatch.setattr(pa,'require_resource',lambda s,r,role:calls.append((r,role)))
    old=original_checker();previous=outcome(lambda:old.check(s,supplied));old_calls=calls[:];calls.clear()
    current=outcome(lambda:pp.check(s,supplied))
    assert current['code']=='E_PERMISSION' and current['details']=={'reason':'unresolved_payment_evidence'}
    assert calls==[]
    if shape in ('target','target_work','payer'):
        assert previous=={'status':'ok'}  # Explicit approved correction, not parity.
        assert old_calls==[('ledger.read','member')]+([('customer-work','member')] if shape=='target_work' else [])
    else:
        assert previous==current and old_calls==[]
    (tmp_path/'r1-outcomes.json').write_text(json.dumps({'shape':shape,'old':previous,'new':current,'old_calls':old_calls,'new_calls':calls},indent=2))


def test_fanout_and_reverse_siblings_do_not_change_root_scope(graph,monkeypatch):
    targets=[f'invoice-{i}' for i in range(201)]
    apps=[(str(i),'p0',target,'apply') for i,target in enumerate(targets)]
    s=graph(roots=['p0','plain',*targets],apps=apps+[('plain','p0','plain','apply')],work=(targets[-1],))
    calls=[];monkeypatch.setattr(pa,'require_resource',lambda s,r,role:calls.append((r,role)))
    assert pa.linked_work_required(s.company,{'p0'})
    pa.authorize_publication_transactions(s,[('plain',False)])
    assert calls==[('ledger.read','member')]  # No reverse sibling path.
    calls.clear();pa.authorize_publication_transactions(s,[('p0',False)])
    assert calls==[('ledger.read','member'),('customer-work','member')]


def test_public_books_exact_sets_history_reparent_and_fanout(client, root, sale, monkeypatch, tmp_path):
    from bookflow.core import registry
    from tests.test_payment_receipts import method, posted
    from tests.test_work_billing_lifecycle import accepted, bill
    from tests.test_attachment_commands import add
    from tests.test_payment_publication_freshness import raw_snapshot
    from tests.test_service_sales_lifecycle import COMPANY
    P=sale['customer']
    J=client.customer.create(name='Publication J',parent_id=P,company=COMPANY)['id']
    K=client.customer.create(name='Publication K',parent_id=J,company=COMPANY)['id']
    U=client.customer.create(name='Publication U',company=COMPANY)['id']
    E=client.customer.create(name='Publication Empty',company=COMPANY)['id']
    cash=method(client)
    x=bill(client,accepted(client,{**sale,'customer':J}))
    y=posted(client,K,sale['item'],'1.00','PUB-Y')
    def receive(payer,key,invoice=None):
        return client.run('payment receive',dict(customer=payer,date='2026-06-03',amount='1.00',
            payment_method=cash,operation_key=key,applications=dict(mode='inline',items=[] if invoice is None else [
                dict(invoice=invoice['id'],expected_version=invoice['version'],amount='0.01')])),company=COMPANY)
    p0=receive(P,'pub-p0');j0=receive(J,'pub-j0',x);k0=receive(K,'pub-k0',y);zero=receive(P,'pub-zero');other=receive(U,'pub-other')
    client.run('payment unapply',dict(payment=j0['id'],expected_version=1,operation_key='pub-unapply',applications=[
        dict(application_id=j0['effect']['applications'][0]['application_id'],invoice_expected_version=2)]),company=COMPANY,reason='Historical authority witness')
    client.run('payment void',dict(payment=zero['id'],expected_version=1,operation_key='pub-void'),company=COMPANY,reason='Reversal contributor')
    # Remove current billing provenance through its supported correction. Old
    # allocation rows alone must still require work access.
    edited=client.run('invoice update',dict(invoice=x['id'],expected_version=3,
        lines=[dict(item=sale['item'],quantity='1',unit_price='1.00')]),company=COMPANY)
    assert not edited['revision']['billing_sources']
    fanout={receive(P,f'pub-fanout-{i}')['id'] for i in range(201)}
    assert len(fanout)==201
    add(client,{'id':P})
    company=client.company.show(company=COMPANY)['company_id']
    B={p0['id'],j0['id'],k0['id'],zero['id'],x['id'],y['id']}|fanout
    H={x['id'],y['id']}
    # Ordinary invoices themselves contribute AR; unlike the pure logical
    # seam above H is a subset of B in these valid books.
    old=original_checker();cmd=registry.get('payment query');planner=cmd.plan;receipts=[]
    def check_group(label, expectations):
        before=raw_snapshot(root,company);assert before['attachments']
        called=[]
        def witness(inp,ctx,s):
            called.append(True)
            for payer,F,expectedB,expectedH,W in expectations:
                family=pa._publication_payer_family(payer);selected=pa._publication_payer_transactions(payer)
                gotF=set(s.company.conn.execute(sa.select(family.c.id)).scalars())
                gotB=set(s.company.conn.execute(selected).scalars())
                h=sa.select(schema.applications.c.paid_transaction_id).where(schema.applications.c.paying_transaction_id.in_(selected))
                gotH=set(s.company.conn.execute(h).scalars());gotC=set(s.company.conn.execute(selected.union(h)).scalars())
                assert (gotF,gotB,gotH,gotC)==(F,expectedB,expectedH,expectedB|expectedH)
                assert pa.payer_transactions(s.company,payer)==expectedB
                assert pa.linked_work_required(s.company,expectedB) is W
                gates=[];gate=pa.require_resource
                def observed(session,resource,role):
                    gates.append((resource,role));return gate(session,resource,role)
                with monkeypatch.context() as patch:
                    patch.setattr(pa,'require_resource',observed)
                    previous=outcome(lambda:old.check(s,[('payer',payer,False)]));oldgates=gates[:];gates.clear()
                    current=outcome(lambda:pp.check(s,[('payer',payer,False)]))
                assert previous==current=={'status':'ok'}
                assert gates==oldgates==[('ledger.read','member')]+([('customer-work','member')] if W else [])
                receipts.append(dict(label=label,payer=payer,F=sorted(gotF),B=sorted(gotB),H=sorted(gotH),C=sorted(gotC),M=[],W=W,
                    old=previous,new=current,gates=gates,old_source=old.source_sha256))
            return planner(inp,ctx,s)
        with monkeypatch.context() as patch:
            patch.setattr(cmd,'plan',witness)
            page=client.run('payment query',dict(limit=1),company=COMPANY)
        assert called==[True] and len(page['items'])==1
        assert raw_snapshot(root,company)==before
    check_group('initial',[(P,{P,J,K},B,H,True),(J,{J,K},{j0['id'],k0['id'],x['id'],y['id']},H,True),(E,{E},set(),set(),False)])
    client.customer.deactivate(customer=K,expected_version=1,company=COMPANY)
    check_group('inactive-descendant',[(P,{P,J,K},B,H,True)])
    client.customer.update(customer=J,expected_version=1,parent_id=U,company=COMPANY)
    check_group('reparented',[(P,{P},{p0['id'],zero['id']}|fanout,set(),False),
        (J,{J,K},{j0['id'],k0['id'],x['id'],y['id']},H,True),
        (U,{U,J,K},{other['id'],j0['id'],k0['id'],x['id'],y['id']},H,True)])
    assert len(receipts)==7 and all(row['old_source']==old.source_sha256 for row in receipts)
    (tmp_path/'public-exact-sets.json').write_text(json.dumps(receipts,indent=2))
