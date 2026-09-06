"""Row 16 demo seeds: linked non-posting customer work with zero ledger effect."""
import hashlib
import io
import sqlite3
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest

from bookflow import BookflowError
from tests.test_reference_year import reference_client, reference_template  # noqa: F401
from tests.test_service_sales_demo import COMPANIES


NOUNS = ("proposal", "estimate", "work-order")
SHARED_VERBS = ("create", "update", "copy", "show", "query", "history")
OWN_VERB = {"proposal": "estimate", "estimate": "work-order", "work-order": "complete"}
OLD_COUNTS = {"seed.toml": 171, "reference.toml": 76}
FIXTURE = files("bookflow.demo").joinpath("example.pdf").read_bytes()


def five_lines():
    """USD cents, independent of the work calculator and the seed inputs.

    Markup: qty 2, cost 4.00, 25% => rate 5.00, net 10.00, tax 0.80.
    Amount: qty 2, net 10.01, no rate; tax half-even(10.01*8%) = 0.80.
    Catalog: qty 1 at the 100.00 catalog price, cost unknown; tax 8.00.
    Zero-cost markup: qty 3, cost 0, 50% => rate 0, net 0, tax 0.
    Manual nonbillable exempt: qty 1 at 20.00; tax 0.
    """
    return [("markup", 500, 1000, 80, 400, 800, True), ("amount", None, 1001, 80, None, None, True),
            ("catalog", 10000, 10000, 800, None, None, True), ("markup", 0, 0, 0, 0, 0, True),
            ("manual", 2000, 2000, 0, None, None, False)]


NET, TAX, GROSS = (sum(line[i] for line in five_lines()) for i in (2, 3, 2))
GROSS = NET + TAX
assert (NET, TAX, GROSS) == (14001, 960, 14961)


@pytest.mark.parametrize("resource", ["seed.toml", "reference.toml"])
def test_manifests_append_all_twenty_one_work_commands_after_the_old_entries(resource):
    commands = tomllib.loads(files("bookflow.demo").joinpath(resource).read_text())["commands"]
    old, new = commands[:OLD_COUNTS[resource]], commands[OLD_COUNTS[resource]:(201 if resource == "seed.toml" else 107)]
    assert not [e for e in old if e["command"].split()[0] in NOUNS]
    verbs = {f"{noun} {verb}" for noun in NOUNS for verb in (*SHARED_VERBS, OWN_VERB[noun])}
    work = [e for e in new if e["command"] in verbs]
    assert {e["command"] for e in work} == {
        f"{noun} {verb}" for noun in NOUNS for verb in (*SHARED_VERBS, OWN_VERB[noun])}
    for entry in work:
        if entry["command"].split()[1] not in ("show", "query", "history"):
            assert entry["reason"].strip()
    assert [e["command"] for e in new if e.get("body_fixture")] == ["attachment add"]
    prefix = "DEMO-WORK-" if resource == "seed.toml" else "REF-WORK-"
    numbers = [e["input"]["number"] for e in work if "number" in e["input"] and "-WORK-" in e["input"]["number"]]
    assert numbers and all(n.startswith(prefix) for n in numbers)
    assert not [e for e in old if str(e.get("input", {}).get("number", "")).startswith(prefix)]


def work_counts(client, company, prefix):
    """Rows belonging to this package's own numbered documents; later packages add their own namespaces."""
    database = Path(client.company.show(company=company)["path"]) / "company.db"
    like = prefix + "-WORK-%"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        docs = 'SELECT id FROM work_documents WHERE number LIKE ?'
        counts = {"work_documents": db.execute(f"SELECT count(*) FROM ({docs})", (like,)).fetchone()[0]}
        for name in ("work_revisions", "work_lines", "work_line_identities"):
            counts[name] = db.execute(f'SELECT count(*) FROM "{name}" WHERE document_id IN ({docs})', (like,)).fetchone()[0]
        counts["work_links"] = db.execute(f"SELECT count(*) FROM work_links WHERE source_document_id IN ({docs})", (like,)).fetchone()[0]
        prior_sales = "SELECT id FROM transactions WHERE number NOT LIKE ? AND number NOT LIKE ?"
        later = (prefix + '-BILL-%', prefix + '-PROG-%')  # Row 17 and Row 18 voided billing demos
        counts['transactions'] = db.execute(f'SELECT count(*) FROM ({prior_sales})', later).fetchone()[0]
        counts.update({name: db.execute(f'SELECT count(*) FROM "{name}" WHERE transaction_id IN ({prior_sales})',
                       later).fetchone()[0]
                       for name in ("transaction_revisions", "posting_batches", "posting_lines")})
        return counts


