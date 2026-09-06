"""Existing billed-sale conditional authority is reused for retained publication."""
import json
import pytest

from bookflow.adapters.mcp.runtime import Runtime
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from tests.test_mcp_runtime import credential
from tests.test_row3_host import hosted as host_fixture
from tests.test_service_sales_lifecycle import sale
from tests.test_work_billing_lifecycle import accepted, bill


@pytest.fixture
def billed_invoice(client, sale):
    return bill(client, accepted(client, sale))


@pytest.fixture
def hosted(root, billed_invoice):
    yield from host_fixture.__wrapped__(root)


def test_retained_billed_sale_rechecks_shared_conditional_resource_without_execution(hosted, billed_invoice, monkeypatch):
    from bookflow.company import billing_queries
    invoice = billed_invoice
    assert invoice['revision']['billing_sources']
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    command = registry.get('invoice show')
    intent = runtime.admit(command, Context.new(Interface.mcp, 'conditional-receipt'), cred,
                           hosted.company_id, 'option', False)
    runtime.prepare_json(intent, {'invoice': invoice['id']}, cred)
    result = runtime.execute_json(intent, cred)
    runtime.intents.delivery(intent)
    runtime.intents.finish(intent, receipt=json.dumps(result).encode(), publication=result.permit.retained())
    calls = []
    require_resource = billing_queries.require_resource
    def observe(session, capability, role):
        calls.append((capability, role))
        return require_resource(session, capability, role)
    monkeypatch.setattr(billing_queries, 'require_resource', observe)
    def forbidden(*args, **kwargs):
        raise AssertionError('Publication retrieval planned or executed the command again')
    monkeypatch.setattr(command, 'plan', forbidden)
    before = hosted.ok('audit.list', {'limit': 200}, company=hosted.company_id)
    for _ in range(2):
        observed = runtime.lookup(intent.reference, cred)
        assert json.loads(observed.receipt)['id'] == invoice['id']
    assert calls == [('customer-work', 'member')] * 2
    assert hosted.ok('audit.list', {'limit': 200}, company=hosted.company_id) == before
