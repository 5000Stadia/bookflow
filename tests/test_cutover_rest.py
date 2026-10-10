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


@pytest.mark.timeout(900)
def test_without_the_new_files_the_banks_come_in_as_one_amount_and_undeposited_funds_as_one_receipt(tmp_path, monkeypatch):
    harbor = _harbor(tmp_path, monkeypatch)
    run = harbor.run
    files = harbor.attach(CORE)
    plan = run("cutover plan", dict(as_of=AS_OF, files=files, mappings=MAPPINGS))
    assert plan["ready"], plan["blocking"]
    lumps = next(e["problem"] for e in plan["exceptions"] if e["code"] == "no_reconciliation")
    assert all(name in lumps for name in ("Checking", "Petty Cash", "Savings", "Visa Business Card"))
    lines = {(line["account"], line["side"]): line["amount"]["minor_units"] for line in plan["journal"]["lines"]}
    assert (lines[("Checking", "debit")], lines[("Visa Business Card", "credit")]) == (2000449, 257385)  # the trial balance's
    counts = _counts(plan)
    assert "uncleared_item" not in counts and "reconciliation_opening" not in counts and "vendor_1099_opening" not in counts
    # Undeposited Funds is one receipt Make Deposits picks whole, not a journal line it cannot.
    assert counts["undeposited_receipt"] == (1, 0, 0) and not any(account == "Undeposited Funds" for account, _ in lines)
    assert ("warning", "undeposited_funds") in _codes(plan)
    run("cutover apply", dict(as_of=AS_OF, files=files, mappings=MAPPINGS), reason="Move in from the old books")
    tie = run("cutover tie-out", dict(as_of=AS_OF, files=files, mappings=MAPPINGS))
    assert tie["tied"], tie["summary"]
    assert tie["bank"]["rows"] == [] and tie["vendor_1099"]["rows"] == []
    waiting = run("deposit sources", dict(date="2026-07-01"))["items"]
    assert [(row["received_from"], row["amount"]["minor_units"]) for row in waiting] == [("Opening balance", 166200)]


_JUNE_27 = '''"Harbor Electric LLC"
"Reconciliation Summary"
"1000 · Checking, Period Ending 06/27/2026"
,,,"Jun 27, 26"
"Beginning Balance",,,"18,402.77"
,"Cleared Transactions",,
,,"Checks and Payments - 38 items","-41,522.14"
,,"Deposits and Credits - 14 items","44,723.49"
,"Total Cleared Transactions",,"3,201.35"
"Cleared Balance",,,"21,604.12"
,"Uncleared Transactions",,
,,"Checks and Payments - 1 item","-2,906.33"
,"Total Uncleared Transactions",,"-2,906.33"
"Register Balance as of 06/27/2026",,,"18,697.79"
,"New Transactions",,
,,"Checks and Payments - 2 items","-1,008.90"
,,"Deposits and Credits - 1 item","2,315.60"
,"Total New Transactions",,"1,306.70"
"Ending Balance",,,"20,004.49"
'''


