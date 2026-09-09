"""Row 7 identity: a second person, at a second workstation, through the host process.

Everything a person does here goes over HTTP against a running host, because the reason
these commands exist is that another machine can use them. `make_actor` writes hub rows
directly and is deliberately not used: the second account is created by `user add` and
given its company by `membership grant`, exactly as an administrator would.
"""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow.commands.host_cmds import start_serving
from bookflow.core.config import os_login
from bookflow.core.context import client_version

INSTALLER_PASSWORD = "correct-horse-battery"
WB = {"X-Bookflow-Workbench": "1"}


class Office:
    """One host, two companies, and the installer's own logged-in browser."""

    def __init__(self, handle, root, login, first, second):
        self.handle, self.root, self.login = handle, root, login
        self.first, self.second = first, second
        self.installer = TestClient(handle.app)
        r = self.installer.post("/login", json={"username": login, "password": INSTALLER_PASSWORD})
        assert r.status_code == 200, r.text

    def browser(self):
        return TestClient(self.handle.app)

    def login_as(self, username: str, password: str, client=None):
        client = client or self.browser()
        r = client.post("/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return client

    def call(self, client, name, body=None, *, company=None, headers=None):
        path = f"/companies/{company}/commands/{name}" if company else f"/commands/{name}"
        return client.post(path, json=body if body is not None else {}, headers={**WB, **(headers or {})})

    def ok(self, client, name, body=None, **kw):
        r = self.call(client, name, body, **kw)
        assert r.status_code == 200, r.text
        return r.json()

    def admin(self, name, body=None, **kw):
        return self.ok(self.installer, name, body, **kw)


@pytest.fixture
def office(root):
    """A demo install with a second company, so 'the other company' is a real place."""
    login = os_login()
    c = bookflow.connect(data_root=str(root))
    first = c.company.list()["items"][0]
    organization = first["organization_id"]
    second = c.run("company new", {"display_name": "Northwind Roofing", "legal_name": "Northwind Roofing LLC",
                                   "home_currency": "USD", "organization": organization})
    c.run("user set-password", {"username": login, "password": INSTALLER_PASSWORD})
    handle = start_serving(root, client_version(), bind="127.0.0.1:8765", secure_cookies=False)
    try:
        yield Office(handle, root, login, first["company_id"], second["company_id"])
    finally:
        handle.stop()


@pytest.fixture
def live(office):
    """The host behind a real uvicorn server, for the witness that needs an open stream."""
    import socket

    import uvicorn
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(office.handle.app, log_level="warning", access_log=False))
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


def add_jordan(office, **extra):
    """The second workstation's person, created the way an administrator creates one."""
    out = office.admin("user.add", {"username": "jordan", "display_name": "Jordan Reyes",
                                    "company": office.first, **extra})
    assert out["password"], "an added user must arrive with a way to log in"
    return out


# ---------------------------------------------------------------- witness 1: two logins

def test_a_second_person_added_over_the_host_can_log_in_and_work(office):
    added = add_jordan(office)
    jordan = office.login_as("jordan", added["password"])

    mine = office.ok(jordan, "company.list")
    assert mine["count"] == 1 and mine["items"][0]["company_id"] == office.first
    theirs = office.admin("company.list")
    assert theirs["count"] == 2

    # Two distinct sessions, held at the same time, by two distinct users.
    who = office.ok(jordan, "company.show", company=office.first)
    assert who["company_id"] == office.first
    assert office.installer.cookies.get("bookflow_session") != jordan.cookies.get("bookflow_session")
    sessions = office.admin("hub.audit.list", {"command": "login", "limit": 10})["items"]
    assert {e["actor_name"] for e in sessions} >= {"Jordan Reyes"}


def test_the_generated_password_is_the_only_one_that_works(office):
    added = add_jordan(office)
    bad = office.browser().post("/login", json={"username": "jordan", "password": "not-the-one"})
    assert bad.status_code == 401 and bad.json()["code"] == "E_LOGIN_FAILED"
    office.login_as("jordan", added["password"])


def test_a_supplied_password_is_not_echoed_back(office):
    out = office.admin("user.add", {"username": "sam", "password": "a-long-enough-password"})
    assert out["password"] is None and out["membership"] is None
    assert "membership grant sam" in out["message"]
    office.login_as("sam", "a-long-enough-password")


