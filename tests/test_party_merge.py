"""R117: merging a duplicate customer into another by an audited alias.

A merge changes nothing posted; A/R aging, the balance summary and the customer list show one
customer; the merge is refused for the cases the anchor refuses, is idempotent, is people only,
and undoes cleanly.
"""
import hashlib
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from tests.conftest import as_user, make_actor
from tests.demo_oracle import DEMO_AS_OF

COMPANY = "Demo Plumbing Co"
SURVIVOR = "Commercial Example Customer"
MERGED = "Line Kinds Example Customer"
POSTED = ("posting_lines", "posting_batches", "transaction_revisions", "sales_profiles",
          "payment_component_keys", "credit_source_keys", "applications", "transactions")


def _posted_digest(root):
    db = sqlite3.connect(root / "organizations" / "Demo Holdings LLC" / COMPANY / "company.db")
    try:
        digest = hashlib.sha256()
        for table in POSTED:
            for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                digest.update(repr(row).encode())
        return digest.hexdigest()
    finally:
        db.close()


def _aging(client):
    out = client.run("report ar-aging", {"as_of": DEMO_AS_OF, "limit": 200}, company=COMPANY)
    return {row["display_customer_label"]: row["total"]["minor_units"] for row in out["rows"]}, out["totals"]["total"]["minor_units"]


def _summary(client):
    out = client.run("report customer-balance-summary", {"as_of": DEMO_AS_OF, "limit": 200}, company=COMPANY)
    return {row["display_customer_label"]: row["balance"]["minor_units"] for row in out["rows"]}


def _listed(client):
    return {row["full_name"] for row in client.run("customer list", {}, company=COMPANY)["items"]}


def _merge(client, merged=MERGED, into=SURVIVOR, **kw):
    return client.run("customer merge", {"merged": merged, "into": into}, company=COMPANY,
                      reason=kw.pop("reason", "same customer entered twice"), **kw)


def test_merge_shows_one_customer_and_changes_nothing_posted(client, root):
    rows, total = _aging(client)
    survivor, merged = rows[SURVIVOR], rows[MERGED]
    assert merged != 0 and MERGED in _listed(client)
    before = _posted_digest(root)

    preview = _merge(client, dry_run=True)
    assert preview["merge_id"] is None and preview["changed"]
    assert preview["merged_balance"]["minor_units"] == merged
    assert preview["survivor_balance"]["minor_units"] == survivor
    assert preview["survivor_balance_after"]["minor_units"] == survivor + merged
    assert preview["document_count"] == sum(item["count"] for item in preview["documents"]) > 0
    assert MERGED in _listed(client)  # a dry run writes nothing

    done = _merge(client)
    assert done["changed"] and done["merge_id"]
    # Nothing posted moved.
    assert _posted_digest(root) == before
    # A/R aging and the balance summary show one combined customer, and the total is unmoved.
    rows, after_total = _aging(client)
    assert MERGED not in rows and rows[SURVIVOR] == survivor + merged and after_total == total
    summary = _summary(client)
    assert MERGED not in summary and summary[SURVIVOR] == survivor + merged
    # Open invoices and the statement for the survivor carry the merged customer's documents.
    opened = client.run("report open-invoices", {"as_of": DEMO_AS_OF, "customer": SURVIVOR, "limit": 200}, company=COMPANY)
    assert opened["totals"]["balance"]["minor_units"] == survivor + merged
    # Selecting the merged customer by name reads as the survivor.
    by_old = client.run("report open-invoices", {"as_of": DEMO_AS_OF, "customer": MERGED, "limit": 200}, company=COMPANY)
    assert by_old["totals"]["balance"]["minor_units"] == survivor + merged
    # The merged customer is hidden and cannot be made active while merged.
    assert MERGED not in _listed(client)
    with pytest.raises(BookflowError) as error:
        client.run("customer activate", {"customer": MERGED}, company=COMPANY)
    assert error.value.code == "E_RECORD_IN_USE"

    # Idempotent: the same merge again writes nothing.
    again = _merge(client)
    assert not again["changed"] and again["merge_id"] == done["merge_id"]
    assert _posted_digest(root) == before

    # The survivor's payment can pay the merged customer's old invoice.
    method = next(row["id"] for row in client.run("payment-method list", {}, company=COMPANY)["items"]
                  if row["kind"] == "cash")
    invoice = next(row for row in opened["rows"] if row["customer_id"] is not None
                   and row["balance"]["minor_units"] == merged)
    paid = client.run("payment receive", dict(customer=SURVIVOR, date=DEMO_AS_OF, amount=invoice["balance"]["amount"],
                                              payment_method=method, operation_key="merged-invoice",
                                              applications=dict(mode="inline", items=[dict(
                                                  invoice=invoice["transaction_id"], expected_version=1,
                                                  amount=invoice["balance"]["amount"])])),
                      company=COMPANY, reason="paid the old duplicate's invoice")
    assert paid
    rows, paid_total = _aging(client)
    assert rows[SURVIVOR] == survivor and paid_total == total - merged

    # Undo: the merged customer reads as itself again and is active; still nothing rewritten.
    undone = client.run("customer unmerge", {"merged": MERGED}, company=COMPANY, reason="they are two companies")
    assert undone["changed"] and undone["reactivated"]
    rows, _ = _aging(client)
    assert MERGED not in rows  # its invoice was paid from the survivor, so it owes nothing now
    assert MERGED in _listed(client)
    # The receipt keyed its credit to the merged customer's own receivable, so each reads as itself.
    summary = _summary(client)
    assert summary[SURVIVOR] == survivor and MERGED not in summary
    assert not client.run("customer unmerge", {"merged": MERGED}, company=COMPANY, reason="again")["changed"]


