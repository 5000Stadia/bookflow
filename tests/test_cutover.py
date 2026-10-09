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
                             "vendor_credit": (1, 0, 0), "inventory_adjustment": (2, 0, 0),
                             "sales_tax_adjustment": (1, 0, 0), "journal": (1, 0, 0), "deactivation": (1, 0, 0)}
    # The old books' sales tax items name one agency, so the 1,036.59 of sales tax they owe comes in
    # as that agency's balance (R175) and the plan has nothing to warn about it.
    assert {(e["severity"], e["code"]) for e in plan["exceptions"]} == {
        ("warning", "non_posting_accounts"), ("warning", "item_skipped"), ("note", "account_number_differs"),
        ("note", "sales_tax_default")}
    assert "IL Sales Tax" in next(e for e in plan["exceptions"] if e["code"] == "sales_tax_default")["problem"]
    tax_step = next(step for step in plan["steps"] if step["kind"] == "sales_tax_adjustment")
    assert (tax_step["outside_id"], tax_step["name"]) == (f"sales-tax:{AS_OF}", "Opening sales tax · Illinois Department of Revenue")
    assert {row["kind"]: row["amount"]["minor_units"] for row in plan["counts"] if row["amount"]}["sales_tax_adjustment"] == 103659
    number = next(e for e in plan["exceptions"] if e["code"] == "account_number_differs")
    assert number["subject"] == "3900 · Retained Earnings" and "3100" in number["problem"]
    # The run at a glance comes first, totals by kind: invoices and credits net to AR, bills and
    # the vendor credit to AP, stock to Inventory Asset.
    assert list(plan)[:9] == ["dry_run", "warnings", "as_of", "ready", "source", "summary", "blocking", "counts", "exceptions"]
    totals = {row["kind"]: row["amount"]["minor_units"] for row in plan["counts"] if row["amount"]}
    assert totals["invoice"] - totals["credit_memo"] == 2509905
    assert totals["bill"] - totals["vendor_credit"] == 984590
    assert totals["inventory_adjustment"] == 167500
    assert {f["name"]: (f["decided_by"], bool(f["attachment"])) for f in plan["files"]}["accounts.iif"] == ("headings", True)
    journal = plan["journal"]
    assert not {line["account"] for line in journal["lines"]} & {
        "Accounts Receivable", "Accounts Payable", "Inventory Asset", "Sales Tax Payable"}
    # The clearing line is what the documents, stock and opening sales tax carry: AR less AP plus
    # inventory, less the sales tax the adjustment credits to the agency.
    assert journal["clearing"]["minor_units"] == 2509905 - 984590 + 167500 - 103659
    # v1.6: the clearing account's parts, in plan and apply alike, net to 0.00.
    parts = {"opening_journal": (2509905 - 984590 + 167500 - 103659, 1), "invoices_and_credit_memos": (-2509905, 12),
             "bills_and_vendor_credits": (984590, 7), "opening_stock": (-167500, 2), "opening_sales_tax": (103659, 1)}
    clearing_parts = lambda result: {p["part"]: (p["amount"]["minor_units"], p["records"]) for p in result["clearing"]["parts"]}
    assert clearing_parts(plan) == parts and plan["clearing"]["net"]["minor_units"] == 0
    assert plan["clearing"]["account"] == "Cutover Clearing" and plan["blocking"] == []

    applied = run("cutover apply", dict(as_of=AS_OF, files=files), reason="Move in from QuickBooks")
    assert (applied["created"], applied["already_in"]) == (76, 0)
    assert clearing_parts(applied) == parts and applied["clearing"]["account_id"] == applied["clearing_account_id"]
    tie = run("cutover tie-out", dict(as_of=AS_OF, files=files))
    assert tie["tied"], tie
    assert tie["clearing"]["minor_units"] == 0
    assert (tie["trial_balance"]["source_total"]["minor_units"], tie["trial_balance"]["books_total"]["minor_units"]) == (29888793, 29888793)
    assert tie["receivables"]["source"] == "A/R Aging Summary"
    assert (tie["receivables"]["source_total"]["minor_units"], tie["receivables"]["books_total"]["minor_units"]) == (2509905, 2509905)
    assert (tie["payables"]["source_total"]["minor_units"], tie["payables"]["books_total"]["minor_units"]) == (984590, 984590)
    assert (tie["inventory"]["source_total"]["minor_units"], tie["inventory"]["books_total"]["minor_units"]) == (167500, 167500)
    # Every list record compared matches; the differences by design are notes.
    assert (tie["lists"]["compared"], tie["lists"]["differences"]) == (74, 0)
    assert {(n["list"], n["name"], n["field"], n["source"], n["books"]) for n in tie["lists"]["notes"]} == {
        ("account", "Retained Earnings", "number", "3900", "3100"),
        ("item", "Faucet Install Kit", "missing", "in the old books", None)}

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
    moved = [e for e in events if (e["source_ref"] or "").startswith("cutover:")]
    # One event per record made, and one per export given as text, kept as an attachment.
    # ... and the one `company update` that makes the only sales tax item the company's default.
    assert len(moved) == 76 + 8 + 1
    assert [e["command"] for e in moved if e["source_ref"] == "cutover:default-sales-tax-item"] == ["company update"]
    assert sorted(e["command"] for e in moved if e["source_ref"].startswith("cutover:file:")) == ["attachment add"] * 8
    assert run("customer show", dict(customer="Patel, Anita"))["active"] is False


