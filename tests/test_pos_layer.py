"""R114 increment 4: the POS layer (design/point-of-sale.md §2, §9; criteria 2 and 8).

Off by default, a company reads exactly as before: every `pos*` command refuses with the switch's message and no POS
surface shows. Turning it on makes register Main and its walk-in customer; turning it off and on again keeps them.
The POS clerk preset, applied in Users & permissions, leaves a person holding exactly `company` and `item` (read)
and `pos.sell` on the live catalog, lands them on the POS with the minimal header, and refuses them the books on
every interface. An agent holds no POS capability until its principal grants it.
"""
import contextlib
import json
import re

import anyio
import pytest
from fastapi.testclient import TestClient

from bookflow.hub import permission_runtime as runtime, pos_presets
from tests.conftest import hosted_call, make_agent
from tests.test_item_pictures_review import _membership_version
from tests.test_row3_host import PASSWORD, hosted, live  # noqa: F401 - fixtures

REASON = {"X-Bookflow-Reason": "pos layer"}
H = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}
# The clerk preset's denies on the current catalog: every company capability `readonly` holds but `company` and
# `item`. A capability added to `readonly` later fails here first, and must be judged before a clerk can read it.
CLERK_DENIES = ['account', 'activity', 'attachment', 'audit', 'bank-feed', 'bulk-edit', 'chart', 'class',
                'custom-field', 'customer', 'customer-message', 'customer-type', 'customer-work', 'directive',
                'employee', 'import', 'item-category', 'job-type', 'ledger.read', 'note', 'other-name',
                'payment-method', 'price-level', 'profile', 'reports', 'sales-rep', 'sales-tax-code', 'ship-method',
                'term', 'unit-of-measure', 'vendor', 'vendor-type']


def _password(name):
    return f"pw-{name}-12345678"


def _login(hosted, name, password=None, follow=True):
    client = TestClient(hosted.handle.app, follow_redirects=follow)
    assert client.post("/login", json={"username": name, "password": password or _password(name)}).status_code == 200
    return client


def _pos(hosted, on):
    info = hosted.info()
    return hosted.ok("company.update", {"pos_enabled": on, "expected_version": info["info_version"]},
                     company=hosted.company_id, headers=REASON)


def _registers(hosted):
    return hosted.ok("pos-register.list", {}, company=hosted.company_id)["items"]


def test_off_by_default_then_main_and_its_walk_in_survive_off_and_on(hosted):
    cid = hosted.company_id
    # Criterion 8: off means off. The switch reads false, every pos* command refuses, and no surface shows.
    assert hosted.info()["info"]["pos_enabled"] is False
    for name, body in (("pos-register.list", {}), ("pos-register.show", {"pos_register": "Main"}),
                       ("pos-register.create", {"name": "Till 2"})):
        refused = hosted.call(name, body, company=cid, headers=REASON if name.endswith("create") else None)
        assert refused.status_code in (400, 403, 409), refused.text
        assert refused.json()["code"] == "E_FEATURE_DISABLED", refused.text
        assert refused.json()["details"] == {"feature": "pos", "setting": "pos_enabled",
                                             "enable_command": "company update"}
    owner = _login(hosted, hosted.login, PASSWORD)
    for path in ("/", "/_group/customers", "/_all"):
        assert f'href="/c/{cid}/pos"' not in owner.get(f"/c/{cid}{path}").text, path
    assert not [item["href"] for item in owner.get(f"/c/{cid}/_finder").json()["items"]
                if re.search(r"/pos(-register)?(/|\?|$)", item["href"])]
    assert owner.get(f"/c/{cid}/pos").status_code != 200
    refused = hosted.call("user.home.set", {"page": "pos", "company": cid}, headers=REASON)
    assert refused.json()["code"] == "E_VALIDATION", refused.text

    # On: register Main with its defaults, and a walk-in customer made for it.
    _pos(hosted, True)
    (main,) = _registers(hosted)
    assert main["name"] == "Main" and main["receipt_width"] == 80 and main["count_shifts"] is False
    assert main["deposit_account"]["name"] == "Undeposited Funds"
    kinds = [m["kind"] for m in main["payment_methods"]]
    rank = {"cash": 0, "credit_card": 1, "debit_card": 1, "check": 2}
    assert kinds and kinds[0] == "cash" and kinds == sorted(kinds, key=rank.__getitem__)
    walk_in = hosted.ok("customer.show", {"customer": main["walk_in_customer"]["id"]}, company=cid)
    assert walk_in["name"] == "Walk-in" == main["walk_in_customer"]["name"]
    for path in ("/", "/_group/customers"):
        assert f'href="/c/{cid}/pos"' in owner.get(f"/c/{cid}{path}").text, path
    page = owner.get(f"/c/{cid}/pos")
    assert page.status_code == 200 and "data-pos-coming" in page.text and 'data-pos-register="Main"' in page.text

    # A second register gets its own walk-in; a name is unique; a deposit account must be a bank or Undeposited Funds.
    till = hosted.ok("pos-register.create", {"name": "Front counter", "receipt_width": 58}, company=cid,
                     headers=REASON)
    assert till["walk_in_customer"]["name"] == "Walk-in (Front counter)" and till["created_walk_in_customer"]
    taken = hosted.call("pos-register.create", {"name": "front COUNTER"}, company=cid, headers=REASON)
    assert taken.json()["code"] == "E_NAME_TAKEN", taken.text
    income = next(a for a in hosted.ok("account.list", {}, company=cid)["items"] if a["type"] == "income")
    wrong = hosted.call("pos-register.update", {"pos_register": "Front counter", "deposit_account": income["id"]},
                        company=cid, headers=REASON)
    assert wrong.json()["code"] == "E_VALIDATION", wrong.text
    changed = hosted.ok("pos-register.update", {"pos_register": "Front counter", "expected_version": 1,
                                                "receipt_message": "Thank you!", "decoder_order": "zxing-wasm"},
                        company=cid, headers=REASON)
    assert changed["version"] == 2 and changed["receipt_message"] == "Thank you!"
    stale = hosted.call("pos-register.update", {"pos_register": "Front counter", "expected_version": 1,
                                                "count_shifts": True}, company=cid, headers=REASON)
    assert stale.json()["code"] == "E_VERSION_CONFLICT", stale.text

    # Off keeps the registers (nothing is held or open yet: tickets are increment 7, shifts increment 10); on again
    # finds Main where it was and makes no second Main or walk-in.
    _pos(hosted, False)
    assert hosted.call("pos-register.list", {}, company=cid).json()["code"] == "E_FEATURE_DISABLED"
    _pos(hosted, True)
    assert [r["id"] for r in _registers(hosted)] == sorted([main["id"], till["id"]],
                                                            key=lambda i: "front counter" if i == till["id"] else "main")
    names = [c["name"] for c in hosted.ok("customer.list", {"query": "Walk-in"}, company=cid)["items"]]
    assert names.count("Walk-in") == 1 and names.count("Walk-in (Front counter)") == 1


