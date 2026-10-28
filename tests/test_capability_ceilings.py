"""R187 increment 1 security review: every finding's chain, replayed over HTTP, now refused.

The principle (design/principal-directives.md §1): an agent never ends up with, or gives anyone, more than its own
grants and its principal's standing allow, by any chain of allowed acts; a person's effective rights are unchanged.
"""
import sqlite3

from tests.conftest import add_membership, hosted_call, make_agent
from tests.test_row3_host import hosted  # noqa: F401 - fixture

REASON = {"X-Bookflow-Reason": "r187 ceilings"}


def _q(hosted, sql, *args):
    with sqlite3.connect((hosted.root / "hub.db").as_uri() + "?mode=ro", uri=True) as db:
        return db.execute(sql, args).fetchall()


def _uid(hosted, username):
    return _q(hosted, "SELECT id FROM users WHERE username=?", username)[0][0]


def _org(hosted):
    return _q(hosted, "SELECT organization_id FROM companies WHERE id=?", hosted.company_id)[0][0]


def _version(hosted, user, scope_type, scope_id):
    row = _q(hosted, "SELECT version FROM memberships WHERE user_id=? AND scope_type=? AND scope_id=?",
             user, scope_type, scope_id)
    return row[0][0] if row else 0


def _grant(hosted, user, role, *, scope=None, grants=(), denies=(), headers=None):
    scope = scope or ("company", hosted.company_id)
    key = "company" if scope[0] == "company" else "organization"
    return hosted.call("membership.grant", {"user": user, key: scope[1], "role": role, "grants": list(grants),
                                            "denies": list(denies),
                                            "expected_version": _version(hosted, user, *scope)},
                       headers={**REASON, **(headers or {})})


def _revoke(hosted, user, headers, scope=None):
    scope = scope or ("company", hosted.company_id)
    return hosted.call("membership.revoke", {"user": user, scope[0]: scope[1],
                                             "expected_version": _version(hosted, user, *scope)},
                       headers={**REASON, **headers})


def _bearer(hosted, user, principal=None, label="t"):
    body = {"user": user, "label": label, **({"principal": principal} if principal else {})}
    return {"Authorization": "Bearer " + hosted.ok("token.issue", body, headers=REASON)["secret"]}


def _rows(hosted, user):
    return _q(hosted, "SELECT scope_type, role, grants, denies, revoked_at, version FROM memberships WHERE user_id=? "
                      "ORDER BY scope_type", user)


def _refused(r, field=None):
    assert r.status_code == 403, r.text
    if field is not None:
        assert r.json()["details"].get("field") == field, r.text


def _setup(hosted, grants=("membership.grant",)):
    cid = hosted.company_id
    add_membership(hosted.root, _uid(hosted, hosted.login), "organization", _org(hosted), "owner")
    hosted.ok("user.add", {"username": "principal-p", "company": cid, "role": "owner", "hub_admin": True,
                           "password": "pw-principal-p-12345"}, headers=REASON)
    agent = make_agent(hosted_call(hosted), "cl-agent", principals="principal-p", company=cid, role="admin")
    r = _grant(hosted, agent, "admin", grants=grants)
    assert r.status_code == 200, r.text
    return agent, _bearer(hosted, agent, "principal-p")


def _authorize(hosted, agent):
    (suspended, version), = _q(hosted, "SELECT suspended_at, version FROM agent_authority WHERE agent_user_id=?", agent)
    if suspended:
        hosted.ok("agent.authorize", {"agent": agent, "confirm_permitted_use": True, "acknowledge_fresh_context": True,
                                      "expected_version": version}, headers=REASON)