@pytest.mark.parametrize("company,prefix", COMPANIES)
def test_seeded_work_chain_lineage_arithmetic_and_history(reference_client, company, prefix):
    client, _ = reference_client
    customer = client.customer.show(customer="Commercial Example Customer", company=company)
    run = lambda name, **data: client.run(name, data, company=company, reason="Customer work demo test")  # noqa: E731
    numbers = {}
    for noun in NOUNS:
        page = run(noun + " query", customer=customer["id"], active=None)
        assert not page["has_more"]
        numbers[noun] = {row["number"]: row for row in page["items"]}
    p = prefix + "-WORK-"
    assert set(numbers["proposal"]) == {p + "PROP-1", p + "PROP-2"}
    assert set(numbers["estimate"]) == {p + "EST-1A", p + "EST-1B", p + "EST-2", p + "EST-3"}
    assert set(numbers["work-order"]) == {p + "WO-1", p + "WO-2", p + "WO-3"}
    assert run("estimate query", customer=customer["id"], status="accepted")["count"] == 1

    # Proposal: five pricing witnesses, custom bool false, then cleared, then opened, then converted.
    proposal = run("proposal show", proposal=numbers["proposal"][p + "PROP-1"]["id"])
    assert (proposal["status"], proposal["version"], proposal["gross_minor_units"]) == ("open", 4, GROSS)
    first = run("proposal show", proposal=proposal["id"], revision_number=1)
    rev = first["revision"]
    assert (rev["net_minor_units"], rev["tax_minor_units"], rev["gross_minor_units"]) == (NET, TAX, GROSS)
    for line, (basis, price, net, tax, cost, extended, billable) in zip(rev["lines"], five_lines(), strict=True):
        assert line["facts"]["pricing_basis"] == basis
        assert (line["unit_price"] and line["unit_price"]["minor_units"]) == price
        assert (line["net"]["minor_units"], line["tax"]["minor_units"]) == (net, tax)
        assert (line["estimated_unit_cost"] and line["estimated_unit_cost"]["minor_units"]) == cost
        assert (line["estimated_cost"] and line["estimated_cost"]["minor_units"]) == extended
        assert line["facts"]["billable"] is billable
        assert line["root_document_id"] == proposal["id"] and line["source_line_id"] is None
    assert rev["known_cost_total"]["minor_units"] == 800
    assert rev["cost_complete"] is False and rev["estimated_profit"] is None
    [permit] = rev["custom_fields"]
    assert (permit["name"], permit["kind"], permit["value"]) == ("Permit pulled", "bool", False)
    assert run("proposal show", proposal=proposal["id"], revision_number=2)["revision"]["custom_fields"] == []
    assert proposal["revision"]["custom_fields"] == []
    history = run("proposal history", proposal=proposal["id"])
    assert [(r["revision_number"], r["status"]) for r in history["items"]] == [(1, "draft"), (2, "draft"), (3, "open"), (4, "open")]
    assert len({r["decision_note"] for r in history["items"]}) == 1  # never decided
    links = {(l["relation"], l["destination_number"]): l for l in proposal["links"]}
    assert set(links) == {("proposal_estimate", p + "EST-1A"), ("copy", p + "PROP-2")}
    # The replayed conversion added no second link; the first conversion bumped the source version.
    assert links["proposal_estimate", p + "EST-1A"]["source_version"] == 3
    assert links["copy", p + "PROP-2"]["source_version"] == 4

    # Estimates: alternative A from the proposal, alternative B copied, EST-2 independent, EST-3 direct.
    est_a = run("estimate show", estimate=numbers["estimate"][p + "EST-1A"]["id"])
    est_b = run("estimate show", estimate=numbers["estimate"][p + "EST-1B"]["id"])
    est_2 = run("estimate show", estimate=numbers["estimate"][p + "EST-2"]["id"])
    est_3 = run("estimate show", estimate=numbers["estimate"][p + "EST-3"]["id"])
    assert est_a["estimate_group_id"] == est_b["estimate_group_id"] == proposal["id"]
    assert est_2["estimate_group_id"] == est_2["id"]
    assert (est_a["version"], est_a["status"], est_a["gross_minor_units"]) == (1, "draft", GROSS)
    assert (est_b["version"], est_b["status"], est_b["gross_minor_units"]) == (3, "accepted", GROSS)
    assert est_3["gross_minor_units"] == 1080 and est_3["revision"]["custom_fields"][0]["value"] is False
    [permit] = est_a["revision"]["custom_fields"]
    assert permit["value"] is True  # creation default after the proposal cleared its own value
    for line in est_a["revision"]["lines"]:
        assert line["root_document_id"] == est_a["id"] and line["source_line_id"] is not None
    a_links = {(l["relation"], l["source_number"], l["destination_number"], l["source_version"]) for l in est_a["links"]}
    assert a_links == {("proposal_estimate", p + "PROP-1", p + "EST-1A", 3), ("copy", p + "EST-1A", p + "EST-1B", 1),
                       ("copy", p + "EST-1A", p + "EST-2", 1)}
    b_history = run("estimate history", estimate=est_b["id"])
    assert [(r["revision_number"], r["status"]) for r in b_history["items"]] == [(1, "draft"), (2, "accepted"), (3, "accepted")]
    assert est_b["revision"]["accepted_revision_id"] == b_history["items"][1]["id"]
    assert est_b["revision"]["decision_note"].startswith("Accepted by telephone")
    assert run("estimate show", estimate=est_b["id"], revision_number=1)["revision"]["accepted_revision_id"] is None
    with pytest.raises(BookflowError) as caught:
        run("estimate update", estimate=est_a["id"], expected_version=1, status="accepted", decision_note="Second acceptance")
    assert caught.value.code == "E_WORK_DEPENDENCY"

    # Work orders: WO-1 from the accepted alternative, scheduled, started, completed; WO-2 copied; WO-3 direct.
    order = run("work-order show", work_order=numbers["work-order"][p + "WO-1"]["id"])
    assert (order["version"], order["status"], order["gross_minor_units"]) == (4, "complete", GROSS)
    facts = order["revision"]["facts"]
    assert (facts["priority"], facts["actual_start"], facts["actual_end"]) == ("high", "2026-09-14T13:10:00Z", "2026-09-14T16:30:00Z")
    assert [a["label"] for a in facts["assignees"]] == ["Casey Worker" if prefix == "DEMO" else "Reference Crew Lead"]
    assert [l["completed_quantity"] for l in order["revision"]["lines"]] == ["2", "2", "1", "3", "1"]
    assert [l["quantity"] for l in order["revision"]["lines"]] == ["2", "2", "1", "3", "1"]
    started = run("work-order show", work_order=order["id"], revision_number=3)
    assert [l["completed_quantity"] for l in started["revision"]["lines"]] == ["1", "0", "0", "0", "0"]
    assert started["revision"]["facts"]["actual_end"] is None
    o_history = run("work-order history", work_order=order["id"])
    assert [(r["revision_number"], r["status"]) for r in o_history["items"]] == [
        (1, "draft"), (2, "scheduled"), (3, "in_progress"), (4, "complete")]
    for line in order["revision"]["lines"]:
        assert line["root_document_id"] == est_b["id"] and line["source_line_id"] is not None  # inherited roots
    assert {(l["relation"], l["source_number"], l["source_version"], l["destination_number"]) for l in order["links"]} == {
        ("estimate_work_order", p + "EST-1B", 2, p + "WO-1"), ("copy", p + "WO-1", 4, p + "WO-2")}
    copy = run("work-order show", work_order=numbers["work-order"][p + "WO-2"]["id"])
    assert (copy["status"], copy["version"], copy["gross_minor_units"]) == ("draft", 1, GROSS)
    assert [l["completed_quantity"] for l in copy["revision"]["lines"]] == ["0"] * 5
    assert copy["revision"]["facts"]["assignees"] == [] and copy["revision"]["facts"]["actual_start"] is None
    assert all(l["root_document_id"] == copy["id"] for l in copy["revision"]["lines"])
    direct = run("work-order show", work_order=numbers["work-order"][p + "WO-3"]["id"])
    assert (direct["status"], direct["gross_minor_units"], direct["revision"]["facts"]["priority"]) == ("draft", 4320, "urgent")
    assert direct["revision"]["lines"][0]["completed_quantity"] == "1"
    assert direct["revision"]["facts"]["site_address"]["line1"] == "12 Riverside Ct"

    # Durable keys: identical intent replays, different intent rejects, a stale source version is visible.
    replay = run("estimate work-order", estimate=est_b["id"], expected_version=2, conversion_key=p + "EST-1B work order",
                 date="2026-09-11", number=p + "WO-1", scheduled_start="2026-09-14T13:00:00Z",
                 scheduled_end="2026-09-14T17:00:00Z", assignees=[facts["assignees"][0]["id"]])
    assert replay["idempotent_replay"] and replay["id"] == order["id"] and replay["status"] == "complete"
    with pytest.raises(BookflowError) as caught:
        run("estimate work-order", estimate=est_b["id"], expected_version=3, conversion_key=p + "EST-1B work order",
            date="2026-09-12")
    assert caught.value.code == "E_CONVERSION_KEY_REUSED"
    with pytest.raises(BookflowError) as caught:
        run("proposal estimate", proposal=proposal["id"], expected_version=3, conversion_key=p + "PROP-1 alternative C",
            date="2026-09-12")
    assert caught.value.code == "E_VERSION_CONFLICT"

    # Annotations stay on the proposal; descendants reach them through exact source links.
    notes = run("note list", record_type="work_document", record_id=proposal["id"])
    assert notes["count"] == 1 and "sketch" in notes["items"][0]["body"]
    attachments = run("attachment list", record_type="work_document", record_id=proposal["id"])
    assert attachments["count"] == 1
    attachment = attachments["items"][0]["attachment"]
    assert (attachment["sha256"], attachment["size_bytes"]) == (hashlib.sha256(FIXTURE).hexdigest(), len(FIXTURE))
    sink = io.BytesIO()
    client.attachment.get(attachment=attachment["id"], output_stream=sink, company=company)
    assert sink.getvalue() == FIXTURE
    ancestry, document = [], order
    while True:
        sources = [l for l in document["links"] if l["destination_document_id"] == document["id"]]
        if not sources:
            break
        [source] = sources
        ancestry.append((source["relation"], source["source_number"]))
        noun = source["source_kind"].replace("_", "-")
        document = run(noun + " show", **{source["source_kind"]: source["source_document_id"]})
    assert ancestry == [("estimate_work_order", p + "EST-1B"), ("copy", p + "EST-1A"), ("proposal_estimate", p + "PROP-1")]
    assert document["id"] == proposal["id"]
    for descendant in (est_a["id"], est_b["id"], order["id"]):
        assert run("note list", record_type="work_document", record_id=descendant)["count"] == 0
        assert run("attachment list", record_type="work_document", record_id=descendant)["count"] == 0


