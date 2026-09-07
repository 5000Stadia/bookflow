"""Selected-company hub-name evidence; company copy is never issuer authority."""
import json
import sqlite3
import pytest
import sqlalchemy as sa
from bookflow.company import deposit_dependency_history as history, deposit_lifecycle as lifecycle
from bookflow.company.deposit_dependency_models import PageInput
from bookflow.company.deposit_dependency_pages import changes_page
from bookflow.core.context import Context, Interface
from bookflow.core.publication import OSBinding
from bookflow.core.errors import BookflowError
from tests.test_deposit_dependency_binding import observe, _storage
from tests.test_deposit_lifecycle import additional_document, driver, replacement
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import database_path


def post_request(client, sale):
    return history.request(dict(command='deposit post', input=dict(operation_key='issuer-proof',
        document=additional_document(client, sale)), context={}))


def preview(s, request):
    binding = OSBinding.from_session(s)
    recipe, facts = history.capture(s, request, binding)
    guard = history.issue(s, recipe, facts, binding)
    plan = lifecycle.prepare(s, Context.new(Interface.python, 'issuer-proof'), request.input, 'post', binding=binding)
    assert guard == plan.dependency_guard
    return facts, guard, json.loads(plan.data_json)['issuer'], s.company_info_row['display_name']


def test_rename_and_aba_are_two_hub_attributions_without_company_events(root, client, sale, monkeypatch):
    request = post_request(client, sale)
    first = observe(client, monkeypatch, lambda s: preview(s, request))
    company = first[0].issuer.company_id
    name = first[2]['display_name']
    path = database_path(client)
    with sqlite3.connect(path) as db:
        before = (tuple(db.execute('SELECT * FROM audit_events ORDER BY seq')), tuple(db.execute('SELECT * FROM audit_entries ORDER BY id')))
    client.run('company rename', dict(name='Issuer changed once', move=False), company=company)
    middle = observe(client, monkeypatch, lambda s: preview(s, request), company)
    assert middle[2]['display_name'] == 'Issuer changed once'
    assert middle[0].issuer.entry_id != first[0].issuer.entry_id and middle[1] != first[1]
    client.run('company rename', dict(name=name, move=False), company=company)
    last = observe(client, monkeypatch, lambda s: preview(s, request), company)
    assert last[2] == first[2] and last[1] != first[1]
    assert len({x[0].issuer.entry_id for x in (first, middle, last)}) == 3
    with sqlite3.connect(path) as db:
        assert before == (tuple(db.execute('SELECT * FROM audit_events ORDER BY seq')), tuple(db.execute('SELECT * FROM audit_entries ORDER BY id')))
    with sqlite3.connect(root/'hub.db') as db:
        expected = [db.execute('SELECT e.id,e.actor_id,e.actor_kind,e.on_behalf_of,e.interface,e.at,a.version_before,a.version_after FROM audit_entries a JOIN audit_events e ON e.id=a.event_id WHERE a.id=?', (x[0].issuer.entry_id,)).fetchone() for x in (middle,last)]
    saved = _storage(root,path)
    def compare(s):
        binding = OSBinding.from_session(s)
        result = history.compare(s, first[1], request, binding)
        assert not result.matches and not result.unknown_history
        assert len(result.changes) == 2
        assert [(x.event_id,x.actor_id,x.actor_kind,x.on_behalf_of,x.interface,x.at,x.version_before,x.version_after) for x in result.changes] == expected
        assert all(x.storage == 'hub' and x.kind == 'issuer' and x.record_id == company and x.fields == ('issuer.display_name',) for x in result.changes)
        one = changes_page(s, first[1], request, PageInput(limit=1), binding)
        two = changes_page(s, first[1], request, PageInput(limit=1,cursor=one.next_cursor), binding)
        assert one.items + two.items == result.changes and two.next_cursor is None
        assert one.total_count == two.total_count == 2
    observe(client,monkeypatch,compare,company)
    assert _storage(root,path) == saved


def test_failed_copy_and_readonly_use_hub_name_without_repair(root, client, sale, monkeypatch):
    from bookflow.company import info
    request = post_request(client,sale)
    first = observe(client,monkeypatch,lambda s:preview(s,request))
    company = first[0].issuer.company_id
    path = database_path(client)
    def fail(*args,**kwargs): raise BookflowError('E_IO')
    with monkeypatch.context() as patch:
        patch.setattr(info,'write_display_name_copy',fail)
        client.run('company rename',dict(name='Hub committed copy failed',move=False),company=company)
    saved = _storage(root,path)
    after = observe(client,monkeypatch,lambda s:preview(s,request),company)
    assert after[3] == first[3] and after[3] != 'Hub committed copy failed'
    assert after[2]['display_name'] == 'Hub committed copy failed'
    assert after[1] != first[1]
    assert _storage(root,path) == saved


