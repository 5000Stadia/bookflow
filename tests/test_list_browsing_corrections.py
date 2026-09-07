"""Family witnesses for owner projections, viewer times and bounded ID reads."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
from bookflow.company.lists import LIST_DEFINITIONS
from bookflow.core import registry
from tests.conftest import make_actor, as_user

C='Demo Plumbing Co'

@pytest.mark.parametrize('noun,field,other',[('customer','linked_vendor_id','vendor'),('vendor','linked_customer_id','customer')])
def test_links_follow_each_owner_across_search_and_pages(client,noun,field,other):
    pairs=[]
    for i in range(4):
        customer=client.customer.create(name=f'Correlation customer {i}',company=C)
        vendor=client.vendor.create(name=f'Correlation vendor {i}',company=C)
        if i%2==0:
            client.run('customer link-vendor',{'customer':customer['id'],'vendor':vendor['id'],
                'expected_customer_version':1,'expected_vendor_version':1},company=C)
        pairs.append((customer,vendor))
    owners=[pair[0 if noun=='customer' else 1] for pair in pairs]
    expected={owner['id']:pairs[i][1 if noun=='customer' else 0]['id'] if i%2==0 else None for i,owner in enumerate(owners)}
    for limit in (1,2,50):
        actual={};cursor=None
        while True:
            page=client.run(noun+' query',{'query':'Correlation '+noun,'columns':[field],'limit':limit,'cursor':cursor},company=C)
            assert page['matching_total']==4
            for row in page['items']:
                value=row['values'][field];actual[row['id']]=value['id'] if value else None
            cursor=page['next_cursor']
            if not cursor:break
        assert actual==expected
    for owner in owners:
        rows=client.run(noun+' query',{'query':owner['name'],'columns':[field]},company=C)['items']
        assert len(rows)==1
        value=rows[0]['values'][field]
        assert (value['id'] if value else None)==expected[owner['id']]

@pytest.fixture(scope='module')
def dated_company(tmp_path_factory):
    import bookflow
    from bookflow.core import clock
    root=tmp_path_factory.mktemp('dated-company')/'root'
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('BOOKFLOW_DATA_ROOT',str(root))
        patch.setattr(clock,'now',lambda:datetime(2026,9,7,0,15,tzinfo=timezone.utc))
        client=bookflow.connect(data_root=str(root));client.init();client.demo.reset()
    info=client.company.show(company=C)
    make_actor(root,'time-reader',company_role=(info['company_id'],'readonly'))
    make_actor(root,'company-time-reader',company_role=(info['company_id'],'readonly'))
    from bookflow.hub import schema as hub
    from bookflow.storage.engine import open_database
    with open_database(root/'hub.db',writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        db.conn.execute(hub.users.update().where(hub.users.c.username=='time-reader').values(timezone='Pacific/Honolulu'))
        db.raw.execute('COMMIT')
    return as_user(root,'company-time-reader'),as_user(root,'time-reader'),info['info']['timezone']

@pytest.mark.parametrize('noun',LIST_DEFINITIONS)
def test_all_master_selected_timestamps_use_viewer_zone(dated_company,noun):
    owner,reader,company_zone=dated_company
    for client,zone in ((owner,company_zone),(reader,'Pacific/Honolulu')):
        page=client.run(noun+' query',{'columns':['created_at','updated_at'],'limit':200,'include_inactive':True},company=C)
        assert page['items'] and page['next_cursor'] is None
        cmd=registry.get(noun+' show')
        for row in page['items']:
            shown=client.run(noun+' show',{cmd.positional[0]:row['id']},company=C)
            for field in ('created_at','updated_at'):
                expected=datetime(2026,9,7,0,15,tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).isoformat(timespec='milliseconds')
                assert row['values'][field]==shown[field]==expected
        if zone=='Pacific/Honolulu':assert expected.startswith('2026-09-06T14:15:')

def test_id_selection_is_bounded_intersected_and_cursor_bound(client):
    from bookflow import BookflowError
    rows=[client.vendor.create(name=f'ID subset {i}',company=C) for i in range(3)]
    ids=[r['id'] for r in rows]
    first=client.vendor.query(ids=ids,projection='reference',limit=1,company=C)
    assert set(first)=={'projection','items','count','next_cursor'}
    assert first['items'][0]['id']==ids[0]
    second=client.vendor.query(ids=ids,projection='reference',limit=1,cursor=first['next_cursor'],company=C)
    assert second['items'][0]['id']==ids[1]
    assert client.vendor.query(ids=ids,query='ID subset 2',company=C)['items'][0]['id']==ids[2]
    with pytest.raises(BookflowError):client.vendor.query(ids=ids[1:],projection='reference',limit=1,cursor=first['next_cursor'],company=C)
    for invalid in ([],ids*22):
        with pytest.raises(BookflowError):client.vendor.query(ids=invalid,company=C)


def test_link_projection_discards_former_pair_after_relink(client):
    customer=client.customer.create(name='Relink projection customer',company=C)
    first=client.vendor.create(name='Relink first vendor',company=C)
    second=client.vendor.create(name='Relink second vendor',company=C)
    client.run('customer link-vendor',{'customer':customer['id'],'vendor':first['id'],
        'expected_customer_version':1,'expected_vendor_version':1},company=C)
    client.run('customer unlink-vendor',{'customer':customer['id'],'expected_customer_version':2,
        'expected_vendor_version':2,'expected_link_version':1},company=C)
    client.run('customer link-vendor',{'customer':customer['id'],'vendor':second['id'],
        'expected_customer_version':3,'expected_vendor_version':1},company=C)
    page=client.customer.query(ids=[customer['id']],columns=['linked_vendor_id'],company=C)
    assert page['items'][0]['values']['linked_vendor_id']['id']==second['id']
    page=client.vendor.query(ids=[first['id'],second['id']],columns=['linked_customer_id'],company=C)
    byid={r['id']:r['values']['linked_customer_id'] for r in page['items']}
    assert byid[first['id']] is None and byid[second['id']]['id']==customer['id']


def test_ids_intersect_custom_predicates_and_totals(client):
    definition=client.run('custom-field create',{'name':'ID intersection flag','kind':'bool','scopes':['vendor']},company=C)
    rows=[client.vendor.create(name=f'ID intersection {i}',custom_fields={definition['id']:flag},company=C) for i,flag in enumerate((False,True))]
    criterion={'definition':definition['id'],'kind':'bool','operator':'eq','value':True}
    for i in range(2):
        page=client.vendor.query(ids=[rows[i]['id']],columns=['name'],custom_filters=[criterion],company=C)
        assert page['count']==page['matching_total']==i
        assert [r['id'] for r in page['items']]==([rows[1]['id']] if i else [])


from tests.test_row3_host import hosted


def test_id_reference_read_http_cli_library_parity(hosted,client,cli):
    vendor=hosted.ok('vendor.create',{'name':'ID surface parity'},company=hosted.company_id)
    payload={'ids':[vendor['id']],'projection':'reference','include_inactive':True,'limit':64}
    observed=hosted.ok('vendor.query',payload,company=hosted.company_id)
    hosted.handle.stop()
    assert client.vendor.query(**payload,company=C)==observed
    import json
    assert cli.json('vendor','query','--ids',json.dumps(payload['ids']),'--projection','reference','--include-inactive','--limit','64','--company',C)==observed
