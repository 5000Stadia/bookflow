"""Row 17 demo seeds: whole-line work billing that ends voided, with exact old balances."""
import hashlib
import tomllib
from collections import defaultdict
from importlib.resources import files

import pytest

from bookflow import BookflowError
from tests.test_reference_year import page_rows, reference_client, reference_template  # noqa: F401
from tests.test_service_sales_demo import COMPANIES


BILLING = {f"{noun} {verb}" for noun in ("estimate", "work-order") for verb in ("invoice", "sales-receipt", "billing")}
OLD_COUNTS = {"seed.toml": 201, "reference.toml": 107}
FIXTURE = files("bookflow.demo").joinpath("example.pdf").read_bytes()

# Independent arithmetic, USD cents. Catalog line: 2 x 100.00 = 20000 net, 8% tax 1600.
# Amount line: net 10.01 = 1001, tax half-even(80.08) = 80. Zero line: 0. Independent trip: 500 + 40.
CATALOG, AMOUNT, TRIP = (20000, 1600), (1001, 80), (500, 40)
FULL = sum(CATALOG) + sum(AMOUNT)            # INV-1, INV-2 as posted, SR-1: 22681
CORRECTED = sum(CATALOG) + sum(TRIP)         # INV-2 after the haul-away line is released: 22140
LABOR = sum(CATALOG)                         # INV-3: 21600
REPAIR = 10000 + 800                         # SR-2: 10800
assert (FULL, CORRECTED, LABOR, REPAIR, sum(AMOUNT)) == (22681, 22140, 21600, 10800, 1081)


@pytest.mark.parametrize("resource", ["seed.toml", "reference.toml"])
def test_manifests_append_all_six_billing_commands_after_the_old_entries(resource):
    commands = tomllib.loads(files("bookflow.demo").joinpath(resource).read_text())["commands"]
    old, new = commands[:OLD_COUNTS[resource]], commands[OLD_COUNTS[resource]:]
    assert not [e for e in old if e["command"] in BILLING]
    assert {e["command"] for e in new if e["command"] in BILLING} == BILLING
    prefix = ("DEMO" if resource == "seed.toml" else "REF") + "-BILL-"
    numbers = [e["input"]["number"] for e in new if "number" in e["input"]]
    assert numbers and all(n.startswith(prefix) for n in numbers)
    assert not [e for e in old if str(e.get("input", {}).get("number", "")).startswith(prefix)]
    for entry in new:
        if entry["command"].split()[1] in ("invoice", "sales-receipt", "post", "update", "void", "create", "work-order"):
            assert entry["reason"].strip(), entry["command"]
    financial = [e for e in new if e["command"] in ("invoice post", "estimate invoice", "estimate sales-receipt",
                                                    "work-order invoice", "work-order sales-receipt") and "capture" in e]
    voided = {e["input"]["invoice"] if "invoice" in e["input"] else e["input"]["sales_receipt"]
              for e in new if e["command"] in ("invoice void", "sales-receipt void")}
    created = {"${" + e["capture"] + ".id}" for e in financial if not e["capture"].endswith(("_retry", "_after_void"))}
    assert created <= voided, created - voided  # every new financial demonstration ends voided
    assert [e["command"] for e in new if e.get("body_fixture")] == ["attachment add"]


