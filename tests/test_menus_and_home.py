"""R191 / R114 increment 3: menus follow capabilities, and each person's Home (design/point-of-sale.md §8, §9).

Three members of one company (standard, readonly, and readonly denied `reports`) see exactly the menu sections,
Overview tiles and finder entries their effective capabilities open, and the command boundary still refuses what a
page no longer offers. A person whose Home is a report lands there on signing in and from the Home button; a Home
their permissions no longer open falls back to the Overview with its one line. `user home set` accepts only a page
the person may open, and setting another person's needs user.manage.
"""
import importlib
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from bookflow.adapters.workbench import home as Home
from tests.conftest import hosted_call, make_agent

from bookflow.hub.user_homes import FALLBACK_LINE
from tests.test_item_pictures_review import _membership_version
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401 - fixture

REASON = {"X-Bookflow-Reason": "menus and home"}
H = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}
ALL_SECTIONS = ["customers", "vendors", "employees", "items", "banking", "accounting", "reports", "company", "settings",
                "audit"]


def _password(name):
    return f"pw-{name}-12345678"


def _member(hosted, name, role, denies=()):
    cid = hosted.company_id
    user = hosted.ok("user.add", {"username": name, "password": _password(name), "company": cid, "role": role},
                     headers=REASON)["user_id"]
    if denies:
        _deny(hosted, user, role, denies)
    return user


def _deny(hosted, user, role, denies):
    reply = hosted.call("membership.grant", {"user": user, "company": hosted.company_id, "role": role, "grants": [],
                                             "denies": list(denies),
                                             "expected_version": _membership_version(hosted, user)}, headers=REASON)
    assert reply.status_code == 200, reply.text


def _login(hosted, name, password=None, follow=True):
    client = TestClient(hosted.handle.app, follow_redirects=follow)
    assert client.post("/login", json={"username": name, "password": password or _password(name)}).status_code == 200
    return client


def _seen(client, cid):
    page = client.get(f"/c/{cid}/")
    assert page.status_code == 200, page.text[:400]
    text = page.text
    finder = client.get(f"/c/{cid}/_finder").json()["items"]
    return dict(menu=re.findall(r'data-section="([a-z]+)"', text),
                users=f'href="/c/{cid}/users"' in text, advanced="Advanced tools" in text,
                administration='href="/hub/"' in text, report_tiles=len(re.findall(r'href="/c/[^"]+/report/', text)),
                finder_sections={item["section"] for item in finder},
                finder_reports=[item["href"] for item in finder if "/report/" in item["href"]],
                finder_menu=[item["label"] for item in finder if item["section"] == "Menu"])


def test_page_and_finder_sets_for_standard_readonly_and_readonly_with_denies(hosted):
    cid = hosted.company_id
    _member(hosted, "std", "standard")
    _member(hosted, "ro", "readonly")
    _member(hosted, "rod", "readonly", ["reports"])
    standard, readonly, denied = (_seen(_login(hosted, name), cid) for name in ("std", "ro", "rod"))
    for seen in (standard, readonly):
        assert seen["menu"] == ALL_SECTIONS and seen["report_tiles"] >= 1 and seen["finder_reports"]
        assert "Reports" in seen["finder_sections"] and "Reports" in seen["finder_menu"]
    # Neither administers memberships nor the installation: no Users & permissions, Administration or System activity.
    for seen in (standard, readonly, denied):
        assert not seen["users"] and not seen["administration"] and "Installation" not in seen["finder_sections"]
    assert standard["advanced"]
    # Denied `reports`: no Reports section, no report tile, no report in the finder; every other section stays.
    assert denied["menu"] == [s for s in ALL_SECTIONS if s != "reports"]
    assert denied["report_tiles"] == 0 and denied["finder_reports"] == [] and "Reports" not in denied["finder_sections"]
    assert "Reports" not in denied["finder_menu"] and "Overview" in denied["finder_menu"]
    # What it holds writes nothing (readonly's one write runs on reports), so Advanced tools goes too.
    assert not denied["advanced"]
    # The page set is smaller, never larger, at each step down.
    assert len(_login(hosted, "rod").get(f"/c/{cid}/_finder").json()["items"]) < len(
        _login(hosted, "ro").get(f"/c/{cid}/_finder").json()["items"])
    # The command boundary is unchanged and stays the guarantee: the report URL and the command are refused.
    client = _login(hosted, "rod")
    assert client.get(f"/c/{cid}/report/profit-and-loss").status_code == 403
    assert client.post(f"/companies/{cid}/commands/report.profit-and-loss", headers=H, json={"date_from": "2026-01-01", "date_to": "2026-12-31"}).status_code == 403
    # The section page itself is not offered; the owner (an installation administrator) still sees everything.
    owner = _seen(_login(hosted, hosted.login, PASSWORD), cid)
    assert owner["menu"] == ALL_SECTIONS and owner["users"] and owner["administration"] and owner["advanced"]