@pytest.mark.timeout(900)
def test_a_statement_ending_before_the_cutover_opens_the_first_reconciliation_on_its_own_date(tmp_path, monkeypatch):
    harbor = _harbor(tmp_path, monkeypatch)
    run = harbor.run
    files = harbor.attach(CORE + ("uncleared_2026-06-30.csv",)) + [{"content": _JUNE_27, "name": "checking_summary.csv"}]
    plan = run("cutover plan", dict(as_of=AS_OF, files=files, mappings=MAPPINGS))
    assert plan["ready"], plan["blocking"]
    # The statement's balance is dated the statement's own day, as the anchor's opening balance is; the checks
    # and deposit after it are new transactions the first reconciliation clears.
    statement = next(step for step in plan["steps"] if step["outside_id"] == "journal:statement:2026-06-27")
    assert (statement["date"], statement["action"]) == ("2026-06-27", "create")
    opening = next(step for step in plan["steps"] if step["kind"] == "reconciliation_opening")
    assert opening["date"] == "2026-06-27" and "1 uncleared item outstanding" in opening["detail"]
    applied = run("cutover apply", dict(as_of=AS_OF, files=files, mappings=MAPPINGS), reason="Move in from the old books")
    tie = run("cutover tie-out", dict(as_of=AS_OF, files=files, mappings=MAPPINGS))
    assert tie["tied"], tie["summary"]
    checking, = [row for row in tie["bank"]["rows"] if row["name"] == "Checking"]
    assert (checking["statement_date"], checking["opening"], checking["opening_proven"]) == ("2026-06-27", "draft", True)
    draft = run("reconcile start", dict(operation_key=new_id(), account="Checking", statement_date="2026-07-25",
                                        ending_balance="100.00"))["draft"]
    totals = run("reconcile preview", dict(draft=draft["id"], expected_version=draft["version"]))["totals"]
    assert totals["beginning_balance"] == 2160412 and applied["created"] > 0


@pytest.mark.timeout(900)
def test_files_that_disagree_with_the_trial_balance_or_the_cutover_block_the_move_in(tmp_path, monkeypatch):
    harbor = _harbor(tmp_path, monkeypatch)
    run = harbor.run
    core = harbor.attach(CORE)
    summaries = harbor.attach(SUMMARIES)
    plan = lambda *extra: run("cutover plan", dict(as_of=AS_OF, files=core + list(extra), mappings=MAPPINGS))
    blocking = lambda result: {e["code"] for e in result["exceptions"] if e["severity"] == "blocking"}
    # The June summaries say checks and charges were uncleared and no uncleared items were given.
    missing = plan(*summaries)
    assert not missing["ready"] and blocking(missing) == {"missing_uncleared_items"}
    # The uncleared items without the deposit in transit (totals made to match): checking no longer ties.
    short = (_text("uncleared_2026-06-30.csv").replace(',,"Deposit","06/30/2026",,,"Bella Notte 1622.40, Patel 693.20",,"2,315.60","-1,599.63"\r\n', '')
             .replace('"Total 1000 · Checking",,,,,,,,"-1,599.63","-1,599.63"', '"Total 1000 · Checking",,,,,,,,"-3,915.23","-3,915.23"')
             .replace('"TOTAL",,,,,,,,"-1,687.31","-1,687.31"', '"TOTAL",,,,,,,,"-4,002.91","-4,002.91"'))
    untied = plan(*summaries, {"content": short, "name": "uncleared.csv"})
    assert blocking(untied) == {"uncleared_do_not_tie"} and "Checking" in untied["blocking"][0]
    # A receipt retyped: Undeposited Funds no longer ties.
    retyped = _text("undeposited_funds_2026-06-30.csv").replace("1,249.13", "1,249.31").replace("1,662.00", "1,662.18")
    assert blocking(plan({"content": retyped, "name": "undeposited.csv"})) == {"undeposited_do_not_tie"}
    # A 1099 Summary that stops a month short of the cutover.
    may = _text("vendor_1099_summary_2026-06.csv").replace("January through June 2026", "January through May 2026")
    assert blocking(plan({"content": may, "name": "1099.csv"})) == {"vendor_1099_dates"}
    # Files checked on their own tie to nothing yet: they plan what they can, block on nothing, and the plan
    # says once that apply needs the trial balance. A 1099 vendor no list names comes in eligible for a 1099.
    pieces = run("cutover plan", dict(as_of=AS_OF, files=harbor.attach(("reconciliation_summary_checking_2026-06.csv",
                                                                        "uncleared_2026-06-30.csv"))))
    assert (pieces["ready"], pieces["blocking"]) == (False, []) and ("note", "no_trial_balance") in _codes(pieces)
    assert (_counts(pieces)["uncleared_item"], _counts(pieces)["reconciliation_opening"]) == ((4, 0, 0), (1, 0, 0))
    alone = run("cutover plan", dict(as_of=AS_OF, files=harbor.attach(("vendor_1099_summary_2026-06.csv",))))
    assert alone["blocking"] == [] and (_counts(alone)["vendor"], _counts(alone)["vendor_1099_opening"]) == ((1, 0, 0), (1, 0, 0))
    assert next(step for step in alone["steps"] if step["kind"] == "vendor_1099_opening")["amount"]["minor_units"] == 845000


