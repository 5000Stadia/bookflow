"""U2 actual pinned producer facts, selected hub anchors and complete guards."""
import json
import sqlite3
import pytest
from bookflow import BookflowError
from bookflow.company import deposit_coordination as coordinator, deposit_dependency_history as history
from bookflow.company.deposit_coordinate_models import CoordinateInput
from bookflow.core.context import Context, Interface
from bookflow.core.publication import OSBinding
from tests.test_deposit_source_coordination import sale, driver, uf, method, additional_document, replacement, COMPANY, canonical_source_data
from tests.test_deposit_dependency_binding import observe, _storage
from tests.test_row8_journal import database_path


@pytest.fixture
def world(client,sale,driver):
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=method(client),lines=[dict(item=sale['item'],net_amount='1')]),company=COMPANY)
    doc=additional_document(client,sale,'1');doc['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    deposited=driver.run('post',dict(operation_key='U2-deposit',document=doc))
    body=replacement(deposited,doc);body['sources']=[dict(source_result=True,source=receipt['id'])]
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='U2-coordinate',source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=receipt['id'],expected_version=2,refresh_defaults=True)),replacement=dict(mode='document',document=body))
    return inp,Context.new(Interface.python,'U2 full source',reason='Refresh captured source facts'),client.company.list()['items'][0]['company_id'],database_path(client),receipt


def prepared(s,world):
    return coordinator.prepare(s,world[1],world[0],binding=OSBinding.from_session(s))


def request(world):
    return history.request(dict(command='deposit coordinate',input=world[0].model_dump(mode='json',by_alias=True,exclude_unset=True),context=dict(reason=world[1].reason)))


def test_nonrefresh_and_deposit_issuer_remain_immutable(root,client,world,monkeypatch):
    inp,ctx,company,path,receipt=world
    action=inp.source_action.model_copy(update={'input':inp.source_action.input.model_copy(update={'refresh_defaults':False})})
    world=(inp.model_copy(update={'source_action':action}),ctx,company,path,receipt)
    first=observe(client,monkeypatch,lambda s:prepared(s,world),company)
    client.run('company rename',dict(name='U2 later nonrefresh name',move=False),company=company)
    saved=_storage(root,path)
    def check(s):
        result=prepared(s,world)
        assert result.dependency_guard==first.dependency_guard
        assert result.facts_fingerprint==first.facts_fingerprint
        assert result.resolution.source.plan.preview.revision.issuer_snapshot==receipt['revision']['issuer_snapshot']
        assert json.loads(result.resolution.deposit_data_json)['issuer']==json.loads(first.resolution.deposit_data_json)['issuer']
        comparison=history.compare(s,first.dependency_guard,request(world),OSBinding.from_session(s))
        assert comparison.matches and not comparison.unknown_history and not comparison.changes
        assert json.loads(result.readset_json)['issuer'] is None
    observe(client,monkeypatch,check,company)
    assert _storage(root,path)==saved


def test_failed_copy_readonly_and_complete_ordinary_source_equivalence(root,client,world,monkeypatch):
    from bookflow.company import info, sales
    inp,ctx,company,path,receipt=world
    first=observe(client,monkeypatch,lambda s:prepared(s,world),company)
    def fail(*args,**kwargs):raise BookflowError('E_IO')
    with monkeypatch.context() as patch:
        patch.setattr(info,'write_display_name_copy',fail)
        client.run('company rename',dict(name='U2 authorized pinned name',move=False),company=company)
    saved=_storage(root,path)
    def check(s):
        assert s.company_info_row['display_name']!='U2 authorized pinned name'
        result=prepared(s,world)
        source=result.resolution.source
        ordinary=sales.prepare(s,ctx,inp.source_action.input,'sales_receipt','update',provenance=source.provenance)
        assert canonical_source_data(ordinary,payment=False)==canonical_source_data(source.plan,payment=False)
        assert ordinary.preview.revision.issuer_snapshot==source.plan.preview.revision.issuer_snapshot
        assert ordinary.preview.revision.issuer_snapshot['display_name']=='U2 authorized pinned name'
        assert result.dependency_guard!=first.dependency_guard
        assert json.loads(result.resolution.deposit_data_json)['issuer']==json.loads(first.resolution.deposit_data_json)['issuer']
        coordinator.validate(s,ctx,result)
    observe(client,monkeypatch,check,company)
    assert _storage(root,path)==saved


