"""Real current authority over stored deposit captures; not whole-event activation."""
from pathlib import Path
import json
import pytest
from tests.test_audit_projection_activity import world,storage
from tests.test_service_sales_lifecycle import COMPANY,sale
from tests.test_deposit_drafts import cash
from tests.test_deposit_draft_financial import run_private,financial
from bookflow.company import deposit_drafts as drafts,deposit_draft_models as m
from bookflow.core.context import Context,client_version
from bookflow.core.config import Config,os_login
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core import identity_admin_binding as binding
from bookflow.core.publication_audit import open_selected
from bookflow.hub import audit_projection as projection,audit_projection_legacy as views

@pytest.fixture(scope='module')
def deposit(world):
    client=world['client'];sales=sale.__wrapped__(client);source=cash.__wrapped__(client,sales)
    bank=client.account.create(name='Audit masked bank',type='bank',company=COMPANY)['id']
    vendor=client.vendor.create(name='Audit masked vendor',company=COMPANY)['id']
    custom=client.run('custom-field create',dict(name='Audit deposit private note',kind='text',scopes=['deposit']),company=COMPANY)['id']
    cls=client.run('class create',dict(name='Audit masked class'),company=COMPANY)['id']
    with pytest.MonkeyPatch.context() as patch:
        run=run_private.__wrapped__(client,patch)
        draft=run(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch.model_validate(dict(date='2026-06-03',deposit_to=bank,custom_fields={custom:'Captured private value'}))),'create'))
        draft=run(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=draft.id,expected_version=draft.version,set_sources=[m.SourcePatch(**source)],set_additional=[m.AdditionalPatch.model_validate(dict(received_from=None,from_account=sales['income'],amount='1.25',**{'class':cls}))]),'update'))
        def previous_row(s,ctx):
            snapshot=s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE id=?',(draft.revision_id,)).fetchone()[0]
            return json.loads(snapshot)['additional'][0]
        absent=run(previous_row)
        draft=run(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=draft.id,expected_version=draft.version,set_additional=[m.AdditionalPatch(line_id=absent['row_id'],received_from=m.Party(kind='vendor',id=vendor))]),'update'))
        result=financial(run,dict(operation_key='audit-mask-deposit',document=dict(mode='draft',draft=draft.id,expected_version=draft.version)))
        def stored(s,ctx):
            cur=s.company.raw.execute('SELECT * FROM deposit_operations WHERE id=?',(result.operation_id,));row=dict(zip((v[0] for v in cur.description),cur.fetchone(),strict=True))
            seq=s.company.raw.execute('SELECT seq FROM audit_events WHERE id=?',(result.effect.audit_event_id,)).fetchone()[0]
            return row,seq
        raw,seq=run(stored)
    company=client.company.show(company=COMPANY)
    return world['root'],company['company_id'],Path(company['path'])/'company.db',raw,seq,custom,vendor,absent


def test_real_deposit_reference_denial_preserves_amounts(deposit):
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.permission_admin_support import CONTEXT
    root,cid,path,raw,seq,custom,vendor,absent=deposit;before=storage(path)
    host=Host(root,version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        def project(current,additional=None):
            ctx=Context.new('http','deposit-capture-mask')
            with binding.hosted_reader(host,current,request_id=ctx.request_id) as reader:
                open_selected(reader,projection.HistorySelection(mode='show',company=cid,event=raw['audit_event_id']),ctx)
                audience=projection.make_audience(reader)
                audience.require(cid,(('ledger.read','member'),))
                value=views.DepositOperationView.model_validate(raw) if additional is None else views.DepositAuditAdditional.model_validate(additional)
                return projection._disclose_company(audience,cid,value,cutoff=seq).model_dump(mode='json',by_alias=True)
        allowed=project(cred)
        manifest=allowed['effect_snapshot']['effect']['consumed_draft']['snapshot']
        assert manifest['header']['custom_fields'][custom]['canonical_text']=='Captured private value'
        assert manifest['additional'][0]['received_from']['id']==vendor
        assert manifest['header']['bank']['id'] is not None
        uid=Config.load(root/'config.toml').user_table(os_login())['user_id']
        def deny():
            with host._commit_hooks.operation('dispatch.apply',host._hub):
                with binding.hosted_operation(host,cred,request_id=CONTEXT.request_id,purpose='apply') as operation:
                    operation.apply(admin.PutMembership(uid,ScopeKey('company',cid),admin.Version(1),'owner',denies=('vendor','account','class','custom-field')),audit=CONTEXT)
                    host._commit_hooks.commit(host._hub,'dispatch.apply')
        host.submit(deny)
        denied=project(OSBinding.capture(host,os_login()))
        manifest=denied['effect_snapshot']['effect']['consumed_draft']['snapshot']
        assert manifest['header']['bank'] is None and manifest['header']['custom_fields'] is None
        assert 'deposit_to' not in manifest['header']['origins']
        row=manifest['additional'][0]
        assert row['received_from'] is None and row['party_name'] is None and row['account'] is None and row['class_ref'] is None
        assert not {'received_from','from_account','class_id'} & set(row['origins'])
        assert project(OSBinding.capture(host,os_login()),additional=absent)==row
        assert row['units']==125 and manifest['summary']['bank_total']==6125
        assert denied['effect_snapshot']['current']['revision_bank_total']==6125
        assert storage(path)==before and host._readers_attached==0
    finally:host.stop()
