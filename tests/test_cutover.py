"""R166: move a company in from its old books at a period boundary.

The sample set under tests/fixtures/cutover/ is a plumbing company's desktop-product export as of
2026-09-30: IIF lists (chart of accounts, customers, vendors, items) and report CSVs (trial
balance, open invoices, unpaid bills, both aging summaries, inventory valuation). Its figures were
added up by hand from the files themselves: the trial balance's debits and credits are 298,887.93
each, the open invoices and credits come to 25,099.05 (the trial balance's Accounts Receivable),
the unpaid bills and credit to 9,845.90 (Accounts Payable) and the two stocked items to 1,675.00
(Inventory Asset).
"""
from pathlib import Path

import pytest

from bookflow import BookflowError

FIXTURES = Path(__file__).parent / "fixtures" / "cutover"
COMPANY = "Riverbend"
AS_OF = "2026-09-30"
FILES = ("accounts.iif", "customers.iif", "vendors.iif", "items.iif", "trial_balance.csv", "open_invoices.csv",
         "unpaid_bills.csv", "ar_aging.csv", "ap_aging.csv", "inventory_valuation.csv")
ATTACHED = ("accounts.iif", "trial_balance.csv")  # read from attachments; the rest are given as text


def text(name):
    raw = (FIXTURES / name).read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


@pytest.fixture
def mover(client):
    client.run("company new", dict(organization="Demo Holdings LLC", legal_name="Riverbend Plumbing LLC",
                                   display_name=COMPANY, home_currency="USD", timezone="America/Chicago"))
    run = lambda name, data=None, **kw: client.run(name, data or {}, company=COMPANY, **kw)
    run("company update", dict(sales_tax_enabled=True))
    company_id = run("company show")["company_id"]
    given = []
    for name in FILES:
        if name in ATTACHED:
            with open(FIXTURES / name, "rb") as body:
                added = run("attachment add", dict(record_type="company_info", record_id=company_id, original_filename=name,
                                                   media_type="text/plain", caption="Old books export"), input_stream=body)
            given.append({"attachment": added["attachment"]["id"]})
        else:
            given.append({"content": text(name), "name": name})
    return dict(run=run, files=given, client=client)


def _counts(result):
    return {row["kind"]: (row["create"], row["already_in"], row["matched"]) for row in result["counts"]}


def _amounts(result):
    return {row["name"]: (row["source"]["minor_units"], row["compared"]["minor_units"]) for row in result["checks"]}


def _balances(run):
    rows = run("report trial-balance", dict(date_to=AS_OF, limit=200))["rows"]
    return {row["display_account_label"]: row["signed_net"]["minor_units"] for row in rows}