@pytest.mark.parametrize("merged, into, problem", [
    (SURVIVOR, SURVIVOR, "into itself"),
    ("Payment Example Customer:Job A", SURVIVOR, "job cannot be merged into a customer"),
    (SURVIVOR, "Payment Example Customer:Job A", "cannot be merged into a job"),
    ("Payment Example Customer", SURVIVOR, "has jobs of its own"),
])
def test_refusals(client, merged, into, problem):
    with pytest.raises(BookflowError) as error:
        _merge(client, merged, into)
    assert error.value.code == "E_MERGE_REFUSED" and problem in error.value.details["problem"]


def test_merge_into_a_merged_customer_and_a_reason_are_required(client):
    _merge(client)
    with pytest.raises(BookflowError) as error:
        _merge(client, "Tax Work Example Customer", MERGED)
    assert error.value.code == "E_MERGE_REFUSED"
    with pytest.raises(BookflowError) as error:
        client.run("customer merge", {"merged": "Tax Work Example Customer", "into": SURVIVOR}, company=COMPANY)
    assert error.value.code == "E_REASON_REQUIRED"


def test_an_agent_is_refused(client, root):
    """An agent authorized at the same role as a person who may merge is still refused."""
    from bookflow.core.config import Config
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run as dispatch_run
    from bookflow.core import registry
    from tests.conftest import make_agent
    company = client.company.show(company=COMPANY)["company_id"]
    person = make_actor(root, "merge-person", company_role=(company, "standard"))
    agent = make_agent(lambda name, body: client.run(name, body), "merge-agent", principals=person, company=company)
    config = Config.load(root / "config.toml")
    config.set_user("merge-agent", agent)
    config.save()
    for name, body in (("customer merge", {"merged": MERGED, "into": SURVIVOR}), ("customer unmerge", {"merged": MERGED})):
        ctx = Context.new(Interface.python, "merge agent witness", on_behalf_of=person, reason="an agent tidying up")
        with pytest.raises(BookflowError) as error:
            dispatch_run(registry.get(name), body, ctx, data_root=str(root), company_selector=COMPANY,
                         company_source="option", _login="merge-agent")
        assert error.value.code == "E_PERMISSION" and error.value.details.get("required_role") == "human"
    # The person the agent acts for may merge.
    assert as_user(root, "merge-person").run("customer merge", {"merged": MERGED, "into": SURVIVOR},
                                             company=COMPANY, reason="same customer twice")["changed"]


def test_vendor_merge_shares_the_alias(client, root):
    def ap_aging():
        out = client.run("report ap-aging", {"as_of": DEMO_AS_OF, "limit": 200}, company=COMPANY)
        return {row["current_vendor_name"]: row["total"]["minor_units"] for row in out["rows"]}
    open_vendor = next(name for name, total in ap_aging().items() if total)
    with pytest.raises(BookflowError) as error:
        client.run("vendor merge", {"merged": open_vendor, "into": "Lakeview Pipe Supply"}, company=COMPANY, reason="dup")
    assert error.value.code == "E_MERGE_REFUSED" and "open payable balance" in error.value.details["problem"], (error.value.code, error.value.details)
    before = _posted_digest(root)
    done = client.run("vendor merge", {"merged": "Summit Pipe Contracting", "into": "Lakeview Pipe Supply"},
                      company=COMPANY, reason="same supplier twice")
    assert done["changed"] and done["document_count"] > 0 and _posted_digest(root) == before
    names = {row["name"] for row in client.run("vendor list", {}, company=COMPANY)["items"]}
    assert "Summit Pipe Contracting" not in names
    undone = client.run("vendor unmerge", {"merged": "Summit Pipe Contracting"}, company=COMPANY, reason="two suppliers")
    assert undone["changed"]
    assert "Summit Pipe Contracting" in {row["name"] for row in client.run("vendor list", {}, company=COMPANY)["items"]}
