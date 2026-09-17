"""Bounded timeout recovery: controlled transports and real loopback command routes."""
from contextlib import closing
import json
import time

import anyio
import httpx2
import pytest

from bookflow.adapters.mcp import client as client_module
from bookflow.adapters.mcp.client import Client
from bookflow.adapters.mcp.envelopes import RecoveryArguments, RunArguments
from bookflow.adapters.mcp.files import Directories
from bookflow.adapters.mcp.framing import encode
from bookflow.adapters.mcp.responses import LIMIT_FIELDS
from bookflow.core.errors import BookflowError

REF = 'original.intent'
HEADERS = {'X-Bookflow-MCP-Version': '2'}


def state(name='completed', receipt=True):
    return {'operation_ref': REF, 'state': name, 'reason': None,
            'receipt_available': receipt, 'inspection_available': False,
            'limits': dict.fromkeys(LIMIT_FIELDS, 1), 'outcome': 'unknown'}


def control(value, status=200):
    return httpx2.Response(status, json=value, headers=HEADERS)


def completion(document, *, error=False):
    return httpx2.Response(200, content=b''.join(encode(document, check=lambda: None,
        operation_ref=REF, is_error=error)), headers={**HEADERS,
        'Content-Type': 'application/vnd.bookflow.mcp-records'})


@pytest.fixture
def short_recovery(monkeypatch):
    monkeypatch.setattr(client_module, 'RECOVERY_SECONDS', 0.15)
    monkeypatch.setattr(client_module, 'RECOVERY_INITIAL_DELAY', 0.01)
    monkeypatch.setattr(client_module, 'RECOVERY_MAX_DELAY', 0.04)


@pytest.mark.parametrize('mode', ['running', 'status_stalls', 'receipt_stalls', 'receipt_timeouts',
    'unavailable', 'expired', 'expires_after_status', 'denied', 'receipt_denied', 'malformed', 'disconnected', 'observation'])
def test_unknown_is_bounded_and_never_resubmits(short_recovery, mode, caplog):
    calls = []
    async def receive(request):
        action = request.url.path.rsplit('/', 1)[-1]
        calls.append((action, bytes(request.content)))
        if len(calls) == 1:
            raise httpx2.ReadTimeout('private-secret')
        if action == 'status':
            if mode == 'status_stalls':
                await anyio.sleep(5)
            if mode in {'expired', 'denied'}:
                return control({'code': 'E_PERMISSION' if mode == 'denied' else 'E_IO',
                    'message': 'unavailable', 'details': {}}, 403)
            if mode == 'disconnected':
                raise httpx2.ConnectError('private-secret')
            if mode == 'malformed':
                return control({'ok': True})
            return control(state('started', False) if mode == 'running' else state(receipt=mode != 'unavailable'))
        assert action == 'execute'
        if mode == 'receipt_stalls':
            await anyio.sleep(5)
        if mode == 'receipt_timeouts':
            raise httpx2.ReadTimeout('private-secret')
        if mode in {'expires_after_status', 'receipt_denied'}:
            return control({'code': 'E_PERMISSION' if mode == 'receipt_denied' else 'E_IO', 'message': 'expired', 'details': {}}, 400)
        if mode == 'observation':
            return control(state('delivering', True))
        raise AssertionError(mode)

    async def witness():
        with closing(Directories([])) as dirs:
            async with httpx2.AsyncClient(base_url='http://fixture', transport=httpx2.MockTransport(receive)) as http:
                started = time.monotonic()
                with pytest.raises(BookflowError) as caught:
                    await Client(http, dirs, dirs).run(RecoveryArguments(operation_ref=REF, action='execute'))
                elapsed = time.monotonic() - started
                assert elapsed < 0.7
                if mode in {'running', 'status_stalls', 'receipt_stalls', 'receipt_timeouts', 'observation'}:
                    assert elapsed >= 0.12
                    assert caught.value.details['reason'] == 'recovery_deadline'
                assert caught.value.details['outcome'] == 'unknown'
                assert caught.value.details['operation_ref'] == REF
                assert caught.value.details['recovery'] == {'operation_ref': REF, 'action': 'status'}
                if mode in {'denied', 'receipt_denied'}:
                    assert caught.value.code == 'E_PERMISSION'
    anyio.run(witness)
    assert all(action in {'execute', 'status'} and body == b'' for action, body in calls)
    assert len(calls) <= 12  # backoff, including repeated receipt timeouts
    assert 'private-secret' not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize('error', [False, True])
