"""Bounded precursor: identical stored values under admitted real bindings.

No validator extraction or permission mock. The ONLY excluded return component
is deposit_drafts.load()[3], its documented execution binding, asserted by identity
below. Full operation/consumption/aggregate values (including ReadEvidence) compare
without normalization, field removal, serialization or audience redaction.
"""
from contextlib import contextmanager

import pytest

from bookflow import BookflowError
from bookflow.adapters.http.app import Credential
from bookflow.adapters.http.execution import _reader_binding
from bookflow.company import deposit_drafts as drafts, deposit_selection as selections
from bookflow.company import deposit_operation_pages as opages, deposit_draft_consumption as consumption
from bookflow.company import deposit_read_facts as facts, deposit_dependency_history as history
from bookflow.company import deposit_coordination as coordinate, deposit_coordinate_persistence as cp
from bookflow.company import deposit_draft_models as m
from bookflow.company.deposit_coordinate_models import CoordinateInput
from bookflow.core import identity_admin_binding as binding
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core.publication_audit import open_selected
from bookflow.hub.audit_projection import HistorySelection
from bookflow.hub import credentials
from bookflow.hub.permission_runtime import catalog_bundle
from bookflow.storage.engine import open_database
from tests.conftest import make_actor
from tests.permission_admin_support import insert, OLD
from tests.test_permission_snapshots import install_fixture_policy
from tests.test_deposit_draft_financial import run_private, make_posted
from tests.test_deposit_drafts import cash
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import database_path


@pytest.fixture(autouse=True)
def current_policy(root):
    with open_database(root/'hub.db', writable=True) as db:
        install_fixture_policy(db.raw, catalog_bundle())