def test_source_aba_full_attribution_and_one_item_pages(root,client,world,monkeypatch):
    from bookflow.company.deposit_dependency_pages import changes_page
    from bookflow.company.deposit_dependency_models import PageInput
    inp,ctx,company,path,receipt=world
    first=observe(client,monkeypatch,lambda s:prepared(s,world),company)
    initial=first.resolution.source.plan.preview.revision.issuer_snapshot['display_name']
    client.run('company rename',dict(name='U2 intermediate name',move=False),company=company)
    middle=observe(client,monkeypatch,lambda s:prepared(s,world),company)
    client.run('company rename',dict(name=initial,move=False),company=company)
    with sqlite3.connect(root/'hub.db') as db:
        expected=[db.execute('SELECT e.id,e.actor_id,e.actor_kind,e.on_behalf_of,e.interface,e.at,a.version_before,a.version_after FROM audit_entries a JOIN audit_events e ON e.id=a.event_id WHERE a.id=?',(json.loads(middle.readset_json)['issuer']['entry_id'],)).fetchone()]
    saved=_storage(root,path)
    def check(s):
        result=prepared(s,world);binding=OSBinding.from_session(s)
        assert result.resolution.source.plan.preview.revision.issuer_snapshot==first.resolution.source.plan.preview.revision.issuer_snapshot
        assert result.dependency_guard!=first.dependency_guard
        last=json.loads(result.readset_json)['issuer']
        from bookflow.hub import schema as h
        import sqlalchemy as sa
        row=s.hub.conn.execute(sa.select(h.audit_entries,h.audit_events).join(h.audit_events,h.audit_entries.c.event_id==h.audit_events.c.id).where(h.audit_entries.c.id==last['entry_id'])).mappings().one()
        expected.append((row['event_id'],row['actor_id'],row['actor_kind'],row['on_behalf_of'],row['interface'],row['at'],row['version_before'],row['version_after']))
        from bookflow.core import clock
        moment=clock.now()
        monkeypatch.setattr(clock,'now',lambda:moment)
        compared=history.compare(s,first.dependency_guard,request(world),binding)
        assert not compared.matches and not compared.unknown_history and len(compared.changes)==2
        assert [(r.event_id,r.actor_id,r.actor_kind,r.on_behalf_of,r.interface,r.at,r.version_before,r.version_after) for r in compared.changes]==expected
        assert all(r.record_id==company and r.fields==('issuer.display_name',) for r in compared.changes)
        one=changes_page(s,first.dependency_guard,request(world),PageInput(limit=1),binding)
        two=changes_page(s,first.dependency_guard,request(world),PageInput(limit=1,cursor=one.next_cursor),binding)
        assert one.items+two.items==compared.changes and two.next_cursor is None and one.total_count==two.total_count==2
    observe(client,monkeypatch,check,company)
    assert _storage(root,path)==saved


@pytest.mark.parametrize('damage',['missing','bypass','malformed'])
def test_source_unknown_history_never_issues_guard(root,client,world,monkeypatch,damage):
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    inp,ctx,company,path,receipt=world
    first=observe(client,monkeypatch,lambda s:prepared(s,world),company)
    with writer(root) as db:
        if damage=='missing':db.conn.execute(h.audit_entries.delete().where(h.audit_entries.c.record_type=='company',h.audit_entries.c.record_id==company))
        elif damage=='bypass':db.conn.execute(h.companies.update().where(h.companies.c.id==company).values(display_name='U2 bypassed name'))
        else:db.conn.execute(h.audit_entries.update().where(h.audit_entries.c.record_type=='company',h.audit_entries.c.record_id==company).values(after=__import__('bookflow.core.audit',fromlist=['encode_snapshot']).encode_snapshot({})))
    saved=_storage(root,path)
    def check(s):
        binding=OSBinding.from_session(s)
        recipe,facts=history.capture(s,request(world),binding)
        assert facts.issuer is None and facts.unknown==('issuer.display_name',)
        with pytest.raises(BookflowError) as error:history.issue(s,recipe,facts,binding)
        assert error.value.code=='E_PREVIEW_STALE' and error.value.details['history']=='unknown_history'
        with pytest.raises(BookflowError) as error:prepared(s,world)
        assert error.value.code=='E_PREVIEW_STALE'
        compared=history.compare(s,first.dependency_guard,request(world),binding)
        assert not compared.matches and compared.unknown_history
    observe(client,monkeypatch,check,company)
    assert _storage(root,path)==saved


