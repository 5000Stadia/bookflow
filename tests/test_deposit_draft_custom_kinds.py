"""N5: real draft/custom producers, complete rollback and immutable JSON oracles."""
import json
import pytest
from bookflow.company import deposit_drafts as d, deposit_draft_models as m
from bookflow.company import deposit_draft_validation as validation, payment_queries as q
from bookflow.core.errors import BookflowError
from tests.test_deposit_drafts import run, financial
from tests.test_deposit_lifecycle import driver, additional_document
from tests.test_service_sales_lifecycle import sale, COMPANY


def definition(client, kind='text', **kwargs):
    return client.run('custom-field create', dict(name='N5 '+kind,kind=kind,scopes=['deposit'],**kwargs),company=COMPANY)['id']


def update(run, draft, **header):
    return run('update',dict(draft=draft.id,expected_version=draft.version,header=header))


@pytest.mark.parametrize('case',['orphan','null','unknown','inapplicable','mismatch'])
def test_complete_expected_kind_admission_no_writes(client,driver,run,case):
    key=definition(client)
    draft=run('create',{})
    values={key:'value'};expected={key:'text'}
    if case=='orphan':values={}
    elif case=='null':values={key:None}
    elif case=='unknown':key='00000000000000000000000000';values={key:'value'};expected={key:'text'}
    elif case=='inapplicable':
        key=client.run('custom-field create',dict(name='Other scope',kind='text',scopes=['customer']),company=COMPANY)['id']
        values={key:'value'};expected={key:'text'}
    else:expected={key:'date'}
    before=driver.dump()
    with pytest.raises(BookflowError) as caught:
        update(run,draft,custom_fields=values,expected_custom_field_kinds=expected)
    assert caught.value.code==('E_RECORD_NOT_FOUND' if case in ('unknown','inapplicable') else 'E_VALIDATION')
    assert driver.dump()==before


@pytest.mark.parametrize('kind,value',[('text','original'),('number','1.000000001'),('date','2026-06-03'),('bool',False),('choice','Alpha')])
def test_expectation_roundtrip_clear_and_old_hash(client,driver,run,kind,value):
    key=definition(client,kind,**({'choices':[dict(value='Alpha'),dict(value='Beta')]} if kind=='choice' else {}))
    draft=run('create',dict(header=dict(custom_fields={key:value},expected_custom_field_kinds={key:kind})))
    with driver.session() as s:
        h,r,manifest,_=d.load(s,draft.id)
        raw=json.loads(r['snapshot'])
        assert raw['header']['custom_fields'][key]['expected_kind']==kind
        original=(r['snapshot'],r['manifest_hash'])
        assert manifest.header.custom_fields[key].expected_kind==kind
        before=financial(s)
    preserved=update(run,draft,memo='unrelated')
    assert preserved.header.custom_fields[key]==draft.header.custom_fields[key]
    cleared=update(run,preserved,custom_fields={key:None})
    assert cleared.header.custom_fields[key].canonical_text is None
    assert 'expected_kind' not in cleared.header.custom_fields[key].model_dump(mode='json')
    with driver.session() as s:
        _,r,old,_=d.load(s,draft.id,1)
        assert (r['snapshot'],r['manifest_hash'])==original
        assert old.header.custom_fields[key].expected_kind==kind
        assert financial(s)==before
    empty=run('clear',dict(draft=draft.id,expected_version=cleared.version))
    assert empty.header.custom_fields=={}


def test_equal_string_kind_change_is_explicit_not_refresh(client,driver,run):
    key=definition(client)
    draft=run('create',dict(header=dict(custom_fields={key:'2026-06-03'})))
    old=draft.header.custom_fields[key]
    assert 'expected_kind' not in old.model_dump(mode='json')
    client.run('custom-field update',dict(custom_field=key,expected_version=1,kind='date'),company=COMPANY)
    retained=update(run,draft,memo='Keep captured text')
    assert retained.header.custom_fields[key]==old
    before=driver.dump()
    with pytest.raises(BookflowError) as caught:
        update(run,retained,custom_fields={key:'2026-06-03'},expected_custom_field_kinds={key:'text'})
    assert caught.value.code=='E_VALIDATION' and driver.dump()==before
    changed=update(run,retained,custom_fields={key:'2026-06-03'},expected_custom_field_kinds={key:'date'})
    capture=changed.header.custom_fields[key]
    assert (capture.kind,capture.expected_kind,capture.definition_version,capture.canonical_text)==('date','date',2,'2026-06-03')
    with driver.session() as s:
        assert d.show(s,m.DraftShow(draft=draft.id,revision_number=1)).header.custom_fields[key]==old


def test_same_kind_inactive_and_assertion_provenance(client,driver,run):
    key=definition(client,default='Captured')
    draft=run('create',{})
    old=draft.header.custom_fields[key]
    client.run('custom-field update',dict(custom_field=key,expected_version=1,name='Renamed',default='New'),company=COMPANY)
    client.run('custom-field deactivate',dict(custom_field=key,expected_version=2),company=COMPANY)
    unchanged=update(run,draft,custom_fields={key:'Captured'})
    assert unchanged.version==draft.version and unchanged.header.custom_fields[key]==old
    asserted=update(run,unchanged,custom_fields={key:'Captured'},expected_custom_field_kinds={key:'text'})
    assert asserted.header.custom_fields[key].model_dump(exclude={'expected_kind'})==old.model_dump()
    assert asserted.header.custom_fields[key].expected_kind=='text'
    before=driver.dump()
    assert update(run,asserted,custom_fields={key:'Captured'}).version==asserted.version
    assert driver.dump()==before


