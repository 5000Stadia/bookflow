"""R168: the real books are protected from agents changing the rules.

The closing date and who can do what are a person's (catalog human-administration-v1): an agent
is refused whatever its role and told to ask its principal, a person with today's rights is not,
and an agent still posts. `demo reset` refuses a root holding real books unless `force` names it.
"""
import pytest

import bookflow
from bookflow import BookflowError
from bookflow.core import registry
from bookflow.hub import permission_human_admin_catalog as layer, people_only, permission_runtime as runtime
from bookflow.hub import permission_cutover_rest_catalog as tip_layer
from tests.conftest import hosted_call, make_agent
from tests.test_row3_host import hosted  # noqa: F401 - fixture

REASON = {"X-Bookflow-Reason": "people-only witness"}


def test_the_people_only_list_is_the_catalogs_and_covers_every_write_that_changes_who_can_do_what():
    registry.load_all()
    tip = runtime.current_catalog()
    # The tip has since moved on (sales-tax-adjustment-v1, restore-v1, party-merge-v1, then cutover-rest-v1), which carry
    # these admin actions and add their own people-only acts: restoring a deleted journal entry or invoice, merging and
    # unmerging customers and vendors. cutover-rest-v1's `vendor 1099-opening` is not one: an agent may set it.
    assert tip is tip_layer and set(layer.CATALOG.admin_actions) <= set(tip.CATALOG.admin_actions)
    assert people_only.people_only_commands() == frozenset(layer.PEOPLE_ONLY_COMMANDS) | {
        'journal restore', 'invoice restore', 'customer merge', 'customer unmerge', 'vendor merge', 'vendor unmerge'}
    # Every write at the identity capabilities is people-only; the one exception lets an
    # agent revoke its own token. Reads are not listed.
    writes = {d.name for d in tip.CATALOG.commands if d.capability in ("user", "membership", "token")
              and registry.get(d.name).is_write}
    assert writes - people_only.people_only_commands() == {"token revoke"}
    assert not {n for n in people_only.people_only_commands() if not registry.get(n).is_write}
    assert {"demo reset", "backup schedule"} <= people_only.people_only_commands()
    closing = next(a for a in tip.CATALOG.admin_actions if a.key == people_only.CLOSING_DATE_ACTION)
    assert closing.human_only and closing.domain == "company"


def _agent(hosted, role="admin"):
    hosted.ok("user.add", {"username": "principal-p", "company": hosted.company_id, "role": "owner",
                           "password": "pw-principal-p-12345"}, headers=REASON)
    agent = make_agent(hosted_call(hosted), "rules-agent", principals="principal-p",
                       company=hosted.company_id, role=role)
    secret = hosted.ok("token.issue", {"user": agent, "principal": "principal-p", "label": "rules"},
                       headers=REASON)["secret"]
    return agent, {"Authorization": "Bearer " + secret, **REASON}


@pytest.mark.timeout(600)
def test_an_admin_agent_cannot_touch_the_closing_date_or_memberships_but_still_posts(hosted):
    agent, as_agent = _agent(hosted)
    cid = hosted.company_id

    # 1. The closing date: refused for the agent, set, moved and cleared by a person.
    for value in ("2026-01-31", None):
        r = hosted.call("company.update", {"closing_date": value}, company=cid, headers=as_agent)
        assert r.status_code == 403, r.text
        body = r.json()
        assert body["code"] == "E_PERMISSION"
        assert body["details"]["rule"] == "the closing date is set by a person"
        assert body["details"]["field"] == "closing_date" and "principal" in body["details"]["next_step"]
        assert "principal" in body["message"]
        if value is not None:
            hosted.ok("company.update", {"closing_date": "2026-01-31"}, company=cid, headers=REASON)
    assert hosted.info()["info"]["closing_date"] == "2026-01-31"
    hosted.ok("company.update", {"closing_date": "2026-02-28"}, company=cid, headers=REASON)
    assert hosted.info()["info"]["closing_date"] == "2026-02-28"
    # Other fields keep today's rules for an admin agent.
    hosted.ok("company.update", {"phone": "555-0168"}, company=cid, headers=as_agent)
    assert hosted.info()["info"]["phone"] == "555-0168"

    # 2. Who can do what: the agent is refused, the person succeeds.
    person = hosted.ok("user.add", {"username": "clerk-c", "password": "pw-clerk-c-12345"}, headers=REASON)
    grant = {"user": person["user_id"], "company": cid, "role": "readonly"}
    r = hosted.call("membership.grant", grant, headers=as_agent)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "E_PERMISSION"
    assert r.json()["details"]["rule"] == "user roles and permissions are set by a person"
    for name, body in (("user.add", {"username": "sneaky", "password": "pw-sneaky-12345"}),
                       ("agent.authorize", {"agent": agent, "confirm_permitted_use": True}),
                       ("token.issue", {"user": "clerk-c", "label": "x"})):
        r = hosted.call(name, body, headers=as_agent)
        assert r.status_code == 403 and r.json()["details"]["reason"] == "people_only", (name, r.text)
    assert hosted.ok("membership.grant", grant, headers=REASON)["role"] == "readonly"
    # Reads stay allowed.
    bearer = {k: v for k, v in as_agent.items() if k == "Authorization"}
    assert hosted.call("membership.list", {}, headers=bearer).status_code == 200, "reads stay allowed"

    # 3. The agent still posts documents.
    posted = hosted.ok("journal.post", {"date": "2026-04-01", "number": "AGENT-R168", "lines": [
        {"account": "Checking", "side": "debit", "amount": "12.34"},
        {"account": "Service Income", "side": "credit", "amount": "12.34"}]}, company=cid, headers=as_agent)
    assert posted["id"]


def test_demo_reset_refuses_a_root_holding_real_books_unless_force_names_it(root):
    c = bookflow.connect(data_root=str(root))
    organization = c.company.list()["items"][0]["organization_id"]
    c.run("company new", {"display_name": "Real Roofing", "legal_name": "Real Roofing LLC",
                          "home_currency": "USD", "organization": organization})
    with pytest.raises(BookflowError) as refused:
        c.run("demo reset", {})
    assert refused.value.code == "E_PERMISSION"
    assert refused.value.details["reason"] == "real_company_present"
    assert refused.value.details["real_companies"] == ["Real Roofing"]
    assert "force" in str(refused.value)
    with pytest.raises(BookflowError) as wrong:
        c.run("demo reset", {"force": str(root / "elsewhere")})
    assert wrong.value.details["reason"] == "real_company_present"
    preview = c.run("demo reset", {"force": str(root)}, dry_run=True)
    assert preview["display_name"] == "Demo Plumbing Co"
    assert "Real Roofing" in {x["display_name"] for x in c.company.list()["items"]}


def test_a_test_never_resolves_the_real_data_root(monkeypatch):
    from bookflow.storage import paths
    monkeypatch.delenv("BOOKFLOW_DATA_ROOT", raising=False)
    with pytest.raises(BookflowError) as refused:
        paths.resolve_data_root()
    assert refused.value.details["reason"] == "real_root_under_test"
    with pytest.raises(BookflowError):
        paths.resolve_data_root(str(paths.default_data_root()))