# ---------------------------------------------------------------- witness 2: attribution

def test_each_login_s_work_is_attributed_to_that_person(office):
    added = add_jordan(office)
    jordan = office.login_as("jordan", added["password"])

    office.ok(jordan, "customer.create", {"name": "Pinewood Apartments"}, company=office.first)
    office.admin("customer.create", {"name": "Cedar Court"}, company=office.first)

    events = office.admin("audit.list", {"command": "customer create", "limit": 10}, company=office.first)["items"]
    by_summary = {e["summary"]: e for e in events}
    theirs = next(e for name, e in by_summary.items() if "Pinewood" in name)
    mine = next(e for name, e in by_summary.items() if "Cedar" in name)
    assert theirs["actor_id"] == added["user_id"] and theirs["actor_name"] == "Jordan Reyes"
    assert mine["actor_id"] != added["user_id"]
    assert theirs["actor_kind"] == "human" and theirs["interface"] == "http"

    # And the person themselves sees their own name against their own work.
    seen = office.ok(jordan, "audit.list", {"command": "customer create", "limit": 10}, company=office.first)["items"]
    assert any(e["actor_name"] == "Jordan Reyes" for e in seen)


def test_the_grant_and_the_revocation_are_themselves_attributed(office):
    added = add_jordan(office)
    office.admin("membership.grant", {"user": "jordan", "company": office.second, "role": "readonly"})
    office.admin("membership.revoke", {"user": "jordan", "company": office.second})
    events = office.admin("hub.audit.list", {"limit": 20})["items"]
    commands = [e["command"] for e in events]
    assert "membership grant" in commands and "membership revoke" in commands and "user add" in commands
    grant = next(e for e in events if e["command"] == "membership grant")
    assert grant["actor_name"] and "jordan" in grant["summary"]
    assert added["user_id"] not in {e["actor_id"] for e in events if e["command"].startswith("membership")}


# ---------------------------------------------------------------- witness 3: one company only

def test_a_grant_to_one_company_reveals_nothing_about_its_sibling(office):
    added = add_jordan(office)
    jordan = office.login_as("jordan", added["password"])
    other = office.admin("company.show", company=office.second)["display_name"]

    listed = office.ok(jordan, "company.list")
    assert [row["company_id"] for row in listed["items"]] == [office.first]
    assert other not in json.dumps(listed)

    by_id = office.call(jordan, "company.show", company=office.second)
    assert by_id.status_code == 404 and by_id.json()["code"] == "E_COMPANY_NOT_FOUND"

    # By name, through a command that resolves a company selector itself: the miss
    # answers the same way, and its suggestions cannot name what they missed.
    by_name = office.call(jordan, "membership.grant", {"user": "jordan", "company": other})
    assert by_name.status_code == 404 and by_name.json()["code"] == "E_COMPANY_NOT_FOUND"
    assert other not in json.dumps(by_name.json()), "a miss must not suggest what it missed"

    # Nor through the hub trail, which carries the sibling's own registration event.
    trail = office.ok(jordan, "hub.audit.list", {"limit": 50})
    assert office.second not in json.dumps(trail)

    # The organization above their company is theirs to see, and says nothing about
    # what else hangs off it.
    organizations = office.ok(jordan, "organization.list")
    assert organizations["count"] == 1
    assert other not in json.dumps(organizations) and office.second not in json.dumps(organizations)


def test_a_company_administrator_cannot_reach_across_to_the_sibling(office):
    added = add_jordan(office, role="admin")
    jordan = office.login_as("jordan", added["password"])
    office.admin("user.add", {"username": "sam", "password": "a-long-enough-password"})

    # Their own company: yes, they administer it.
    granted = office.ok(jordan, "membership.grant", {"user": "sam", "company": office.first, "role": "readonly"})
    assert granted["changed"] and granted["scope_id"] == office.first

    # The sibling: not found, in the same words as a company that does not exist.
    denied = office.call(jordan, "membership.grant", {"user": "sam", "company": office.second, "role": "readonly"})
    assert denied.status_code == 404 and denied.json()["code"] == "E_COMPANY_NOT_FOUND"

    # And the organization above it, which would hand them every sibling at once.
    organization = office.admin("company.show", company=office.first)["organization_id"]
    escalate = office.call(jordan, "membership.grant", {"user": "jordan", "organization": organization, "role": "owner"})
    assert escalate.status_code == 403 and escalate.json()["code"] == "E_PERMISSION"
    assert office.ok(jordan, "company.list")["count"] == 1


