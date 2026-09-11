"""The home window's availability contract.

A tile is live only when its backing commands are registered and routed, it declares a
destination, a write-labelled tile is backed by a command that writes -- and this file navigates
to that destination and finds a usable page. The navigation witness is the third condition, not a
courtesy check: registration and routing are discovery facts and say nothing about whether the
handler works or whether a browser page exists.
"""

import re

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from bookflow.adapters.mcp import catalog
from bookflow.adapters.workbench import home
from bookflow.core import registry
from bookflow.core.errors import BookflowError

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401


TILE = re.compile(r'<(?P<element>a|div) class="flow-tile[^"]*"[^>]*>(?P<body>.*?)</(?P=element)>', re.S)
TITLE = re.compile(r'<span class="flow-tile-title">(.*?)</span>')


def _browser(hosted) -> TestClient:
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def _main(text: str) -> str:
    return text.split("<main>", 1)[-1].split("</main>", 1)[0]


def _tiles(html: str) -> dict[str, tuple[str, str]]:
    """Map each tile's visible title to (element name, the whole opening tag plus body)."""
    found = {}
    for match in TILE.finditer(html):
        title = TITLE.search(match.group("body"))
        assert title, match.group(0)[:200]
        found[title.group(1)] = (match.group("element"), match.group(0))
    return found


def navigate_witness(browser: TestClient, item, label: str) -> None:
    """Assert a live tile's declared destination is the page a person actually lands on, and usable.

    Landing matters as much as status. A route that sends an authenticated reader to the login
    page answers 303 and then 200, and 200 alone would call that a working tile while the reader
    is stuck in a login loop. So the first response must be the page that was asked for.
    """
    assert item.href, f"{label}: a live tile must declare a destination"
    page = browser.get(item.href, follow_redirects=False)
    assert page.status_code == 200, (
        label, item.href, page.status_code, page.headers.get("location"),
        "the destination did not answer; a redirect is not a landing")
    assert 'name="password"' not in page.text, (label, item.href, "this destination asks a signed-in reader to log in")
    body = _main(page.text)
    assert 'class="error"' not in body, (label, item.href, body[:400])
    assert not re.search(r"\bE_[A-Z_]+\b", body), (label, item.href, body[:400])
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", page.text, re.S)
    assert heading and heading.group(1).strip(), (label, item.href, "no heading")
    # A usable page offers the reader somewhere to go or something to fill in.
    assert re.search(r"<(form|input|button|a )", body), (label, item.href, "nothing to do on this page")


def resolved(company_id):
    return home.resolve(company_id)


def live_steps(company_id):
    return [(panel, item) for panel in resolved(company_id) for item in panel.steps if item.live]


# ---------------------------------------------------------------- the positive witnesses

def test_every_live_tile_navigates_to_a_usable_page(hosted):
    browser = _browser(hosted)
    board = resolved(hosted.company_id)
    live = [(panel, item) for panel in board for item in panel.steps if item.live]
    assert live, "the board resolved nothing live; the map or the registry is broken"
    for panel, item in live:
        navigate_witness(browser, item, f"{panel.panel.id}/{item.step.id}")


def test_every_live_tile_is_reachable_through_mcp(hosted):
    """The same named action an agent can discover and run, not merely a command sharing its noun."""
    for panel, item in live_steps(hosted.company_id):
        label = f"{panel.panel.id}/{item.step.id}"
        writes = False
        for name in item.step.action.commands:
            listed = catalog.list_commands(prefix=name, limit=5)["commands"]
            assert name in {row["name"] for row in listed}, (label, name, "not in the MCP catalog")
            described = catalog.command_help(name)
            assert described["name"] == name, (label, name)
            assert described["local_only"] is False and described["standalone"] is False, (label, name)
            assert described["input_schema"], (label, name)
            writes = writes or described["kind"] == "write"
        if item.step.action.kind == home.WRITE:
            assert writes, (label, "a write tile whose commands only read")


