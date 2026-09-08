"""Direct commands from real producers and actual governed disclosed entries."""
from pathlib import Path

import pytest

from bookflow.core import identity_admin_binding as binding
from bookflow.core.config import os_login
from bookflow.core.context import Context
from bookflow.core.publication import OSBinding
from bookflow.core.publication_audit import open_selected
from bookflow.hub import audit_projection as projection, audit_projection_initiation as initiation
from bookflow.storage.engine import open_database
from tests.test_audit_projection_activity import world
from tests.test_audit_projection_coordinate_events import hosted, denied, coordinate
from tests.test_audit_projection_draft_owners import draft_history
from tests.test_audit_projection_recovery import recovered
from tests.test_audit_projection_draft_disclosure import complete

COMPANY = 'Demo Plumbing Co'


def events(c, command):
    with open_database(c['path'], writable=False) as db:
        return tuple(r[0] for r in db.raw.execute('SELECT id FROM audit_events WHERE command=? ORDER BY seq', (command,)))


def read(c, host, event, command, *, visible=True, bounded=False):
    ctx = Context.new('http', 'Initiating command witness')
    selection = projection.HistorySelection(mode='list', company=c['cid'], command=command,
        limit=1 if bounded else 100, endpoint=event if bounded and visible else None)
    with binding.hosted_reader(host, OSBinding.capture(host, os_login()), request_id=ctx.request_id) as reader:
        open_selected(reader, selection, ctx)
        audience = projection.make_audience(reader)
        value = projection.project_event(audience, event, company=c['cid'])
        page = projection.project_history(audience, selection)
        assert len(page.events) <= (1 if bounded else 100)
        if not bounded: assert not page.has_more
        if visible:
            complete(c, value)
            assert value.command == command and value.summary == command + '.'
            assert next(e for e in page.events if e.id == event) == value
        else:
            assert value is None
            assert event not in {e.id for e in page.events}
        return value


def direct_only(value, command):
    # These are real already-disclosed images, not constructed snapshots/audiences.
    assert initiation.direct_command(command, value.entries) == command
    children = tuple(e for e in value.entries if e.identity.kind not in (
        *initiation.DIRECT, 'transaction', 'payment_operation', 'deposit_operation'))
    assert initiation.direct_command(command, children) is None
    assert initiation.direct_command(command + ' invented', value.entries) is None
    return children


@pytest.fixture(scope='module')
def ordinary(world):
    from tests.test_attachment_commands import add
    from tests.test_payment_receipts import method
    client = world['client']
    customer = client.customer.create(name='Initiation owner', company=COMPANY)
    note = client.note.add(record_type='customer', record_id=customer['id'], body='Original', company=COMPANY)['note']
    client.note.edit(note=note['id'], expected_version=1, body='Corrected', company=COMPANY)
    directive = client.directive.add(text='Owned instruction', company=COMPANY)['directive']
    client.directive.deactivate(directive=directive['id'], company=COMPANY)
    client.run('rate set', dict(date='2026-06-02', from_currency='JPY', rate='0.0068', expected_version=0), company=COMPANY)
    attachment = add(client, customer)
    client.attachment.unlink(link=attachment['link']['id'], expected_version=1, company=COMPANY)
    client.attachment.link(attachment=attachment['attachment']['id'], record_type='customer', record_id=customer['id'], company=COMPANY)
    client.company.compact(company=COMPANY, reason='Record collection')
    bank = client.account.create(name='Initiation bank', type='bank', company=COMPANY)['id']
    expense = client.account.create(name='Initiation expense', type='expense', company=COMPANY)['id']
    client.run('register post', dict(account=bank, date='2026-06-02', direction='decrease', amount='1.50', category=expense), company=COMPANY)
    payment = client.run('payment receive', dict(customer=customer['id'], date='2026-06-02', amount='1.50',
        payment_method=method(client), operation_key='initiation-receive'), company=COMPANY)
    args = dict(payment=payment['id'], expected_version=payment['version'], operation_key='initiation-no-effect')
    result = client.run('payment update', args, company=COMPANY, reason='Retain current receipt')
    assert result['changed'] is False
    co = client.company.show(company=COMPANY)
    c = dict(root=world['root'], path=Path(co['path'])/'company.db', cid=co['company_id'])
    before = events(c, 'payment update')
    replay = client.run('payment update', args, company=COMPANY, reason='Retain current receipt')
    assert replay['idempotent_replay'] and events(c, 'payment update') == before
    return c