def test_an_ordinary_member_cannot_grant_anything_at_all(office):
    added = add_jordan(office)
    jordan = office.login_as("jordan", added["password"])
    office.admin("user.add", {"username": "sam", "password": "a-long-enough-password"})
    denied = office.call(jordan, "membership.grant", {"user": "sam", "company": office.first, "role": "readonly"})
    assert denied.status_code == 403 and denied.json()["code"] == "E_PERMISSION"
    assert office.call(jordan, "user.add", {"username": "mallory"}).status_code == 403
    # Whether a username exists is answered only to someone who administers the scope,
    # so the refusal is the same for a real name and an invented one.
    invented = office.call(jordan, "membership.grant", {"user": "nobody-at-all", "company": office.first})
    assert invented.status_code == 403 and invented.json()["code"] == "E_PERMISSION"


# ---------------------------------------------------------------- witness 4: revocation

def test_revoking_removes_access_a_live_session_and_an_issued_token_already_hold(office):
    added = add_jordan(office)
    jordan = office.login_as("jordan", added["password"])
    issued = office.admin("token.issue", {"user": "jordan", "label": "jordan's laptop"})
    bearer = TestClient(office.handle.app)
    bearer_headers = {"Authorization": f"Bearer {issued['secret']}"}

    # Both credentials work before the revocation, and neither is touched by it.
    assert office.ok(jordan, "company.show", company=office.first)["company_id"] == office.first
    before = bearer.post(f"/companies/{office.first}/commands/company.show", json={}, headers=bearer_headers)
    assert before.status_code == 200

    office.admin("membership.revoke", {"user": "jordan", "company": office.first})

    after_cookie = office.call(jordan, "company.show", company=office.first)
    assert after_cookie.status_code == 404 and after_cookie.json()["code"] == "E_COMPANY_NOT_FOUND"
    after_bearer = bearer.post(f"/companies/{office.first}/commands/company.show", json={}, headers=bearer_headers)
    assert after_bearer.status_code == 404 and after_bearer.json()["code"] == "E_COMPANY_NOT_FOUND"
    assert office.ok(jordan, "company.list")["count"] == 0

    # The credential still authenticates: nothing was silently logged out, and the
    # access is gone because the membership is, not because the session expired.
    still_valid = office.call(jordan, "company.list")
    assert still_valid.status_code == 200
    assert office.admin("token.list", {"user": "jordan"})["items"][0]["revoked_at"] is None

    # A write is refused too, not only a read.
    write = office.call(jordan, "customer.create", {"name": "After The Fact"}, company=office.first)
    assert write.status_code == 404 and write.json()["code"] == "E_COMPANY_NOT_FOUND"


def test_a_regrant_restores_access_and_revives_nothing_else(office):
    added = add_jordan(office)
    jordan = office.login_as("jordan", added["password"])
    office.admin("membership.revoke", {"user": "jordan", "company": office.first})
    again = office.admin("membership.grant", {"user": "jordan", "company": office.first, "role": "readonly"})
    assert again["changed"] and again["revoked_at"] is None and again["role"] == "readonly"
    assert office.ok(jordan, "company.list")["count"] == 1
    # Restored at readonly, so the writing they could do before is still gone.
    write = office.call(jordan, "customer.create", {"name": "Back Again"}, company=office.first)
    assert write.status_code == 403 and write.json()["code"] == "E_PERMISSION"


def test_revoking_twice_is_honest_about_changing_nothing(office):
    add_jordan(office)
    first = office.admin("membership.revoke", {"user": "jordan", "company": office.first})
    second = office.admin("membership.revoke", {"user": "jordan", "company": office.first})
    assert first["changed"] and not second["changed"]
    assert first["revoked_at"] == second["revoked_at"]
    missing = office.call(office.installer, "membership.revoke", {"user": "jordan", "company": office.second})
    assert missing.status_code == 404 and missing.json()["code"] == "E_RECORD_NOT_FOUND"


