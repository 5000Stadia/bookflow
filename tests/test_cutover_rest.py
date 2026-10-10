"""R179: the move-in brings the rest of the old books.

Harbor Electric (tests/fixtures/fakeco/) moves in at 2026-06-30 with, beside the lists, trial balance and
open documents, the June Reconciliation Summary of checking, savings and the Visa card, the transactions
the statements had not shown yet (three checks and a deposit on checking, two charges on the card), the
two customer checks waiting in Undeposited Funds (1,662.00) and the 1099 Summary for January to June
(Delgado, Ray: 8,450.00, of which check 4472 for 900.00 had not cleared). Figures are the fixture's own,
added up by hand: checking 21,604.12 on the statement, less 3,915.23 of outstanding checks, plus the
2,315.60 deposit in transit, is the trial balance's 20,004.49; the card's 2,486.17 owed plus 87.68 of
charges not yet posted is its 2,573.85.
"""
from pathlib import Path

import pytest

import bookflow
from bookflow import BookflowError
from bookflow.company import cutover_sources as src
from bookflow.core.ids import new_id
from tests import fakeco_replay as replay_module

OLD = Path(__file__).parent / "fixtures" / "fakeco" / "handed-over" / "old-books"
AS_OF = replay_module.AS_OF
MAPPINGS = replay_module.MAPPINGS
CORE = replay_module.CORE_FILES
SUMMARIES = ("reconciliation_summary_checking_2026-06.csv", "reconciliation_summary_savings_2026-06.csv",
             "reconciliation_summary_visa_2026-06.csv")
REST = SUMMARIES + ("uncleared_2026-06-30.csv", "undeposited_funds_2026-06-30.csv", "vendor_1099_summary_2026-06.csv")


def _text(name: str) -> str:
    return src.decode((OLD / name).read_bytes())


def _read(*named: tuple[str, str]) -> src.Sources:
    files = [src.SourceFile(name, None, src.digest(text), text) for name, text in named]
    return src.read(files, 2)


def _harbor(tmp_path, monkeypatch) -> replay_module.Replay:
    root = tmp_path / "root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    harbor = replay_module.Replay(root, client)
    harbor.create_company()
    return harbor


def _codes(result) -> set[tuple[str, str]]:
    return {(e["severity"], e["code"]) for e in result["exceptions"]}


def _counts(result) -> dict:
    return {row["kind"]: (row["create"], row["already_in"], row["matched"]) for row in result["counts"]}


def _parts(result) -> dict:
    return {p["part"]: (p["amount"]["minor_units"], p["records"]) for p in result["clearing"]["parts"]}


# ---------------------------------------------------------------- reading the files

def test_the_rest_of_the_old_books_reads_as_the_desktop_product_exports_it():
    sources = _read(*((name, _text(name)) for name in REST))
    assert [f.kind for f in sources.files] == ["reconciliation_summary"] * 3 + ["uncleared", "uncleared", "vendor_1099"]
    assert not sources.problems
    checking, savings, visa = sources.reconciliations
    # The statement's ending balance is the report's Cleared Balance; its Ending Balance is the register's.
    assert (checking.label, checking.path, checking.statement_date) == ("1000 · Checking", "Checking", "2026-06-30")
    assert (checking.cleared_balance, checking.uncleared_total, checking.uncleared_count, checking.ending_balance) == (
        2160412, -159963, 4, 2000449)
    assert (savings.cleared_balance, savings.uncleared_total) == (1851263, 0)
    assert (visa.cleared_balance, visa.uncleared_total, visa.uncleared_count) == (248617, 8768, 2)  # what is owed
    bank = [(i.path, i.type, i.date, i.num, i.name, i.amount) for i in sources.open_items if i.path != "Undeposited Funds"]
    assert bank == [
        ("Checking", "Check", "2026-06-25", "4471", "Midland Electric Supply", -290633),
        ("Checking", "Bill Pmt -Check", "2026-06-29", "4472", "Delgado, Ray", -90000),
        ("Checking", "Bill Pmt -Check", "2026-06-30", "4473", "Prairie Uniform Service", -10890),
        ("Checking", "Deposit", "2026-06-30", None, None, 231560),
        ("Visa Business Card", "Credit Card Charge", "2026-06-29", None, "Plainfield Hardware & Lumber", -6418),
        ("Visa Business Card", "Credit Card Charge", "2026-06-30", None, None, -2350)]
    receipts = [(i.type, i.num, i.name, i.amount) for i in sources.open_items if i.path == "Undeposited Funds"]
    assert receipts == [("Payment", "2207", "Schultz, Gary", 41287), ("Payment", "1043", "Nguyen, Linh", 124913)]
    report, = sources.vendor_1099
    assert (report.date_from, report.date_to, report.boxes) == ("2026-01-01", "2026-06-30", ["Box 1 Nonemployee Compensation"])
    assert [(row.vendor, row.total) for row in report.rows] == [("Delgado, Ray", 845000)]
    for heading, covered in (("January 1 through June 30, 2026", ("2026-01-01", "2026-06-30")),
                             ("December 2025 through June 2026", ("2025-12-01", "2026-06-30")),
                             ("June 2026", ("2026-06-01", "2026-06-30")), ("All Transactions", None)):
        assert src.date_range(heading) == covered, heading


