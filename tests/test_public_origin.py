"""The host behind a TLS proxy (design/https-serving.md): a configured public origin, never the Host header.

The proxy itself is not started here: these drive the origin logic directly, through the app's test client
(links, redirects, Origin/Referer checks, Secure cookies) and through `setup`, `owner-link` and `serve`.
"""

import json
import socket

import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow import BookflowError
from bookflow.commands.host_cmds import cookie_security, start_serving
from bookflow.core import public_origin as po
from bookflow.core.config import Config, os_login
from bookflow.core.context import client_version

ORIGIN = "https://colony.example.ts.net:8443"
PASSWORD = "correct-horse-battery"
WB = {"X-Bookflow-Workbench": "1"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_origin_is_normalized_and_anything_else_refused():
    assert po.normalize("HTTPS://Colony.Example.ts.net:8443/") == ORIGIN
    assert po.normalize("https://books.example:443") == "https://books.example"
    assert po.normalize("") is None and po.normalize(None) is None
    for bad in ("colony.example:8443", "ftp://x", "https://x/books", "https://u:p@x", "https://x?a=1", "https://x:99999",
                "https://x y"):
        with pytest.raises(BookflowError) as e:
            po.normalize(bad)
        assert e.value.code == "E_VALIDATION", bad
    assert po.local_origins("127.0.0.1:9123") == {"http://127.0.0.1:9123", "http://localhost:9123"}
    assert cookie_security("127.0.0.1", None, ORIGIN) is True
    assert cookie_security("127.0.0.1", None, "http://127.0.0.1:1") is False
    assert cookie_security("127.0.0.1", False, ORIGIN) is False


def test_serve_refuses_any_bind_but_loopback_with_a_public_origin(root):
    c = bookflow.connect(data_root=str(root))
    for body in ({"bind": "0.0.0.0:9000", "allow_network": True, "public_origin": ORIGIN},
                 {"bind": "10.1.2.3:9000", "allow_network": True, "public_origin": ORIGIN},
                 {"bind": "[::1]:9000", "public_origin": ORIGIN}):
        with pytest.raises(BookflowError) as e:
            c.run("serve", body)
        assert e.value.code == "E_NETWORK_NOT_ALLOWED" and "127.0.0.1 only" in e.value.message, body
    # The same refusal when the origin comes from config.toml rather than the flag.
    cfg = Config.load(root / "config.toml")
    cfg.data["host"]["public_origin"] = ORIGIN
    cfg.save()
    assert Config.load(root / "config.toml").data["host"] == {"public_origin": ORIGIN}
    with pytest.raises(BookflowError) as e:
        c.run("serve", {"bind": "0.0.0.0:9000", "allow_network": True})
    assert e.value.code == "E_NETWORK_NOT_ALLOWED" and ORIGIN in e.value.message


@pytest.fixture
def proxied(root):
    c = bookflow.connect(data_root=str(root))
    c.run("user set-password", {"username": os_login(), "password": PASSWORD})
    bind = f"127.0.0.1:{_free_port()}"
    handle = start_serving(root, client_version(), bind=bind, publish_descriptor=True, public_origin=ORIGIN,
                           secure_cookies=cookie_security("127.0.0.1", None, ORIGIN))
    try:
        yield handle, bind
    finally:
        handle.stop()


def test_behind_the_proxy_cookies_links_and_origin_checks_use_the_public_origin(proxied, root):
    handle, bind = proxied
    assert json.loads((root / "host.json").read_text())["public_origin"] == ORIGIN
    # A client that names some other Host never steers an absolute URL: a trailing-slash redirect goes to the origin.
    api = TestClient(handle.app, base_url="http://evil.example", follow_redirects=False)
    moved = api.get("/login/")
    assert moved.status_code in (307, 308) and moved.headers["location"] == f"{ORIGIN}/login"
    assert api.get("/").headers["location"] == "/login"  # the workbench's own redirects stay relative
    assert api.get("/secure-context").json() == {"public_origin": ORIGIN, "https": True}

    # Login through the proxy: the session cookie is Secure and SameSite=Lax.
    r = api.post("/login", json={"username": os_login(), "password": PASSWORD}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"].lower()
    assert "secure" in cookie and "samesite=lax" in cookie and "httponly" in cookie
    # Colony serves many projects on one hostname, ports apart: cookies are not isolated by port, so Bookflow's
    # carry a bookflow_ name and no Domain, and Bookflow never sets HSTS (it would bind the whole host).
    assert cookie.startswith("bookflow_session=") and "domain=" not in cookie
    assert "strict-transport-security" not in {k.lower() for k in r.headers}
    session = {"Cookie": f"bookflow_session={r.cookies['bookflow_session']}"}

    def post(**headers):
        return api.post("/commands/company.list", json={}, headers={**session, **headers})

    assert post(**WB, Origin=ORIGIN).status_code == 200
    assert post(**WB, Origin=f"http://{bind}").status_code == 200  # a browser on this computer, at the host itself
    assert post(**WB, Referer=f"{ORIGIN}/c/x/").status_code == 200
    for bad in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": "http://colony.example.ts.net:8443"},
                {"Origin": "https://colony.example.ts.net:9443"}, {"Origin": "https://colony.example.ts.net"},
                {"Referer": "https://colony.example.ts.net:9443/board"},
                {"Referer": "https://evil.example/page"}):
        refused = post(**WB, **bad)
        assert refused.status_code == 403 and refused.json()["code"] == "E_ORIGIN_REFUSED", bad
    # The workbench header still guards a cookie write from the right origin.
    assert post(Origin=ORIGIN).json()["code"] == "E_WORKBENCH_HEADER"
    # A program with neither header (an agent's bearer, the MCP launcher) is not affected.
    assert post(**WB).status_code == 200
    # A read is never origin-checked.
    assert api.get("/health", headers={"Origin": "https://evil.example"}).status_code == 200


def test_without_a_public_origin_nothing_changes_and_tailscale_headers_never_reach_a_handler():
    from bookflow.adapters.http.app import PublicOriginMiddleware
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope)

    import asyncio
    scope = {"type": "http", "method": "POST", "scheme": "http", "path": "/",
             "headers": [(b"host", b"127.0.0.1:1"), (b"origin", b"https://evil.example"),
                         (b"tailscale-user-login", b"ann@example.com"), (b"Tailscale-User-Name", b"Ann")]}
    asyncio.run(PublicOriginMiddleware(inner, public_origin=None, bind="127.0.0.1:1")(scope, None, None))
    assert seen[0]["scheme"] == "http"
    assert seen[0]["headers"] == [(b"host", b"127.0.0.1:1"), (b"origin", b"https://evil.example")]


