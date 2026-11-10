"""R128 increment 1: synthetic transport, never a deployable wider host."""
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import HTMLResponse, Response

from bookflow.core.deployment import Deployment, PENDING
from bookflow.core.errors import BookflowError
from bookflow.adapters.http.transport_security import TransportSecurity, PRELOGIN

ORIGIN = "https://books.example:8443"
DEPLOYMENT = Deployment("wider", ORIGIN, "127.0.0.1:8123", "127.0.0.1", True)


@pytest.fixture
def transport():
    app = FastAPI()
    @app.api_route('/{path:path}', methods=['GET', 'POST', 'DELETE'])
    async def echo(request: Request, path: str):
        if path == 'download':
            return Response('<script>bad()</script>', media_type='text/html',
                            headers={'Content-Disposition': 'attachment; filename="untrusted.html"'})
        return {'csrf': request.state.csrf_token, 'nonce': request.state.csp_nonce,
                'headers': dict(request.headers)}
    app.add_middleware(TransportSecurity, deployment=DEPLOYMENT)
    return TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 1234))


def test_preflight_lists_all_missing_evidence_and_transport(tmp_path):
    bad = Deployment('wider').preflight()
    assert {b['code'] for b in bad['blockers']} == {
        'https_origin', 'loopback_upstream', 'trusted_proxy', 'secure_cookies', *(b.code for b in PENDING)}
    assert not bad['wider_ready']
    assert not DEPLOYMENT.preflight()['wider_ready']
    assert {b['code'] for b in DEPLOYMENT.preflight()['blockers']} == {b.code for b in PENDING}
    with pytest.raises(BookflowError, match='E_SECURITY_NOT_READY'):
        DEPLOYMENT.require_startup()
    with pytest.raises(BookflowError, match='E_CONFIG_INVALID'):
        Deployment('typo')
    local = Deployment.load(tmp_path, public_origin=None, bind='127.0.0.1:8123', secure_cookies=False)
    assert local.profile == 'local' and local.preflight()['blockers'] == []
    local.require_startup()


@pytest.mark.parametrize('headers', [
    {'Host': 'evil.example'}, {'Host': '127.0.0.1:8123'},
    [('Host', 'books.example:8443'), ('Host', 'evil.example')],
    [('Origin', ORIGIN), ('Origin', ORIGIN)],
    {'Authorization': 'Basic x'},
    {'Cookie': 'bookflow_session=a; bookflow_session=b'},
    {'Cookie': 'broken'},
])
def test_ambiguous_or_spoofed_headers_refused(transport, headers):
    assert transport.get('/health', headers=headers).status_code == 403


def test_forwarded_headers_never_supply_authority(transport):
    result = transport.get('/health', headers={'Forwarded': 'for=127.0.0.1;host=evil.example;proto=http',
        'X-Forwarded-Host': 'evil.example', 'X-Forwarded-For': '10.1.2.3', 'X-Forwarded-Proto': 'http',
        'Tailscale-User-Login': 'admin', 'X-Remote-User': 'admin'})
    assert result.status_code == 200
    assert not any(k.startswith(('x-forwarded', 'tailscale', 'x-remote')) or k == 'forwarded'
                   for k in result.json()['headers'])
    foreign = TestClient(transport.app, base_url=ORIGIN, client=('10.0.0.9', 1234))
    assert foreign.get('/health', headers={'X-Forwarded-For': '127.0.0.1'}).status_code == 403


@pytest.mark.parametrize('source', [None, 'null', 'https://evil.example', ORIGIN+'/',
                                    'http://books.example:8443', 'http://127.0.0.1:8123'])
@pytest.mark.parametrize('path', ['/login', '/setup/one-time', '/logout', '/commands/company.list'])
def test_unsafe_missing_null_and_foreign_origin_refused(transport, source, path):
    csrf = transport.get('/login').json()['csrf']
    headers = {'X-Bookflow-Workbench': '1', 'X-Bookflow-CSRF': csrf}
    if source is not None:
        headers['Origin'] = source
    assert transport.post(path, headers=headers).status_code == 403


