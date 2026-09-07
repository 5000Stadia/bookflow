"""10k opt-in custom query cost and exact-value witnesses; original budget retained."""
import json
import statistics
import time
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from bookflow.company import schema
from bookflow.core.ids import new_id
from bookflow.core.context import client_version
from bookflow.commands.host_cmds import start_serving
from bookflow.storage.engine import open_database
from tests.test_bounded_queries import _bulk_customers, _db_path, COMPANY


def _seed_custom_workload(client):
    definitions = {}
    for kind in ('number', 'bool', 'text'):
        definitions[kind] = client.run('custom-field create', {'name':'10k ' + kind, 'kind':kind, 'scopes':['customer']}, company=COMPANY)['id']
    with open_database(_db_path(client), writable=True) as db:
        owners = db.conn.execute(sa.select(schema.customers.c.id, schema.customers.c.name).where(schema.customers.c.name.like('Workload %')).order_by(schema.customers.c.name)).all()
        assert len(owners) == 10_000
        rows=[]
        for index, (id, name) in enumerate(owners):
            for kind, value in {'number':f'{index}.000000001','bool':'true' if index % 2 else 'false','text':f'Band {index % 10}'}.items():
                rows.append({'id':new_id(),'def_id':definitions[kind],'record_type':'customer','record_id':id,'active':True,'canonical_text':value})
        db.raw.execute('BEGIN IMMEDIATE'); db.conn.execute(schema.custom_field_values.insert(),rows); db.raw.execute('COMMIT')
    return definitions


@pytest.mark.timeout(180)
def test_ten_thousand_custom_projections_filters_and_data_volume(client, root, monkeypatch):
    _bulk_customers(client, 10_000)
    definitions = _seed_custom_workload(client)
    cid=client.company.show(company=COMPANY)['company_id']
    secret=client.token.issue(label='custom-query-budget')['secret']
    handle=start_serving(root,client_version(),bind='127.0.0.1:8765',secure_cookies=False,publish_descriptor=False)
    from tests.query_phase_trace import QueryTrace, assert_bounded
    trace=QueryTrace(monkeypatch)
    api=TestClient(trace.app(handle.app))
    keys=['full_name','email']+['custom:'+id for id in definitions.values()]
    def criterion(kind,operator,value): return {'definition':definitions[kind],'kind':kind,'operator':operator,'value':value}
    def run(payload):
        response=api.post(f'/companies/{cid}/commands/customer.query',json=payload,headers={'Authorization':f'Bearer {secret}'})
        assert response.status_code==200,response.text
        return response.json(),len(response.content)
    cases={
        'selected_50':{'columns':keys},
        'selected_200':{'columns':keys,'limit':200},
        'number_eq':{'columns':keys,'custom_filters':[criterion('number','eq','9007.000000001')]},
        'number_range':{'columns':keys,'custom_filters':[criterion('number','gte','9900.000000001')]},
        'false_and_text':{'columns':keys,'custom_filters':[criterion('bool','eq',False),criterion('text','contains','Band 2')]},
        'numeric_miss':{'columns':keys,'custom_filters':[criterion('number','lt','0')]},
    }
    timings, sizes, sql_counts = {},{},{}
    traces={}
    statements=[]
    def capture(*args): statements.append(args[2])
    try:
        for name,payload in cases.items():
            run(payload)
            elapsed=[]
            for _ in range(3):
                started=time.perf_counter();page,size=run(payload);elapsed.append((time.perf_counter()-started)*1000)
            timings[name]=round(statistics.median(elapsed),2);sizes[name]=size
            _, traces[name] = trace.run(lambda: run(payload))
            sql_counts[name]=len(traces[name]['raw_sql'])
            assert page['count']<=payload.get('limit',50)
            if name=='number_eq':
                assert page['matching_total']==1 and page['items'][0]['values']['custom:'+definitions['number']]=='9007.000000001'
            if name=='number_range': assert page['matching_total']==100
            if name=='false_and_text': assert page['matching_total']==1000
            if name=='numeric_miss': assert page['matching_total']==0
        print('10k custom milliseconds:',json.dumps(timings,sort_keys=True),'response bytes:',json.dumps(sizes,sort_keys=True),'SQL counts:',json.dumps(sql_counts,sort_keys=True))
        print('custom phase receipts:',json.dumps(traces))
        assert_bounded(traces['selected_50'],traces['selected_200'])
        assert sizes['selected_200'] < 200_000
        assert all(value<100 for value in timings.values()),timings
    finally:
        if sa.event.contains(sa.engine.Engine,'before_cursor_execute',capture):sa.event.remove(sa.engine.Engine,'before_cursor_execute',capture)
        handle.stop()
