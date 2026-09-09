"""Real producers, readers and denials shared by the public deposit witnesses.

Nothing here is a stand-in. Deposits are posted by the genuine lifecycle
producer over real payment and sales receipts, denials are current membership
denies applied through the identity administration owner, and every reader is an
authenticated BoundReader carrying its own binding.
"""
import shutil
from contextlib import contextmanager

import pytest

import bookflow
from bookflow.core import registry
from bookflow.core.context import client_version
from bookflow.core.host import Host
from bookflow.storage.engine import open_database

COMPANY = 'Demo Plumbing Co'
ABSENT = '00000000000000000000000000'


def make_root(seeded_template, tmp_path_factory, name):
    """A private copy of the shared seed with the permission policy installed."""
    from bookflow.hub.permission_runtime import catalog_bundle
    from tests.test_permission_snapshots import install_fixture_policy
    root = tmp_path_factory.mktemp(name) / 'root'
    shutil.copytree(seeded_template, root)
    with open_database(root / 'hub.db', writable=True) as db:
        install_fixture_policy(db.raw, catalog_bundle())
    registry.load_all()
    return root


def post(client, body):
    """One deposit through the genuine prepare/execute lifecycle."""
    from tests.test_deposit_draft_financial import financial, run_private
    with pytest.MonkeyPatch.context() as patch:
        return financial(run_private.__wrapped__(client, patch), body)


def update(client, body):
    from tests.test_deposit_draft_financial import financial, run_private
    with pytest.MonkeyPatch.context() as patch:
        return financial(run_private.__wrapped__(client, patch), body, 'update')


def void(client, body):
    from tests.test_deposit_draft_financial import financial, run_private
    with pytest.MonkeyPatch.context() as patch:
        return financial(run_private.__wrapped__(client, patch), body, 'void')


def replacement(output, document):
    """The complete replacement document for one posted deposit."""
    return dict(document, number=output.current.number, memo=document.get('memo'),
                cash_back=document.get('cash_back'), custom_fields={},
                expected_custom_field_kinds={}, sources=document.get('sources', []),
                additional=[dict(row, line_id=saved.row_id) for row, saved
                            in zip(document.get('additional', []),
                                   output.effect.financial.intent.additional)])


def masters(client):
    """The reference masters every fixture deposit draws on."""
    bank = client.account.create(name='Public detail bank', type='bank', company=COMPANY)['id']
    till = client.account.create(name='Public detail till', type='other_current_asset', company=COMPANY)['id']
    method = client.run('payment-method create', dict(name='Public detail cash', kind='cash'), company=COMPANY)['id']
    grouping = client.run('class create', dict(name='Public detail class'), company=COMPANY)['id']
    definition = client.run('custom-field create', dict(name='Public detail label', kind='text',
                                                        scopes=['deposit'], default='Captured default'),
                            company=COMPANY)['id']
    return dict(bank=bank, till=till, method=method, grouping=grouping, definition=definition)


