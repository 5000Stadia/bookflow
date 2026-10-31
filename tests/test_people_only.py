"""R187 increment 1: the people-only acts become capabilities; five stay a person's (design/principal-directives.md).

* the people-only list is exactly the five, and every other former entry names a capability held by people at the
  role that ran it before;
* every person's effective permissions are identical before and after the layer: every company command, every role
  (and installation administrator), every combination of denies over the command's own capabilities, through the
  policy engine itself, old catalog against new;
* no agent is suspended and no token revoked, in the engine and on a real activated root upgraded in place;
* over HTTP, for every former entry: an agent is refused naming the capability, passes once granted, and only while
  its principal holds it too; a person is never asked for it; a grant above the granter's own is refused.
"""
from itertools import combinations
import socket
import sqlite3

import pytest

import bookflow
from bookflow import BookflowError
from bookflow.core import registry
from bookflow.hub import capabilities, people_only, permission_catalog as c, permission_policy as p
from bookflow.hub import permission_runtime as runtime
from bookflow.hub import permission_bank_line_catalog as before_layer, permission_capabilities_catalog as layer
from tests.conftest import hosted_call, make_agent, make_legacy
from tests.test_row3_host import hosted  # noqa: F401 - fixture

REASON = {"X-Bookflow-Reason": "people-only witness"}
FIVE = {"user set-password", "token issue", "agent assign", "agent authorize", "permission activate"}


def test_the_people_only_list_is_exactly_the_five_and_every_other_entry_is_a_capability():
    registry.load_all()
    tip = runtime.current_catalog()
    # bulk-edit-v1 (on item-picture-v1) adds none: every record changes through its own update command, as the caller.
    # bank-line-details-v1 (on bulk-edit-v1) adds none: a deposit or bill payment from a line runs its own command.
    # user-home-v1 (R191, on bank-line-details-v1) adds none: one's own Home is self-service and another's is
    # user.manage, a capability.
    # automation-v1 (on user-home-v1) adds none: automate.bank-rule and automate.invoice are capabilities, not acts.
    # bank-rule-v1 (R108 increment 4, on automation-v1) adds none: making a rule live is automate.bank-rule.
    assert tip is runtime.known_catalog(c.BANK_RULE_POLICY_VERSION)
    assert people_only.people_only_commands() == FIVE == set(layer.STAYS_HUMAN)
    assert sorted(a.key for a in tip.CATALOG.admin_actions if a.human_only) == sorted(
        people_only.PEOPLE_ONLY_PREFIX + x for x in FIVE)
    # What was people-only one layer down is exactly the five plus the converted commands, and the closing date.
    was, closing = people_only._people_only(before_layer.CATALOG.version)
    assert was == FIVE | set(layer.COMMAND_CAPABILITY) and closing
    # §2's (a) list: 33 entries (32 commands and the closing date), 14 capabilities; §3 and §4 add the delivery four.
    section2 = {k: v for k, v in layer.CAPABILITIES.items() if not k.startswith(("delivery.", "automate."))}
    assert len(section2) == 14 and sum(len(names) for _, names in section2.values()) + 1 == 33
    commands = {d.name: d for d in tip.CATALOG.commands}
    defaults = set(tip.CATALOG.defaults)
    for capability, (threshold, names) in layer.CAPABILITIES.items():
        assert capability not in {x.name for x in before_layer.CATALOG.capabilities}, "every capability is new"
        roles = {d.role for d in defaults if d.requirement == c.Requirement(capability, threshold)}
        held = {"hub_admin"} if threshold == "hub_admin" else {*c.ROLES[c.ROLES.index(threshold):], "hub_admin"}
        assert roles == held, capability
        for name in names:
            assert name in commands and registry.get(name).is_write
            if commands[name].routed_scope == "company":
                # People hold it at no lower a role than the command already asked.
                assert c.THRESHOLDS.index(threshold) <= c.THRESHOLDS.index(commands[name].threshold) or (
                    capability == "delivery.connect"), name
    assert capabilities.capability_for("membership grant") == "membership.grant"
    assert capabilities.capability_for("token issue") is None


