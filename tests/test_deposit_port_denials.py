"""Three regression witnesses for the existing connected-work admission boundary."""
from contextlib import contextmanager
import shutil
import pytest
import bookflow
from bookflow.company import deposit_public_authority as pa, deposit_public_reads as reads
from bookflow.company import deposit_read_models as m
from bookflow.core.errors import BookflowError
COMPANY = 'Demo Plumbing Co'

@pytest.fixture(scope='module')
def world(_seeded_template,tmp_path_factory):
    from tests.test_service_sales_lifecycle import sale
    from tests.test_work_billing_lifecycle import accepted,bill
    from tests.test_deposit_drafts import cash
    from tests.test_permission_snapshots import install_fixture_policy
    from bookflow.hub.permission_runtime import catalog_bundle
    from bookflow.storage.engine import open_database
    from bookflow.core.host import Host
    from bookflow.core.context import client_version
    root=tmp_path_factory.mktemp('deposit-denials')/'root'
    shutil.copytree(_seeded_template,root)
    client=bookflow.connect(data_root=str(root))
    sales=sale.__wrapped__(client)
    ordinary=cash.__wrapped__(client,sales)
    invoice=bill(client,accepted(client,sales),key='port-denial-work')
    method=client.run('payment-method create',dict(name='Port denial cash',kind='cash'),company=COMPANY)['id']
    payment=client.run('payment receive',dict(customer=sales['customer'],date='2026-06-02',amount='1.00',payment_method=method,operation_key='port-denial-payment',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='1.00')])),company=COMPANY)
    bank=client.account.create(name='Port denial bank',type='bank',company=COMPANY)['id']
    posted=client.run('deposit post',dict(operation_key='port-denial-deposit',document=dict(mode='inline',deposit_to=bank,date='2026-06-03',sources=[ordinary,dict(source_type='payment',source=payment['id'],expected_version=1)])),company=COMPANY)
    cid=client.company.show(company=COMPANY)['company_id']
    with open_database(root/'hub.db',writable=True) as db:
        install_fixture_policy(db.raw,catalog_bundle())
    host=Host(root,version=client_version());host.start()
    try:yield dict(root=root,host=host,cid=cid,deposit=posted['deposit']['id'],ordinary=ordinary['source'],work_payment=payment['id'])
    finally:host.stop()

@contextmanager
def reading(world):
    """One authenticated reader with its own binding and a sealed audience."""
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core.config import os_login
    from bookflow.core.context import Context
    from bookflow.core.publication import OSBinding
    ctx = Context.new('python', 'Public deposit detail witness')
    admitted = OSBinding.capture(world['host'], os_login())
    with ib.hosted_reader(world['host'], admitted, request_id=ctx.request_id) as reader:
        audience = pa.audience(reader, admitted)
        reads.open_selected(reader, audience, world['cid'], ctx)
        yield reader.session, audience, admitted

def set_denies(world, denies):
    """Real current membership denies through the identity administration owner."""
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core.config import Config, os_login
    from bookflow.core.publication import OSBinding
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.permission_admin_support import CONTEXT
    host = world['host']
    uid = Config.load(world['root'] / 'config.toml').user_table(os_login())['user_id']

    def update():
        with host._commit_hooks.operation('dispatch.apply', host._hub):
            with ib.hosted_operation(host, OSBinding.capture(host, os_login()),
                                     request_id=CONTEXT.request_id, purpose='apply') as operation:
                version = host._hub.raw.execute(
                    "SELECT version FROM memberships WHERE user_id=? AND scope_type='company' AND scope_id=?",
                    (uid, world['cid'])).fetchone()[0]
                operation.apply(admin.PutMembership(uid, ScopeKey('company', world['cid']),
                                                    admin.Version(version), 'owner', denies=denies),
                                audit=CONTEXT)
                host._commit_hooks.commit(host._hub, 'dispatch.apply')
    host.submit(update)

@pytest.mark.parametrize('case',['show','items','off_page'])
def test_connected_work_denial_on_port(world,case):
    def call(s,a):
        if case=='show':return reads.show(s,m.ShowInput(deposit=world['deposit']),audience=a)
        return reads.items(s,m.ItemsInput(deposit=world['deposit'],kind='sources',page=m.PageInput(limit=1 if case=='off_page' else 100)),audience=a)
    with reading(world) as (s,a,b):
        allowed=call(s,a)
        if case=='off_page':
            assert allowed.total_count==2 and len(allowed.items)==1 and allowed.next_cursor
            assert allowed.items[0].source.transaction_id==world['ordinary']
    set_denies(world,('customer-work',))
    try:
        with reading(world) as (s,a,b):
            assert not a.admits('customer-work')
            with pytest.raises(BookflowError) as error:call(s,a)
            assert error.value.code=='E_RECORD_NOT_FOUND'
    finally:set_denies(world,())
    with reading(world) as (s,a,b):assert call(s,a)
