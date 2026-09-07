"""Real hosted dispatch/check mutations prove the phase guard's two failure modes."""
import json
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from bookflow.commands.host_cmds import start_serving
from bookflow.core.context import client_version
from bookflow.core import registry
from tests.test_bounded_queries import _bulk_customers, COMPANY
from tests.query_phase_trace import QueryTrace, assert_bounded


def test_actual_phase_guard_and_mutations(client, root, monkeypatch, tmp_path):
    _bulk_customers(client, 200)
    cid = client.company.show(company=COMPANY)['company_id']
    secret = client.token.issue(label='phase-controls')['secret']
    trace = QueryTrace(monkeypatch)
    handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False, publish_descriptor=False)
    api = TestClient(trace.app(handle.app))
    receipts = {}
    def run(limit):
        response = api.post(f'/companies/{cid}/commands/customer.query', json={'limit': limit},
                            headers={'Authorization': 'Bearer '+secret})
        assert response.status_code == 200, response.text
        assert response.json()['count'] == limit
        return response
    try:
        for n in (10,50,200): run(n)
        for n in (10,50,200):
            _, receipts[str(n)] = trace.run(lambda: run(n))
        assert_bounded(*receipts.values())
        sizes = {n: sum(f['bytes'] for f in r['frames']) for n,r in receipts.items()}
        assert sizes['10'] < sizes['50'] < 65536 < sizes['200']
        from bookflow.adapters.http.execution import PublishedDocument
        original = PublishedDocument.check
        calls = 0
        def omitted(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: return  # actual header check invocation is omitted
            return original(self, **kwargs)
        with monkeypatch.context() as mutation:
            mutation.setattr(PublishedDocument, 'check', omitted)
            _, receipts['missing'] = trace.run(lambda: run(10))
            with pytest.raises(AssertionError, match='missing or extra release check') as failure:
                assert_bounded(receipts['missing'])
            receipts['missing_failure'] = str(failure.value)
        command = registry.get('customer query'); original_plan = command.plan
        def per_row(inp, ctx, session):
            plan = original_plan(inp, ctx, session)
            for _ in plan.preview.items:
                session.company.conn.execute(sa.select(sa.literal(1))).scalar_one()
            return plan
        with monkeypatch.context() as mutation:
            mutation.setattr(command, 'plan', per_row)
            _, receipts['per_row_10'] = trace.run(lambda: run(10))
            _, receipts['per_row_50'] = trace.run(lambda: run(50))
            with pytest.raises(AssertionError, match='page-size dependent work') as failure:
                assert_bounded(receipts['per_row_10'], receipts['per_row_50'])
            receipts['per_row_failure'] = str(failure.value)
        _, restored = trace.run(lambda: run(10))
        assert_bounded(receipts['10'], restored)
    finally:
        handle.stop()
        (tmp_path/'phase-controls.json').write_text(json.dumps(receipts, indent=2))
