"""Journal-owned custom facts remain exact, immutable and atomic across surfaces."""
import copy

import pytest

from bookflow import BookflowError
from bookflow.company import journal_custom_fields as custom
from bookflow.core import idempotency
from bookflow.storage.engine import open_database
from tests.test_row8_atomicity import state
from tests.test_row8_journal import COMPANY, database_path, journal_accounts, lines, assert_oracle  # noqa: F401


def definition(client, name, **values):
    return client.run('custom-field create', {'name':name,'kind':'text','scopes':['journal_entry'], **values}, company=COMPANY)


def post(client, accounts, **values):
    return client.journal.post(date='2026-01-12', lines=lines(accounts), company=COMPANY, **values)


def complete_state(client):
    result=state(client)
    with open_database(database_path(client), writable=False) as db:
        result['custom_field_values']=list(db.raw.execute('SELECT * FROM custom_field_values ORDER BY id'))
    return result


def snapshot(result):
    return result['revision']['custom_fields_snapshot']


def test_preserved_and_refreshed_facts_do_not_rewrite_history(client, journal_accounts):
    field=definition(client,'Original work order')
    choice=definition(client,'Source',kind='choice',choices=[{'value':'Web'},{'value':'Phone'}])
    first=post(client,journal_accounts,custom_fields={field['id']:'WO-7',choice['id']:'Web'})
    original=copy.deepcopy(snapshot(first))
    client.run('custom-field update', {'custom_field':field['id'],'name':'Renamed work order'},company=COMPANY)
    client.run('custom-field update', {'custom_field':choice['id'], 'choices':[
        {'id':row['id'],'active':row['active'],'value':'WEB' if row['value']=='Web' else row['value']} for row in choice['choices']]},company=COMPANY)
    changed=client.journal.update(journal=first['id'],expected_version=1,memo='Unrelated edit',company=COMPANY)
    assert snapshot(changed) == original
    before=complete_state(client)
    noop=client.journal.update(journal=first['id'],expected_version=changed['version'],
        custom_fields={choice['id']:'WEB'},company=COMPANY)
    assert not noop['changed'] and complete_state(client) == before
    refreshed=client.journal.update(journal=first['id'],expected_version=changed['version'],refresh_defaults=True,company=COMPANY)
    assert snapshot(refreshed)[field['id']]['name'] == 'Renamed work order'
    assert snapshot(refreshed)[choice['id']]['choice_label'] == 'WEB'
    assert snapshot(refreshed)[choice['id']]['value'] == snapshot(refreshed)[choice['id']]['canonical_text'] == 'Web'
    assert snapshot(refreshed)[choice['id']]['choice_id'] == original[choice['id']]['choice_id']
    assert snapshot(client.journal.show(journal=first['id'],revision_number=1,company=COMPANY)) == original
    assert_oracle(client,first['id'],{('2026-01-12',journal_accounts[0]):1234,('2026-01-12',journal_accounts[1]):-1234})
    summary=client.journal.history(journal=first['id'],company=COMPANY)['items'][0]
    assert 'custom_fields' not in summary and 'custom_fields_snapshot' not in summary


def test_slot_clear_reuse_and_register_preservation_under_inactive_definition(client, journal_accounts):
    field=definition(client,'Register custom fact')
    bank,expense=journal_accounts
    first=client.register.post(account=bank,date='2026-01-12',direction='decrease',amount='12.34',category=expense,
        custom_fields={field['id']:'Captured'},company=COMPANY)
    from tests.test_row8_register import edit
    request=edit(first)
    client.run('custom-field deactivate',{'custom_field':field['id']},company=COMPANY)
    unchanged=client.register.update(**request,company=COMPANY)
    assert not unchanged['changed'] and snapshot(unchanged)==snapshot(first)
    client.run('custom-field activate',{'custom_field':field['id']},company=COMPANY)
    cleared=client.journal.update(journal=first['id'],expected_version=1,custom_fields={field['id']:None},company=COMPANY)
    assert snapshot(cleared) == {}
    restored=client.journal.update(journal=first['id'],expected_version=cleared['version'],custom_fields={field['id']:''},company=COMPANY)
    assert snapshot(restored)[field['id']]['value'] == ''
    assert snapshot(restored)[field['id']]['value_id'] == snapshot(first)[field['id']]['value_id']
    before=complete_state(client)['custom_field_values']
    client.journal.void(journal=first['id'],expected_version=restored['version'],reason='Void test entry',company=COMPANY)
    assert complete_state(client)['custom_field_values'] == before
    assert snapshot(client.journal.show(journal=first['id'],revision_number=1,company=COMPANY)) == snapshot(first)


@pytest.mark.parametrize('operation',['post','set','clear','reactivate'])
@pytest.mark.parametrize('failure',['slot','idempotency'])
def test_value_mutations_roll_back_with_journal_and_retry(client,journal_accounts,monkeypatch,operation,failure):
    field=definition(client,'Atomic custom fact')
    first=None
    if operation!='post':
        first=post(client,journal_accounts,custom_fields={field['id']:'before'})
    if operation=='reactivate':
        first=client.journal.update(journal=first['id'],expected_version=first['version'],custom_fields={field['id']:None},company=COMPANY)
    patch={field['id']:None if operation=='clear' else 'after'}
    def invoke():
        if first is None:
            return post(client,journal_accounts,custom_fields=patch,idempotency_key='custom-atomic')
        return client.journal.update(journal=first['id'],expected_version=first['version'],custom_fields=patch,
            idempotency_key='custom-atomic',company=COMPANY)
    before=complete_state(client)
    with monkeypatch.context() as m:
        owner,name=(custom,'apply') if failure=='slot' else (idempotency,'store')
        original=getattr(owner,name)
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('injected after custom value mutation')
        m.setattr(owner,name,fail)
        with pytest.raises((RuntimeError,BookflowError)):
            invoke()
    assert complete_state(client)==before
    saved=invoke();replay=invoke()
    assert saved['id']==replay['id'] and replay['idempotent_replay']
    if operation=='clear': assert snapshot(saved)=={}
    else: assert snapshot(saved)[field['id']]['value']=='after'


def test_custom_only_updates_use_whole_header_version_and_zero_effect_preview(client,journal_accounts):
    field=definition(client,'First custom fact')
    second=definition(client,'Second custom fact')
    first=post(client,journal_accounts)
    before=complete_state(client)
    preview=client.journal.update(journal=first['id'],expected_version=1,custom_fields={field['id']:'preview'},dry_run=True,company=COMPANY)
    assert snapshot(preview)[field['id']]['value']=='preview' and complete_state(client)==before
    changed=client.journal.update(journal=first['id'],expected_version=1,custom_fields={field['id']:'committed'},company=COMPANY)
    with pytest.raises(BookflowError) as error:
        client.journal.update(journal=first['id'],expected_version=1,custom_fields={second['id']:'stale disjoint'},company=COMPANY)
    assert error.value.code=='E_VERSION_CONFLICT'
    assert changed['version']==2
    event=client.audit.show(event=changed['revision']['audit_event_id'],company=COMPANY)
    assert event['command']=='journal update'
    assert sum(row['record_type']=='custom_field_value' for row in event['entries'])==1
