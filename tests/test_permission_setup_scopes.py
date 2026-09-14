"""Existing registration/removal paths reconcile inside their own hub writer."""
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from bookflow.core.config import Config, os_login
from tests.payment_raw_evidence import database


@pytest.mark.timeout(180)
def test_create_detach_and_attach_keep_exact_administrator_enrollment(root,client):
    original=client.company.list()['items'][0]
    state=client.permission.show()
    client.permission.activate(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'])
    user=client.user.add(username='attached-admin',password='owned fixture',company=original['company_id'])
    created=client.run('company new',dict(display_name='Scoped new company',legal_name='Scoped Company',
        home_currency='USD',organization=original['organization_id']))
    with sqlite3.connect(root/'hub.db') as db:
        actor=Config.load(root/'config.toml').user_table(os_login())['user_id']
        assert db.execute('SELECT user_id,role FROM memberships WHERE scope_type=? AND scope_id=?',('company',created['company_id'])).fetchall()==[(actor,'owner')]
        path=root/db.execute('SELECT path FROM companies WHERE id=?',(created['company_id'],)).fetchone()[0]
        generation=db.execute('SELECT generation FROM permission_state').fetchone()[0]
    before=database(path/'company.db')
    client.run('company detach',{'company':created['company_id']})
    with sqlite3.connect(root/'hub.db') as db:
        assert db.execute('SELECT count(*) FROM memberships WHERE scope_id=?',(created['company_id'],)).fetchone()==(0,)
        assert db.execute('SELECT generation FROM permission_state').fetchone()[0] > generation
    assert database(path/'company.db')==before
    with pytest.raises(BookflowError) as missing:
        client.run('company attach',{'path':str(path)})
    assert missing.value.code=='E_VALIDATION'
    attached=client.run('company attach',{'path':str(path),'administrator':user['user_id']})
    assert attached['company_id']==created['company_id']
    with sqlite3.connect(root/'hub.db') as db:
        assert db.execute('SELECT user_id,role FROM memberships WHERE scope_type=? AND scope_id=?',('company',created['company_id'])).fetchall()==[(user['user_id'],'admin')]
    assert database(path/'company.db')==before
    assert created['company_id'] not in {x['company_id'] for x in client.company.list()['items']}


@pytest.mark.timeout(180)
def test_pending_trash_recovery_and_demo_cascade_use_registry_policy_owner(root,client,monkeypatch):
    from tests.test_move_recovery import _hub_set
    from bookflow.hub import schema as h
    original=client.company.list()['items'][0]
    state=client.permission.show()
    client.permission.activate(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'])
    created=client.company.new(legal_name='Interrupted removal',home_currency='USD',organization=original['organization_id'])
    with sqlite3.connect(root/'hub.db') as db:
        path=root/db.execute('SELECT path FROM companies WHERE id=?',(created['company_id'],)).fetchone()[0]
    before=database(path/'company.db')
    # A historical pending marker represents the committed first half of removal.
    # Opening this company must execute the existing _complete_trash recovery.
    target='trash/phase2-interrupted-removal'
    _hub_set(root,h.companies,created['company_id'],pending_path=target)
    with sqlite3.connect(root/'hub.db') as db:
        generation=db.execute('SELECT generation FROM permission_state').fetchone()[0]
    with pytest.raises(BookflowError) as removed:
        client.company.update(company=created['company_id'],fax='must not execute')
    assert removed.value.code=='E_COMPANY_NOT_FOUND'
    assert database(root/target/'company.db')==before
    with sqlite3.connect(root/'hub.db') as db:
        assert db.execute('SELECT count(*) FROM companies WHERE id=?',(created['company_id'],)).fetchone()==(0,)
        assert db.execute('SELECT count(*) FROM memberships WHERE scope_id=?',(created['company_id'],)).fetchone()==(0,)
        assert db.execute('SELECT generation FROM permission_state').fetchone()[0]==generation+1
    events=client.hub.audit.list(command='trash recovery')['items']
    assert len(events)==1
    event=client.hub.audit.show(event=events[0]['id'])
    assert any(x['record_type']=='permission_state' for x in event['entries'])
    # The ordinary reset cascade removes old membership scope and explicitly
    # enrolls its new creator. It does not transfer old company grants to new IDs.
    from bookflow.commands import hub_cmds
    seed_history=hub_cmds._apply_seed_history
    def bounded_history(session,ctx,seed,row):
        return seed_history(session,ctx,dict(commands=[dict(command='account create',
            input=dict(name='Scoped creator seed witness',type='expense'))]),row)
    # This is registry/cascade acceptance, not the full historical demo suite.
    with monkeypatch.context() as patch:
        patch.setattr(hub_cmds,'_apply_seed_history',bounded_history)
        replacement=client.demo.reset()
    with sqlite3.connect(root/'hub.db') as db:
        assert db.execute('SELECT count(*) FROM memberships WHERE scope_id IN (?,?)',
            (original['company_id'],original['organization_id'])).fetchone()==(0,)
        actor=Config.load(root/'config.toml').user_table(os_login())['user_id']
        assert db.execute("SELECT user_id,role,grants,denies FROM memberships WHERE scope_type='company' AND scope_id=?",
            (replacement['company_id'],)).fetchall()==[(actor,'owner',None,None)]
    assert client.company.show(company=replacement['company_id'])['company_id']==replacement['company_id']

    assert any(x['name']=='Scoped creator seed witness' for x in client.account.list(company=replacement['company_id'])['items'])