@pytest.mark.parametrize('to_file', [False, True])
def test_original_verified_completion_or_rejection(short_recovery, tmp_path, error, to_file):
    document = {'code': 'E_VALIDATION', 'message': 'original rejection', 'details': {}} if error else {'value': 'original output'}
    calls = []
    async def receive(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx2.ReadTimeout('lost reply')
        if calls[-1].endswith('/status'):
            return control(state())
        return completion(document, error=error)
    destination = tmp_path / 'result.json'
    async def witness():
        with closing(Directories([str(tmp_path)])) as dirs:
            async with httpx2.AsyncClient(base_url='http://fixture', transport=httpx2.MockTransport(receive)) as http:
                result, is_error, meta = await Client(http, dirs, dirs).run(RecoveryArguments(
                    operation_ref=REF, action='execute', **({'result_file': str(destination)} if to_file else {})))
                assert is_error is error
                assert meta['response_kind'] == 'verified_command_completion'
                assert (json.loads(destination.read_bytes()) if to_file else result) == document
    anyio.run(witness)
    assert calls == [f'/adapters/mcp/intents/{REF}/{action}' for action in ('execute', 'status', 'execute')]
    assert sorted(p.name for p in tmp_path.iterdir()) == (['result.json'] if to_file else [])


@pytest.mark.parametrize('stage', ['status', 'backoff', 'receipt'])
def test_cancellation_propagates_without_release(short_recovery, stage):
    calls = []
    cancelled = []
    async def witness():
        reached = anyio.Event()
        async def receive(request):
            action = request.url.path.rsplit('/', 1)[-1]
            calls.append(action)
            if len(calls) == 1:
                raise httpx2.ReadTimeout('lost reply')
            if action == 'status':
                if stage == 'status':
                    reached.set()
                    await anyio.sleep_forever()
                if stage == 'backoff':
                    reached.set()
                    return control(state('started', False))
                return control(state())
            reached.set()
            await anyio.sleep_forever()
        with closing(Directories([])) as dirs:
            async with httpx2.AsyncClient(base_url='http://fixture', transport=httpx2.MockTransport(receive)) as http:
                async def call():
                    try:
                        await Client(http, dirs, dirs).run(RecoveryArguments(operation_ref=REF, action='execute'))
                    except anyio.get_cancelled_exc_class():
                        cancelled.append(True)
                        raise
                async with anyio.create_task_group() as group:
                    group.start_soon(call)
                    await reached.wait()
                    group.cancel_scope.cancel()
    anyio.run(witness)
    assert cancelled == [True]
    assert 'release' not in calls
    assert calls == (['execute', 'status', 'execute'] if stage == 'receipt' else ['execute', 'status'])


@pytest.mark.parametrize('exception', [httpx2.ConnectError, httpx2.WriteTimeout, RuntimeError])
def test_other_failures_do_not_start_recovery(short_recovery, exception, caplog):
    calls = []
    async def receive(request):
        calls.append(request.url.path)
        raise exception('private-secret')
    async def witness():
        with closing(Directories([])) as dirs:
            async with httpx2.AsyncClient(base_url='http://fixture', transport=httpx2.MockTransport(receive)) as http:
                with pytest.raises(BookflowError) as caught:
                    await Client(http, dirs, dirs).run(RecoveryArguments(operation_ref=REF, action='execute'))
                assert caught.value.details['outcome'] == 'unknown'
                assert caught.value.details['reason'] == 'transport_failure'
    anyio.run(witness)
    assert len(calls) == 1
    assert 'private-secret' not in caplog.text


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    """Hub only: no company migration or demo reset."""
    from types import SimpleNamespace
    import bookflow
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    root = tmp_path / 'hub'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    local = bookflow.connect(data_root=str(root))
    local.init()
    issued = local.token.issue(label='timeout-test')
    handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False)
    try:
        yield SimpleNamespace(handle=handle, secret=issued['secret'])
    finally:
        handle.stop()