def test_prelogin_csrf_session_binding_and_logout(transport):
    response = transport.get('/login')
    csrf = response.json()['csrf']
    cookie = response.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie and 'domain=' not in cookie
    headers = {'Origin': ORIGIN, 'X-Bookflow-Workbench': '1'}
    assert transport.post('/login', headers=headers).status_code == 403
    assert transport.post('/setup/one-time', headers={**headers, 'X-Bookflow-CSRF': 'wrong'}).status_code == 403
    assert transport.post('/setup/one-time', headers={**headers, 'X-Bookflow-CSRF': csrf}).status_code == 200
    assert PRELOGIN not in transport.cookies
    transport.cookies.set('bookflow_session', 'session-one')
    csrf = transport.get('/').json()['csrf']
    assert transport.post('/logout', headers={**headers, 'X-Bookflow-CSRF': csrf}).status_code == 200
    assert transport.post('/logout', headers={'Referer': ORIGIN+'/page', 'X-Bookflow-Workbench':'1', 'X-Bookflow-CSRF':csrf}).status_code == 200
    transport.cookies.set('bookflow_session', 'session-two')
    assert transport.post('/logout', headers={**headers, 'X-Bookflow-CSRF': csrf}).status_code == 403


def test_bearer_only_and_cookie_conflicts(transport):
    headers = {'Authorization': 'Bearer opaque-token'}
    assert transport.post('/commands/company.list', headers=headers).status_code == 200
    for origin in ('null', 'https://foreign.example'):
        assert transport.post('/commands/company.list', headers={**headers, 'Origin': origin}).status_code == 403
        assert transport.get('/health', headers={**headers, 'Origin': origin}).status_code == 403
    assert transport.post('/login', headers={**headers, 'Origin': ORIGIN}).status_code == 403
    for cookie in ('bookflow_session=a', PRELOGIN+'=b'):
        assert transport.post('/commands/company.list', headers={**headers, 'Cookie': cookie}).status_code == 403


def test_headers_and_untrusted_downloads(transport):
    response = transport.get('/download')
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-content-type-options'] == 'nosniff'
    csp = response.headers['content-security-policy']
    assert "frame-ancestors 'none'" in csp and "'nonce-" in csp
    assert "'unsafe-eval'" not in csp and 'https:' not in csp
    assert 'strict-transport-security' not in response.headers  # proxy-owned, never sibling policy


def test_nonce_applies_only_to_trusted_template_source():
    from jinja2 import Environment
    from bookflow.adapters.workbench.security_templates import NonceLoader
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, 'test.html').write_text('<script>trusted()</script>{{ stored }}')
        env = Environment(loader=NonceLoader(directory), autoescape=True)
        page = env.get_template('test.html').render(request=SimpleNamespace(state=SimpleNamespace(csp_nonce='nonce')),
                                                   stored='<script>stolen()</script>')
    assert '<script nonce="nonce">trusted()' in page
    assert '&lt;script&gt;stolen()' in page
    assert page.count('nonce=') == 1


@pytest.fixture
def app_host(tmp_path, monkeypatch):
    import bookflow
    from bookflow.core.config import Config, os_login
    from bookflow.core.host import Host
    from bookflow.core.context import client_version
    root = tmp_path / 'books'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.run('user set-password', {'username': os_login(), 'password': 'transport-test-password'})
    bearer = client.run('token issue', {'label': 'transport-test', 'days': 1})['secret']
    config = Config.load(root / 'config.toml')
    config.data['host'].update(deployment_profile='wider', trusted_proxy='127.0.0.1')
    config.save()
    host = Host(root, version=client_version(), schedule_check_seconds=86400)
    host.start()
    try:
        yield host, bearer
    finally:
        host.stop()


def test_actual_app_login_forms_bearer_and_redacted_errors(app_host, monkeypatch):
    from bookflow.adapters.http.app import create_app
    from bookflow.core.config import os_login
    host, bearer = app_host
    app = create_app(host, secure_cookies=True, public_origin=ORIGIN, bind='127.0.0.1:8123')
    @app.get('/synthetic-error')
    def error():
        raise BookflowError('E_IO', message='password=DO-NOT-PRINT SQL SELECT secret', details={'secret':'DO-NOT-PRINT'})
    api = TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 1234))
    error = api.get('/synthetic-error')
    assert 'DO-NOT-PRINT' not in error.text and 'SELECT' not in error.text
    login = api.get('/login')
    csrf = re.search(r'name="bookflow-csrf" content="([^"]+)"', login.text)[1]
    assert 'allowEval' in login.text and 'hx-on' not in login.text
    headers = {'Origin': ORIGIN, 'X-Bookflow-CSRF': csrf, 'X-Bookflow-Workbench':'1', 'HX-Request':'true'}
    response = api.post('/login', data={'username':os_login(), 'password':'transport-test-password'}, headers=headers)
    assert response.status_code == 200, response.text
    assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
    page = api.get('/companies')
    csrf = re.search(r'name="bookflow-csrf" content="([^"]+)"', page.text)[1]
    response = api.post('/commands/company.list', json={}, headers={**headers, 'X-Bookflow-CSRF':csrf})
    assert response.status_code == 200, response.text
    response = api.post('/logout', headers={**headers, 'X-Bookflow-CSRF':csrf})
    assert response.status_code == 200, response.text
    from bookflow.hub import setup_links
    claims = []
    monkeypatch.setattr(setup_links, 'claim', lambda root, secret: claims.append(secret))
    setup = api.get('/setup/synthetic-link')
    assert not claims  # GET never consumes setup authority.
    csrf = re.search(r'name="bookflow-csrf" content="([^"]+)"', setup.text)[1]
    data = {'password':'transport-setup-password', 'again':'transport-setup-password'}
    assert api.post('/setup/synthetic-link', data=data, headers={'Origin':ORIGIN}).status_code == 403
    assert not claims
    protected = api.post('/setup/synthetic-link', data=data, headers={**headers, 'X-Bookflow-CSRF':csrf})
    assert protected.status_code == 410 and claims == ['synthetic-link']
    api.cookies.clear()
    assert api.post('/commands/company.list', json={}, headers={'Authorization':f'Bearer {bearer}'}).status_code == 200