@pytest.mark.parametrize("company,prefix", COMPANIES)
def test_billing_chain_lineage_corrections_and_replay(reference_client, company, prefix):
    client, _ = reference_client
    p = prefix + "-BILL-"
    run = lambda name, **data: client.run(name, data, company=company, reason="Work billing demo test")  # noqa: E731
    customer = client.customer.show(customer="Billing Example Customer", company=company)
    assert customer["current_balance"]["minor_units"] == 0
    invoices = {r["number"]: r for r in run("invoice query", customer=customer["id"])["items"]}
    receipts = {r["number"]: r for r in run("sales-receipt query", customer=customer["id"])["items"]}
    assert set(invoices) == {p + "INV-1", p + "INV-2", p + "INV-3", p + "INV-AMT"}
    assert set(receipts) == {p + "SR-1", p + "SR-2"}
    assert {r["status"] for r in (*invoices.values(), *receipts.values())} == {"voided"}
    assert {n: r["total_minor_units"] for n, r in invoices.items()} == {
        p + "INV-1": FULL, p + "INV-2": CORRECTED, p + "INV-3": LABOR, p + "INV-AMT": sum(AMOUNT)}
    assert {n: r["total_minor_units"] for n, r in receipts.items()} == {p + "SR-1": FULL, p + "SR-2": REPAIR}

    estimate = run("estimate query", customer=customer["id"], number=p + "EST-1")["items"][0]
    estimate = run("estimate show", estimate=estimate["id"])
    order = run("work-order query", customer=customer["id"])["items"]
    [order] = order
    order = run("work-order show", work_order=order["id"])
    roots = {l["root_line_id"] for l in estimate["revision"]["lines"]}
    assert {l["root_line_id"] for l in order["revision"]["lines"]} == roots  # inherited roots

    # INV-1: two selected whole lines, retried once, voided, retried again after the void.
    first = run("invoice show", invoice=invoices[p + "INV-1"]["id"])
    assert (first["version"], first["status"]) == (2, "voided")
    lines = first["revision"]["lines"]
    assert [(l["pricing_basis"], l["unit_price"] and l["unit_price"]["minor_units"], l["net_minor_units"], l["tax_minor_units"])
            for l in lines] == [("unit", 10000, *CATALOG), ("amount", None, *AMOUNT)]
    sources = first["revision"]["billing_sources"]
    assert [(s["source_document_id"], s["root_document_id"]) for s in sources] == [(estimate["id"], estimate["id"])] * 2
    assert [s["root_line_id"] for s in sources] == [l["root_line_id"] for l in estimate["revision"]["lines"][:2]]
    assert {s["source_revision_id"] for s in sources} == {run("estimate show", estimate=estimate["id"], revision_number=2)["revision"]["id"]}
    assert {s["document_line_id"] for s in sources} == {l["id"] for l in lines}
    assert [(s["net_minor_units"], s["tax_minor_units"]) for s in sources] == [CATALOG, AMOUNT]
    history = run("invoice history", invoice=first["id"])
    assert [(r["revision_number"], len(r["batches"])) for r in history["items"]] == [(1, 2)]

    # INV-2: rebill of all remaining billable lines, then the haul-away line released by correction.
    second = run("invoice show", invoice=invoices[p + "INV-2"]["id"])
    posted = run("invoice show", invoice=second["id"], revision_number=1)
    assert (second["version"], posted["revision"]["total_minor_units"], second["total_minor_units"]) == (3, FULL, CORRECTED)
    assert [l["net_minor_units"] for l in posted["revision"]["lines"]] == [CATALOG[0], AMOUNT[0], 0]
    assert len(posted["revision"]["billing_sources"]) == 3
    current = second["revision"]
    assert [(l["net_minor_units"], l["description"]) for l in current["lines"]] == [
        (CATALOG[0], "Installation labor at catalog rate"), (0, "Warranty registration at no charge"), (TRIP[0], "Independent trip charge")]
    assert current["lines"][0]["line_id"] == posted["revision"]["lines"][0]["line_id"]
    assert [s["document_line_id"] for s in current["billing_sources"]] == [current["lines"][0]["id"], current["lines"][1]["id"]]
    assert [(r["revision_number"], len(r["batches"])) for r in run("invoice history", invoice=second["id"])["items"]] == [(1, 2), (2, 2)]

    # INV-3 from the work order billed only labor; SR-1 took every remaining billable line including the zero line.
    third = run("invoice show", invoice=invoices[p + "INV-3"]["id"])
    assert [s["source_document_id"] for s in third["revision"]["billing_sources"]] == [order["id"]]
    assert third["revision"]["billing_sources"][0]["root_document_id"] == estimate["id"]
    receipt = run("sales-receipt show", sales_receipt=receipts[p + "SR-1"]["id"])
    assert [s["gross_minor_units"] for s in receipt["revision"]["billing_sources"]] == [sum(CATALOG), sum(AMOUNT), 0]
    assert receipt["revision"]["deposit_account"]["name"] == "Checking" if "deposit_account" in receipt["revision"] else True

    # Ordinary amount-priced sale: exact net, no invented rate.
    amount = run("invoice show", invoice=invoices[p + "INV-AMT"]["id"])
    [line] = amount["revision"]["lines"]
    assert (line["pricing_basis"], line["unit_price"], line["quantity"], line["net_minor_units"], line["tax_minor_units"]) == ("amount", None, "2", *AMOUNT)
    assert amount["revision"]["billing_sources"] == []

    # Billing reads: the estimate now directs billing to its work order; everything is free again.
    est_state = run("estimate billing", estimate=estimate["id"])
    # The estimate read names its work order as the billing owner; can_* describe that owner, and billing
    # the estimate itself is rejected below with E_WORK_DEPENDENCY.
    assert (est_state["owner_id"], est_state["owner_kind"], est_state["can_invoice"], est_state["can_sales_receipt"]) == (order["id"], "work_order", True, True)
    assert [l["state"] for l in est_state["lines"]] == ["unbilled", "unbilled", "no_charge", "nonbillable"]
    assert {d["number"] for d in est_state["destinations"]} == {p + "INV-1", p + "INV-2", p + "SR-1", p + "INV-3"}  # whole root history
    wo_state = run("work-order billing", work_order=order["id"])
    assert (wo_state["owner_id"], wo_state["can_invoice"], wo_state["can_sales_receipt"]) == (order["id"], True, True)
    assert (wo_state["remaining_net_minor_units"], wo_state["remaining_tax_minor_units"]) == (CATALOG[0] + AMOUNT[0], CATALOG[1] + AMOUNT[1])
    assert [l["billed_quantity"] for l in wo_state["lines"]] == ["0"] * 4
    assert {d["number"] for d in wo_state["destinations"]} == {d["number"] for d in est_state["destinations"]}  # same roots, same history
    assert {d["status"] for d in (*est_state["destinations"], *wo_state["destinations"])} == {"voided"}

    # Source history: every conversion appended a same-facts revision and bumped the version.
    est_history = run("estimate history", estimate=estimate["id"])
    assert [(r["revision_number"], r["status"]) for r in est_history["items"]] == [
        (1, "draft"), (2, "accepted"), (3, "accepted"), (4, "accepted"), (5, "accepted")]
    assert estimate["version"] == 5 and order["version"] == 3
    two = run("estimate query", customer=customer["id"], number=p + "EST-2")["items"][0]
    assert two["status"] == "accepted" and run("estimate show", estimate=two["id"])["version"] == 3

    # Durable keys against the seeded state: same intent replays the voided invoice, different intent rejects,
    # and the estimate can no longer offer a competing sale because its work order owns billing.
    accepted_version = 2
    ids = [l["line_id"] for l in estimate["revision"]["lines"][:2]]
    replay = run("estimate invoice", estimate=estimate["id"], expected_version=accepted_version, conversion_key=p + "EST-1 first invoice",
                 date="2026-09-16", number=p + "INV-1", line_ids=ids)
    assert replay["idempotent_replay"] and replay["id"] == first["id"] and replay["status"] == "voided"
    with pytest.raises(BookflowError) as caught:
        run("estimate invoice", estimate=estimate["id"], expected_version=accepted_version, conversion_key=p + "EST-1 first invoice",
            date="2026-09-17", number=p + "INV-1", line_ids=ids)
    assert caught.value.code == "E_CONVERSION_KEY_REUSED"
    with pytest.raises(BookflowError) as caught:
        run("estimate invoice", estimate=estimate["id"], expected_version=estimate["version"], conversion_key=p + "EST-1 competing",
            date="2026-09-22")
    assert caught.value.code == "E_WORK_DEPENDENCY"
    with pytest.raises(BookflowError) as caught:
        run("work-order invoice", work_order=order["id"], expected_version=order["version"], conversion_key=p + "WO-1 zero only",
            date="2026-09-22", line_ids=[order["revision"]["lines"][2]["line_id"]])
    assert caught.value.code == "E_WORK_DEPENDENCY"  # zero-only selection has no charge

    # Files and notes stay on the estimate and are reachable from every sale through its billing sources.
    notes = run("note list", record_type="work_document", record_id=estimate["id"])
    assert notes["count"] == 1 and "attached to this estimate" in notes["items"][0]["body"]
    attachments = run("attachment list", record_type="work_document", record_id=estimate["id"])
    assert attachments["count"] == 1
    attachment = attachments["items"][0]["attachment"]
    assert (attachment["sha256"], attachment["size_bytes"]) == (hashlib.sha256(FIXTURE).hexdigest(), len(FIXTURE))
    for sale in (first, second, receipt, third):
        root = sale["revision"]["billing_sources"][0]["root_document_id"]
        assert root == estimate["id"] and run("attachment list", record_type="work_document", record_id=root)["count"] == 1
    assert run("attachment list", record_type="work_document", record_id=order["id"])["count"] == 0  # bytes never copied
    snapshot = first["revision"]["billing_sources"][0]["facts_snapshot"]
    assert snapshot["source_number"] == p + "EST-1" and snapshot["title"] == "Water heater replacement"

    # Row 16 examples are untouched: the commercial customer's documents and histories are exactly as before.
    commercial = client.customer.show(customer="Commercial Example Customer", company=company)
    assert commercial["current_balance"]["minor_units"] == 12800
    assert {r["number"] for r in run("estimate query", customer=commercial["id"], active=None)["items"]} == {
        prefix + "-WORK-EST-1A", prefix + "-WORK-EST-1B", prefix + "-WORK-EST-2", prefix + "-WORK-EST-3"}
    assert run("invoice query", customer=commercial["id"])["count"] == 2