def _clerk_effective(hosted, user):
    effective = hosted.ok("membership.effective", {"company": hosted.company_id, "user": user})
    company_caps = pos_presets.company_capabilities(runtime.current_catalog().CATALOG)
    return {p["requirement"]["capability"] for p in effective["permissions"]
            if p["admitted"] and p["requirement"]["capability"] in company_caps}


def test_the_clerk_preset_holds_company_item_and_pos_sell_and_lands_on_the_pos(hosted, live, tmp_path):
    cid = hosted.company_id
    _pos(hosted, True)
    # The preset on the live catalog: readonly, pos.sell, and the pinned denies.
    clerk = pos_presets.preset("pos-clerk")
    assert clerk["role"] == "readonly" and clerk["grants"] == ["pos.sell"] and clerk["home"] == "pos"
    assert clerk["denies"] == CLERK_DENIES
    assert pos_presets.preset("pos-clerk-returns")["grants"] == ["pos.sell", "pos.void", "pos.refund"]

    user = hosted.ok("user.add", {"username": "till", "password": _password("till"), "company": cid,
                                  "role": "readonly"}, headers=REASON)["user_id"]
    owner = _login(hosted, hosted.login, PASSWORD)
    # Users & permissions offers the presets while POS is on; Fill from preset previews, Save applies it and the Home.
    form = owner.get(f"/c/{cid}/users?user=till")
    assert "data-pos-presets" in form.text
    filled = owner.post(f"/c/{cid}/users", headers=H, data={
        "user": "till", "expected_version": str(_membership_version(hosted, user)), "role": "readonly",
        "other_grants": "[]", "other_denies": "[]", "allow_read": "on", "allow_post": "on", "preset": "pos-clerk",
        "action": "preset"})
    assert filled.status_code == 200 and "data-applied-preset" in filled.text, filled.text[-2000:]
    grants = re.search(r'name="other_grants" value="([^"]*)"', filled.text).group(1).replace("&#34;", '"')
    denies = re.search(r'name="other_denies" value="([^"]*)"', filled.text).group(1).replace("&#34;", '"')
    assert json.loads(grants) == ["pos.sell"] and "ledger.read" not in json.loads(denies)
    assert 'name="allow_read" checked' not in filled.text
    saved = owner.post(f"/c/{cid}/users", headers=H, data={
        "user": "till", "expected_version": str(_membership_version(hosted, user)), "role": "readonly",
        "other_grants": grants, "other_denies": denies, "allow_post": "on", "applied_preset": "pos-clerk",
        "action": "save"})
    assert saved.status_code == 200 and "Saved" in saved.text, saved.text[-2000:]
    assert _clerk_effective(hosted, user) == {"company", "item", "pos.sell"}
    home = hosted.ok("user.home.show", {"user": "till"})["home"]
    assert home["page"] == "pos" and home["available"]

    # Criterion 2: signing in lands on the POS, with the minimal header: Home, the company, Point of sale, Items and
    # Sign out; no side menu, finder or account menu.
    fresh = _login(hosted, "till", follow=False)
    landed = fresh.get("/")
    assert landed.status_code == 303 and landed.headers["location"] == f"/c/{cid}/pos"
    page = fresh.get(f"/c/{cid}/pos")
    assert page.status_code == 200 and "app-header-minimal" in page.text
    assert re.findall(r'data-header-link>([^<]+)<', page.text) == ["Point of sale", "Items"]
    assert "data-section=" not in page.text and "data-finder-open" not in page.text and "Switch company" not in page.text
    assert "data-home-button" in page.text
    # Home returns to the POS from any page reached, and Items opens for looking items up.
    assert fresh.get(f"/c/{cid}/_group/items").status_code == 200
    assert fresh.get("/").headers["location"] == f"/c/{cid}/pos"
    # The books are refused: a form, a report URL, and the commands over HTTP (the CLI's remote route) and MCP.
    for path in ("/invoice/new", "/invoice/post", "/report/profit-and-loss", "/customer", "/"):
        status = fresh.get(f"/c/{cid}{path}").status_code
        assert status in (403, 404) if path != "/" else status == 200, (path, status)
    for name, body in (("invoice.post", {"customer": "Walk-in", "lines": [{"item": "Labor", "quantity": "1"}]}),
                       ("report.profit-and-loss", {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
                       ("customer.list", {})):
        refused = fresh.post(f"/companies/{cid}/commands/{name}", headers=H, json=body)
        assert refused.status_code == 403 and refused.json()["code"] == "E_PERMISSION", (name, refused.text)
    assert fresh.post(f"/companies/{cid}/commands/pos-register.list", headers=H, json={}).status_code == 200
    secret = fresh.post("/commands/token.issue", headers={**H, **REASON}, json={"label": "till-mcp"}).json()["secret"]

    async def over_mcp():
        from tests.test_audit_capability_visibility import _mcp
        async with contextlib.AsyncExitStack() as stack:
            call = await _mcp(stack, live, secret, cid, tmp_path)
            ok, reply = await call("report profit-and-loss", {"date_from": "2026-01-01", "date_to": "2026-12-31"})
            assert not ok and "E_PERMISSION" in json.dumps(reply), reply
            ok, reply = await call("pos-register list", {})
            assert ok and reply["items"][0]["name"] == "Main", reply
    anyio.run(over_mcp)

    # A readonly member with no grant holds no POS capability: no entry, and the command refuses.
    hosted.ok("user.add", {"username": "viewer", "password": _password("viewer"), "company": cid,
                           "role": "readonly"}, headers=REASON)
    viewer = _login(hosted, "viewer")
    assert f'href="/c/{cid}/pos"' not in viewer.get(f"/c/{cid}/_group/customers").text
    refused = viewer.post(f"/companies/{cid}/commands/pos-register.list", headers=H, json={})
    assert refused.status_code == 403 and refused.json()["details"]["capability"] == "pos.sell", refused.text


def test_an_agent_holds_no_pos_capability_until_its_principal_grants_it(hosted):
    cid = hosted.company_id
    _pos(hosted, True)
    owner_id = hosted.ok("user.home.show", {})["user_id"]
    agent = make_agent(hosted_call(hosted), "till-bot", principals=owner_id, company=cid, role="admin")
    issued = hosted.ok("token.issue", {"user": agent, "principal": owner_id, "label": "till-bot"})
    api = TestClient(hosted.handle.app)
    bearer = {"Authorization": f"Bearer {issued['secret']}"}
    refused = api.post(f"/companies/{cid}/commands/pos-register.list", headers=bearer, json={})
    assert refused.status_code == 403, refused.text
    details = refused.json()["details"]
    assert details["capability"] == "pos.sell" and details["reason"] == "capability_not_granted"
    # Its role is no grant; its principal's explicit grant is.
    hosted.ok("membership.grant", {"user": agent, "company": cid, "role": "admin", "grants": ["pos.sell"],
                                   "expected_version": _membership_version(hosted, agent)}, headers=REASON)
    issued = hosted.ok("token.issue", {"user": agent, "principal": owner_id, "label": "till-bot-2"})
    bearer = {"Authorization": f"Bearer {issued['secret']}"}
    listed = api.post(f"/companies/{cid}/commands/pos-register.list", headers=bearer, json={})
    assert listed.status_code == 200 and listed.json()["items"][0]["name"] == "Main", listed.text
    layout = api.post(f"/companies/{cid}/commands/pos-register.create", headers={**bearer, **REASON},
                      json={"name": "Agent till"})
    assert layout.status_code == 403 and layout.json()["details"]["capability"] == "pos.layout", layout.text


@pytest.mark.parametrize("missing", ["pos-register list"])
def test_every_pos_command_names_its_pos_capability(missing):
    from bookflow.core import registry
    from bookflow.hub import permission_pos_catalog as layer
    registry.load_all()
    named = {c.name: (c.capability, c.required_role) for c in registry.all_commands() if c.name.startswith("pos")}
    assert named == {name: (cap, threshold) for name, cap, threshold, _ in layer._ADDED}
    assert missing in named