def test_the_opening_sales_tax_is_the_agencys_and_sales_tax_pay_settles_it(mover):
    """R175: one agency in the old books, so what they owe it is that agency's balance here."""
    run, files = mover["run"], mover["files"]
    run("cutover apply", dict(as_of=AS_OF, files=files), reason="Move in from QuickBooks")
    owed = run("sales-tax liability", dict(as_of=AS_OF))
    row, = owed["rows"]
    assert row["display_agency_label"] == "Illinois Department of Revenue" and row["is_tax_agency"]
    assert (row["adjusted"]["minor_units"], row["unattributed"]["minor_units"], row["balance"]["minor_units"]) == (103659, 0, 103659)
    assert run("account show", dict(account="Sales Tax Payable"))["balance"]["minor_units"] == 103659
    adjustment, = run("sales-tax adjustment query", {})["items"]
    assert (adjustment["date"], adjustment["direction"], adjustment["adjustment_account_name"]) == (AS_OF, "increase", "Cutover Clearing")
    assert run("cutover tie-out", dict(as_of=AS_OF, files=files))["tied"]
    paid = run("sales-tax pay", dict(agency="Illinois Department of Revenue", date="2026-10-20", through_date=AS_OF,
                                    funding_account="Checking", method="Check", memo="Q3 sales tax"), reason="Pay Q3 sales tax")
    assert paid["total"]["minor_units"] == 103659 and paid["remainder_at_posting"]["minor_units"] == 0
    assert run("sales-tax liability", dict(as_of="2026-10-31"))["totals"]["balance"]["minor_units"] == 0
    # The tie-out is as of the cutover date, before the payment, so it still ties.
    assert run("cutover tie-out", dict(as_of=AS_OF, files=files))["tied"]


def test_two_agencies_keep_the_opening_sales_tax_in_the_journal_with_a_warning(mover):
    run, files = mover["run"], mover["files"]
    items = text("items.iif")
    line = next(row for row in items.splitlines() if row.startswith("INVITEM\tIL Sales Tax\t"))
    second = line.replace("IL Sales Tax", "City Sales Tax", 1).replace("Illinois Department of Revenue", "City of Riverbend", 1)
    given = [{"content": items.replace(line, line + "\r\n" + second), "name": "items.iif"} if f.get("name") == "items.iif" else f
             for f in files]
    plan = run("cutover plan", dict(as_of=AS_OF, files=given))
    assert not [step for step in plan["steps"] if step["kind"] == "sales_tax_adjustment"]
    tax = next(e for e in plan["exceptions"] if e["code"] == "sales_tax_payable")
    assert "1,036.59" in tax["problem"] and "not tied to a tax agency" in tax["problem"]
    assert tax["fix"] == "Pay it to the agency with a check (or a journal entry) whose account is Sales Tax Payable."
    assert "Sales Tax Payable" in {line["account"] for line in plan["journal"]["lines"]}
    assert plan["journal"]["clearing"]["minor_units"] == 2509905 - 984590 + 167500
    # The sales tax is in the journal's part, and no opening_sales_tax part: still nets to 0.00.
    assert [p["part"] for p in plan["clearing"]["parts"]] == [
        "opening_journal", "invoices_and_credit_memos", "bills_and_vendor_credits", "opening_stock"]
    assert plan["clearing"]["net"]["minor_units"] == 0