def test_an_opening_1099_amount_is_set_replaced_and_cleared_and_counts_by_its_date(client):
    run = lambda name, data, **kw: client.run(name, data, company="Demo Plumbing Co", **kw)
    vendor = "Summit Pipe Contracting"  # 1099-eligible: a 2,400.00 check on 2026-12-08
    first = run("vendor 1099-opening", dict(vendor=vendor, year=2026, as_of="2026-06-30", amount="1500.00"), reason="Old books")
    assert (first["changed"], first["version"], first["amount"]["minor_units"]) == (True, 1, 150000)
    year = lambda frm, to: {row["display_vendor_label"]: (row["payments"]["minor_units"], row["opening_payments"]["minor_units"])
                            for row in run("report vendor-1099-summary", dict(date_from=frm, date_to=to,
                                                                              above_threshold_only=False))["rows"]}
    assert year("2026-01-01", "2026-12-31")[vendor] == (390000, 150000)
    assert year("2026-07-01", "2026-12-31")[vendor] == (240000, 0)  # paid through June 30: outside these dates
    with pytest.raises(BookflowError) as stale:
        run("vendor 1099-opening", dict(vendor=vendor, year=2026, as_of="2026-06-30", amount="1600.00", expected_version=0),
            reason="Old books")
    assert stale.value.code == "E_VERSION_CONFLICT"
    replaced = run("vendor 1099-opening", dict(vendor=vendor, year=2026, as_of="2026-06-30", amount="1600.00", expected_version=1),
                   reason="Corrected from the 1099 Summary")
    assert (replaced["id"], replaced["version"]) == (first["id"], 2)
    for bad, field in ((dict(vendor="Central Supply", year=2026, as_of="2026-06-30", amount="1.00"), "vendor"),
                       (dict(vendor=vendor, year=2026, as_of="2025-12-31", amount="1.00"), "as_of")):
        with pytest.raises(BookflowError) as refused:
            run("vendor 1099-opening", bad, reason="Old books")
        assert refused.value.code == "E_VALIDATION" and field in str(refused.value.details), bad
    cleared = run("vendor 1099-opening", dict(vendor=vendor, year=2026, as_of="2026-06-30", amount="0.00"), reason="Not paid after all")
    assert cleared["changed"] and year("2026-01-01", "2026-12-31")[vendor] == (240000, 0)


# ---------------------------------------------------------------- July after the move-in

