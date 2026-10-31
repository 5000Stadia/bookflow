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
CLERK_DENIES = ['account', 'activity', 'attachment', 'audit', 'automation', 'bank-feed', 'bulk-edit', 'chart', 'class',
                'custom-field', 'customer', 'customer-message', 'customer-type', 'customer-work', 'directive',
                'employee', 'import', 'item-category', 'job-type', 'ledger.read', 'note', 'other-name',
                'payment-method', 'price-level', 'profile', 'reports', 'sales-rep', 'sales-tax-code', 'ship-method',
                'term', 'unit-of-measure', 'vendor', 'vendor-type']

# Every company command the clerk is admitted to on the current catalog, pinned: a command a later change lets a clerk
# reach fails here first. Shipments, their returns and pick lists name customers, so they need `customer` (pos-v1).
CLERK_COMMANDS = ['barcode lookup', 'bin list', 'bin show', 'company show',
                  'cycle-class list', 'cycle-class show', 'cycle-count due', 'inventory-count query', 'inventory-count sheet', 'inventory-count show',
                  'inventory-site list', 'inventory-site show', 'inventory-transfer history', 'inventory-transfer query',
                  'inventory-transfer show', 'item list', 'item picture get', 'item query', 'item query children',
                  'item query options', 'item show', 'lot list', 'lot show', 'pos customer add', 'pos customer search',
                  'pos receipt show', 'pos sale complete', 'pos sale preview', 'pos shift close', 'pos shift open',
                  'pos ticket discard', 'pos ticket hold', 'pos ticket list', 'pos ticket resume', 'pos ticket take-over', 'pos-box list', 'pos-box picture get', 'pos-register list', 'pos-register show',
                  'report pos-day', 'serial-number list', 'serial-number show']