@pytest.mark.parametrize('damage',['missing','bypass'])
def test_issuer_missing_or_bypassed_history_fails_closed_without_copy(root,client,sale,monkeypatch,damage):
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    request=post_request(client,sale)
    first=observe(client,monkeypatch,lambda s:preview(s,request))
    company=first[0].issuer.company_id
    path=database_path(client)
    # Explicit U1 owned negative fixture; no real root, trigger, or guard changes.
    with writer(root) as db:
        if damage=='missing':
            db.conn.execute(h.audit_entries.delete().where(h.audit_entries.c.record_type=='company',h.audit_entries.c.record_id==company))
        else:
            db.conn.execute(h.companies.update().where(h.companies.c.id==company).values(display_name='Unaudited name'))
    saved=_storage(root,path)
    def check(s):
        binding=OSBinding.from_session(s)
        recipe,facts=history.capture(s,request,binding)
        assert facts.issuer is None and facts.unknown == ('issuer.display_name',)
        with pytest.raises(BookflowError) as error: history.issue(s,recipe,facts,binding)
        assert error.value.code=='E_PREVIEW_STALE' and error.value.details['history']=='unknown_history'
        result=history.compare(s,first[1],request,binding)
        assert not result.matches and result.unknown_history
    observe(client,monkeypatch,check,company)
    assert _storage(root,path)==saved


def test_correction_retains_exact_issuer_without_issuer_history_dependency(root,client,sale,driver,monkeypatch):
    document=additional_document(client,sale)
    posted=driver.run('post',dict(operation_key='issuer-original',document=document))
    with sqlite3.connect(database_path(client)) as db:
        original=db.execute('SELECT issuer_snapshot FROM transaction_revisions WHERE transaction_id=?',(posted.current.id,)).fetchone()[0]
    company=client.company.list()['items'][0]['company_id']
    path=database_path(client)
    client.run('company rename',dict(name='Later hub name',move=False),company=company)
    request=history.request(dict(command='deposit update',input=dict(operation_key='issuer-correction',deposit=posted.current.id,
        expected_version=1,document=dict(replacement(posted,document),memo='Correct captured memo')),context=dict(reason='Correct memo, preserve issuer')))
    saved=_storage(root,path)
    def check(s):
        statements=[]
        def query(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
        sa.event.listen(s.hub.conn,'before_cursor_execute',query)
        try:
            binding=OSBinding.from_session(s)
            recipe,facts=history.capture(s,request,binding)
            token=history.issue(s,recipe,facts,binding)
            assert recipe.issuer_entry is None and facts.issuer is None
            assert history.compare(s,token,request,binding).matches
            result=lifecycle.prepare(s,Context.new(Interface.python,'issuer-correction',reason=request.context.reason),request.input,'update',binding=binding)
            assert json.loads(result.data_json)['issuer']==json.loads(original)
            assert not any('audit_entries' in sql for sql in statements),statements
        finally:sa.event.remove(s.hub.conn,'before_cursor_execute',query)
    observe(client,monkeypatch,check,company)
    assert _storage(root,path)==saved


def test_all_unrelated_hub_changes_keep_the_name_anchor_and_guard(root,client,sale,monkeypatch):
    from pathlib import Path
    from dataclasses import replace
    from tests.conftest import make_actor
    from tests.test_row7_credentials import writer
    from bookflow.hub import companies, schema as h
    from bookflow.core import audit
    request=post_request(client,sale)
    company=client.company.list()['items'][0]['company_id']
    # Leave the old folder so the next SAME-NAME command really moves its path.
    client.run('company rename',dict(name='Name stable during move',move=False),company=company)
    first=observe(client,monkeypatch,lambda s:preview(s,request),company)
    original_path=client.company.show(company=company)['path']
    moved=client.run('company rename',dict(name='Name stable during move',move=True),company=company)
    assert moved['moved'] and moved['path']!=original_path
    def unchanged():
        path=Path(client.company.show(company=company)['path'])/'company.db'
        saved=_storage(root,path)
        result=observe(client,monkeypatch,lambda s:preview(s,request),company)
        assert result[:3]==first[:3]
        assert _storage(root,path)==saved
    unchanged()
    seed=observe(client,monkeypatch,lambda s:s,company)
    with writer(root) as db:
        db.conn.execute(h.companies.update().where(h.companies.c.id==company).values(schema_revision='co0020'))
    unchanged()
    # Same after-only co.update + audit owner used by real projection repair.
    with writer(root) as db:
        s=replace(seed,hub=db)
        row=companies.get(s,company)
        _,touch=companies.update(s,row,'python',legal_name='Hub projection only')
        audit.write_event(s,Context.new(Interface.python,'owned projection repair'),'projection repair','projection only',[touch])
    unchanged()
    make_actor(root,'unrelated-member',company_role=(company,'readonly'))
    unchanged()
    client.run('organization rename',dict(organization=seed.company_row['organization_id'],name='Unrelated organization name',move=False))
    unchanged()
