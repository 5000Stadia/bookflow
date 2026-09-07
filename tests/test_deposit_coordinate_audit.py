"""Byte-exact ordinary writer compatibility with controlled existing ID/clock."""
import ast
import subprocess
from pathlib import Path
import pytest
from bookflow.core import audit
from bookflow.core.context import Context, Interface
from bookflow.core.registry import Touched
from tests.test_deposit_lifecycle import driver

BASE='703c002945b2ccbd564d98ea20b0a0d5f690f300'


def test_ordinary_audit_rows_equal_frozen_writer_and_preassembly_is_readonly(client,driver,monkeypatch):
    original=subprocess.check_output(['git','show',BASE+':src/bookflow/core/audit.py'],cwd=Path(__file__).parents[1],text=True)
    node=next(n for n in ast.parse(original).body if isinstance(n,ast.FunctionDef) and n.name=='write_event_to')
    node.decorator_list=[]
    namespace=dict(audit.__dict__)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<accepted audit writer>','exec'),namespace)
    ctx=Context.new(Interface.python,'byte compatibility',reason='Verify exact ordinary snapshots')
    touched=[Touched('customer','owned-audit-target','update',1,2,
        dict(name='A\0B',tax_id='secret',memo='é'*500),dict(name='old',tax_id=None),db='company')]
    with driver.session() as s:
        baseline=tuple(s.company.raw.iterdump())
        def run(writer):
            ids=iter(('01AAAAAAAAAAAAAAAAAAAAAAAA','01BBBBBBBBBBBBBBBBBBBBBBBB'))
            with monkeypatch.context() as patch:
                patch.setattr(audit,'new_id',lambda:next(ids))
                patch.setattr(audit,'now_iso',lambda:'2026-06-03T12:01:02+00:00')
                namespace['new_id']=audit.new_id;namespace['now_iso']=audit.now_iso
                s.company.raw.execute('SAVEPOINT audit_compatibility')
                try:
                    event=writer(s.company,ctx,'customer update','x'*600,touched,actor_id=s.actor.id,actor_kind=s.actor.kind)
                    result=(s.company.raw.execute('SELECT * FROM audit_events WHERE id=?',(event,)).fetchall(),
                        s.company.raw.execute('SELECT * FROM audit_entries WHERE event_id=?',(event,)).fetchall())
                finally:
                    s.company.raw.execute('ROLLBACK TO audit_compatibility');s.company.raw.execute('RELEASE audit_compatibility')
            assert tuple(s.company.raw.iterdump())==baseline
            return result
        expected=run(namespace['write_event_to'])
        actual=run(audit.write_event_to)
        assert actual==expected
        prepared=audit.prepare_event_to(s.company,ctx,'customer update','prepared',touched,actor_id=s.actor.id,actor_kind=s.actor.kind)
        assert tuple(s.company.raw.iterdump())==baseline
        assert audit.decode_snapshot(prepared.entries[0]['after'])['name']=='A\0B'
        assert audit.decode_snapshot(prepared.entries[0]['after'])['tax_id'].startswith('sha256:')
        assert prepared.entries[0]['after'][:1]==audit.ZIP
