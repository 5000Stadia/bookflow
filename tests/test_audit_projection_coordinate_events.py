"""Actual coordinate events and current governed disclosure; private release only."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json

import pytest

from bookflow.company import deposit_coordinate_persistence as persistence, payment_authority
from bookflow.company.deposit_coordinate_models import CoordinateInput
from bookflow.core import identity_admin_binding as binding
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, Interface, client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core.publication_audit import open_selected
from bookflow.hub import audit_projection as projection, audit_projection_legacy as legacy
from bookflow.hub import identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from tests.permission_admin_support import CONTEXT
from tests.test_audit_projection_activity import world as governed_world, storage
from tests.test_deposit_coordinate_persistence import n2, driver, sale, prepare
from tests.test_service_sales_lifecycle import COMPANY


@pytest.fixture(scope='module', params=('payment_update', 'sales_receipt_update'))
def coordinate(request, _seeded_template, tmp_path_factory):
    generator = governed_world.__wrapped__(_seeded_template, tmp_path_factory)
    world = next(generator)
    try:
        client = world['client']
        sales = sale.__wrapped__(client)
        with pytest.MonkeyPatch.context() as patch:
            runner = driver.__wrapped__(client, patch)
            inp, post, document, payment, receipt, invoice = n2.__wrapped__(client, sales, runner)
            wire = inp.model_dump(mode='json', by_alias=True, exclude_unset=True)
            method = client.run('payment-method create', dict(name='Coordinate event method', kind='cash'), company=COMPANY)['id']
            custom = None
            if request.param == 'payment_update':
                wire['source_action']['input']['payment_method'] = method
                total = 19200
            else:
                custom = client.run('custom-field create', dict(name='Coordinate receipt note', kind='text', scopes=['sales_receipt']), company=COMPANY)['id']
                # Current UF account retained explicitly; reference captures populated.
                source_account = client.run('sales-receipt show', dict(sales_receipt=receipt['id']), company=COMPANY)
                # The actual posting fixture already captures the UF account identity.
                with runner.session() as s:
                    uf = s.company.raw.execute("SELECT id FROM accounts WHERE system_role='undeposited_funds'").fetchone()[0]
                wire['source_action'] = dict(kind=request.param, input=dict(sales_receipt=receipt['id'],
                    expected_version=2, memo='Coordinate receipt correction', customer=sales['customer'],
                    payment_method=method, deposit_to=uf, billing_address=dict(line1='Captured billing address'),
                    custom_fields={custom:'Captured custom text'}, custom_field_kinds={custom:'text'}))
                wire['replacement']['document']['sources'] = [dict(source_type='payment', source=payment['id'], expected_version=2),
                    dict(source_result=True, source=receipt['id'])]
                total = 17200
            ctx = Context.new(Interface.python, 'Coordinate event producer', reason='Correct captured source')
            with runner.session() as s:
                proposed = CoordinateInput.model_validate(wire)
                prepared = prepare(s, ctx, proposed)
                # Both private freshness inputs are actually supplied, so their
                # removal from display and supplied-field metadata has a witness.
                proposed = proposed.model_copy(update={'expected_facts_fingerprint': prepared.facts_fingerprint})
                output = persistence.execute(s, ctx, prepare(s, ctx, proposed))
                assert output.current.revision_bank_total == total
                cur = s.company.raw.execute('SELECT * FROM deposit_operations WHERE id=?', (output.operation_id,))
                raw = dict(zip((x[0] for x in cur.description), cur.fetchone(), strict=True))
                event = raw['audit_event_id']
                seq = s.company.raw.execute('SELECT seq FROM audit_events WHERE id=?', (event,)).fetchone()[0]
                entries = s.company.raw.execute('SELECT id,record_type,record_id,action FROM audit_entries WHERE event_id=? ORDER BY id', (event,)).fetchall()
                requirements = payment_authority.event_requirements(s.company, event)
                assert ('ledger.read', 'member') in requirements
        company = client.company.show(company=COMPANY)
        yield dict(root=world['root'], path=Path(company['path'])/'company.db', cid=company['company_id'],
            raw=raw, event=event, seq=seq, entries=entries, total=total, method=method, custom=custom,
            customer=sales['customer'], family=request.param)
    finally:
        generator.close()


@contextmanager
def hosted(c):
    original = (hashlib.sha256(c['path'].read_bytes()).hexdigest(), storage(c['path']))
    host = Host(c['root'], version=client_version())
    host.start()
    try:
        yield host
    finally:
        assert host._readers_attached == 0
        host.stop()
        assert (hashlib.sha256(c['path'].read_bytes()).hexdigest(), storage(c['path'])) == original


def read(c, host, *, event=True):
    ctx = Context.new('http', 'Coordinate audit hosted reader')
    with binding.hosted_reader(host, OSBinding.capture(host, os_login()), request_id=ctx.request_id) as reader:
        open_selected(reader, projection.HistorySelection(mode='show', company=c['cid'], event=c['event']), ctx)
        audience = projection.make_audience(reader)
        if event:
            return projection.project_event(audience, c['event'], company=c['cid'])
        # Test the descriptor path separately, with actual mandatory owner admission.
        payment_authority.authorize_event(reader.session, c['event'])
        audience.require(c['cid'], payment_authority.event_requirements(reader.session.company, c['event']))
        captured = legacy.decode_company_snapshot(producer='deposit coordinate', record_type='deposit_operation',
            action='create', snapshot=c['raw'])
        return projection._disclose_company(audience, c['cid'], captured, cutoff=c['seq']).model_dump(mode='json', by_alias=True)


@contextmanager
def denied(c, host, capabilities):
    uid = Config.load(c['root']/'config.toml').user_table(os_login())['user_id']
    def apply(values):
        credential = OSBinding.capture(host, os_login())
        with host._commit_hooks.operation('dispatch.apply', host._hub):
            with binding.hosted_operation(host, credential, request_id=CONTEXT.request_id, purpose='apply') as operation:
                version = host._hub.raw.execute('SELECT version FROM memberships WHERE user_id=? AND scope_type=? AND scope_id=?',
                    (uid, 'company', c['cid'])).fetchone()[0]
                operation.apply(admin.PutMembership(uid, ScopeKey('company', c['cid']), admin.Version(version),
                    'owner', denies=values), audit=CONTEXT)
                host._commit_hooks.commit(host._hub, 'dispatch.apply')
    host.submit(lambda: apply(capabilities))
    try:
        yield
    finally:
        host.submit(lambda: apply(()))


def test_real_coordinate_whole_event_covers_all_touched_entries(coordinate):
    c = coordinate
    with hosted(c) as host:
        result = read(c, host)
        assert result is not None and result.command == 'deposit coordinate'
        expected = {(row[0], row[1], row[2], row[3]) for row in c['entries']}
        assert {(e.id, e.identity.kind, e.identity.id, e.action) for e in result.entries} == expected
        operations = [e for e in result.entries if e.identity.kind == 'deposit_operation']
        assert len(operations) == 1
        actual = operations[0].after.effect_snapshot
        assert actual.current.revision_bank_total == c['total']
        assert actual.effect.source.action.kind == c['family']
        assert actual.operation_id == c['raw']['id']
        assert len(result.entries) > 1
        original_request = json.loads(c['raw']['request_snapshot'])
        internal = {'dependency_guard', 'expected_facts_fingerprint'}
        assert internal <= set(original_request['provided_fields'])
        shown_request = operations[0].after.request_snapshot
        assert internal.isdisjoint(shown_request.provided_fields)
        assert internal.isdisjoint(shown_request.input.model_dump(mode='json'))


def test_fresh_mandatory_source_denial_hides_entire_coordinate_event(coordinate):
    c = coordinate
    with hosted(c) as host:
        # Independent owner baseline; a missing decoder cannot mask this gate.
        assert read(c, host, event=False)['effect_snapshot']['current']['revision_bank_total'] == c['total']
        with denied(c, host, ('ledger.read',)):
            assert read(c, host) is None
        assert read(c, host, event=False)['effect_snapshot']['current']['revision_bank_total'] == c['total']


def test_coordinate_current_field_denies_preserve_amounts(coordinate, monkeypatch):
    c = coordinate
    with hosted(c) as host:
        allowed = read(c, host, event=False)
        with denied(c, host, ('account', 'payment-method', 'custom-field', 'customer')):
            from bookflow.hub import audit_projection_deposit_coordinate as coordinate_codec
            from bookflow.hub import audit_projection_deposit_drafts as draft_codec
            # A granted requirement in an earlier map must not replace the
            # existing denied custom-field requirement in the coordinate map.
            with monkeypatch.context() as patch:
                patch.setitem(draft_codec.FIELD_REQUIREMENTS,
                    (coordinate_codec.CoordinateSourceEvidence, 'custom_changes'), ('employee',))
                masked = read(c, host, event=False)
        for value in (allowed, masked):
            current = value['effect_snapshot']['current']
            assert current['revision_bank_total'] == c['total'] and current['revision_cash_back'] == 500
        before = allowed['effect_snapshot']['effect']
        after = masked['effect_snapshot']['effect']
        for key in ('financial', 'reversal'):
            for amount in ('posting_total', 'subtotal', 'bank_total', 'cash_back', 'cells'):
                assert after['deposit'][key][amount] == before['deposit'][key][amount]
        if c['family'] == 'sales_receipt_update':
            original = before['source']['action']['input']
            hidden = after['source']['action']['input']
            assert original['customer'] == c['customer'] and original['payment_method'] == c['method']
            assert original['deposit_to'] is not None and original['billing_address']['line1'] == 'Captured billing address'
            assert original['custom_fields'] == {c['custom']:'Captured custom text'}
            assert original['custom_field_kinds'] == {c['custom']:'text'}
            for key in ('customer', 'payment_method', 'deposit_to', 'billing_address', 'custom_fields', 'custom_field_kinds'):
                assert hidden[key] is None
            assert before['source']['custom_changes']
            assert after['source']['custom_changes'] is None
            request = masked['request_snapshot']['input']['source_action']['input']
            for key in ('customer', 'payment_method', 'deposit_to', 'billing_address', 'custom_fields', 'custom_field_kinds'):
                assert request[key] is None
        else:
            assert before['source']['action']['input']['payment_method'] == c['method']
            assert after['source']['action']['input']['payment_method'] is None
            assert after['source']['payment_effect']['current']['received_minor_units'] == 12000
