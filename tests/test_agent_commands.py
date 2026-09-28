"""Row 7: the public agent commands, over the host.

An installation administrator creates an agent, assigns the humans it acts for, grants its
membership, authorizes it and issues it a bound token. Every narrowing suspends it and revokes
its tokens; nothing but an explicit, acknowledged authorization lifts that.
"""
import sqlite3

import pytest

import bookflow
from tests.conftest import make_actor, make_legacy
from tests.test_row3_host import hosted  # noqa: F401 - fixture

REASON = {"X-Bookflow-Reason": "agent witness"}


def hub_rows(root, sql, *args):
    with sqlite3.connect((root / "hub.db").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


READS = {"agent show", "agent list"}


def admin(hosted, name, body):
    return hosted.ok(name.replace(" ", "."), body, headers={} if name in READS else REASON)


def refused(hosted, name, body, headers=None):
    r = hosted.call(name.replace(" ", "."), body, headers={**({} if name in READS else REASON), **(headers or {})})
    assert r.status_code >= 400, r.text
    return r.json()


def people(hosted, *names, role="owner", company=None):
    ids = {}
    for name in names:
        ids[name] = admin(hosted, "user add", {"username": name, "company": company or hosted.company_id, "role": role,
                                               "password": "pw-" + name + "-12345"})["user_id"]
    return ids


def acts(hosted, secret, label):
    bearer = {"Authorization": "Bearer " + secret}
    read = hosted.call("company.show", {}, company=hosted.company_id, headers=bearer)
    write = hosted.call("customer.create", {"name": "Agent " + label}, company=hosted.company_id, headers={**bearer, **REASON})
    return read.status_code, write.status_code


@pytest.mark.timeout(600)
def test_agent_lifecycle_create_assign_authorize_narrow_and_reauthorize(hosted):
    ids = people(hosted, "principal-p", "principal-q")
    created = admin(hosted, "agent create", {"username": "books-agent", "owner": "principal-p"})["agent"]
    agent = created["agent_id"]
    assert created["authority"]["suspended"] and created["authority"]["suspension_reason"] == "not_yet_authorized"
    assert created["owner_username"] == "principal-p" and created["principals"] == []
    # A suspended agent gets no token, and ownership conveys nothing.
    no_token = refused(hosted, "token issue", {"user": agent, "principal": "principal-p", "label": "early"})
    assert no_token["code"] == "E_PERMISSION", no_token

    missing = refused(hosted, "agent assign", {"agent": "books-agent", "principal": "principal-p"})
    assert missing["code"] == "E_VALIDATION" and missing["details"]["fields"][0]["field"] == "confirm_permitted_use"
    before = hub_rows(hosted.root, "SELECT * FROM agent_principals"), hub_rows(hosted.root, "SELECT * FROM agent_authority")
    preview = hosted.api.post("/commands/agent.assign", params={"dry_run": "true"}, headers={**hosted.bearer, **REASON},
                              json={"agent": "books-agent", "principal": "principal-p", "confirm_permitted_use": True})
    assert preview.status_code == 200 and preview.json()["dry_run"] and preview.json()["changed"], preview.text
    assert (hub_rows(hosted.root, "SELECT * FROM agent_principals"), hub_rows(hosted.root, "SELECT * FROM agent_authority")) == before
    assigned = admin(hosted, "agent assign", {"agent": "books-agent", "principal": "principal-p", "confirm_permitted_use": True})
    assert assigned["changed"] and [p["username"] for p in assigned["agent"]["principals"]] == ["principal-p"]
    assert assigned["agent"]["authority"]["suspended"], "assigning never lifts a suspension"
    admin(hosted, "membership grant", {"user": "books-agent", "company": hosted.company_id, "role": "standard"})
    authorized = admin(hosted, "agent authorize", {"agent": "books-agent", "confirm_permitted_use": True,
                                                   "expected_version": assigned["agent"]["authority"]["version"]})
    assert authorized["changed"] and not authorized["agent"]["authority"]["suspended"]
    assert "token issue --user books-agent --principal principal-p" in authorized["message"]
    epoch = authorized["agent"]["authority"]["epoch"]
    token = admin(hosted, "token issue", {"user": "books-agent", "principal": "principal-p", "label": "books"})
    assert token["authority_epoch"] == epoch
    assert acts(hosted, token["secret"], "first") == (200, 200)

    # Q holds the same permissions, so it can be added without suspending anything.
    same = admin(hosted, "agent assign", {"agent": "books-agent", "principal": "principal-q", "confirm_permitted_use": True})
    assert same["changed"] and not same["agent"]["authority"]["suspended"] and same["revoked_token_count"] == 0
    assert acts(hosted, token["secret"], "still") == (200, 200)

    # Removing Q suspends the whole agent, revokes the P-bound token too, and requires a fresh context.
    narrowed = admin(hosted, "agent unassign", {"agent": "books-agent", "principal": "principal-q"})
    authority = narrowed["agent"]["authority"]
    assert authority["suspended"] and authority["suspension_reason"] == "binding_loss"
    assert authority["epoch"] == epoch + 1 and authority["fresh_context_required"] and narrowed["revoked_token_count"] == 1
    assert acts(hosted, token["secret"], "after unassign") == (401, 401)
    need_ack = refused(hosted, "agent authorize", {"agent": "books-agent", "confirm_permitted_use": True})
    assert need_ack["code"] == "E_VALIDATION" and need_ack["details"]["fields"][0]["field"] == "acknowledge_fresh_context"
    again = admin(hosted, "agent authorize", {"agent": "books-agent", "confirm_permitted_use": True,
                                              "acknowledge_fresh_context": True})
    assert not again["agent"]["authority"]["suspended"] and again["agent"]["authority"]["fresh_context_ack_at"]
    assert acts(hosted, token["secret"], "old token") == (401, 401), "reauthorization never revives old tokens"
    fresh = admin(hosted, "token issue", {"user": "books-agent", "principal": "principal-p", "label": "fresh"})
    assert fresh["authority_epoch"] == epoch + 1 and acts(hosted, fresh["secret"], "fresh") == (200, 200)

    # The agent's own membership: revoke suspends, a regrant revives nothing.
    admin(hosted, "membership revoke", {"user": "books-agent", "company": hosted.company_id})
    shown = admin(hosted, "agent show", {"agent": "books-agent"})
    assert shown["authority"]["suspended"] and shown["authority"]["suspension_reason"] == "own_authority_loss"
    admin(hosted, "membership grant", {"user": "books-agent", "company": hosted.company_id, "role": "standard"})
    assert acts(hosted, fresh["secret"], "after regrant") == (401, 401)
    listed = admin(hosted, "agent list", {"principal": "principal-p"})
    assert "books-agent" in [a["username"] for a in listed["items"]]
    assert ids["principal-q"] not in {p["user_id"] for a in listed["items"] for p in a["principals"]}

    events = hub_rows(hosted.root, "SELECT command FROM audit_events WHERE command LIKE 'agent %' OR command LIKE 'permission %'")
    assert {"agent create"} <= {e["command"] for e in events}


@pytest.mark.timeout(600)
def test_unequal_principals_are_refused_without_disclosure_or_write(hosted):
    people(hosted, "principal-p")
    people(hosted, "reader-r", role="readonly")
    admin(hosted, "agent create", {"username": "split-agent"})
    admin(hosted, "agent assign", {"agent": "split-agent", "principal": "principal-p", "confirm_permitted_use": True})
    before = hub_rows(hosted.root, "SELECT * FROM agent_principals"), hub_rows(hosted.root, "SELECT * FROM agent_authority")
    mismatch = refused(hosted, "agent assign", {"agent": "split-agent", "principal": "reader-r", "confirm_permitted_use": True})
    assert mismatch["code"] == "E_AGENT_PRINCIPAL_MISMATCH"
    assert set(mismatch["details"]) == {"suggestion"} and hosted.company_id not in str(mismatch)
    assert (hub_rows(hosted.root, "SELECT * FROM agent_principals"), hub_rows(hosted.root, "SELECT * FROM agent_authority")) == before
    stale = refused(hosted, "agent authorize", {"agent": "split-agent", "confirm_permitted_use": True, "expected_version": 99})
    assert stale["code"] == "E_VERSION_CONFLICT"


@pytest.mark.timeout(600)
def test_only_a_human_installation_administrator_administers_agents(hosted):
    people(hosted, "principal-p")
    admin(hosted, "agent create", {"username": "owned-agent", "owner": "principal-p"})
    owner = hosted.api
    login = owner.post("/login", json={"username": "principal-p", "password": "pw-principal-p-12345"})
    assert login.status_code == 200
    for name, body in (("agent create", {"username": "mine"}),
                       ("agent assign", {"agent": "owned-agent", "principal": "principal-p", "confirm_permitted_use": True}),
                       ("agent authorize", {"agent": "owned-agent", "confirm_permitted_use": True}),
                       ("agent unassign", {"agent": "owned-agent", "principal": "principal-p"}),
                       ("agent show", {"agent": "owned-agent"}), ("agent list", {})):
        r = owner.post("/commands/" + name.replace(" ", "."), json=body,
                       headers={"X-Bookflow-Workbench": "1", **({} if name in READS else REASON)})
        assert r.status_code == 403 and r.json()["code"] == "E_PERMISSION", (name, r.text)


def test_agent_writes_and_agent_tokens_refuse_on_a_legacy_root(root):
    make_legacy(root)
    c = bookflow.connect(data_root=str(root))
    for name, body in (("agent create", {"username": "late-agent"}),):
        with pytest.raises(bookflow.BookflowError) as caught:
            c.run(name, body)
        assert caught.value.code == "E_PERMISSION" and caught.value.details["reason"] == "activation_required"
        assert "permission activate" in caught.value.message
    assert c.run("agent list", {})["count"] >= 0
    human = make_actor(root, "legacy-owner")
    agent = make_actor(root, "legacy-agent", kind="agent", owner_user_id=human)
    with pytest.raises(bookflow.BookflowError) as caught:
        c.token.issue(user=agent, principal="legacy-owner", label="legacy")
    assert caught.value.details["reason"] == "activation_required"


@pytest.mark.timeout(900)
def test_a_root_activated_at_the_previous_catalog_admits_agents_and_upgrades_without_suspension(root, monkeypatch):
    """The deploy path for an installation already activated before agent administration existed.

    The root is activated at `customer-refund-history-v1` (the stored catalog on such an
    installation). The new build admits the agent commands at once, without re-activation; an
    authorized agent's live token keeps working; and replacing the stored catalog with
    `permission activate` suspends no agent and revokes no token."""
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.config import os_login
    from bookflow.core.context import client_version
    from bookflow.hub import permission_catalog as c, permission_runtime as runtime
    from tests.test_row3_host import PASSWORD, Hosted

    make_legacy(root)
    c_lib = bookflow.connect(data_root=str(root))
    with monkeypatch.context() as previous_build:
        previous_build.setattr(runtime, "current_catalog", lambda: runtime.known_catalog(c.REFUND_HISTORY_POLICY_VERSION))
        state = c_lib.permission.show()
        c_lib.permission.activate(expected_generation=state["generation"], expected_catalog_sha256=state["catalog_sha256"])
    assert hub_rows(root, "SELECT mode, catalog_version FROM permission_state") == [
        {"mode": "policy_v1", "catalog_version": "customer-refund-history-v1"}]

    company = c_lib.company.list()["items"][0]["company_id"]
    login = os_login()
    c_lib.run("user set-password", {"username": login, "password": PASSWORD})
    issued = c_lib.token.issue(label="installer")
    handle = start_serving(root, client_version(), bind="127.0.0.1:8765", secure_cookies=False)
    try:
        hosted = Hosted(handle, root, login, company, issued, "", {})
        people_here = hosted.ok("user.list", {"company": company})["items"]
        installer = next(p["user_id"] for p in people_here if p["username"] == login)
        agent = admin(hosted, "agent create", {"username": "upgrade-agent"})["agent"]["agent_id"]
        admin(hosted, "agent assign", {"agent": agent, "principal": installer, "confirm_permitted_use": True})
        admin(hosted, "membership grant", {"user": agent, "company": company, "role": "standard"})
        authorized = admin(hosted, "agent authorize", {"agent": agent, "confirm_permitted_use": True})["agent"]["authority"]
        token = admin(hosted, "token issue", {"user": agent, "principal": installer, "label": "live agent"})
        assert acts(hosted, token["secret"], "before upgrade") == (200, 200)
        assert hub_rows(root, "SELECT catalog_version FROM permission_state") == [{"catalog_version": "customer-refund-history-v1"}]

        state = hosted.ok("permission.show", {})
        upgraded = hosted.ok("permission.activate", {"expected_generation": state["generation"],
                                                     "expected_catalog_sha256": state["catalog_sha256"]})
        assert upgraded["changed"] and upgraded["affected_agents"] == []
        assert hub_rows(root, "SELECT catalog_version FROM permission_state") == [
            {"catalog_version": runtime.current_catalog().CATALOG.version}]
        after = admin(hosted, "agent show", {"agent": agent})["authority"]
        assert not after["suspended"] and after["epoch"] == authorized["epoch"]
        assert hub_rows(root, "SELECT revoked_at FROM api_tokens WHERE id=?", token["token_id"]) == [{"revoked_at": None}]
        assert acts(hosted, token["secret"], "after upgrade") == (200, 200)
    finally:
        handle.stop()


@pytest.mark.timeout(600)
def test_users_and_permissions_shows_agents_and_offers_controls_only_to_agent_administrators(hosted):
    from fastapi.testclient import TestClient
    from tests.test_row3_host import PASSWORD
    WB = {"X-Bookflow-Workbench": "1"}
    installer = TestClient(hosted.handle.app)
    assert installer.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    page = installer.get(f"/c/{hosted.company_id}/users")
    assert page.status_code == 200 and 'id="company-agents"' in page.text and "demo-assistant" in page.text
    demo = admin(hosted, "agent show", {"agent": "demo-assistant"})["agent_id"]
    assert f"/hub/agent/{demo}/authorize" in page.text
    assert installer.get(f"/hub/agent/{demo}/authorize").status_code == 200

    admin(hosted, "user add", {"username": "company-admin", "company": hosted.company_id, "role": "admin",
                               "password": "pw-company-admin-12345"})
    company_admin = TestClient(hosted.handle.app)
    assert company_admin.post("/login", json={"username": "company-admin", "password": "pw-company-admin-12345"}).status_code == 200
    theirs = company_admin.get(f"/c/{hosted.company_id}/users", headers=WB)
    assert theirs.status_code == 200 and 'id="company-agents"' in theirs.text and "demo-assistant" in theirs.text
    assert "/hub/agent/" not in theirs.text, "a company administrator is offered no agent controls"


@pytest.mark.timeout(900)
def test_demo_reset_keeps_its_one_dedicated_agent_authorized_and_touches_nobody_else(root):
    from bookflow.core.config import Config
    c = bookflow.connect(data_root=str(root))
    first = c.agent.show(agent="demo-assistant")
    assert not first["authority"]["suspended"] and [p["username"] for p in first["principals"]] == [first["owner_username"]]
    bystander = make_actor(root, "bystander", company_role=(c.company.list()["items"][0]["company_id"], "standard"))
    before = hub_rows(root, "SELECT * FROM users WHERE id=?", bystander)
    reset = c.demo.reset()
    again = c.agent.show(agent="demo-assistant")
    assert again["agent_id"] == first["agent_id"] and not again["authority"]["suspended"]
    assert again["authority"]["epoch"] == first["authority"]["epoch"] + 1, "the trashed company's access was a reduction"
    assert again["authority"]["fresh_context_ack_at"]
    held = c.membership.list(user="demo-assistant")["items"]
    assert [(m["scope_id"], m["role"]) for m in held] == [(reset["company_id"], "standard")]
    assert hub_rows(root, "SELECT * FROM users WHERE id=?", bystander) == before
    assert Config.load(root / "config.toml").user_table("bystander")["user_id"] == bystander
    assert [a["username"] for a in c.agent.list()["items"]] == ["demo-assistant"]