@pytest.mark.parametrize('command', ('note add', 'note edit', 'directive add', 'directive deactivate',
    'rate set', 'attachment add', 'attachment link', 'attachment unlink', 'company compact',
    'register post', 'payment update'))
def test_ordinary_direct_command_and_filtered_list(ordinary, command):
    c = ordinary
    event = events(c, command)[-1]
    with hosted(c) as host:
        value = read(c, host, event, command)
        children = direct_only(value, command)
        if command == 'payment update':
            assert {e.identity.kind for e in value.entries} == {'payment_operation', 'payment_operation_item'}
            assert children  # Real new no-effect receipt, no fabricated header.
            receipt = next(e.after for e in value.entries if e.identity.kind == 'payment_operation')
            assert receipt.effect_snapshot.changed is False
            assert initiation.direct_command('payment apply', value.entries) is None
        if command == 'attachment add':
            assert any(e.identity.kind == 'attachment' for e in children)


@pytest.mark.parametrize('command', ('deposit draft create', 'deposit draft update',
    'deposit selection create', 'deposit selection update', 'deposit selection clear', 'deposit selection accept', 'deposit post'))
def test_draft_and_consumption_initiators(draft_history, command):
    c = draft_history
    event = events(c, command)[-1]
    with hosted(c) as host:
        value = read(c, host, event, command)
        direct_only(value, command)
        if command == 'deposit selection accept':
            derived = tuple(e for e in value.entries if e.identity.kind.startswith('deposit_draft'))
            assert derived and initiation.direct_command(command, derived) is None
        if command == 'deposit post':
            receipt_only = tuple(e for e in value.entries if e.identity.kind == 'deposit_operation')
            assert receipt_only and initiation.direct_command(command, receipt_only) == command
            assert initiation.direct_command('deposit void', receipt_only) is None


@pytest.mark.parametrize('verb', ('begin', 'upload', 'seal', 'apply', 'abort', 'replace'))
def test_recovery_phase_initiators(recovered, verb):
    root, cid, path, _ = recovered
    c = dict(root=root, cid=cid, path=path); command = 'payment recovery ' + verb
    with hosted(c) as host:
        event = events(c, command)[-1]
        if verb == 'upload':
            # Upload changes only omitted header provenance here. The real
            # surviving chunk/items are derived, so this allocation leaves None.
            ctx = Context.new('http', 'Recovery derived-only initiation')
            selected = projection.HistorySelection(mode='list', company=cid, command=command, limit=1, endpoint=event)
            with binding.hosted_reader(host, OSBinding.capture(host, os_login()), request_id=ctx.request_id) as reader:
                open_selected(reader, selected, ctx)
                audience = projection.make_audience(reader)
                value = projection.project_event(audience, event, company=cid)
                assert value is not None and value.command is None
                assert {e.identity.kind for e in value.entries} == {'payment_selection_recovery_item', 'payment_selection_recovery_chunk'}
                assert value.summary == 'payment_selection_recovery_chunk, payment_selection_recovery_item updated.'
                assert initiation.direct_command(command, value.entries) is None
                assert event not in {e.id for e in projection.project_history(audience, selected).events}
            return
        value = read(c, host, event, command)
        direct_only(value, command)
        derived = tuple(e for e in value.entries if e.identity.kind.startswith('payment_selection') and
                        e.identity.kind != 'payment_selection_recovery')
        assert initiation.direct_command(command, derived) is None
        if verb != 'seal': assert derived


def test_coordinate_receipt_initiator(coordinate):
    c = coordinate
    with hosted(c) as host:
        value = read(c, host, c['event'], 'deposit coordinate')
        receipt = tuple(e for e in value.entries if e.identity.kind == 'deposit_operation')
        assert len(receipt) == 1
        assert initiation.direct_command('deposit coordinate', receipt) == 'deposit coordinate'
        assert initiation.direct_command('deposit update', receipt) is None
        direct_only(value, 'deposit coordinate')


# B2 restoration alone measured ~75s on this source's retained party witness.
@pytest.mark.timeout(120)
@pytest.mark.parametrize('command,capability', (('note add', 'customer'), ('payment update', 'ledger.read')))
def test_current_owner_denial_removes_command_and_filtered_event(ordinary, command, capability):
    c = ordinary; event = events(c, command)[-1]
    with hosted(c) as host:
        allowed = read(c, host, event, command, bounded=True)
        with denied(c, host, (capability,)):
            read(c, host, event, command, visible=False, bounded=True)
        assert read(c, host, event, command, bounded=True) == allowed
