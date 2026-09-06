"""Retained complete payment graphs invoke current pure authority, never plans."""
import json
import pytest

from bookflow.adapters.mcp.runtime import Runtime
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from tests.test_mcp_runtime import credential
from tests.test_row3_host import hosted as host_fixture
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_work_billing_lifecycle import accepted, bill
from tests.test_payment_receipts import method


@pytest.fixture
def payment_graph(client, sale):
    plain = client.run('invoice post', dict(customer=sale['customer'], date='2026-06-01',
        lines=[dict(item=sale['item'], quantity='1', unit_price='1.00')]), company=COMPANY)
    invoice = bill(client, accepted(client, sale))
    paid = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='1.00',
        payment_method=method(client), operation_key='publication-graph', applications=dict(mode='inline', items=[
            dict(invoice=plain['id'], expected_version=plain['version'], amount='0.50'),
            dict(invoice=invoice['id'], expected_version=invoice['version'], amount='0.50')])), company=COMPANY)
    selection = client.run('payment selection create', dict(mode='new_receipt', customer=sale['customer'], date='2026-06-03'), company=COMPANY)
    selection = client.run('payment selection update', dict(selection=selection['id'], expected_version=selection['version'],
        set_items=[dict(invoice=invoice['id'], expected_version=invoice['version'] + 1, amount='0.01')]), company=COMPANY)
    selection = client.run('payment selection clear', dict(selection=selection['id'], expected_version=selection['version']), company=COMPANY)
    return dict(plain=plain['id'], invoice=invoice['id'], payment=paid['id'], selection=selection['id'],
                application=paid['effect']['applications'][1]['application_id'], operation_key='publication-graph')


@pytest.fixture
def hosted(root, payment_graph):
    yield from host_fixture.__wrapped__(root)