# ---------------------------------------------------------------- the engine: people identical, agents kept

def _deny_sets(catalog):
    """Every combination of denies over a company command's own capabilities."""
    sets = {()}
    for action in catalog.company_actions:
        names = sorted({r.capability for r in action.requirements})
        for k in range(1, len(names) + 1):
            sets.update(combinations(names, k))
    return sorted(sets)


def _comparison(denies):
    org, company = "O1", "C1"
    subjects, members = {}, {}
    for role in c.ROLES:
        for i, deny in enumerate(denies):
            subjects[f"H-{role}-{i}"] = p.Subject("human", True, False)
            members[(f"H-{role}-{i}", c.ScopeKey("company", company))] = p.Membership(role, p.Policy((), deny))
    for i, deny in enumerate(denies):  # installation administrators, with and without a membership
        subjects[f"X-{i}"] = p.Subject("human", True, True)
        members[(f"X-{i}", c.ScopeKey("company", company))] = p.Membership("owner", p.Policy((), deny))
    subjects["X-bare"] = p.Subject("human", True, True)
    agents = {}
    for role in c.ROLES:  # an agent per role, for its same-role principal
        subjects[f"A-{role}"] = p.Subject("agent", True, False)
        members[(f"A-{role}", c.ScopeKey("company", company))] = p.Membership(role, p.Policy((), ()))
        agents[f"A-{role}"] = p.AgentState(False, (f"H-{role}-0",))
    scopes = [c.ScopeKey("hub", "root"), c.ScopeKey("organization", org), c.ScopeKey("company", company),
              c.ScopeKey("future_company", org)]

    def phase(catalog):
        visibility = tuple(p.Visibility(i, s, s.kind in ("hub", "organization") or subjects[i].hub_admin
                                        or (s.kind == "company" and (i, s) in members)) for i in subjects for s in scopes)
        built = p.Phase(catalog, (p.OrganizationSlot(org, True),), (p.CompanySlot(company, True, org),),
                        tuple(p.SubjectSlot(i, v) for i, v in subjects.items()),
                        tuple(p.MembershipSlot(i, s, members.get((i, s))) for i in subjects for s in scopes
                              if s.kind in ("organization", "company")),
                        visibility, tuple(p.AgentSlot(i, agents[i]) for i in sorted(agents)), "scoped_v1")
        manifest = p.PhaseManifest((org,), ((company, org),), tuple((i, v.kind) for i, v in subjects.items()),
                                   c.catalog_manifest(catalog))
        return built, manifest

    old, om = phase(before_layer.CATALOG)
    new, nm = phase(layer.CATALOG)
    return p.validate_comparison(p.ComparisonInput(p.Expected(om, nm), old, new)), subjects, agents