def build(seeded_template, tmp_path_factory, name='deposit-public'):
    """A company with the mixed, simple, corrected, voided and work deposits."""
    from tests.test_deposit_drafts import cash
    from tests.test_service_sales_lifecycle import sale
    from tests.test_work_billing_lifecycle import accepted, bill
    root = make_root(seeded_template, tmp_path_factory, name)
    client = bookflow.connect(data_root=str(root))
    world = dict(root=root, client=client, host=None)
    world.update(masters(client))
    sales = sale.__wrapped__(client)
    receipt = cash.__wrapped__(client, sales)
    world.update(customer=sales['customer'], income=sales['income'], source=receipt['source'])

    mixed = post(client, dict(operation_key='public-detail-post', document=dict(
        mode='inline', deposit_to=world['bank'], date='2026-06-03', memo='Public detail memo',
        sources=[receipt],
        additional=[
            dict(received_from=dict(kind='customer', id=sales['customer']), from_account=sales['income'],
                 amount='10.00', memo='Extra cash', check_number='2291',
                 payment_method=world['method'], **{'class': world['grouping']}),
            dict(received_from=dict(kind='customer', id=sales['customer']), from_account=sales['income'],
                 amount='-2.00', memo='Returned cash')],
        cash_back=dict(account=world['till'], amount='5.00', memo='Till cash'))))
    assert mixed.current.revision_bank_total == 6300
    world['deposit'] = mixed.current.id
    world['note'] = client.note.add(record_type='transaction', record_id=mixed.current.id,
                                    body='Deposit annotation', company=COMPANY)['note']['id']

    # A deposit with no sources at all: the simplest posted composition.
    simple = post(client, dict(operation_key='public-detail-simple', document=dict(
        mode='inline', deposit_to=world['bank'], date='2026-06-04', memo='Simple deposit',
        additional=[dict(received_from=dict(kind='customer', id=sales['customer']),
                         from_account=sales['income'], amount='25.00', memo='Counter cash')])))
    world['simple'] = simple.current.id
    world['simple_revision'] = simple.current.revision_id

    # A corrected deposit: two immutable revisions, the second current.
    party = dict(kind='customer', id=sales['customer'])
    original = dict(mode='inline', deposit_to=world['bank'], date='2026-06-05', memo='Before correction',
                    additional=[dict(received_from=party, from_account=sales['income'],
                                     amount='11.00', memo='First amount'),
                                dict(received_from=party, from_account=sales['income'],
                                     amount='12.00', memo='Second amount')])
    corrected = post(client, dict(operation_key='public-detail-correct-post', document=original))
    world['corrected'] = corrected.current.id
    changed = replacement(corrected, original)
    changed['memo'] = 'After correction'
    changed['additional'][1]['amount'] = '19.00'
    after = update(client, dict(deposit=corrected.current.id, expected_version=corrected.current.version,
                                operation_key='public-detail-correct', document=changed))
    world['corrected_version'] = after.current.version

    # A voided deposit: current status voided, the selected revision unchanged.
    voided = post(client, dict(operation_key='public-detail-void-post', document=dict(
        mode='inline', deposit_to=world['bank'], date='2026-06-06', memo='To be voided',
        additional=[dict(received_from=party, from_account=sales['income'],
                         amount='7.00', memo='Voided cash')])))
    void(client, dict(deposit=voided.current.id, expected_version=voided.current.version,
                      operation_key='public-detail-void'))
    world['voided'] = voided.current.id

    # A deposit whose connected graph carries a real customer-work requirement.
    invoice = bill(client, accepted(client, dict(customer=sales['customer'], item='Sale witness service')),
                   key='public-detail-work')
    payment = client.run('payment receive', dict(
        customer=sales['customer'], date='2026-06-02', amount='1.00', payment_method=world['method'],
        operation_key='public-detail-work-pay',
        applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1,
                                                     amount='1.00')])), company=COMPANY)
    work = post(client, dict(operation_key='public-detail-work-deposit', document=dict(
        mode='inline', deposit_to=world['bank'], date='2026-06-03',
        sources=[dict(source_type='payment', source=payment['id'], expected_version=1)])))
    world.update(work=work.current.id, work_payment=payment['id'])

    info = client.company.show(company=COMPANY)
    world['cid'] = info['company_id']
    return world


def start(world, *, serving=False):
    """Take the data-root lock, either as a bare Host or a full serving host."""
    assert world['host'] is None
    if serving:
        from bookflow.commands.host_cmds import start_serving
        world['issued'] = world.get('issued') or world['client'].token.issue(label='Public deposit bearer')
        world['handle'] = start_serving(world['root'], client_version(), bind='127.0.0.1:8765',
                                        secure_cookies=False)
        world['host'] = world['handle'].host
    else:
        world['handle'] = None
        world['host'] = Host(world['root'], version=client_version())
        world['host'].start()
    return world['host']


def stop(world):
    if world['host'] is None:
        return
    if world.get('handle') is not None:
        world['handle'].stop()
    else:
        world['host'].stop()
    world['host'] = world['handle'] = None


@contextmanager
def hosting(world, *, serving=False):
    start(world, serving=serving)
    try:
        yield world['host']
    finally:
        stop(world)


def api(world):
    """A bearer HTTP caller against the running serving host.

    In-process hosts are never forwarded to (`try_forward` skips its own pid),
    so an ordinary client write would take the data-root lock the host holds.
    Writes that must happen while a proof is outstanding go through here.
    """
    from fastapi.testclient import TestClient
    assert world.get('handle') is not None, 'HTTP calls need a serving host'
    client = TestClient(world['handle'].app)
    secret = world['issued']['secret']

    def call(name, body=None, *, company=None, headers=None, expect=200):
        path = f'/companies/{company}/commands/{name}' if company else f'/commands/{name}'
        response = client.post(path, json=body if body is not None else {},
                               headers={'Authorization': 'Bearer ' + secret, **(headers or {})})
        if expect == 'error':
            assert response.status_code != 200, response.text
        elif expect is not None:
            assert response.status_code == expect, response.text
        return response.json()
    return call


