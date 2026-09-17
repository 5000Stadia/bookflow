"""Retry only the host filesystem gate while observing an already submitted intent."""
from contextlib import closing
import time

import anyio
import httpx2
import pytest

from bookflow.adapters.mcp.client import Client
from bookflow.adapters.mcp.envelopes import RecoveryArguments
from bookflow.adapters.mcp.files import Directories
from bookflow.core.errors import BookflowError
from tests.test_mcp_timeout_recovery import REF, completion, control, short_recovery, state


def busy(operation='filesystem_change', code='E_DB_BUSY'):
    return {'code': code, 'message': 'retry shortly', 'details': {'operation': operation}}


@pytest.mark.parametrize('mode', ['status', 'receipt', 'persistent', 'cancel', 'revoked', 'denied',
                                  'other_busy', 'missing_operation', 'business_busy'])
def test_filesystem_busy_recovery(short_recovery, mode):
    calls, poll_times = [], []
    returned = {'original': 'completion'}
    async def witness():
        reached = anyio.Event()
        async def receive(request):
            action = request.url.path.rsplit('/', 1)[-1]
            assert request.url.path == f'/adapters/mcp/intents/{REF}/{action}'
            assert not request.content
            calls.append(action)
            if len(calls) == 1:
                raise httpx2.ReadTimeout('lost reply')
            if action == 'status':
                poll_times.append(time.monotonic())
                if mode == 'other_busy':
                    return control(busy(operation='other'), 503)
                if mode == 'missing_operation':
                    return control({'code': 'E_DB_BUSY', 'message': 'busy', 'details': {}}, 503)
                if mode == 'persistent' or mode == 'cancel' or (mode != 'receipt' and len(poll_times) == 1):
                    reached.set()
                    return control(busy(), 503)
                if mode in {'revoked', 'denied'}:
                    return control(busy(code='E_UNAUTHENTICATED' if mode == 'revoked' else 'E_PERMISSION'), 403)
                return control(state())
            assert action == 'execute'
            if mode == 'receipt' and calls.count('execute') == 2:
                return control(busy(), 503)
            return completion(busy() if mode == 'business_busy' else returned, error=mode == 'business_busy')
        with closing(Directories([])) as dirs:
            async with httpx2.AsyncClient(base_url='http://fixture', transport=httpx2.MockTransport(receive)) as http:
                client = Client(http, dirs, dirs)
                async def run():
                    return await client.run(RecoveryArguments(operation_ref=REF, action='execute'))
                started = time.monotonic()
                if mode == 'cancel':
                    cancelled = []
                    async def call():
                        try:
                            await run()
                        except anyio.get_cancelled_exc_class():
                            cancelled.append(True)
                            raise
                    async with anyio.create_task_group() as group:
                        group.start_soon(call)
                        await reached.wait()
                        group.cancel_scope.cancel()
                    assert cancelled == [True]
                    assert calls == ['execute', 'status']
                elif mode in {'persistent', 'revoked', 'denied', 'other_busy', 'missing_operation'}:
                    with pytest.raises(BookflowError) as caught:
                        await run()
                    error = caught.value
                    assert error.details['outcome'] == 'unknown'
                    assert error.details['operation_ref'] == REF
                    assert error.details['recovery']['action'] == 'status'
                    if mode == 'persistent':
                        assert error.details['reason'] == 'recovery_deadline'
                        assert 0.12 <= time.monotonic() - started < 0.7
                        assert 3 <= len(poll_times) <= 7
                    else:
                        assert error.code == {'revoked': 'E_UNAUTHENTICATED', 'denied': 'E_PERMISSION'}.get(mode, 'E_DB_BUSY')
                        assert calls == ['execute'] + ['status'] * (2 if mode in {'revoked', 'denied'} else 1)
                else:
                    result, is_error, meta = await run()
                    assert result == (busy() if mode == 'business_busy' else returned)
                    assert is_error is (mode == 'business_busy')
                    assert meta['response_kind'] == 'verified_command_completion'
                    assert calls == (['execute', 'status', 'execute', 'status', 'execute'] if mode == 'receipt'
                                     else ['execute', 'status', 'status', 'execute'])
    anyio.run(witness)
    # No tight retry loop, including the persistent-busy case.
    assert all(later - earlier >= 0.008 for earlier, later in zip(poll_times, poll_times[1:]))
