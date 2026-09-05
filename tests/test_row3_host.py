"""Row 3 done sequence: the host, its credentials, the local hand-off, the event feed, and the workbench.

The host runs inside the test process, so a library call here is the host's own process and never
forwards (row 3 plan, Local hand-off). Everything a program would do goes over HTTP; the forwarded
hand-off is exercised by the CLI in a subprocess and by one hand-built envelope.
"""

import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow import BookflowError
from bookflow.commands.host_cmds import parse_bind, start_serving
from bookflow.core.config import os_login
from bookflow.core.context import client_version
from tests.conftest import as_user, make_actor

PASSWORD = "correct-horse-battery"
OUTSIDER_PASSWORD = "another-long-password"
WB = {"X-Bookflow-Workbench": "1"}


class Hosted:
    """One running host, an HTTP client with a bearer token, and the identities the tests act as."""

    def __init__(self, handle, root, login, company_id, issued, outsider_id, company_list):
        self.handle, self.root, self.login, self.company_id = handle, root, login, company_id
        self.token, self.secret, self.outsider_id = issued["token_id"], issued["secret"], outsider_id
        self.company_list = company_list
        self.api = TestClient(handle.app)
        self.bearer = {"Authorization": f"Bearer {self.secret}"}

    def call(self, name, body=None, *, company=None, headers=None, client=None):
        api = client or self.api
        path = f"/companies/{company}/commands/{name}" if company else f"/commands/{name}"
        return api.post(path, json=body if body is not None else {}, headers={**self.bearer, **(headers or {})})

    def ok(self, name, body=None, **kw):
        r = self.call(name, body, **kw)
        assert r.status_code == 200, r.text
        return r.json()

    def info(self):
        return self.ok("company.show", company=self.company_id)


@pytest.fixture
def hosted(root):
    login = os_login()
    c = bookflow.connect(data_root=str(root))
    company_id = c.company.list()["items"][0]["company_id"]
    company_list = c.company.list()
    c.run("user set-password", {"username": login, "password": PASSWORD})
    outsider_id = make_actor(root, "outsider")
    c.run("user set-password", {"username": "outsider", "password": OUTSIDER_PASSWORD})
    issued = c.token.issue(label="robot-one")
    handle = start_serving(root, client_version(), bind="127.0.0.1:8765", secure_cookies=False)
    try:
        yield Hosted(handle, root, login, company_id, issued, outsider_id, company_list)
    finally:
        handle.stop()