@pytest.mark.timeout(900)
def test_every_persons_permissions_are_identical_and_no_agent_is_suspended_by_the_layer():
    denies = _deny_sets(before_layer.CATALOG)
    value, subjects, agents = _comparison(denies)
    p._LAST = None
    result = p.compare(value)
    old = {s.subject: s for s in result.signatures_old}
    new = {s.subject: s for s in result.signatures_new}
    actions = before_layer.CATALOG.company_actions
    known = set(c.catalog_manifest(before_layer.CATALOG).company_requirements)  # every company atom before
    compared = admitted = 0
    for subject, facts in subjects.items():
        if facts.kind != "human":
            continue
        for a, b in zip(old[subject].scopes, new[subject].scopes):
            assert (a.scope, a.present, a.effective_visible, a.membership_role, a.hub_admin) == (
                b.scope, b.present, b.effective_visible, b.membership_role, b.hub_admin)
            assert a.admin_bits == b.admin_bits, (subject, a.scope)  # human_only never bound a person
            if a.scope.kind not in ("company", "future_company"):
                assert a.company_bits == b.company_bits == ()
                continue
            before, after = dict(a.company_bits), dict(b.company_bits)
            assert {r: before[r] for r in known} == {r: after[r] for r in known}
            gained = {r for r in after if r not in known and after[r]}
            assert all(r.capability in layer.CAPABILITIES for r in gained)  # only the new capabilities appear
            if a.scope.kind != "company":
                continue
            for action in actions:  # command x role x deny combination
                was = all(before[r] for r in action.requirements)
                now = all(after[r] for r in action.requirements)
                assert was == now, (subject, action.key)
                compared += 1
                admitted += was
    assert compared == len(actions) * (len(c.ROLES) + 1) * len(denies) + len(actions)
    assert 0 < admitted < compared
    print(f"\n{compared} command x role x deny admissions compared ({admitted} admitted), "
          f"{len(actions)} commands, {len(denies)} deny combinations, identical before and after")
    assert len(result.agents) == len(agents)
    for transition in result.agents:
        assert not transition.needs_suspension and not transition.remains_suspended, transition


# ---------------------------------------------------------------- over HTTP

PROBES = {
    "customer merge": {"merged": "Nobody A", "into": "Nobody B"}, "customer unmerge": {"merged": "Nobody A"},
    "vendor merge": {"merged": "Nobody A", "into": "Nobody B"}, "vendor unmerge": {"merged": "Nobody A"},
    "account merge": {"merged": "Nobody A", "into": "Nobody B"}, "account unmerge": {"merged": "Nobody A"},
    "item merge": {"merged": "Nobody A", "into": "Nobody B"}, "item unmerge": {"merged": "Nobody A"},
    "invoice restore": {"invoice": "NOPE-1"}, "journal restore": {"journal": "NOPE-1"},
    "item convert": {"item": "Nobody", "to": "inventory"},
    "check print-confirm": {"run": "nope"}, "check print-cancel": {"run": "nope"}, "check reprint": {"check": "nope"},
    "clock link": {"employee": "Nobody"},
    "accountant add": {"user": "nobody", "through": "2026-01-31"}, "accountant remove": {"user": "nobody"},
    "accountant-copy cancel": {}, "accountant-copy import": {"file": "/nonexistent/copy.bfa"},
    "costing-method set": {"method": "fifo", "effective_from": "2026-01-01"},
    "costing-method withdraw": {"switch": "nope"},
    "delivery approve": {"messages": ["M-NOPE"]}, "delivery mark-sent": {"message": "M-NOPE"},
    "delivery-connection add": {"name": "probe", "kind": "smtp"},
    "delivery-connection check": {"connection": "nope"}, "delivery-connection disable": {"connection": "nope"},
    "delivery-connection enable": {"connection": "nope"}, "delivery-connection remove": {"connection": "nope"},
    "delivery-connection update": {"connection": "nope"}, "delivery-schedule live": {"schedule": "nope"},
    "demo reset": {}, "user add": {"username": "probe-user"}, "user activate": {"user": "nobody"},
    "user deactivate": {"user": "nobody"}, "agent create": {"username": "probe-agent"},
    "agent activate": {"agent": "nobody"}, "agent deactivate": {"agent": "nobody"},
    "agent unassign": {"agent": "nobody", "principal": "nobody"},
}


def _probe(hosted, name, headers, company):
    body = dict(PROBES.get(name, {}))
    if name == "backup schedule":
        body = {"company": company}
    elif name in ("membership grant", "membership revoke"):
        body = {"user": "nobody", "company": company, **({"role": "readonly"} if name == "membership grant" else {})}
    elif name == "company update":
        body = {"closing_date": "2026-01-31"}
    scoped = registry.get(name).scope == "company"
    path = (f"/companies/{company}/commands/" if scoped else "/commands/") + name.replace(" ", ".") + "?dry_run=true"
    return hosted.api.post(path, json=body, headers={**REASON, **headers})