@pytest.fixture
def delayed_route(hosted, monkeypatch, request):
    """Buffer the original real route's frames so its reply can be lost after commit."""
    from bookflow.core import registry
    cmd = registry.get('user add')
    original_plan = cmd.plan
    plans = []
    mode = request.param
    hosted.rejection = mode in {'rejection', 'ordinary_rejection'}
    hosted.ordinary_rejection = mode == 'ordinary_rejection'
    hosted.expired = mode == 'expired'
    def delayed_plan(*args, **kwargs):
        plans.append(True)
        if mode == 'command':
            time.sleep(1)
        return original_plan(*args, **kwargs)
    monkeypatch.setattr(cmd, 'plan', delayed_plan)
    app = hosted.handle.app
    paths, bodies, original_frames = [], [], []
    async def delayed(scope, receive, send):
        if scope['type'] != 'http':
            return await app(scope, receive, send)
        paths.append(scope['path'])
        if mode == 'expired' and scope['path'].endswith('/execute'):
            from bookflow.adapters.mcp.runtime import Runtime
            intents = Runtime.for_host(hosted.handle.host).intents
            clock = intents.clock
            intents.clock = lambda: clock() + 301
        async def observed_receive():
            message = await receive()
            if scope['path'].endswith('/run') and message['type'] == 'http.request':
                bodies.append(message.get('body', b''))
            return message
        if not scope['path'].endswith('/run'):
            return await app(scope, observed_receive, send)
        messages = []
        async def capture(message):
            messages.append(message)
        # Complete the actual route/authorization/framing even after the client
        # disconnects; the delayed network send is outside the command worker.
        scoped = {**scope, 'asgi': {**scope['asgi'], 'spec_version': '2.4'}}
        await app(scoped, observed_receive, capture)
        original_frames.append(b''.join(m.get('body', b'') for m in messages))
        if mode in {'reply', 'rejection', 'expired'}:
            await anyio.sleep(1)
        for message in messages:
            await send(message)
    hosted.handle.app = delayed
    return plans, paths, bodies, original_frames


@pytest.fixture
def live(delayed_route, hosted):
    # Reuse only the existing ephemeral loopback server, not its demo fixture.
    from tests.test_row3_host import live as live_fixture
    yield from live_fixture.__wrapped__(hosted)


@pytest.mark.parametrize('delayed_route', ['command', 'reply', 'rejection', 'ordinary_rejection', 'expired'], indirect=True)
def test_actual_route_recovers_original_with_one_execution(hosted, delayed_route, live, monkeypatch):
    import io
    from bookflow.adapters.mcp.framing import Decoder
    from bookflow.core.ids import new_id
    plans, paths, bodies, original_frames = delayed_route
    rejection = getattr(hosted, 'rejection', False)
    monkeypatch.setattr(client_module, 'RECOVERY_SECONDS', 3)
    monkeypatch.setattr(client_module, 'RECOVERY_INITIAL_DELAY', 0.02)
    monkeypatch.setattr(client_module, 'RECOVERY_MAX_DELAY', 0.1)
    arguments = RunArguments(command='user add', input={
        'username': 'system' if rejection else 'timeout-person', 'password': 'test-password-only'}, reason='timeout recovery witness')
    async def witness():
        async def read_bound(request):
            if request.url.path.endswith('/run') and not hosted.ordinary_rejection:
                request.extensions['timeout']['read'] = 0.5
        with closing(Directories([])) as dirs:
            async with httpx2.AsyncClient(base_url=live, trust_env=False, headers={
                'Authorization': f'Bearer {hosted.secret}', 'X-Bookflow-Session-Id': new_id()},
                timeout=httpx2.Timeout(2, connect=1), event_hooks={'request': [read_bound]}) as http:
                client = Client(http, dirs, dirs)
                if hosted.expired:
                    with pytest.raises(BookflowError) as caught:
                        await client.run(arguments, metadata={'transfer': None},
                            selection={'value': None, 'source': 'none'})
                    assert caught.value.details['outcome'] == 'unknown'
                    ref = caught.value.details['operation_ref']
                    assert caught.value.details['recovery']['action'] == 'status'
                else:
                    result, error, meta = await client.run(arguments,
                        metadata={'transfer': None}, selection={'value': None, 'source': 'none'})
                    assert error is rejection
                    assert meta['response_kind'] == 'verified_command_completion'
                    ref = meta['operation_ref']
                    # Exact original document, not a state-shaped stand-in.
                    sink = io.BytesIO()
                    decoder = Decoder(sink, None, operation_ref=ref)
                    decoder.feed(original_frames[0])
                    decoder.finish()
                    assert result == json.loads(sink.getvalue())
                async with http.stream('POST', '/commands/hub.audit.list', json={'command': 'user add'}, timeout=2) as response:
                    await response.aread()
                    assert response.status_code == 200, response.text
                    assert len(response.json()['items']) == (0 if rejection else 1)
                return ref
    ref = anyio.run(witness)
    assert plans == [True]
    assert sum(path.endswith('/new') for path in paths) == 1
    assert sum(path.endswith('/run') for path in paths) == 1
    assert json.loads(b''.join(bodies)) == arguments.input
    recovery_paths = [p for p in paths if p.endswith(('/status', '/execute'))]
    if not getattr(hosted, 'ordinary_rejection', False):
        assert recovery_paths
        assert all(p in {f'/adapters/mcp/intents/{ref}/status', f'/adapters/mcp/intents/{ref}/execute'} for p in recovery_paths)
    else:
        assert recovery_paths == []