def test_f1_revoking_a_membership_never_lifts_a_deny_the_agent_could_not_lift(hosted):
    cid, oid = hosted.company_id, _org(hosted)
    agent, as_agent = _setup(hosted)  # holds membership.grant only, not party.merge
    sib = make_agent(hosted_call(hosted), "cl-sibling", principals="principal-p", company=cid, role="standard")
    assert _grant(hosted, sib, "standard", scope=("organization", oid), grants=["party.merge"]).status_code == 200
    assert _grant(hosted, sib, "standard", denies=["party.merge"]).status_code == 200
    _authorize(hosted, sib)
    as_sib = _bearer(hosted, sib, "principal-p")
    for name in ("Dup Co A", "Dup Co B"):
        hosted.ok("customer.create", {"name": name}, company=cid, headers={**REASON, **as_sib})
    merge = lambda: hosted.call("customer.merge?dry_run=true", {"merged": "Dup Co A", "into": "Dup Co B"},
                                company=cid, headers={**REASON, **as_sib})
    assert merge().status_code == 403
    _refused(_grant(hosted, sib, "standard", denies=[], headers=as_agent), "grants")
    before = _rows(hosted, sib)
    _refused(_revoke(hosted, sib, as_agent), "grants")
    assert _rows(hosted, sib) == before
    assert merge().status_code == 403
    # A person's deny is just as fixed: revoking the membership that carries it is refused.
    t = hosted.ok("user.add", {"username": "clerk-t", "password": "pw-clerk-t-1234567"}, headers=REASON)["user_id"]
    assert _grant(hosted, t, "standard", scope=("organization", oid)).status_code == 200
    assert _grant(hosted, t, "standard", denies=["customer"]).status_code == 200
    _refused(_revoke(hosted, t, as_agent), "grants")
    as_t = _bearer(hosted, "clerk-t")
    assert hosted.call("customer.create", {"name": "T after"}, company=cid, headers={**REASON, **as_t}).status_code == 403
    # Revoking a membership that carries no deny narrows, and stays the agent's to do.
    u = hosted.ok("user.add", {"username": "clerk-u", "password": "pw-clerk-u-1234567"}, headers=REASON)["user_id"]
    assert _grant(hosted, u, "standard").status_code == 200
    r = _revoke(hosted, u, as_agent)
    assert r.status_code == 200, r.text


def test_f2_demo_reset_refuses_while_the_demo_organization_holds_a_real_company(hosted):
    cid = hosted.company_id
    hosted.ok("user.add", {"username": "principal-p", "company": cid, "role": "owner", "hub_admin": True,
                           "password": "pw-principal-p-12345"}, headers=REASON)
    a = make_agent(hosted_call(hosted), "cl-demo", principals="principal-p", company=cid, role="admin")
    assert _grant(hosted, a, "admin", grants=["demo.reset"]).status_code == 200
    as_a = _bearer(hosted, a, "principal-p")
    assert _q(hosted, "SELECT is_demo FROM organizations WHERE id=?", _org(hosted)) == [(1,)]
    hosted.ok("company.new", {"display_name": "Real Roofing", "legal_name": "Real Roofing LLC",
                              "home_currency": "USD", "organization": _org(hosted)}, headers=REASON)
    before = _q(hosted, "SELECT id, organization_id, pending_path FROM companies ORDER BY id")
    root = str(hosted.root)
    for headers in (as_a, {}):  # the agent, and the installation administrator, force or not
        for body in ({}, {"force": root}):
            r = hosted.call("demo.reset", body, headers={**REASON, **headers})
            assert r.status_code == 403, r.text
            assert r.json()["details"]["reason"] == "real_company_in_demo_organization"
            assert r.json()["details"]["real_companies"] == ["Real Roofing"]
            assert root not in r.text and str(hosted.root.resolve()) not in r.text
    assert _q(hosted, "SELECT id, organization_id, pending_path FROM companies ORDER BY id") == before


def test_f2_demo_reset_refusal_for_real_books_elsewhere_names_no_path(hosted):
    cid = hosted.company_id
    hosted.ok("user.add", {"username": "principal-p", "company": cid, "role": "owner", "hub_admin": True,
                           "password": "pw-principal-p-12345"}, headers=REASON)
    a = make_agent(hosted_call(hosted), "cl-demo2", principals="principal-p", company=cid, role="admin")
    assert _grant(hosted, a, "admin", grants=["demo.reset"]).status_code == 200
    as_a = _bearer(hosted, a, "principal-p")
    org = hosted.ok("organization.new", {"name": "Elsewhere Org"}, headers=REASON)
    hosted.ok("company.new", {"display_name": "Elsewhere Books", "legal_name": "Elsewhere Books LLC",
                              "home_currency": "USD", "organization": org["organization_id"]}, headers=REASON)
    root = str(hosted.root)
    r = hosted.call("demo.reset?dry_run=true", {}, headers={**REASON, **as_a})
    assert r.status_code == 403, r.text
    assert r.json()["details"]["reason"] == "real_company_present"
    assert r.json()["details"]["data_root"] is None  # withheld from a non-administrator
    assert root not in r.text and str(hosted.root.resolve()) not in r.text
    person = hosted.call("demo.reset?dry_run=true", {}, headers=REASON)
    assert person.status_code == 403 and root not in person.json()["message"]
    assert person.json()["details"]["data_root"] is not None  # the administrator still learns what to type


