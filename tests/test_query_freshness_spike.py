"""Private spike: real command sessions, unchanged live registration outside tests."""
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from bookflow.company import query_freshness as f, query_customer as customer, schema
from bookflow.company.query import QueryInput
from bookflow.core import registry
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan
from bookflow.storage.engine import open_database
from tests.test_bounded_queries import COMPANY, _db_path, _bulk_customers
from tests.conftest import as_user, make_actor


def install_spike(monkeypatch, metrics):
    """Test-only ordinary customer planner; A1 retains actual authority fences."""
    registry.load_all('customer query')
    command = registry.get('customer query')
    def plan(inp, ctx, s):
        value = customer.read_customer(s, inp)
        metrics.append(dict(phase='execution', rows=value.rows_hashed, partition=value.peak_partition,
                            scan_ms=value.scan_seconds * 1000, dependencies=value.dependency_rows,
                            limit=inp.limit, query=inp.query, projection=inp.projection,
                            encoded_bytes=value.encoded_bytes, prepare_ms=value.prepare_seconds * 1000))
        return Plan(value.page)

    monkeypatch.setattr(command, 'plan', plan)
    monkeypatch.setattr(command, 'output_model', customer.CustomerPage)


@pytest.fixture
def spike(monkeypatch):
    metrics = []
    install_spike(monkeypatch, metrics)
    return metrics


def clean(page):
    """Only for explicit legacy business-value parity, never paired-world equality."""
    result = dict(page)
    result.pop('query_contract_version'); result.pop('query_fingerprint'); result['next_cursor'] = None
    result['items'] = [{k: v for k, v in row.items() if k != 'projection_revision'} for row in result['items']]
    return result


def test_customer_parity_hierarchy_columns_and_complete_paging(client, monkeypatch, tmp_path):
    kind = client.run('customer-type create', {'name': 'Fresh Kind'}, company=COMPANY)
    parent = client.customer.create(name='Fresh Root', customer_type_id=kind['id'], contacts=[
        {'role': 'primary', 'display_name': 'Inherited Person', 'work_phone': '555-3434'}], company=COMPANY)
    leaf = parent
    for depth in range(4):
        leaf = client.customer.create(name=f'Fresh Child {depth}', parent_id=leaf['id'], company=COMPANY)
    columns = ['full_name', 'customer_type', 'email', 'created_at', 'current_balance']
    inputs = [dict(query='Fresh', limit=200), dict(query='Fresh', projection='reference', limit=200),
              dict(query='Fresh', columns=columns, sort='full_name', direction='desc', limit=200)]
    expected = [client.customer.query(**inp, company=COMPANY) for inp in inputs]
    metrics=[]; install_spike(monkeypatch, metrics)
    observed=[client.customer.query(**inp, company=COMPANY) for inp in inputs]
    assert [clean(p) for p in observed] == expected
    assert observed[0]['items'][-1]['customer_type'] == 'Fresh Kind'
    assert observed[0]['items'][-1]['primary_contact'] == 'Inherited Person'
    all_rows=[]; cursor=None
    while True:
        page=client.customer.query(query='Fresh', limit=2, cursor=cursor, company=COMPANY)
        all_rows.extend(page['items']); cursor=page['next_cursor']
        if cursor is None: break
    assert all_rows == observed[0]['items']
    (tmp_path/'parity.json').write_text(json.dumps(dict(expected=expected,observed=observed,paged=all_rows),indent=2))