def test_the_sample_books_move_in_and_tie_out_to_the_cent(mover):
    run, files = mover["run"], mover["files"]
    plan = run("cutover plan", dict(as_of=AS_OF, files=files))
    assert plan["ready"], plan["exceptions"]
    assert {f["name"]: f["kind"] for f in plan["files"]} == {
        "accounts.iif": "iif", "customers.iif": "iif", "vendors.iif": "iif", "items.iif": "iif",
        "trial_balance.csv": "trial_balance", "open_invoices.csv": "open_invoices", "unpaid_bills.csv": "unpaid_bills",
        "ar_aging.csv": "ar_aging", "ap_aging.csv": "ap_aging", "inventory_valuation.csv": "inventory_valuation"}
    assert _amounts(plan) == {"trial_balance_total": (29888793, 29888793), "receivables": (2509905, 2509905),
                              "payables": (984590, 984590), "inventory": (167500, 167500)}
    # 41 posting accounts: 19 are the general chart's own (number and name, or the system account
    # the old books' marker names), 22 are made, plus the clearing account; Net 45 is the one new term.
    assert _counts(plan) == {"account": (23, 0, 19), "term": (1, 0, 4), "customer": (11, 0, 0), "vendor": (7, 0, 0),
                             "item": (10, 0, 0), "invoice": (10, 0, 0), "credit_memo": (2, 0, 0), "bill": (6, 0, 0),
                             "vendor_credit": (1, 0, 0), "inventory_adjustment": (2, 0, 0), "journal": (1, 0, 0),
                             "deactivation": (1, 0, 0)}
    assert {e["code"] for e in plan["exceptions"]} == {"non_posting_accounts", "item_skipped", "sales_tax_payable"}
    journal = plan["journal"]
    assert not {line["description"] for line in journal["lines"]} & {
        "1100 · Accounts Receivable", "2000 · Accounts Payable", "1300 · Inventory Asset"}
    # The clearing line is what the documents and stock carry: AR less AP plus inventory.
    assert journal["clearing"]["minor_units"] == 2509905 - 984590 + 167500

    applied = run("cutover apply", dict(as_of=AS_OF, files=files), reason="Move in from QuickBooks")
    assert (applied["created"], applied["already_in"]) == (75, 0)
    tie = run("cutover tie-out", dict(as_of=AS_OF, files=files))
    assert tie["tied"], tie
    assert tie["clearing"]["minor_units"] == 0
    assert (tie["trial_balance"]["source_total"]["minor_units"], tie["trial_balance"]["books_total"]["minor_units"]) == (29888793, 29888793)
    assert tie["receivables"]["source"] == "A/R Aging Summary"
    assert (tie["receivables"]["source_total"]["minor_units"], tie["receivables"]["books_total"]["minor_units"]) == (2509905, 2509905)
    assert (tie["payables"]["source_total"]["minor_units"], tie["payables"]["books_total"]["minor_units"]) == (984590, 984590)

    # Balances on each account's normal side, as `account show` reports them.
    balance = lambda name: run("account show", dict(account=name))["balance"]["minor_units"]
    assert balance("Accounts Receivable") == 2509905
    assert balance("Accounts Payable") == 984590
    assert balance("Inventory Asset") == 167500
    assert balance("Retained Earnings") == 5172572  # the old books' 3900 · Retained Earnings
    assert balance("Truck:Accumulated Depreciation") == -1455000
    assert balance("Cutover Clearing") == 0
    assert all(step["record_id"] for step in applied["steps"])

    # Invoice 1150 keeps its number, dates and open balance, under the job it was billed to.
    steps = {step["name"]: step for step in applied["steps"]}
    invoice = run("invoice show", dict(invoice=steps["Invoice 1150 · Harbor View Apartments:Boiler Replacement"]["record_id"]))
    assert (invoice["number"], invoice["revision"]["date"], invoice["revision"]["profile"]["due_date"]) == ("1150", "2026-06-20", "2026-07-20")
    assert invoice["revision"]["total_minor_units"] == 645000
    assert invoice["revision"]["profile"]["customer"]["label"] == "Harbor View Apartments:Boiler Replacement"
    # Every write names its outside id.
    events = run("audit list", dict(limit=200))["items"]
    assert len([e for e in events if (e["source_ref"] or "").startswith("cutover:")]) == 75
    assert run("customer show", dict(customer="Patel, Anita"))["active"] is False


def test_a_rerun_makes_nothing_and_a_retry_replays(mover):
    run, files = mover["run"], mover["files"]
    first = run("cutover apply", dict(as_of=AS_OF, files=files), idempotency_key="move-in-1")
    before = _balances(run)
    retry = run("cutover apply", dict(as_of=AS_OF, files=files), idempotency_key="move-in-1")
    assert retry["idempotent_replay"] is True and retry["created"] == first["created"] == 75
    again = run("cutover apply", dict(as_of=AS_OF, files=files))
    assert (again["created"], again["already_in"]) == (0, 75)
    assert all(step["action"] == "already_in" for step in again["steps"])
    assert _balances(run) == before
    assert run("cutover tie-out", dict(as_of=AS_OF, files=files))["tied"]