def test_f3_an_agent_gives_no_hub_capability_its_principal_could_not(hosted):
    cid = hosted.company_id
    hosted.ok("user.add", {"username": "principal-q", "company": cid, "role": "admin",
                           "password": "pw-principal-q-12345"}, headers=REASON)
    hosted.ok("user.add", {"username": "principal-h", "company": cid, "role": "owner", "hub_admin": True,
                           "password": "pw-principal-h-12345"}, headers=REASON)
    a = make_agent(hosted_call(hosted), "cl-a-for-q", principals="principal-q", company=cid, role="admin")
    b = make_agent(hosted_call(hosted), "cl-b-for-h", principals="principal-h", company=cid, role="standard")
    assert _grant(hosted, a, "admin", grants=["membership.grant", "user.manage"]).status_code == 200
    as_a = _bearer(hosted, a, "principal-q")
    assert hosted.call("user.add", {"username": "via-a"}, headers={**REASON, **as_a}).status_code == 403
    _refused(_grant(hosted, b, "standard", grants=["user.manage"], headers=_bearer(hosted, "principal-q")))
    before = _rows(hosted, b)
    _refused(_grant(hosted, b, "standard", grants=["user.manage"], headers=as_a), "grants")
    assert _rows(hosted, b) == before
    _authorize(hosted, b)
    assert hosted.call("user.add", {"username": "via-b"},
                       headers={**REASON, **_bearer(hosted, b, "principal-h")}).status_code == 403


def test_f3_an_agent_gives_no_capability_its_principal_is_denied(hosted):
    cid = hosted.company_id
    agent, as_agent = _setup(hosted, grants=("membership.grant", "party.merge"))
    p = _uid(hosted, "principal-p")
    assert _grant(hosted, p, "owner", denies=["party.merge"]).status_code == 200
    _authorize(hosted, agent)
    as_agent = _bearer(hosted, agent, "principal-p", label="t2")
    u = hosted.ok("user.add", {"username": "clerk-v", "password": "pw-clerk-v-1234567"}, headers=REASON)["user_id"]
    _refused(_grant(hosted, u, "standard", grants=["party.merge"], headers=as_agent), "grants")
    ok = _grant(hosted, u, "standard", headers=as_agent)
    assert ok.status_code == 200, ok.text
    assert cid


def test_f4_an_agent_never_edits_a_hub_administrators_membership(hosted):
    cid = hosted.company_id
    agent, as_agent = _setup(hosted)
    h2 = hosted.ok("user.add", {"username": "hub-two", "company": cid, "role": "admin", "hub_admin": True,
                                "password": "pw-hub-two-123456"}, headers=REASON)["user_id"]
    before = _rows(hosted, h2)
    _refused(_grant(hosted, h2, "readonly", headers=as_agent), "target")
    _refused(_grant(hosted, h2, "admin", denies=["customer"], headers=as_agent), "target")
    _refused(_revoke(hosted, h2, as_agent), "target")
    assert _rows(hosted, h2) == before


def test_f5_an_agent_never_edits_any_of_its_principals(hosted):
    cid = hosted.company_id
    for name in ("pp-one", "pp-two"):
        hosted.ok("user.add", {"username": name, "company": cid, "role": "admin", "password": f"pw-{name}-1234567"},
                  headers=REASON)
    m = make_agent(hosted_call(hosted), "cl-multi", principals=["pp-one", "pp-two"], company=cid, role="admin")
    assert _grant(hosted, m, "admin", grants=["membership.grant"]).status_code == 200
    as_m = _bearer(hosted, m, "pp-one")
    for who in ("pp-one", "pp-two"):
        uid = _uid(hosted, who)
        before = _rows(hosted, uid)
        _refused(_grant(hosted, uid, "readonly", headers=as_m), "target")
        _refused(_revoke(hosted, uid, as_m), "target")
        assert _rows(hosted, uid) == before
    assert _q(hosted, "SELECT suspended_at FROM agent_authority WHERE agent_user_id=?", m) == [(None,)]


