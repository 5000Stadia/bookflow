"""Fresh permit facts and actual hosted HTTP/MCP audit publication."""
import json
import os
from pathlib import Path

import anyio
from bookflow.adapters.mcp.runtime import Runtime
from bookflow.core import registry, publication_payment as pp
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from bookflow.company import payment_authority as pa
from tests.test_mcp_payment_publication import hosted, payment_graph, sale
from tests.test_mcp_runtime import credential
from tests.test_mcp_registry_browsing import company_snapshot
from tests.mcp_matrix_support import Matrix


def test_successive_permit_checks_reload_after_public_application(hosted, payment_graph, sale, monkeypatch, tmp_path):
    paid = hosted.ok('payment.receive', dict(customer=sale['customer'], date='2026-06-03', amount='1.00',
        payment_method=hosted.ok('payment-method.query', {'limit':1}, company=hosted.company_id)['items'][0]['id'],
        operation_key='fresh-publication-receipt', applications=dict(mode='inline', items=[])), company=hosted.company_id)
    event = hosted.ok('audit.list', dict(command='payment receive', limit=1), company=hosted.company_id)['items'][0]['id']
    cred = credential(hosted, monkeypatch); runtime = Runtime.for_host(hosted.handle.host)
    intent = runtime.admit(registry.get('audit show'), Context.new(Interface.mcp, 'fresh-publication'),
                           cred, hosted.company_id, 'option', False)
    runtime.prepare_json(intent, {'event': event}, cred)
    document = runtime.execute_json(intent, cred)
    runtime.intents.delivery(intent)
    runtime.intents.finish(intent, receipt=json.dumps(document).encode(), publication=document.permit.retained())
    original_gate = pa.require_resource; original_init = pa._EventCohort.__init__
    calls = []; databases = []
    def gate(s, resource, role):
        calls.append((resource, role)); return original_gate(s, resource, role)
    def initialize(self, db, ids):
        databases.append(db); original_init(self, db, ids)
    monkeypatch.setattr(pa, 'require_resource', gate)
    monkeypatch.setattr(pa._EventCohort, '__init__', initialize)
    document.check()
    assert calls == [('ledger.read', 'member')]
    first = databases[-1]
    changed = hosted.ok('payment.apply', dict(payment=paid['id'], expected_version=1, date='2026-06-03',
        operation_key='fresh-publication-apply', applications=dict(mode='inline', items=[
            dict(invoice=payment_graph['invoice'], expected_version=2, amount='0.01')])), company=hosted.company_id)
    assert changed['version'] == 2
    calls.clear(); databases.clear()
    before = company_snapshot(hosted.root, hosted.company_id)
    document.check()
    assert calls == [('ledger.read', 'member'), ('customer-work', 'member')]
    assert databases and all(db is not first for db in databases)
    assert json.loads(runtime.lookup(intent.reference, cred).receipt) == document
    assert company_snapshot(hosted.root, hosted.company_id) == before
    (tmp_path/'freshness.json').write_text(json.dumps(dict(event=event, before_requirements=['ledger.read'],
        after_requirements=['ledger.read','customer-work'], retained_result=document), indent=2))


def test_actual_http_mcp_audit_outputs_and_publication_denial(root, tmp_path, monkeypatch):
    """Existing pure permission seam, on the actual SDK and HTTP transports."""
    if binary := os.environ.get('BOOKFLOW_MCP_TEST_BINARY'):
        monkeypatch.setattr('tests.conftest.BIN', Path(binary))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path/'surfaces')
            before = {s: company_snapshot(r, matrix.company) for s,r in matrix.roots.items()}
            receipts = {}
            for surface in ('python', 'http', 'mcp'):
                receipts[surface] = []
                for command, args in [('audit list', {'limit':20}), ('audit tail', {'after':0,'limit':20})]:
                    receipts[surface].append(await matrix.call(surface, command, args))
                event = receipts[surface][0]['items'][0]['id']
                receipts[surface].append(await matrix.call(surface, 'audit show', {'event':event}))
            assert receipts['python'] == receipts['http'] == receipts['mcp']
            original = pp.check; original_gate = pa.require_resource
            checked = []
            def reject(s, resource, role):
                if resource == 'ledger.read':
                    checked.append((resource, role)); raise BookflowError('E_PERMISSION')
                return original_gate(s, resource, role)
            def publication(s, roots):
                with monkeypatch.context() as m:
                    m.setattr(pa, 'require_resource', reject)
                    return original(s, roots)
            monkeypatch.setattr(pp, 'check', publication)
            # Select a seeded financial event, rather than assuming the latest
            # ordinary list event has a ledger requirement.
            event = (await matrix.call('python', 'audit list', {'command':'payment receive','limit':1}))['items'][0]['id']
            denied = {}
            for surface in ('http', 'mcp'):
                denied[surface] = await matrix.call(surface, 'audit show', {'event':event}, rejected=True)
                assert denied[surface]['code'] == 'E_PERMISSION'
                assert denied[surface]['details']['stage'] == 'publication'
                assert 'entries' not in denied[surface]
            assert checked
            for s,r in matrix.roots.items():
                assert company_snapshot(r, matrix.company) == before[s]
            (tmp_path/'transport-receipts.json').write_text(json.dumps(dict(normal=receipts, denied=denied), indent=2))
        finally:
            if hasattr(matrix, 'stack'):
                await matrix.close()
    anyio.run(witness)
