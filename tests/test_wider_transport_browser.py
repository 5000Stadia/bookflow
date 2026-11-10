"""TLS loopback proxy with real htmx submits at both acceptance widths."""
import json
import socket
import subprocess
import threading
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from fastapi import Request
from starlette.responses import HTMLResponse

from tests.test_wider_transport import app_host  # noqa: F401
from tests.test_row5_browser_acceptance import _Cdp
from bookflow.adapters.http.app import create_app
from bookflow.core.config import os_login


@contextmanager
def proxy_site(host, tmp_path):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(32)
    origin = f'https://127.0.0.1:{listener.getsockname()[1]}'
    app = create_app(host, secure_cookies=True, public_origin=origin, bind='127.0.0.1:8123')
    @app.get('/test-fragment')
    def fragment(request: Request):
        return HTMLResponse('<div id="fragment-ok">Loaded</div><script nonce="' + request.state.csp_nonce +
                            '">window.fragmentRan=true</script>')
    # The proxy overwrites authority and forwards over a loopback ASGI transport.
    # There is no service-activation bypass in the production launcher.
    upstream = httpx.ASGITransport(app=app, client=('127.0.0.1', 1234))
    async def proxy(scope, receive, send):
        if scope['type'] != 'http':
            return
        body = bytearray()
        while True:
            part = await receive()
            body.extend(part.get('body', b''))
            if not part.get('more_body'):
                break
        headers = [(k, v) for k, v in scope['headers'] if k.lower() not in (b'host', b'connection')]
        async with httpx.AsyncClient(transport=upstream, base_url=origin) as client:
            result = await client.request(scope['method'], scope['path'], params=scope['query_string'].decode('ascii'),
                                          headers=headers, content=bytes(body))
        await send({'type':'http.response.start', 'status':result.status_code, 'headers':result.headers.raw})
        await send({'type':'http.response.body', 'body':result.content})
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=127.0.0.1', '-keyout', str(key), '-out', str(cert)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ready = threading.Event()
    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()
    server = Server(uvicorn.Config(proxy, lifespan='off', log_level='error', access_log=False,
                                   proxy_headers=False, ssl_certfile=str(cert), ssl_keyfile=str(key)))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    assert ready.wait(5)
    try:
        yield origin
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.parametrize('width', [390, 1440])
def test_wider_login_logout_htmx_and_csp(app_host, tmp_path, width):
    host, _ = app_host
    with proxy_site(host, tmp_path) as origin:
        browser = _Cdp(tmp_path / 'chrome', extra_args=('--ignore-certificate-errors',))
        try:
            browser.call('Emulation.setDeviceMetricsOverride', {'width':width, 'height':900, 'deviceScaleFactor':1, 'mobile':False})
            browser.call('Page.navigate', {'url':origin+'/login'})
            browser.wait_for("document.readyState === 'complete' && location.pathname === '/login'", timeout=3)
            browser.wait_for("!!document.querySelector('form[hx-post=\"/login\"]')", timeout=3)
            assert browser.evaluate('document.documentElement.scrollWidth <= innerWidth')
            browser.evaluate("window.violations=[];document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective));")
            browser.evaluate(f"document.querySelector('[name=username]').value={json.dumps(os_login())};document.querySelector('[name=password]').value='transport-test-password';document.querySelector('form[hx-post=\"/login\"]').requestSubmit()")
            browser.wait_for("location.pathname !== '/login'", timeout=3)
            browser.wait_for("!!document.querySelector('[data-logout]')", timeout=3)
            # Native fetch and htmx must share the session-bound token.
            result = browser.evaluate("fetch('/commands/company.list',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.status)", await_promise=True)
            assert result == 200
            assert browser.evaluate("document.querySelector('meta[name=bookflow-csrf]').content.length") == 64
            assert browser.evaluate("WebAssembly.compile(new Uint8Array([0,97,115,109,1,0,0,0])).then(()=>true)", await_promise=True)
            browser.evaluate("const target=document.createElement('div');target.id='fragment-target';document.body.append(target);htmx.ajax('GET','/test-fragment',{target:'#fragment-target',swap:'innerHTML'})")
            browser.wait_for('window.fragmentRan === true', timeout=3)
            # Attempt stored-script-shaped markup without a nonce; neither script nor handler executes.
            browser.evaluate("window.injected=false;const s=document.createElement('script');s.textContent='window.injected=true';document.body.append(s)")
            assert browser.evaluate('window.injected') is False
            browser.evaluate("document.querySelector('[data-logout]').requestSubmit()")
            browser.wait_for("location.pathname === '/login' && document.readyState === 'complete' && !!document.querySelector('form[hx-post=\"/login\"]')", timeout=3)
            assert browser.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            browser.close()
