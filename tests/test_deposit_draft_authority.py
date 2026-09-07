"""Actual authenticated Sessions/bound credentials; no new permission provider."""
import pytest
from bookflow.company import deposit_drafts as drafts,deposit_selection as child,deposit_source_queries as candidates
from bookflow.company import deposit_draft_models as m
from bookflow.core.context import Context,Interface
from bookflow.core.errors import BookflowError
from tests.test_deposit_drafts import run,cash
from tests.test_deposit_lifecycle import driver
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_deposit_dependency_binding import observe,bound_people,_credential


def test_actual_bound_principal_and_revocation_precede_all_private_reads(root,client,cash,driver,run,bound_people,monkeypatch):
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    from bookflow.core import clock
    people=bound_people
    draft=run('create',dict(header=dict(date='2026-06-03')))
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[cash]))
    selection=run('create',dict(draft=draft.id,expected_version=draft.version),True)
    credential=_credential(client,people['agent'],people['first'])
    ctx=Context.new(Interface.http,'G3 bound witness',on_behalf_of=people['first'])
    calls=[lambda s:drafts.show(s,m.DraftShow(draft=draft.id),ctx=ctx,binding=credential),
        lambda s:drafts.items(s,m.DraftItems(draft=draft.id),ctx=ctx,binding=credential),
        lambda s:drafts.query(s,m.DraftQuery(),ctx=ctx,binding=credential),
        lambda s:child.show(s,m.SelectionShow(selection=selection.id),ctx=ctx,binding=credential),
        lambda s:child.items(s,m.SelectionItems(selection=selection.id),ctx=ctx,binding=credential),
        lambda s:child.query(s,m.SelectionQuery(),ctx=ctx,binding=credential),
        lambda s:candidates.query(s,m.SourceQuery(date='2026-06-03'),ctx=ctx,binding=credential)]
    for call in calls:assert observe(people['bot'],monkeypatch,call,people['company']) is not None
    with writer(root) as db:
        db.conn.execute(h.memberships.update().where(h.memberships.c.user_id==people['first']).values(revoked_at=clock.now_iso()))
    def denied(s):
        before=tuple(s.company.raw.iterdump())
        for call in calls:
            with pytest.raises(BookflowError) as error:call(s)
            assert error.value.code in ('E_PERMISSION','E_UNAUTHENTICATED','E_COMPANY_NOT_FOUND')
            assert draft.id not in str(error.value.details) and cash['source'] not in str(error.value.details)
        assert tuple(s.company.raw.iterdump())==before
    observe(people['bot'],monkeypatch,denied,people['company'])


def test_hub_administrator_without_company_membership_cannot_enter_private_reader(root,client,run,monkeypatch):
    from tests.conftest import make_actor,as_user
    from bookflow.core import registry
    draft=run('create',{})
    make_actor(root,'g3-installation-admin',hub_admin=True)
    admin=as_user(root,'g3-installation-admin');called=[]
    command=registry.get('company show');original=command.plan
    def read(inp,ctx,s):
        called.append(True)
        return drafts.show(s,m.DraftShow(draft=draft.id))
    with monkeypatch.context() as patch:
        patch.setattr(command,'plan',read)
        with pytest.raises(BookflowError) as error:admin.run('company show',{},company=COMPANY)
    assert error.value.code=='E_COMPANY_NOT_FOUND'
    # Legacy company opening currently admits hub-admin; G3 denies at its own
    # membership precheck before any draft decode/count, pending shared full C.
    assert called==[True]


def _scoped_driver(actor,owner_client,monkeypatch):
    # company.show intentionally hides physical paths for lesser roles. The
    # fixture owner supplies its own path; Session capture still dispatches as
    # the real actor, with no authority or production function patched.
    from tests import test_deposit_lifecycle as lifecycle
    from tests.test_row8_journal import database_path
    path=database_path(owner_client)
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle,'database_path',lambda _:path)
        return lifecycle.driver.__wrapped__(actor,monkeypatch)


@pytest.mark.parametrize('org_role,company_role,can_write',[
    ('readonly',None,False),('standard',None,True),('admin',None,True),('owner',None,True),
    ('readonly','standard',True),('standard','readonly',True),('readonly','readonly',False),
    (None,'standard',True),
])
def test_applicable_organization_and_company_roles_use_existing_highest_role(root,client,run,monkeypatch,org_role,company_role,can_write):
    from tests.conftest import make_actor,as_user
    draft=run('create',{})
    company=client.company.list()['items'][0]
    make_actor(root,'g3-scoped',org_role=(company['organization_id'],org_role) if org_role else None,
        company_role=(company['company_id'],company_role) if company_role else None)
    actor=as_user(root,'g3-scoped');private=_scoped_driver(actor,client,monkeypatch)
    with private.session() as s:
        assert drafts.show(s,m.DraftShow(draft=draft.id)).id==draft.id
        assert draft.id in {r['id'] for r in drafts.query(s,m.DraftQuery())['items']}
        before=tuple(s.company.raw.iterdump())
        ctx=Context.new(Interface.python,'Applicable scoped write')
        if can_write:
            created=drafts.run(s,ctx,m.DraftCreate(),'create')
            assert created.id!=draft.id and created.version==1
            assert drafts.show(s,m.DraftShow(draft=created.id))==created
        else:
            with pytest.raises(BookflowError) as error:drafts.run(s,ctx,m.DraftCreate(),'create')
            assert error.value.code=='E_PERMISSION' and error.value.details=={}
            assert tuple(s.company.raw.iterdump())==before