@pytest.mark.parametrize('corrupt', [False, True])
def test_partial_read_timeout_cleans_file_and_still_requires_terminal(short_recovery, tmp_path, corrupt):
    class Interrupted(httpx2.AsyncByteStream):
        async def __aiter__(self):
            # Feed a real partial JSON channel into the original destination.
            from bookflow.adapters.mcp.framing import MAGIC, record
            yield MAGIC + record(b'J', b'{"partial":"' + b'x' * 65500)
            raise httpx2.ReadTimeout('private-secret')
    calls = []
    destination = tmp_path / 'result.json'
    def receive(request):
        calls.append(request.url.path.rsplit('/', 1)[-1])
        if len(calls) == 1:
            return httpx2.Response(200, stream=Interrupted(), headers={**HEADERS,
                'Content-Type': 'application/vnd.bookflow.mcp-records'})
        if calls[-1] == 'status':
            return control(state())
        response = completion({'original': True})
        if corrupt:
            return httpx2.Response(200, content=response.content[:-5], headers=response.headers)
        return response
    async def witness():
        with closing(Directories([str(tmp_path)])) as dirs:
            async with httpx2.AsyncClient(base_url='http://fixture', transport=httpx2.MockTransport(receive)) as http:
                arguments = RecoveryArguments(operation_ref=REF, action='execute', result_file=str(destination))
                if corrupt:
                    with pytest.raises(BookflowError) as caught:
                        await Client(http, dirs, dirs).run(arguments)
                    assert caught.value.details['outcome'] == 'unknown'
                    assert not destination.exists()
                else:
                    _, error, meta = await Client(http, dirs, dirs).run(arguments)
                    assert not error and meta['response_kind'] == 'verified_command_completion'
                    assert json.loads(destination.read_bytes()) == {'original': True}
    anyio.run(witness)
    assert calls == ['execute', 'status', 'execute']
    assert sorted(p.name for p in tmp_path.iterdir()) == ([] if corrupt else ['result.json'])


def test_admission_read_timeout_does_not_start_recovery(short_recovery):
    calls = []
    def receive(request):
        calls.append(request.url.path)
        raise httpx2.ReadTimeout('lost admission')
    async def witness():
        with closing(Directories([])) as dirs:
            async with httpx2.AsyncClient(base_url='http://fixture', transport=httpx2.MockTransport(receive)) as http:
                with pytest.raises(BookflowError) as caught:
                    await Client(http, dirs, dirs).run(RunArguments(command='user add', input={}),
                        metadata={'transfer': None}, selection={'value': None, 'source': 'none'})
                assert 'operation_ref' not in caught.value.details
    anyio.run(witness)
    assert calls == ['/adapters/mcp/intents/new']