def test_a_double_count_is_caught(mover):
    run, files = mover["run"], mover["files"]
    # The same open invoices given twice: refused before anything is written.
    doubled = files + [{"content": text("open_invoices.csv"), "name": "open_invoices (again).csv"}]
    plan = run("cutover plan", dict(as_of=AS_OF, files=doubled))
    assert not plan["ready"]
    # Each copy of each document is caught; none is counted twice.
    assert {"duplicate_file", "duplicate_document"} <= {e["code"] for e in plan["exceptions"] if e["severity"] == "blocking"}
    assert len([e for e in plan["exceptions"] if e["code"] == "duplicate_document"]) == 12
    with pytest.raises(BookflowError) as refused:
        run("cutover apply", dict(as_of=AS_OF, files=doubled))
    assert refused.value.code == "E_CUTOVER_BLOCKED"
    # A receivable account cannot be pointed at an ordinary account to slip AR into the journal.
    plan = run("cutover plan", dict(as_of=AS_OF, files=files, mappings={"accounts": {"1100 · Accounts Receivable": "1000"}}))
    assert "control_account_mapping" in {e["code"] for e in plan["exceptions"]}

    # The classic trap: the books are moved in, then the opening AR goes in again as a journal.
    run("cutover apply", dict(as_of=AS_OF, files=files))
    adams = run("customer show", dict(customer="Adams, Rachel"))["id"]
    run("journal post", dict(date=AS_OF, memo="Opening AR", lines=[
        dict(account="Accounts Receivable", side="debit", amount="412.50", name_type="customer", name_id=adams),
        dict(account="Opening Balance Equity", side="credit", amount="412.50")]))
    tie = run("cutover tie-out", dict(as_of=AS_OF, files=files))
    assert not tie["tied"]
    differences = {(row["name"], row["difference"]["minor_units"]) for row in tie["trial_balance"]["rows"]}
    assert {("Accounts Receivable", -41250), ("Opening Balance Equity", 41250)} <= differences
    assert ("Adams, Rachel", "total", -41250) in {(r["name"], r["column"], r["difference"]["minor_units"]) for r in tie["receivables"]["rows"]}
    plan = run("cutover plan", dict(as_of=AS_OF, files=files))
    assert "entries_before_cutover" in {e["code"] for e in plan["exceptions"] if e["severity"] == "blocking"}


def test_the_exception_report_lists_unmapped_accounts(mover):
    run, files = mover["run"], mover["files"]
    original = text("trial_balance.csv")
    extra = original.replace('"6300 · Office Supplies",,"1,148.72",',
                             '"6300 · Office Supplies",,"1,048.72",\r\n"6150 · Uncategorized Expenses",,"100.00",')
    assert extra != original
    files = [f for name, f in zip(FILES, files) if name != "trial_balance.csv"] + [{"content": extra, "name": "trial_balance.csv"}]
    plan = run("cutover plan", dict(as_of=AS_OF, files=files))
    assert not plan["ready"]
    unmapped = [e for e in plan["exceptions"] if e["code"] == "unmapped_account"]
    assert [(e["severity"], e["subject"], e["file"]) for e in unmapped] == [("blocking", "6150 · Uncategorized Expenses", "trial_balance.csv")]
    with pytest.raises(BookflowError) as refused:
        run("cutover apply", dict(as_of=AS_OF, files=files))
    assert refused.value.code == "E_CUTOVER_BLOCKED"
    assert [e["subject"] for e in refused.value.details["exceptions"]] == ["6150 · Uncategorized Expenses"]
    mapped = {"accounts": {"6150 · Uncategorized Expenses": "6300"}}
    plan = run("cutover plan", dict(as_of=AS_OF, files=files, mappings=mapped))
    assert plan["ready"]
    office = run("account show", dict(account="Office Supplies"))["id"]
    assert plan["mappings"]["accounts"]["6150 · Uncategorized Expenses"] == office
    run("cutover apply", dict(as_of=AS_OF, files=files, mappings=mapped))
    assert run("cutover tie-out", dict(as_of=AS_OF, files=files, mappings=mapped))["tied"]
    assert run("account show", dict(account="Office Supplies"))["balance"]["minor_units"] == 114872