def test_a_denied_write_form_answers_the_refusal_page_up_front(hosted):
    cid = hosted.company_id
    _member(hosted, "nocust", "standard", ["customer"])
    client = _login(hosted, "nocust")
    assert client.get(f"/c/{cid}/customer/create").status_code == 403
    # Its section's "+ New customer" is gone too, and the customers' own records with it.
    assert f'href="/c/{cid}/customer/create"' not in client.get(f"/c/{cid}/_group/customers").text
    assert _login(hosted, hosted.login, PASSWORD).get(f"/c/{cid}/customer/create").status_code == 200


def test_a_home_that_is_a_report_is_where_signing_in_and_the_home_button_land(hosted):
    cid = hosted.company_id
    _member(hosted, "ro", "readonly")
    client = _login(hosted, "ro")
    made = client.post("/home", headers=H, data={"page": f"/c/{cid}/report/profit-and-loss"})
    assert 'data-make-home-result="set"' in made.text, made.text
    shown = client.post("/commands/user.home.show", headers=H, json={}).json()
    assert shown["home"]["url"] == f"/c/{cid}/report/profit-and-loss" and shown["home"]["available"]
    # Signing in lands there; a `next=` deep link still wins (the login answer carries it, unchanged).
    fresh = _login(hosted, "ro", follow=False)
    landed = fresh.get("/")
    assert landed.status_code == 303 and landed.headers["location"] == f"/c/{cid}/report/profit-and-loss"
    deep = fresh.post("/login", data={"username": "ro", "password": _password("ro"), "next": f"/c/{cid}/customer"},
                      headers={"HX-Request": "true"})
    assert deep.headers.get("HX-Redirect") == f"/c/{cid}/customer"
    # The Home button is on every page, and returns there.
    for page in (f"/c/{cid}/customer", f"/c/{cid}/_group/items", "/companies"):
        assert 'data-home-button' in fresh.get(page).text
    assert fresh.get("/").headers["location"] == f"/c/{cid}/report/profit-and-loss"


def test_a_revoked_home_falls_back_to_the_overview_with_its_line(hosted):
    cid = hosted.company_id
    user = _member(hosted, "ro", "readonly")
    assert _login(hosted, "ro").post("/commands/user.home.set", headers=H,
                                     json={"page": "report/profit-and-loss", "company": cid}).status_code == 200
    _deny(hosted, user, "readonly", ["reports"])
    client = _login(hosted, "ro", follow=False)
    landed = client.get("/")
    assert landed.status_code == 303 and landed.headers["location"] == f"/c/{cid}/?home=unavailable"
    page = client.get(landed.headers["location"])
    assert FALLBACK_LINE == "Your Home page is not available to you; showing the Overview."
    assert FALLBACK_LINE in page.text
    shown = client.post("/commands/user.home.show", headers=H, json={}).json()["home"]
    assert not shown["available"] and shown["unavailable_reason"] == "your permissions no longer open that page"
    # An Overview reached any other way carries no line.
    assert FALLBACK_LINE not in client.get(f"/c/{cid}/").text