def test_a_rerun_makes_nothing_and_a_retry_replays(mover):
    run, files = mover["run"], mover["files"]
    first = run("cutover apply", dict(as_of=AS_OF, files=files), idempotency_key="move-in-1")
    # The only sales tax item becomes the company's default, and the result says so (the blind July
    # trial's first invoice failed for want of one).
    assert first["default_sales_tax_item"] == "IL Sales Tax" and "company's default" in first["summary"]
    assert run("company show")["info"]["default_sales_tax_item_id"] == run("item show", dict(item="IL Sales Tax"))["id"]
    before = _balances(run)
    retry = run("cutover apply", dict(as_of=AS_OF, files=files), idempotency_key="move-in-1")
    assert retry["idempotent_replay"] is True and retry["created"] == first["created"] == 76
    # The eight files given as text were kept as attachments; every later call passes ids.
    kept = {f["name"]: f["attachment"] for f in first["files"]}
    assert len(kept) == 10 and all(kept.values())
    attachments = {a["attachment"]["original_filename"] for a in run("attachment list", dict(
        record_type="company_info", record_id=run("company show")["company_id"], limit=50))["items"]}
    assert set(FILES) <= attachments
    by_id = [{"attachment": kept[name]} for name in FILES]
    plan = run("cutover plan", dict(as_of=AS_OF, files=by_id))
    assert plan["ready"] and {f["name"]: f["attachment"] for f in plan["files"]} == kept
    again = run("cutover apply", dict(as_of=AS_OF, files=by_id))
    assert (again["created"], again["already_in"]) == (0, 76) and again["default_sales_tax_item"] is None
    assert all(step["action"] == "already_in" for step in again["steps"])
    text_again = run("cutover plan", dict(as_of=AS_OF, files=files))
    assert {f["name"]: f["attachment"] for f in text_again["files"]} == kept  # text an earlier run kept shows its id
    assert _balances(run) == before
    assert run("cutover tie-out", dict(as_of=AS_OF, files=by_id))["tied"]


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


def _shift(name, who, old, new):
    """The export with one row retyped: `old` replaced by `new` inside the row that starts with `who`."""
    original = text(name)
    line = next(line for line in original.split("\r\n") if line.startswith(who))
    assert old in line
    return original.replace(line, line.replace(old, new, 1))


def test_a_retyped_list_row_with_a_tab_too_many_or_too_few_is_refused(mover):
    run = mover["run"]
    others = [f for name, f in zip(FILES, mover["files"]) if name not in ("customers.iif", "items.iif")]
    # The blind trial's mistake: one tab too many in a run of empty fields moved Anita Patel's
    # HIDDEN Y into DELCOUNT, and one too few moved a job's status out of JOBSTATUS.
    extra = _shift("customers.iif", "CUST\tPatel, Anita", "\t\t\t", "\t\t\t\t")
    dropped = _shift("items.iif", "INVITEM\tLabor", "\t\t", "\t")
    plan = run("cutover plan", dict(as_of=AS_OF, files=others + [
        {"content": extra, "name": "customers.iif"}, {"content": dropped, "name": "items.iif"}]))
    assert not plan["ready"]
    refused = {(e["code"], e["file"], e["line"], e["subject"]) for e in plan["exceptions"] if e["severity"] == "blocking"}
    assert ("row_width", "customers.iif", 13, "Patel, Anita") in refused
    # Each blocking exception is also one compact line naming file, line and problem, leading the plan.
    assert len(plan["blocking"]) == len(refused)
    assert any(line.startswith("customers.iif line 13: row_width: ") and "Patel, Anita" in line for line in plan["blocking"])
    shifted = next(e for e in plan["exceptions"] if e["code"] == "row_shifted")
    assert (shifted["file"], shifted["line"], shifted["subject"]) == ("items.iif", 5, "Labor")
    assert "HIDDEN" in shifted["problem"] and "Export the list again" in shifted["fix"]
    with pytest.raises(BookflowError) as caught:
        run("cutover apply", dict(as_of=AS_OF, files=others + [{"content": extra, "name": "customers.iif"}]))
    assert caught.value.code == "E_CUTOVER_BLOCKED"


def test_a_term_here_that_means_something_else_is_caught(mover):
    run, files = mover["run"], mover["files"]
    net30 = run("term show", dict(term="Net 30"))
    run("term update", dict(term=net30["id"], expected_version=net30["version"], due_days=25))
    plan = run("cutover plan", dict(as_of=AS_OF, files=files))
    differ = [e for e in plan["exceptions"] if e["code"] == "term_settings_differ"]
    assert [(e["severity"], e["subject"]) for e in differ] == [("blocking", "Net 30")]
    assert "due in days 25 here, 30 in the old books" in differ[0]["problem"]