def test_wider_launcher_refuses_before_start_or_migration(tmp_path, monkeypatch):
    from bookflow.core.config import Config
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.host import Host
    config = Config(tmp_path / 'config.toml')
    config.data['host'].update(deployment_profile='wider')
    config.save()
    monkeypatch.setattr(Host, 'start', lambda self: pytest.fail('must not touch storage or start a writer'))
    with pytest.raises(BookflowError) as refused:
        start_serving(tmp_path, 'test')
    assert refused.value.code == 'E_SECURITY_NOT_READY'
    assert len(refused.value.details['blockers']) == len(PENDING) + 3
    assert not (tmp_path / 'hub.db').exists()


@pytest.mark.parametrize('origin', [None, ORIGIN])
def test_local_plain_and_tls_proxy_compatibility(app_host, origin):
    from bookflow.adapters.http.app import create_app
    from bookflow.core.config import Config, os_login
    host, _ = app_host
    config = Config.load(host.data_root / 'config.toml')
    config.data['host'] = {}
    config.save()
    app = create_app(host, secure_cookies=bool(origin), public_origin=origin, bind='127.0.0.1:9123')
    api = TestClient(app, base_url=origin or 'http://127.0.0.1:9123')
    # Existing local clients need neither new CSRF nor source headers.
    response = api.post('/login', json={'username':os_login(), 'password':'transport-test-password'})
    assert response.status_code == 200, response.text
    assert api.post('/commands/company.list', json={}, headers={'X-Bookflow-Workbench':'1'}).status_code == 200
    assert api.post('/logout', headers={'X-Bookflow-Workbench':'1', 'Origin':'http://127.0.0.1:9123'}).status_code == 200


def test_malformed_referer_and_non_ascii_csrf_are_refused(transport):
    assert transport.post('/login', headers={'Referer': 'https://['}).status_code == 403
    transport.get('/login')
    assert transport.post('/login', headers=[(b'Origin', ORIGIN.encode()), (b'X-Bookflow-Workbench', b'1'),
        (b'X-Bookflow-CSRF', b'\xff')]).status_code == 403


def test_redaction_keeps_business_recovery_contract():
    from bookflow.adapters.http.redaction import public_error
    err = BookflowError('E_VERSION_CONFLICT', details={'reason':'applied_invoice_correction',
        'current_version':3, 'password':'private', 'nested':{'secret':'private'}})
    safe = public_error(err)
    assert safe.details['reason'] == 'applied_invoice_correction'
    assert safe.details['current_version'] == 3
    assert 'private' not in str(safe.to_dict())


def test_prelogin_expiry_and_restart_refuse_old_tokens(transport, monkeypatch):
    response = transport.get('/login')
    csrf = response.json()['csrf']
    import time
    monkeypatch.setattr('bookflow.adapters.http.transport_security.time.time', lambda: time_ns / 1e9 + 7201)
    time_ns = time.time_ns()
    headers = {'Origin':ORIGIN, 'X-Bookflow-Workbench':'1', 'X-Bookflow-CSRF':csrf}
    assert transport.post('/login', headers=headers).status_code == 403


def test_internal_exception_never_logs_submitted_secret(transport, caplog):
    @transport.app.get('/crash/path')
    def crash():
        raise RuntimeError('SQL password=DO-NOT-PRINT')
    # Register before middleware construction; the catch-all route is replaced
    # with a deliberately failing endpoint for this isolated app.
    async def fail(request):
        raise RuntimeError('SQL password=DO-NOT-PRINT')
    transport.app.router.routes[4].endpoint = fail
    from starlette.routing import request_response
    transport.app.router.routes[4].app = request_response(fail)
    response = transport.get('/crash')
    assert response.status_code == 500
    assert 'DO-NOT-PRINT' not in response.text + caplog.text
    assert response.headers['cache-control'] == 'private, no-store'