def test_the_home_window_renders_its_panels_and_keeps_the_old_grid(hosted):
    browser = _browser(hosted)
    page = browser.get(f"/c/{hosted.company_id}/")
    assert page.status_code == 200
    for panel in home.PANELS:
        assert f'<h2 id="flow-panel-{panel.id}">{panel.title}</h2>' in page.text, panel.id
    for panel in resolved(hosted.company_id):
        for item in panel.steps:
            if item.live:
                assert f'href="{item.href}"' in page.text, item.step.id
    assert f'href="/c/{hosted.company_id}/_all"' in page.text
    assert f'href="/c/{hosted.company_id}/_planned"' in page.text

    grid = browser.get(f"/c/{hosted.company_id}/_all")
    assert grid.status_code == 200
    headings = ("Company", "Customers and sales", "Vendors and purchases", "Employees", "Items",
                "Accounting", "Settings", "Audit")
    positions = [grid.text.find(f"<h2>{heading}</h2>") for heading in headings]
    assert [p for p in positions if p >= 0] == sorted(p for p in positions if p >= 0)
    assert f'href="/c/{hosted.company_id}/term"' in grid.text


def test_the_home_window_runs_no_business_command_while_rendering(hosted, monkeypatch):
    """Resolution is a registry read. The page runs the company lookup it needs and nothing else."""
    from bookflow.adapters.http import execution

    ran = []
    original = execution.run_hosted

    def recording(host, cmd, *args, **kwargs):
        ran.append(cmd.name)
        return original(host, cmd, *args, **kwargs)

    monkeypatch.setattr(execution, "run_hosted", recording)
    browser = _browser(hosted)
    ran.clear()
    assert browser.get(f"/c/{hosted.company_id}/").status_code == 200
    assert ran == ["company show"], ran


# ---------------------------------------------------------------- the placeholder half

def test_a_step_with_no_registered_command_renders_an_inert_placeholder(hosted):
    page = _browser(hosted).get(f"/c/{hosted.company_id}/")
    tiles = _tiles(page.text)
    planned = [item for panel in resolved(hosted.company_id) for item in panel.steps if not item.live]
    assert planned, "the map declares nothing planned; the placeholder half is untested"
    for item in planned:
        for name in item.step.action.commands:
            assert registry.get(name) is None, (item.step.id, name, "declared live but rendered planned")
        element, markup = tiles[item.step.title]
        assert element == "div", (item.step.id, "a placeholder must not be an anchor")
        assert 'aria-disabled="true"' in markup, item.step.id
        assert "href=" not in markup, (item.step.id, "a placeholder must not link anywhere")
        assert item.reason.split(".")[0][:40] in markup, (item.step.id, "a placeholder must say what it waits on")