def test_unrelated_hub_owners_do_not_change_source_guard(root,client,world,monkeypatch):
    from pathlib import Path
    from dataclasses import replace
    from tests.conftest import make_actor
    from tests.test_row7_credentials import writer
    from bookflow.hub import companies,schema as h
    from bookflow.core import audit
    inp,ctx,company,path,receipt=world
    client.run('company rename',dict(name='U2 stable name during move',move=False),company=company)
    first=observe(client,monkeypatch,lambda s:prepared(s,world),company)
    oldpath=client.company.show(company=company)['path']
    moved=client.run('company rename',dict(name='U2 stable name during move',move=True),company=company)
    assert moved['moved'] and moved['path']!=oldpath
    def unchanged():
        currentpath=Path(client.company.show(company=company)['path'])/'company.db'
        before=_storage(root,currentpath)
        result=observe(client,monkeypatch,lambda s:prepared(s,world),company)
        assert result.dependency_guard==first.dependency_guard
        assert result.facts_fingerprint==first.facts_fingerprint
        assert json.loads(result.readset_json)['issuer']==json.loads(first.readset_json)['issuer']
        assert _storage(root,currentpath)==before
    unchanged()
    seed=observe(client,monkeypatch,lambda s:s,company)
    with writer(root) as db:db.conn.execute(h.companies.update().where(h.companies.c.id==company).values(schema_revision='co0020'))
    unchanged()
    with writer(root) as db:
        session=replace(seed,hub=db)
        _,touch=companies.update(session,companies.get(session,company),'python',legal_name='Unrelated hub projection')
        audit.write_event(session,Context.new(Interface.python,'U2 projection only'),'projection repair','projection only',[touch])
    unchanged()
    make_actor(root,'U2 unrelated member',company_role=(company,'readonly'))
    unchanged()
    client.run('organization rename',dict(organization=seed.company_row['organization_id'],name='U2 unrelated organization',move=False))
    unchanged()


def test_pinned_name_survives_interleaving_but_guard_rejects_incoherent_anchor(root,client,world,monkeypatch):
    from bookflow.company import sales,sales_validation
    inp,ctx,company,path,receipt=world
    before=observe(client,monkeypatch,lambda s:prepared(s,world),company)
    def check(s):
        # Actual hub rename after read-only dispatch pinned this Session; the
        # ordinary producer must not reread a new value during validation.
        pinned=s.company_row['display_name']
        from dataclasses import replace
        from tests.test_row7_credentials import writer
        from bookflow.hub import companies
        from bookflow.core import audit
        with writer(root) as db:
            current=replace(s,hub=db)
            _,touch=companies.update(current,companies.get(current,company),'python',display_name='U2 after dispatch pin')
            audit.write_event(current,ctx,'company rename','U2 interleaving owned name change',[touch])
        saved=_storage(root,path)
        ordinary=sales.prepare(s,ctx,inp.source_action.input,'sales_receipt','update')
        assert ordinary.preview.revision.issuer_snapshot['display_name']==pinned
        sales_validation.validate(ordinary,s,ctx)
        recipe,facts=history.capture(s,request(world),OSBinding.from_session(s))
        # The existing read transaction can retain a coherent old hub snapshot.
        assert not facts.unknown and facts.issuer.display_name==pinned
        # End only this owned read snapshot to exercise a fresh anchor lookup
        # against the same dispatch pin; no writer/guard is disabled.
        s.hub.raw.rollback()
        recipe,facts=history.capture(s,request(world),OSBinding.from_session(s))
        assert facts.unknown==('issuer.display_name',) and facts.issuer is None
        with pytest.raises(BookflowError) as error:history.issue(s,recipe,facts,OSBinding.from_session(s))
        assert error.value.code=='E_PREVIEW_STALE'
        assert _storage(root,path)==saved
    observe(client,monkeypatch,check,company)