@pytest.fixture(autouse=True)
def restore_diagnostic_posture(monkeypatch):
    # Synthetic local and wider apps share this pytest process, unlike hosts.
    from bookflow.core import deployment
    monkeypatch.setattr(deployment, '_redact_diagnostics', False)


@pytest.mark.parametrize('profile', ['local', 'wider'])
def test_internal_failure_log_diagnostics_follow_profile(app_host, caplog, profile):
    from bookflow.adapters.http.app import create_app
    from bookflow.core.config import Config
    host, _ = app_host
    config = Config.load(host.data_root / 'config.toml')
    config.data['host']['deployment_profile'] = profile
    config.save()
    app = create_app(host, secure_cookies=True, public_origin=ORIGIN, bind='127.0.0.1:8123')

    @app.get('/diagnostic-failure')
    def crash():
        raise RuntimeError('diagnostic-private-cause')

    api = TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 1234), raise_server_exceptions=False)
    response = api.get('/diagnostic-failure')
    assert response.status_code == 500
    assert 'diagnostic-private-cause' not in response.text
    if profile == 'local':
        assert 'Traceback (most recent call last)' in caplog.text
        assert 'RuntimeError: diagnostic-private-cause' in caplog.text
        assert any(record.exc_info for record in caplog.records)
    else:
        assert 'Traceback' not in caplog.text
        assert 'diagnostic-private-cause' not in caplog.text
        assert not any(record.exc_info for record in caplog.records)


def test_wider_missing_http_client_is_refused(transport):
    api = TestClient(transport.app, base_url=ORIGIN, client=None)
    assert api.get('/health').status_code == 403


def test_wider_local_cli_socket_bypasses_http_middleware(app_host, tmp_path, monkeypatch):
    from bookflow.adapters.http.app import create_app
    from bookflow.adapters.http.local import LocalListener
    from bookflow.commands.host_cmds import make_local_handler
    from bookflow.core.context import Context, Interface, client_version
    from bookflow.core.forward import call_host
    host, _ = app_host
    create_app(host, secure_cookies=True, public_origin=ORIGIN, bind='127.0.0.1:8123')

    async def unexpected_http(*args, **kwargs):
        pytest.fail('local CLI forwarding must not enter HTTP transport middleware')

    monkeypatch.setattr(TransportSecurity, '__call__', unexpected_http)
    listener = LocalListener(host, tmp_path / 'cli.sock', make_local_handler(host, client_version()))
    listener.start()
    try:
        reply = call_host(str(listener.path), {
            'command': 'company list', 'input': {}, 'version': client_version(),
            'context': Context.new(Interface.cli, 'diagnostic-test').model_dump(mode='json'),
        })
        assert 'error' not in reply, reply
        assert 'output' in reply, reply
    finally:
        listener.stop()


@pytest.mark.parametrize('profile', ['local', 'wider'])
def test_guard_refusal_log_diagnostics_follow_profile(profile, caplog):
    import sqlite3
    from bookflow.core.deployment import configure_diagnostics
    from bookflow.core.dispatch import guard
    configure_diagnostics(Deployment(profile))
    error = sqlite3.IntegrityError('private-guard-message')
    error.sqlite_errorcode = sqlite3.SQLITE_CONSTRAINT_TRIGGER

    def refuse():
        raise error

    with pytest.raises(BookflowError) as refused:
        guard(refuse, True, lambda message: {'request': 'private-guard-facts'})
    assert refused.value.code == 'E_GUARD_REFUSED'
    if profile == 'local':
        assert 'private-guard-message' in caplog.text
        assert 'private-guard-facts' in caplog.text
        assert 'Traceback (most recent call last)' in caplog.text
    else:
        assert 'private-guard' not in caplog.text
        assert 'Traceback' not in caplog.text
        assert not any(record.exc_info for record in caplog.records)


def test_no_workbench_template_carries_an_inline_handler():
    """The wider policy allows no inline script, so a page's behaviour lives in a static file."""
    templates = Path(__file__).resolve().parents[1] / 'src/bookflow/adapters/workbench/templates'
    inline = re.compile(r'\son[a-z]+=["\']|hx-on|javascript:', re.I)
    found = sorted(path.name for path in templates.rglob('*.html') if inline.search(path.read_text(encoding='utf-8')))
    assert found == []
