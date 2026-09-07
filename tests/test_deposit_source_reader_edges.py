"""Independent source selector and nested-history failure oracles."""
import copy
import json
import pytest
from bookflow import BookflowError
from bookflow.company import deposit_coordination as coordinator,deposit_dependency_history as history
from bookflow.core.publication import OSBinding
from tests.test_deposit_source_issuer import world,prepared,request,sale,driver,COMPANY
from tests.test_deposit_dependency_binding import observe,_storage


def test_source_alias_rename_and_unread_default_have_distinct_guards(root,client,world,monkeypatch,sale):
    inp,ctx,company,path,receipt=world
    payload=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
    payload['source_action']['input']['refresh_defaults']=False
    payload['source_action']['input']['lines']=[dict(item='Sale witness service',line_id=receipt['revision']['lines'][0]['line_id'],net_amount='2',description='Explicit retained description')]
    inp=type(inp).model_validate_json(json.dumps(payload));world=(inp,ctx,company,path,receipt)
    first=observe(client,monkeypatch,lambda s:prepared(s,world),company)
    client.run('item update',dict(item=sale['item'],expected_version=1,description='Unselected default description'),company=company)
    saved=_storage(root,path)
    def unchanged(s):
        compared=history.compare(s,first.dependency_guard,request(world),OSBinding.from_session(s))
        assert compared.matches and not compared.unknown_history
        again=prepared(s,world)
        assert again.dependency_guard==first.dependency_guard and again.facts_fingerprint==first.facts_fingerprint
    observe(client,monkeypatch,unchanged,company)
    assert _storage(root,path)==saved
    client.run('item update',dict(item=sale['item'],expected_version=2,name='Renamed source item'),company=company)
    saved=_storage(root,path)
    def stale(s):
        compared=history.compare(s,first.dependency_guard,request(world),OSBinding.from_session(s))
        assert not compared.matches and not compared.unknown_history
        assert any(row.kind=='source_item' and row.record_id==sale['item'] and row.fields==('full_name',) for row in compared.changes)
        with pytest.raises(BookflowError) as error:
            coordinator.prepare(s,ctx,inp.model_copy(update={'dependency_guard':first.dependency_guard}),binding=OSBinding.from_session(s))
        assert error.value.code=='E_PREVIEW_STALE'
    observe(client,monkeypatch,stale,company)
    assert _storage(root,path)==saved


@pytest.mark.parametrize('kind,field,child,changes',[
 ('source_price_level','items',dict(id='01AAAAAAAAAAAAAAAAAAAAAAAA',position=0,active=True,item_id='01BBBBBBBBBBBBBBBBBBBBBBBB',price={'amount':'1.00','currency':'USD','minor_units':100},percent=None,adjustment_basis='standard_price'),[{'position':True},{'price':{'amount':'2.00','currency':'USD','minor_units':100}},{'adjustment_basis':'unknown'}]),
 ('source_item','members',dict(id='01AAAAAAAAAAAAAAAAAAAAAAAA',position=0,active=True,component_item_id='01BBBBBBBBBBBBBBBBBBBBBBBB',quantity='1',unit_id=None),[{'active':1},{'quantity':'nan'},{'unit_id':'not-an-id'}]),
 ('source_unit','units',dict(id='01AAAAAAAAAAAAAAAAAAAAAAAA',position=0,active=True,name='Each',abbreviation='ea',is_base=True,base_factor='1'),[{'position':'0'},{'active':1},{'base_factor':1}]),
])
def test_nested_logical_history_has_strict_complete_types(kind,field,child,changes):
    value={'id':'01CCCCCCCCCCCCCCCCCCCCCCCC',field:[child]}
    history._source_image_types(kind,value,(field,))
    for change in changes:
        broken=copy.deepcopy(value);broken[field][0].update(change)
        with pytest.raises(history.MissingHistory):history._source_image_types(kind,broken,(field,))
    broken=copy.deepcopy(value);broken[field].append(copy.deepcopy(child))
    with pytest.raises(history.MissingHistory):history._source_image_types(kind,broken,(field,))


