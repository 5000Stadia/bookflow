"""Review4 offline callback witness, extended to every launcher failure family.

Original two cases: Aristotle review4 .cache/test_review_recovery.py.
No host, credentials, database or actual network.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import anyio
import httpx2
import pytest
from bookflow.adapters.mcp import launcher

@pytest.mark.parametrize('failure', ['incompatible', 'connection', 'unexpected', 'serialization', 'invalid_action'])
@pytest.mark.parametrize('alias', ['operation_ref', 'input_ref'])
def test_recovery_preflight_preserves_prior_operation_uncertainty(monkeypatch, failure, alias):
    import mcp.server
    import mcp.server.stdio
    real_client=httpx2.AsyncClient
    captured={}
    def reply(request):
        if failure=='unexpected': raise RuntimeError('Do not expose this fixture diagnostic')
        if failure=='connection': raise httpx2.ConnectError('Offline fixture',request=request)
        return httpx2.Response(200,json={'bridge_version':1},headers={'X-Bookflow-MCP-Version':'1'})
    monkeypatch.setattr(httpx2,'AsyncClient',lambda **kw: real_client(**kw,transport=httpx2.MockTransport(reply)))
    if failure == 'serialization':
        from bookflow.adapters.mcp.client import Client
        from mcp import types
        async def completed(self, *args, **kwargs):
            return {'outcome':'unknown'}, False, None
        monkeypatch.setattr(Client, 'run', completed)
        original_text = types.TextContent
        first = True
        def text_once(**kwargs):
            nonlocal first
            if first:
                first = False
                raise ValueError('Do not expose this fixture diagnostic')
            return original_text(**kwargs)
        monkeypatch.setattr(types, 'TextContent', text_once)
        def valid_preflight(request):
            return httpx2.Response(200, json={}, headers={'X-Bookflow-MCP-Version':'2'})
        monkeypatch.setattr(httpx2,'AsyncClient',lambda **kw: real_client(**kw,transport=httpx2.MockTransport(valid_preflight)))
    class Server:
        def __init__(self,*args,**kwargs): self.call=kwargs['on_call_tool']
        def create_initialization_options(self): return None
        async def run(self,*args):
            captured['reply']=await self.call(None,SimpleNamespace(name='bookflow_run',arguments={alias:'retained.intent','action':[] if failure == 'invalid_action' else 'execute'}))
    @asynccontextmanager
    async def stdio(): yield None,None
    monkeypatch.setattr(mcp.server,'Server',Server)
    monkeypatch.setattr(mcp.server.stdio,'stdio_server',stdio)
    anyio.run(launcher.serve,SimpleNamespace(label='offline-review'), 'http://127.0.0.1:1','noncredential-fixture',None,None)
    result=captured['reply']; print(failure,result.structured_content)
    assert result.is_error
    details=result.structured_content['details']
    assert details.get('outcome')=='unknown'
    assert details.get('operation_ref')=='retained.intent'

    assert 'Do not expose' not in str(result.structured_content)