def test_the_tie_out_lists_list_fields_and_stock_that_do_not_match(mover):
    run, files = mover["run"], mover["files"]
    applied = run("cutover apply", dict(as_of=AS_OF, files=files))
    patel = run("customer show", dict(customer="Patel, Anita"))
    run("customer activate", dict(customer=patel["id"], expected_version=patel["version"]))
    boiler = run("customer show", dict(customer="Harbor View Apartments:Boiler Replacement"))
    run("customer update", dict(customer=boiler["id"], expected_version=boiler["version"], job_status="in_progress"))
    clearing = applied["clearing_account_id"]
    run("inventory adjust", dict(item="Water Heater 40 gal", date=AS_OF, adjustment_account=clearing,
                                 quantity_change="1", value_change="450.00"))
    tie = run("cutover tie-out", dict(as_of=AS_OF, files=files))
    assert not tie["tied"]
    assert {(r["list"], r["name"], r["field"], r["source"], r["books"]) for r in tie["lists"]["rows"]} == {
        ("customer", "Patel, Anita", "active", "no", "yes"),
        ("customer", "Harbor View Apartments:Boiler Replacement", "job_status", "closed", "in_progress")}
    assert tie["inventory"]["differences"] == 1
    heater = tie["inventory"]["rows"][0]
    assert (heater["name"], heater["source_quantity"], heater["books_quantity"]) == ("Water Heater 40 gal", "3", "4")
    assert (heater["source_value"]["minor_units"], heater["books_value"]["minor_units"]) == (135000, 180000)
    assert "1 stock difference" in tie["summary"] and "2 list differences" in tie["summary"]


def test_the_essentials_survive_an_mcp_result_cut_to_size(mover):
    from bookflow.adapters.mcp import budget
    plan = mover["run"]("cutover plan", dict(as_of=AS_OF, files=mover["files"]))
    cut = budget.fit(plan)
    assert "result_compacted" in cut and len(cut["steps"]) < len(plan["steps"])
    assert cut["counts"] == plan["counts"] and cut["exceptions"] == plan["exceptions"]
    assert {row["kind"] for row in cut["counts"]} >= {"invoice", "credit_memo", "bill", "inventory_adjustment", "journal"}


def test_files_planned_in_pieces_get_one_note_for_the_trial_balance_and_one_for_the_account_list(mover):
    """v1.6, the cutover trial's third run: a partial plan blocked on the missing trial balance every
    time, and the item list alone said each item's account "is not an account that comes in"."""
    run = mover["run"]
    plan = run("cutover plan", dict(as_of=AS_OF, files=[{"content": text("items.iif"), "name": "items.iif"},
                                                         {"content": text("vendors.iif"), "name": "vendors.iif"}]))
    assert not plan["ready"] and plan["blocking"] == []
    assert plan["summary"] == "checked 2 files on their own: nothing in them blocks; `cutover apply` needs the trial balance with all the files"
    codes = [(e["severity"], e["code"]) for e in plan["exceptions"]]
    assert ("note", "no_trial_balance") in codes and ("warning", "no_account_list") in codes
    assert not any("is not an account that comes in" in e["problem"] for e in plan["exceptions"])
    missing, = [e for e in plan["exceptions"] if e["code"] == "no_account_list"]
    assert "no chart of accounts" in missing["problem"] and "'Service Income'" in missing["problem"]
    # With the chart of accounts the items come in, and apply still needs the trial balance.
    pieces = [{"content": text(name), "name": name} for name in ("accounts.iif", "items.iif", "vendors.iif")]
    plan = run("cutover plan", dict(as_of=AS_OF, files=pieces))
    assert "no_account_list" not in {e["code"] for e in plan["exceptions"]}
    with pytest.raises(BookflowError) as refused:
        run("cutover apply", dict(as_of=AS_OF, files=pieces))
    assert refused.value.code == "E_CUTOVER_BLOCKED"
    assert [e["code"] for e in refused.value.details["exceptions"]] == ["no_trial_balance"]


def test_blocking_lines_lead_and_exceptions_outlast_steps_when_a_result_is_cut():
    from bookflow.adapters.mcp.budget import BUDGET, fit, size
    exceptions = [{"severity": "blocking", "code": "row_width", "problem": "x" * 300, "file": "customers.iif", "line": n}
                  for n in range(14)]
    document = {"dry_run": False, "warnings": [], "ready": False, "summary": "14 blocking exceptions",
                "blocking": [f"customers.iif line {n}: row_width: the row is wrong" for n in range(14)],
                "counts": [], "exceptions": exceptions, "checks": [{"name": "x", "rows": ["y" * 40] * 10}],
                "steps": [{"order": n, "name": "z" * 400} for n in range(66)]}
    cut = fit(document, result_files=False)
    assert size(cut) <= BUDGET and list(cut)[0] == "blocking" and cut["blocking"] == document["blocking"]
    assert cut["exceptions"] == exceptions and 0 < len(cut["steps"]) < 66
    assert "no output directory is configured" in cut["result_compacted"]["full_result"]
    assert "--output-dir DIR" in cut["result_compacted"]["full_result"]


