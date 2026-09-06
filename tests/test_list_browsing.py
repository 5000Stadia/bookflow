"""Behavioral witnesses for opt-in master projections and discovery."""
import pytest
from bookflow.company.lists import LIST_DEFINITIONS
from bookflow.company.query_catalog import builtins

COMPANY = 'Demo Plumbing Co'


@pytest.mark.parametrize('noun', LIST_DEFINITIONS)
def test_every_declared_projection_is_reachable(client, noun):
    catalog = builtins(noun)
    metadata = client.run(f'{noun} query options', {'limit': 200}, company=COMPANY)
    assert set(catalog) <= {item['key'] for item in metadata['items']}
    keys = list(catalog)
    for start in range(0, len(keys), 64):
        selected = keys[start:start + 64]
        page = client.run(f'{noun} query', {'columns': selected, 'limit': 2, 'include_inactive': True}, company=COMPANY)
        assert page['matching_total'] >= page['count']
        assert [item['key'] for item in page['columns']] == selected
        for row in page['items']:
            assert set(row['values']) == set(selected)
            assert row['id'] and row['version'] >= 1
    old = client.run(f'{noun} query', {}, company=COMPANY)
    assert set(old) == {'projection', 'items', 'count', 'next_cursor'}


def test_typed_filters_presence_identity_and_retained_values(client):
    from bookflow import BookflowError
    defs = {}
    for kind in ('text', 'number', 'date', 'bool', 'choice'):
        data = {'name': 'Browse ' + kind, 'kind': kind, 'scopes': ['customer']}
        if kind == 'choice':
            data['choices'] = [{'value': 'Gold'}, {'value': 'Silver'}]
        defs[kind] = client.run('custom-field create', data, company=COMPANY)
    values = {'text': '', 'number': '0', 'date': '2026-09-06', 'bool': False, 'choice': 'Gold'}
    owner = client.customer.create(name='Browse populated', custom_fields={defs[k]['id']: v for k, v in values.items()}, company=COMPANY)
    empty = client.customer.create(name='Browse missing', company=COMPANY)
    keys = ['full_name'] + ['custom:' + d['id'] for d in defs.values()]
    page = client.customer.query(query='Browse', columns=keys, company=COMPANY)
    by_id = {row['id']: row['values'] for row in page['items']}
    assert by_id[owner['id']]['custom:' + defs['bool']['id']] is False
    assert by_id[owner['id']]['custom:' + defs['number']['id']] == '0'
    assert by_id[owner['id']]['custom:' + defs['text']['id']] == ''
    assert all(by_id[empty['id']][key] is None for key in keys[1:])
    for kind in values:
        value = values[kind]
        if kind == 'choice':
            value = next(c['id'] for c in defs[kind]['choices'] if c['value'] == 'Gold')
        criterion = {'definition': defs[kind]['id'], 'kind': kind, 'operator': 'eq', 'value': value}
        result = client.customer.query(query='Browse', custom_filters=[criterion], company=COMPANY)
        assert [r['id'] for r in result['items']] == [owner['id']]
        criterion['operator'] = 'ne'
        assert client.customer.query(query='Browse', custom_filters=[criterion], company=COMPANY)['count'] == 0
        missing = {'definition': defs[kind]['id'], 'kind': 'presence', 'operator': 'is_missing'}
        assert [r['id'] for r in client.customer.query(query='Browse', custom_filters=[missing], company=COMPANY)['items']] == [empty['id']]
    client.run('custom-field update', {'custom_field': defs['number']['id'], 'expected_version': defs['number']['version'], 'active': False}, company=COMPANY)
    result = client.customer.query(columns=['custom:' + defs['number']['id']], custom_filters=[
        {'definition': defs['number']['id'], 'kind': 'number', 'operator': 'lte', 'value': '0'}], company=COMPANY)
    assert [r['id'] for r in result['items']] == [owner['id']]
    with pytest.raises(BookflowError):
        client.vendor.query(columns=['custom:' + defs['number']['id']], company=COMPANY)


def test_retired_choice_label_reuse_is_not_identity(client):
    d = client.run('custom-field create', {'name': 'Reused label', 'kind': 'choice', 'scopes': ['customer'],
        'choices': [{'value': 'Gold'}, {'value': 'Silver'}]}, company=COMPANY)
    retired = next(choice for choice in d['choices'] if choice['value'] == 'Gold')
    choices = [{**choice, 'active': False} if choice['id'] == retired['id'] else choice for choice in d['choices']]
    choices = [{k: v for k, v in choice.items() if k in ('id', 'value', 'active')} for choice in choices]
    choices.append({'value': 'Gold'})
    d = client.run('custom-field update', {'custom_field': d['id'], 'expected_version': d['version'], 'choices': choices}, company=COMPANY)
    active = next(choice for choice in d['choices'] if choice['value'] == 'Gold' and choice['active'])
    owner = client.customer.create(name='Reused choice owner', custom_fields={d['id']: 'Gold'}, company=COMPANY)
    for choice, expected in ((retired, []), (active, [owner['id']])):
        result = client.customer.query(custom_filters=[{'definition': d['id'], 'kind': 'choice', 'operator': 'eq', 'value': choice['id']}], company=COMPANY)
        assert [r['id'] for r in result['items']] == expected