@contextmanager
def offline(world):
    """No host at all: the data-root lock is free for the offline dispatch path."""
    was = world['host'] is not None
    serving = world.get('handle') is not None
    stop(world)
    try:
        yield
    finally:
        if was:
            start(world, serving=serving)


@contextmanager
def reading(world, *, ctx=None):
    """One authenticated reader with its own binding and a sealed audience."""
    from bookflow.company import deposit_public_authority as pa
    from bookflow.company import deposit_public_reads as reads
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core.config import os_login
    from bookflow.core.context import Context
    from bookflow.core.publication import OSBinding
    ctx = ctx or Context.new('python', 'Public deposit detail witness')
    admitted = OSBinding.capture(world['host'], os_login())
    with ib.hosted_reader(world['host'], admitted, request_id=ctx.request_id) as reader:
        audience = pa.audience(reader, admitted)
        reads.open_selected(reader, audience, world['cid'], ctx)
        yield reader.session, audience, admitted


@contextmanager
def offline_reading(world, *, ctx=None):
    """A reader with no host at all, over the private execution bridge."""
    from bookflow.company import deposit_public_authority as pa
    from bookflow.company import deposit_public_reads as reads
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core.context import Context
    assert world['host'] is None, 'the offline reader needs the data-root lock'
    ctx = ctx or Context.new('python', 'Public deposit offline witness')
    with ib.offline_reader(world['root'], request_id=ctx.request_id) as reader:
        binding = reader.execution_binding()
        audience = pa.audience(reader, binding)
        reads.open_selected(reader, audience, world['cid'], ctx)
        yield reader.session, audience, binding


@contextmanager
def denied_offline(world, denies):
    """Apply a real deny with the host up, then hand the lock back to the reader."""
    start(world, serving=True)
    set_denies(world, denies)
    stop(world)
    try:
        yield
    finally:
        start(world, serving=True)
        set_denies(world, ())
        stop(world)


def set_denies(world, denies, *, user_id=None, role='owner'):
    """Real current membership denies through the identity administration owner."""
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core.config import Config, os_login
    from bookflow.core.publication import OSBinding
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.permission_admin_support import CONTEXT
    host = world['host']
    uid = user_id or Config.load(world['root'] / 'config.toml').user_table(os_login())['user_id']

    def update():
        with host._commit_hooks.operation('dispatch.apply', host._hub):
            with ib.hosted_operation(host, OSBinding.capture(host, os_login()),
                                     request_id=CONTEXT.request_id, purpose='apply') as operation:
                version = host._hub.raw.execute(
                    "SELECT version FROM memberships WHERE user_id=? AND scope_type='company' AND scope_id=?",
                    (uid, world['cid'])).fetchone()[0]
                operation.apply(admin.PutMembership(uid, ScopeKey('company', world['cid']),
                                                   admin.Version(version), role, denies=denies),
                                audit=CONTEXT)
                host._commit_hooks.commit(host._hub, 'dispatch.apply')
    host.submit(update)


@contextmanager
def denying(world, denies, **target):
    set_denies(world, denies, **target)
    try:
        yield
    finally:
        set_denies(world, (), **target)


@contextmanager
def without_host(world):
    """The host owns the data-root lock; ordinary client writes need it released."""
    with offline(world):
        yield


# A composition wide enough to page: 13 positive supplies spread across 30
# negative buckets and the main bank account, which the derivation resolves into
# exactly 13 * 31 = 403 cash allocation rows from 43 business rows.
WIDE_SUPPLIES = 13
WIDE_BUCKETS = 30
WIDE_CELLS = WIDE_SUPPLIES * (WIDE_BUCKETS + 1)


def wide(world, *, key='public-detail-wide'):
    """One deposit whose cash allocation collection has 403 rows."""
    client = world['client']
    party = dict(kind='customer', id=world['customer'])
    rows = [dict(received_from=party, from_account=world['income'], amount='1000.00',
                 memo=f'Wide supply {index + 1}') for index in range(WIDE_SUPPLIES)]
    rows += [dict(received_from=party, from_account=world['income'], amount='-13.00',
                  memo=f'Wide offset {index + 1}') for index in range(WIDE_BUCKETS)]
    posted = post(client, dict(operation_key=key, document=dict(
        mode='inline', deposit_to=world['bank'], date='2026-06-07', memo='Wide composition',
        additional=rows)))
    world['wide'] = posted.current.id
    return posted