def _version(hosted, user, company):
    with sqlite3.connect((hosted.root / "hub.db").as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT version FROM memberships WHERE user_id=? AND scope_type='company' AND scope_id=?",
                         (user, company)).fetchone()
    return row[0] if row else 0


def _grant(hosted, user, role, *, grants=(), denies=(), headers=None):
    cid = hosted.company_id
    return hosted.ok("membership.grant", {"user": user, "company": cid, "role": role, "grants": list(grants),
                                          "denies": list(denies), "expected_version": _version(hosted, user, cid)},
                     headers={**REASON, **(headers or {})})


def _bearer(hosted, user, principal=None, label="t"):
    body = {"user": user, "label": label, **({"principal": principal} if principal else {})}
    return {"Authorization": "Bearer " + hosted.ok("token.issue", body, headers=REASON)["secret"]}


ENTRIES = sorted([*layer.COMMAND_CAPABILITY, "company update"])


def _capability(name):
    return layer.CLOSING_DATE_CAPABILITY if name == "company update" else layer.COMMAND_CAPABILITY[name]


@pytest.mark.timeout(900)
def test_each_entry_refuses_an_agent_by_capability_admits_it_once_granted_and_only_with_its_principal(hosted):
    cid = hosted.company_id
    everything = sorted(layer.CAPABILITIES)
    company_caps = sorted(k for k, (t, _) in layer.CAPABILITIES.items() if t != "hub_admin")
    # principal-p: an installation administrator who owns the company -- holds every capability.
    hosted.ok("user.add", {"username": "principal-p", "company": cid, "role": "owner", "hub_admin": True,
                           "password": "pw-principal-p-12345"}, headers=REASON)
    # principal-q: an admin of the company who is denied every company capability and administers no installation.
    q = hosted.ok("user.add", {"username": "principal-q", "company": cid, "role": "admin",
                               "password": "pw-principal-q-12345"}, headers=REASON)["user_id"]
    # Restoring also needs the document's own explicit Delete grant, held by everyone here so only the capability differs.
    deletes = ["transaction.invoice.delete", "transaction.journal_entry.delete"]
    _grant(hosted, q, "admin", grants=deletes, denies=company_caps)
    p_id = hosted.ok("user.list", {})["items"]
    _grant(hosted, next(x["user_id"] for x in p_id if x["username"] == "principal-p"), "owner", grants=deletes)
    agent = make_agent(hosted_call(hosted), "cap-agent", principals="principal-p", company=cid, role="admin")
    other = make_agent(hosted_call(hosted), "cap-agent-q", principals="principal-q", company=cid, role="admin")
    _grant(hosted, agent, "admin", grants=deletes)
    _grant(hosted, other, "admin", grants=deletes)
    as_agent, as_other = _bearer(hosted, agent, "principal-p"), _bearer(hosted, other, "principal-q")
    as_person = _bearer(hosted, "principal-p")

    # 1. Not granted: refused, naming the capability, and pointing at the principal.
    for name in ENTRIES:
        r = _probe(hosted, name, as_agent, cid)
        body = r.json()
        assert r.status_code == 403 and body["code"] == "E_PERMISSION", (name, r.text)
        assert body["details"]["capability"] == _capability(name), (name, body)
        assert body["details"]["reason"] == "capability_not_granted", (name, body)
        assert _capability(name) in body["message"] and "principal" in body["message"], (name, body)
    # 2. Granted, by an installation administrator: admitted (the probe's made-up records may still be missing).
    _grant(hosted, agent, "admin", grants=everything + deletes)
    _grant(hosted, other, "admin", grants=everything + deletes)
    for name in ENTRIES:
        r = _probe(hosted, name, as_agent, cid)
        assert r.status_code != 403, (name, r.text)
    # 3. Granted, but its principal does not hold it: refused, saying so.
    for name in ENTRIES:
        r = _probe(hosted, name, as_other, cid)
        body = r.json()
        assert r.status_code == 403 and body["details"].get("capability") == _capability(name), (name, r.text)
        assert body["details"]["reason"] == "principal_lacks_capability", (name, body)
    # 4. A person at the old role is never asked for a capability.
    for name in ENTRIES:
        r = _probe(hosted, name, as_person, cid)
        assert r.status_code != 403, (name, r.text)
    # The five stay a person's, granted or not, and say so in both the old and the new words.
    r = hosted.call("token.issue", {"user": "principal-q", "label": "x"}, headers={**REASON, **as_agent})
    details = r.json()["details"]
    assert r.status_code == 403 and details["reason"] == "people_only" and details["principal_only"] is True