def test_set_accepts_only_a_page_the_person_may_open_and_another_persons_needs_user_manage(hosted):
    cid = hosted.company_id
    _member(hosted, "rod", "readonly", ["reports"])
    _member(hosted, "std", "standard")
    rod = _login(hosted, "rod")

    def set_home(client, raw):
        return client.post("/commands/user.home.set", headers=H, json=raw)

    refused = set_home(rod, {"page": "report/profit-and-loss", "company": cid})
    assert refused.status_code == 422 and refused.json()["code"] == "E_VALIDATION"
    assert "may open" in refused.json()["message"]
    for bad in ("no-such-page", "../hub/user", "customer/x/../../y", "_group/nowhere", "https://evil.example/"):
        reply = set_home(rod, {"page": bad, "company": cid})
        assert reply.status_code == 422 and reply.json()["code"] == "E_VALIDATION", (bad, reply.text)
    # A section it may open, its Overview, a list page and a record's own page are all pages.
    for good in ("_group/items", "", "customer", "item"):
        assert set_home(rod, {"page": good, "company": cid}).status_code == 200, good
    # Another person's Home: refused without user.manage, through the same check a token would meet.
    std = _login(hosted, "std")
    other = set_home(std, {"user": "rod", "page": "item", "company": cid})
    assert other.status_code == 403 and other.json()["code"] == "E_PERMISSION"
    assert std.post("/commands/user.home.show", headers=H, json={"user": "rod"}).status_code == 403
    # The installation administrator (user.manage) sets a clerk's Home from their row in Users & permissions.
    owner = _login(hosted, hosted.login, PASSWORD)
    row = owner.get(f"/c/{cid}/users?user=rod")
    assert "Home page for" in row.text
    saved = owner.post(f"/c/{cid}/users/home", headers=H, data={"user": "rod", "page": "_group/items", "action": "set"})
    assert saved.status_code == 200 and "data-user-home-result" in saved.text
    assert owner.post("/commands/user.home.show", headers=H, json={"user": "rod"}).json()["home"]["page"] == "_group/items"
    # It still checks the clerk's own capabilities, not the administrator's.
    wrong = owner.post("/commands/user.home.set", headers=H, json={"user": "rod", "page": "report/profit-and-loss",
                                                                  "company": cid})
    assert wrong.status_code == 422 and wrong.json()["code"] == "E_VALIDATION"
    assert owner.post("/commands/user.home.clear", headers=H, json={"user": "rod"}).json()["home"] is None


def _agent(hosted, name, role="standard"):
    cid = hosted.company_id
    owner_id = hosted.ok("user.home.show", {})["user_id"]
    agent = make_agent(hosted_call(hosted), name, principals=owner_id, company=cid, role=role)
    issued = hosted.ok("token.issue", {"user": agent, "principal": owner_id, "label": name})
    return agent, {"Authorization": f"Bearer {issued['secret']}"}


def test_agents_meet_the_same_check_and_nobody_learns_who_exists(hosted):
    cid = hosted.company_id
    victim = _member(hosted, "victim", "readonly")
    agent, bearer = _agent(hosted, "home-bot")
    api = TestClient(hosted.handle.app)
    write = {**bearer, "X-Bookflow-Reason": "home"}
    # Its own Home: accepted, and the page comes back (never redacted as a filesystem path).
    own = api.post("/commands/user.home.set", headers=write, json={"page": "customer", "company": cid})
    assert own.status_code == 200 and own.json()["home"]["page"] == "customer", own.text
    assert api.post("/commands/user.home.show", headers=bearer, json={}).json()["home"]["page"] == "customer"
    # Another person's, without user.manage: refused by the capability, whether or not the person exists.
    for who in ("victim", victim, "no-such-person"):
        other = api.post("/commands/user.home.set", headers=write, json={"user": who, "page": "item", "company": cid})
        assert other.status_code == 403 and other.json()["code"] == "E_PERMISSION", (who, other.text)
        assert other.json()["details"]["capability"] == "user.manage"
    _member(hosted, "std", "standard")
    std = _login(hosted, "std")
    for who in ("victim", "no-such-person"):
        assert std.post("/commands/user.home.show", headers=H, json={"user": who}).status_code == 403
    # A person's own page field survives redaction too.
    assert std.post("/commands/user.home.set", headers=H, json={"page": "customer", "company": cid}).status_code == 200
    assert std.post("/commands/user.home.show", headers=H, json={}).json()["home"]["page"] == "customer"
    # user.manage reads an agent's Home; setting one stays refused.
    assert hosted.ok("user.home.show", {"user": agent})["home"]["page"] == "customer"
    refused = hosted.call("user.home.set", {"user": agent, "page": "item", "company": cid})
    assert refused.status_code == 422 and refused.json()["code"] == "E_VALIDATION"
    # The agent's pages offer what it may run for its principal: the intersection the boundary enforces.
    page = api.get(f"/c/{cid}/", headers=bearer)
    assert page.status_code == 200 and 'data-section="customers"' in page.text


