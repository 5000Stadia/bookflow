"""Real deposit events traverse owner admission and complete entry projection."""
import json
import pytest
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core import identity_admin_binding as binding
from bookflow.core.publication_audit import open_selected
from bookflow.hub import audit_projection as projection
from tests.test_audit_projection_deposit_inline_masking import captures, world, storage


@pytest.fixture(scope="module")
def event_captures(captures, world):
    c = captures
    from tests.test_deposit_draft_financial import run_private, financial
    rows = list(c['rows'])
    with pytest.MonkeyPatch.context() as patch:
        run = run_private.__wrapped__(world['client'],patch)
        current = json.loads(rows[-1][0]['effect_snapshot'])['current']
        voided = financial(run,dict(operation_key='event-void',deposit=current['id'],expected_version=current['version']),'void')
        def stored(session,ctx):
            cursor = session.company.raw.execute('SELECT * FROM deposit_operations WHERE id=?',(voided.operation_id,))
            raw = dict(zip((column[0] for column in cursor.description),cursor.fetchone(),strict=True))
            return raw, session.company.raw.execute('SELECT seq FROM audit_events WHERE id=?',(raw['audit_event_id'],)).fetchone()[0]
        rows.append(run(stored))
    return c, rows


@pytest.mark.parametrize('access', ['allowed', 'ledger-denied'])
@pytest.mark.parametrize('row_index', [0, 1, 2], ids=['post', 'update', 'void'])
def test_real_inline_deposit_events_include_operation_receipts(event_captures, row_index, access):
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.permission_admin_support import CONTEXT
    c, all_rows = event_captures
    rows = [all_rows[row_index]]
    before = storage(c['path'])
    host = Host(c['root'], version=client_version())
    host.start()
    try:
        credential = OSBinding.capture(host, os_login())
        if access == 'allowed':
            for raw, _ in rows:
                ctx = Context.new('http','deposit-event-projection')
                with binding.hosted_reader(host,credential,request_id=ctx.request_id) as reader:
                    open_selected(reader,projection.HistorySelection(mode='show',company=c['cid'],event=raw['audit_event_id']),ctx)
                    audience = projection.make_audience(reader)
                    result = projection.project_event(audience,raw['audit_event_id'],company=c['cid'])
                    assert result is not None
                    operation = [entry for entry in result.entries if entry.identity.kind == 'deposit_operation']
                    assert len(operation) == 1
                    assert operation[0].after.operation_key == raw['operation_key']
                    expected = json.loads(raw['effect_snapshot'])['current']
                    assert operation[0].after.effect_snapshot.current.model_dump(mode='json') == expected
                    assert not any(entry.identity.kind == 'deposit_operation_item' for entry in result.entries)
        else:
            uid = Config.load(c['root']/'config.toml').user_table(os_login())['user_id']
            def set_denies(version, denies):
                with host._commit_hooks.operation('dispatch.apply',host._hub):
                    with binding.hosted_operation(host,credential,request_id=CONTEXT.request_id,purpose='apply') as operation:
                        operation.apply(admin.PutMembership(uid,ScopeKey('company',c['cid']),admin.Version(version),'owner',denies=denies),audit=CONTEXT)
                        host._commit_hooks.commit(host._hub,'dispatch.apply')
            membership_version = host.submit(lambda: host._hub.raw.execute(
                'SELECT version FROM memberships WHERE user_id=? AND scope_id=? AND scope_type="company"', (uid, c['cid'])).fetchone()[0])
            host.submit(lambda: set_denies(membership_version, ('ledger.read',)))
            try:
                for raw, _ in rows:
                    ctx = Context.new('http','denied-deposit-event')
                    with binding.hosted_reader(host,OSBinding.capture(host,os_login()),request_id=ctx.request_id) as reader:
                        open_selected(reader,projection.HistorySelection(mode='show',company=c['cid'],event=raw['audit_event_id']),ctx)
                        audience = projection.make_audience(reader)
                        assert projection.project_event(audience,raw['audit_event_id'],company=c['cid']) is None
            finally:
                host.submit(lambda: set_denies(membership_version + 1, ()))
        assert storage(c['path']) == before
        assert host._readers_attached == 0
    finally:
        host.stop()
