"""R89: agents and people can be retired, and brought back without reviving anything.

Deactivating suspends authority and revokes every token in one audited change, hides the
identity from the default lists and keeps its history. The last active human installation
administrator cannot be deactivated. Reactivation never revives a token: an agent must be
authorized again.
"""
import sqlite3

import pytest

import bookflow
from tests.conftest import hosted_call, make_agent, make_legacy
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401 - fixture

REASON = {"X-Bookflow-Reason": "deactivation witness"}
WB = {"X-Bookflow-Workbench": "1"}


def hub_rows(root, sql, *args):
    with sqlite3.connect((root / "hub.db").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


READS = {"agent show", "agent list", "user list", "membership list"}


def admin(hosted, name, body):
    return hosted.ok(name.replace(" ", "."), body, headers={} if name in READS else REASON)


def refused(hosted, name, body):
    r = hosted.call(name.replace(" ", "."), body, headers={} if name in READS else REASON)
    assert r.status_code >= 400, r.text
    return r.json()


def acts(hosted, secret):
    return hosted.call("company.show", {}, company=hosted.company_id,
                       headers={"Authorization": "Bearer " + secret}).status_code


@pytest.mark.timeout(600)
def test_deactivating_an_agent_suspends_it_revokes_every_token_and_reactivation_revives_none(hosted):
    admin(hosted, "user add", {"username": "principal-p", "company": hosted.company_id, "role": "owner",
                               "password": "pw-principal-p-12345"})
    agent = make_agent(hosted_call(hosted), "retired-agent", principals="principal-p", company=hosted.company_id)
    first = admin(hosted, "token issue", {"user": agent, "principal": "principal-p", "label": "one"})
    second = admin(hosted, "token issue", {"user": agent, "principal": "principal-p", "label": "two"})
    assert acts(hosted, first["secret"]) == 200 and acts(hosted, second["secret"]) == 200
    epoch = admin(hosted, "agent show", {"agent": agent})["authority"]["epoch"]
    events_before = hub_rows(hosted.root, "SELECT count(*) AS n FROM audit_events")[0]["n"]

    stale = refused(hosted, "agent deactivate", {"agent": agent, "expected_version": 99})
    assert stale["code"] == "E_VERSION_CONFLICT"
    retired = admin(hosted, "agent deactivate", {"agent": "retired-agent"})
    assert retired["changed"] and retired["revoked_token_count"] == 2
    assert not retired["agent"]["active"]
    authority = retired["agent"]["authority"]
    assert authority["suspended"] and authority["epoch"] == epoch + 1
    assert authority["suspension_reason"] == "own_authority_loss" and authority["fresh_context_required"]
    assert acts(hosted, first["secret"]) == 401 and acts(hosted, second["secret"]) == 401
    tokens = hub_rows(hosted.root, "SELECT revoked_at FROM api_tokens WHERE user_id=?", agent)
    assert len(tokens) == 2 and len({t["revoked_at"] for t in tokens}) == 1 and tokens[0]["revoked_at"]
    # One audited change, and the history stays.
    assert hub_rows(hosted.root, "SELECT count(*) AS n FROM audit_events")[0]["n"] == events_before + 1
    assert hub_rows(hosted.root, "SELECT active FROM users WHERE id=?", agent) == [{"active": 0}]
    assert [p["username"] for p in admin(hosted, "agent show", {"agent": agent})["principals"]] == ["principal-p"]

    assert "retired-agent" not in [a["username"] for a in admin(hosted, "agent list", {})["items"]]
    assert "retired-agent" in [a["username"] for a in admin(hosted, "agent list", {"include_inactive": True})["items"]]
    assert "retired-agent" not in [u["username"] for u in admin(hosted, "user list", {})["items"]]
    assert "retired-agent" not in [m["username"] for m in admin(hosted, "membership list", {"company": hosted.company_id})["items"]]
    assert refused(hosted, "token issue", {"user": agent, "principal": "principal-p", "label": "late"})["code"] == "E_USER_NOT_FOUND"
    assert not admin(hosted, "agent deactivate", {"agent": agent})["changed"]

    back = admin(hosted, "agent activate", {"agent": agent})
    assert back["changed"] and back["agent"]["active"] and back["agent"]["authority"]["suspended"]
    assert back["revoked_token_count"] == 0 and back["agent"]["authority"]["epoch"] == epoch + 1
    assert acts(hosted, first["secret"]) == 401, "reactivation never revives a token"
    assert refused(hosted, "token issue", {"user": agent, "principal": "principal-p", "label": "early"})["code"] == "E_PERMISSION"
    admin(hosted, "agent authorize", {"agent": agent, "confirm_permitted_use": True, "acknowledge_fresh_context": True})
    fresh = admin(hosted, "token issue", {"user": agent, "principal": "principal-p", "label": "fresh"})
    assert acts(hosted, fresh["secret"]) == 200 and acts(hosted, first["secret"]) == 401


@pytest.mark.timeout(600)
def test_deactivating_a_person_revokes_their_sessions_and_suspends_their_agents(hosted):
    from fastapi.testclient import TestClient
    admin(hosted, "user add", {"username": "leaver", "company": hosted.company_id, "role": "owner",
                               "password": "pw-leaver-12345"})
    agent = make_agent(hosted_call(hosted), "leaver-agent", principals="leaver", company=hosted.company_id)
    token = admin(hosted, "token issue", {"user": agent, "principal": "leaver", "label": "agent"})
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": "leaver", "password": "pw-leaver-12345"}).status_code == 200
    assert browser.get(f"/c/{hosted.company_id}/customer", headers=WB).status_code == 200

    gone = admin(hosted, "user deactivate", {"user": "leaver"})
    assert gone["changed"] and not gone["active"] and gone["revoked_token_count"] == 1
    assert gone["suspended_agents"] == ["leaver-agent"]
    assert admin(hosted, "agent show", {"agent": agent})["authority"]["suspension_reason"] == "binding_loss"
    assert acts(hosted, token["secret"]) == 401
    assert browser.get(f"/c/{hosted.company_id}/customer", headers=WB, follow_redirects=False).status_code in (303, 401)
    assert TestClient(hosted.handle.app).post("/login", json={"username": "leaver", "password": "pw-leaver-12345"}).status_code == 401
    assert "leaver" not in [u["username"] for u in admin(hosted, "user list", {})["items"]]
    assert "leaver" in [u["username"] for u in admin(hosted, "user list", {"include_inactive": True})["items"]]
    rows = admin(hosted, "membership list", {"company": hosted.company_id, "include_inactive": True})["items"]
    assert [r["account_active"] for r in rows if r["username"] == "leaver"] == [False]

    back = admin(hosted, "user activate", {"user": "leaver"})
    assert back["changed"] and back["active"] and back["suspended_agents"] == []
    assert TestClient(hosted.handle.app).post("/login", json={"username": "leaver", "password": "pw-leaver-12345"}).status_code == 200
    assert admin(hosted, "agent show", {"agent": agent})["authority"]["suspended"], "restoring a person revives no agent"
    assert acts(hosted, token["secret"]) == 401


@pytest.mark.timeout(600)
def test_the_last_active_installation_administrator_cannot_be_deactivated(hosted):
    last = refused(hosted, "user deactivate", {"user": hosted.login})
    assert last["code"] == "E_PERMISSION" and last["details"]["reason"] == "protected_identity"
    assert "last active installation administrator" in last["message"]
    assert hub_rows(hosted.root, "SELECT active FROM users WHERE username=?", hosted.login) == [{"active": 1}]
    admin(hosted, "user add", {"username": "second-admin", "hub_admin": True, "password": "pw-second-admin-12345"})
    own = refused(hosted, "user deactivate", {"user": hosted.login})
    assert own["code"] == "E_VALIDATION" and "yourself" in own["details"]["fields"][0]["problem"]
    assert admin(hosted, "user deactivate", {"user": "second-admin"})["changed"]
    again = refused(hosted, "user deactivate", {"user": hosted.login})
    assert again["details"]["reason"] == "protected_identity"


@pytest.mark.timeout(600)
def test_only_an_installation_administrator_deactivates(hosted):
    from fastapi.testclient import TestClient
    admin(hosted, "user add", {"username": "company-owner", "company": hosted.company_id, "role": "owner",
                               "password": "pw-company-owner-12345"})
    owner = TestClient(hosted.handle.app)
    assert owner.post("/login", json={"username": "company-owner", "password": "pw-company-owner-12345"}).status_code == 200
    for name, body in (("agent deactivate", {"agent": "demo-assistant"}), ("agent activate", {"agent": "demo-assistant"}),
                       ("user deactivate", {"user": hosted.login}), ("user activate", {"user": hosted.login})):
        r = owner.post("/commands/" + name.replace(" ", "."), json=body, headers={**WB, **REASON})
        assert r.status_code == 403 and r.json()["code"] == "E_PERMISSION", (name, r.text)


def test_deactivation_refuses_on_a_legacy_root_like_the_other_agent_writes(root):
    make_legacy(root)
    c = bookflow.connect(data_root=str(root))
    c.run("user add", {"username": "legacy-person", "password": "pw-legacy-person-12345"})
    for name, body in (("agent deactivate", {"agent": "demo-assistant"}), ("agent activate", {"agent": "demo-assistant"}),
                       ("user deactivate", {"user": "legacy-person"}), ("user activate", {"user": "legacy-person"})):
        with pytest.raises(bookflow.BookflowError) as caught:
            c.run(name, body)
        assert caught.value.code == "E_PERMISSION" and caught.value.details["reason"] == "activation_required", name
        assert "permission activate" in caught.value.message


@pytest.mark.timeout(600)
def test_the_workbench_offers_deactivation_on_the_agent_page_the_agents_panel_and_the_users_list(hosted):
    from fastapi.testclient import TestClient
    installer = TestClient(hosted.handle.app)
    assert installer.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    demo = admin(hosted, "agent show", {"agent": "demo-assistant"})["agent_id"]
    panel = installer.get(f"/c/{hosted.company_id}/users").text
    assert f"/hub/agent/{demo}/deactivate" in panel
    record = installer.get(f"/hub/agent/{demo}").text
    assert f"/hub/agent/{demo}/deactivate" in record and f"/hub/agent/{demo}/activate" not in record
    users = installer.get("/hub/user").text
    assert "/hub/user/deactivate" in users and "/hub/user/activate" in users

    done = installer.post(f"/hub/agent/{demo}/deactivate", headers=WB, follow_redirects=False,
                          data={"originals": "{}", "f:agent": "", "ctx:reason": "retire it", "action": "submit"})
    assert done.status_code == 303, done.text
    record = installer.get(f"/hub/agent/{demo}").text
    assert f"/hub/agent/{demo}/activate" in record
    assert f"/hub/agent/{demo}/deactivate" not in record and f"/hub/agent/{demo}/authorize" not in record
    panel = installer.get(f"/c/{hosted.company_id}/users").text
    assert f"/hub/agent/{demo}/" not in panel, "a retired agent leaves the Agents panel"
    back = installer.post(f"/hub/agent/{demo}/activate", headers=WB, follow_redirects=False,
                          data={"originals": "{}", "f:agent": "", "ctx:reason": "bring it back", "action": "submit"})
    assert back.status_code == 303, back.text
    assert admin(hosted, "agent show", {"agent": demo})["authority"]["suspended"]