def test_an_open_event_stream_ends_when_the_membership_behind_it_is_revoked(office, live):
    import httpx
    added = add_jordan(office)
    issued = office.admin("token.issue", {"user": "jordan", "label": "jordan's feed"})
    assert added["user_id"] == issued["user_id"]
    ended = []

    def read():
        try:
            with httpx.stream("GET", f"{live}/companies/{office.first}/events",
                              headers={"Authorization": f"Bearer {issued['secret']}"}, timeout=12.0) as r:
                assert r.status_code == 200, r.read()
                kind = None
                for line in r.iter_lines():
                    if line.startswith("event: "):
                        kind = line[7:]
                    elif line.startswith("data: "):
                        ended.append((kind, json.loads(line[6:])))
                        if kind == "error":
                            return
        except Exception as e:  # noqa: BLE001 - reported through the assertion below
            ended.append(("raised", repr(e)))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    time.sleep(0.7)  # drained, and waiting on the next commit
    office.admin("membership.revoke", {"user": "jordan", "company": office.first})
    office.admin("company.update", {"fax": "555-7777"}, company=office.first)  # wakes the stream
    reader.join(timeout=10)
    assert not reader.is_alive(), "the stream was still open after the revocation"
    assert ended, "the stream produced nothing at all"
    # It ends one of two honest ways: the drain's own denial as an error frame, or
    # the publication guard cutting the connection before the next frame leaves.
    kind, payload = ended[-1]
    assert kind in ("error", "raised"), ended
    if kind == "error":
        assert payload["code"] == "E_COMPANY_NOT_FOUND", ended
    delivered = [item for k, item in ended if k == "audit" and isinstance(item, dict)]
    assert not [e for e in delivered if e.get("command") == "company update"], \
        "an event written after the revocation reached a revoked reader"


# ---------------------------------------------------------------- the commands themselves

def test_user_add_refuses_a_name_already_in_use_whatever_its_case(office):
    add_jordan(office)
    clash = office.call(office.installer, "user.add", {"username": "JORDAN"})
    assert clash.status_code == 422 and clash.json()["code"] == "E_VALIDATION"
    reserved = office.call(office.installer, "user.add", {"username": "system"})
    assert reserved.status_code == 422 and reserved.json()["code"] == "E_VALIDATION"


def test_a_scope_is_named_exactly_once(office):
    office.admin("user.add", {"username": "sam", "password": "a-long-enough-password"})
    organization = office.admin("company.show", company=office.first)["organization_id"]
    for body in ({"user": "sam"}, {"user": "sam", "company": office.first, "organization": organization}):
        r = office.call(office.installer, "membership.grant", body)
        assert r.status_code == 422 and r.json()["code"] == "E_VALIDATION"


def test_a_dry_run_adds_nobody_and_grants_nothing(office):
    preview = office.installer.post("/commands/user.add?dry_run=true",
                                    json={"username": "ghost", "company": office.first}, headers=WB)
    assert preview.status_code == 200 and preview.json()["dry_run"] is True
    assert preview.json()["password"] is None and preview.json()["membership"]["role"] == "standard"
    assert office.browser().post("/login", json={"username": "ghost", "password": "anything"}).status_code == 401


def test_an_organization_grant_covers_every_company_in_it(office):
    organization = office.admin("company.show", company=office.first)["organization_id"]
    added = office.admin("user.add", {"username": "morgan", "organization": organization, "role": "standard"})
    morgan = office.login_as("morgan", added["password"])
    assert office.ok(morgan, "company.list")["count"] == 2
    office.admin("membership.revoke", {"user": "morgan", "organization": organization})
    assert office.ok(morgan, "company.list")["count"] == 0


def test_the_three_commands_are_on_every_surface(office):
    from bookflow.core import registry
    registry.load_all()
    for name in ("user add", "membership grant", "membership revoke"):
        cmd = registry.get(name)
        assert cmd is not None and not cmd.local_only and not cmd.standalone
        assert cmd in registry.routed_commands()
        assert cmd.scope == "hub" and cmd.is_write


