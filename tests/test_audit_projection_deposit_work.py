"""Work-billed cash keeps its source authority in ordinary deposit history."""
import copy
from pathlib import Path

import pytest

from bookflow.company import payment_authority
from bookflow.core import identity_admin_binding as binding
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core.publication_audit import open_selected
from bookflow.hub import audit_projection as projection, identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from tests.permission_admin_support import CONTEXT
from tests.test_audit_projection_activity import world, storage
from tests.test_deposit_draft_financial import run_private, financial
from tests.test_deposit_sources import uf
from tests.test_service_sales_lifecycle import COMPANY, sale
from tests.test_work_billing_lifecycle import accepted, bill


@pytest.fixture(scope='module')
def work_deposit(world):
    client = world['client']
    facts = sale.__wrapped__(client)
    work = accepted(client, facts)
    receipt = bill(client, work, verb='sales-receipt', deposit_to=uf(client),
                   payment_method='Check', amount_received='24.68')
    bank = client.account.create(name='Work deposit bank', type='bank', company=COMPANY)['id']
    document = dict(mode='inline', deposit_to=bank, date='2026-06-03',
                    sources=[dict(source_type='sales_receipt', source=receipt['id'], expected_version=1)])
    with pytest.MonkeyPatch.context() as patch:
        run = run_private.__wrapped__(client, patch)
        posted = financial(run, dict(operation_key='work-deposit-post', document=document))
        replacement = copy.deepcopy(document)
        replacement.update(number=posted.current.number, memo='Corrected deposit memo',
                           cash_back=None, custom_fields={}, expected_custom_field_kinds={}, additional=[])
        replacement['sources'][0]['expected_version'] = 2
        updated = financial(run, dict(operation_key='work-deposit-update', deposit=posted.current.id,
                            expected_version=1, document=replacement), 'update')
        voided = financial(run, dict(operation_key='work-deposit-void', deposit=posted.current.id,
                           expected_version=updated.current.version), 'void')
        def inspect(s, ctx):
            result = []
            for output in (posted, updated, voided):
                event = s.company.raw.execute('SELECT audit_event_id FROM deposit_operations WHERE id=?',
                                             (output.operation_id,)).fetchone()[0]
                result.append((output.operation_id, event))
            assert s.company.raw.execute('SELECT count(*) FROM work_billing_allocations WHERE transaction_id=?',
                                         (receipt['id'],)).fetchone()[0] > 0
            return result
        events = run(inspect)
    company = client.company.show(company=COMPANY)
    return dict(root=world['root'], cid=company['company_id'], path=Path(company['path'])/'company.db',
                events=events, source=receipt['id'], deposit=posted.current.id)


def test_all_ordinary_operation_roots_retain_work_source(work_deposit):
    """The specific operation walker must work even when other event rows also do."""
    from bookflow.storage.engine import open_database
    c = work_deposit
    before = storage(c['path'])
    with open_database(c['path'], writable=False) as db:
        cohort = payment_authority._EventCohort(db, [event for _, event in c['events']])
        for operation, event in c['events']:
            roots = payment_authority.record_transactions(db, 'deposit_operation', operation)
            assert roots == {c['deposit'], c['source']}
            assert cohort._walk(('deposit_operation', operation)) == roots
            expected = (('ledger.read', 'member'), ('customer-work', 'member'))
            assert payment_authority.requirements(db, roots) == expected
            assert payment_authority.event_requirements(db, event) == expected
            assert cohort.requirements(event) == expected
    assert storage(c['path']) == before


@pytest.mark.parametrize('denied', [False, True], ids=['allowed', 'work-denied'])
def test_work_source_controls_real_deposit_event(work_deposit, denied):
    c = work_deposit
    operation_id, event = c['events'][0]
    before = storage(c['path'])
    host = Host(c['root'], version=client_version())
    host.start()
    version = None
    try:
        credential = OSBinding.capture(host, os_login())
        uid = Config.load(c['root']/'config.toml').user_table(os_login())['user_id']
        def membership(v, denies):
            with host._commit_hooks.operation('dispatch.apply', host._hub):
                with binding.hosted_operation(host, credential, request_id=CONTEXT.request_id, purpose='apply') as op:
                    op.apply(admin.PutMembership(uid, ScopeKey('company', c['cid']), admin.Version(v),
                                                 'owner', denies=denies), audit=CONTEXT)
                    host._commit_hooks.commit(host._hub, 'dispatch.apply')
        if denied:
            version = host.submit(lambda: host._hub.raw.execute(
                'SELECT version FROM memberships WHERE user_id=? AND scope_type="company" AND scope_id=?',
                (uid, c['cid'])).fetchone()[0])
            host.submit(lambda: membership(version, ('customer-work',)))
        ctx = Context.new('http', 'work-deposit-history')
        with binding.hosted_reader(host, OSBinding.capture(host, os_login()), request_id=ctx.request_id) as reader:
            open_selected(reader, projection.HistorySelection(mode='show', company=c['cid'], event=event), ctx)
            audience = projection.make_audience(reader)
            audience.require(c['cid'], (('ledger.read', 'member'),))
            result = projection.project_event(audience, event, company=c['cid'])
            if denied:
                assert result is None
            else:
                assert result is not None
                assert any(e.identity.kind == 'deposit_operation' and e.identity.id == operation_id
                           for e in result.entries)
        assert host._readers_attached == 0
        assert storage(c['path']) == before
    finally:
        if denied and version is not None:
            host.submit(lambda: membership(version + 1, ()))
        host.stop()
