"""Actual membership writes cannot publish through a preparation certificate."""
import asyncio
import json

import pytest

from bookflow.adapters.http.publication import PublicationMiddleware, protect
from bookflow.adapters.mcp.runtime import Runtime
from bookflow.adapters.mcp.transport import IntentGuard
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from bookflow.core.publication import PublicationPermit
from tests.test_mcp_runtime import credential
from tests.test_row3_host import hosted


@pytest.mark.parametrize('revoke', [False, True])
def test_own_membership_transition_pending_then_verified_result(hosted, monkeypatch, revoke):
    cred = credential(hosted, monkeypatch)
    runtime = Runtime.for_host(hosted.handle.host)
    command = registry.get('membership grant')
    intent = runtime.admit(command, Context.new(Interface.mcp, 'lifecycle-pending', reason='Change own demo role'), cred, None, 'none', False)
    runtime.prepare_json(intent, {'user': hosted.login, 'company': hosted.company_id, 'role': 'admin'}, cred)
    guard = IntentGuard(runtime, intent, cred, PublicationPermit.from_retained(intent.publication))
    result = runtime.execute_json(intent, cred)
    assert intent.publication_pending
    for check in (lambda: runtime.lookup(intent.reference, cred), guard.check):
        with pytest.raises(BookflowError) as caught:
            check()
        assert caught.value.code == 'E_DB_BUSY'
        assert caught.value.details['operation'] == 'publication_pending'

    # A response admitted before execution must serialize the new pending state,
    # not reuse the now-obsolete preparation certificate or erase the retry marker.
    sent = []
    async def app(scope, receive, send):
        protect(guard, original_response=False)
        await send({'type':'http.response.start', 'status':200, 'headers':[]})
        await send({'type':'http.response.body', 'body':b'original-status'})
    async def receive():
        return {'type':'http.request'}
    async def send(message):
        sent.append(message)
    asyncio.run(PublicationMiddleware(app)({'type':'http','path':'/adapters/mcp/intents/x/status'}, receive, send))
    body = json.loads(b''.join(x.get('body', b'') for x in sent))
    assert body['code'] == 'E_DB_BUSY'
    assert body['details']['operation'] == 'publication_pending'
    assert 'original-status' not in str(sent)

    from bookflow.adapters.mcp.delivery import Delivery
    from starlette.requests import ClientDisconnect
    delivery = Delivery(runtime, intent, result)
    async def lost_send(message):
        raise OSError('socket disconnected before first frame')
    with pytest.raises(ClientDisconnect):
        asyncio.run(delivery({'type':'http', 'asgi':{'spec_version':'2.4'}}, receive, lost_send))
    assert intent.receipt is not None
    if revoke:
        hosted.ok('membership.revoke', {'user': hosted.login, 'company': hosted.company_id}, headers={'X-Bookflow-Reason':'Independent revocation'})
    assert not intent.publication_pending
    def forbidden(*args, **kwargs):
        raise AssertionError('Recovery executed membership grant again')
    monkeypatch.setattr(command, 'plan', forbidden)
    if revoke:
        for check in (lambda: runtime.lookup(intent.reference, cred), guard.check):
            with pytest.raises(BookflowError) as caught:
                check()
            assert caught.value.code == 'E_PERMISSION'
    else:
        assert json.loads(runtime.lookup(intent.reference, cred).receipt) == result
        guard.check()
        assert runtime.intents.resume_delivery(intent)
        assert not intent.publication_pending
        guard.check()
        runtime.intents.finish(intent, receipt=intent.receipt, publication=intent.publication)
