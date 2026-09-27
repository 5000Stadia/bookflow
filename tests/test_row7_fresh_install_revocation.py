"""Row 7: a fresh install starts in the current permission mode, so revocation always suspends agents.

`init` leaves a new data root exactly where `permission activate` would, with the current
catalog, before any company exists. Nothing then needs activating before an access reduction
suspends an agent's whole authority and revokes its tokens, and a restored membership revives
no old token.

Fixture note: until the agent commands exist, the agent, its authority and its principal
assignments are written directly (as tests/test_row7_credentials.py does). Everything the
test asserts about goes through the public commands over HTTP.
"""
import sqlite3

import pytest

import bookflow
from bookflow.core import clock
from bookflow.hub import permission_runtime as runtime, schema as h
from tests.conftest import make_actor
from tests.test_row3_host import hosted  # noqa: F401 - fixture
from tests.test_row7_credentials import writer


def hub_rows(root, sql, *args):
    with sqlite3.connect((root / "hub.db").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


def test_init_leaves_a_new_root_as_permission_activate_would(tmp_path, monkeypatch):
    root = tmp_path / "fresh"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    c = bookflow.connect(data_root=str(root))
    me = c.init()
    state = c.permission.show()
    build = runtime.current_catalog()
    assert state["mode"] == "policy_v1"
    assert state["catalog_sha256"] == build.MANIFEST.descriptor_sha256
    stored = hub_rows(root, "SELECT mode, catalog_version FROM permission_state")
    assert stored == [{"mode": "policy_v1", "catalog_version": build.CATALOG.version}]
    events = hub_rows(root, "SELECT command, actor_id FROM audit_events ORDER BY rowid")
    assert [e["command"] for e in events] == ["init", "permission activate"]
    assert {e["actor_id"] for e in events} == {me["user_id"]}
    # Activating again is the documented no-op, which is what "exactly as activate
    # would leave it" means for the public contract.
    again = c.permission.activate(expected_generation=state["generation"],
                                  expected_catalog_sha256=state["catalog_sha256"])
    assert again["changed"] is False and again["generation"] == state["generation"]
    # Re-running init on the same root does not move it again.
    assert c.init()["created"] is False
    assert c.permission.show()["generation"] == state["generation"]


@pytest.mark.timeout(300)
@pytest.mark.parametrize("who", ["other", "own", "agent"])
def test_on_a_fresh_install_revocation_suspends_the_agent_and_restoration_revives_nothing(hosted, who):
    root, cid = hosted.root, hosted.company_id
    # The seeded root is a plain `init` + `demo reset`; nobody ran `permission activate`.
    assert hub_rows(root, "SELECT mode FROM permission_state") == [{"mode": "policy_v1"}]
    assert [e["command"] for e in hub_rows(root, "SELECT command FROM audit_events")].count("permission activate") == 1
    for name in ("principal-p", "principal-q"):
        hosted.ok("user.add", {"username": name, "company": cid, "role": "owner", "password": "pw-" + name + "-12345"})
    ids = {r["username"]: r["id"] for r in hub_rows(
        root, "SELECT username, id FROM users WHERE username IN ('principal-p', 'principal-q')")}
    P, Q = ids["principal-p"], ids["principal-q"]
    G = make_actor(root, "agent-g", kind="agent", owner_user_id=P, company_role=(cid, "owner"))
    with writer(root) as db:
        db.conn.execute(h.agent_authority.insert().values(agent_user_id=G, epoch=1))
        for principal in (P, Q):
            db.conn.execute(h.agent_principals.insert().values(agent_user_id=G, principal_user_id=principal,
                                                               assigned_by=P, assigned_at=clock.now_iso()))
    issued = hosted.ok("token.issue", {"user": G, "label": "agent witness", "principal": P})
    agent = {"Authorization": "Bearer " + issued["secret"]}

    def acts(label):
        read = hosted.call("company.show", {}, company=cid, headers=agent)
        write = hosted.call("customer.create", {"name": f"Agent write {label}"}, company=cid,
                            headers={**agent, "X-Bookflow-Reason": "witness"})
        return read.status_code, write.status_code

    assert acts("before") == (200, 200)
    target = {"other": "principal-q", "own": "principal-p", "agent": G}[who]
    revoked = hosted.call("membership.revoke", {"user": target, "company": cid}, headers={"X-Bookflow-Reason": "witness"})
    assert revoked.status_code == 200 and revoked.json()["changed"], revoked.text

    authority = hub_rows(root, "SELECT epoch, suspended_at FROM agent_authority WHERE agent_user_id=?", G)
    assert authority[0]["suspended_at"] is not None and authority[0]["epoch"] == 2
    tokens = hub_rows(root, "SELECT revoked_at FROM api_tokens WHERE user_id=?", G)
    assert tokens and all(t["revoked_at"] is not None for t in tokens)
    assert acts("after revoke") == (401, 401)

    regranted = hosted.call("membership.grant", {"user": target, "company": cid, "role": "owner"},
                            headers={"X-Bookflow-Reason": "witness"})
    assert regranted.status_code == 200, regranted.text
    assert acts("after restore") == (401, 401)
    assert hub_rows(root, "SELECT suspended_at FROM agent_authority WHERE agent_user_id=?", G)[0]["suspended_at"]
    # Nor does restoration make a new token issuable without explicit reauthorization.
    fresh = hosted.call("token.issue", {"user": G, "label": "after restore", "principal": P})
    assert fresh.status_code == 403 and fresh.json()["code"] == "E_PERMISSION"