def test_the_workbench_can_actually_reach_them(office):
    """The browser is the primary human surface: a link has to lead to a usable form."""
    hub = office.installer.get("/hub/")
    assert hub.status_code == 200 and "membership" in hub.text

    listing = office.installer.get("/hub/user")
    assert listing.status_code == 200 and 'href="/hub/user/add"' in listing.text
    members = office.installer.get("/hub/membership")
    assert members.status_code == 200
    assert 'href="/hub/membership/grant"' in members.text and 'href="/hub/membership/revoke"' in members.text

    for path, field in (("/hub/user/add", "f:username"), ("/hub/membership/grant", "f:user"),
                        ("/hub/membership/revoke", "f:user")):
        form = office.installer.get(path)
        assert form.status_code == 200, form.text
        assert f'name="{field}"' in form.text and 'name="f:company"' in form.text, path
        assert f'action="{path}"' in form.text, path

    # Every link these pages render has to land somewhere usable, including the ones
    # the same rule newly surfaces for the neighbouring credential nouns.
    import re
    for noun in ("user", "membership", "token"):
        for link in sorted(set(re.findall(r'href="(/hub/[^"]+)"', office.installer.get(f"/hub/{noun}").text))):
            landed = office.installer.get(link)
            assert landed.status_code == 200, (link, landed.text)
            assert "<form" in landed.text, link
            assert "no such" not in landed.text and "has no show command" not in landed.text, link


def test_a_grant_submitted_from_the_browser_form_takes_effect(office):
    added = add_jordan(office)
    office.admin("user.add", {"username": "sam", "password": "a-long-enough-password"})
    posted = office.installer.post(
        "/hub/membership/grant", headers=WB,
        data={"originals": "{}", "action": "submit", "f:user": "sam",
              "f:company": office.second, "f:role": "readonly"})
    assert posted.status_code == 200, posted.text
    assert "readonly access to" in posted.text
    sam = office.login_as("sam", "a-long-enough-password")
    assert [row["company_id"] for row in office.ok(sam, "company.list")["items"]] == [office.second]
    assert added["username"] == "jordan"


def test_only_an_owner_can_move_another_owner(office):
    added = add_jordan(office, role="admin")
    jordan = office.login_as("jordan", added["password"])
    office.admin("user.add", {"username": "casey", "password": "a-long-enough-password",
                              "company": office.first, "role": "owner"})
    for body in ({"user": "casey", "company": office.first, "role": "readonly"},
                 {"user": "casey", "company": office.first, "role": "owner"}):
        denied = office.call(jordan, "membership.grant", body)
        assert denied.status_code == 403 and denied.json()["code"] == "E_PERMISSION", body
    gone = office.call(jordan, "membership.revoke", {"user": "casey", "company": office.first})
    assert gone.status_code == 403 and gone.json()["code"] == "E_PERMISSION"
    # An admin still administers everyone else at that scope.
    office.admin("user.add", {"username": "sam", "password": "a-long-enough-password"})
    assert office.ok(jordan, "membership.grant", {"user": "sam", "company": office.first})["changed"]


def test_a_member_can_hand_back_their_own_access_and_the_answer_still_publishes(office):
    """The actor's own memberships change mid-request; the publication fence has to
    account for that from this request's own audit rather than refusing the answer."""
    added = add_jordan(office, role="admin")
    jordan = office.login_as("jordan", added["password"])
    handed_back = office.ok(jordan, "membership.revoke", {"user": "jordan", "company": office.first})
    assert handed_back["changed"] and handed_back["revoked_at"]
    assert office.ok(jordan, "company.list")["count"] == 0
    assert office.call(jordan, "company.show", company=office.first).status_code == 404


def test_losing_one_company_does_not_lose_the_others(office):
    added = add_jordan(office)
    office.admin("membership.grant", {"user": "jordan", "company": office.second})
    jordan = office.login_as("jordan", added["password"])
    assert office.ok(jordan, "company.list")["count"] == 2

    office.admin("membership.revoke", {"user": "jordan", "company": office.first})
    remaining = office.ok(jordan, "company.list")
    assert [row["company_id"] for row in remaining["items"]] == [office.second]
    assert office.ok(jordan, "customer.create", {"name": "Still Working"}, company=office.second)
    assert office.call(jordan, "customer.create", {"name": "Not Here"}, company=office.first).status_code == 404
