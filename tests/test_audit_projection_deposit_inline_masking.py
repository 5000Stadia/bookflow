"""Real inline/replacement receipts and captured edit origins under current denies.

Private financial producers; actual governed hosted reader. No public activation.
"""
import copy
import json
from pathlib import Path

import pytest

# These cases include real producer setup and governed membership commits.
# Independent review measured 76s for the first case including its fixture.
# This is a bounded correctness-test ceiling, not a product latency allowance.
pytestmark = pytest.mark.timeout(120)

from bookflow.company import deposit_drafts as drafts, deposit_draft_models as models
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core import identity_admin_binding as binding
from bookflow.core.publication_audit import open_selected
from bookflow.hub import audit_projection as projection, audit_projection_legacy as views
from bookflow.hub import identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from tests.permission_admin_support import CONTEXT
from tests.test_audit_projection_activity import world, storage
from tests.test_deposit_draft_financial import run_private, financial
from tests.test_deposit_drafts import cash
from tests.test_service_sales_lifecycle import COMPANY, sale


@pytest.fixture(scope='module')
def captures(world):
    client = world['client']
    sales = sale.__wrapped__(client)
    source = cash.__wrapped__(client, sales)
    bank = client.account.create(name='Inline mask bank', type='bank', company=COMPANY)['id']
    till = client.account.create(name='Inline mask till', type='other_current_asset', company=COMPANY)['id']
    vendor = client.vendor.create(name='Inline mask vendor', company=COMPANY)['id']
    method = client.run('payment-method create', dict(name='Inline extra cash', kind='cash'), company=COMPANY)['id']
    custom = client.run('custom-field create', dict(name='Inline private text', kind='text', scopes=['deposit']), company=COMPANY)['id']
    document = dict(mode='inline', deposit_to=bank, date='2026-06-03', memo='Original inline',
        cash_back=dict(account=till, amount='1.00', memo='Till'),
        custom_fields={custom: 'Original value'}, expected_custom_field_kinds={custom: 'text'},
        sources=[source], additional=[dict(received_from=dict(kind='vendor', id=vendor),
            from_account=sales['income'], amount='1.25', payment_method=method)])
    with pytest.MonkeyPatch.context() as patch:
        run = run_private.__wrapped__(client, patch)
        posted = financial(run, dict(operation_key='inline-mask-post', document=document))
        replacement = copy.deepcopy(document)
        replacement.update(number=posted.current.number, memo='Replacement inline')
        replacement['sources'][0]['expected_version'] = 2
        replacement['additional'][0].update(line_id=posted.effect.financial.intent.additional[0].row_id, amount='2.25')
        replacement['cash_back']['amount'] = '2.00'
        replacement['custom_fields'][custom] = 'Replacement value'
        updated = financial(run, dict(operation_key='inline-mask-update', deposit=posted.current.id,
            expected_version=posted.current.version, document=replacement), 'update')
        assert updated.changed and updated.current.version == 2
        assert updated.effect.reversal is not None
        edit = run(lambda s, ctx: drafts.run(s, ctx, models.DraftCreate(
            from_deposit=updated.current.id, expected_version=updated.current.version), 'create'))

        def stored(s, ctx):
            rows = []
            for result in (posted, updated):
                cur = s.company.raw.execute('SELECT * FROM deposit_operations WHERE id=?', (result.operation_id,))
                row = dict(zip((col[0] for col in cur.description), cur.fetchone(), strict=True))
                seq = s.company.raw.execute('SELECT seq FROM audit_events WHERE id=?', (row['audit_event_id'],)).fetchone()[0]
                rows.append((row, seq))
            manifest = json.loads(s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE id=?', (edit.revision_id,)).fetchone()[0])
            event = s.company.raw.execute('SELECT audit_event_id FROM deposit_draft_revisions WHERE id=?', (edit.revision_id,)).fetchone()[0]
            seq = s.company.raw.execute('SELECT seq FROM audit_events WHERE id=?', (event,)).fetchone()[0]
            return rows, manifest, (event, seq)
        rows, manifest, manifest_event = run(stored)
    company = client.company.show(company=COMPANY)
    return dict(root=world['root'], cid=company['company_id'], path=Path(company['path'])/'company.db',
        rows=rows, manifest=manifest, manifest_event=manifest_event, documents=(document, replacement), bank=bank, till=till,
        income=sales['income'], method=method, custom=custom)


@pytest.mark.parametrize('case', ('post', 'update', 'manifest'))
def test_inline_and_replacement_current_denies_preserve_money_and_storage(captures, case):
    c = captures
    before = storage(c['path'])
    host = Host(c['root'], version=client_version())
    host.start()
    try:
        cred = OSBinding.capture(host, os_login())
        def project(current):
            if case == 'manifest':
                event, seq = c['manifest_event']
                value = views.DepositAuditManifest.model_validate(c['manifest'])
            else:
                raw, seq = c['rows'][0 if case == 'post' else 1]
                event = raw['audit_event_id']
                value = views.DepositOperationView.model_validate(raw)
            ctx = Context.new('http', 'inline-deposit-mask')
            with binding.hosted_reader(host, current, request_id=ctx.request_id) as reader:
                open_selected(reader, projection.HistorySelection(mode='show', company=c['cid'], event=event), ctx)
                audience = projection.make_audience(reader)
                audience.require(c['cid'], (('ledger.read', 'member'),))
                return projection._disclose_company(audience, c['cid'], value, cutoff=seq).model_dump(mode='json', by_alias=True)
        allowed = project(cred)
        uid = Config.load(c['root']/'config.toml').user_table(os_login())['user_id']
        version = host.submit(lambda: host._hub.raw.execute(
            "SELECT version FROM memberships WHERE user_id=? AND scope_type='company' AND scope_id=?",
            (uid, c['cid'])).fetchone()[0])
        def membership(expected, denies):
            with host._commit_hooks.operation('dispatch.apply', host._hub):
                with binding.hosted_operation(host, cred, request_id=CONTEXT.request_id, purpose='apply') as operation:
                    operation.apply(admin.PutMembership(uid, ScopeKey('company', c['cid']), admin.Version(expected),
                        'owner', denies=denies), audit=CONTEXT)
                    host._commit_hooks.commit(host._hub, 'dispatch.apply')
        host.submit(lambda: membership(version, ('account', 'payment-method', 'custom-field')))
        try:
            denied = project(OSBinding.capture(host, os_login()))
            if case == 'manifest':
                assert_manifest(c, allowed, denied)
            else:
                assert_operation(c, 0 if case == 'post' else 1, allowed, denied)
        finally:
            host.submit(lambda: membership(version + 1, ()))
        assert storage(c['path']) == before and host._readers_attached == 0
    finally:
        host.stop()


def assert_operation(c, index, yes, no):
    raw_input = json.loads(c['rows'][index][0]['request_snapshot'])
    expected_presence = ['document'] if index == 0 else ['deposit', 'document', 'expected_version']
    assert 'dependency_guard' in raw_input['provided_fields']
    for projected in (yes, no):
        assert projected['request_snapshot']['provided_fields'] == expected_presence
        assert set(projected['request_snapshot']['input']) == set(expected_presence)
        assert 'resolved_identity_map' not in projected['request_snapshot']
    original = yes['request_snapshot']['input']['document']
    hidden = no['request_snapshot']['input']['document']
    assert original['deposit_to'] == c['bank'] and original['cash_back']['account'] == c['till']
    assert original['additional'][0]['payment_method'] == c['method']
    assert original['custom_fields'] == c['documents'][index]['custom_fields']
    assert original['expected_custom_field_kinds'] == {c['custom']: 'text'}
    assert hidden['deposit_to'] is None and hidden['cash_back']['account'] is None
    assert hidden['additional'][0]['from_account'] is None and hidden['additional'][0]['payment_method'] is None
    assert hidden['custom_fields'] is None and hidden['expected_custom_field_kinds'] is None
    for field in ('amount', 'memo'):
        assert hidden['cash_back'][field] == original['cash_back'][field]
    assert hidden['additional'][0]['amount'] == original['additional'][0]['amount']
    for projected in (yes, no):
        current = projected['effect_snapshot']['current']
        assert (current['revision_subtotal'], current['revision_bank_total'], current['revision_cash_back']) == (6125 + index*100, 6025, 100 + index*100)
    effects = no['effect_snapshot']['effect']
    assert all(row['account_id'] is None for row in effects['bank_effects'])
    for key in ('financial', 'reversal'):
        if effects[key] is None:
            continue
        money = effects[key]
        original_money = yes['effect_snapshot']['effect'][key]
        assert {k: money[k] for k in ('posting_total', 'subtotal', 'bank_total', 'cash_back', 'cells')} == {k: original_money[k] for k in ('posting_total', 'subtotal', 'bank_total', 'cash_back', 'cells')}
        for account in (money['intent']['bank'], money['intent']['cash_back']['account'],
                        money['intent']['additional'][0]['account']):
            assert all(account[field] is None for field in
                ('id', 'name', 'full_name', 'number', 'type', 'normal_balance', 'system_role', 'active'))
            assert account == dict.fromkeys(('id', 'name', 'full_name', 'number', 'type',
                'normal_balance', 'system_role', 'active')) | {'currency': 'USD'}
        assert money['intent']['additional'][0]['payment_method'] is None


def assert_manifest(c, allowed_manifest, denied_manifest):
    assert allowed_manifest['header']['origins']['deposit_to'] == 'source'
    assert allowed_manifest['header']['cash_back']['origins']['account'] == 'source'
    assert allowed_manifest['additional'][0]['origins']['payment_method'] == 'source'
    assert allowed_manifest['additional'][0]['origins']['from_account'] == 'source'
    assert allowed_manifest['header']['custom_fields'][c['custom']]['canonical_text'] == 'Replacement value'
    assert denied_manifest['header']['custom_fields'] is None
    assert 'deposit_to' not in denied_manifest['header']['origins']
    assert 'account' not in denied_manifest['header']['cash_back']['origins']
    assert not {'from_account', 'payment_method'} & set(denied_manifest['additional'][0]['origins'])
    assert denied_manifest['summary'] == allowed_manifest['summary']
    assert denied_manifest['header']['cash_back']['account'] is None
