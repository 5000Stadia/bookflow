"""Row 18 demo seeds: partial quantity, net-amount and percentage billing that ends voided."""
import hashlib
import tomllib
from collections import defaultdict
from fractions import Fraction
from importlib.resources import files
from math import lcm

import pytest

from bookflow import BookflowError
from tests.test_reference_year import demo_runner, page_rows, reference_client, reference_template  # noqa: F401
from tests.test_service_sales_demo import COMPANIES


OLD_COUNTS = {"seed.toml": 242, "reference.toml": 148}
FIXTURE = files("bookflow.demo").joinpath("example.pdf").read_bytes()
FINANCIAL = {"estimate invoice", "estimate sales-receipt", "work-order invoice", "work-order sales-receipt", "invoice post", "sales-receipt post"}


# Independent interval arithmetic (spec 18): D = lcm(Q, max(N, 1), 1e8); [a, b) owns E(N*b/D) - E(N*a/D).
def even(n, d):
    q, r = divmod(n, d)
    return q + (2 * r > d or (2 * r == d and q % 2 == 1))


def portion(total, a, b, d):
    return even(total * b, d) - even(total * a, d)


def tax(net):
    return even(net * 8, 100)


# Kitchen remodel quote: L0 4 units at 100.00 (N 40000), L1 3 units for 0.99 (N 99), L2 one microunit for 1.00 (N 100).
D0, D1, D2 = lcm(4_000_000, 40000, 10**8), lcm(3_000_000, 99, 10**8), lcm(1, 100, 10**8)
INV1 = portion(40000, 0, D0 // 4, D0)                               # one unit of L0
INV2 = portion(100, 0, 40 * (D2 // 100), D2)                        # 40 cents of L2
INV3 = portion(99, 0, D1 // 2, D1)                                  # 50 percent of L1
INV4 = [portion(40000, D0 // 4, D0 // 2, D0), portion(99, D1 // 2, 3 * D1 // 4, D1), portion(100, 40 * (D2 // 100), 40 * (D2 // 100) + D2 // 4, D2)]
INV5 = [portion(40000, D0 // 2, D0, D0), portion(99, 3 * D1 // 4, D1, D1), portion(100, 40 * (D2 // 100) + D2 // 4, D2, D2)]
EXTRA = 500
assert (INV1, INV2, INV3, INV4, INV5) == (10000, 40, 50, [10000, 24, 25], [20000, 25, 35])
assert INV1 + INV2 + INV3 + sum(INV4) + sum(INV5) == 40000 + 99 + 100     # full coverage telescopes to the quoted net
SR1, INV7 = 5000, 15000                                                    # half a day and the remaining day and a half at 100.00


@pytest.mark.parametrize("resource", ["seed.toml", "reference.toml"])
def test_manifests_append_progress_examples_after_the_old_entries(resource):
    commands = tomllib.loads(files("bookflow.demo").joinpath(resource).read_text())["commands"]
    # Row18 owns exactly41 appended commands; later examples have their own witness.
    old, new = commands[:OLD_COUNTS[resource]], commands[OLD_COUNTS[resource]:OLD_COUNTS[resource]+41]
    prefix = ("DEMO" if resource == "seed.toml" else "REF") + "-PROG-"
    assert not [e for e in old if str(e.get("input", {}).get("number", "")).startswith(prefix)]
    assert not [e for e in old if e["command"] in FINANCIAL and ("selections" in e["input"] or "percent" in e["input"])]
    conversions = [e for e in new if e["command"] in FINANCIAL]
    modes = [("selections", "quantity"), ("selections", "net_amount"), ("selections", "percent"), ("selections", "rebill_allocation_id"), ("percent", None)]
    for family, member in modes:
        assert any(family in e["input"] and (member is None or member in e["input"][family][0]) for e in conversions), (family, member)
    assert [e for e in conversions if "selections" not in e["input"] and "percent" not in e["input"]]   # remaining-work conversions
    assert {e["command"] for e in new if e["command"].split()[1] == "billing"} == {"estimate billing", "work-order billing"}
    created = {"${" + e["capture"] + ".id}" for e in conversions if "capture" in e and not e["capture"].endswith("_retry")}
    voided = {e["input"]["invoice"] if "invoice" in e["input"] else e["input"]["sales_receipt"]
              for e in new if e["command"] in ("invoice void", "sales-receipt void")}
    assert created <= voided, created - voided
    for entry in new:
        if entry["command"].split()[1] in ("invoice", "sales-receipt", "void", "update", "create", "work-order"):
            assert entry["reason"].strip(), entry["command"]
    assert [e["command"] for e in new if e.get("body_fixture")] == ["attachment add"]


@pytest.mark.parametrize("company,prefix", COMPANIES)
def test_progress_chain_exact_installments_rebill_correction_and_lineage(reference_client, company, prefix):
    client, _ = reference_client
    p = prefix + "-PROG-"
    run = demo_runner(client, company, "Progress billing demo test")
    customer = client.customer.show(customer="Progress Example Customer", company=company)
    assert customer["current_balance"]["minor_units"] == 0
    invoices = {r["number"]: r for r in run("invoice query", customer=customer["id"])["items"]}
    receipts = {r["number"]: r for r in run("sales-receipt query", customer=customer["id"])["items"]}
    assert set(invoices) == {p + f"INV-{n}" for n in (1, 2, 3, 4, 5, 6, 7)} and set(receipts) == {p + "SR-1"}
    assert {r["status"] for r in (*invoices.values(), *receipts.values())} == {"voided"}
    assert {n: (r["subtotal_minor_units"], r["tax_minor_units"]) for n, r in invoices.items()} == {
        p + "INV-1": (INV1, tax(INV1)), p + "INV-2": (INV2, tax(INV2)), p + "INV-3": (INV3, tax(INV3)),
        p + "INV-4": (sum(INV4) + EXTRA, sum(map(tax, INV4)) + tax(EXTRA)), p + "INV-5": (sum(INV5), sum(map(tax, INV5))),
        p + "INV-6": (INV1, tax(INV1)), p + "INV-7": (INV7, tax(INV7))}
    assert receipts[p + "SR-1"]["total_minor_units"] == SR1 + tax(SR1)

    estimate = run("estimate show", estimate=run("estimate query", customer=customer["id"], number=p + "EST-1")["items"][0]["id"])
    lines = estimate["revision"]["lines"]
    assert [(l["quantity"], l["net"]["minor_units"]) for l in lines] == [("4", 40000), ("3", 99), ("0.000001", 100)]
    roots = [l["root_line_id"] for l in lines]

    def sources(number, revision_number=None):
        args = dict(invoice=invoices[number]["id"])
        if revision_number:
            args["revision_number"] = revision_number
        return run("invoice show", **args)

    # Quantity, net-amount and percent installments carry exact allocated facts and truthful quantities.
    one = sources(p + "INV-1")
    [line] = one["revision"]["lines"]
    assert (line["pricing_basis"], line["quantity"], line["quoted_quantity"], line["unit_price"]["minor_units"]) == ("allocated", "1", "4", 10000)
    [proof] = one["revision"]["billing_sources"]
    assert proof["allocation_version"] == 3 and proof["root_line_id"] == roots[0]
    assert proof["allocation_proof"]["spans"] == [{"start": "0", "end": str(D0 // 4)}] and proof["allocation_proof"]["denominator"] == str(D0)
    forty = sources(p + "INV-2")
    [line] = forty["revision"]["lines"]
    assert (line["net_minor_units"], line["quantity"], line["quantity_microunits"], line["quoted_quantity"], line["unit_price"]) == (40, "1/2500000", None, "0.000001", None)
    assert line["quantity_fraction"] == {"numerator": "1", "denominator": "2500000"}
    half = sources(p + "INV-3")
    [line] = half["revision"]["lines"]
    assert (line["net_minor_units"], line["quantity"], line["quoted_quantity"]) == (50, "1.5", "3")
    mixed = sources(p + "INV-4", 1)
    assert [(l["net_minor_units"], l["quantity"]) for l in mixed["revision"]["lines"]] == [(INV4[0], "1"), (INV4[1], "0.75"), (INV4[2], "1/4000000")]
    assert [s["root_line_id"] for s in mixed["revision"]["billing_sources"]] == roots

    # Correction retained every proof and added one unlinked line; the source consumption did not move.
    corrected = sources(p + "INV-4")
    assert corrected["version"] == 3 and len(corrected["revision"]["lines"]) == 4
    assert [l["line_id"] for l in corrected["revision"]["lines"][:3]] == [l["line_id"] for l in mixed["revision"]["lines"]]
    assert [l["item_snapshot"] for l in corrected["revision"]["lines"][:3]] == [l["item_snapshot"] for l in mixed["revision"]["lines"]]
    extra = corrected["revision"]["lines"][3]
    assert (extra["pricing_basis"], extra["net_minor_units"], extra["description"]) == ("amount", EXTRA, "Debris disposal, not part of the quote")
    assert [s["allocation_proof"]["spans"] for s in corrected["revision"]["billing_sources"]] == [s["allocation_proof"]["spans"] for s in mixed["revision"]["billing_sources"]]
    assert [(r["revision_number"], len(r["batches"])) for r in run("invoice history", invoice=corrected["id"])["items"]] == [(1, 2), (2, 2)]

    # The rebill reproduced the voided first installment's exact spans under a new key.
    rebill = sources(p + "INV-6")
    assert rebill["revision"]["billing_sources"][0]["allocation_proof"]["spans"] == proof["allocation_proof"]["spans"]
    assert rebill["revision"]["lines"][0]["quantity"] == "1" and rebill["subtotal_minor_units"] == INV1
    remaining = sources(p + "INV-5")
    assert [(l["net_minor_units"], l["quantity"]) for l in remaining["revision"]["lines"]] == [(INV5[0], "2"), (INV5[1], "0.75"), (INV5[2], "7/20000000")]
    assert [s["allocation_proof"]["spans"][0]["end"] for s in remaining["revision"]["billing_sources"]] == [str(D0), str(D1), str(D2)]

    # Everything is voided, so the billing read shows the whole quote free again with the full destination history.
    state = run("estimate billing", estimate=estimate["id"])
    assert (state["remaining_net_minor_units"], state["can_invoice"], state["source_version"]) == (40199, True, estimate["version"])
    assert [(l["state"], l["billed_quantity"], l["billed_scope_percent"]) for l in state["lines"]] == [("unbilled", "0", "0")] * 3
    assert {d["number"] for d in state["destinations"]} == {p + f"INV-{n}" for n in (1, 2, 3, 4, 5, 6)}
    assert {d["status"] for d in state["destinations"]} == {"voided"} and all(d["amount_due_minor_units"] == 0 for d in state["destinations"])
    history = run("estimate history", estimate=estimate["id"])
    assert [r["status"] for r in history["items"]] == ["draft"] + ["accepted"] * 7      # acceptance plus six conversions
    assert estimate["version"] == 8

    # Durable keys against the seeded state: identical intent replays the voided invoice; a new partial request is fresh.
    line2 = lines[2]["line_id"]
    replay = run("estimate invoice", estimate=estimate["id"], expected_version=3, conversion_key=p + "EST-1 forty cents",
                 date="2026-09-24", number=p + "INV-2", selections=[dict(line_id=line2, net_amount="0.40")])
    assert replay["idempotent_replay"] and replay["id"] == forty["id"] and replay["status"] == "voided"
    with pytest.raises(BookflowError) as caught:
        run("estimate invoice", estimate=estimate["id"], expected_version=3, conversion_key=p + "EST-1 forty cents",
            date="2026-09-24", number=p + "INV-2", selections=[dict(line_id=line2, net_amount="0.41")])
    assert caught.value.code == "E_CONVERSION_KEY_REUSED"
    with pytest.raises(BookflowError) as caught:
        run("estimate invoice", estimate=estimate["id"], expected_version=estimate["version"], conversion_key=p + "EST-1 overrun",
            date="2026-09-30", selections=[dict(line_id=lines[0]["line_id"], quantity="4.000001")])
    assert caught.value.code == "E_VALUE_RANGE"

    # Work order: half a day paid, the rest invoiced, both voided; the file stays on the estimate and is reachable from every sale.
    order = run("work-order show", work_order=run("work-order query", customer=customer["id"])["items"][0]["id"])
    receipt = run("sales-receipt show", sales_receipt=receipts[p + "SR-1"]["id"])
    assert receipt["revision"]["lines"][0]["quantity"] == "0.5" and receipt["revision"]["billing_sources"][0]["source_document_id"] == order["id"]
    seven = sources(p + "INV-7")
    assert seven["revision"]["lines"][0]["quantity"] == "1.5"
    wo_state = run("work-order billing", work_order=order["id"])
    assert (wo_state["remaining_net_minor_units"], wo_state["lines"][0]["state"]) == (20000, "unbilled")
    attachments = run("attachment list", record_type="work_document", record_id=estimate["id"])
    assert attachments["count"] == 1
    attachment = attachments["items"][0]["attachment"]
    assert (attachment["sha256"], attachment["size_bytes"]) == (hashlib.sha256(FIXTURE).hexdigest(), len(FIXTURE))
    for sale in (one, forty, half, corrected, rebill, remaining):
        assert {s["root_document_id"] for s in sale["revision"]["billing_sources"]} == {estimate["id"]}
    # Earlier demo namespaces are untouched.
    commercial = client.customer.show(customer="Commercial Example Customer", company=company)
    billing = client.customer.show(customer="Billing Example Customer", company=company)
    assert (commercial["current_balance"]["minor_units"], billing["current_balance"]["minor_units"]) == (12800, 0)
    assert run("invoice query", customer=billing["id"])["count"] == 4


@pytest.mark.parametrize("company,prefix", COMPANIES)
def test_voided_progress_demos_leave_every_old_balance_and_zero_net_effect(reference_client, company, prefix):
    client, _ = reference_client
    # DEMO's aggregates moved when the seed gained the buying half of its month -- an order
    # billed and paid, a cheque, a card charge, a card payment, a return credit and a count
    # adjustment. Recomputed from the report rather than nudged to fit, and each one checked
    # against what the arc should do: the books still balance (debit == credit), the balance
    # sheet's difference is still zero, Checking is untouched because that arc funds itself
    # from the demo's other bank account, A/P settles to the 21.90 return credit still open,
    # and stock ends at 22 valves valued 240.90 -- 24 ordered less the 2 the count wrote off,
    # at the order's actual 10.95 rather than the item's standard cost.
    checking, trial, journals, profit, equity = ((624895, 749354, 14, 119290, 619290) if prefix == "DEMO"
                                                 else (7267800, 8048639, 36, 6457035, 7457035))
    assert client.account.show(account="Checking", company=company)["balance"]["minor_units"] == checking
    assert client.account.show(account="Accounts Receivable", company=company)["balance"]["minor_units"] == 13839
    assert client.account.show(account="Sales Tax Payable", company=company)["balance"]["minor_units"] == 1604
    totals = client.report.trial_balance(company=company, date_to="2026-12-31", limit=200)["totals"]
    assert totals["debit"]["minor_units"] == totals["credit"]["minor_units"] == trial
    statement = client.report.profit_and_loss(company=company, date_from="2026-01-01", date_to="2026-12-31")
    sheet = client.report.balance_sheet(company=company, date_to="2026-12-31")
    assert statement["totals"]["net_income"]["minor_units"] == profit
    assert (sheet["totals"]["total_equity"]["minor_units"], sheet["totals"]["difference"]["minor_units"]) == (equity, 0)
    assert client.journal.query(company=company, limit=200)["count"] == journals
    rows, _ = page_rows(client.report.general_ledger, company=company, date_from="2026-01-01", date_to="2026-12-31", limit=200)
    net, gross = defaultdict(int), defaultdict(int)
    p = prefix + "-PROG-"
    for row in rows:
        if row["kind"] == "posting" and row["transaction_number"].startswith(p):
            net[row["transaction_number"], row["current_account_label"]] += row["debit"]["minor_units"] - row["credit"]["minor_units"]
            gross[row["transaction_number"]] += row["debit"]["minor_units"]
    assert net and all(value == 0 for value in net.values()), dict(net)
    posted = lambda nets: sum(nets) + sum(map(tax, nets))  # noqa: E731
    assert gross == {p + "INV-1": 2 * posted([INV1]), p + "INV-2": 2 * posted([INV2]), p + "INV-3": 2 * posted([INV3]),
                     p + "INV-4": 2 * posted(INV4) + 2 * posted(INV4 + [EXTRA]), p + "INV-5": 2 * posted(INV5),
                     p + "INV-6": 2 * posted([INV1]), p + "INV-7": 2 * posted([INV7]), p + "SR-1": 2 * posted([SR1])}
    # Read-only previews change nothing: a fresh quarter of the free quote and a zero-only remaining check.
    customer = client.customer.show(customer="Progress Example Customer", company=company)
    estimate = client.run("estimate query", {"customer": customer["id"], "number": p + "EST-1"}, company=company)["items"][0]
    preview = client.run("estimate invoice", dict(estimate=estimate["id"], expected_version=estimate["version"],
        conversion_key=p + "EST-1 preview only", date="2026-09-30", percent="25"), company=company, reason="Progress billing demo test", dry_run=True)
    assert preview["dry_run"] and [l["net_minor_units"] for l in preview["revision"]["lines"]] == [10000, even(99 * 25, 100), 25]
    assert client.run("estimate billing", {"estimate": estimate["id"]}, company=company)["remaining_net_minor_units"] == 40199
    assert client.customer.show(customer="Progress Example Customer", company=company)["current_balance"]["minor_units"] == 0