@pytest.mark.parametrize("company,prefix", COMPANIES)
def test_work_seeds_post_nothing_and_previews_change_nothing(reference_client, company, prefix):
    client, _ = reference_client
    checking, trial, journals, profit = ((624895, 690195, 10, 133095) if prefix == "DEMO"
                                         else (7267800, 8030600, 36, 6439000))
    assert client.account.show(account="Checking", company=company)["balance"]["minor_units"] == checking
    assert client.account.show(account="Accounts Receivable", company=company)["balance"]["minor_units"] == 12800
    totals = client.report.trial_balance(company=company, date_to="2026-12-31", limit=200)["totals"]
    assert totals["debit"]["minor_units"] == totals["credit"]["minor_units"] == trial
    statement = client.report.profit_and_loss(company=company, date_from="2026-01-01", date_to="2026-12-31")
    assert statement["totals"]["net_income"]["minor_units"] == profit
    customer = client.customer.show(customer="Commercial Example Customer", company=company)
    assert customer["current_balance"]["minor_units"] == 12800
    before = work_counts(client, company, prefix)
    assert before["transactions"] == journals + 4
    assert (before["transaction_revisions"], before["posting_batches"], before["posting_lines"]) == (
        (22, 33, 99) if prefix == "DEMO" else (45, 53, 132))
    # Nine documents: PROP-1 (4 revisions), PROP-2, EST-1A, EST-1B (3), EST-2, EST-3, WO-1 (4), WO-2, WO-3.
    assert before["work_documents"] == 9
    assert before["work_revisions"] == 4 + 1 + 1 + 3 + 1 + 1 + 4 + 1 + 1
    assert before["work_lines"] == 5 * (4 + 1 + 1 + 3 + 1 + 4 + 1) + 1 + 1
    assert before["work_line_identities"] == 5 * 7 + 1 + 1
    assert before["work_links"] == 6
    run = lambda name, **data: client.run(name, data, company=company, reason="Customer work demo test", dry_run=True)  # noqa: E731
    p = prefix + "-WORK-"
    order = client.run("work-order query", {"number": p + "WO-1"}, company=company)["items"][0]
    preview = run("work-order complete", work_order=order["id"], expected_version=4, actual_end="2026-09-14T16:30:00Z")
    assert not preview["changed"] and preview["status"] == "complete"
    estimate = client.run("estimate query", {"number": p + "EST-3"}, company=company)["items"][0]
    preview = run("estimate update", estimate=estimate["id"], expected_version=1, status="accepted", decision_note="Preview only")
    assert preview["dry_run"] and preview["status"] == "accepted" and len(preview["facts_fingerprint"]) == 64
    preview = run("proposal create", date="2026-09-16", customer=customer["id"], title="Preview only",
                  sales_tax_item="Commercial Example Tax 8%", customer_tax_code="Tax",
                  lines=[dict(item="Commercial Example Service", quantity="2", estimated_unit_cost="4.00", markup_percent="25")])
    assert (preview["net_minor_units"], preview["tax_minor_units"]) == (1000, 80)
    assert preview["revision"]["custom_fields"][0]["value"] is True
    assert work_counts(client, company, prefix) == before
    assert client.run("estimate query", {"number": p + "EST-3"}, company=company)["items"][0]["status"] == "draft"