def _admitted_commands(preset):
    """The company commands a member with this preset is admitted to, from the live catalog alone."""
    from bookflow.hub.permission_catalog import Requirement
    catalog = runtime.current_catalog().CATALOG
    defaults = {(d.role, d.requirement) for d in catalog.defaults}
    grants, denies = set(preset["grants"]), set(preset["denies"])

    def ok(r):
        return (r.threshold in ("authenticated", "member") and r.capability not in denies
                and ((preset["role"], r) in defaults or r.capability in grants))
    return {c.name for c in catalog.commands if c.routed_scope == "company"
            and all(ok(r) for r in (Requirement(c.capability, c.threshold), *c.resources))}


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
        assert refused.status_code == 400, refused.text
        assert refused.json()["code"] == "E_FEATURE_DISABLED", refused.text
        assert refused.json()["details"] == {"feature": "pos", "setting": "pos_enabled",
                                             "enable_command": "company update"}
    owner = _login(hosted, hosted.login, PASSWORD)
    for path in ("/", "/_group/customers", "/_all"):
        assert f'href="/c/{cid}/pos"' not in owner.get(f"/c/{cid}{path}").text, path
    assert not [item["href"] for item in owner.get(f"/c/{cid}/_finder").json()["items"]
                if re.search(r"/pos(-register)?(/|\?|$)", item["href"])]
    off = owner.get(f"/c/{cid}/pos")
    assert off.status_code == 400 and "Point of sale is off for this company" in off.text
    refused = hosted.call("user.home.set", {"page": "pos", "company": cid}, headers=REASON)
    assert refused.json()["code"] == "E_VALIDATION", refused.text

    # A customer already named Walk-in keeps its name: Main's walk-in becomes "Walk-in 2".
    plain = hosted.ok("customer.create", {"name": "Walk-in"}, company=cid, headers=REASON)["id"]

    # On: register Main with its defaults, and a walk-in customer made for it; the update says so.
    turned = _pos(hosted, True)
    assert any("Made register Main and customer Walk-in 2" in w for w in turned["warnings"]), turned
    (main,) = _registers(hosted)
    assert main["name"] == "Main" and main["receipt_width"] == 80 and main["count_shifts"] is False
    assert main["deposit_account"]["name"] == "Undeposited Funds"
    kinds = [m["kind"] for m in main["payment_methods"]]
    rank = {"cash": 0, "credit_card": 1, "debit_card": 1, "check": 2}
    assert kinds and kinds[0] == "cash" and kinds == sorted(kinds, key=rank.__getitem__)
    walk_in = hosted.ok("customer.show", {"customer": main["walk_in_customer"]["id"]}, company=cid)
    assert walk_in["name"] == "Walk-in 2" == main["walk_in_customer"]["name"]
    for path in ("/", "/_group/customers"):
        assert f'href="/c/{cid}/pos"' in owner.get(f"/c/{cid}{path}").text, path
    page = owner.get(f"/c/{cid}/pos")
    assert page.status_code == 200 and "data-pos-boxes" in page.text and 'id="pos-data"' in page.text

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
    assert sorted(names) == ["Walk-in", "Walk-in (Front counter)", "Walk-in 2"]

    # What a register points at cannot be deactivated or merged away; the refusal names the register. The walk-in
    # customer is never merged, and a bulk edit leaves it out.
    for name, body in (("customer.deactivate", {"customer": main["walk_in_customer"]["id"]}),
                       ("payment-method.deactivate", {"payment_method": main["payment_methods"][0]["id"]}),
                       ("customer.merge", {"merged": plain, "into": main["walk_in_customer"]["id"]})):
        blocked = hosted.call(name, body, company=cid, headers=REASON)
        assert blocked.json()["code"] == "E_RECORD_IN_USE", (name, blocked.text)
        assert "Main" in blocked.json()["message"] or blocked.json()["details"].get("reason") == "pos_walk_in", blocked.text
    grid = hosted.ok("bulk-edit.grid", {"kind": "customer", "query": "Walk-in"}, company=cid)
    assert plain in json.dumps(grid) and main["walk_in_customer"]["id"] not in json.dumps(grid)
    assert till["walk_in_customer"]["id"] not in json.dumps(grid)


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
    assert clerk["role"] == "readonly" and clerk["grants"] == ["pos.sell", "preset.pos-clerk"]
    assert clerk["home"] == "pos"
    assert clerk["denies"] == CLERK_DENIES
    assert pos_presets.preset("pos-clerk-returns")["grants"] == ["pos.sell", "pos.void", "pos.refund",
                                                                 "preset.pos-clerk-returns"]
    assert runtime.current_catalog().CATALOG.version == "pos-day-v1"  # on pos-layout-v1
    assert sorted(_admitted_commands(clerk)) == CLERK_COMMANDS
    assert "pos sale void" not in _admitted_commands(clerk)
    assert _admitted_commands(pos_presets.preset("pos-clerk-returns")) == set(CLERK_COMMANDS) | {
        "pos sale void", "pos refund", "pos refund lines", "pos refund show", "pos refund receipt"}
    assert not any(name.startswith("bank-feed") for name in _admitted_commands(clerk))

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
    assert json.loads(grants) == ["pos.sell", "preset.pos-clerk"] and "ledger.read" not in json.loads(denies)
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
    assert all(marker not in page.text for marker in ("data-pos-edit", "data-pos-style", "data-pos-layout-form"))
    assert re.findall(r'data-header-link>([^<]+)<', page.text) == ["Point of sale", "Items"]
    assert "data-section=" not in page.text and "data-finder-open" not in page.text and "Switch company" not in page.text
    assert "data-home-button" in page.text
    # Home returns to the POS from any page reached, and Items opens for looking items up.
    assert fresh.get(f"/c/{cid}/_group/items").status_code == 200
    assert fresh.get("/").headers["location"] == f"/c/{cid}/pos"
    # The books are refused: a form, a report URL, and the commands over HTTP (the CLI's remote route) and MCP.
    # Each page exists (the owner opens it) and the clerk is refused it: refused, not missing.
    # /invoice/new is not a page (the invoice form is /invoice/post): the owner is told so, while the clerk is refused
    # the invoice noun before anything is looked up.
    assert owner.get(f"/c/{cid}/invoice/new").status_code == 404
    assert fresh.get(f"/c/{cid}/invoice/new").status_code == 403
    for path in ("/invoice/post", "/report/profit-and-loss", "/customer", "/shipments",
                 "/_all", "/find"):
        assert owner.get(f"/c/{cid}{path}").status_code == 200, path
        assert fresh.get(f"/c/{cid}{path}").status_code == 403, path
    assert fresh.get(f"/c/{cid}/").status_code == 200
    # Items: the item page opens with no activity panel, and its costs and vendors are not shown on any surface.
    item = next(i for i in hosted.ok("item.list", {}, company=cid)["items"]
                if i["type"] == "inventory_part" and i["cost"] and i["preferred_vendor_id"])
    record = fresh.get(f"/c/{cid}/item/{item['id']}")
    assert record.status_code == 200, record.text[-1500:]
    seen = fresh.post(f"/companies/{cid}/commands/item.show", headers=H, json={"item": item["id"]}).json()
    assert seen["name"] == item["name"] and seen["price"] == item["price"]
    assert seen["cost"] is None and seen["average_cost"] is None and seen["inventory_value"] is None
    assert seen["preferred_vendor_id"] is None and seen["vendor_profiles"] == []
    queried = fresh.post(f"/companies/{cid}/commands/item.query", headers=H,
                         json={"columns": ["full_name", "cost", "preferred_vendor"], "query": item["name"]}).json()
    row = next(r for r in queried["items"] if r["id"] == item["id"])
    assert row.get("cost") is None and row.get("preferred_vendor") is None, row
    items_page = fresh.get(f"/c/{cid}/_group/items").text
    assert f'href="/c/{cid}/shipments"' not in items_page and f'href="/c/{cid}/inventory/pick-lists"' not in items_page
    for name, body in (("invoice.post", {"customer": "Walk-in", "lines": [{"item": "Labor", "quantity": "1"}]}),
                       ("report.profit-and-loss", {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
                       ("customer.list", {})):
        refused = fresh.post(f"/companies/{cid}/commands/{name}", headers=H, json=body)
        assert refused.status_code == 403 and refused.json()["code"] == "E_PERMISSION", (name, refused.text)
    assert fresh.post(f"/companies/{cid}/commands/pos-register.list", headers=H, json={}).status_code == 200
    layout = fresh.post(f"/companies/{cid}/commands/pos-box.list", headers=H, json={})
    assert layout.status_code == 200, layout.text
    layout = layout.json()
    box = next(b['id'] for b in layout['items'] if b['kind'] == 'group')
    layout_writes = {
        'create': {'kind': 'group', 'label': 'Not allowed'},
        'update': {'pos_box': box, 'label': 'Not allowed'},
        'move': {'pos_box': box, 'position': 0},
        'remove': {'pos_box': box},
        'seed': {'mode': 'empty'},
        'view': {'style': 'list'},
    }
    for verb, body in layout_writes.items():
        refused = fresh.post(f"/companies/{cid}/commands/pos-box.{verb}", headers={**H, **REASON},
                             json={**body, 'expected_version': layout['version']})
        assert refused.status_code == 403 and refused.json()['code'] == 'E_PERMISSION', (verb, refused.text)
    from bookflow.core.transfer_protocol import encode_input
    from tests.test_item_pictures import png
    refused = fresh.post(f"/companies/{cid}/transfers/pos-box.picture.set", headers={**H, **REASON, 'Content-Type': 'application/octet-stream',
        'X-Bookflow-Input': encode_input({'pos_box': box, 'expected_version': layout['version'],
                                        'original_filename': 'forbidden.png'})}, content=png())
    assert refused.status_code == 403 and refused.json()['code'] == 'E_PERMISSION', refused.text
    # The bespoke editor route has the same command-boundary refusal if typed directly.
    refused = fresh.post(f"/c/{cid}/pos/layout", headers=H,
                         data={'command': 'view', 'body': json.dumps({'style': 'list', 'expected_version': layout['version']})})
    assert refused.status_code == 400 and refused.json()['code'] == 'E_PERMISSION', refused.text
    # POS off: the seller keeps the minimal header, now with Items alone.
    _pos(hosted, False)
    off = fresh.get(f"/c/{cid}/_group/items")
    assert "app-header-minimal" in off.text and re.findall(r'data-header-link>([^<]+)<', off.text) == ["Items"]
    _pos(hosted, True)
    secret = fresh.post("/commands/token.issue", headers={**H, **REASON}, json={"label": "till-mcp"}).json()["secret"]

    async def over_mcp():
        from tests.test_audit_capability_visibility import _mcp
        async with contextlib.AsyncExitStack() as stack:
            call = await _mcp(stack, live, secret, cid, tmp_path)
            ok, reply = await call("report profit-and-loss", {"date_from": "2026-01-01", "date_to": "2026-12-31"})
            assert not ok and "E_PERMISSION" in json.dumps(reply), reply
            ok, reply = await call("pos-register list", {})
            assert ok and reply["items"][0]["name"] == "Main", reply
            ok, reply = await call("pos-box list", {})
            assert ok and reply['version'] == layout['version'], reply
            for verb, body in layout_writes.items():
                ok, reply = await call('pos-box ' + verb, {**body, 'expected_version': layout['version']})
                assert not ok and 'E_PERMISSION' in json.dumps(reply), (verb, reply)
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


def test_every_pos_command_names_its_pos_capability():
    from bookflow.core import registry
    from bookflow.hub import permission_pos_catalog as layer
    registry.load_all()
    named = {c.name: (c.capability, c.required_role) for c in registry.all_commands() if c.name.startswith("pos")}
    from bookflow.hub import permission_pos_sale_catalog as sale_layer
    expected = {name: (cap, threshold) for name, cap, threshold, _ in layer._ADDED}
    expected.update({name: ('pos.sell', 'member') for name, _, _ in sale_layer._ADDED})   # increment 5
    from bookflow.hub import permission_pos_layout_catalog as layout_layer
    expected.update({name: (cap, threshold) for name, _, cap, threshold, _ in layout_layer._ADDED})
    expected.update({f'pos ticket {verb}': ('pos.sell', 'member') for verb in ('hold', 'list', 'resume', 'take-over', 'discard')})
    expected['pos sale void'] = ('pos.void', 'member')
    expected['pos receipt show'] = ('pos.sell', 'member')
    expected.update({name: ('pos.refund', 'member') for name in (
        'pos refund', 'pos refund lines', 'pos refund show', 'pos refund receipt')})
    expected.update({'pos shift open': ('pos.sell', 'member'), 'pos shift close': ('pos.sell', 'member'),
                     'pos deposit prepare': ('ledger.post', 'standard')})
    assert named == expected
    # The POS layer is decided by capability, not noun: the gate and the switch find each command by its capability.
    from bookflow.adapters.workbench import home as Home
    from bookflow.hub import capabilities
    assert all(capabilities.capability_for(name) == cap for name, (cap, _) in named.items())
    assert all(Home.is_pos(registry.get(name)) for name in named if name != 'pos deposit prepare') and not Home.is_pos(registry.get("item show"))
    # The shipment, return and pick-list reads need `customer` too.
    for name, needed in layer.EXTRA_READS.items():
        assert (needed.capability, needed.threshold) in registry.get(name).resource_requirements, name


def test_new_capabilities_a_later_catalog_gives_readonly_are_found():
    """A layer giving `readonly` a new company capability (here a made-up `widget`, read by a company action) is what
    `permission activate` extends a preset clerk's denies with; nothing else is."""
    from dataclasses import replace
    from bookflow.hub import permission_catalog as c
    old = runtime.current_catalog().CATALOG
    widget = c.Requirement("widget", "member")
    new = replace(old, capabilities=(*old.capabilities, c.CapabilitySpec("widget", ("member",), ("member",))),
                  defaults=(*old.defaults, c.DefaultEntry("readonly", widget)),
                  company_actions=(*old.company_actions, c.CompanyAction("widget show", (widget,), True, ())))
    assert pos_presets.new_capabilities(old, new) == {"widget"}
    assert pos_presets.new_capabilities(old, old) == set()


def test_permission_activate_keeps_a_preset_clerk_narrow(hosted, monkeypatch):
    cid = hosted.company_id
    _pos(hosted, True)
    clerk = pos_presets.preset("pos-clerk")
    user = hosted.ok("user.add", {"username": "till", "password": _password("till"), "company": cid,
                                  "role": "readonly"}, headers=REASON)["user_id"]
    # As if `vendor` were new to `readonly` in the catalog being activated: the clerk saved before it lacks the deny.
    hosted.ok("membership.grant", {"user": user, "company": cid, "role": "readonly", "grants": clerk["grants"],
                                   "denies": [d for d in clerk["denies"] if d != "vendor"],
                                   "expected_version": _membership_version(hosted, user)}, headers=REASON)
    hosted.ok("user.add", {"username": "plain", "password": _password("plain"), "company": cid, "role": "readonly"},
              headers=REASON)
    monkeypatch.setattr(pos_presets, "new_capabilities", lambda old, new: {"vendor"})
    state = hosted.ok("permission.show", {})
    body = {"expected_generation": state["generation"], "expected_catalog_sha256": state["catalog_sha256"]}
    activated = hosted.ok("permission.activate", body, headers=REASON)
    assert activated["presets_extended"] == [{"user": "till", "scope_type": "company", "scope_id": cid,
                                              "preset": "pos-clerk", "added": ["vendor"]}], activated
    effective = hosted.ok("membership.effective", {"company": cid, "user": user})
    assert not any(p["admitted"] for p in effective["permissions"] if p["requirement"]["capability"] == "vendor")
    # The plain readonly member, made from no preset, is untouched.
    plain = hosted.ok("membership.effective", {"company": cid, "user": "plain"})
    assert any(p["admitted"] for p in plain["permissions"] if p["requirement"]["capability"] == "vendor")


def test_a_root_activated_before_pos_v1_says_to_activate(root, monkeypatch):
    """On a root whose permission rules predate pos-v1, POS answers a clear refusal naming `permission activate`, on
    the commands, the POS page and `membership grant --grants pos.sell`; activating fixes it."""
    import socket

    import bookflow
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.config import os_login
    from bookflow.core.context import client_version
    from bookflow.hub import permission_catalog as c
    from tests.conftest import make_legacy
    from tests.test_row3_host import Hosted
    make_legacy(root)
    lib = bookflow.connect(data_root=str(root))
    with monkeypatch.context() as previous_build:
        previous_build.setattr(runtime, "current_catalog", lambda: runtime.known_catalog(c.AUTOMATION_POLICY_VERSION))
        state = lib.permission.show()
        lib.permission.activate(expected_generation=state["generation"], expected_catalog_sha256=state["catalog_sha256"])
    company = lib.company.list()["items"][0]["company_id"]
    login = os_login()
    lib.run("user set-password", {"username": login, "password": PASSWORD})
    issued = lib.token.issue(label="installer")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    handle = start_serving(root, client_version(), bind=f"127.0.0.1:{port}", secure_cookies=False)
    try:
        old = Hosted(handle, root, login, company, issued, "", {})
        old.ok("company.update", {"pos_enabled": True, "expected_version": old.info()["info_version"]},
               company=company, headers=REASON)
        refused = old.call("pos-register.list", {}, company=company)
        assert refused.status_code == 403 and refused.json()["details"]["reason"] == "catalog_upgrade_required"
        assert "permission activate" in refused.json()["message"], refused.text
        page = _login(old, login, PASSWORD).get(f"/c/{company}/pos")
        assert page.status_code == 403 and "permission activate" in page.text
        added = old.ok("user.add", {"username": "cashier", "password": _password("cashier"), "company": company,
                                    "role": "readonly"}, headers=REASON)["user_id"]
        granted = old.call("membership.grant", {"user": added, "company": company, "role": "readonly",
                                                "grants": ["pos.sell"],
                                                "expected_version": _membership_version(old, added)}, headers=REASON)
        assert granted.json()["details"].get("reason") == "catalog_upgrade_required", granted.text
        assert "permission activate" in granted.json()["message"]
        state = old.ok("permission.show", {})
        old.ok("permission.activate", {"expected_generation": state["generation"],
                                       "expected_catalog_sha256": state["catalog_sha256"]}, headers=REASON)
        assert old.ok("pos-register.list", {}, company=company)["items"][0]["name"] == "Main"
    finally:
        handle.stop()