@pytest.fixture
def state(root, client, cash, run_private):
    posted, original = make_posted(client, cash, run_private)
    edit = run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(
        from_deposit=posted.current.id,expected_version=1),'create'))
    child = run_private(lambda s,ctx:selections.run(s,ctx,m.SelectionCreate(
        draft=edit.id,expected_version=edit.version),'create'))
    accepted = run_private(lambda s,ctx:selections.run(s,ctx,m.SelectionAccept(
        selection=child.id,expected_version=child.version,draft=edit.id,expected_draft_version=edit.version),'accept'))
    edit = accepted.draft
    assert accepted.selection.accepted_revision_id==edit.revision_id
    inp = CoordinateInput.model_validate(dict(deposit=posted.current.id,expected_version=1,
        operation_key='binding-coordinate',source_action=dict(kind='sales_receipt_update',input=dict(
            sales_receipt=cash['source'],expected_version=2,memo='Binding-independent coordinate')),
        replacement=dict(mode='document',document=dict(mode='draft',draft=edit.id,expected_version=edit.version),
            draft_source_result='retain')))
    def execute(s,ctx):
        credential = OSBinding.from_session(s)
        prepared = coordinate.prepare(s,ctx,inp,binding=credential)
        prepared = coordinate.prepare(s,ctx,inp.model_copy(update={'dependency_guard':prepared.dependency_guard}),binding=credential)
        return cp.execute(s,ctx,prepared)
    coordinated = run_private(execute)
    assert coordinated.current_draft.state=='consumed' and coordinated.current_draft.id==edit.id
    assert coordinated.current.revision_bank_total==6000
    cid=client.company.show(company=COMPANY)['company_id']
    owner=Config.load(root/'config.toml').user_table(os_login())['user_id']
    users={name:make_actor(root,'deposit-binding-'+name,company_role=(cid,role))
        for name,role in [('first','standard'),('second','readonly')]}
    users['agent']=make_actor(root,'deposit-binding-agent',company_role=(cid,'standard'),kind='agent',owner_user_id=owner)
    users['denied']=make_actor(root,'deposit-binding-denied')
    creds={}
    with open_database(root/'hub.db',writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        insert(db.raw,'agent_authority',dict(agent_user_id=users['agent'],epoch=1,version=1,
            authorized_at=OLD,authorized_by=owner,permitted_use_at=OLD))
        insert(db.raw,'agent_principals',dict(agent_user_id=users['agent'],principal_user_id=users['second'],
            assigned_by=owner,assigned_at=OLD))
        for name,uid in users.items():
            principal=users['second'] if name=='agent' else None
            row,secret=credentials.issue_token(db,user_id=uid,kind='bearer',label='Owned deposit precursor',
                days=None,via='python',actor_id=owner,on_behalf_of=principal)
            creds[name]=Credential(uid,row['id'],'bearer',None,on_behalf_of=principal,
                actor_kind='agent' if name=='agent' else 'human',secret=secret)
        db.raw.execute('COMMIT')
    path=database_path(client)
    host=Host(root,version=client_version());host.start()
    try:
        yield dict(root=root,host=host,creds=creds,users=users,company=cid,path=path,
            deposit=posted.current.id,operations=(posted.operation_id,coordinated.operation_id),
            drafts=(original.id,edit.id),selection=accepted.selection.id,source=cash['source'])
    finally:host.stop()


@contextmanager
def observed(state,who):
    ctx=Context.new('http','Deposit binding precursor').model_copy(update={'request_id':'REQUEST'})
    cred=state['creds'][who]
    selected=HistorySelection(mode='list',company=state['company'])
    with binding.hosted_reader(state['host'],_reader_binding(state['host'],cred,ctx.request_id),request_id=ctx.request_id) as reader:
        identity=reader.authenticate()
        assert (identity.actor,identity.actor_kind,identity.principal,identity.epoch)==(
            state['users'][who],'agent' if who=='agent' else 'human',state['users']['second'] if who=='agent' else None,
            1 if who=='agent' else None)
        assert not identity.hub_admin
        open_selected(reader,selected,ctx)
        yield reader.session,cred


def stored(state):
    with open_database(state['path'],writable=False) as db:
        company=tuple(db.raw.iterdump())
    hub=state['host'].submit(lambda:tuple(state['host']._hub.raw.iterdump()))
    return hub,company


def test_same_state_financial_loaders_for_admitted_bindings(state,record_property):
    before=stored(state);values=[];identities=[]
    for who in ('first','second','agent'):
        with observed(state,who) as (s,cred):
            identity=history.execution_binding(s,cred);identities.append(identity)
            original=[];consumed=[];loaded=[]
            for operation,draft in zip(state['operations'],state['drafts']):
                saved=drafts._row(s,opages.c.deposit_operations,operation)
                output=opages.authorized_original(s,saved,cred,write=False)
                assert output.operation_id==operation and output.current.id==state['deposit']
                original.append(output)
                current=consumption.current(s,operation,binding=cred,write=False)
                assert current is not None and current.id==draft and current.operation_id==operation and current.state=='consumed'
                consumed.append(current)
            assert original[1].command=='deposit coordinate'
            assert set(original[1].effect.target_ids)=={state['deposit'],state['source']}
            for kind,identifier in [('draft',x) for x in state['drafts']]+[('selection',state['selection'])]:
                header,revision,manifest,returned_binding=drafts.load(s,identifier,kind=kind,binding=cred)
                assert returned_binding is cred
                assert history.execution_binding(s,returned_binding)==identity
                assert header['id']==identifier and manifest.summary.source_count==1
                assert header['state']==('consumed' if kind=='draft' else 'accepted')
                loaded.append((header,revision,manifest))
            aggregate=facts.load_complete(s,[state['deposit']],binding=cred)
            assert len(aggregate)==1 and aggregate[0].header['id']==state['deposit']
            assert len(aggregate[0].selected)==2
            assert all(effect.bank_total==6000 for effect in aggregate[0].effects.values())
            assert len(aggregate[0].evidence.consumed_links)==2
            assert any(link.kind=='selection' and link.id==state['selection'] for link in aggregate[0].links)
            values.append((tuple(original),tuple(consumed),tuple(loaded),aggregate))
        assert stored(state)==before and state['host']._readers_attached==0
    assert len({identity[0] for identity in identities})==3
    assert identities[2][2]==identities[1][0] and identities[2][3]==1
    # Exact native equality includes every financial, history and evidence field.
    assert values[0]==values[1]==values[2]
    record_property('binding_identities',repr(identities))
    record_property('coverage','2 operations (post+coordinate), 2 consumed drafts, 1 accepted selection, complete 2-revision deposit; bank total6000')
    # Real no-membership credential is refused at selected-company admission.
    with pytest.raises(BookflowError) as denied:
        with observed(state,'denied'):pytest.fail('Denied company admitted')
    assert denied.value.code in ('E_PERMISSION','E_COMPANY_NOT_FOUND')
    record_property('denied_company_code',denied.value.code)
    # Existing output is not a proof allowing a different execution binding.
    with observed(state,'first') as (s,cred):
        wrong=state['creds']['second'];saved=drafts._row(s,opages.c.deposit_operations,state['operations'][0])
        for load in (
            lambda:opages.authorized_original(s,saved,wrong),
            lambda:consumption.current(s,state['operations'][0],binding=wrong),
            lambda:drafts.load(s,state['drafts'][0],binding=wrong),
            lambda:drafts.load(s,state['selection'],kind='selection',binding=wrong),
            lambda:facts.load_complete(s,[state['deposit']],binding=wrong),
        ):
            with pytest.raises(BookflowError) as caught:load()
            assert caught.value.code=='E_UNAUTHENTICATED'
    assert stored(state)==before and state['host']._readers_attached==0
