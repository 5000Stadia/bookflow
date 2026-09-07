"""Actual note producers, typed activity paging and retained fresh publication."""
import shutil
import sqlite3
import pytest
import bookflow
from bookflow.core import registry
from bookflow.core.context import Context,client_version
from bookflow.core.config import os_login
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding,PublicationPermit
from bookflow.storage.engine import open_database
from bookflow.hub.permission_runtime import catalog_bundle
from bookflow.hub.audit_projection import HistorySelection
from bookflow.adapters.http.execution import run_history
from tests.test_permission_snapshots import install_fixture_policy


@pytest.fixture(scope='module')
def world(_seeded_template,tmp_path_factory):
    root=tmp_path_factory.mktemp('projected-activity')/'root'
    shutil.copytree(_seeded_template,root)
    client=bookflow.connect(data_root=str(root))
    with open_database(root/'hub.db',writable=True) as db:install_fixture_policy(db.raw,catalog_bundle())
    registry.load_all()
    yield {'root':root,'client':client}


@pytest.fixture(scope='module')
def customer(world):
    value=world['client'].customer.create(name='Projected activity owner',company='Demo Plumbing Co')
    world['customer']=value
    return value


def test_actual_customer_producer(customer):
    assert customer['name']=='Projected activity owner' and customer['version']==1


@pytest.fixture(scope='module')
def note(world,customer):
    return world['client'].note.add(record_type='customer',record_id=customer['id'],body='  Original <script>\n',company='Demo Plumbing Co')['note']


def test_actual_note_producer(note):
    assert note['body']=='  Original <script>\n' and note['version']==1


@pytest.fixture(scope='module')
def edited(world,note):
    return world['client'].note.edit(note=note['id'],body='Corrected exactly',expected_version=1,company='Demo Plumbing Co')


def test_actual_note_correction(edited):
    assert edited['note']['body']=='Corrected exactly' and edited['note']['version']==2


@pytest.fixture(scope='module')
def hosted(world,edited):
    client=world['client'];row=client.company.show(company='Demo Plumbing Co')
    host=Host(world['root'],version=client_version());host.start()
    from pathlib import Path
    try:
        yield host,OSBinding.capture(host,os_login()),row['company_id'],Path(row['path'])/'company.db'
    finally:host.stop()


def storage(path):
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:return '\n'.join(db.iterdump())


def read(hosted,customer,**fields):
    host,cred,cid,path=hosted
    before=storage(path)
    result=run_history(host,HistorySelection(mode='activity',company=cid,record_type='customer',record_id=customer['id'],**fields),
        Context.new('http','activity-projection').model_copy(update={'request_id':'ACTIVITY'}),cred)
    assert storage(path)==before and host._readers_attached==0
    return result


@pytest.fixture(scope='module')
def first(hosted,customer):return read(hosted,customer,limit=1)


def test_first_projected_page(hosted,customer,first):
    assert first['count']==1 and first['has_more']
    assert first['items'][0]['kind']=='audit' and first['items'][0]['record_id']==customer['id']
    assert first['items'][0]['body'] is None
    assert not {'seq','high_water'} & set(first['items'][0])
    assert first['next_entry_anchor']==first['items'][0]['entry_id']


@pytest.fixture(scope='module')
def second(hosted,customer,first):
    return read(hosted,customer,limit=1,anchor=first['next_anchor'],entry_anchor=first['next_entry_anchor'],endpoint=first['endpoint'])


def test_second_projected_page(second,note):
    assert second['count']==1 and second['has_more']
    assert second['items'][0]['record_id']==note['id']
    assert second['items'][0]['body']=='  Original <script>\n'


@pytest.fixture(scope='module')
def third(hosted,customer,second):
    return read(hosted,customer,limit=1,anchor=second['next_anchor'],entry_anchor=second['next_entry_anchor'],endpoint=second['endpoint'])


def test_third_projected_page(third,note):
    assert third['count']==1 and not third['has_more']
    assert third['items'][0]['record_id']==note['id'] and third['items'][0]['body']=='Corrected exactly'
    assert third['next_anchor'] is None and third['next_entry_anchor'] is None


def test_activity_fresh_publication(hosted,second):
    host,cred,_,path=hosted;before=storage(path)
    permit=PublicationPermit.from_retained(second.permit.retained())
    permit.check(host,cred)
    assert storage(path)==before and host._readers_attached==0
