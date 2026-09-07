"""One retained producer-built 403 graph, copied without changing B evidence."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import pytest
import bookflow
from bookflow.core.context import Context,Interface
from bookflow.core.publication import OSBinding
from bookflow.company import deposit_coordination as coord, deposit_coordinate_persistence as persistence
from bookflow.company import deposit_operation_pages as pages
from bookflow.company.deposit_coordinate_models import CoordinateInput
from bookflow.company.deposit_dependency_models import PageInput
from tests.test_deposit_lifecycle import driver
from tests.test_row8_journal import database_path

ORIGINAL=Path('/tmp/bookflow-deposit-source-coordination-current/.cache/source-coordination/pytest-all-active-403')


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


@pytest.mark.timeout(600)
def test_all_active_403_complete_commit_and_pages(tmp_path,monkeypatch):
    roots=sorted({p.resolve() for p in ORIGINAL.glob('test_all_active*/root')})
    assert len(roots)==1
    original=roots[0];before=hashes(original)
    root=tmp_path/'root';shutil.copytree(original,root)
    assert hashes(original)==before
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(root));monkeypatch.delenv('BOOKFLOW_COMPANY',raising=False)
    client=bookflow.connect(data_root=str(root));company='Demo Plumbing Co'
    client.upgrade()
    path=database_path(client)
    with sqlite3.connect(path) as db:
        payment=json.loads(db.execute("SELECT effect_snapshot FROM payment_operations WHERE operation_key='complete-cancellation'").fetchone()[0])['id']
        apps=dict(db.execute('SELECT id,paid_transaction_id FROM applications WHERE paying_transaction_id=?',(payment,)))
        allocations=dict(db.execute('SELECT id,target_transaction_id FROM application_allocations WHERE source_transaction_id=?',(payment,)))
        version=db.execute('SELECT version FROM transactions WHERE id=?',(payment,)).fetchone()[0]
    assert len(apps)==len(allocations)==len(set(apps.values()))==403
    bank=client.account.create(name='C403 Bank',type='bank',company=company)['id']
    owned=driver.__wrapped__(client,monkeypatch)
    deposited=owned.run('post',dict(operation_key='C403-post',document=dict(mode='inline',date='2026-06-03',deposit_to=bank,
        sources=[dict(source_type='payment',source=payment,expected_version=version)])))
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='C403-void',
        source_action=dict(kind='payment_void',payment=payment,expected_version=version+1,unapply='all_active'),replacement=dict(mode='void'))
    ctx=Context.new(Interface.python,'C403 complete persistence',reason='Cancel all applications and deposited cash')
    with owned.session() as s:
        binding=OSBinding.from_session(s)
        p=coord.prepare(s,ctx,inp,binding=binding)
        inp=inp.model_copy(update={'dependency_guard':p.dependency_guard})
        p=coord.prepare(s,ctx,inp,binding=binding)
        old=dict(s.company.raw.execute('SELECT id,version FROM transactions'))
        output=persistence.execute(s,ctx,p)
        assert {v.after.id for v in output.effect.headers}==set(apps.values())|{payment,deposited.current.id}
        assert len(output.effect.headers)==405
        assert all(v.after.version==old[v.after.id]+1 for v in output.effect.headers)
        inserted=output.effect.source.inserted
        assert len(inserted.applications)==403 and {v.reverses_application_id for v in inserted.applications}==set(apps)
        assert len(inserted.application_allocations)==403 and {v.reverses_allocation_id for v in inserted.application_allocations}==set(allocations)
        assert sum(v.amount_minor_units for v in inserted.applications)==40300
        assert sum(v.amount_minor_units for v in inserted.application_allocations)==40300
        assert {v.invoice_id:v.due_minor_units for v in output.effect.source.payment_effect.effect.document_changes}=={v:100 for v in apps.values()}
        assert output.current.status=='voided' and output.current.effective_bank_total==0
        assert len(output.effect.deposit.batch_ids)==1 and len(inserted.posting_batches)==1
        assert output.effect.source.after_header.status=='voided'
        for kind,expected in pages.collections(output).items():
            for limit in (1,50,200):
                actual=[];cursor=None;seen=set()
                while True:
                    page=pages.items(s,inp.operation_key,kind,PageInput(limit=limit,cursor=cursor),binding)
                    assert page.total_count==len(expected)
                    actual.extend(page.items)
                    if page.next_cursor is None:break
                    assert page.next_cursor not in seen
                    seen.add(page.next_cursor);cursor=page.next_cursor
                assert actual==expected
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
    # This outer transaction actually committed. Lost-response recovery observes
    # the durable operation without another event or row.
    with owned.session() as s:
        frozen=tuple(s.company.raw.iterdump())
        replay=persistence.recover(s,ctx,inp,OSBinding.from_session(s))
        assert replay.effect==output.effect and replay.idempotent_replay
        assert tuple(s.company.raw.iterdump())==frozen
    assert hashes(original)==before
    (tmp_path/'retained-provenance.json').write_text(json.dumps(dict(original=str(original),hashes=before),indent=2))