@pytest.mark.parametrize('name,raw', [
    ('payment show', lambda g: {'payment': g['payment']}),
    ('payment operation items', lambda g: {'operation_key': g['operation_key'], 'kind': 'effect_applications', 'limit': 1}),
    ('payment selection show', lambda g: {'selection': g['selection']}),
    ('payment selection items', lambda g: {'selection': g['selection'], 'limit': 1}),
    ('invoice settlement', lambda g: {'invoice': g['invoice'], 'limit': 1}),
    ('application show', lambda g: {'application': g['application']}),
])
def test_retained_graph_denies_current_work_authority_without_reexecution(hosted, payment_graph, monkeypatch, name, raw):
    from bookflow.company import payment_authority
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    cmd = registry.get(name)
    intent = runtime.admit(cmd, Context.new(Interface.mcp, 'payment-publication'), cred,
                           hosted.company_id, 'option', False)
    runtime.prepare_json(intent, raw(payment_graph), cred)
    result = runtime.execute_json(intent, cred)
    runtime.intents.delivery(intent)
    runtime.intents.finish(intent, receipt=json.dumps(result).encode(), publication=result.permit.retained())
    assert result.permit.projection['payment_roots']
    if name == 'payment operation items':
        assert result['items'][0]['invoice_id'] == payment_graph['plain']
        assert result['next_cursor'] is not None
    if name == 'payment selection items':
        assert result['items'] == []
    import sqlite3
    def counts():
        with sqlite3.connect((hosted.root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
            relative = db.execute('SELECT path FROM companies WHERE id=?', (hosted.company_id,)).fetchone()[0]
        with sqlite3.connect((hosted.root / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
            return [db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in
                    ('audit_events', 'audit_entries', 'payment_operations', 'posting_lines', 'applications')]
    before = counts()
    seen = []
    original = payment_authority.require_resource
    def reject_work(session, resource, role):
        seen.append((resource, role))
        if resource == 'customer-work':
            raise BookflowError('E_PERMISSION', details={'capability': resource})
        return original(session, resource, role)
    def forbidden(*args, **kwargs):
        raise AssertionError('Publication planned/executed/recovered the command')
    monkeypatch.setattr(cmd, 'plan', forbidden)
    from bookflow.company import payment_operations
    monkeypatch.setattr(payment_operations, 'recover', forbidden)
    if cmd.permanent_recovery:
        monkeypatch.setattr(cmd, 'permanent_recovery', forbidden)
    monkeypatch.setattr(payment_authority, 'require_resource', reject_work)
    with pytest.raises(BookflowError) as caught:
        runtime.lookup(intent.reference, cred)
    assert caught.value.code == 'E_PERMISSION'
    assert caught.value.details['outcome'] == 'unknown'
    assert ('customer-work', 'member') in seen
    monkeypatch.setattr(payment_authority, 'require_resource', original)
    # Same reference becomes readable again after authority is restored; it is
    # the exact retained result, not a second financial execution.
    assert json.loads(runtime.lookup(intent.reference, cred).receipt) == result

    assert counts() == before


def test_committed_payment_denial_keeps_one_effect_and_reference_cannot_reexecute(hosted, payment_graph, monkeypatch):
    from bookflow.company import payment_authority
    from bookflow.core.publication import PublicationPermit
    def update_events():
        query = {'command': 'payment update', 'limit': 200}
        events = {}
        while True:
            page = hosted.ok('audit.list', query, company=hosted.company_id)
            events.update((event['id'], event) for event in page['items'])
            if not page.get('next_before'):
                return events
            query = dict(query, before=page['next_before'])
    before = update_events()
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    cmd = registry.get('payment update')
    intent = runtime.admit(cmd, Context.new(Interface.mcp, 'payment-postcommit', reason='Postcommit boundary'), cred,
                           hosted.company_id, 'option', False)
    runtime.prepare_json(intent, dict(payment=payment_graph['payment'], expected_version=1,
        operation_key='publication-postcommit', memo='Committed before denial',
        invoice_versions=[dict(invoice=payment_graph['invoice'], expected_version=2),
                          dict(invoice=payment_graph['plain'], expected_version=2)]), cred)
    finish = PublicationPermit.finish
    original = payment_authority.require_resource
    def reject_work(session, resource, role):
        if resource == 'customer-work':
            raise BookflowError('E_PERMISSION')
        return original(session, resource, role)
    def after_commit(permit, session, **kwargs):
        finish(permit, session, **kwargs)
        if permit.cmd.name == 'payment update' and permit.committed:
            monkeypatch.setattr(payment_authority, 'require_resource', reject_work)
    monkeypatch.setattr(PublicationPermit, 'finish', after_commit)
    with pytest.raises(BookflowError) as caught:
        runtime.execute_json(intent, cred)
    assert caught.value.code == 'E_PERMISSION' and caught.value.details['outcome'] == 'unknown'
    monkeypatch.setattr(payment_authority, 'require_resource', original)
    assert runtime.execute_json(intent, cred) is None
    operation = hosted.ok('payment.operation.show', {'operation_key': 'publication-postcommit'}, company=hosted.company_id)
    assert operation['original']['current']['version'] == 2
    after = update_events()
    assert {key: after[key] for key in before} == before
    added = [event for key, event in after.items() if key not in before]
    assert len(added) == 1 and added[0]['reason'] == 'Postcommit boundary'


def test_plain_invoice_does_not_inherit_other_invoice_work_permission(hosted, payment_graph, monkeypatch):
    from bookflow.company import payment_authority
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    cmd = registry.get('invoice show')
    intent = runtime.admit(cmd, Context.new(Interface.mcp, 'plain-invoice'), cred, hosted.company_id, 'option', False)
    runtime.prepare_json(intent, {'invoice': payment_graph['plain']}, cred)
    result = runtime.execute_json(intent, cred)
    runtime.intents.delivery(intent)
    runtime.intents.finish(intent, receipt=json.dumps(result).encode(), publication=result.permit.retained())
    original = payment_authority.require_resource
    seen = []
    def reject_work(session, resource, role):
        seen.append(resource)
        if resource == 'customer-work':
            raise BookflowError('E_PERMISSION')
        return original(session, resource, role)
    monkeypatch.setattr(payment_authority, 'require_resource', reject_work)
    # Core invoice show discloses its own settlement summary; it does not expose
    # another invoice's work facts simply because one payment paid both invoices.
    direct = hosted.ok('invoice.show', {'invoice': payment_graph['plain']}, company=hosted.company_id)
    assert direct == result
    assert json.loads(runtime.lookup(intent.reference, cred).receipt) == result
    assert 'ledger.read' in seen and 'customer-work' not in seen