def test_a_company_named_and_the_same_company_in_the_path_agree(hosted):
    cid = hosted.company_id
    name = hosted.ok("company.show", {}, company=cid)["display_name"]
    _member(hosted, "ro", "readonly")
    ro = _login(hosted, "ro")
    same = ro.post("/commands/user.home.set", headers=H, json={"page": f"/c/{cid}/customer", "company": name})
    assert same.status_code == 200, same.text
    assert same.json()["home"]["url"] == f"/c/{cid}/customer"


def test_the_menus_follow_a_permission_change_on_the_next_request(hosted):
    cid = hosted.company_id
    user = _member(hosted, "ro2", "readonly")
    client = _login(hosted, "ro2")
    before = _seen(client, cid)
    assert "reports" in before["menu"] and before["finder_reports"]
    _deny(hosted, user, "readonly", ["reports"])
    after = _seen(client, cid)
    assert "reports" not in after["menu"] and after["report_tiles"] == 0 and not after["finder_reports"]
    assert 'data-section="reports"' not in client.get("/companies").text  # a hub page, the same answer
    _deny(hosted, user, "readonly", [])
    assert "reports" in _seen(client, cid)["menu"]


def test_the_hub_address_survives_a_data_root_with_url_characters(tmp_path):
    from bookflow.adapters.workbench.pages import hub_reader_uri
    root = tmp_path / "books #1? 100%"
    root.mkdir()
    with sqlite3.connect(root / "hub.db") as db:
        db.execute("CREATE TABLE authority_generation (id INTEGER, token TEXT)")
        db.execute("INSERT INTO authority_generation VALUES (1, 'tok')")
    with sqlite3.connect(hub_reader_uri(root), uri=True) as db:
        assert db.execute("SELECT token FROM authority_generation").fetchone() == ("tok",)


def test_hub0017_ddl_is_what_the_metadata_declares():
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateTable
    from bookflow.hub import schema as h
    from bookflow.storage.migrate import HEADS
    m = importlib.import_module("bookflow.storage.hub_migrations.versions.0017_user_homes")
    assert m.revision == "hub0017" and m.down_revision == "hub0016" and HEADS["hub"] >= "hub0017"
    assert m.DDL == (str(CreateTable(h.user_homes).compile(dialect=sqlite.dialect())),)


def test_the_minimal_header_for_a_member_with_nothing_to_navigate(hosted):
    cid = hosted.company_id
    user = _member(hosted, "bare", "readonly")
    held = hosted.ok("membership.effective", {"company": cid, "user": user})
    caps = sorted({p["requirement"]["capability"] for p in held["permissions"] if p["admitted"]})
    _deny(hosted, user, "readonly", [c for c in caps if c != "company"])  # every page runs `company show`
    client = _login(hosted, "bare")
    page = client.get(f"/c/{cid}/")
    text = page.text
    assert page.status_code == 200 and "app-header-minimal" in text
    assert "data-home-button" in text and ">Sign out<" in text and 'class="current-company"' in text
    for gone in ("data-finder-open", 'id="menu-toggle"', 'id="company-navigation"', "account-menu",
                 "Start a transaction", "All tasks", "Advanced tools", 'id="finder"'):
        assert gone not in text, gone
    assert client.get(f"/c/{cid}/_finder").json()["items"] == []