def test_unrelated_organization_does_not_admit_even_hub_admin(root,client,run,monkeypatch):
    from tests.conftest import make_actor,as_user
    draft=run('create',{})
    other=client.organization.new(name='G3 unrelated organization')
    make_actor(root,'g3-wrong-org',hub_admin=True,org_role=(other['id'],'owner'))
    actor=as_user(root,'g3-wrong-org');private=_scoped_driver(actor,client,monkeypatch)
    with private.session() as s:
        before=tuple(s.company.raw.iterdump())
        for read in (lambda:drafts.show(s,m.DraftShow(draft=draft.id)),lambda:drafts.query(s,m.DraftQuery()),
                     lambda:drafts.run(s,Context.new(Interface.python,'Wrong organization'),m.DraftCreate(),'create')):
            with pytest.raises(BookflowError) as error:read()
            assert error.value.code=='E_COMPANY_NOT_FOUND'
            assert draft.id not in str(error.value.details)
        assert tuple(s.company.raw.iterdump())==before


@pytest.mark.parametrize('reduced',['actor','principal'])
def test_real_bound_organization_memberships_allow_then_enforce_current_role(root,client,run,bound_people,monkeypatch,reduced):
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    people=bound_people;draft=run('create',{})
    organization=client.company.list()['items'][0]['organization_id']
    with writer(root) as db:
        db.conn.execute(h.memberships.update().where(h.memberships.c.user_id.in_([people['agent'],people['first'],people['second']])).values(scope_type='organization',scope_id=organization))
    credential=_credential(client,people['agent'],people['first'])
    ctx=Context.new(Interface.http,'Actual scoped principal',on_behalf_of=people['first'])
    private=_scoped_driver(people['bot'],client,monkeypatch)
    with private.session() as s:
        assert drafts.show(s,m.DraftShow(draft=draft.id),ctx=ctx,binding=credential).id==draft.id
        created=drafts.run(s,ctx,m.DraftCreate(),'create',binding=credential)
        assert created.id!=draft.id
    identity=people['agent'] if reduced=='actor' else people['first']
    # Current-row fixture transition preserves the real issued binding, exposing
    # both role checks even before the separately owned full-C suspension wiring.
    with writer(root) as db:
        db.conn.execute(h.memberships.update().where(h.memberships.c.user_id==identity).values(role='readonly'))
    with private.session() as s:
        assert drafts.show(s,m.DraftShow(draft=draft.id),ctx=ctx,binding=credential).id==draft.id
        before=tuple(s.company.raw.iterdump())
        with pytest.raises(BookflowError) as error:drafts.run(s,ctx,m.DraftCreate(),'create',binding=credential)
        assert error.value.code=='E_PERMISSION'
        assert tuple(s.company.raw.iterdump())==before


def test_bound_hub_admin_without_applicable_membership_has_no_book_override(root,client,run,bound_people,monkeypatch):
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    from bookflow.core import clock
    people=bound_people;draft=run('create',{})
    credential=_credential(client,people['agent'],people['first'])
    ctx=Context.new(Interface.http,'Membershipless bound administrator',on_behalf_of=people['first'])
    private=_scoped_driver(people['bot'],client,monkeypatch)
    with writer(root) as db:
        db.conn.execute(h.users.update().where(h.users.c.id==people['first']).values(hub_admin=True))
        db.conn.execute(h.memberships.update().where(h.memberships.c.user_id==people['first']).values(revoked_at=clock.now_iso()))
    with private.session() as s:
        before=tuple(s.company.raw.iterdump())
        for call in (lambda:drafts.show(s,m.DraftShow(draft=draft.id),ctx=ctx,binding=credential),
                     lambda:drafts.query(s,m.DraftQuery(),ctx=ctx,binding=credential),
                     lambda:drafts.run(s,ctx,m.DraftCreate(),'create',binding=credential)):
            with pytest.raises(BookflowError) as error:call()
            assert error.value.code=='E_COMPANY_NOT_FOUND' and error.value.details=={}
        assert tuple(s.company.raw.iterdump())==before