@pytest.mark.timeout(900)
def test_a_granted_agent_really_does_the_work_within_its_own_ceiling(hosted):
    cid = hosted.company_id
    hosted.ok("user.add", {"username": "principal-p", "company": cid, "role": "owner", "hub_admin": True,
                           "password": "pw-principal-p-12345"}, headers=REASON)
    agent = make_agent(hosted_call(hosted), "work-agent", principals="principal-p", company=cid, role="admin")
    as_agent = _bearer(hosted, agent, "principal-p")
    _grant(hosted, agent, "admin", grants=["party.merge", "company.closing-date", "membership.grant", "user.manage",
                                           "agent.manage"])
    # Merges and the closing date.
    for name in ("Dup Plumbing A", "Dup Plumbing B"):
        hosted.ok("customer.create", {"name": name}, company=cid, headers={**REASON, **as_agent})
    merged = hosted.ok("customer.merge", {"merged": "Dup Plumbing A", "into": "Dup Plumbing B"}, company=cid,
                       headers={**REASON, **as_agent})
    assert merged
    hosted.ok("company.update", {"closing_date": "2026-01-31"}, company=cid, headers={**REASON, **as_agent})
    assert hosted.info()["info"]["closing_date"] == "2026-01-31"
    # A user without a password, never an installation administrator.
    added = hosted.ok("user.add", {"username": "new-clerk"}, headers={**REASON, **as_agent})
    assert added["password"] is None and not added["hub_admin"] and "set-password" in added["message"]
    for body in ({"username": "boss", "hub_admin": True}, {"username": "spy", "password": "pw-spy-12345678"}):
        r = hosted.call("user.add", body, headers={**REASON, **as_agent})
        assert r.status_code == 403 and r.json()["details"]["reason"] == "above_own_authority", r.text
    # Memberships: up to its own role and capabilities, never its own or its principal's.
    clerk = added["user_id"]
    assert _grant(hosted, clerk, "standard", grants=["party.merge"], headers=as_agent)["role"] == "standard"
    for user, role, grants in ((clerk, "owner", []), (clerk, "standard", ["costing.method"]),
                               (clerk, "standard", ["backup.schedule"]), (agent, "admin", ["costing.method"])):
        r = hosted.call("membership.grant", {"user": user, "company": cid, "role": role, "grants": grants,
                                             "expected_version": _version(hosted, user, cid)},
                        headers={**REASON, **as_agent})
        assert r.status_code == 403, (user, role, grants, r.text)
    # A person who is not an installation administrator cannot give an installation-wide capability either.
    hosted.ok("user.add", {"username": "co-admin", "company": cid, "role": "admin", "password": "pw-co-admin-12345"},
              headers=REASON)
    as_co_admin = _bearer(hosted, "co-admin")
    r = hosted.call("membership.grant", {"user": clerk, "company": cid, "role": "standard", "grants": ["user.manage"],
                                         "expected_version": _version(hosted, clerk, cid)},
                    headers={**REASON, **as_co_admin})
    assert r.status_code == 403, r.text
    # Agents: create one for its principal, deactivate it; never itself.
    made = hosted.ok("agent.create", {"username": "helper-bot"}, headers={**REASON, **as_agent})["agent"]
    assert made["owner_username"] == "principal-p"
    hosted.ok("agent.deactivate", {"agent": "helper-bot"}, headers={**REASON, **as_agent})
    r = hosted.call("agent.deactivate", {"agent": agent}, headers={**REASON, **as_agent})
    assert r.status_code == 403, r.text
    # Still an agent: the five stay out of reach.
    r = hosted.call("user.set-password", {"username": "new-clerk", "password": "pw-new-clerk-12345"},
                    headers={**REASON, **as_agent})
    assert r.status_code == 403 and r.json()["details"]["reason"] == "people_only", r.text


