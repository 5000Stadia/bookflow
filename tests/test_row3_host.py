"""Row 3 done sequence: the host, its credentials, the local hand-off, the event feed, and the workbench.

The host runs inside the test process, so a library call here is the host's own process and never
forwards (row 3 plan, Local hand-off). Everything a program would do goes over HTTP; the forwarded
hand-off is exercised by the CLI in a subprocess and by one hand-built envelope.
"""

import json
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


def test_set_password_is_local_only_and_validated(root):
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
    for name in ("company.use", "serve", "user.set-password", "company.frobnicate"):
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
    doc = hosted.api.get("/openapi.json").json()
    assert "/commands/company.list" in doc["paths"]
    assert "/companies/{company_id}/commands/company.update" in doc["paths"]
    assert "/commands/token.issue" in doc["paths"]
    assert "/commands/serve" not in doc["paths"] and "/commands/user.set-password" not in doc["paths"]
    assert doc["paths"]["/commands/company.list"]["post"]["summary"].endswith(".")


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
    ctx = Context.new(Interface.cli, "forger").model_dump(mode="json")
    ctx["actor_id"] = hosted.outsider_id
    ctx["actor_kind"] = "agent"
    ctx["on_behalf_of"] = hosted.outsider_id
    envelope = {"command": "company update", "input": {"website": "https://forged.example"},
                "company_selector": hosted.company_id, "company_source": "option", "dry_run": False, "context": ctx}
    reply = forward.call_host(str(hosted.handle.socket), envelope)
    assert reply is not None and "output" in reply, reply
    event = hosted.ok("audit.list", {"command": "company update", "limit": 1}, company=hosted.company_id)["items"][0]
    assert event["actor_id"] != hosted.outsider_id and event["actor_name"] != "Outsider"
    assert event["on_behalf_of"] is None and event["client_name"] == "forger"


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


# ---------------------------------------------------------------- serve itself

def test_serve_dry_run_and_bind_validation(root):
    c = bookflow.connect(data_root=str(root))
    out = c.run("serve", {}, dry_run=True)
    assert out["dry_run"] and out["bind"] == "127.0.0.1:8765" and out["socket"].endswith(".sock")
    for bind, code in (("127.0.0.1", "E_VALIDATION"), ("127.0.0.1:0", "E_VALIDATION"),
                       ("10.1.2.3:9000", "E_NETWORK_NOT_ALLOWED")):
        with pytest.raises(BookflowError) as e:
            c.run("serve", {"bind": bind}, dry_run=True)
        assert e.value.code == code, bind
    assert c.run("serve", {"bind": "10.1.2.3:9000", "allow_network": True}, dry_run=True)["dry_run"]
    assert parse_bind("[::1]:8765") == ("::1", 8765) and parse_bind("localhost:1") == ("localhost", 1)


def test_serve_needs_a_hub_admin_and_an_initialized_root(root, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BookflowError) as e:
        bookflow.connect(data_root=str(empty)).run("serve", {}, dry_run=True)
    assert e.value.code == "E_NOT_INITIALIZED"
    make_actor(root, "plain")
    with pytest.raises(BookflowError) as e:
        as_user(root, "plain").run("serve", {}, dry_run=True)
    assert e.value.code == "E_PERMISSION"


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
    org = hosted.ok("organization.list")["items"][0]
    return {
        "audit list": ({"limit": 5}, cid),
        "audit show": ({"event": event}, cid),
        "audit tail": ({"limit": 5}, cid),
        "company list": ({}, None),
        "company show": ({}, cid),
        "directive list": ({}, cid),
        "directive show": ({"directive": directive["code"]}, cid),
        "hub audit list": ({"limit": 5}, None),
        "hub audit show": ({"event": hub_event}, None),
        "hub audit tail": ({"limit": 5}, None),
        "organization list": ({}, None),
        "organization show": ({"organization": org["organization_id"]}, None),
        "token list": ({}, None),
    }


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
    for r in (by_path, by_ghost, by_header):
        assert r.status_code == 404 and r.json()["code"] == "E_COMPANY_NOT_FOUND"
    assert by_path.json() == by_ghost.json() == by_header.json()

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

