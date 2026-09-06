"""Local lifecycle commands remain discoverable but cannot run on a remote host."""
import os
from pathlib import Path
import sys
import anyio
from bookflow.core import registry
from bookflow.documentation.examples import EXAMPLES
from tests.test_row3_host import hosted, live
from tests.test_mcp_registry_credentials import hub_snapshot
from tests.test_mcp_registry_work import company_snapshot

COMMANDS = frozenset({'init', 'company use', 'docs generate', 'mcp', 'serve'})


def test_installed_local_boundaries_are_explicit_and_do_not_execute(hosted, live, tmp_path, monkeypatch):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    registry.load_all()
    assert {cmd.name for cmd in registry.all_commands(include_standalone=True) if cmd.local_only or cmd.standalone} == COMMANDS
    before = hub_snapshot(hosted.root), company_snapshot(hosted.root)
    def forbidden(*args, **kwargs):
        raise AssertionError('A local command reached a hosted planner')
    for name in COMMANDS:
        monkeypatch.setattr(registry.get(name), 'plan', forbidden)
    output = tmp_path/'never-generated'
    async def witness():
        binary = os.environ.get('BOOKFLOW_MCP_TEST_BINARY',str(Path(sys.executable).with_name('bookflow')))
        params = StdioServerParameters(command=binary,args=['mcp','--url',live],cwd=str(tmp_path),
            env={'BOOKFLOW_TOKEN':hosted.secret, 'BOOKFLOW_DATA_ROOT':str(tmp_path/'absent')})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.discover()
                for name in sorted(COMMANDS):
                    help_ = await session.call_tool('bookflow_help', {'command':name})
                    assert not help_.is_error and (help_.structured_content['local_only'] or help_.structured_content['standalone'])
                    assert 'cannot execute through the hosted MCP adapter' in help_.structured_content['documentation']
                    raw = dict(EXAMPLES[name].input)
                    if name == 'docs generate':
                        raw = {'output':str(output),'check':False}
                    registry.get(name).input_model.model_validate(raw)
                    reply = await session.call_tool('bookflow_run',{'command':name,'input':raw})
                    assert reply.is_error and reply.structured_content['code'] == 'E_USAGE',reply
                    assert reply.structured_content['details']['boundary'] == 'local_only'
                    # The generic HTTP route rejects local names with its existing E_USAGE envelope.
                    http = hosted.call(name.replace(' ','.'),raw)
                    assert http.status_code == 400 and http.json()['code'] == 'E_USAGE'
    anyio.run(witness)
    assert (hub_snapshot(hosted.root),company_snapshot(hosted.root)) == before
    assert not output.exists() and not (tmp_path/'absent').exists()
    assert not hosted.handle.host._mcp_runtime.intents.active