def test_an_export_that_cannot_be_right_is_refused_by_line():
    summary = _text("reconciliation_summary_checking_2026-06.csv").replace('"Cleared Balance",,,"21,604.12"',
                                                                          '"Cleared Balance",,,"21,604.21"')
    cleared = _text("uncleared_2026-06-30.csv").replace('"S1185870.001, S1186212.001",,', '"S1185870.001, S1186212.001","X",')
    sources = _read(("summary.csv", summary), ("uncleared.csv", cleared))
    found = {(p.code, p.file) for p in sources.problems if p.severity == "blocking"}
    # The summary's own figures disagree (a retyped balance), and a row marked cleared shows the report was not
    # filtered to what had not cleared; the account's subtotal then no longer adds up either.
    assert {("total_mismatch", "summary.csv"), ("cleared_row", "uncleared.csv")} <= found


# ---------------------------------------------------------------- the move-in

@pytest.mark.timeout(900)
def test_the_rest_of_the_old_books_moves_in_ties_out_and_reruns_to_nothing(tmp_path, monkeypatch):
    harbor = _harbor(tmp_path, monkeypatch)
    run = harbor.run
    files = harbor.attach(CORE + REST)
    plan = run("cutover plan", dict(as_of=AS_OF, files=files, mappings=MAPPINGS))
    assert plan["ready"] and plan["blocking"] == [], plan["blocking"]
    assert _codes(plan) == {("warning", "account_number_dropped"), ("warning", "non_posting_accounts"),
                            ("warning", "possible_duplicate_vendor"), ("warning", "item_skipped"),
                            ("note", "account_number_differs"), ("note", "no_reconciliation"), ("note", "sales_tax_default")}
    # Petty Cash, a bank account with no reconciliation, still comes in as one amount, and the plan says so.
    assert "Petty Cash comes in as one opening amount" in next(e["problem"] for e in plan["exceptions"]
                                                                if e["code"] == "no_reconciliation")
    counts = _counts(plan)
    assert (counts["uncleared_item"], counts["undeposited_receipt"], counts["reconciliation_opening"],
            counts["vendor_1099_opening"]) == ((6, 0, 0), (2, 0, 0), (3, 0, 0), (1, 0, 0))
    # The opening journal carries each statement's ending balance; the uncleared items and receipts carry the rest,
    # each against Cutover Clearing, so it still nets to 0.00.
    lines = {(line["account"], line["side"]): line["amount"]["minor_units"] for line in plan["journal"]["lines"]}
    assert (lines[("Checking", "debit")], lines[("Savings", "debit")], lines[("Visa Business Card", "credit")]) == (
        2160412, 1851263, 248617)
    assert not any(account == "Undeposited Funds" for account, _ in lines)
    parts = _parts(plan)
    assert parts["uncleared_items"] == (290633 + 90000 + 10890 - 231560 + 6418 + 2350, 6)
    assert parts["undeposited_receipts"] == (-166200, 2) and plan["clearing"]["net"]["minor_units"] == 0
    steps = {step["outside_id"]: step for step in plan["steps"]}
    check = next(step for step in plan["steps"] if step["name"].startswith("Bill Pmt -Check 4472"))
    assert (check["command"], check["date"], check["amount"]["minor_units"]) == ("check post", "2026-06-29", 90000)
    deposit = next(step for step in plan["steps"] if step["name"].startswith("Deposit ·"))
    assert deposit["command"] == "register post" and "deposit on Checking" in deposit["detail"]
    ten99 = steps["1099:delgado, ray:2026"]
    # The old books' 8,450.00 less check 4472, which the move-in brings and the 1099 summary counts on its own date.
    assert (ten99["command"], ten99["amount"]["minor_units"]) == ("vendor 1099-opening", 755000)
    assert "less 900.00 of uncleared checks" in ten99["detail"]

    applied = run("cutover apply", dict(as_of=AS_OF, files=files, mappings=MAPPINGS), reason="Move in from the old books")
    assert (applied["created"], applied["already_in"]) == (sum(c[0] for c in counts.values()), 0)
    again = run("cutover apply", dict(as_of=AS_OF, files=files, mappings=MAPPINGS), reason="Move in again, to be safe")
    assert again["created"] == 0 and again["already_in"] == applied["created"]

    tie = run("cutover tie-out", dict(as_of=AS_OF, files=files + harbor.attach(replay_module.AGING_FILES), mappings=MAPPINGS))
    assert tie["tied"], tie["summary"]
    bank = {row["name"]: row for row in tie["bank"]["rows"]}
    money = lambda row, *names: tuple(row[name]["minor_units"] for name in names)
    assert money(bank["Checking"], "statement_balance", "uncleared_increase", "uncleared_decrease", "books_balance") == (
        2160412, 231560, 391523, 2000449)
    assert money(bank["Visa Business Card"], "statement_balance", "uncleared_increase", "books_balance") == (248617, 8768, 257385)
    assert all((row["opening"], row["opening_proven"], row["items_source"] == row["items_books"]) == ("draft", True, True)
               for row in bank.values())
    assert (tie["undeposited"]["source_total"]["minor_units"], tie["undeposited"]["books_total"]["minor_units"]) == (166200, 166200)
    assert [(row["name"], row["source"]["minor_units"], row["books"]["minor_units"]) for row in tie["vendor_1099"]["rows"]] == [
        ("Delgado, Ray", 845000, 845000)]

    # Customers keep their sales tax item; the church stays exempt; a job takes its customer's.
    items = {row["id"]: row["full_name"] for row in run("item list")["items"]}
    codes = {row["id"]: row["code"] for row in run("sales-tax-code list")["items"]}
    ruth, church = run("customer show", dict(customer="Abernathy, Ruth")), run("customer show", dict(customer="St. Casimir Parish"))
    job = run("customer show", dict(customer="Lakeview Property Management:Cass Street Apartments"))
    assert (items[ruth["sales_tax_item_id"]], codes[ruth["sales_tax_code_id"]]) == ("IL Sales Tax", "Tax")
    assert (church["sales_tax_item_id"], codes[church["sales_tax_code_id"]]) == (None, "Non")
    assert job["sales_tax_item_id"] is None and items[job["effective_sales_tax_item_id"]] == "IL Sales Tax"

    # The first statement after the move-in opens from the June reconciliation, naming no opening.
    for account, june in (("Checking", 2160412), ("Savings", 1851263), ("Visa Business Card", 248617)):
        draft = run("reconcile start", dict(operation_key=new_id(), account=account, statement_date="2026-07-31",
                                            ending_balance="100.00"))["draft"]
        totals = run("reconcile preview", dict(draft=draft["id"], expected_version=draft["version"]))["totals"]
        assert totals["beginning_balance"] == june, account

    # The year so far is the old books' figure, whatever the dates read show of it.
    year = run("report vendor-1099-summary", dict(date_from="2026-01-01", date_to="2026-12-31"))
    assert [(row["display_vendor_label"], row["payments"]["minor_units"], row["opening_payments"]["minor_units"])
            for row in year["rows"]] == [("Delgado, Ray", 845000, 755000)]

    # The two checks wait in Undeposited Funds and the July 1 deposit picks them.
    waiting = run("deposit sources", dict(date="2026-07-01"))["items"]
    assert sorted((row["received_from"], row["reference"], row["amount"]["minor_units"]) for row in waiting) == [
        ("Nguyen, Linh", "1043", 124913), ("Schultz, Gary", "2207", 41287)]
    run("deposit post", dict(operation_key=new_id(), document=dict(mode="inline", deposit_to="Checking", date="2026-07-01",
        sources=[dict(source_type=row["source_type"], source=row["source"], expected_version=row["expected_version"])
                 for row in waiting])))
    rows = run("report trial-balance", dict(date_to="2026-07-01", limit=200))["rows"]
    assert sum(row["signed_net"]["minor_units"] for row in rows if row["display_account_label"].endswith("Undeposited Funds")) == 0
