"""Response-family boundaries: mocked HTTP documents, no external host access."""
from contextlib import closing
import io
import json

import anyio
import httpx2
import pytest

from bookflow.adapters.mcp.client import Client
from bookflow.adapters.mcp.envelopes import RecoveryArguments
from bookflow.adapters.mcp.files import Directories
from bookflow.adapters.mcp.framing import Decoder, MAGIC, record
from bookflow.core.errors import BookflowError

REFERENCE = 'owned.intent'


@pytest.mark.parametrize('action', ['run', 'execute'])
@pytest.mark.parametrize('content_type', ['application/json', 'text/plain'])
def test_unverified_success_dictionary_is_not_business_completion(tmp_path, action, content_type):
    async def witness():
        def receive(request):
            return httpx2.Response(200, json={'ok': True}, headers={'X-Bookflow-MCP-Version': '2', 'Content-Type': content_type})
        with closing(Directories([])) as inputs, closing(Directories([])) as outputs:
            async with httpx2.AsyncClient(base_url='http://127.0.0.1:1', transport=httpx2.MockTransport(receive)) as http:
                with pytest.raises(BookflowError) as caught:
                    await Client(http, inputs, outputs).result(REFERENCE, action)
                assert caught.value.code == 'E_IO'
                assert caught.value.details['operation'] == 'mcp_result'
                assert caught.value.details['stage'] == 'post_submission'
                assert caught.value.details['outcome'] == 'unknown'
    anyio.run(witness)


@pytest.mark.parametrize('alias', ['operation_ref', 'input_ref'])
@pytest.mark.parametrize('action', ['execute', 'status', 'inspect'])
def test_recovery_denial_keeps_known_reference_and_uncertainty(tmp_path, alias, action):
    async def witness():
        def receive(request):
            return httpx2.Response(403, json={'code': 'E_PERMISSION', 'message': 'Authority changed',
                'details': {'stage': 'publication', 'reason': 'authority_changed'}}, headers={'X-Bookflow-MCP-Version': '2'})
        with closing(Directories([])) as inputs, closing(Directories([])) as outputs:
            async with httpx2.AsyncClient(base_url='http://127.0.0.1:1', transport=httpx2.MockTransport(receive)) as http:
                client = Client(http, inputs, outputs)
                if action == 'inspect':
                    # Mapping lookup is independent of the HTTP publication gate.
                    client.mappings.get = lambda ref: {}
                arguments = RecoveryArguments.model_validate({alias: REFERENCE, 'action': action})
                with pytest.raises(BookflowError) as caught:
                    await client.run(arguments)
                assert caught.value.code == 'E_PERMISSION'
                assert caught.value.details['operation_ref'] == REFERENCE
                assert caught.value.details['stage'] == 'publication'
                assert caught.value.details['outcome'] == 'unknown'
    anyio.run(witness)


def test_deep_terminal_has_typed_uncertainty_without_recursive_exception():
    terminal = b'{"recovery":{"mode":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}}'
    with pytest.raises(BookflowError) as caught:
        Decoder(io.BytesIO(), None, operation_ref=REFERENCE).feed(MAGIC + record(b'T', terminal))
    assert caught.value.code == 'E_IO'
    assert caught.value.details['stage'] == 'post_submission'
    assert caught.value.details['outcome'] == 'unknown'


def test_execution_error_cannot_claim_pre_admission_command_rejection():
    async def witness():
        def receive(request):
            return httpx2.Response(403, json={'code': 'E_PERMISSION', 'message': 'denied', 'details': {}},
                headers={'X-Bookflow-MCP-Version': '2', 'X-Bookflow-MCP-Response': 'command_rejection'})
        with closing(Directories([])) as inputs, closing(Directories([])) as outputs:
            async with httpx2.AsyncClient(base_url='http://127.0.0.1:1', transport=httpx2.MockTransport(receive)) as http:
                with pytest.raises(BookflowError) as caught:
                    await Client(http, inputs, outputs).run(RecoveryArguments(operation_ref=REFERENCE, action='execute'))
                assert caught.value.details['operation_ref'] == REFERENCE
                assert caught.value.details['outcome'] == 'unknown'
    anyio.run(witness)


def test_unframed_transport_observation_has_no_published_result_file(tmp_path):
    from bookflow.adapters.mcp.responses import LIMIT_FIELDS
    state = {'operation_ref': REFERENCE, 'state': 'completed', 'reason': 'execution_result_unavailable',
        'receipt_available': False, 'inspection_available': False, 'outcome': 'unknown',
        'limits': dict.fromkeys(LIMIT_FIELDS, 1)}
    outbox = tmp_path / 'out'; outbox.mkdir(mode=0o700)
    async def witness():
        def receive(request):
            return httpx2.Response(200, json=state, headers={'X-Bookflow-MCP-Version': '2'})
        with closing(Directories([])) as inputs, closing(Directories([str(outbox)])) as outputs:
            async with httpx2.AsyncClient(base_url='http://127.0.0.1:1', transport=httpx2.MockTransport(receive)) as http:
                result, error, meta = await Client(http, inputs, outputs).result(REFERENCE, 'execute', result_file=str(outbox / 'receipt.json'))
                assert not error and result == state
                assert meta['response_kind'] == 'recovery_observation'
    anyio.run(witness)
    assert list(outbox.iterdir()) == []