def test_f6_a_persons_deny_of_a_new_capability_says_it_binds_only_their_agents(hosted):
    cid = hosted.company_id
    t = hosted.ok("user.add", {"username": "clerk-d", "password": "pw-clerk-d-1234567"}, headers=REASON)["user_id"]
    r = _grant(hosted, t, "standard", denies=["party.merge"])
    assert r.status_code == 200, r.text
    assert r.json()["denies"] == ["party.merge"]
    assert "binds only the agents acting for clerk-d" in r.json()["message"]
    plain = _grant(hosted, t, "standard", denies=["customer"])
    assert "binds only" not in plain.json()["message"]
    # Unchanged for the person: still merges at their role.
    _grant(hosted, t, "standard", denies=["party.merge"])
    for name in ("L1 A", "L1 B"):
        hosted.ok("customer.create", {"name": name}, company=cid, headers=REASON)
    m = hosted.call("customer.merge?dry_run=true", {"merged": "L1 A", "into": "L1 B"}, company=cid,
                    headers={**REASON, **_bearer(hosted, "clerk-d")})
    assert m.status_code == 200, m.text


def test_a_principals_deny_refuses_the_agents_next_act_live(hosted):
    cid = hosted.company_id
    hosted.ok("user.add", {"username": "principal-p", "company": cid, "role": "owner", "hub_admin": True,
                           "password": "pw-principal-p-12345"}, headers=REASON)
    a = make_agent(hosted_call(hosted), "cl-mid", principals="principal-p", company=cid, role="admin")
    assert _grant(hosted, a, "admin", grants=["user.manage"]).status_code == 200
    as_a = _bearer(hosted, a, "principal-p")
    assert hosted.call("user.add", {"username": "mid-1"}, headers={**REASON, **as_a}).status_code == 200
    assert _grant(hosted, _uid(hosted, "principal-p"), "owner", denies=["user.manage"]).status_code == 200
    r = hosted.call("user.add", {"username": "mid-2"}, headers={**REASON, **as_a})
    assert r.status_code == 403 and r.json()["details"]["reason"] == "principal_lacks_capability", r.text


def test_co_principals_must_match_on_hub_administration_and_new_capability_denies(hosted):
    cid = hosted.company_id
    for name, hub in (("eq-h", True), ("eq-p", False), ("eq-1", False), ("eq-2", False)):
        hosted.ok("user.add", {"username": name, "company": cid, "role": "owner", "hub_admin": hub,
                               "password": f"pw-{name}-123456789"}, headers=REASON)
    assert _grant(hosted, _uid(hosted, "eq-1"), "owner", denies=["party.merge"]).status_code == 200
    for first, second in (("eq-h", "eq-p"), ("eq-1", "eq-2")):
        agent = hosted.ok("agent.create", {"username": f"cl-eq-{first}"}, headers=REASON)["agent"]["agent_id"]
        ok = hosted.call("agent.assign", {"agent": agent, "principal": first, "confirm_permitted_use": True},
                         headers=REASON)
        assert ok.status_code == 200, ok.text
        r = hosted.call("agent.assign", {"agent": agent, "principal": second, "confirm_permitted_use": True},
                        headers=REASON)
        assert r.status_code != 200 and r.json()["code"] == "E_AGENT_PRINCIPAL_MISMATCH", r.text


