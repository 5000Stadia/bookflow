"""Actual local default-company producer; self-profile disclosure is target-free."""
import pytest
from bookflow.core import registry, identity_admin_binding as binding
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, client_version
from bookflow.core.dispatch import run
from bookflow.core.host import Host
from bookflow.core.publication_audit import execute_history
from bookflow.hub.audit_projection import HistorySelection
from bookflow.hub.identity_admin import TokenBinding, RevokeMembership
from bookflow.storage.engine import open_database
from tests.test_permission_runtime import path as root_fixture
from tests.permission_admin_support import binding as token_binding
from tests.test_audit_projection_publication import apply


@pytest.fixture(scope='module')
def preferences(tmp_path_factory):
    path=root_fixture.__wrapped__(tmp_path_factory.mktemp('history-company-use'))
    registry.load_all()
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE companies SET display_name='Sensitive company',name_key='sensitive company' WHERE id='C'")
    events={}
    for who in ('A','P'):
        config=Config.load(path.parent/'config.toml');config.set_user(os_login(),who);config.save()
        run(registry.get('company use'),{'company':'Sensitive company'},Context.new('cli','Preference witness'),data_root=str(path.parent))
        with open_database(path,writable=False) as db:
            row=db.raw.execute("SELECT id,summary FROM audit_events WHERE command='company use' AND actor_id=? ORDER BY seq DESC LIMIT 1",(who,)).fetchone()
            assert row and 'Sensitive company' in row[1]
            assert db.raw.execute('SELECT count(*) FROM audit_entries WHERE event_id=?',(row[0],)).fetchone()[0]==0
            events[who]=row[0]
    host=Host(path.parent,version=client_version());host.start()
    try:yield host,path,events
    finally:host.stop()


def read(preferences,who,event):
    host,path,_=preferences
    cred=(TokenBinding('secret-GP-live','GP-live','G','bearer','P',path,'REQUEST')
          if who=='GP' else token_binding(path,who))
    with binding.hosted_reader(host,cred,request_id='REQUEST') as reader:
        out,_=execute_history(reader,HistorySelection(mode='list',command='company use'))
        return next((e for e in out['events'] if e['id']==event),None)


def test_human_self_and_identity_admin_only_get_fixed_preference_history(preferences):
    _,_,events=preferences
    own=read(preferences,'P',events['P'])
    assert own['command']=='company use' and own['summary']=='Default company preference changed.'
    assert own['entries']==[]
    admin=read(preferences,'H',events['P'])
    assert admin['summary']==own['summary']
    assert 'Sensitive company' not in str(own) and 'Sensitive company' not in str(admin)
    assert read(preferences,'R',events['P']) is None
    assert read(preferences,'GP',events['P']) is None


def test_self_preference_presence_and_content_survive_loss_of_target_visibility(preferences):
    host,path,events=preferences
    before=read(preferences,'A',events['A'])
    assert before is not None
    apply(host,path,RevokeMembership('M-A',1),'W')
    after=read(preferences,'A',events['A'])
    assert after==before