def _agent(hosted, role="admin"):
    """An agent for principal-p (an owner), with its bearer headers; other suites use it to probe agent refusals."""
    hosted.ok("user.add", {"username": "principal-p", "company": hosted.company_id, "role": "owner",
                           "password": "pw-principal-p-12345"}, headers=REASON)
    agent = make_agent(hosted_call(hosted), "rules-agent", principals="principal-p",
                       company=hosted.company_id, role=role)
    secret = hosted.ok("token.issue", {"user": agent, "principal": "principal-p", "label": "rules"},
                       headers=REASON)["secret"]
    return agent, {"Authorization": "Bearer " + secret, **REASON}


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.timeout(900)
def test_a_root_activated_at_the_previous_catalog_upgrades_with_nothing_broader_and_no_agent_suspended(root, monkeypatch):
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.config import os_login
    from bookflow.core.context import client_version
    from tests.test_row3_host import PASSWORD, Hosted

    make_legacy(root)
    lib = bookflow.connect(data_root=str(root))
    with monkeypatch.context() as previous_build:
        previous_build.setattr(runtime, "current_catalog", lambda: runtime.known_catalog(c.BANK_FEED_REVERSE_POLICY_VERSION))
        state = lib.permission.show()
        lib.permission.activate(expected_generation=state["generation"], expected_catalog_sha256=state["catalog_sha256"])
    company = lib.company.list()["items"][0]["company_id"]
    login = os_login()
    lib.run("user set-password", {"username": login, "password": PASSWORD})
    issued = lib.token.issue(label="installer")
    handle = start_serving(root, client_version(), bind=f"127.0.0.1:{_free_port()}", secure_cookies=False)
    try:
        hosted = Hosted(handle, root, login, company, issued, "", {})
        hosted.ok("user.add", {"username": "principal-p", "company": company, "role": "owner",
                               "password": "pw-principal-p-12345"}, headers=REASON)
        agent = make_agent(hosted_call(hosted), "upgrade-agent", principals="principal-p", company=company,
                           role="admin")
        as_agent = _bearer(hosted, agent, "principal-p")
        as_person = _bearer(hosted, "principal-p")

        names = iter(range(10))

        def acts():
            return [_probe(hosted, n, h, company).status_code
                    for h in (as_agent, as_person) for n in ("customer merge", "company update")] + [
                hosted.call("customer.create", {"name": f"Upgrade {next(names)}"}, company=company,
                            headers={**REASON, **as_agent}).status_code]

        seen = [acts()]
        state = hosted.ok("permission.show", {})
        upgraded = hosted.ok("permission.activate", {"expected_generation": state["generation"],
                                                     "expected_catalog_sha256": state["catalog_sha256"]}, headers=REASON)
        assert upgraded["changed"] and upgraded["affected_agents"] == []
        with sqlite3.connect((root / "hub.db").as_uri() + "?mode=ro", uri=True) as db:
            assert db.execute("SELECT catalog_version FROM permission_state").fetchone() == ("bank-rule-v1",)  # the tip, on automation-v1
            assert db.execute("SELECT count(*) FROM agent_authority WHERE suspended_at IS NOT NULL AND agent_user_id=?",
                              (agent,)).fetchone() == (0,)
            assert db.execute("SELECT count(*) FROM api_tokens WHERE revoked_at IS NOT NULL").fetchone() == (0,)
        seen.append(acts())
        # Before: the agent was refused the people-only acts; after: refused by capability, nothing broader.
        # The person and the agent's ordinary work are unchanged.
        # (The person's merge probe names customers that do not exist: admitted, then not found.)
        assert seen[0] == seen[1] == [403, 403, 404, 200, 200], seen
        r = _probe(hosted, "customer merge", as_agent, company)
        assert r.json()["details"]["reason"] == "capability_not_granted"
    finally:
        handle.stop()