def test_a_home_on_a_page_that_runs_a_report_falls_back_when_reports_are_denied(hosted):
    cid = hosted.company_id
    user = _member(hosted, "sites", "readonly")
    client = _login(hosted, "sites")
    assert client.post("/commands/user.home.set", headers=H, json={"page": "inventory/sites", "company": cid}
                       ).status_code == 200
    _deny(hosted, user, "readonly", ["reports"])
    shown = client.post("/commands/user.home.show", headers=H, json={}).json()["home"]
    assert not shown["available"]
    landed = _login(hosted, "sites", follow=False).get("/")
    assert landed.headers["location"] == f"/c/{cid}/?home=unavailable"
    refused = client.post("/commands/user.home.set", headers=H, json={"page": "accountant", "company": cid})
    assert refused.status_code == 422


@pytest.mark.timeout(900)
def test_every_page_runs_only_what_it_declares(hosted, monkeypatch):
    """PAGE_RUNS cannot drift from the pages: each declared page is opened (as the owner) and every command it runs
    beyond the ones it names must be declared."""
    from bookflow.adapters.http import execution
    cid = hosted.company_id
    ran = []
    original = execution.run_hosted

    def recording(host, cmd, *args, **kwargs):
        ran.append(cmd.name)
        return original(host, cmd, *args, **kwargs)

    monkeypatch.setattr(execution, "run_hosted", recording)
    client = _login(hosted, hosted.login, PASSWORD)
    undeclared = {}
    for destination, named in Home._declared_destinations().items():
        client.get(f"/c/{cid}/")  # every page's own reads warm, so only the page's are recorded
        ran.clear()
        reply = client.get(f"/c/{cid}{destination}")
        if reply.status_code != 200:
            continue  # switched off for this company (pick lists while picking is off)
        extra = set(ran) - set(named) - set(Home.PAGE_RUNS.get(destination, ())) - {
            "company show", "membership effective", "user home show", "agent show", "membership list", "user list"}
        if extra:
            undeclared[destination] = sorted(extra)
    assert undeclared == {}, undeclared


@pytest.mark.timeout(2400)
def test_every_offered_page_opens_for_standard_readonly_and_readonly_with_denies(hosted):
    cid = hosted.company_id
    members = {"std": "standard", "ro": "readonly", "rod": "readonly"}
    _member(hosted, "std", "standard")
    _member(hosted, "ro", "readonly")
    _member(hosted, "rod", "readonly", ["reports"])
    candidates = sorted(Home._declared_destinations()) + [f"/_group/{entry.slug}" for entry in Home.MENU] + [
        "/", "/_all", "/find", "/_registers", "/audit", "/report/profit-and-loss", "/customer", "/item"]
    refused = {}
    for name in members:
        client = _login(hosted, name)
        offered = {item["href"] for item in client.get(f"/c/{cid}/_finder").json()["items"]}
        for entry in Home.MENU:
            section = client.get(f"/c/{cid}/_group/{entry.slug}")
            if section.status_code == 200 and f'data-section="{entry.slug}"' in client.get(f"/c/{cid}/").text:
                offered.add(f"/c/{cid}/_group/{entry.slug}")
                offered |= {href for href in re.findall(r'href="(/c/' + cid + r'/[^"#?]*)', section.text)}
        for candidate in candidates:
            page = candidate.split("?")[0].strip("/")
            if client.post("/commands/user.home.set", headers=H, json={"page": page, "company": cid}).status_code == 200:
                offered.add(f"/c/{cid}/" + page)
        for href in sorted(offered):
            path = href[len(f"/c/{cid}/"):] if href.startswith(f"/c/{cid}/") else None
            if path is None or (path.startswith("_") and not path.startswith(("_group/", "_all", "_registers"))):
                continue  # another company, or a fragment route a page fetches for itself
            reply = client.get(href)
            if reply.status_code != 200:
                refused.setdefault(name, []).append((href.replace(f"/c/{cid}", ""), reply.status_code))
        client.post("/commands/user.home.clear", headers=H, json={})
    assert refused == {}, refused
