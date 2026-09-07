"""Distinct graph dimension: retained real payment with403 applications, no reseed."""
import hashlib
import json
import os
import shutil
from pathlib import Path
import pytest
import bookflow
from bookflow.company import deposit_drafts as d,deposit_draft_models as m,deposit_lifecycle as life,deposit_persistence as persist
from bookflow.company import deposit_coordination as coord,deposit_coordinate_persistence as cp
from bookflow.company.deposit_coordinate_models import CoordinateInput,CoordinateOutput
from bookflow.company.deposit_lifecycle_models import LifecycleOutput
from bookflow.core.publication import OSBinding
from tests.test_deposit_draft_financial import run_private
from tests.test_service_sales_lifecycle import COMPANY

ORIGINAL=Path('/tmp/bookflow-deposit-source-coordination-current/.cache/source-coordination/pytest-all-active-403')

def hashes(root):
    return {str(p.relative_to(root)):dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(root.rglob('*')) if p.is_file()}

@pytest.fixture(scope='module')
def world():
    roots=sorted({p.resolve() for p in ORIGINAL.glob('test_all_active*/root')})
    assert len(roots)==1
    original=roots[0];before=hashes(original)
    root=Path(os.environ['BOOKFLOW_G3_APPLICATION_WORLD'])
    if not root.exists():shutil.copytree(original,root)
    manifest=root.parent/'source-provenance.json'
    if not manifest.exists():manifest.write_text(json.dumps(dict(original=str(original),hashes=before),indent=2))
    value=dict(root=root)
    for key,model in (('ready',m.DraftOutput),('posted',LifecycleOutput),('edit',m.DraftOutput),('result',CoordinateOutput)):
        path=root.parent/(key+'.json')
        if path.exists():value[key]=model.model_validate_json(path.read_text())
    receipt=root.parent/'source.json'
    if receipt.exists():value['source']=json.loads(receipt.read_text())
    yield value
    assert hashes(original)==before
    (root.parent/'source-after.json').write_text(json.dumps(hashes(original),indent=2))

@pytest.fixture
def client(world,monkeypatch):
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(world['root']))
    monkeypatch.delenv('BOOKFLOW_COMPANY',raising=False)
    return bookflow.connect(data_root=str(world['root']))

def save(world,key,value):
    world[key]=value
    (world['root'].parent/(key+'.json')).write_text(value.model_dump_json() if hasattr(value,'model_dump_json') else json.dumps(value))


def test_01_capture_retained403_application_source(client,world,run_private):
    client.upgrade()
    def capture(s,ctx):
        saved=json.loads(s.company.raw.execute("SELECT effect_snapshot FROM payment_operations WHERE operation_key='complete-cancellation'").fetchone()[0])
        source=saved['id']
        apps=dict(s.company.raw.execute('SELECT id,paid_transaction_id FROM applications WHERE paying_transaction_id=?',(source,)))
        alloc=dict(s.company.raw.execute('SELECT id,target_transaction_id FROM application_allocations WHERE source_transaction_id=?',(source,)))
        version=s.company.raw.execute('SELECT version FROM transactions WHERE id=?',(source,)).fetchone()[0]
        assert len(apps)==len(alloc)==len(set(apps.values()))==403
        return dict(id=source,version=version,apps=apps,allocations=alloc)
    save(world,'source',run_private(capture))
    bank=client.account.create(name='G3 application bank',type='bank',company=COMPANY)['id']
    def create(s,ctx):
        draft=d.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(date='2026-06-03',deposit_to=bank)),'create')
        return d.run(s,ctx,m.DraftUpdate(draft=draft.id,expected_version=draft.version,set_sources=[m.SourcePatch(source=world['source']['id'],source_type='payment',expected_version=world['source']['version'])]),'update')
    save(world,'ready',run_private(create))
    assert world['ready'].summary.source_count==1 and world['ready'].summary.bank_total==40400


def test_02_preview_draft_with403_applications(world,run_private):
    draft=world['ready']
    world['post_input']=life.INPUTS['post'].model_validate(dict(operation_key='G3-applications-post',document=dict(mode='draft',draft=draft.id,expected_version=draft.version)))
    def prepare(s,ctx):
        # Frozen producer uses count+1 currency units:403 invoices plus1 credit.
        assert world['ready'].summary.bank_total==40400
        assert s.company.raw.execute('SELECT sum(amount_minor_units) FROM applications WHERE paying_transaction_id=?',(world['source']['id'],)).fetchone()[0]==40300
        assert s.company.raw.execute("SELECT sum(p.debit_minor_units-p.credit_minor_units) FROM posting_lines p JOIN accounts a ON a.id=p.account_id WHERE p.transaction_id=? AND a.system_role='undeposited_funds'",(world['source']['id'],)).fetchone()[0]==40400
        return life.prepare(s,ctx,world['post_input'],'post')
    world['post_prepared']=run_private(prepare)


def test_03_commit_draft_with403_applications(world,run_private):
    out=run_private(lambda s,ctx:persist.execute(s,ctx,world['post_prepared']))
    assert out.current.revision_bank_total==40400 and len(out.effect.memberships)==1
    assert out.current_draft.state=='consumed'
    save(world,'posted',out)