def test_actual_customer_children_refresh_and_corrupt_owned_projection(root,client,world,monkeypatch,sale):
    from bookflow.company import schema as c
    import sqlalchemy as sa
    inp,ctx,company,path,receipt=world
    customer=client.customer.update(customer=sale['customer'],expected_version=1,contacts=[dict(role='primary',first_name='Source contact',points=[dict(kind='main_phone',value='555-0100')])],shipping_addresses=[dict(label='Source dock',is_default=True,line1='1 Owned Dock')],company=COMPANY)
    saved=_storage(root,path)
    def check(s):
        result=prepared(s,world)
        coordinator.validate(s,ctx,result)
        facts=json.loads(result.readset_json)
        captured=next(json.loads(row['semantic_json']) for row in facts['records'] if row['kind']=='source_customer' and row['id']==sale['customer'])
        assert captured['contacts'][0]['first_name']=='Source contact'
        assert captured['contacts'][0]['points'][0]['value']=='555-0100'
        assert captured['shipping_addresses'][0]['address_line1']=='1 Owned Dock'
        raw=dict(s.company.conn.execute(sa.select(c.customers).where(c.customers.c.id==sale['customer'])).mappings().one())
        image=json.loads(history.canonical(history._source_owner_image(s,'source_customer',raw)))
        for field in ('contacts','shipping_addresses'):
            history._project('source_customer',image,('id',field))
            damaged=copy.deepcopy(image);damaged[field][0]['customer_id']='01AAAAAAAAAAAAAAAAAAAAAAAA'
            with pytest.raises(history.MissingHistory):history._project('source_customer',damaged,('id',field))
        damaged=copy.deepcopy(image);damaged['contacts'][0]['points'][0]['active']=1
        with pytest.raises(history.MissingHistory):history._project('source_customer',damaged,('id','contacts'))
    observe(client,monkeypatch,check,company)
    assert _storage(root,path)==saved


def test_all_source_custom_kinds_preserve_full_plan_and_exact_pending_values(root,client,world,monkeypatch):
    from bookflow.company import sales
    from bookflow.company.deposit_coordination import canonical_source_data
    inp,ctx,company,path,receipt=world
    values={'text':'01AAAAAAAAAAAAAAAAAAAAAAAA','number':'123456789.123456789','date':'2026-06-01','bool':False,'choice':'Web'}
    fields={kind:client.run('custom-field create',dict(name='B source '+kind,kind=kind,scopes=['sales_receipt'],**({'choices':[{'value':'Web'},{'value':'Phone'}]} if kind=='choice' else {})),company=company) for kind in values}
    payload=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
    payload['source_action']['input']['custom_fields']={fields[k]['id']:v for k,v in values.items()}
    payload['source_action']['input']['custom_field_kinds']={fields[k]['id']:k for k in values}
    inp=type(inp).model_validate_json(json.dumps(payload));world=(inp,ctx,company,path,receipt)
    saved=_storage(root,path)
    def check(s):
        result=prepared(s,world);source=result.resolution.source
        ordinary=sales.prepare(s,ctx,inp.source_action.input,'sales_receipt','update',provenance=source.provenance)
        assert canonical_source_data(ordinary,payment=False)==canonical_source_data(source.plan,payment=False)
        snapshots=json.loads(source.plan.data['pending']['transaction_revisions'][0]['custom_fields_snapshot'])
        assert {k:snapshots[f['id']]['value'] for k,f in fields.items()}==values
        assert {m.definition_id for m in source.plan.data['custom_plan'].owner_plan.mutations}=={f['id'] for f in fields.values()}
        assert all(m.operation=='insert' for m in source.plan.data['custom_plan'].owner_plan.mutations)
        coordinator.validate(s,ctx,result)
        bad=payload.copy();bad=json.loads(json.dumps(payload));bad['source_action']['input']['custom_field_kinds'][fields['text']['id']]='date'
        with pytest.raises(BookflowError):coordinator.prepare(s,ctx,type(inp).model_validate_json(json.dumps(bad)),binding=OSBinding.from_session(s))
    observe(client,monkeypatch,check,company)
    assert _storage(root,path)==saved