@pytest.mark.timeout(600)
def test_setup_and_owner_link_hand_out_the_public_origin(tmp_path, monkeypatch):
    root, project = tmp_path / "books", tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    client = bookflow.connect(data_root=str(root))
    port = _free_port()
    out = client.run("setup", {"company": "Ruiz Plumbing", "owner_name": "Dana Ruiz", "serve": False,
                               "bind": f"127.0.0.1:{port}", "mcp_config": str(project / ".mcp.json"),
                               "public_origin": ORIGIN + "/"})
    assert out["url"] == f"{ORIGIN}/"
    assert out["owner_link"]["url"].startswith(f"{ORIGIN}/setup/")
    args = json.loads((project / ".mcp.json").read_text())["mcpServers"]["bookflow"]["args"]
    assert args[args.index("--url") + 1] == f"{ORIGIN}/"
    with pytest.raises(BookflowError) as e:
        client.run("owner-link", {"bind": f"[::1]:{port}", "public_origin": ORIGIN})
    assert e.value.code == "E_NETWORK_NOT_ALLOWED"
    # owner-link takes the origin from config.toml when no flag names it.
    cfg = Config.load(root / "config.toml")
    cfg.data["host"]["public_origin"] = "https://books.example"
    cfg.save()
    link = client.run("owner-link", {"bind": f"127.0.0.1:{port}"})
    assert link["url"].startswith("https://books.example/setup/") and link["host_running"] is False
    link = client.run("owner-link", {"bind": f"127.0.0.1:{port}", "public_origin": ORIGIN})
    assert link["url"].startswith(f"{ORIGIN}/setup/")