class _RestOfTheBooks(replay_module.Replay):
    """The fit check's July (tests/fakeco_replay.py), kept after a move-in that brings the rest of the old books:
    no uncleared item is entered again, no June statement balance is typed, and the July 1 deposit picks the two
    checks waiting in Undeposited Funds."""

    def move_in(self) -> dict:
        files = self.attach(CORE + REST)
        plan = self.run("cutover plan", dict(as_of=AS_OF, files=files, mappings=MAPPINGS))
        assert plan["ready"], plan["blocking"]
        applied = self.run("cutover apply", dict(as_of=AS_OF, files=files, mappings=MAPPINGS), reason="Move in from the old books")
        tie = self.run("cutover tie-out", dict(as_of=AS_OF, files=files + self.attach(replay_module.AGING_FILES), mappings=MAPPINGS))
        self.cutover = dict(plan=plan, applied=applied, tie=tie)
        self.journals = {step["record_id"] for step in applied["steps"] if step["kind"] == "journal"}
        self.openings = {step["name"].split(" · ", 1)[1]: step["record_id"] for step in applied["steps"]
                         if step["kind"] == "reconciliation_opening"}
        return self.cutover

    def opening_detail(self):
        """Nothing to enter again: the move-in brought each uncleared item and the June reconciliation."""

    def _deposit(self, ev: dict):
        if not all(row["source"].startswith("uf:") for row in ev["items"]):
            return super()._deposit(ev)
        names = {row["source"][3:] for row in ev["items"]}
        picked = [row for row in self.run("deposit sources", dict(date=ev["date"]))["items"] if row["received_from"] in names]
        assert len(picked) == len(ev["items"]), picked
        self.made[ev["id"]] = self.run("deposit post", dict(operation_key=new_id(), document=dict(
            mode="inline", deposit_to="Checking", date=ev["date"],
            sources=[dict(source_type=row["source_type"], source=row["source"], expected_version=row["expected_version"])
                     for row in picked])))

    def open_reconciliation(self, account: str, statement: str, ending: str, june: str) -> dict:
        opening = next(draft for label, draft in self.openings.items() if label.endswith(account))
        self.covered = {row["group_fingerprint"] for row in self.candidates(opening)
                        if row["movement"]["transaction_id"] in self.journals}
        return self.run("reconcile start", dict(operation_key=new_id(), account=account, statement_date=statement,
                                                ending_balance=ending))["draft"]


@pytest.mark.timeout(900)
def test_july_after_the_move_in_reconciles_to_its_statements_with_nothing_entered_again(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    harbor = _RestOfTheBooks(root, client)
    month = harbor.keep_july()
    assert harbor.cutover["tie"]["tied"], harbor.cutover["tie"]["summary"]
    assert replay_module.differences(harbor.books("2026-07-31"), replay_module.expected("2026-07-31")) == []
    key = replay_module.key("2026-07-31")["reconciliations"]
    for account, done in month["reconciliations"].items():
        want, totals = key[account], done["finish"]["totals"]
        assert totals["difference"] == 0, account
        assert (totals["beginning_balance"], totals["ending_balance"]) == (
            replay_module.cents(want["beginning_balance"]), replay_module.cents(want["ending_balance"])), account
        assert sorted(abs(row["amount"]) for row in done["outstanding"]) == sorted(
            abs(replay_module.cents(row["amount"])) for row in want["outstanding"]), account
    # June's three checks clear on their own numbers, and nothing is ticked by hand.
    checking = month["reconciliations"]["Checking"]
    assert checking["by_hand"] == []
    assert not [line for line in checking["first"]["lines"] if line["status"] == "unmatched" and line["number"]]
    # The year's 1099 summary is the whole year's: January to June from the old books, July from Bookflow.
    year = harbor.run("report vendor-1099-summary", dict(date_from="2026-01-01", date_to="2026-12-31"))
    assert [(row["display_vendor_label"], row["payments"]["minor_units"], row["opening_payments"]["minor_units"])
            for row in year["rows"]] == [("Delgado, Ray", 845000 + 255000, 755000)]
    # Once July is reconciled and closed, the move-in run again makes nothing, and what it brought still ties as of
    # the cutover: each opening is now certified, the receipts were deposited, the 1099 figure stands.
    files = harbor.cutover["plan"]["files"]
    given = [{"attachment": f["attachment"]} for f in files]
    again = harbor.run("cutover apply", dict(as_of=AS_OF, files=given, mappings=MAPPINGS), reason="Run the move-in again")
    assert again["created"] == 0 and "reconciliation_exists" not in {e["code"] for e in again["exceptions"]}
    tie = harbor.run("cutover tie-out", dict(as_of=AS_OF, files=given, mappings=MAPPINGS))
    assert [(row["name"], row["opening"], row["tied"]) for row in tie["bank"]["rows"]] == [
        ("Checking", "certified", True), ("Savings", "certified", True), ("Visa Business Card", "certified", True)]
    assert (tie["undeposited"]["differences"], tie["vendor_1099"]["differences"], tie["trial_balance"]["differences"]) == (0, 0, 0)
