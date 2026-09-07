"""Real adapters retain whole typed pages and the named safe business failure."""
import json
import os
from pathlib import Path
import sys
import anyio
from bookflow.company import payment_preparation
from tests.mcp_matrix_support import Matrix
from tests.test_mcp_registry_browsing import company_snapshot


def test_real_four_interface_pages_and_profile_business_error(root, tmp_path, monkeypatch):
    # An owned executable pins CLI and the actual SDK adapter to this checkout.
    source = Path(__file__).resolve().parents[1] / 'src'
    payload = tmp_path / 'decode-payload.json'
    binary = tmp_path / 'bookflow-labels'
    binary.write_text(f'''#!{sys.executable}
import sys
import json
from pathlib import Path
sys.path.insert(0, {str(source)!r})
from bookflow.company import payment_preparation as p
original = p._payment_query_labels
def decode(snapshot, **ids):
    path = Path({str(payload)!r})
    injected = json.loads(path.read_text()) if path.exists() else None
    return original(injected['snapshot'] if injected and ids['payment_id'] == injected['payment_id'] else snapshot, **ids)
p._payment_query_labels = decode
from bookflow.adapters.cli.app import main
main()
''')
    binary.chmod(0o700)
    monkeypatch.setenv('BOOKFLOW_MCP_TEST_BINARY', str(binary))
    monkeypatch.setattr('tests.conftest.BIN', binary)
    decode = payment_preparation._payment_query_labels
    monkeypatch.setattr(payment_preparation, '_payment_query_labels',
        lambda snapshot, **ids: decode(json.loads(payload.read_text())['snapshot'] if payload.exists() and ids['payment_id'] == json.loads(payload.read_text())['payment_id'] else snapshot, **ids))
    async def witness():
        matrix = Matrix()
        receipts = {}
        try:
            await matrix.open(root, tmp_path/'surfaces')
            before = {s: company_snapshot(r, matrix.company) for s, r in matrix.roots.items()}
            for surface in matrix.documents:
                chain = []
                raw = {'limit': 1}
                while True:
                    page = await matrix.call(surface, 'payment query', raw)
                    chain.append(page)
                    if page['next_cursor'] is None:
                        break
                    raw['cursor'] = page['next_cursor']
                receipts[surface] = chain
            assert all(receipts[s] == receipts['python'] for s in receipts)
            first = receipts['python'][0]['items'][0]
            shown = await matrix.call('python', 'payment show', {'payment': first['id']})
            expected = {'code': 'E_PAYMENT_PROFILE_INVALID', 'message': 'Stored payment profile is invalid.',
                'details': {'payment_id': first['id'], 'revision_id': shown['current_revision_id'], 'field': 'profile_snapshot'}}
            for snapshot in ('{DO-NOT-DISCLOSE', json.dumps({**shown['revision']['profile'], 'preferences': {}})):
                payload.write_text(json.dumps({'payment_id':first['id'], 'snapshot':snapshot}))
                for surface in matrix.documents:
                    assert await matrix.call(surface, 'payment query', {'limit': 25}, rejected=True) == expected
                host = matrix.hosts['http']
                response = host.api.post('/companies/'+matrix.company+'/commands/payment.query', json={'limit':25}, headers=host.bearer)
                assert response.status_code == 500 and response.json() == expected
                reply = await matrix.mcp.call_tool('bookflow_run', {'command':'payment query', 'input':{'limit':25}, 'company':matrix.company})
                assert reply.is_error and reply.structured_content == expected
            payload.unlink()
            for surface, path in matrix.roots.items():
                assert company_snapshot(path, matrix.company) == before[surface]
            (tmp_path/'four-interface-receipts.json').write_text(json.dumps({'pages':receipts, 'error':expected}, indent=2))
        finally:
            if hasattr(matrix, 'stack'):
                await matrix.close()
    anyio.run(witness)