def test_choice_discovery_pages_and_stale_snapshot(client):
    from bookflow import BookflowError
    d = client.run('custom-field create', {'name': 'Large choice discovery', 'kind': 'choice', 'scopes': ['customer'],
        'choices': [{'value': f'Choice {i:03d}'} for i in range(403)]}, company=COMPANY)
    first = client.run('customer query options', {'kind': 'choices', 'definition': d['id'], 'limit': 200}, company=COMPANY)
    second = client.run('customer query options', {'kind': 'choices', 'definition': d['id'], 'limit': 200, 'cursor': first['next_cursor']}, company=COMPANY)
    third = client.run('customer query options', {'kind': 'choices', 'definition': d['id'], 'limit': 200, 'cursor': second['next_cursor']}, company=COMPANY)
    actual = [row['key'] for page in (first, second, third) for row in page['items']]
    assert actual == sorted(choice['id'] for choice in d['choices'])
    assert [p['count'] for p in (first, second, third)] == [200, 200, 3]
    assert third['next_cursor'] is None
    client.customer.create(name='Invalidate metadata snapshot', company=COMPANY)
    with pytest.raises(BookflowError) as exc:
        client.run('customer query options', {'kind': 'choices', 'definition': d['id'], 'limit': 200, 'cursor': first['next_cursor']}, company=COMPANY)
    assert exc.value.code == 'E_QUERY_STALE'


def test_effective_inheritance_and_additive_total(client):
    parent = client.customer.create(name='Browse parent', email='inherited@example.invalid', billing_address={'postal_code':'90876'}, company=COMPANY)
    child = client.customer.create(name='Browse child', parent_id=parent['id'], company=COMPANY)
    result = client.customer.query(query='Browse', columns=['full_name','email','postal_code'], limit=1, company=COMPANY)
    assert result['matching_total'] == 2 and result['count'] == 1
    rows = result['items'] + client.customer.query(query='Browse', columns=['full_name','email','postal_code'], limit=1,
        cursor=result['next_cursor'], company=COMPANY)['items']
    assert {r['id'] for r in rows} == {parent['id'], child['id']}
    assert all(r['values']['email'] == 'inherited@example.invalid' and r['values']['postal_code'] == '90876' for r in rows)
    result = client.customer.query(query='No such parent', columns=['email'], company=COMPANY)
    assert result['matching_total'] == result['count'] == 0


def test_collection_columns_and_children_are_bounded(client):
    d = client.run('custom-field create', {'name':'Child pages', 'kind':'choice', 'scopes':['vendor'],
        'choices':[{'value':f'Choice {i:03d}'} for i in range(403)]}, company=COMPANY)
    result = client.run('custom-field query', {'query':'Child pages', 'columns':['name','choices']}, company=COMPANY)
    summary = result['items'][0]['values']['choices']
    assert summary == {'count':403, 'command':'custom-field query children', 'record':d['id'], 'column':'choices'}
    ids, cursor = [], None
    while True:
        page = client.run(summary['command'], {'record':d['id'], 'column':'choices', 'cursor':cursor, 'limit':200}, company=COMPANY)
        assert page['count'] <= 200 and page['matching_total'] == 403
        ids.extend(row['id'] for row in page['items'])
        cursor = page['next_cursor']
        if cursor is None: break
    assert ids == [row['id'] for row in d['choices']]


@pytest.mark.parametrize('value', [0, 0.1, True, '01', '1.0', '1e2', '0.0000000001', '9223372036.854775808'])
def test_noncanonical_custom_number_criteria_reject(client, value):
    from bookflow import BookflowError
    with pytest.raises(BookflowError):
        client.customer.query(custom_filters=[{'definition':'01ARZ3NDEKTSV4RRFFQ69G5FAV', 'kind':'number', 'operator':'eq', 'value':value}], company=COMPANY)