@pytest.mark.parametrize('tamper',['kind','null','legacy'])
def test_independent_decoder_expectation_and_legacy_bytes(client,driver,run,tamper):
    key=definition(client)
    draft=run('create',dict(header=dict(custom_fields={key:'Captured'})))
    before=driver.dump()
    with driver.session() as s:
        h,r,manifest,_=d.load(s,draft.id)
        raw=json.loads(r['snapshot'])
        assert 'expected_kind' not in raw['header']['custom_fields'][key]
        if tamper=='legacy':
            assert q.digest(raw)==r['manifest_hash']
            assert validation.decode_revision(s,h,r,'draft')==manifest
        else:
            capture=raw['header']['custom_fields'][key]
            capture['expected_kind']='date' if tamper=='kind' else 'text'
            if tamper=='null':capture['canonical_text']=None
            # Recompute even the hash: the semantic decoder, not a checksum, rejects.
            fake=dict(r,snapshot=q.canonical(raw),manifest_hash=q.digest(raw))
            with pytest.raises(BookflowError) as caught:validation.decode_revision(s,h,fake,'draft')
            assert caught.value.details['reason']=='custom_kind_expectation'
    assert driver.dump()==before


def test_edit_and_voided_copy_do_not_fabricate_assertions(client,sale,driver,run):
    key=definition(client)
    doc=additional_document(client,sale)
    doc.update(custom_fields={key:'Posted'},expected_custom_field_kinds={key:'text'})
    posted=driver.run('post',dict(operation_key='n5-original',document=doc))
    with driver.session() as s:before=financial(s)
    edit=run('create',dict(from_deposit=posted.current.id,expected_version=posted.current.version))
    capture=edit.header.custom_fields[key]
    assert capture.origin=='source' and capture.canonical_text=='Posted'
    assert 'expected_kind' not in capture.model_dump(mode='json')
    with driver.session() as s:assert financial(s)==before
    voided=driver.run('void',dict(operation_key='n5-void',deposit=posted.current.id,expected_version=posted.current.version),reason='N5 copy evidence')
    with driver.session() as s:before=financial(s)
    copied=run('create',dict(copy_from_voided=posted.current.id,expected_version=voided.current.version))
    assert copied.header.custom_fields[key]==capture
    assert copied.edit_transaction_id is None and copied.copy_transaction_id==posted.current.id
    with driver.session() as s:assert financial(s)==before


def test_replacement_drops_old_assertion_and_choice_snapshot_stays_captured(client,driver,run):
    key=definition(client,default='old')
    draft=run('create',dict(header=dict(custom_fields={key:'old'},expected_custom_field_kinds={key:'text'})))
    changed=update(run,draft,custom_fields={key:'new'})
    assert changed.header.custom_fields[key].canonical_text=='new'
    assert 'expected_kind' not in changed.header.custom_fields[key].model_dump(mode='json')
    choice=definition(client,'choice',choices=[dict(value='Alpha'),dict(value='Beta')],default='Alpha')
    draft=run('create',{})
    capture=draft.header.custom_fields[choice]
    shown=client.run('custom-field show',dict(custom_field=choice),company=COMPANY)
    choices=[dict(id=row['id'],value='Renamed' if row['value']=='Alpha' else row['value']) for row in shown['choices']]
    client.run('custom-field update',dict(custom_field=choice,expected_version=1,choices=choices,default='Renamed'),company=COMPANY)
    preserved=update(run,draft,custom_fields={choice:'Alpha'},expected_custom_field_kinds={choice:'choice'})
    assert preserved.header.custom_fields[choice].choice_label=='Alpha'
    assert preserved.header.custom_fields[choice].choice_id==capture.choice_id
    assert preserved.header.custom_fields[choice].definition_version==capture.definition_version


def test_inactive_kind_change_is_not_an_unchanged_historical_value(client,driver,run):
    key=definition(client)
    draft=run('create',dict(header=dict(custom_fields={key:'2026-06-03'})))
    client.run('custom-field update',dict(custom_field=key,expected_version=1,kind='date'),company=COMPANY)
    client.run('custom-field deactivate',dict(custom_field=key,expected_version=2),company=COMPANY)
    retained=update(run,draft,memo='Retain historical value')
    assert retained.header.custom_fields[key]==draft.header.custom_fields[key]
    before=driver.dump()
    with pytest.raises(BookflowError) as caught:
        update(run,retained,custom_fields={key:'2026-06-03'},expected_custom_field_kinds={key:'date'})
    assert caught.value.code=='E_VALIDATION' and driver.dump()==before


def test_create_expectations_do_not_borrow_defaults(client,driver,run):
    key=definition(client,default='Captured')
    before=driver.dump()
    for values in ({},{key:None}):
        with pytest.raises(BookflowError) as caught:
            run('create',dict(header=dict(custom_fields=values,expected_custom_field_kinds={key:'text'})))
        assert caught.value.code=='E_VALIDATION' and driver.dump()==before
