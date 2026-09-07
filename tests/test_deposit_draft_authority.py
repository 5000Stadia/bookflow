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


def test_organization_only_membership_is_not_company_book_admission(root,client,run,monkeypatch):
    from tests.conftest import make_actor,as_user
    draft=run('create',{})
    company=client.company.list()['items'][0]
    make_actor(root,'g3-org-only',org_role=(company['organization_id'],'owner'))
    actor=as_user(root,'g3-org-only')
    def denied(s):
        with pytest.raises(BookflowError) as error:drafts.show(s,m.DraftShow(draft=draft.id))
        assert error.value.code=='E_COMPANY_NOT_FOUND'
    observe(actor,monkeypatch,denied,company['company_id'])