def test_more_than_two_hundred_definitions_have_complete_metadata(client):
    import sqlalchemy as sa
    from bookflow.company import schema
    from bookflow.core.ids import new_id
    from bookflow.storage.engine import open_database
    from tests.test_bounded_queries import _db_path
    created = client.run('custom-field create', {'name':'Metadata sample', 'kind':'text', 'scopes':['customer']}, company=COMPANY)
    # Ordinary read workload fixtures use existing owned-table constraints, no guard changes.
    with open_database(_db_path(client), writable=True) as db:
        template = dict(db.conn.execute(sa.select(schema.custom_field_defs).where(schema.custom_field_defs.c.id == created['id'])).mappings().one())
        scope = dict(db.conn.execute(sa.select(schema.custom_field_scopes).where(schema.custom_field_scopes.c.definition_id == created['id'])).mappings().one())
        definitions, scopes = [], []
        for i in range(403):
            id = new_id(); name = f'Metadata page {i:03d}'
            definitions.append({**template,'id':id,'name':name,'name_key':name.casefold(),'seed_key':None})
            scopes.append({**scope,'id':new_id(),'definition_id':id,'definition_name_key':name.casefold()})
        db.raw.execute('BEGIN IMMEDIATE')
        db.conn.execute(schema.custom_field_defs.insert(),definitions)
        db.conn.execute(schema.custom_field_scopes.insert(),scopes)
        db.raw.execute('COMMIT')
    ids, cursor = [], None
    while True:
        page = client.run('customer query options', {'query':'Metadata page', 'limit':200, 'cursor':cursor}, company=COMPANY)
        ids.extend(row['definition'] for row in page['items'])
        assert page['default_columns']
        cursor = page['next_cursor']
        if cursor is None: break
    assert ids == sorted(row['id'] for row in definitions)


@pytest.mark.parametrize('noun', LIST_DEFINITIONS)
def test_discovery_cli_library_and_selected_read_parity(cli, client, noun):
    expected = client.run(noun + ' query options', {'limit':2}, company=COMPANY)
    actual = cli.json(noun, 'query', 'options', '--limit','2','--company',COMPANY)
    assert actual == expected
    keys = list(builtins(noun))[:2]
    expected = client.run(noun + ' query', {'columns':keys,'limit':2}, company=COMPANY)
    actual = cli.json(noun, 'query', '--columns', json_columns(keys), '--limit','2','--company',COMPANY)
    assert actual == expected


def json_columns(keys):
    import json
    return json.dumps(keys)


def test_exact_numeric_neighbors_above_binary_float_precision(client):
    d = client.run('custom-field create', {'name':'Exact neighboring values','kind':'number','scopes':['customer']},company=COMPANY)
    numbers = ['9007199.254740991', '9007199.254740992', '9007199.254740993']
    ids = [client.customer.create(name='Exact neighbor '+str(i),custom_fields={d['id']:value},company=COMPANY)['id'] for i,value in enumerate(numbers)]
    for operator, expected in [('eq',[ids[1]]),('lt',[ids[0]]),('lte',ids[:2]),('gt',[ids[2]]),('gte',ids[1:]),('ne',[ids[0],ids[2]])]:
        result = client.customer.query(query='Exact neighbor', columns=['custom:'+d['id']],custom_filters=[{
            'definition':d['id'],'kind':'number','operator':operator,'value':numbers[1]}],company=COMPANY)
        assert [row['id'] for row in result['items']] == expected
        assert result['matching_total'] == len(expected)


def test_inactive_definition_search_and_choice_normalized_rename(client):
    d = client.run('custom-field create', {'name':'Retained words','kind':'choice','scopes':['customer'],
        'choices':[{'value':'Straße'}]},company=COMPANY)
    owner=client.customer.create(name='Retained owner',custom_fields={d['id']:'Straße'},company=COMPANY)
    choice=d['choices'][0]
    changed=client.run('custom-field update',{'custom_field':d['id'],'expected_version':d['version'],
        'choices':[{'id':choice['id'],'value':'STRASSE'}]},company=COMPANY)
    client.run('custom-field update',{'custom_field':d['id'],'expected_version':changed['version'],'active':False},company=COMPANY)
    page=client.customer.query(query='STRASSE',columns=['custom:'+d['id']],custom_filters=[{
        'definition':d['id'],'kind':'choice','operator':'eq','value':choice['id']}],company=COMPANY)
    assert [row['id'] for row in page['items']]==[owner['id']]
    assert page['items'][0]['values']['custom:'+d['id']]=='Straße'
    assert page['columns'][0]['active'] is False


def test_partial_billing_address_does_not_inherit_individual_leaves(client):
    parent=client.customer.create(name='Postal parent',billing_address={'city':'Parent city','postal_code':'12345'},company=COMPANY)
    child=client.customer.create(name='Postal child',parent_id=parent['id'],billing_address={'city':'Own city'},company=COMPANY)
    page=client.customer.query(query='Postal child',columns=['postal_code'],company=COMPANY)
    assert page['items'][0]['id']==child['id'] and page['items'][0]['values']['postal_code'] is None