@pytest.mark.parametrize("company,prefix", COMPANIES)
def test_voided_billing_demos_leave_every_old_balance_and_zero_net_effect(reference_client, company, prefix):
    client, _ = reference_client
    checking, trial, journals, profit, equity = ((624895, 690195, 10, 133095, 633095) if prefix == "DEMO"
                                                 else (7267800, 8030600, 36, 6439000, 7439000))
    assert client.account.show(account="Checking", company=company)["balance"]["minor_units"] == checking
    assert client.account.show(account="Accounts Receivable", company=company)["balance"]["minor_units"] == 12800
    assert client.account.show(account="Sales Tax Payable", company=company)["balance"]["minor_units"] == 1600
    totals = client.report.trial_balance(company=company, date_to="2026-12-31", limit=200)["totals"]
    assert totals["debit"]["minor_units"] == totals["credit"]["minor_units"] == trial
    statement = client.report.profit_and_loss(company=company, date_from="2026-01-01", date_to="2026-12-31")
    sheet = client.report.balance_sheet(company=company, date_to="2026-12-31")
    assert statement["totals"]["net_income"]["minor_units"] == profit
    assert (sheet["totals"]["total_equity"]["minor_units"], sheet["totals"]["difference"]["minor_units"]) == (equity, 0)
    assert client.journal.query(company=company, limit=200)["count"] == journals
    rows, _ = page_rows(client.report.general_ledger, company=company, date_from="2026-01-01", date_to="2026-12-31", limit=200)
    net = defaultdict(int)
    gross = defaultdict(int)
    for row in rows:
        if row["kind"] == "posting" and row["transaction_number"].startswith(prefix + "-BILL-"):
            net[row["transaction_number"], row["current_account_label"]] += row["debit"]["minor_units"] - row["credit"]["minor_units"]
            gross[row["transaction_number"]] += row["debit"]["minor_units"]
    assert net and all(value == 0 for value in net.values()), dict(net)
    # Each voided demonstration posted and reversed exactly its gross once (INV-2 twice: original and replacement).
    p = prefix + "-BILL-"
    assert gross == {p + "INV-1": 2 * FULL, p + "INV-2": 2 * FULL + 2 * CORRECTED, p + "INV-3": 2 * LABOR,
                     p + "INV-AMT": 2 * sum(AMOUNT), p + "SR-1": 2 * FULL, p + "SR-2": 2 * REPAIR}
    # Read-only previews change nothing.
    customer = client.customer.show(customer="Billing Example Customer", company=company)
    order = client.run("work-order query", {"customer": customer["id"]}, company=company)["items"][0]
    preview = client.run("work-order invoice", dict(work_order=order["id"], expected_version=order["version"],
        conversion_key=p + "WO-1 preview only", date="2026-09-22"), company=company, reason="Work billing demo test", dry_run=True)
    assert preview["dry_run"] and preview["total_minor_units"] == FULL and len(preview["revision"]["billing_sources"]) == 3
    assert client.run("work-order billing", {"work_order": order["id"]}, company=company)["remaining_net_minor_units"] == CATALOG[0] + AMOUNT[0]
    assert client.customer.show(customer="Billing Example Customer", company=company)["current_balance"]["minor_units"] == 0