def test_a_file_refusal_says_when_no_output_directory_is_configured():
    from bookflow.adapters.mcp.files import Directories
    error = Directories([], flag="--output-dir").outside("transport.result_file", outcome="not_submitted")
    assert error.code == "E_PERMISSION" and error.details["configured"] is False
    assert error.message.startswith("No output directory is configured, so this MCP server saves no file.")
    assert "--output-dir DIR" in error.message and error.details["allowed_directories"] == []


def _with_taken_number(files):
    """The old books gain 'Shop Tools', numbered 6300: the general chart's Office Supplies has that number."""
    accounts = text("accounts.iif")
    row = next(line for line in accounts.split("\r\n") if line.startswith("ACCNT\tOffice Supplies"))
    accounts = accounts.replace(row, row + "\r\n" + "ACCNT\tShop Tools\t99\t1715623380\tEXP\t0.00\t\t6300\t0\t\t\tN\t0\tN")
    ledger = text("trial_balance.csv")
    ledger = ledger.replace('"6300 · Office Supplies",,"1,148.72",',
                            '"6300 · Office Supplies",,"1,048.72",\r\n"6300 · Shop Tools",,"100.00",')
    assert accounts != text("accounts.iif") and ledger != text("trial_balance.csv")
    swap = {"accounts.iif": accounts, "trial_balance.csv": ledger}
    return [{"content": swap[name], "name": name} if name in swap else f
            for name, f in zip(FILES, files)]


def test_create_on_a_taken_number_says_so_and_still_ties_out(mover):
    run = mover["run"]
    files = _with_taken_number(mover["files"])
    blocked = run("cutover plan", dict(as_of=AS_OF, files=files))
    taken = next(e for e in blocked["exceptions"] if e["code"] == "number_taken")
    assert taken["subject"].endswith("Shop Tools") and not blocked["ready"]
    mapped = {"accounts": {"6300 · Shop Tools": "create"}}
    plan = run("cutover plan", dict(as_of=AS_OF, files=files, mappings=mapped))
    assert plan["ready"], plan["exceptions"]
    dropped = next(e for e in plan["exceptions"] if e["code"] == "account_number_dropped")
    for words in ("6300", "Office Supplies", "renumber", "different number", "unnumbered"):
        assert words in dropped["problem"] + dropped["fix"], (words, dropped)
    run("cutover apply", dict(as_of=AS_OF, files=files, mappings=mapped))
    tie = run("cutover tie-out", dict(as_of=AS_OF, files=files, mappings=mapped))
    assert tie["tied"], tie["summary"]
    assert run("account show", dict(account="Shop Tools"))["balance"]["minor_units"] == 10000
    # The same plan, read again with or without the mapping, finds the account it made.
    assert run("cutover tie-out", dict(as_of=AS_OF, files=files))["tied"]
    again = run("cutover plan", dict(as_of=AS_OF, files=files, mappings=mapped))
    assert again["ready"] and not any(e["code"] == "name_taken" for e in again["exceptions"])


def test_create_with_a_number_brings_it_in_under_that_number(mover):
    run = mover["run"]
    files = _with_taken_number(mover["files"])
    taken = {"accounts": {"6300 · Shop Tools": "create 6300"}}
    blocked = run("cutover plan", dict(as_of=AS_OF, files=files, mappings=taken))
    assert not blocked["ready"] and any(e["code"] == "number_taken" for e in blocked["exceptions"])
    mapped = {"accounts": {"6300 · Shop Tools": "create 6350"}}
    plan = run("cutover plan", dict(as_of=AS_OF, files=files, mappings=mapped))
    assert plan["ready"] and not any(e["code"] == "account_number_dropped" for e in plan["exceptions"])
    run("cutover apply", dict(as_of=AS_OF, files=files, mappings=mapped))
    assert run("account show", dict(account="Shop Tools"))["number"] == "6350"
    assert run("cutover tie-out", dict(as_of=AS_OF, files=files, mappings=mapped))["tied"]