def test_a_person_is_never_asked_for_a_capability_and_an_actorless_caller_is_refused():
    class Actor:
        def __init__(self, kind):
            self.kind, self.id, self.hub_admin = kind, "x", False

    class S:
        def __init__(self, actor):
            self.actor, self.company_row, self.hub = actor, {"id": "c", "organization_id": "o"}, None

    for name in layer.COMMAND_CAPABILITY:
        capabilities.require_company(S(Actor("human")), name)  # returns at once: nothing asked of a person
    with pytest.raises(BookflowError) as refused:
        capabilities.require_company(S(None), "customer merge")
    assert refused.value.details["reason"] == "people_only"


def test_demo_reset_never_runs_while_the_demo_organization_holds_a_real_company(root):
    c_ = bookflow.connect(data_root=str(root))
    demo_org = c_.company.list()["items"][0]["organization_id"]
    c_.run("company new", {"display_name": "Inside Co", "legal_name": "Inside Co LLC",
                           "home_currency": "USD", "organization": demo_org})
    for body in ({}, {"force": str(root)}):  # force does not override it, even for an installation administrator
        with pytest.raises(BookflowError) as held:
            c_.run("demo reset", body, dry_run=True)
        assert held.value.details["reason"] == "real_company_in_demo_organization"
        assert held.value.details["real_companies"] == ["Inside Co"]
        assert str(root) not in held.value.message
    assert "Inside Co" in {x["display_name"] for x in c_.company.list()["items"]}


def test_demo_reset_refuses_a_root_holding_real_books_unless_force_names_it(root):
    c_ = bookflow.connect(data_root=str(root))
    organization = c_.run("organization new", {"name": "Roofing Org"})["organization_id"]
    c_.run("company new", {"display_name": "Real Roofing", "legal_name": "Real Roofing LLC",
                           "home_currency": "USD", "organization": organization})
    with pytest.raises(BookflowError) as refused:
        c_.run("demo reset", {})
    assert refused.value.code == "E_PERMISSION"
    assert refused.value.details["reason"] == "real_company_present"
    assert refused.value.details["real_companies"] == ["Real Roofing"]
    assert "force" in str(refused.value) and str(root) not in refused.value.message
    with pytest.raises(BookflowError) as wrong:
        c_.run("demo reset", {"force": str(root / "elsewhere")})
    assert wrong.value.details["reason"] == "real_company_present"
    preview = c_.run("demo reset", {"force": str(root)}, dry_run=True)
    assert preview["display_name"] == "Demo Plumbing Co"
    assert "Real Roofing" in {x["display_name"] for x in c_.company.list()["items"]}


def test_a_test_never_resolves_the_real_data_root(monkeypatch):
    from bookflow.storage import paths
    monkeypatch.delenv("BOOKFLOW_DATA_ROOT", raising=False)
    with pytest.raises(BookflowError) as refused:
        paths.resolve_data_root()
    assert refused.value.details["reason"] == "real_root_under_test"
    with pytest.raises(BookflowError):
        paths.resolve_data_root(str(paths.default_data_root()))