def test_the_planned_page_states_what_each_step_needs(hosted):
    browser = _browser(hosted)
    index = browser.get(f"/c/{hosted.company_id}/_planned")
    assert index.status_code == 200
    board = resolved(hosted.company_id)
    for panel in board:
        for item in panel.planned:
            assert f'href="/c/{hosted.company_id}/_planned/{item.step.id}"' in index.text, item.step.id
            page = browser.get(f"/c/{hosted.company_id}/_planned/{item.step.id}")
            assert page.status_code == 200, item.step.id
            assert item.step.action.label in page.text
            assert item.reason.split(".")[0][:40] in page.text
    # A step that has gone live no longer has a placeholder page; it redirects to the real one.
    live = next(item for panel in board for item in panel.steps if item.live)
    redirect = browser.get(f"/c/{hosted.company_id}/_planned/{live.step.id}", follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers["location"] == live.href
    assert browser.get(f"/c/{hosted.company_id}/_planned/not-a-step").status_code == 400


# ---------------------------------------------------------------- the contract itself

class _Empty(BaseModel):
    pass


def _stub(name: str, *, writes: bool = False, local_only: bool = False) -> registry.Command:
    return registry.Command(
        name=name, scope="company", description="stub", input_model=_Empty, output_model=_Empty,
        plan=lambda **kwargs: None, apply=None,
        writes=frozenset({"company"}) if writes else frozenset(),
        kind="write" if writes else "read", local_only=local_only)


def _panels(step: home.Step) -> tuple[home.Panel, ...]:
    return (home.Panel(id="probe", title="Probe", summary="", steps=(step,)),)


def _resolve_one(step: home.Step, catalogue: dict[str, registry.Command]) -> home.ResolvedStep:
    board = home.resolve(
        "C1", panels=_panels(step), get=catalogue.get,
        # mirror registry.routed_commands(): local-only commands never reach a routed surface
        routed=lambda: [command for command in catalogue.values() if not command.local_only])
    return board[0].steps[0]


# The stand-in for work the product has not built. Purchase orders are a planned tile with no
# commands and no page of their own, which is what makes them the honest example here: bills
# held this seat, then vendor credits did, and neither can any longer because both now have
# commands and a page of their own. The seat belongs to whatever is genuinely still unbuilt.
VIEW_ORDERS = home.Step(
    id="view-purchase-orders", title="Purchase orders", summary="What you have ordered from a vendor.",
    action=home.Action("View purchase orders", home.READ, ("purchase-order list",), "/purchase-order"),
    waits_on="purchase order commands")


def test_an_unregistered_command_is_a_placeholder():
    assert registry.get("purchase-order list") is None, "this branch is supposed to be without purchase orders"
    item = _resolve_one(VIEW_ORDERS, {})
    assert not item.live and item.href is None
    assert item.reason == "purchase order commands"


def test_a_local_only_command_is_a_placeholder():
    item = _resolve_one(VIEW_ORDERS, {"purchase-order list": _stub("purchase-order list", local_only=True)})
    assert not item.live, "a command the host does not route is not something the browser may offer"


def test_a_working_command_with_no_destination_is_a_placeholder():
    step = home.Step(id="nowhere", title="Nowhere", summary="",
                     action=home.Action("Open the customer list", home.READ, ("customer query",)),
                     waits_on="")
    item = _resolve_one(step, {"customer query": _stub("customer query")})
    assert not item.live and "browser page" in item.reason


def test_a_read_only_command_under_a_write_label_is_a_placeholder():
    step = home.Step(id="mislabelled", title="Write a purchase order", summary="",
                     action=home.Action("Write a purchase order", home.WRITE,
                                        ("purchase-order list",), "/purchase-order"))
    item = _resolve_one(step, {"purchase-order list": _stub("purchase-order list")})
    assert not item.live and "only read" in item.reason
    # the same declaration, honestly labelled, passes the lookup half of the contract
    honest = home.Step(id="honest", title="Purchase orders", summary="",
                       action=home.Action("View purchase orders", home.READ,
                                          ("purchase-order list",), "/purchase-order"))
    assert _resolve_one(honest, {"purchase-order list": _stub("purchase-order list")}).live


def test_the_map_never_labels_a_read_command_as_a_write():
    for panel in home.PANELS:
        for step in panel.steps:
            commands = [registry.get(name) for name in step.action.commands]
            if step.action.kind == home.WRITE and commands and all(command is not None for command in commands):
                assert any(command.is_write for command in commands), step.id


# ---------------------------------------------------------------- the flip, both directions

def test_a_tile_flips_with_registry_state_and_no_template_edit(hosted):
    """The same map and the same template: only what the registry answers changes."""
    browser = _browser(hosted)
    invoice = next(step for panel in home.PANELS for step in panel.steps if step.id == "invoice")

    without = _resolve_one(invoice, {})
    assert not without.live and "not registered yet" in without.reason

    real = home.resolve(hosted.company_id, panels=_panels(invoice))[0].steps[0]
    assert real.live and real.href == f"/c/{hosted.company_id}/invoice/post"
    navigate_witness(browser, real, "invoice")

    page = browser.get(f"/c/{hosted.company_id}/").text
    assert _tiles(page)["Invoice"][0] == "a"
    # Reconcile is the planned half of this pair: its commands do not exist yet, so the same
    # map and the same template render it as an inert div rather than a link.
    assert _tiles(page)["Reconcile"][0] == "div"


def test_registration_and_routing_alone_do_not_deliver_a_live_tile(hosted):
    """Registering purchase-order commands would satisfy the lookups. The witness still refuses."""
    stub = {"purchase-order list": _stub("purchase-order list")}
    item = _resolve_one(VIEW_ORDERS, stub)
    assert item.live, "the lookup half of the contract is satisfied by registration alone"

    browser = _browser(hosted)
    reachable = home.ResolvedStep(step=item.step, live=True, reason="",
                                  href=f"/c/{hosted.company_id}/purchase-order")
    with pytest.raises(AssertionError):
        navigate_witness(browser, reachable, "purchase-order")


def test_a_signed_in_reader_whose_command_refuses_sees_the_error_not_a_login(hosted, monkeypatch):
    """The integration guarantee: a completed login is never answered with "log in again".

    This pair used to be one test asserting the opposite, because `page_error` answered ANY
    E_UNAUTHENTICATED with a 303 to /login regardless of session — an infinite loop for a reader
    who had already logged in. That is fixed, so the signed-in half now asserts the fix, and the
    witness half below produces the redirect the honest way, by not logging in at all.
    """
    browser = _browser(hosted)
    audit = next(item for panel in resolved(hosted.company_id)
                 for item in panel.steps if item.step.id == "audit")

    def refused(*args, **kwargs):
        raise BookflowError("E_UNAUTHENTICATED", message="No valid credential: log in, or send a bearer token.")

    command = registry.get("audit list")
    assert command is not None and command.name in {c.name for c in registry.routed_commands()}
    monkeypatch.setattr(command, "plan", refused)
    answered = browser.get(audit.href, follow_redirects=False)
    assert answered.status_code != 303, "a signed-in reader must never be sent back to the login page"
    assert 'name="password"' not in answered.text, "a signed-in reader must never be shown a login form"


def test_a_destination_that_sends_a_reader_to_login_never_passes_the_witness(hosted):
    """A different failure shape from a broken handler, and the one a bare 200 check would miss.

    A reader without a session is legitimately redirected to /login, and the login page then
    answers 200. A witness that followed redirects, or that checked only the final status, would
    call that a working tile. The witness must refuse it: the first response has to be the page
    that was asked for.
    """
    stranger = TestClient(hosted.handle.app)
    audit = next(item for panel in resolved(hosted.company_id)
                 for item in panel.steps if item.step.id == "audit")
    trapped = stranger.get(audit.href, follow_redirects=False)
    assert trapped.status_code == 303 and trapped.headers["location"].startswith("/login")
    assert stranger.get(trapped.headers["location"]).status_code == 200, "the login page itself answers 200"
    with pytest.raises(AssertionError):
        navigate_witness(stranger, audit, "audit")


def test_a_registered_command_whose_handler_fails_never_passes_the_witness(hosted, monkeypatch):
    """The counterexample the contract was rewritten for: registered, routed, and broken."""
    browser = _browser(hosted)
    customers = next(item for panel in resolved(hosted.company_id)
                     for item in panel.steps if item.step.id == "customer-list")
    navigate_witness(browser, customers, "customer-list")

    def broken(*args, **kwargs):
        raise BookflowError("E_INTERNAL", message="Internal failure; see the host log.")

    command = registry.get("customer query")
    assert command is not None and command.name in {c.name for c in registry.routed_commands()}
    monkeypatch.setattr(command, "plan", broken)
    with pytest.raises(AssertionError):
        navigate_witness(browser, customers, "customer-list")


# ---------------------------------------------------------------- the menu

def test_every_menu_group_lands_on_a_page_of_that_group(hosted):
    browser = _browser(hosted)
    home_page = browser.get(f"/c/{hosted.company_id}/")
    for entry in home.MENU:
        assert f'href="/c/{hosted.company_id}/_group/{entry.slug}"' in home_page.text, entry.slug
        page = browser.get(f"/c/{hosted.company_id}/_group/{entry.slug}")
        assert page.status_code == 200, entry.slug
        assert f"<h1>{entry.label}</h1>" in page.text, entry.slug
        assert '<div class="noun-row">' in page.text, (entry.slug, "an empty section is not a destination")
        for group in entry.groups:
            assert f"<h2>{group}</h2>" in page.text, (entry.slug, group)
        for noun in entry.nouns:
            assert f"<h3>{noun}</h3>" in page.text, (entry.slug, noun)
    assert browser.get(f"/c/{hosted.company_id}/_group/nope").status_code == 400


def test_a_company_the_credential_cannot_see_never_renders_a_board(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": "outsider", "password": "another-long-password"}).status_code == 200
    for path in ("/", "/_all", "/_planned", "/_group/customers"):
        response = browser.get(f"/c/{hosted.company_id}{path}", follow_redirects=False)
        assert response.status_code in (303, 403, 404), (path, response.status_code)
        assert "flow-tile" not in response.text
