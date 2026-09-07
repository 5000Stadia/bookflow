"""Actual old selector/default and complete ordinary conflict witnesses."""
import pytest
from bookflow.company import deposit_dependency_history as history
from bookflow.core.publication import OSBinding
from bookflow.core.errors import BookflowError
from tests.test_deposit_lifecycle import driver, additional_document, replacement
from tests.test_deposit_dependency_binding import observe
from tests.test_service_sales_lifecycle import sale, COMPANY


def token(s,request):
    binding=OSBinding.from_session(s)
    recipe,facts=history.capture(s,request,binding)
    assert not facts.unknown,facts.unknown
    return history.issue(s,recipe,facts,binding)


def test_only_used_default_changes_and_change_back_are_attributed(client,sale,monkeypatch):
    definition=client.run('custom-field create',dict(name='Deposit provenance default',kind='text',scopes=['deposit'],default='First'),company=COMPANY)['id']
    doc=additional_document(client,sale)
    omitted=history.request(dict(command='deposit post',input=dict(operation_key='default-implicit',document=doc),context={}))
    explicit=history.request(dict(command='deposit post',input=dict(operation_key='default-explicit',document=dict(doc,custom_fields={definition:'Chosen'})),context={}))
    guards=[observe(client,monkeypatch,lambda s:token(s,r)) for r in (omitted,explicit)]
    for version,value in ((1,'Second'),(2,'First')):
        client.run('custom-field update',dict(custom_field=definition,expected_version=version,default=value),company=COMPANY)
    def compare(s):
        before=tuple(s.company.raw.iterdump())
        binding=OSBinding.from_session(s)
        unchanged=history.compare(s,guards[1],explicit,binding)
        assert unchanged.matches and not unchanged.unknown_history and unchanged.changes==()
        changed=history.compare(s,guards[0],omitted,binding)
        assert not changed.matches and not changed.unknown_history
        assert len(changed.changes)==2
        assert [(x.kind,x.record_id,x.version_before,x.version_after,x.fields) for x in changed.changes]==[
            ('custom_field',definition,1,2,('default_canonical_text',)),('custom_field',definition,2,3,('default_canonical_text',))]
        assert tuple(s.company.raw.iterdump())==before
    observe(client,monkeypatch,compare)


def test_old_alias_disappears_then_is_reused_without_fabricating_history(client,sale,monkeypatch):
    doc=additional_document(client,sale)
    old=doc['deposit_to'];doc['deposit_to']='G2 bank'
    request=history.request(dict(command='deposit post',input=dict(operation_key='old-alias',document=doc),context={}))
    guard=observe(client,monkeypatch,lambda s:token(s,request))
    client.account.update(account=old,expected_version=1,name='Renamed old bank',company=COMPANY)
    def compare(s,new=None):
        result=history.compare(s,guard,request,OSBinding.from_session(s))
        assert not result.matches and not result.unknown_history,result.unknown_records
        baseline=[r for r in result.baseline.relations if r.kind=='selector:account' and r.owner_id=='G2 bank']
        current=[r for r in result.current.relations if r.kind=='selector:account' and r.owner_id=='G2 bank']
        assert baseline[0].members==(old,)
        assert current[0].members==(() if new is None else (new,))
        assert any(x.record_id==old and 'name' in x.fields for x in result.changes)
    observe(client,monkeypatch,compare)
    new=client.account.create(name='G2 bank',type='bank',company=COMPANY)['id']
    observe(client,monkeypatch,lambda s:compare(s,new))


def test_owner_version_conflict_uses_attributed_inspection_recipe(client,sale,driver,monkeypatch):
    from bookflow.company.deposit_dependency_models import InspectionRoot,PageInput
    from bookflow.company.deposit_dependency_pages import changes_page
    doc=additional_document(client,sale)
    posted=driver.run('post',dict(operation_key='version-original',document=doc))
    edited=driver.run('update',dict(operation_key='version-edited',deposit=posted.current.id,expected_version=1,
        document=dict(replacement(posted,doc),memo='First editor wrote')),reason='First editor correction')
    before=driver.dump()
    with pytest.raises(BookflowError) as error:
        driver.run('update',dict(operation_key='version-conflict',deposit=posted.current.id,expected_version=1,
            document=dict(replacement(posted,doc),memo='Second editor wrote')),reason='Second editor correction')
    assert error.value.code=='E_VERSION_CONFLICT'
    details=error.value.details
    assert details['history']=='known_stale'
    assert any(x['event_id']==edited.effect.audit_event_id and 'commercial.memo' in x['fields'] for x in details['changes']['items'])
    def again(s):
        page=changes_page(s,details['dependency_guard'],InspectionRoot.model_validate(details['original_request']),PageInput(),OSBinding.from_session(s))
        assert page.total_count==details['changes']['total_count']
        assert [(x.event_id,x.fields) for x in page.items]==[(x['event_id'],tuple(x['fields'])) for x in details['changes']['items']]
    observe(client,monkeypatch,again)
    assert driver.dump()==before


def test_automatic_number_change_has_complete_baseline(client,sale,driver,monkeypatch):
    import json
    from bookflow.company import deposit_lifecycle as lifecycle
    from bookflow.core.context import Context,Interface
    document=additional_document(client,sale)
    request=history.request(dict(command='deposit post',input=dict(operation_key='waiting-number',document=document),context={}))
    def preview(s):
        result=lifecycle.prepare(s,Context.new(Interface.python,'number dependency'),request.input,'post')
        return result.dependency_guard,result.facts_fingerprint,json.loads(result.data_json)['number']
    before=observe(client,monkeypatch,preview)
    other=driver.run('post',dict(operation_key='occupying-number',document=document))
    after=observe(client,monkeypatch,preview)
    assert before[2]!=after[2] and before[1]!=after[1]
    def complete(s):
        result=history.compare(s,before[0],request,OSBinding.from_session(s))
        assert not result.matches and not result.unknown_history
        assert any(row.event_id==other.effect.audit_event_id for row in result.changes)
    observe(client,monkeypatch,complete)


def test_unused_choice_and_other_scope_changes_are_not_dependencies(client,sale,monkeypatch):
    definition=client.run('custom-field create',dict(name='Deposit chosen label',kind='choice',scopes=['deposit'],
        default='Selected',choices=[dict(value='Selected'),dict(value='Unused')]),company=COMPANY)
    document=additional_document(client,sale)
    document['custom_fields']={definition['id']:'Selected'}
    request=history.request(dict(command='deposit post',input=dict(operation_key='used-choice',document=document),context={}))
    guard=observe(client,monkeypatch,lambda s:token(s,request))
    choices=definition['choices']
    client.run('custom-field update',dict(custom_field=definition['id'],expected_version=1,scopes=['deposit','invoice'],
        choices=[dict(id=c['id'],value='Changed unused' if c['value']=='Unused' else c['value']) for c in choices]),company=COMPANY)
    def check(s):
        result=history.compare(s,guard,request,OSBinding.from_session(s))
        assert result.matches and not result.unknown_history and result.changes==()
    observe(client,monkeypatch,check)