def test_two_reads_are_not_held_up_by_a_long_write(hosted, live):
    import concurrent.futures

    import httpx
    host = hosted.handle.host
    gate = threading.Event()

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
    began = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        reads = [pool.submit(httpx.post, f"{live}/commands/company.list", json={}, headers=hosted.bearer, timeout=10.0)
                 for _ in range(2)]
        results = [f.result() for f in reads]
    elapsed = time.monotonic() - began
    assert all(r.status_code == 200 for r in results), [r.status_code for r in results]
    assert elapsed < 1.0, f"the reads waited {elapsed:.2f}s behind the write"
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


def test_a_read_enqueues_at_most_one_throttled_refresh(hosted, root):
    stale = "2001-01-01T00:00:00.000Z"
    _hub_sql(root, "UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (stale, hosted.token))
    seen = []
    for _ in range(5):
        assert hosted.call("company.list").status_code == 200
        seen.append(_hub_read(root, "SELECT last_used_at FROM api_tokens WHERE id = ?", (hosted.token,))[0][0])
    assert seen[0] != stale, "the first read refreshed the token"
    assert len(set(seen)) == 1, f"five quick reads refreshed more than once: {seen}"


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
        for leaf in F.leaves(cmd.input_model):
            assert page.text.count(f'name="f:{leaf["path"]}"') == 1, (cmd.name, leaf["path"])
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

def test_allow_network_gates_the_bind_and_reports_the_cookie_decision(root):
    c = bookflow.connect(data_root=str(root))
    with pytest.raises(BookflowError) as e:
        c.run("serve", {"bind": "0.0.0.0:8765"}, dry_run=True)
    assert e.value.code == "E_NETWORK_NOT_ALLOWED" and e.value.details["bind"] == "0.0.0.0:8765"
    opened = c.run("serve", {"bind": "0.0.0.0:8765", "allow_network": True}, dry_run=True)
    assert opened["dry_run"] and opened["secure_cookies"] is True
    assert c.run("serve", {}, dry_run=True)["secure_cookies"] is False
    assert c.run("serve", {"secure_cookies": True}, dry_run=True)["secure_cookies"] is True


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
    assert current_revision_raw(db) == "co0001"

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
        hub.conn.execute(h.users.insert().values(id=aid, kind="agent", username="ledger-bot", display_name="Ledger Bot", owner_user_id=admin["id"], password_hash=None, hub_admin=False, timezone=None, active=True, **common(admin["id"], "system")))
        hub.conn.execute(h.memberships.insert().values(id=new_id(), user_id=aid, scope_type="organization", scope_id=org_id, role="admin", granted_by=admin["id"], granted_at=clock.now_iso(), revoked_at=None))
        hub.raw.execute("COMMIT")
    hosted.handle.host.submit(insert)
    issued = hosted.ok("token.issue", {"user": "ledger-bot", "label": "bot-token", "principal": hosted.login})
    bot = TestClient(hosted.handle.app)
    r = bot.post(f"/companies/{hosted.company_id}/commands/company.update", json={"phone": "555-0199"},
                 headers={"Authorization": f"Bearer {issued['secret']}", "X-Bookflow-Reason": "owner asked by text"})
    assert r.status_code == 200, r.text
    ev = hosted.ok("audit.list", {"command": "company update", "limit": 1}, company=hosted.company_id)["items"][0]
    assert ev["actor_name"] == "Ledger Bot" and ev["actor_kind"] == "agent" and ev["on_behalf_of_name"] == admin["display_name"]
    # without a reason or directive the agent's write is refused (the reason gate applies over HTTP too)
    r = bot.post(f"/companies/{hosted.company_id}/commands/company.update", json={"phone": "555-0198"}, headers={"Authorization": f"Bearer {issued['secret']}"})
    assert r.status_code == 400 and r.json()["code"] == "E_REASON_REQUIRED"