@pytest.fixture
def live(hosted):
    """The host's app behind a real uvicorn server, for the one test that needs a live stream."""
    import socket

    import uvicorn
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(hosted.handle.app, log_level="warning", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the server did not start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        try:
            sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- authentication

def test_login_sets_a_cookie_and_writes_need_the_workbench_header(hosted):
    api = TestClient(hosted.handle.app)
    bad = api.post("/login", json={"username": hosted.login, "password": "wrong"})
    assert bad.status_code == 401 and bad.json()["code"] == "E_LOGIN_FAILED"
    r = api.post("/login", json={"username": hosted.login, "password": PASSWORD})
    assert r.status_code == 200 and r.cookies.get("bookflow_session")
    naked = api.post("/commands/company.list", json={})
    assert naked.status_code == 403 and naked.json()["code"] == "E_WORKBENCH_HEADER"
    ok = api.post("/commands/company.list", json={}, headers=WB)
    assert ok.status_code == 200 and ok.json()["count"] == 1
    # the login is a hub event
    events = hosted.ok("hub.audit.list", {"command": "login", "limit": 5})["items"]
    assert events and events[0]["interface"] == "http"


def test_bearer_returns_the_same_document_as_the_library(hosted):
    over_http = hosted.call("company.list")
    assert over_http.status_code == 200
    assert over_http.json() == hosted.company_list


def test_no_credential_and_a_revoked_bearer_are_401(hosted):
    none = hosted.api.post("/commands/company.list", json={})
    assert none.status_code == 401 and none.json()["code"] == "E_UNAUTHENTICATED"
    assert hosted.ok("token.revoke", {"token": hosted.token})["changed"] is True
    gone = hosted.call("company.list")
    assert gone.status_code == 401 and gone.json()["details"]["reason"] == "revoked"


def test_authentication_precedes_route_and_company_header_diagnostics(hosted):
    unknown = hosted.api.post("/commands/not.a.command", json={})
    mismatched = hosted.api.post(
        f"/companies/{hosted.company_id}/commands/company.show",
        json={},
        headers={"X-Bookflow-Company": GHOST},
    )
    for response in (unknown, mismatched):
        assert response.status_code == 401
        assert response.json()["code"] == "E_UNAUTHENTICATED"
        assert response.json()["details"]["reason"] == "no credential"


def test_token_commands(hosted):
    listed = hosted.ok("token.list")
    assert [t["token_id"] for t in listed["items"]] == [hosted.token]
    assert all("token_hash" not in t for t in listed["items"])
    missing = hosted.call("token.revoke", {"token": "01ARZ3NDEKTSV4RRFFQ69G5FAV"})
    assert missing.status_code == 404 and missing.json()["code"] == "E_TOKEN_NOT_FOUND"
    unknown = hosted.call("token.issue", {"label": "x", "user": "nobody-at-all"})
    assert unknown.status_code == 404 and unknown.json()["code"] == "E_USER_NOT_FOUND"
    not_agent = hosted.call("token.issue", {"label": "x", "principal": hosted.login})
    assert not_agent.status_code == 422 and not_agent.json()["details"]["fields"][0]["field"] == "principal"

    second = hosted.ok("token.issue", {"label": "robot-two", "days": 7})
    assert second["expires_at"] and "shown once" in second["message"]
    again = hosted.api.post("/commands/company.list", json={}, headers={"Authorization": f"Bearer {second['secret']}"})
    assert again.status_code == 200
    assert {t["label"] for t in hosted.ok("token.list")["items"]} == {"robot-one", "robot-two"}

    # the secret never reaches the audit trail; the hash is masked
    events = hosted.ok("hub.audit.list", {"command": "token issue", "limit": 5})["items"]
    entry = hosted.ok("hub.audit.show", {"event": events[0]["id"]})["entries"][0]
    assert entry["record_type"] == "api_token" and "token_hash" not in entry["after"]
    assert second["secret"] not in json.dumps(entry)


def test_a_non_admin_cannot_issue_for_anyone_else(hosted):
    other = TestClient(hosted.handle.app)
    assert other.post("/login", json={"username": "outsider", "password": OUTSIDER_PASSWORD}).status_code == 200
    for user in (hosted.login, "nobody-at-all"):
        r = other.post("/commands/token.issue", json={"label": "sneaky", "user": user}, headers=WB)
        assert r.status_code == 403 and r.json()["code"] == "E_PERMISSION", user
    mine = other.post("/commands/token.issue", json={"label": "mine"}, headers=WB)
    assert mine.status_code == 200 and mine.json()["username"] == "outsider"
    mine_only = other.post("/commands/token.list", json={}, headers=WB).json()["items"]
    assert {t["label"] for t in mine_only} == {"mine", "browser session"}
    assert {t["user_id"] for t in mine_only} == {hosted.outsider_id}
    # and cannot revoke someone else's: the answer is the one for a token that does not exist, so nothing is enumerable
    r = other.post("/commands/token.revoke", json={"token": hosted.token}, headers=WB)
    assert r.status_code == 404 and r.json()["code"] == "E_TOKEN_NOT_FOUND"


def test_password_reset_over_http_preserves_only_the_calling_session_and_bearers(hosted):
    first = TestClient(hosted.handle.app)
    second = TestClient(hosted.handle.app)
    assert first.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    assert second.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    old_second = second.cookies["bookflow_session"]

    changed = first.post("/commands/user.set-password", json={
        "username": hosted.login, "password": "a-new-long-password",
    }, headers=WB)
    assert changed.status_code == 200 and changed.json()["changed"] is True
    assert first.post("/commands/company.list", json={}, headers=WB).status_code == 200
    replay = hosted.api.post("/commands/company.list", json={}, headers={
        **WB, "Cookie": f"bookflow_session={old_second}",
    })
    assert replay.status_code == 401 and replay.json()["details"]["reason"] == "revoked"
    assert hosted.call("company.list").status_code == 200, "password resets leave bearer tokens alone"


def test_non_admin_password_targets_are_non_enumerating(hosted):
    other = TestClient(hosted.handle.app)
    assert other.post("/login", json={"username": "outsider", "password": OUTSIDER_PASSWORD}).status_code == 200
    real = other.post("/commands/user.set-password", json={"username": hosted.login, "password": PASSWORD}, headers=WB)
    ghost = other.post("/commands/user.set-password", json={"username": "nobody-at-all", "password": PASSWORD}, headers=WB)
    assert real.status_code == ghost.status_code == 403
    assert real.content == ghost.content
    assert real.json()["details"] == {"capability": "user", "required_role": "self"}


def test_token_list_hides_expired_tokens_by_default(hosted):
    issued = hosted.ok("token.issue", {"label": "already-expired", "days": 1})
    hosted.handle.host.submit(lambda: hosted.handle.host._hub.raw.execute(
        "UPDATE api_tokens SET expires_at = '2001-01-01T00:00:00.000Z' WHERE id = ?", (issued["token_id"],)))
    visible = {row["token_id"] for row in hosted.ok("token.list")["items"]}
    complete = {row["token_id"] for row in hosted.ok("token.list", {"include_revoked": True})["items"]}
    assert issued["token_id"] not in visible and issued["token_id"] in complete


def test_set_password_is_self_service_and_validated(root):
    c = bookflow.connect(data_root=str(root))
    with pytest.raises(BookflowError) as e:
        c.run("user set-password", {"username": os_login()})
    assert e.value.code == "E_VALIDATION" and e.value.details["fields"][0]["field"] == "password"
    with pytest.raises(BookflowError) as e:
        c.run("user set-password", {"username": "nobody", "password": "x" * 12})
    assert e.value.code == "E_USER_NOT_FOUND"
    out = c.run("user set-password", {"username": os_login(), "password": PASSWORD})
    assert out["changed"] and out["username"] == os_login()
    # the hash is masked in the audit snapshot, and the row version moved
    event = c.run("hub audit list", {"command": "user set-password", "limit": 1})["items"][0]
    entry = c.run("hub audit show", {"event": event["id"]})["entries"][0]
    assert entry["record_type"] == "user" and entry["after"]["password_hash"].startswith("sha256:")
    assert entry["version_after"] == entry["version_before"] + 1
    make_actor(root, "plain")
    with pytest.raises(BookflowError) as e:
        as_user(root, "plain").run("user set-password", {"username": os_login(), "password": PASSWORD})
    assert e.value.code == "E_PERMISSION"
    assert e.value.details == {"capability": "user", "required_role": "self"}
    assert as_user(root, "plain").run("user set-password", {"username": "plain", "password": PASSWORD})["changed"]


# ---------------------------------------------------------------- commands over HTTP

def test_a_write_over_http_is_audited_with_the_token_label(hosted):
    r = hosted.call("company.update", {"phone": "555-0100"}, company=hosted.company_id,
                    headers={"X-Bookflow-Reason": "the customer called"})
    assert r.status_code == 200, r.text
    events = hosted.ok("audit.list", {"command": "company update", "limit": 5}, company=hosted.company_id)["items"]
    assert events[0]["interface"] == "http" and events[0]["client_name"] == "robot-one"
    assert events[0]["reason"] == "the customer called"
    assert hosted.info()["info"]["phone"] == "555-0100"


def test_a_dry_run_over_http_writes_nothing(hosted):
    before = hosted.info()["info"]["fax"]
    r = hosted.api.post(f"/companies/{hosted.company_id}/commands/company.update?dry_run=true",
                        json={"fax": "555-0000"}, headers=hosted.bearer)
    assert r.status_code == 200 and r.json()["dry_run"] is True
    assert hosted.info()["info"]["fax"] == before


def test_context_in_the_body_and_local_only_names_are_refused(hosted):
    bad = hosted.call("company.update", {"reason": "no"}, company=hosted.company_id)
    assert bad.status_code == 400 and bad.json()["code"] == "E_CONTEXT_IN_INPUT"
    assert "X-Bookflow-Reason" in bad.json()["message"]
    for name in ("company.use", "serve", "company.frobnicate"):
        r = hosted.call(name)
        assert r.status_code == 400 and r.json()["code"] == "E_USAGE", name


def test_a_non_member_cannot_see_another_company(hosted):
    other = TestClient(hosted.handle.app)
    assert other.post("/login", json={"username": "outsider", "password": OUTSIDER_PASSWORD}).status_code == 200
    r = other.post(f"/companies/{hosted.company_id}/commands/company.show", json={}, headers=WB)
    assert r.status_code == 404 and r.json()["code"] == "E_COMPANY_NOT_FOUND"
    invented = other.post("/companies/01ARZ3NDEKTSV4RRFFQ69G5FAV/commands/company.show", json={}, headers=WB)
    assert invented.status_code == 404 and invented.json() == r.json()
    assert other.post("/commands/company.list", json={}, headers=WB).json()["count"] == 0


def test_openapi_lists_the_routed_commands_only(hosted):
    from bookflow.core import registry
    doc = hosted.api.get("/openapi.json").json()
    registry.load_all()
    expected = {
        (f"/commands/{cmd.name.replace(' ', '.')}" if cmd.scope == "hub" else
         f"/companies/{{company_id}}/commands/{cmd.name.replace(' ', '.')}")
        for cmd in registry.all_commands() if not cmd.local_only
    }
    actual = {path for path, methods in doc["paths"].items() if path != "/login" and "post" in methods}
    assert actual == expected
    assert "/commands/serve" not in doc["paths"] and "/commands/user.set-password" in doc["paths"]
    for path in expected:
        op = doc["paths"][path]["post"]
        assert op["summary"].endswith(".") and op["security"] == [{"bearer": []}, {"cookie": []}]
        assert "E_UNAUTHENTICATED" in op["x-bookflow-error-codes"]
        assert op["requestBody"]["required"] is True
        if "/companies/" in path:
            company = next(p for p in op["parameters"] if p["name"] == "X-Bookflow-Company")
            assert "must be the same company id" in company["description"]


def test_the_event_stream_delivers_a_write_as_it_happens(hosted, live):
    """A real server, because the test client runs an app to completion and an event stream never ends."""
    import httpx
    cid = hosted.company_id
    start = hosted.ok("audit.tail", {"limit": 1}, company=cid)["high_water"] or 0

    def write_later():
        time.sleep(0.4)
        httpx.post(f"{live}/companies/{cid}/commands/company.update", json={"fax": "555-0199"},
                   headers=hosted.bearer, timeout=10.0)

    threading.Thread(target=write_later, daemon=True).start()
    began = time.monotonic()
    seen = []
    with httpx.stream("GET", f"{live}/companies/{cid}/events?after={start}", headers=hosted.bearer, timeout=10.0) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            seen.append(line)
            if line.startswith("event: audit"):
                break
    assert any(line.startswith("event: audit") for line in seen)
    assert any(line.startswith("id: ") for line in seen)
    assert time.monotonic() - began < 5.0
    assert hosted.info()["info"]["fax"] == "555-0199"


# ---------------------------------------------------------------- the local hand-off

def test_a_cli_call_forwards_to_the_running_host(hosted, cli):
    out = cli.json("company", "list")
    assert out["count"] == 1 and out["items"][0]["company_id"] == hosted.company_id
    cli.json("company", "update", "--industry", "Forwarded Plumbing", "--company", hosted.company_id)
    events = hosted.ok("audit.list", {"command": "company update", "limit": 5}, company=hosted.company_id)["items"]
    assert events[0]["interface"] == "cli" and events[0]["client_name"] == "bookflow-cli"
    assert hosted.info()["info"]["industry"] == "Forwarded Plumbing"


def test_a_forged_actor_id_in_the_envelope_is_ignored(hosted):
    from bookflow.core import forward
    from bookflow.core.context import Context, Interface
    ctx = Context.new(Interface.system, "forger").model_dump(mode="json")
    ctx["actor_id"] = hosted.outsider_id
    ctx["actor_kind"] = "agent"
    ctx["on_behalf_of"] = hosted.outsider_id
    envelope = {"command": "company update", "input": {"website": "https://forged.example"},
                "company_selector": hosted.company_id.lower(), "company_source": "option", "dry_run": False, "context": ctx}
    reply = forward.call_host(str(hosted.handle.socket), envelope)
    assert reply is not None and "output" in reply, reply
    event = hosted.ok("audit.list", {"command": "company update", "limit": 1}, company=hosted.company_id)["items"][0]
    assert event["actor_id"] != hosted.outsider_id and event["actor_name"] != "Outsider"
    assert event["on_behalf_of"] is None and event["client_name"] == "forger"
    assert event["interface"] == "cli"


def test_a_version_mismatch_over_the_socket_is_named(hosted):
    from bookflow.core import forward
    from bookflow.core.context import Context, Interface
    ctx = Context.new(Interface.cli, "old-client").model_dump(mode="json") | {"client_version": "0.0.0-old"}
    reply = forward.call_host(str(hosted.handle.socket), {"command": "company list", "input": {}, "context": ctx})
    assert reply["error"]["code"] == "E_VERSION_MISMATCH"
    assert reply["error"]["details"] == {"host": client_version(), "client": "0.0.0-old"}


def test_a_second_host_reports_the_lock_holder(hosted, root):
    with pytest.raises(BookflowError) as e:
        start_serving(root, client_version(), bind="127.0.0.1:8766")
    assert e.value.code == "E_DB_BUSY" and e.value.details["command"] == "serve"
    assert "already serving this data root" in e.value.message

    # Even when the requested address is already this host's address, the
    # data-root owner is the deliberate error—not a lower-level bind failure.
    with pytest.raises(BookflowError) as e:
        bookflow.connect(data_root=str(root)).run("serve", {"bind": "127.0.0.1:8765"})
    assert e.value.code == "E_DB_BUSY" and e.value.details["command"] == "serve"


# ---------------------------------------------------------------- serve itself

def test_serve_is_not_a_write_command_and_bind_validation(root, cli):
    c = bookflow.connect(data_root=str(root))
    with pytest.raises(BookflowError) as e:
        c.run("serve", {}, dry_run=True)
    assert e.value.code == "E_USAGE"
    help_text = cli.run("serve", "--help").stdout
    assert "--dry-run" not in help_text and "--reason" not in help_text and "--source-ref" not in help_text
    for bind, code in (("127.0.0.1", "E_VALIDATION"), ("127.0.0.1:0", "E_VALIDATION"),
                       ("10.1.2.3:9000", "E_NETWORK_NOT_ALLOWED")):
        with pytest.raises(BookflowError) as e:
            c.run("serve", {"bind": bind})
        assert e.value.code == code, bind
    assert parse_bind("[::1]:8765") == ("::1", 8765) and parse_bind("localhost:1") == ("localhost", 1)


def test_serve_needs_a_hub_admin_and_an_initialized_root(root, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BookflowError) as e:
        bookflow.connect(data_root=str(empty)).run("serve", {})
    assert e.value.code == "E_NOT_INITIALIZED"
    make_actor(root, "plain")
    with pytest.raises(BookflowError) as e:
        as_user(root, "plain").run("serve", {})
    assert e.value.code == "E_PERMISSION"

    make_actor(
        root, "serve-agent", hub_admin=True, login="serve-agent-login",
        kind="agent", owner_user_id=_admin_id(root),
    )
    with pytest.raises(BookflowError) as e:
        as_user(root, "serve-agent-login").run("serve", {})
    assert e.value.code == "E_PERMISSION"
    assert e.value.details["required_role"] == "human"


def test_the_host_migrates_and_records_the_descriptor(hosted, root):
    descriptor = json.loads((root / "host.json").read_text())
    assert descriptor["bind"] == "127.0.0.1:8765" and descriptor["version"] == client_version()
    assert descriptor["socket"] == str(hosted.handle.socket) and hosted.handle.socket.exists()
    assert hosted.handle.companies_failed == [] and hosted.handle.companies_migrated == []
    startup = hosted.ok("hub.audit.list", {"command": "upgrade", "limit": 5})["items"]
    assert all(e["interface"] in ("system", "cli", "python") for e in startup)
    hosted.handle.stop()
    assert not (root / "host.json").exists() and not hosted.handle.socket.exists()
    # with the host gone the lock is free again and an ordinary command takes it
    assert bookflow.connect(data_root=str(root)).company.list()["count"] == 1


def test_a_company_that_cannot_migrate_does_not_stop_the_host(root, tmp_path):
    """A registered company whose folder has gone is reported and the rest is served."""
    c = bookflow.connect(data_root=str(root))
    c.organization.new(name="Second Org")
    made = c.company.new(legal_name="Gone Co", home_currency="USD", organization="Second Org", timezone="UTC")
    import shutil
    shutil.rmtree(made["path"])
    handle = start_serving(root, client_version(), bind="127.0.0.1:8767")
    try:
        assert handle.companies_failed == [{"company_id": made["company_id"], "code": "E_COMPANY_MISSING"}]
        assert TestClient(handle.app).get("/health").json() == {"ok": True}
    finally:
        handle.stop()


# ---------------------------------------------------------------- the workbench

def test_workbench_pages_and_a_generated_form(hosted):
    api = TestClient(hosted.handle.app)
    anonymous = api.get("/", follow_redirects=False)
    assert anonymous.status_code == 303 and anonymous.headers["location"] == "/login"
    assert api.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    assert "Demo Plumbing Co" in api.get("/").text
    assert "Demo Plumbing Co" in api.get(f"/c/{hosted.company_id}/").text

    show = hosted.info()
    phone = show["info"]["phone"]
    form = api.get(f"/c/{hosted.company_id}/company/self/update")
    assert form.status_code == 200 and f'name="f:phone" value="{phone}"' in form.text
    assert 'name="originals"' in form.text and 'name="ctx:reason"' in form.text

    originals = {**show["info"], "expected_version": show["info_version"]}
    body = {"originals": json.dumps(originals, default=str), "f:phone": "555-9", "action": "submit"}
    posted = api.post(f"/c/{hosted.company_id}/company/self/update", data=body, headers=WB)
    assert posted.status_code == 200, posted.text
    assert hosted.info()["info"]["phone"] == "555-9"

    # the same POST without the workbench header is refused, and writes nothing
    csrf = {"originals": json.dumps({**originals, "phone": "555-9"}, default=str), "f:phone": "555-CSRF", "action": "submit"}
    refused = api.post(f"/c/{hosted.company_id}/company/self/update", data=csrf)
    assert refused.status_code == 403 and "E_WORKBENCH_HEADER" in refused.text
    assert hosted.info()["info"]["phone"] == "555-9"

    # an untouched field sends nothing: resubmitting the form as shown writes no event
    before = hosted.ok("audit.list", {"command": "company update"}, company=hosted.company_id)["count"]
    same = {"originals": json.dumps({**originals, "phone": "555-9"}, default=str), "f:phone": "555-9", "action": "submit"}
    assert api.post(f"/c/{hosted.company_id}/company/self/update", data=same, headers=WB).status_code == 200
    assert hosted.ok("audit.list", {"command": "company update"}, company=hosted.company_id)["count"] == before

    # the record page carries the audit trail
    record = api.get(f"/c/{hosted.company_id}/company/self")
    assert record.status_code == 200 and "555-9" in record.text and "company update" in record.text


def test_workbench_preview_runs_the_dry_run(hosted):
    api = TestClient(hosted.handle.app)
    api.post("/login", json={"username": hosted.login, "password": PASSWORD})
    show = hosted.info()
    body = {"originals": json.dumps({**show["info"], "expected_version": show["info_version"]}, default=str),
            "f:fax": "555-1234", "action": "preview"}
    r = api.post(f"/c/{hosted.company_id}/company/self/update", data=body, headers=WB)
    assert r.status_code == 200 and "Preview (nothing written)" in r.text
    assert hosted.info()["info"]["fax"] != "555-1234"


# ---------------------------------------------------------------- shared helpers for the rest of the row

GHOST = "01ARZ3NDEKTSV4RRFFQ69G5FAV"  # a well-formed ULID that names nothing
VOLATILE = {"seconds_since_update", "hold_seconds"}  # details that move with the clock


def _admin_id(root):
    from bookflow.core.config import Config
    return Config.load(root / "config.toml").user_table(os_login())["user_id"]


def _hub_sql(root, statement, params=()):
    import sqlite3
    conn = sqlite3.connect(str(root / "hub.db"), timeout=10)
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _hub_read(root, statement, params=()):
    import sqlite3
    conn = sqlite3.connect(f"file:{root / 'hub.db'}?mode=ro", uri=True, timeout=10)
    try:
        return conn.execute(statement, params).fetchall()
    finally:
        conn.close()


def _days_from_now(days):
    from datetime import timedelta

    from bookflow.core import clock
    return (clock.now() + timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _insert_session(root, *, user_id, label, expires_at):
    """A browser-session token row straight into the hub, so the sweep has something to find."""
    from bookflow.core.ids import new_id
    from bookflow.core.session import now_iso
    tid, at = new_id(), now_iso()
    _hub_sql(root,
             "INSERT INTO api_tokens (id, version, created_at, created_by, created_via, updated_at, updated_by,"
             " updated_via, user_id, on_behalf_of, kind, token_hash, label, expires_at, last_used_at, revoked_at)"
             " VALUES (?, 1, ?, ?, 'test', ?, ?, 'test', ?, NULL, 'session', ?, ?, ?, ?, NULL)",
             (tid, at, user_id, at, user_id, user_id, "hash-" + tid, label, expires_at, at))
    return tid


def _strip(details):
    return {k: v for k, v in (details or {}).items() if k not in VOLATILE}


# ---------------------------------------------------------------- parity: library and HTTP

def _read_calls(hosted):
    """One call per routed read command, with real identifiers taken from the running host."""
    cid = hosted.company_id
    event = hosted.ok("audit.list", {"limit": 1}, company=cid)["items"][0]["id"]
    hub_event = hosted.ok("hub.audit.list", {"limit": 1})["items"][0]["id"]
    directive = hosted.ok("directive.list", {}, company=cid)["items"][0]
    note = hosted.ok("note.list", {"record_type": "company_info", "record_id": cid}, company=cid)["items"][0]
    org = hosted.ok("organization.list")["items"][0]
    calls = {
        "audit list": ({"limit": 5}, cid),
        "audit show": ({"event": event}, cid),
        "audit tail": ({"limit": 5}, cid),
        "company list": ({}, None),
        "company show": ({}, cid),
        "directive list": ({}, cid),
        "directive show": ({"directive": directive["code"]}, cid),
        "note list": ({"record_type": "company_info", "record_id": cid}, cid),
        "note show": ({"note": note["id"]}, cid),
        "hub audit list": ({"limit": 5}, None),
        "hub audit show": ({"event": hub_event}, None),
        "hub audit tail": ({"limit": 5}, None),
        "organization list": ({}, None),
        "organization show": ({"organization": org["organization_id"]}, None),
        "token list": ({}, None),
        "chart list": ({}, None),
        "chart show": ({"template_id": "general"}, None),
        "profile list": ({}, None),
        "profile show": ({"profile_id": "standard"}, None),
    }
    create_inputs = {
        "custom-field": {"name": "HTTP parity field", "kind": "text", "scopes": ["customer"]},
        "item-category": {"name": "HTTP parity category"},
        "class": {"name": "HTTP parity class"},
        "customer-type": {"name": "HTTP parity customer type"},
        "vendor-type": {"name": "HTTP parity vendor type"},
        "job-type": {"name": "HTTP parity job type"},
        "price-level": {"name": "HTTP parity pricing", "kind": "fixed_percent", "percent": "-2"},
        "unit-of-measure": {
            "name": "HTTP parity count",
            "units": [{"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"}],
        },
    }
    for noun, body in create_inputs.items():
        hosted.ok(f"{noun}.create", body, company=cid)

    company_db = Path(hosted.info()["path"]) / "company.db"
    with sqlite3.connect(company_db) as conn:
        actor_id = conn.execute("SELECT created_by FROM company_info").fetchone()[0]
        source_id = "01ARZ3NDEKTSV4RRFFQ69G5FAA"
        at = "2026-09-04T00:00:00.000Z"
        conn.execute(
            "INSERT INTO employees (id,version,created_at,created_by,created_via,updated_at,updated_by,updated_via,active,seed_key,name,name_key) "
            "VALUES (?,1,?,?, 'system',?,?, 'system',1,NULL,?,?)",
            (source_id, at, actor_id, at, actor_id, "HTTP parity employee", "http parity employee"),
        )
    hosted.ok(
        "sales-rep.create",
        {"name": "HTTP parity rep", "initials": "HP", "name_type": "employee", "name_id": source_id},
        company=cid,
    )

    supporting = (
        "account", "customer", "vendor", "employee", "other-name", "item",
        "custom-field", "item-category", "class", "term", "payment-method", "price-level",
        "sales-tax-code", "unit-of-measure",
        "customer-type", "vendor-type", "job-type", "sales-rep", "ship-method",
        "customer-message",
    )
    for noun in supporting:
        listed = hosted.ok(f"{noun}.list", company=cid)
        assert listed["items"]
        calls[f"{noun} list"] = ({}, cid)
        calls[f"{noun} query"] = ({"limit": 2}, cid)
        calls[f"{noun} show"] = ({noun.replace("-", "_"): listed["items"][0]["id"]}, cid)
    return calls


def test_every_routed_read_returns_the_same_document_over_http_as_in_the_library(hosted, root):
    from bookflow.core import registry
    from tests.test_row1_flow import normalize
    registry.load_all()
    calls = _read_calls(hosted)
    routed_reads = {c.name for c in registry.routed_commands() if c.kind == "read"}
    assert routed_reads == set(calls), "every routed read command needs a parity call here"
    over_http = {name: hosted.ok(name.replace(" ", "."), body, company=company) for name, (body, company) in calls.items()}
    hosted.handle.stop()  # the library takes the data-root lock, so the host lets go first
    c = bookflow.connect(data_root=str(root))
    for name, (body, company) in calls.items():
        assert normalize(c.run(name, body, company=company)) == normalize(over_http[name]), name


def test_write_errors_carry_the_same_document_and_the_mapped_status(hosted, root):
    cid = hosted.company_id
    version = hosted.info()["info_version"]
    assert hosted.ok("company.update", {"phone": "555-9101", "expected_version": version}, company=cid)["version"] == version + 1
    over_http = {
        "E_VALIDATION": (422, hosted.call("company.update", {"expected_version": "not-a-number"}, company=cid)),
        "E_COMPANY_NOT_FOUND": (404, hosted.call("company.show", {}, company=GHOST)),
        "E_VERSION_CONFLICT": (409, hosted.call("company.update", {"phone": "555-9202", "expected_version": version}, company=cid)),
    }
    for code, (status, r) in over_http.items():
        assert r.status_code == status, (code, r.status_code, r.text)
        assert set(r.json()) == {"code", "message", "details"} and r.json()["code"] == code

    outsider = TestClient(hosted.handle.app)
    assert outsider.post("/login", json={"username": "outsider", "password": OUTSIDER_PASSWORD}).status_code == 200
    denied = outsider.post("/commands/token.issue", json={"label": "sneaky", "user": hosted.login}, headers=WB)
    assert denied.status_code == 403 and denied.json()["code"] == "E_PERMISSION"

    hosted.handle.stop()
    c = bookflow.connect(data_root=str(root))
    library = {}
    for name, body, company in (("company update", {"expected_version": "not-a-number"}, cid),
                                ("company show", {}, GHOST),
                                ("company update", {"phone": "555-9202", "expected_version": version}, cid)):
        with pytest.raises(BookflowError) as e:
            c.run(name, body, company=company)
        library[e.value.code] = e.value.to_dict()
    with pytest.raises(BookflowError) as e:
        as_user(root, "outsider").run("token issue", {"label": "sneaky", "user": hosted.login})
    library["E_PERMISSION"] = e.value.to_dict()

    for code, (_, r) in over_http.items():
        assert _strip(library[code]["details"]) == _strip(r.json()["details"]), code
    assert library["E_PERMISSION"]["details"] == denied.json()["details"]
    assert set(library) == {"E_VALIDATION", "E_COMPANY_NOT_FOUND", "E_VERSION_CONFLICT", "E_PERMISSION"}


# ---------------------------------------------------------------- isolation

def test_a_member_of_one_company_cannot_reach_another_by_id_header_or_page(hosted, root):
    hosted.ok("organization.new", {"name": "Second Org"})
    beta = hosted.ok("company.new", {"legal_name": "Beta Books LLC", "home_currency": "USD",
                                     "organization": "Second Org", "timezone": "UTC"})["company_id"]
    make_actor(root, "membera", company_role=(hosted.company_id, "standard"))
    issued = hosted.ok("token.issue", {"user": "membera", "label": "member-a"})
    hdr = {"Authorization": f"Bearer {issued['secret']}"}
    api = TestClient(hosted.handle.app)

    by_path = api.post(f"/companies/{beta}/commands/company.show", json={}, headers=hdr)
    by_ghost = api.post(f"/companies/{GHOST}/commands/company.show", json={}, headers=hdr)
    by_header = api.post(f"/companies/{hosted.company_id}/commands/company.show", json={},
                         headers={**hdr, "X-Bookflow-Company": beta})
    by_ghost_header = api.post(f"/companies/{GHOST}/commands/company.show", json={},
                               headers={**hdr, "X-Bookflow-Company": beta})
    for r in (by_path, by_ghost):
        assert r.status_code == 404 and r.json()["code"] == "E_COMPANY_NOT_FOUND"
    assert by_path.json() == by_ghost.json()
    assert by_header.status_code == 422 and by_header.json()["code"] == "E_VALIDATION"
    assert by_header.json() == by_ghost_header.json(), "a path/header mismatch does not look either company up"

    before_a = hosted.info()
    before_b = hosted.ok("company.show", company=beta)
    rejected_write = api.post(
        f"/companies/{hosted.company_id}/commands/company.update",
        json={"phone": "555-NEVER"},
        headers={**hdr, "X-Bookflow-Company": beta},
    )
    assert rejected_write.status_code == 422 and rejected_write.json() == by_header.json()
    after_a = hosted.info()
    after_b = hosted.ok("company.show", company=beta)
    assert after_a["info_version"] == before_a["info_version"]
    assert after_b["info_version"] == before_b["info_version"]
    assert after_a["info"].get("phone") != "555-NEVER" and after_b["info"].get("phone") != "555-NEVER"

    assert api.post(f"/companies/{hosted.company_id}/commands/company.show", json={}, headers=hdr).status_code == 200
    assert api.post("/commands/company.list", json={}, headers=hdr).json()["count"] == 1

    page_beta = api.get(f"/c/{beta}/", headers=hdr)
    page_ghost = api.get(f"/c/{GHOST}/", headers=hdr)
    assert page_beta.status_code == 404 and page_ghost.status_code == 404
    assert page_beta.text == page_ghost.text
    assert "Beta Books" not in page_beta.text

    # nothing the member can provoke names a path on this machine
    provoked = [by_path, by_ghost, by_header,
                api.post(f"/companies/{beta}/commands/company.update", json={"phone": "1"}, headers=hdr),
                api.post(f"/companies/{beta}/commands/audit.list", json={}, headers=hdr),
                api.post("/commands/organization.show", json={"organization": "Second Org"}, headers=hdr),
                api.post("/commands/company.detach", json={"company": beta}, headers=hdr),
                api.post("/commands/company.attach", json={"path": str(root / "organizations")}, headers=hdr),
                api.post("/commands/token.issue", json={"label": "x", "user": hosted.login}, headers=hdr),
                api.post("/commands/upgrade", json={}, headers=hdr)]
    blob = json.dumps([r.json() for r in provoked]) + page_beta.text + page_ghost.text
    assert str(root) not in blob and "/organizations/" not in blob


# ---------------------------------------------------------------- the local hand-off

def test_a_forwarded_company_use_writes_the_callers_login_table(hosted, cli, root):
    from bookflow.core.config import Config
    out = cli.json("company", "use", hosted.company_id)
    assert out["company_id"] == hosted.company_id
    assert Config.load(root / "config.toml").user_table(os_login())["default_company"] == hosted.company_id
    events = hosted.ok("hub.audit.list", {"command": "company use", "limit": 5})["items"]
    assert events and events[0]["interface"] == "cli" and events[0]["client_name"] == "bookflow-cli"


def test_a_connection_from_another_uid_is_refused(hosted, monkeypatch):
    from bookflow.adapters.http import local
    from bookflow.core import forward
    from bookflow.core.context import Context, Interface
    monkeypatch.setattr(local, "peer_login", lambda conn: "nobody-with-a-mapping")
    ctx = Context.new(Interface.cli, "bookflow-cli").model_dump(mode="json")
    reply = forward.call_host(str(hosted.handle.socket), {"command": "company list", "input": {}, "context": ctx})
    assert reply["error"]["code"] == "E_UNAUTHENTICATED"
    assert "not mapped" in reply["error"]["details"]["reason"]


def test_serve_and_init_are_never_forwarded(hosted, root):
    from bookflow.core import forward, registry
    from bookflow.core.context import Context, Interface
    registry.load_all()
    ctx = Context.new(Interface.cli, "bookflow-cli")
    for name in ("serve", "init"):
        cmd = registry.get(name)
        assert cmd.local_only and cmd.bootstrap, name
        assert forward.try_forward(root, cmd, {}, ctx, None, "option", False) is None, name


def test_a_stale_descriptor_falls_back_to_the_lock_path(root):
    import errno
    import os
    from bookflow.core import forward
    dead_socket = root / "no-such.sock"

    # a descriptor whose pid is alive but whose socket refuses: the caller takes the ordinary lock path
    (root / "host.json").write_text(json.dumps(
        {"pid": os.getppid(), "bind": "127.0.0.1:1", "socket": str(dead_socket), "version": client_version()}))
    assert bookflow.connect(data_root=str(root)).company.list()["count"] == 1
    assert forward.call_host(str(dead_socket), {"command": "company list"}) is None

    dead_pid = 0
    for candidate in range(300000, 200000, -1):
        try:
            os.kill(candidate, 0)
        except OSError as e:
            if e.errno == errno.ESRCH:
                dead_pid = candidate
                break
    assert dead_pid, "no free pid to stand in for a dead host"
    (root / "host.json").write_text(json.dumps(
        {"pid": dead_pid, "bind": "127.0.0.1:1", "socket": str(dead_socket), "version": client_version()}))
    assert bookflow.connect(data_root=str(root)).company.list()["count"] == 1


# ---------------------------------------------------------------- concurrency

def test_two_reads_are_not_held_up_by_a_long_write(hosted, live, monkeypatch):
    import concurrent.futures

    import httpx
    host = hosted.handle.host
    gate = threading.Event()
    write_calls = []
    real_run_write = host.run_write

    def counted_run_write(*args, **kwargs):
        write_calls.append(1)
        return real_run_write(*args, **kwargs)

    monkeypatch.setattr(host, "run_write", counted_run_write)

    def slow():
        gate.set()
        time.sleep(1.0)

    blocker = threading.Thread(target=lambda: host.submit(slow), daemon=True)
    blocker.start()
    assert gate.wait(3), "the writer never picked the slow job up"
    writer = threading.Thread(target=lambda: httpx.post(
        f"{live}/companies/{hosted.company_id}/commands/company.update",
        json={"fax": "555-3000"}, headers=hosted.bearer, timeout=20.0), daemon=True)
    writer.start()
    time.sleep(0.15)  # the write is now queued behind the slow job
    reads_ready = threading.Barrier(3)

    def read():
        reads_ready.wait(timeout=3)
        return httpx.post(f"{live}/commands/company.list", json={}, headers=hosted.bearer, timeout=10.0)

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        reads = [pool.submit(read) for _ in range(2)]
        reads_ready.wait(timeout=3)
        began = time.monotonic()
        results = [f.result() for f in reads]
    elapsed = time.monotonic() - began
    assert all(r.status_code == 200 for r in results), [r.status_code for r in results]
    assert elapsed < 1.0, f"the reads waited {elapsed:.2f}s behind the write"
    assert len(write_calls) == 1, "a routed read went through the single writer"
    blocker.join(timeout=5)
    writer.join(timeout=15)


def test_two_writes_serialize_into_consecutive_versions(hosted, live):
    import concurrent.futures

    import httpx
    cid = hosted.company_id
    start = hosted.info()["info_version"]

    def write(field, value):
        return httpx.post(f"{live}/companies/{cid}/commands/company.update", json={field: value},
                          headers=hosted.bearer, timeout=20.0)

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first, second = pool.submit(write, "phone", "555-1111"), pool.submit(write, "fax", "555-2222")
        a, b = first.result(), second.result()
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert sorted([a.json()["version"], b.json()["version"]]) == [start + 1, start + 2]
    assert hosted.info()["info_version"] == start + 2


def test_filesystem_operations_release_every_affected_pooled_company(hosted):
    from pathlib import Path

    from bookflow.storage.engine import Database

    host, cid = hosted.handle.host, hosted.company_id

    def is_pooled(company_id):
        return host.submit(lambda: company_id in host._companies)

    def pool_company(marker):
        hosted.ok("company.update", {"fax": marker}, company=cid)
        assert is_pooled(cid)

    pool_company("555-6101")
    renamed = hosted.ok("company.rename", {"name": "Release Witness", "move": True}, company=cid)
    assert renamed["moved"] is True and not is_pooled(cid)

    pool_company("555-6102")
    organization = hosted.ok("organization.list")["items"][0]["organization_id"]
    moved = hosted.ok("organization.rename", {
        "organization": organization, "name": "Release Witness Org", "move": True,
    })
    assert moved["moved"] is True and not is_pooled(cid)

    pool_company("555-6103")
    hosted.ok("upgrade")
    assert not is_pooled(cid)

    pool_company("555-6104")
    folder = Path(hosted.info()["path"])
    hosted.ok("company.detach", {"company": cid})
    assert not is_pooled(cid)

    # Recreate the stale-cache condition attach must defend against. The
    # registry row is gone, but a long-lived host could still have the old DB
    # object if detach came from an older process or interrupted release path.
    host.submit(lambda: host._companies.__setitem__(cid, Database(folder / "company.db", True)))
    assert is_pooled(cid)
    hosted.ok("company.attach", {"path": str(folder)})
    assert not is_pooled(cid)

    pool_company("555-6105")
    reset = hosted.ok("demo.reset")
    assert reset["company_id"] != cid and not is_pooled(cid)


def test_a_durable_write_error_still_checkpoints_and_wakes_subscribers(hosted, root):
    from bookflow.core import registry
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import execute

    class ImmediateLoop:
        @staticmethod
        def call_soon_threadsafe(fn):
            fn()

    host, cid = hosted.handle.host, hosted.company_id
    event = threading.Event()
    subscription, _ = host.subscribe(cid, ImmediateLoop(), event)
    ctx = Context.new(Interface.http, "partial-write-witness")

    def committed_then_failed(session):
        execute(
            registry.get("company update"), {"fax": "555-6199"}, ctx, session,
            company_selector=cid, company_source="option",
        )
        raise BookflowError("E_PARTIAL_WRITE")

    try:
        with pytest.raises(BookflowError) as caught:
            host.run_write(_admin_id(root), "", committed_then_failed)
        assert caught.value.code == "E_PARTIAL_WRITE"
        assert event.wait(0.5), "the durable audit event did not wake its subscriber"
        assert hosted.info()["info"]["fax"] == "555-6199"
    finally:
        host.unsubscribe(subscription)


def test_a_read_enqueues_at_most_one_throttled_refresh(hosted, root):
    stale = "2001-01-01T00:00:00.000Z"
    _hub_sql(root, "UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (stale, hosted.token))
    seen = []
    for _ in range(5):
        assert hosted.call("company.list").status_code == 200
        seen.append(_hub_read(root, "SELECT last_used_at FROM api_tokens WHERE id = ?", (hosted.token,))[0][0])
    assert seen[0] != stale, "the first read refreshed the token"
    assert len(set(seen)) == 1, f"five quick reads refreshed more than once: {seen}"


def test_a_stale_credential_does_not_wait_for_the_writer(hosted, monkeypatch):
    from bookflow.adapters.http import auth
    host = hosted.handle.host
    host.submit(lambda: host._hub.raw.execute(
        "UPDATE api_tokens SET last_used_at = '2001-01-01T00:00:00.000Z' WHERE id = ?", (hosted.token,)))
    original = auth.refresh_token
    refreshed = []

    def counted(*args, **kwargs):
        refreshed.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(auth, "refresh_token", counted)
    entered, release = threading.Event(), threading.Event()

    def occupy_writer():
        entered.set()
        assert release.wait(5)

    blocker = threading.Thread(target=lambda: host.submit(occupy_writer), daemon=True)
    blocker.start()
    assert entered.wait(3)
    began = time.monotonic()
    for _ in range(5):
        assert hosted.call("company.list").status_code == 200
    assert time.monotonic() - began < 1.0
    assert host._refresh_pending == {hosted.token}
    release.set()
    blocker.join(timeout=5)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and host._refresh_pending:
        time.sleep(0.01)
    assert refreshed == [hosted.token]


# ---------------------------------------------------------------- login, logout, credentials

def test_login_and_logout_are_both_audited(hosted):
    api = TestClient(hosted.handle.app)
    logged_in = api.post("/login", json={"username": hosted.login, "password": PASSWORD})
    assert logged_in.status_code == 200
    cookie = logged_in.cookies["bookflow_session"]
    assert api.post("/logout", headers=WB).status_code == 200
    for command in ("login", "logout"):
        events = hosted.ok("hub.audit.list", {"command": command, "limit": 5})["items"]
        assert events, command
        assert events[0]["interface"] == "http" and events[0]["actor_id"], command
    # the browser's cookie is cleared, and the session behind it is revoked
    assert api.post("/commands/company.list", json={}, headers=WB).json()["details"]["reason"] == "no credential"
    replayed = api.post("/commands/company.list", json={}, headers={**WB, "Cookie": f"bookflow_session={cookie}"})
    assert replayed.status_code == 401 and replayed.json()["details"]["reason"] == "revoked"


def test_cookie_renews_only_with_the_throttled_refresh_and_dead_logout_clears(hosted):
    from bookflow.adapters.http import auth
    api = TestClient(hosted.handle.app)
    login = api.post("/login", json={"username": hosted.login, "password": PASSWORD})
    secret = login.cookies["bookflow_session"]
    token_id = hosted.handle.host.submit(lambda: hosted.handle.host._hub.raw.execute(
        "SELECT id FROM api_tokens WHERE token_hash = ?", (auth.token_hash(secret),)).fetchone()[0])
    hosted.handle.host.submit(lambda: hosted.handle.host._hub.raw.execute(
        "UPDATE api_tokens SET last_used_at = '2001-01-01T00:00:00.000Z' WHERE id = ?", (token_id,)))
    renewed = api.post("/commands/company.list", json={}, headers=WB)
    assert renewed.status_code == 200 and "Max-Age=43200" in renewed.headers.get("set-cookie", "")
    assert len(renewed.headers.get_list("content-length")) == 1
    assert int(renewed.headers["content-length"]) == len(renewed.content)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and token_id in hosted.handle.host._refresh_pending:
        time.sleep(0.01)
    fresh = api.post("/commands/company.list", json={}, headers=WB)
    assert "bookflow_session" not in fresh.headers.get("set-cookie", "")

    hosted.handle.host.submit(lambda: hosted.handle.host._hub.raw.execute(
        "UPDATE api_tokens SET expires_at = '2001-01-01T00:00:00.000Z' WHERE id = ?", (token_id,)))
    no_csrf = api.post("/logout")
    assert no_csrf.status_code == 403 and no_csrf.json()["code"] == "E_WORKBENCH_HEADER"
    dead = api.post("/logout", headers=WB)
    assert dead.status_code == 200 and "bookflow_session" in dead.headers.get("set-cookie", "")
    assert "Max-Age=0" in dead.headers["set-cookie"]


def test_an_sse_response_never_renews_the_browser_cookie(hosted, live):
    from bookflow.adapters.http import auth
    import httpx

    with httpx.Client(base_url=live, timeout=10.0) as browser:
        login = browser.post("/login", json={"username": hosted.login, "password": PASSWORD})
        assert login.status_code == 200
        secret = browser.cookies["bookflow_session"]
        token_id = hosted.handle.host.submit(lambda: hosted.handle.host._hub.raw.execute(
            "SELECT id FROM api_tokens WHERE token_hash = ?", (auth.token_hash(secret),)).fetchone()[0])
        hosted.handle.host.submit(lambda: hosted.handle.host._hub.raw.execute(
            "UPDATE api_tokens SET last_used_at = '2001-01-01T00:00:00.000Z' WHERE id = ?", (token_id,)))
        with browser.stream("GET", "/hub-events") as response:
            assert response.status_code == 200
            assert "bookflow_session" not in response.headers.get("set-cookie", "")

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and hosted.handle.host._subscriptions:
        time.sleep(0.02)
    assert hosted.handle.host._subscriptions == {}


def test_every_unauthenticated_shape_names_its_reason(hosted, root):
    naked = hosted.api.post("/commands/company.list", json={})
    unknown = hosted.api.post("/commands/company.list", json={}, headers={"Authorization": "Bearer not-a-real-secret"})
    expiring = hosted.ok("token.issue", {"label": "expiring", "days": 1})
    _hub_sql(root, "UPDATE api_tokens SET expires_at = ? WHERE id = ?", ("2001-01-01T00:00:00.000Z", expiring["token_id"]))
    expired = hosted.api.post("/commands/company.list", json={}, headers={"Authorization": f"Bearer {expiring['secret']}"})
    doomed = hosted.ok("token.issue", {"label": "doomed"})
    hosted.ok("token.revoke", {"token": doomed["token_id"]})
    revoked = hosted.api.post("/commands/company.list", json={}, headers={"Authorization": f"Bearer {doomed['secret']}"})
    reasons = {"no credential": naked, "unknown token": unknown, "expired": expired, "revoked": revoked}
    for reason, r in reasons.items():
        assert r.status_code == 401, reason
        assert set(r.json()) == {"code", "message", "details"} and r.json()["code"] == "E_UNAUTHENTICATED", reason
        assert r.json()["details"]["reason"] == reason


def test_an_envelope_from_another_version_is_a_version_mismatch(hosted):
    from bookflow.core import forward
    from bookflow.core.context import Context, Interface
    ctx = Context.new(Interface.cli, "old-client").model_dump(mode="json") | {"client_version": "9.9.9-future"}
    reply = forward.call_host(str(hosted.handle.socket), {"command": "company list", "input": {}, "context": ctx})
    assert reply["error"]["code"] == "E_VERSION_MISMATCH"
    assert "stop the host" in reply["error"]["message"]


# ---------------------------------------------------------------- the event stream

def _collect(base, path, headers, want, timeout=15.0):
    """Read a server-sent event stream until ``want`` data frames have arrived."""
    import httpx
    out, kind = [], None
    with httpx.stream("GET", base + path, headers=headers, timeout=timeout) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                out.append((kind, json.loads(line[6:])))
                if len(out) >= want:
                    break
    return out


def test_the_stream_drains_a_burst_and_resumes_from_last_event_id(hosted, live):
    cid = hosted.company_id
    start = hosted.ok("audit.tail", {"limit": 1}, company=cid)["high_water"] or 0
    for i in range(10):
        assert hosted.call("company.update", {"phone": f"555-01{i:02d}"}, company=cid).status_code == 200
    burst = _collect(live, f"/companies/{cid}/events?after={start}", hosted.bearer, 10)
    assert len(burst) == 10 and all(kind == "audit" for kind, _ in burst)
    seqs = [item["seq"] for _, item in burst]
    assert seqs == sorted(seqs)
    resumed = _collect(live, f"/companies/{cid}/events?after={start}",
                       {**hosted.bearer, "Last-Event-ID": str(seqs[4])}, 5)
    assert [item["seq"] for _, item in resumed] == seqs[5:], "Last-Event-ID wins over after"
    # one more commit wakes the generators the closed connections left waiting, so the server can shut down
    hosted.ok("company.update", {"fax": "555-0999"}, company=cid)


def test_the_stream_ends_with_an_error_when_its_credential_is_revoked(hosted, live):
    cid = hosted.company_id
    issued = hosted.ok("token.issue", {"label": "streamer"})
    ended = []

    def read():
        try:
            ended.extend(_collect(live, f"/companies/{cid}/events",
                                  {"Authorization": f"Bearer {issued['secret']}"}, 1, timeout=12.0))
        except Exception as e:  # noqa: BLE001 - reported through the assertion below
            ended.append(("raised", repr(e)))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    time.sleep(0.7)  # the stream is drained and waiting for a commit
    hosted.ok("token.revoke", {"token": issued["token_id"]})
    hosted.ok("company.update", {"fax": "555-7777"}, company=cid)  # wakes it up
    reader.join(timeout=10)
    assert ended, "the stream never ended"
    kind, payload = ended[0]
    assert kind == "error" and payload["code"] == "E_UNAUTHENTICATED", ended


def test_invalid_stream_cursors_are_ordinary_validation_documents(hosted):
    for headers, query in ((hosted.bearer, "after=not-an-int"),
                           ({**hosted.bearer, "Last-Event-ID": "not-an-int"}, "after=0")):
        r = hosted.api.get(f"/companies/{hosted.company_id}/events?{query}", headers=headers)
        assert r.status_code == 422
        assert r.json()["code"] == "E_VALIDATION" and r.json()["details"]["fields"][0]["field"] == "after"


def test_lowercase_company_streams_use_the_canonical_commit_key(hosted, live):
    import httpx
    cid = hosted.company_id
    start = hosted.ok("audit.tail", {"limit": 1}, company=cid)["high_water"] or 0

    def write_later():
        time.sleep(0.3)
        httpx.post(f"{live}/companies/{cid}/commands/company.update", json={"fax": "555-6060"},
                   headers=hosted.bearer, timeout=10.0)

    threading.Thread(target=write_later, daemon=True).start()
    began = time.monotonic()
    seen = _collect(live, f"/companies/{cid.lower()}/events?after={start}", hosted.bearer, 1, timeout=10)
    assert seen[0][0] == "audit" and time.monotonic() - began < 5


def test_stream_disconnect_closes_its_buffered_reader_and_subscription(hosted, live):
    cid = hosted.company_id
    start = hosted.ok("audit.tail", {"limit": 1}, company=cid)["high_water"] or 0
    for i in range(8):
        hosted.ok("company.update", {"phone": f"555-88{i:02d}"}, company=cid)
    assert _collect(live, f"/companies/{cid}/events?after={start}", hosted.bearer, 1)
    deadline = time.monotonic() + 3
    host = hosted.handle.host
    while time.monotonic() < deadline and (host._readers_attached or host._subscriptions):
        time.sleep(0.02)
    assert host._readers_attached == 0 and host._subscriptions == {}


def test_more_than_forty_idle_streams_do_not_exhaust_read_workers(hosted, live):
    import httpx
    count = 44
    ready = 0
    lock = threading.Lock()
    release = threading.Event()

    def idle():
        nonlocal ready
        try:
            with httpx.stream("GET", f"{live}/hub-events", headers=hosted.bearer, timeout=30.0) as response:
                assert response.status_code == 200
                with lock:
                    ready += 1
                release.wait(15)
        except Exception:  # noqa: BLE001 - assertions below report readiness and cleanup
            return

    threads = [threading.Thread(target=idle, daemon=True) for _ in range(count)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        with lock:
            if ready == count:
                break
        time.sleep(0.02)
    try:
        assert ready == count
        began = time.monotonic()
        assert httpx.post(f"{live}/commands/company.list", json={}, headers=hosted.bearer, timeout=5).status_code == 200
        assert time.monotonic() - began < 2
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=3)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and hosted.handle.host._subscriptions:
        time.sleep(0.02)
    assert hosted.handle.host._subscriptions == {}


def test_commit_between_first_drain_and_subscription_is_not_missed(hosted, live, root, monkeypatch):
    from bookflow.core import registry
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import execute
    host, cid = hosted.handle.host, hosted.company_id
    start = hosted.ok("audit.tail", {"limit": 1}, company=cid)["high_water"] or 0
    original = host.subscribe
    raced = False

    def subscribe_after_commit(key, loop, event):
        nonlocal raced
        if not raced:
            raced = True
            ctx = Context.new(Interface.http, "race-witness")
            host.run_write(_admin_id(root), "", lambda s: execute(
                registry.get("company update"), {"fax": "555-5151"}, ctx, s,
                company_selector=cid, company_source="option"))
        return original(key, loop, event)

    monkeypatch.setattr(host, "subscribe", subscribe_after_commit)
    began = time.monotonic()
    seen = _collect(live, f"/companies/{cid}/events?after={start}", hosted.bearer, 1, timeout=10)
    assert raced and seen[0][0] == "audit"
    assert time.monotonic() - began < 5, "the stream waited for its 15-second keepalive"


# ---------------------------------------------------------------- the workbench

def _page_url(cmd, company_id):
    noun = cmd.noun if cmd.scope == "company" else cmd.noun.replace(" ", "-")
    base = f"/c/{company_id}/{noun}" if cmd.scope == "company" else f"/hub/{noun}"
    if not cmd.verb:
        return base
    return f"{base}/self/{cmd.verb}" if cmd.version_source else f"{base}/{cmd.verb}"


def test_every_routed_command_has_a_form_with_one_control_per_input_leaf(hosted):
    from bookflow.adapters.workbench import forms as F
    from bookflow.core import registry
    registry.load_all()
    api = TestClient(hosted.handle.app)
    assert api.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    for cmd in registry.routed_commands():
        url = _page_url(cmd, hosted.company_id)
        page = api.get(url)
        assert page.status_code == 200, (cmd.name, url, page.status_code, page.text[:300])
        definition = registry.noun_meta(cmd.noun).get("definition")
        for leaf in F.leaves(cmd.input_model):
            if leaf["path"] == "custom_fields" and getattr(
                definition, "runtime_field_provider", None
            ) == "custom-fields":
                assert 'name="f:custom_fields"' not in page.text, cmd.name
                assert 'name="cf:' in page.text, cmd.name
            elif leaf["kind"] == "collection":
                assert page.text.count(
                    f'name="collection:{leaf["path"]}"'
                ) == 1, (cmd.name, leaf["path"])
                assert f'name="f:{leaf["path"]}"' not in page.text, cmd.name
            else:
                assert page.text.count(f'name="f:{leaf["path"]}"') == 1, (
                    cmd.name,
                    leaf["path"],
                )
        assert 'name="originals"' in page.text, cmd.name


def test_an_update_form_carries_expected_version_and_the_originals(hosted):
    api = TestClient(hosted.handle.app)
    assert api.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    show = hosted.info()
    form = api.get(f"/c/{hosted.company_id}/company/self/update")
    assert form.status_code == 200
    assert f'name="f:expected_version" value="{show["info_version"]}"' in form.text
    assert 'name="originals"' in form.text and show["info"]["legal_name"] in form.text


def test_preview_writes_nothing_and_a_submit_renders_a_result_that_links_back(hosted):
    cid = hosted.company_id
    api = TestClient(hosted.handle.app)
    assert api.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    show = hosted.info()
    originals = {**show["info"], "expected_version": show["info_version"]}
    before = hosted.ok("audit.list", {"command": "company update"}, company=cid)["count"]

    preview = api.post(f"/c/{cid}/company/self/update", headers=WB,
                       data={"originals": json.dumps(originals, default=str), "f:website": "https://preview.example",
                             "action": "preview"})
    assert preview.status_code == 200 and "Preview (nothing written)" in preview.text
    assert hosted.ok("audit.list", {"command": "company update"}, company=cid)["count"] == before
    assert hosted.info()["info"]["website"] != "https://preview.example"

    posted = api.post(f"/c/{cid}/company/self/update", headers=WB,
                      data={"originals": json.dumps(originals, default=str), "f:website": "https://done.example",
                            "action": "submit"})
    assert posted.status_code == 200 and "done" in posted.text
    assert f'href="/c/{cid}/company"' in posted.text or f'href="/c/{cid}/' in posted.text
    assert hosted.info()["info"]["website"] == "https://done.example"
    assert hosted.ok("audit.list", {"command": "company update"}, company=cid)["count"] == before + 1

    # and a form resubmitted exactly as it was rendered sends nothing at all
    untouched = {**originals, "website": "https://done.example"}
    same = api.post(f"/c/{cid}/company/self/update", headers=WB,
                    data={"originals": json.dumps(untouched, default=str), "f:website": "https://done.example",
                          "action": "submit"})
    assert same.status_code == 200
    assert hosted.ok("audit.list", {"command": "company update"}, company=cid)["count"] == before + 1


# ---------------------------------------------------------------- serve itself

def test_cookie_security_default_and_explicit_override():
    from bookflow.commands.host_cmds import cookie_security
    assert cookie_security("127.0.0.1", None) is False
    assert cookie_security("0.0.0.0", None) is True
    assert cookie_security("0.0.0.0", False) is False
    assert cookie_security("127.0.0.1", True) is True


def test_busy_serve_port_has_a_deliberate_redacted_error(root):
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    bind = f"127.0.0.1:{sock.getsockname()[1]}"
    try:
        with pytest.raises(BookflowError) as caught:
            bookflow.connect(data_root=str(root)).run("serve", {"bind": bind})
    finally:
        sock.close()
    assert caught.value.code == "E_IO"
    assert caught.value.details["operation"] == "bind" and caught.value.details["address"] == bind
    assert caught.value.details["errno"] == "EADDRINUSE"
    assert "path" not in caught.value.details


def test_sigint_wakes_a_live_stream_and_cleans_the_host(root):
    import os
    import signal
    import socket
    import subprocess
    from pathlib import Path

    import httpx
    from tests.conftest import BIN

    client = bookflow.connect(data_root=str(root))
    issued = client.token.issue(label="shutdown-witness")
    start = client.run("hub audit tail", {"limit": 1})["high_water"] or 0
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    env = {**os.environ, "BOOKFLOW_DATA_ROOT": str(root)}
    proc = subprocess.Popen([str(BIN), "serve", "--bind", f"127.0.0.1:{port}"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    descriptor = root / "host.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not descriptor.exists() and proc.poll() is None:
        time.sleep(0.03)
    assert descriptor.exists(), proc.communicate(timeout=2)
    socket_file = json.loads(descriptor.read_text())["socket"]
    idle_ready = threading.Event()
    ended = []

    def follow():
        try:
            with httpx.stream("GET", f"http://127.0.0.1:{port}/hub-events?after={start}", headers={
                "Authorization": f"Bearer {issued['secret']}",
            }, timeout=15) as response:
                for line in response.iter_lines():
                    if line == ": ready":
                        idle_ready.set()
                    if line.startswith("event: error"):
                        ended.append(line)
                        break
        except Exception as e:  # noqa: BLE001 - surfaced in the assertions and process stderr
            ended.append(repr(e))

    follower = threading.Thread(target=follow, daemon=True)
    follower.start()
    assert idle_ready.wait(5), "the event stream never reached its idle wait"
    time.sleep(0.75)  # let the generator resume from the ready comment into event.wait()
    began = time.monotonic()
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
    follower.join(timeout=3)
    stderr = proc.stderr.read() if proc.stderr else ""
    assert proc.returncode == 0, stderr
    assert time.monotonic() - began < 3
    assert not descriptor.exists() and not Path(socket_file).exists()
    assert ended, "the idle stream was not woken during shutdown"


def test_startup_migration_is_attributed_to_the_serving_admin(root):
    import sqlite3
    from pathlib import Path

    from bookflow.storage.migrate import current_revision_raw
    from tests.test_migration_chain import _downgrade_copy
    c = bookflow.connect(data_root=str(root))
    cid = c.company.list()["items"][0]["company_id"]
    folder = Path(c.company.show(company=cid)["path"])
    issued = c.token.issue(label="after-migration")
    admin = _admin_id(root)
    db = folder / "company.db"
    columns = [r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(company_info)").fetchall()]
    _downgrade_copy(db, "company", {"company_info": columns,
                                    "principals": ["user_id", "username", "display_name", "kind", "first_seen_at", "last_seen_at"]})
    _hub_sql(root, "UPDATE companies SET schema_revision = 'co0001' WHERE id = ?", (cid,))
    common = ["id", "version", "created_at", "created_by", "created_via", "updated_at", "updated_by", "updated_via"]
    _downgrade_copy(root / "hub.db", "hub", {
        "users": common + ["kind", "username", "display_name", "owner_user_id", "password_hash", "hub_admin", "timezone", "active"],
        "api_tokens": common + ["user_id", "on_behalf_of", "kind", "token_hash", "label", "expires_at", "last_used_at", "revoked_at"],
        "organizations": common + ["display_name", "name_key", "path", "pending_path", "is_demo"],
        "companies": common + ["organization_id", "display_name", "name_key", "path", "pending_path", "legal_name", "home_currency", "schema_revision", "is_demo"],
        "memberships": ["id", "user_id", "scope_type", "scope_id", "role", "granted_by", "granted_at", "revoked_at"],
        "audit_events": ["id", "seq", "at", "command", "actor_id", "actor_kind", "on_behalf_of", "interface", "client_name", "client_version", "client_host", "session_id", "request_id", "idempotency_key", "reason", "directive_id", "directive_code", "source_ref", "summary"],
        "audit_entries": ["id", "event_id", "record_type", "record_id", "action", "version_before", "version_after", "after", "before"],
        "idempotency_keys": ["actor_id", "key", "command", "input_hash", "state", "request_id", "output", "created_at"],
    }, revision="hub0002")
    assert current_revision_raw(db) == "co0001" and current_revision_raw(root / "hub.db") == "hub0002"

    handle = start_serving(root, client_version(), bind="127.0.0.1:8771")
    try:
        assert handle.companies_migrated == [cid] and handle.companies_failed == []
        api = TestClient(handle.app)
        hdr = {"Authorization": f"Bearer {issued['secret']}"}
        events = api.post(f"/companies/{cid}/commands/audit.list", json={"command": "upgrade", "limit": 5}, headers=hdr)
        assert events.status_code == 200, events.text
        items = events.json()["items"]
        assert items, "the startup migration is in the company's audit"
        assert items[0]["actor_kind"] == "system" and items[0]["on_behalf_of"] == admin
        assert items[0]["on_behalf_of_name"] and items[0]["interface"] == "system"
        hub_events = api.post("/commands/hub.audit.list", json={"command": "upgrade", "limit": 5}, headers=hdr)
        assert hub_events.status_code == 200, hub_events.text
        hub_items = hub_events.json()["items"]
        assert hub_items and hub_items[0]["actor_kind"] == "system"
        assert hub_items[0]["on_behalf_of"] == admin and hub_items[0]["on_behalf_of_name"]
        assert hub_items[0]["interface"] == "system"
    finally:
        handle.stop()


# ---------------------------------------------------------------- checkpoints and the sweep

def test_the_wal_stays_bounded_with_a_reader_attached(hosted, root):
    import sqlite3
    from pathlib import Path

    from bookflow.core import registry
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import _close, execute
    host = hosted.handle.host
    cid = hosted.company_id
    admin = _admin_id(root)
    db = Path(hosted.info()["path"]) / "company.db"
    cmd = registry.get("company update")

    reader = host.reader_session(admin)  # attached for the whole burst, so the idle timer stays out of it
    pinned = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    pinned.execute("BEGIN")
    pinned.execute("SELECT count(*) FROM company_info").fetchone()  # a snapshot no checkpoint may pass
    try:
        for i in range(300):
            if i == 60:  # the reading snapshot goes; from here the writer's checkpoints can reclaim the WAL
                pinned.rollback()
                pinned.close()
                pinned = None
            ctx = Context.new(Interface.http, "wal-fixture")
            host.run_write(admin, "", lambda s, i=i, ctx=ctx: execute(
                cmd, {"phone": f"555-{i:04d}"}, ctx, s, company_selector=cid, company_source="option"))
        assert (db.parent / "company.db-wal").exists()
    finally:
        if pinned is not None:
            pinned.rollback()
            pinned.close()
        _close(reader)
        host.reader_done()
    host.checkpoint_now()
    size = (db.parent / "company.db-wal").stat().st_size
    assert size < 4 * 1024 * 1024, f"the WAL grew to {size} bytes"


def test_checkpoint_now_logs_every_connection_it_restarts(hosted, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="bookflow.host")
    hosted.ok("company.update", {"fax": "555-4242"}, company=hosted.company_id)  # pools a company connection
    result = hosted.handle.host.checkpoint_now()
    assert set(result) == {"hub", hosted.company_id}
    assert all(row is not None and row[0] == 0 for row in result.values()), result
    logged = [r.getMessage() for r in caplog.records if "idle checkpoint" in r.getMessage()]
    assert any("hub" in m for m in logged) and any(hosted.company_id in m for m in logged), logged


def test_each_write_runs_a_passive_checkpoint_and_a_discarded_hub_reopens(hosted):
    host, cid = hosted.handle.host, hosted.company_id
    hosted.ok("company.update", {"fax": "555-4040"}, company=cid)
    traced = []
    host.submit(lambda: host._companies[cid].raw.set_trace_callback(traced.append))
    try:
        hosted.ok("company.update", {"fax": "555-4041"}, company=cid)
    finally:
        host.submit(lambda: host._companies[cid].raw.set_trace_callback(None))
    assert any("wal_checkpoint(PASSIVE)" in statement for statement in traced)

    old = host._hub
    host.submit(lambda: host._discard(old))
    assert host._hub is None
    assert hosted.ok("token.issue", {"label": "after-reopen"})["secret"]
    assert host._hub is not None and host._hub is not old


def test_nested_path_redaction_is_recursive():
    from bookflow.core.models import redact_paths
    value = {"outer": {"path": "/private/one", "items": [{"backup_path": "/private/two", "safe": "yes"}]}}
    assert redact_paths(value, False) == {"outer": {"path": None, "items": [{"backup_path": None, "safe": "yes"}]}}


def test_the_sweep_deletes_stale_sessions_as_one_system_event(hosted, root):
    admin = _admin_id(root)
    stale = _insert_session(root, user_id=admin, label="two days gone", expires_at=_days_from_now(-2))
    fresh = _insert_session(root, user_id=admin, label="still good", expires_at=_days_from_now(1))
    just_expired = _insert_session(root, user_id=admin, label="an hour gone", expires_at=_days_from_now(-1 / 24))

    assert hosted.handle.host.sweep_now() == 1
    ids = {t["token_id"] for t in hosted.ok("token.list", {"include_revoked": True})["items"]}
    assert stale not in ids
    assert fresh in ids and just_expired in ids, "only sessions expired more than a day are swept"

    events = hosted.ok("hub.audit.list", {"command": "session sweep", "limit": 5})["items"]
    assert len(events) == 1
    assert events[0]["actor_name"] == "System" and events[0]["actor_kind"] == "system"
    assert events[0]["interface"] == "system" and events[0]["client_name"] == "bookflow-host"
    shown = hosted.ok("hub.audit.show", {"event": events[0]["id"]})
    assert len(shown["entries"]) == 1
    entry = shown["entries"][0]
    assert entry["record_type"] == "api_token" and entry["action"] == "delete" and entry["record_id"] == stale
    assert "token_hash" not in json.dumps(entry)

    assert hosted.handle.host.sweep_now() == 0
    assert hosted.ok("hub.audit.list", {"command": "session sweep", "limit": 5})["count"] == 1


def test_the_timers_run_on_their_own(tmp_path, monkeypatch, caplog):
    """A second data root with a host whose timers tick in fractions of a second."""
    import logging

    from bookflow.core.config import Config
    from bookflow.core.host import Host
    caplog.set_level(logging.INFO, logger="bookflow.host")
    r2 = tmp_path / "timers"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(r2))
    bookflow.connect(data_root=str(r2)).init()
    admin = Config.load(r2 / "config.toml").user_table(os_login())["user_id"]
    _insert_session(r2, user_id=admin, label="two days gone", expires_at=_days_from_now(-2))
    assert len(_hub_read(r2, "SELECT id FROM api_tokens WHERE kind = 'session'")) == 1

    host = Host(r2, version=client_version(), idle_checkpoint_seconds=0.2, sweep_seconds=0.3)
    host.start()
    try:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and _hub_read(r2, "SELECT id FROM api_tokens WHERE kind = 'session'"):
            time.sleep(0.05)
        assert not _hub_read(r2, "SELECT id FROM api_tokens WHERE kind = 'session'"), "the sweep timer never ran"
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not any("idle checkpoint of hub" in r.getMessage() for r in caplog.records):
            time.sleep(0.05)
        assert any("idle checkpoint of hub" in r.getMessage() for r in caplog.records), "the checkpoint timer never ran"
    finally:
        host.stop()
    assert not host._timer.is_alive() and not host._writer.is_alive()


def test_an_agent_token_with_a_principal_acts_on_behalf_of_that_person(hosted, root):
    """Blueprint 4.3: an agent's token names the human it acts for; every write it makes records on_behalf_of."""
    import sqlalchemy as sa
    from bookflow.core import clock
    from bookflow.core.ids import new_id
    from bookflow.hub import schema as h
    from bookflow.hub.users import common
    from bookflow.storage.engine import open_database
    admin_id = hosted.ok("company.list", {})  # any call proves the fixture credential works
    with open_database(root / "hub.db", writable=False) as db:
        admin = dict(db.conn.execute(sa.select(h.users).where(h.users.c.username == hosted.login)).mappings().first())
        org_id = db.conn.execute(sa.select(h.companies.c.organization_id).where(h.companies.c.id == hosted.company_id)).scalar_one()
    aid = new_id()
    hosted.handle.host.submit(lambda: None)  # writer idle; insert through the writer's own hub connection to respect the single-writer rule
    def insert():
        hub = hosted.handle.host._hub
        hub.raw.execute("BEGIN IMMEDIATE")
        hub.conn.execute(h.users.insert().values(id=aid, kind="agent", username="ledger-bot", display_name="Ledger Bot", owner_user_id=admin["id"], password_hash=None, hub_admin=True, timezone=None, active=True, **common(admin["id"], "system")))
        hub.conn.execute(h.memberships.insert().values(id=new_id(), user_id=aid, scope_type="organization", scope_id=org_id, role="admin", granted_by=admin["id"], granted_at=clock.now_iso(), revoked_at=None))
        hub.raw.execute("COMMIT")
    hosted.handle.host.submit(insert)
    faceless = hosted.call("token.issue", {"user": "ledger-bot", "label": "faceless"})
    assert faceless.status_code == 422 and faceless.json()["details"]["fields"][0]["field"] == "principal"
    issued = hosted.ok("token.issue", {"user": "ledger-bot", "label": "bot-token", "principal": hosted.login})
    bot = TestClient(hosted.handle.app)
    refused = bot.post("/commands/token.issue", json={"label": "self-issued", "principal": hosted.login}, headers={
        "Authorization": f"Bearer {issued['secret']}", "X-Bookflow-Reason": "trying to mint another credential",
    })
    switched = bot.post("/commands/token.issue", json={"label": "switched", "principal": "outsider"}, headers={
        "Authorization": f"Bearer {issued['secret']}", "X-Bookflow-Reason": "trying another principal",
    })
    assert refused.status_code == switched.status_code == 403
    assert refused.content == switched.content
    assert refused.json()["details"] == {"capability": "token", "required_role": "human"}
    password = bot.post("/commands/user.set-password", json={"username": hosted.login, "password": PASSWORD}, headers={
        "Authorization": f"Bearer {issued['secret']}", "X-Bookflow-Reason": "trying an administrator reset",
    })
    assert password.status_code == 403
    assert password.json()["details"] == {"capability": "user", "required_role": "human"}
    r = bot.post(f"/companies/{hosted.company_id}/commands/company.update", json={"phone": "555-0199"},
                 headers={"Authorization": f"Bearer {issued['secret']}", "X-Bookflow-Reason": "owner asked by text"})
    assert r.status_code == 200, r.text
    ev = hosted.ok("audit.list", {"command": "company update", "limit": 1}, company=hosted.company_id)["items"][0]
    assert ev["actor_name"] == "Ledger Bot" and ev["actor_kind"] == "agent" and ev["on_behalf_of_name"] == admin["display_name"]
    # without a reason or directive the agent's write is refused (the reason gate applies over HTTP too)
    r = bot.post(f"/companies/{hosted.company_id}/commands/company.update", json={"phone": "555-0198"}, headers={"Authorization": f"Bearer {issued['secret']}"})
    assert r.status_code == 400 and r.json()["code"] == "E_REASON_REQUIRED"


def test_login_lands_in_a_company_without_a_click(hosted):
    """No picker step when the company is not in doubt; deep links return through login."""
    c = TestClient(hosted.handle.app, follow_redirects=False)
    # a deep link before login goes to /login?next=<the link>
    r = c.get(f"/c/{hosted.company_id}/directive")
    assert r.status_code == 303 and r.headers["location"] == f"/login?next=%2Fc%2F{hosted.company_id}%2Fdirective"
    page = c.get(f"/login?next=%2Fc%2F{hosted.company_id}%2Fdirective")
    assert f'/c/{hosted.company_id}/directive' in page.text
    assert "https://" not in c.get("/login?next=https://evil.example/").text  # off-site targets are dropped
    # an outsider who can see exactly one company lands there from /
    other = TestClient(hosted.handle.app, follow_redirects=False)
    assert other.post("/login", json={"username": "outsider", "password": OUTSIDER_PASSWORD}).status_code == 200
    visible = other.post("/commands/company.list", json={}, headers=WB).json()["items"]
    r = other.get("/")
    if len(visible) == 1:
        assert r.status_code == 303 and r.headers["location"] == f"/c/{visible[0]['company_id']}/"
    else:
        assert r.status_code == 200
    # the picker is always reachable; visiting a company remembers it for this browser
    assert other.get("/companies").status_code == 200
    assert c.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    r = c.get(f"/c/{hosted.company_id}/")
    assert r.status_code == 200 and "bookflow_company" in r.headers.get("set-cookie", "")
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == f"/c/{hosted.company_id}/", r.headers


def test_every_link_the_workbench_renders_resolves(hosted):
    """Every internal link emitted by the generated workbench resolves."""
    import re
    c = TestClient(hosted.handle.app, follow_redirects=True)
    assert c.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    seen, queue = set(), ["/companies", "/hub/", f"/c/{hosted.company_id}/"]
    while queue:
        url = queue.pop()
        if url in seen or url.startswith("/static/"):
            continue
        seen.add(url)
        r = c.get(url)
        assert "//" not in url.replace("http://", ""), url
        assert r.status_code == 200, (url, r.status_code, r.text[:200])
        assert "Not Found" not in r.text[:300], url
        for href in re.findall(r'href="([^"]+)"', r.text):
            if href.startswith("/") and not href.startswith("/static/") and "?" not in href:
                queue.append(href)
    assert len(seen) > 15, sorted(seen)
    assert f"/c/{hosted.company_id}/directive" in seen and "/hub/hub-audit" in seen and "/hub/organization/new" in seen