def test_hidden_audit_unrelated_source_offpage_and_counts(client, spike, tmp_path):
    _bulk_customers(client, 5)
    args=dict(query='Workload 0',limit=2,columns=['full_name','phone'])
    first=client.customer.query(**args,company=COMPANY)
    reference_args=dict(query='Workload 0',projection='reference',limit=2)
    reference=client.customer.query(**reference_args,company=COMPANY)
    # Real unrelated business+audit write; deliberately does not touch customer.
    client.vendor.create(name='Not customer source',company=COMPANY)
    unchanged=client.customer.query(**args,company=COMPANY)
    assert unchanged == first
    second=client.customer.query(**args,cursor=first['next_cursor'],company=COMPANY)
    assert [x['label'] for x in second['items']]==['Workload 00002','Workload 00003']
    # Off-page relation/label update with no customer version or global audit help.
    with open_database(_db_path(client),writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        owner=db.conn.execute(sa.select(schema.customers.c.id).where(schema.customers.c.name=='Workload 00004')).scalar_one()
        db.conn.execute(schema.customer_contacts.update().where(schema.customer_contacts.c.customer_id==owner).values(work_phone='555-9999'))
        db.raw.execute('COMMIT')
    with pytest.raises(BookflowError) as caught:
        client.customer.query(**args,cursor=first['next_cursor'],company=COMPANY)
    assert caught.value.code=='E_QUERY_STALE'
    assert caught.value.details=={'restart':'Repeat the query without cursor.'}
    with pytest.raises(BookflowError) as reference_stale:
        client.customer.query(**reference_args,cursor=reference['next_cursor'],company=COMPANY)
    assert reference_stale.value.code=='E_QUERY_STALE'
    current=client.customer.query(**args,company=COMPANY)
    assert current['matching_total']==5
    assert current['items']==first['items']
    assert current['query_fingerprint']!=first['query_fingerprint']
    client.customer.create(name='Workload 00005',company=COMPANY)
    with pytest.raises(BookflowError) as inserted:
        client.customer.query(**args,cursor=current['next_cursor'],company=COMPANY)
    assert inserted.value.code=='E_QUERY_STALE'
    after_insert=client.customer.query(**args,company=COMPANY)
    assert after_insert['matching_total']==6
    (tmp_path/'paired.json').write_text(json.dumps(dict(before=first,hidden_world=unchanged,after_visible_offpage=current,reference_before=reference,after_insert=after_insert),indent=2))


def test_current_role_actor_binding_and_tax_mask(client, root, monkeypatch, tmp_path):
    cid=client.company.show(company=COMPANY)['company_id']
    make_actor(root,'spike-reader',company_role=(cid,'readonly'))
    make_actor(root,'spike-other',company_role=(cid,'readonly'))
    reader=as_user(root,'spike-reader'); other=as_user(root,'spike-other')
    metrics=[]; install_spike(monkeypatch,metrics)
    one=reader.customer.query(limit=1,company=COMPANY)
    hidden=client.company.new(legal_name='Unrelated hidden books',home_currency='USD',timezone='UTC')
    client.customer.create(name='Hidden customer',company=hidden['company_id'])
    assert reader.customer.query(limit=1,company=COMPANY)==one
    with pytest.raises(BookflowError) as hidden_error:
        reader.customer.query(company=hidden['company_id'])
    assert hidden_error.value.code=='E_COMPANY_NOT_FOUND'
    with pytest.raises(BookflowError) as caught:
        other.customer.query(limit=1,cursor=one['next_cursor'],company=COMPANY)
    assert caught.value.code=='E_VALIDATION'
    # Capture an actual readonly command session; test generic projection mask
    # against the real party owner (not a supplied test-audience boolean).
    cmd=registry.get('customer query'); old=cmd.plan; values=[]
    def capture(inp,ctx,s):
        mask=f.role_mask(s,'employee')
        assert mask.partial
        a=dict(id='employee-example',version=1,label='Visible',active=True,tax_id_last4='1234',updated_at='first')
        b=dict(a,version=2,tax_id_last4='9999',updated_at='later')
        values.extend([f.project_record(b'k'*32,'reader','employee',row,mask) for row in (a,b)])
        return old(inp,ctx,s)
    monkeypatch.setattr(cmd,'plan',capture)
    reader.customer.query(limit=1,company=COMPANY)
    assert values[0]==values[1]
    assert values[0]['version'] is None and values[0]['updated_at'] is None and values[0]['tax_id_last4'] is None
    (tmp_path/'role-mask.json').write_text(json.dumps(values,indent=2))


def test_complete_customer_columns_custom_values_and_sorts(client, monkeypatch, tmp_path):
    from bookflow.company.query_catalog import builtins
    from tests.test_bounded_queries import test_all_query_declared_sort_and_search_matches_legacy_selection
    definitions={}
    for kind in ('number','bool','text'):
        definitions[kind]=client.run('custom-field create',dict(name='Fresh '+kind,kind=kind,scopes=['customer']),company=COMPANY)['id']
    row=client.customer.create(name='Fresh typed',custom_fields={definitions['number']:'0',definitions['bool']:False,definitions['text']:''},company=COMPANY)
    keys=list(builtins('customer'))
    inputs=[dict(columns=keys[start:start+64],ids=[row['id']]) for start in range(0,len(keys),64)]
    custom=dict(columns=['custom:'+id for id in definitions.values()],ids=[row['id']])
    inputs.append(custom)
    expected=[client.customer.query(**inp,company=COMPANY) for inp in inputs]
    metrics=[];install_spike(monkeypatch,metrics)
    observed=[client.customer.query(**inp,company=COMPANY) for inp in inputs]
    assert [clean(page) for page in observed]==expected
    assert observed[-1]['items'][0]['values']=={'custom:'+definitions['number']:'0','custom:'+definitions['bool']:False,'custom:'+definitions['text']:''}
    criterion=dict(definition=definitions['number'],kind='number',operator='eq',value='0')
    filtered=client.customer.query(**custom,custom_filters=[criterion],company=COMPANY)
    assert filtered['matching_total']==1 and filtered['items'][0]['id']==row['id']
    test_all_query_declared_sort_and_search_matches_legacy_selection(client,'customer')
    (tmp_path/'all-columns.json').write_text(json.dumps(dict(expected=expected,observed=observed,filtered=filtered),indent=2))


def test_key_errors_and_internal_driver_failure(client, spike, monkeypatch):
    with open_database(_db_path(client),writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');db.raw.execute('DELETE FROM report_cursor_keys');db.raw.execute('COMMIT')
    with pytest.raises(BookflowError) as caught: client.customer.query(company=COMPANY)
    assert caught.value.code=='E_INTERNAL'
    with open_database(_db_path(client),writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');db.raw.execute('INSERT INTO report_cursor_keys VALUES (1,?)',(b'k'*32,));db.raw.execute('COMMIT')
    original=sa.engine.Connection.execute
    def fail(self,statement,*args,**kwargs):
        if 'FROM customers' in str(statement) and '__order_' in str(statement):
            raise sa.exc.OperationalError('private driver failure',{},RuntimeError('hidden raw'))
        return original(self,statement,*args,**kwargs)
    monkeypatch.setattr(sa.engine.Connection,'execute',fail)
    with pytest.raises(BookflowError) as caught: client.customer.query(company=COMPANY)
    assert caught.value.code=='E_IO' and caught.value.details=={'reason':'query_facts_unavailable'}


def test_typed_domains_and_cursor_errors():
    key=b'k'*32
    assert len({f.canonical(v) for v in (None,False,0,'0',b'0',[],{})})==7
    assert f.keyed(key,f.FACTS,'same') != f.keyed(key,f.RECORD,'same')
    proof=f.QueryProof(company='C',contract='query',audience='A',facts='facts')
    cursor=f.continuation(key,proof,0,1,2)
    assert f.decode_cursor(key,cursor).offset==1
    for bad in ('eyJ2IjoxfQ',cursor+'x','!'*2049):
        with pytest.raises(BookflowError) as caught: f.decode_cursor(key,bad)
        assert caught.value.code=='E_VALIDATION'
    with pytest.raises(BookflowError): f.canonical(1.5)


def test_flat_scalar_encoding_exact_bytes_and_rejections():
    cells = (None, False, True, 0, -17, 'é:0', b'\x00:')
    expected = b'l7: nfti1:0i3:-17s4:\xc3\xa9:0b2:\x00:'.replace(b' ', b'')
    assert f.flat_encoder(7)(cells) == expected == f.canonical(cells)
    for bad in ((1.0,), ({'id': 'x'},), ([],)):
        with pytest.raises(BookflowError): f.flat_encoder(1)(bad)
    with pytest.raises(BookflowError): f.flat_encoder(2)((1,))


def test_ordinary_execution_snapshot_and_fresh_release_authority(client, root, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    from bookflow.adapters.http.execution import PublishedDocument
    from bookflow.hub import schema as hub
    _bulk_customers(client, 5)
    metrics=[]; install_spike(monkeypatch, metrics)
    expected=client.customer.query(limit=2,company=COMPANY)
    cid=client.company.show(company=COMPANY)['company_id']
    token=client.token.issue(label='snapshot-fence')
    company_path=_db_path(client)
    handle=start_serving(root,client_version(),bind='127.0.0.1:8765',secure_cookies=False,publish_descriptor=False)
    api=TestClient(handle.app)
    original=PublishedDocument.check
    from contextlib import contextmanager
    from bookflow.core import publication
    original_reader=publication.publication_reader
    readers=[]
    @contextmanager
    def fresh_reader(*args, **kwargs):
        with original_reader(*args, **kwargs) as session:
            raw=session.hub.raw
            assert all(session is not prior and raw is not connection for prior,connection in readers)
            readers.append((session,raw))
            yield session
    monkeypatch.setattr(publication,'publication_reader',fresh_reader)
    mode='business'; calls=0
    def change_after_execution(self, **kwargs):
        nonlocal calls
        original(self, **kwargs)
        calls+=1
        if calls!=1:return
        if mode=='business':
            with open_database(company_path,writable=True) as db:
                db.raw.execute('BEGIN IMMEDIATE')
                db.conn.execute(schema.customers.update().where(schema.customers.c.name=='Workload 00004').values(version=schema.customers.c.version+1))
                db.raw.execute('COMMIT')
        else:
            with open_database(root/'hub.db',writable=True) as db:
                db.raw.execute('BEGIN IMMEDIATE')
                db.conn.execute(hub.api_tokens.update().where(hub.api_tokens.c.id==token['token_id']).values(revoked_at='2026-09-07T00:00:00Z'))
                db.raw.execute('COMMIT')
    monkeypatch.setattr(PublishedDocument,'check',change_after_execution)
    def run(payload):
        return api.post(f'/companies/{cid}/commands/customer.query',json=payload,
                        headers={'Authorization':'Bearer '+token['secret']})
    try:
        before=len(metrics)
        response=run({'limit':2})
        assert response.status_code==200 and response.json()==expected, response.text
        assert len(metrics)==before+1  # complete proof once, no relation read in authority fences
        assert len(readers)==3  # execution-return, header, single body: fresh hub connections
        stale=run({'limit':2,'cursor':expected['next_cursor']})
        assert stale.status_code!=200 and stale.json()['code']=='E_QUERY_STALE'
        mode='authority';calls=0
        denied=run({'limit':2})
        assert denied.status_code==401
        assert denied.json()=={'code':'E_UNAUTHENTICATED','message':'No valid credential: log in, or send a bearer token.',
                              'details':{'stage':'publication','outcome':'unknown'}}
        (tmp_path/'ordinary-snapshot.json').write_text(json.dumps({'expected':expected,'snapshot':response.json(),
            'stale':stale.json(),'denied':denied.json(),'reads':metrics,'fresh_readers':len(readers)},indent=2))
    finally:handle.stop()


def test_shared_matching_statement_and_midstream_failure(client, spike, monkeypatch, tmp_path):
    _bulk_customers(client, 5)
    statements=[]
    def capture(conn,cursor,sql,params,context,many):
        if 'query_customer_matching' in sql: statements.append(sql)
    sa.event.listen(sa.engine.Engine,'before_cursor_execute',capture)
    try:
        page=client.customer.query(query='Workload 0',company=COMPANY)
    finally:sa.event.remove(sa.engine.Engine,'before_cursor_execute',capture)
    assert page['count']==5
    assert len(statements)==1 and statements[0].count('query_customer_matching AS MATERIALIZED')==1
    assert 'UNION ALL' in statements[0]
    (tmp_path/'shared-matching.sql').write_text(statements[0])
    original=sa.engine.Result.partitions
    def broken(self,*args,**kwargs):
        for partition in original(self,*args,**kwargs):
            yield partition
            raise sa.exc.OperationalError('midstream',{},RuntimeError('private values'))
    monkeypatch.setattr(sa.engine.Result,'partitions',broken)
    with pytest.raises(BookflowError) as caught:client.customer.query(query='Workload 0',company=COMPANY)
    assert caught.value.code=='E_IO' and caught.value.details=={'reason':'query_facts_unavailable'}
