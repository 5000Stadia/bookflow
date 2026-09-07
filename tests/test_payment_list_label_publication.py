"""Query publication still rechecks both returned roots and empty-page access."""
import json
import pytest
from bookflow.adapters.mcp.runtime import Runtime
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow import BookflowError
from tests.test_mcp_runtime import credential
from tests.test_mcp_payment_publication import payment_graph, hosted
from tests.test_service_sales_lifecycle import sale


@pytest.mark.parametrize('empty', [False, True])
def test_query_retained_work_access_and_roots(hosted, payment_graph, monkeypatch, empty):
    from bookflow.company import payment_authority
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    cmd = registry.get('payment query')
    intent = runtime.admit(cmd, Context.new(Interface.mcp, 'labels-publication'), cred, hosted.company_id, 'option', False)
    runtime.prepare_json(intent, {'q':'absent-label-fixture'} if empty else {'limit':200}, cred)
    result = runtime.execute_json(intent, cred)
    roots = result.permit.projection['payment_roots']
    assert any(root[0] == 'work_access' for root in roots)
    if empty:
        assert result['items'] == []
    else:
        assert any(root[1] == payment_graph['payment'] for root in roots)
    runtime.intents.delivery(intent)
    runtime.intents.finish(intent, receipt=json.dumps(result).encode(), publication=result.permit.retained())
    original = payment_authority.require_resource
    def reject(session, resource, role):
        if resource == 'customer-work':
            raise BookflowError('E_PERMISSION')
        return original(session, resource, role)
    monkeypatch.setattr(payment_authority, 'require_resource', reject)
    with pytest.raises(BookflowError) as caught:
        runtime.lookup(intent.reference, cred)
    assert caught.value.code == 'E_PERMISSION'
    monkeypatch.setattr(payment_authority, 'require_resource', original)
    assert json.loads(runtime.lookup(intent.reference, cred).receipt) == result


def test_query_filters_historical_work_before_counts_and_decode(client, payment_graph, monkeypatch):
    from bookflow.company import payment_authority, payment_preparation
    from tests.test_service_sales_lifecycle import COMPANY
    from tests.test_payment_gate_b_oracles import allrows
    payment = client.run('payment show', {'payment':payment_graph['payment']}, company=COMPANY)
    # Reuse the ordinary linked-work graph and public unapply, retaining its history.
    import sqlite3
    from tests.test_row8_journal import database_path
    with sqlite3.connect(database_path(client)) as db:
        apps = db.execute("SELECT id,paid_transaction_id FROM applications WHERE paying_transaction_id=? AND kind='apply'", (payment['id'],)).fetchall()
    versions = {invoice:client.run('invoice show', {'invoice':invoice}, company=COMPANY)['version'] for _,invoice in apps}
    client.run('payment unapply', {'payment':payment['id'], 'expected_version':payment['version'],
        'operation_key':'label-historical-unapply', 'applications':[{'application_id':app,'invoice_expected_version':versions[invoice]} for app,invoice in apps]}, company=COMPANY, reason='Remove allocations')
    request = {'number':payment['number'], 'limit':1}
    assert client.run('payment query', request, company=COMPANY)['total_count'] == 1
    before = allrows(client)
    original = payment_authority.require_resource
    def denied(session, resource, role):
        if resource == 'customer-work':
            raise BookflowError('E_PERMISSION')
        return original(session, resource, role)
    monkeypatch.setattr(payment_authority, 'require_resource', denied)
    def forbidden(*args, **kwargs):
        raise AssertionError('Filtered payment profile was decoded')
    monkeypatch.setattr(payment_preparation, '_payment_query_labels', forbidden)
    result = client.run('payment query', request, company=COMPANY)
    assert result['items'] == [] and result['total_count'] == 0 and result['next_cursor'] is None
    assert allrows(client) == before
