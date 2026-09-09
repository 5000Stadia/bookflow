"""A fresh install must be able to read its own audit trail, on every surface.

The walkthrough shape, not a unit test of a mode flag: initialize a data root,
create an organization and a company, do a bookkeeper's ordinary work, and ask
for the audit trail back through the CLI, through HTTP with a session cookie,
and through the workbench page the navigation advertises.

A never-activated root is the only kind an install can have: `init` leaves
`permission_state.mode` at `legacy`, and no registered command changes it, so
every authenticated reader has to work in that state or no install ever reads
its own history.
"""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow.core.errors import BookflowError
from bookflow.core import registry
from bookflow.core.config import os_login
from bookflow.core.context import client_version
from bookflow.commands.host_cmds import start_serving
from bookflow.storage.engine import open_database
from tests.conftest import BIN
from tests.test_row3_host import PASSWORD, WB, hosted  # noqa: F401

ORGANIZATION = "Fresh Install Org"
COMPANY = "Riverbend Plumbing"


def _work(client):
    """A bookkeeper's ordinary morning: bill a customer, then take the money."""
    income = client.account.create(name="Drain service income", type="income", company=COMPANY)["id"]
    customer = client.customer.create(name="Ada Waterworks", company=COMPANY)["id"]
    code = next(row["id"] for row in client.run("sales-tax-code list", {}, company=COMPANY)["items"]
                if not row["taxable"])
    item = client.run("item create", dict(name="Drain service", type="service", sales_enabled=True,
                                          description="Service labor", income_account_id=income,
                                          price="100.00", sales_tax_code_id=code), company=COMPANY)["id"]
    invoice = client.run("invoice post", dict(customer=customer, date="2026-06-01",
                                              lines=[dict(item=item, quantity="1", net_amount="100")]),
                         company=COMPANY, reason="bill the June service call")
    method = next(row["id"] for row in client.run("payment-method list", {}, company=COMPANY)["items"]
                  if row["kind"] == "cash")
    payment = client.run("payment receive", dict(customer=customer, date="2026-06-02", amount="100",
                                                 payment_method=method, operation_key="june-cash",
                                                 applications=dict(mode="inline", items=[
                                                     dict(invoice=invoice["id"], expected_version=1, amount="100")])),
                         company=COMPANY, reason="customer paid in cash")
    return invoice, payment


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A data root exactly as an installer leaves it: initialized, no demo seed."""
    root = tmp_path / "root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.run("organization new", dict(name=ORGANIZATION), reason="first organization")
    client.run("company new", dict(legal_name=COMPANY, home_currency="USD", organization=ORGANIZATION),
               reason="first company")
    return root, client


def test_a_fresh_install_is_never_activated(fresh):
    """The state every install is in, stated once so the rest is not a guess."""
    root, _ = fresh
    with open_database(root / "hub.db", writable=False) as db:
        assert db.raw.execute("SELECT mode, catalog_version, catalog_sha256, catalog_json "
                              "FROM permission_state").fetchall() == [("legacy", None, None, None)]
    registry.load_all()
    # No registered command names the permission catalog, so nothing an operator
    # can run turns `legacy` into `policy_v1`. (The `activate` verbs on the list
    # nouns are record activation and touch no permission state.)
    assert not [cmd.name for cmd in registry.all_commands(include_standalone=True)
                if "permission" in cmd.name or "catalog" in cmd.name]


def test_fresh_install_reads_its_own_audit_trail_from_the_cli(fresh):
    root, client = fresh
    invoice, payment = _work(client)

    events = client.run("audit list", {}, company=COMPANY)
    commands = [event["command"] for event in events["items"]]
    assert "invoice post" in commands and "payment receive" in commands
    posted = next(event for event in events["items"] if event["command"] == "invoice post")
    assert posted["reason"] == "bill the June service call"
    assert client.run("audit show", dict(event=posted["id"]), company=COMPANY)["entries"]
    assert client.run("hub audit list", {})["items"], "the hub trail records init and company new"

    # The reported reproduction was the packaged CLI, so ask it the same way.
    out = subprocess.run([str(BIN), "--json", "--company", COMPANY, "audit", "list"],
                         capture_output=True, text=True,
                         env={"BOOKFLOW_DATA_ROOT": str(root), "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, (out.stdout, out.stderr)
    assert {"invoice post", "payment receive"} <= {row["command"] for row in json.loads(out.stdout)["items"]}

    # `undo` cannot name an event it cannot obtain; the trail is where ids come from.
    listed = client.run("audit list", dict(record_type="customer"), company=COMPANY)["items"]
    undone = client.run("undo", dict(event_id=listed[-1]["id"]), company=COMPANY, reason="undo witness")
    assert undone["original_event_id"] == listed[-1]["id"]
    # `activity` reads the same trail through its own projection.
    assert client.run("activity", dict(record_type="customer", record_id=undone["affected_records"][0]["record_id"]),
                      company=COMPANY)["items"]


def test_fresh_install_reads_its_own_audit_trail_over_http_and_in_the_workbench(fresh):
    root, client = fresh
    _work(client)
    company_id = client.company.list()["items"][0]["company_id"]
    login = os_login()
    client.run("user set-password", dict(username=login, password=PASSWORD))
    handle = start_serving(root, client_version(), bind="127.0.0.1:8765", secure_cookies=False)
    try:
        browser = TestClient(handle.app)
        assert browser.post("/login", json={"username": login, "password": PASSWORD}).status_code == 200

        listed = browser.post(f"/companies/{company_id}/commands/audit.list", json={}, headers=WB)
        assert listed.status_code == 200, listed.text
        assert {"invoice post", "payment receive"} <= {row["command"] for row in listed.json()["items"]}
        assert browser.post("/commands/hub.audit.list", json={}, headers=WB).status_code == 200

        page = browser.get(f"/c/{company_id}/audit", follow_redirects=False)
        assert page.status_code == 200, (page.status_code, page.headers.get("location"))
        assert "invoice post" in page.text
        assert browser.get("/hub/audit", follow_redirects=False).status_code == 200
    finally:
        handle.stop()


def test_a_never_activated_root_can_observe_its_own_memberships(fresh):
    """The reader gate underneath every surface above, checked where it lives."""
    from bookflow.hub import permission_catalog as c, permission_runtime as runtime
    root, client = fresh
    company_id = client.company.list()["items"][0]["company_id"]
    with open_database(root / "hub.db", writable=False) as db:
        actor = db.raw.execute("SELECT id FROM users WHERE username=?", (os_login(),)).fetchone()[0]
        observed = runtime.observe_current(db)
        assert actor in observed.snapshot.comparison.subjects
        assert observed.snapshot.old.stamp.mode == "legacy"
        admitted = runtime.require_company(db, actor=actor, principal=None, company=company_id,
                                           requirement=c.Requirement("ledger.read", "member"))
        assert admitted.intersection_admitted


def test_administration_still_refuses_a_never_activated_root(fresh):
    """The relaxation is for self-observation only; a policy transition is not one."""
    from bookflow.hub import permission_runtime as runtime, permission_snapshot as s
    root, _ = fresh
    with open_database(root / "hub.db", writable=False) as db:
        bundle = runtime.catalog_bundle()
        loaded = s.load_root(db, catalog=bundle)
        with pytest.raises(s.SnapshotError) as refused:
            s.assemble_pair(loaded, loaded, old_catalog=bundle, new_catalog=bundle,
                            visibility=runtime.VISIBILITY)
        assert refused.value.args == ("legacy_comparison_unavailable", "mode")
        with pytest.raises(s.SnapshotError):
            # Two distinct roots are a comparison whatever the flag says.
            s.observe_pair(loaded, s.load_root(db, catalog=bundle), old_catalog=bundle,
                           new_catalog=bundle, visibility=runtime.VISIBILITY, activated=False)


def test_a_logged_in_user_is_never_redirected_to_the_login_they_completed(hosted, monkeypatch):
    """An unavailable page owes a signed-in user a reason, not another login."""
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200

    def refuse(inp, ctx, s):
        raise BookflowError("E_UNAUTHENTICATED")

    monkeypatch.setattr(registry.get("audit list"), "plan", refuse)
    page = browser.get(f"/c/{hosted.company_id}/audit", follow_redirects=False)
    assert page.status_code != 303 and page.headers.get("location") is None
    assert page.status_code == 401 and "E_UNAUTHENTICATED" in page.text

    # Following the link would have returned to the same page and bounced again.
    assert browser.get(f"/c/{hosted.company_id}/audit").status_code == 401

    # A browser that has not logged in is still sent to the login page.
    anonymous = TestClient(hosted.handle.app)
    sent = anonymous.get(f"/c/{hosted.company_id}/audit", follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"].startswith("/login?next=")