def test_an_organization_wide_grant_never_steps_over_a_company_carve_out(hosted):
    """Verification round 2: agent A (granted party.merge at the organization, denied it at the company) gave D
    party.merge organization-wide, and D then lifted A's company deny. Each step is now refused, as are a role change,
    a deny lift and a revoke at the organization that would widen past the carve-out."""
    cid, oid = hosted.company_id, _org(hosted)
    add_membership(hosted.root, _uid(hosted, hosted.login), "organization", oid, "owner")
    hosted.ok("user.add", {"username": "principal-n", "company": cid, "role": "owner", "hub_admin": True,
                           "password": "pw-principal-n-12345"}, headers=REASON)
    add_membership(hosted.root, _uid(hosted, "principal-n"), "organization", oid, "owner")
    a = make_agent(hosted_call(hosted), "cl-carve-a", principals="principal-n", company=cid, role="admin")
    d = make_agent(hosted_call(hosted), "cl-carve-d", principals="principal-n", company=cid, role="standard")
    assert _grant(hosted, a, "admin", scope=("organization", oid),
                  grants=["membership.grant", "party.merge"]).status_code == 200
    assert _grant(hosted, a, "admin", denies=["party.merge"]).status_code == 200
    for agent in (a, d):
        _authorize(hosted, agent)
    as_a, as_d = _bearer(hosted, a, "principal-n"), _bearer(hosted, d, "principal-n")
    _refused(_grant(hosted, d, "standard", grants=["party.merge"], headers=as_a), "grants")  # company: already refused
    before = _rows(hosted, d)
    _refused(_grant(hosted, d, "admin", scope=("organization", oid), grants=["party.merge", "membership.grant"],
                    headers=as_a), "grants")
    assert _rows(hosted, d) == before
    # The chain's second step: D, holding nothing A could not give, cannot lift A's company deny.
    before_a = _rows(hosted, a)
    _refused(_grant(hosted, a, "admin", denies=[], headers=as_d))
    assert _rows(hosted, a) == before_a
    for name in ("Carve A", "Carve B"):
        hosted.ok("customer.create", {"name": name}, company=cid, headers=REASON)
    merge = hosted.call("customer.merge?dry_run=true", {"merged": "Carve A", "into": "Carve B"},
                        company=cid, headers={**REASON, **as_a})
    assert merge.status_code == 403, merge.text
    # A role change, a deny lift and a revoke at the organization run through the same rule.
    u = hosted.ok("user.add", {"username": "clerk-w", "password": "pw-clerk-w-1234567"}, headers=REASON)["user_id"]
    assert _grant(hosted, u, "standard", scope=("organization", oid), denies=["party.merge"]).status_code == 200
    before_u = _rows(hosted, u)
    _refused(_grant(hosted, u, "admin", scope=("organization", oid), grants=["party.merge"],
                    denies=["party.merge"], headers=as_a), None)
    _refused(_grant(hosted, u, "standard", scope=("organization", oid), denies=[], headers=as_a), "grants")
    _refused(_revoke(hosted, u, as_a, scope=("organization", oid)), "grants")
    assert _rows(hosted, u) == before_u
    # The principal's own carve-out binds the same way: with A's lifted by a person, the principal's deny refuses.
    assert _grant(hosted, a, "admin", denies=[]).status_code == 200
    assert _grant(hosted, _uid(hosted, "principal-n"), "owner", denies=["party.merge"]).status_code == 200
    _authorize(hosted, a)
    as_a = _bearer(hosted, a, "principal-n", label="t3")
    _refused(_grant(hosted, d, "standard", scope=("organization", oid), grants=["party.merge"], headers=as_a), "grants")


def test_import_messages_read_as_written_and_a_data_root_is_still_withheld(hosted):
    """Verification round 2: imports name input field paths under `path`; redaction garbled them for everyone but
    administrators. Only a filesystem-shaped value is withheld now."""
    from bookflow.core.dispatch import redact_message
    cid = hosted.company_id
    hosted.ok("user.add", {"username": "principal-p", "company": cid, "role": "owner", "hub_admin": True,
                           "password": "pw-principal-p-12345"}, headers=REASON)
    r = hosted.ok("user.add", {"username": "clerk-r", "password": "pw-clerk-r-1234567"}, headers=REASON)["user_id"]
    assert _grant(hosted, r, "standard").status_code == 200
    agent = make_agent(hosted_call(hosted), "cl-import", principals="principal-p", company=cid, role="standard")
    readers = {"person": _bearer(hosted, "clerk-r"), "agent": _bearer(hosted, agent, "principal-p"), "admin": {}}
    content = "Name,Town\nAcme Supply,Springfield\n"
    for target in ("billing_address.nowhere", "a"):
        messages = {}
        for who, headers in readers.items():
            got = hosted.call("import.check", {"kind": "customer", "content": content, "columns": {"Town": target}},
                              company=cid, headers=headers)
            assert got.status_code in (400, 422), (who, got.text)
            messages[who] = got.json()["message"]
        assert "withheld" not in messages["person"], messages
        assert messages["person"] == messages["agent"] == messages["admin"], messages
        assert f"'{target}' is not a field of `customer create`" in messages["person"], messages
    root = str(hosted.root)
    said = redact_message(f"Cannot read {root}/hub.db.", {"path": f"{root}/hub.db"})
    assert root not in said and "<path withheld>" in said