def test_04_build_removal_draft_preserving_complete_application_action(client,world,run_private):
    income=client.account.create(name='G3 application replacement income',type='income',company=COMPANY)['id']
    customer=client.customer.create(name='G3 application replacement payee',company=COMPANY)['id']
    def create(s,ctx):
        draft=d.run(s,ctx,m.DraftCreate(from_deposit=world['posted'].current.id,expected_version=1),'create')
        return d.run(s,ctx,m.DraftUpdate(draft=draft.id,expected_version=draft.version,remove_sources=[world['source']['id']],set_additional=[m.AdditionalPatch(received_from=m.Party(kind='customer',id=customer),from_account=income,amount='5')]),'update')
    save(world,'edit',run_private(create))


def test_05_preview_all_active403_and_draft_removal(world,run_private):
    draft=world['edit'];source=world['source']
    world['coordinate_input']=CoordinateInput.model_validate(dict(deposit=world['posted'].current.id,expected_version=1,operation_key='G3-applications-all-active',
        source_action=dict(kind='payment_void',payment=source['id'],expected_version=source['version']+1,unapply='all_active'),
        replacement=dict(mode='document',document=dict(mode='draft',draft=draft.id,expected_version=draft.version),draft_source_result='remove')))
    world['coordinate_prepared']=run_private(lambda s,ctx:coord.prepare(s,ctx,world['coordinate_input'],binding=OSBinding.from_session(s)))


def test_06_commit_all_active403_and_draft_removal(world,run_private):
    preview=world['coordinate_prepared'];inp=world['coordinate_input'].model_copy(update={'dependency_guard':preview.dependency_guard})
    # Coordinate Prepared stores the input in its resolved snapshot; use the
    # owning guarded preparation rather than constructing internal overlays.
    world['coordinate_guarded']=run_private(lambda s,ctx:coord.prepare(s,ctx,inp,binding=OSBinding.from_session(s)))


def test_07_persist_complete_all_active403(world,run_private):
    def execute(s,ctx):
        old=dict(s.company.raw.execute('SELECT id,version FROM transactions'))
        out=cp.execute(s,ctx,world['coordinate_guarded'])
        source=world['source'];inserted=out.effect.source.inserted
        assert len(inserted.applications)==403 and {v.reverses_application_id for v in inserted.applications}==set(source['apps'])
        assert len(inserted.application_allocations)==403 and {v.reverses_allocation_id for v in inserted.application_allocations}==set(source['allocations'])
        assert sum(v.amount_minor_units for v in inserted.applications)==sum(v.amount_minor_units for v in inserted.application_allocations)==40300
        assert {v.invoice_id:v.due_minor_units for v in out.effect.source.payment_effect.effect.document_changes}=={v:100 for v in source['apps'].values()}
        assert {v.after.id for v in out.effect.headers}==set(source['apps'].values())|{source['id'],world['posted'].current.id}
        assert len(out.effect.headers)==405 and all(v.after.version==old[v.after.id]+1 for v in out.effect.headers)
        assert out.current.revision_bank_total==500 and out.current.status=='posted' and not out.current.active_source_ids
        assert out.effect.source.after_header.status=='voided' and out.current_draft.id==world['edit'].id
        assert out.current_draft.state=='consumed' and len(out.effect.deposit.batch_ids)==2
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
        return out
    save(world,'result',run_private(execute))


def test_08_recover_complete_all_active403_without_mutation(world,run_private):
    def check(s,ctx):
        before=tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump())
        out=cp.recover(s,ctx,world['coordinate_input'],OSBinding.from_session(s))
        assert out.idempotent_replay and out.effect==world['result'].effect and out.current_draft.state=='consumed'
        assert (tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))==before
    run_private(check)


def test_09_independent_final403_application_ledger_and_consumption_rows(world,run_private):
    def check(s,ctx):
        source=world['source'];out=world['result'];deposit=out.current.id
        bank=world['posted'].effect.financial.intent.bank.id
        assert s.company.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? AND account_id=?',(deposit,bank)).fetchone()[0]==500
        for identity in (deposit,source['id']):
            assert s.company.raw.execute("SELECT sum(p.debit_minor_units-p.credit_minor_units) FROM posting_lines p JOIN accounts a ON a.id=p.account_id WHERE p.transaction_id=? AND a.system_role='undeposited_funds'",(identity,)).fetchone()[0]==0
            assert all(v[1]==0 for v in s.company.raw.execute('SELECT batch_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY batch_id',(identity,)))
        invoice_ids=set(source['apps'].values())
        rows=dict(s.company.raw.execute("SELECT id,version FROM transactions WHERE type='invoice'"))
        assert {identity:rows[identity] for identity in invoice_ids}=={identity:3 for identity in invoice_ids}
        receipts=s.company.raw.execute('SELECT d.id,d.state,d.current_revision_id,d.consumed_revision_id,d.consumed_operation_id,r.revision_id,r.operation_id,r.manifest_hash FROM deposit_drafts d JOIN deposit_draft_consumptions r ON r.draft_id=d.id').fetchall()
        assert len(receipts)==2
        by_id={v[0]:v for v in receipts}
        for draft,operation in ((world['ready'],world['posted']),(world['edit'],out)):
            row=by_id[draft.id]
            assert row[1:] == ('consumed',draft.revision_id,draft.revision_id,operation.operation_id,draft.revision_id,operation.operation_id,draft.manifest_hash)
        assert s.company.raw.execute('SELECT count(*) FROM deposit_current_memberships WHERE transaction_id=?',(deposit,)).fetchone()[0]==0
    run_private(check)
