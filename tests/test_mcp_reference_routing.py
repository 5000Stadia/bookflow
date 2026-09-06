import anyio
import httpx2
import pytest

from bookflow.adapters.mcp.client import Client
from bookflow.adapters.mcp.envelopes import RecoveryArguments
from bookflow.adapters.mcp.files import Directories
from bookflow.core.errors import BookflowError


def test_reference_cannot_route_to_another_authenticated_http_endpoint():
    """Mock transport only: record effective HTTP path, never invoke a host command."""
    requests = []
    async def witness():
        def receive(request):
            requests.append(str(request.url))
            return httpx2.Response(200, headers={'X-Bookflow-MCP-Version': '2'}, json={'safe_mock': True})
        async with httpx2.AsyncClient(base_url='http://127.0.0.1:1', transport=httpx2.MockTransport(receive)) as http:
            client = Client(http, Directories([]), Directories([]))
            try:
                await client.run(RecoveryArguments(operation_ref='../../../commands/company.list?#', action='status'))
            except (BookflowError, ValueError):
                pass
    anyio.run(witness)
    assert requests == [], 'An opaque reference selected an HTTP route: ' + repr(requests)


@pytest.mark.parametrize('value', ['../x', '.', '..', 'a/b', 'a?b', 'a#b', '%2e%2e', 'a\\b', 'a\nb', 'x' * 129])
def test_reference_path_constraints_are_advertised_and_enforced(value):
    from jsonschema import Draft202012Validator
    from bookflow.adapters.mcp.envelopes import tool_schema, validate
    raw = {'input_ref': value, 'action': 'execute'}
    assert not Draft202012Validator(tool_schema('bookflow_run')).is_valid(raw)
    with pytest.raises(BookflowError) as caught:
        validate('bookflow_run', raw)
    assert caught.value.code == 'E_VALIDATION'
