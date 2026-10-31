"""R107 increment 7: guided opening balances for a business with no old books (design/bulk-import.md §6, §10 criterion 6).

The figures below were added up by hand. Twelve open invoices to five customers come to 17,000.00; eight unpaid bills
to four vendors to 7,500.00; thirty stock items, item N holding N units at 2.50 each, to 465 units and 1,162.50. The
owner knows Checking 12,430.55, Savings 5,000.00, the Visa card 1,284.33 owed and the truck loan 8,000.00, and gives
receivables and payables totals; stock is left to the rows (derived). So assets are 12,430.55 + 5,000.00 + 17,000.00
+ 1,162.50 = 35,593.05, liabilities 1,284.33 + 8,000.00 + 7,500.00 = 16,784.33, and Opening Balance Equity, worked out,
18,808.72 (a credit).
"""
import pytest

from bookflow import BookflowError
from tests.test_row3_host import hosted  # noqa: F401 - fixture

COMPANY = "Fresh Start"
AS_OF = "2026-09-30"

BALANCES = ("Account\tBalance\n"
            "Checking\t12,430.55\n"
            "1010\t5000.00\n"           # Savings, by its number
            "Visa\t1,284.33\n"          # a card: what is owed, on its own side
            "Truck Loan\t8000\n")
INVOICES = [  # customer, number, date, due date, terms, open amount
    ("Adams, Rachel", "1001", "09/02/2026", "", "Net 30", "412.50"),
    ("Adams, Rachel", "1007", "09/20/2026", "", "Net 30", "300.00"),
    ("Brookside Dental", "1002", "08/14/2026", "09/13/2026", "", "840.00"),
    ("Brookside Dental", "1005", "09/10/2026", "", "Net 15", "615.00"),
    ("Brookside Dental", "1011", "09/28/2026", "", "Net 30", "1,200.00"),
    ("Harbor View Apartments", "1003", "07/29/2026", "", "Net 30", "2,380.25"),
    ("Harbor View Apartments", "1009", "09/25/2026", "", "Net 30", "6,450.00"),
    ("Lakeside Cafe", "1004", "08/30/2026", "", "Net 30", "99.99"),
    ("Lakeside Cafe", "1008", "09/21/2026", "", "Net 30", "150.01"),
    ("Lakeside Cafe", "1012", "09/29/2026", "", "Net 30", "75.00"),
    ("Miller Construction", "1006", "09/15/2026", "", "Net 30", "3,000.00"),
    ("Miller Construction", "1010", "09/26/2026", "", "Net 30", "1,477.25"),
]
BILLS = [  # vendor, reference, date, due date, terms, open amount
    ("Ferguson Supply", "INV-1", "09/01/2026", "", "Net 30", "1,206.40"),
    ("Ferguson Supply", "INV-2", "09/10/2026", "", "Net 30", "3,412.88"),
    ("Midwest Gas", "G-1", "09/26/2026", "10/11/2026", "", "318.62"),
    ("Midwest Gas", "G-2", "08/26/2026", "09/11/2026", "", "300.00"),
    ("Office Depot", "OD-1", "09/05/2026", "", "Net 15", "145.10"),
    ("Office Depot", "OD-2", "09/18/2026", "", "Net 15", "54.90"),
    ("Smith CPA", "S-1", "09/30/2026", "", "Net 30", "1,500.00"),
    ("Smith CPA", "S-2", "08/31/2026", "", "Net 30", "562.10"),
]
ITEMS = [f"Part {n:02d}" for n in range(1, 31)]
NEW_ITEMS = ITEMS[-2:]  # not yet items here: made on the owner's confirmation


def grid(header, rows, sep="\t"):
    return sep.join(header) + "\n" + "".join(sep.join(row) + "\n" for row in rows)


def invoices_text(rows=INVOICES):
    return grid(("Customer", "Number", "Date", "Due date", "Terms", "Open amount"), rows)


def bills_text(rows=BILLS):
    return grid(("Vendor", "Reference", "Date", "Due date", "Terms", "Open amount"), rows)


def stock_text():
    # Half give a total value, half a unit cost: both are read.
    rows = [(name, str(n), f"{n * 2.5:.2f}" if n % 2 else "", "" if n % 2 else "2.50") for n, name in enumerate(ITEMS, 1)]
    return grid(("Item", "Quantity", "Total value", "Unit cost"), rows)


@pytest.fixture
def fresh(client):
    client.run("company new", dict(organization="Demo Holdings LLC", legal_name="Fresh Start LLC", display_name=COMPANY,
                                   home_currency="USD", timezone="America/Chicago"))
    run = lambda name, data=None, **kw: client.run(name, data or {}, company=COMPANY, **kw)
    run("account create", dict(name="Savings", type="bank", number="1010"))
    run("account create", dict(name="Visa", type="credit_card", number="2050"))
    run("account create", dict(name="Truck Loan", type="long_term_liability", number="2700"))
    ids = {n: run("account show", {"account": n})["id"] for n in ("Inventory Asset", "Cost of Goods Sold", "Sales Income")}
    for name in ITEMS[:-2]:
        run("item create", dict(name=name, type="inventory_part", price="5.00", cost="2.50", description=name,
                                purchase_description=name, asset_account_id=ids["Inventory Asset"],
                                cogs_account_id=ids["Cost of Goods Sold"], income_account_id=ids["Sales Income"]))
    return dict(run=run, client=client)


def _grant_deletes(fresh):
    """Undoing an opening deletes its invoices, bills and journal: the caller's own delete grants (design §8a)."""
    client, run = fresh["client"], fresh["run"]
    company = run("company show")["company_id"]
    state = client.permission.show()
    client.permission.activate(expected_generation=state["generation"], expected_catalog_sha256=state["catalog_sha256"])
    member = next(x for x in client.membership.list(company=company)["items"]
                  if x["scope_type"] == "company" and x["scope_id"] == company and x["role"] == "owner")
    client.membership.grant(user=member["user_id"], company=company, expected_version=member["version"],
                            grants=["transaction.invoice.delete", "transaction.bill.delete",
                                    "transaction.journal_entry.delete", "transaction.credit_memo.delete"])


def _draft(run, **overrides):
    data = dict(as_of=AS_OF, balances={"content": BALANCES}, open_invoices={"content": invoices_text()},
                unpaid_bills={"content": bills_text()}, stock={"content": stock_text()},
                given={"receivables": "17,000.00", "payables": "7500"}, make_items=NEW_ITEMS)
    data.update(overrides)
    return run("opening-draft create", data, reason="Owner's opening figures")


def _balances(run):
    rows = run("report trial-balance", dict(date_to=AS_OF, limit=200))["rows"]
    return {row["display_account_label"].split(" · ", 1)[-1]: row["signed_net"]["minor_units"] for row in rows
            if row["signed_net"]["minor_units"]}


def _customers(run):
    return sorted(r["full_name"] for r in run("customer list")["items"])


def _books(run):
    """What a person reads off the books at the date: every balance, receivables and payables by name, stock by item."""
    ar = {r["current_customer_label"]: r["total"]["minor_units"] for r in run("report ar-aging", dict(as_of=AS_OF, limit=200))["rows"]}
    ap = {r["current_vendor_name"]: r["total"]["minor_units"] for r in run("report ap-aging", dict(as_of=AS_OF, limit=200))["rows"]}
    stock = {r["item_name"]: (r["quantity_on_hand"], r["asset_value"]["minor_units"])
             for r in run("report inventory-valuation", dict(as_of=AS_OF, limit=200))["rows"]}
    return dict(balances=_balances(run), ar=ar, ap=ap, stock=stock)


def test_a_business_with_no_old_books_pastes_its_opening_and_it_proves(fresh):
    """Criterion 6: the books at the date show what was pasted, the given totals tie, derived checks say so, the
    clearing account is 0.00, and a deliberately wrong total refuses."""
    run = fresh["run"]
    draft = _draft(run)
    assert draft["state"] == "draft" and [s["rows"] for s in draft["steps"]] == [4, 12, 8, 30]
    assert all(not s["problems"] for s in draft["steps"])
    planned = run("opening-draft plan", dict(draft=draft["id"]), reason="Plan the opening")
    plan = planned["plan"]
    assert plan["ready"], plan["blocking"]
    assert planned["draft"]["state"] == "planned" and planned["draft"]["next"] == f"opening-draft apply {draft['id']}"
    counts = {c["kind"]: (c["create"], c["amount"]["minor_units"] if c["amount"] else None) for c in plan["counts"]}
    assert counts["invoice"] == (12, 1_700_000) and counts["bill"] == (8, 750_000)
    assert counts["inventory_adjustment"] == (30, 116_250) and counts["customer"][0] == 5 and counts["vendor"][0] == 4
    assert counts["item"][0] == 3  # the two confirmed stock items and the Opening balance item
    checks = {c["name"]: (c["basis"], c["source"]["minor_units"], c["compared"]["minor_units"]) for c in plan["checks"]}
    assert checks == {"trial_balance_total": ("derived", 3_559_305, 3_559_305),
                      "receivables": ("given", 1_700_000, 1_700_000), "payables": ("given", 750_000, 750_000),
                      "inventory": ("derived", 116_250, 116_250)}
    assert plan["clearing"]["net"]["minor_units"] == 0

    applied = run("opening-draft apply", dict(draft=draft["id"]), reason="Owner confirmed the plan's totals")
    assert applied["draft"]["state"] == "applied" and applied["apply"]["created"] > 0 and applied["draft"]["batch"]
    books = _books(run)
    assert books["balances"] == {"Checking": 1_243_055, "Savings": 500_000, "Accounts Receivable": 1_700_000,
                                 "Inventory Asset": 116_250, "Visa": -128_433, "Truck Loan": -800_000,
                                 "Accounts Payable": -750_000, "Opening Balance Equity": -1_880_872}
    assert books["ar"] == {"Adams, Rachel": 71_250, "Brookside Dental": 265_500, "Harbor View Apartments": 883_025,
                           "Lakeside Cafe": 32_500, "Miller Construction": 447_725}
    assert books["ap"] == {"Ferguson Supply": 461_928, "Midwest Gas": 61_862, "Office Depot": 20_000, "Smith CPA": 206_210}
    assert books["stock"]["Part 07"] == ("7", 1_750) and books["stock"]["Part 30"] == ("30", 7_500)
    assert sum(value for _, value in books["stock"].values()) == 116_250 and len(books["stock"]) == 30
    # Due dates: given, or from the terms (Net 30 from 09/02 is 10/02), never just the invoice date.
    invoice = run("invoice show", dict(invoice="1001"))
    assert invoice["due_date"] == "2026-10-02"

    tie = run("opening-draft tie-out", dict(draft=draft["id"]))["tie_out"]
    assert tie["tied"] and tie["clearing"]["minor_units"] == 0, tie["summary"]
    says = {c["name"]: (c["basis"], c["tied"], c["says"]) for c in tie["checks"]}
    assert says["Checking"] == ("given", True, "tied to the 12,430.55 you gave")
    assert says["Accounts Receivable"] == ("given", True, "tied to the 17,000.00 you gave")
    assert says["Inventory Asset"] == ("derived", True, "taken from your stock list; not independently checked")
    assert says["Opening Balance Equity"][0:2] == ("derived", True)
    assert [c["basis"] for c in tie["checks"]] == sorted((c["basis"] for c in tie["checks"]), key=lambda b: b != "given")
    assert tie["inventory"]["basis"] == "derived" and tie["receivables"]["basis"] == "derived"


def test_a_wrong_total_refuses_and_leaves_nothing(fresh):
    run = fresh["run"]
    draft = _draft(run, given={"receivables": "17,010.00", "payables": "7500"})
    plan = run("opening-draft plan", dict(draft=draft["id"]))["plan"]
    assert not plan["ready"]
    assert [e["code"] for e in plan["exceptions"] if e["severity"] == "blocking"] == ["receivables_do_not_tie"]
    before = _balances(run), _customers(run)
    with pytest.raises(BookflowError) as refused:
        run("opening-draft apply", dict(draft=draft["id"]), reason="Try it")
    assert refused.value.code == "E_CUTOVER_BLOCKED"
    assert (_balances(run), _customers(run)) == before
    assert run("opening-draft show", dict(draft=draft["id"]))["state"] == "planned"


def test_accepting_differences_posts_and_the_difference_stays_in_clearing(fresh):
    run = fresh["run"]
    draft = _draft(run, given={"receivables": "17,010.00", "payables": "7500"})
    applied = run("opening-draft apply", dict(draft=draft["id"], accept_differences=True), reason="Owner accepts 10.00")
    warned = {e["code"]: e["problem"] for e in applied["apply"]["exceptions"]}
    assert warned["receivables_do_not_tie"].startswith("accepted: ")
    tie = run("opening-draft tie-out", dict(draft=draft["id"]))["tie_out"]
    assert not tie["tied"] and tie["clearing"]["minor_units"] == 1_000
    ar = next(c for c in tie["checks"] if c["name"] == "Accounts Receivable")
    assert ar["basis"] == "given" and not ar["tied"] and ar["difference"]["minor_units"] == 1_000


def test_a_refusal_part_way_undoes_everything_and_a_stop_continues(fresh, monkeypatch):
    from bookflow.company import cutover
    run = fresh["run"]
    draft = _draft(run)
    real = cutover._run_step
    calls = {"n": 0}

    def refusing(s, ctx, step, payload, recorder=None):
        calls["n"] += 1
        if step.kind == "invoice" and step.name.startswith("Invoice 1009"):
            raise BookflowError("E_VALIDATION", message="refused for the test.")
        return real(s, ctx, step, payload, recorder)
    monkeypatch.setattr(cutover, "_run_step", refusing)
    # The default owner holds no delete grant: the undo is the apply's own all-or-nothing promise (review M1).
    before = _balances(run)
    customers = _customers(run)
    with pytest.raises(BookflowError) as stopped:
        run("opening-draft apply", dict(draft=draft["id"]), reason="Post it")
    assert stopped.value.code == "E_CUTOVER_INCOMPLETE" and stopped.value.details["rolled_back"] is True, \
        stopped.value.details.get("undo_refused")
    assert "nothing of this opening stays" in stopped.value.message
    assert _balances(run) == before == {}
    assert _customers(run) == customers  # made, then retired with the rest
    shown = run("opening-draft show", dict(draft=draft["id"]))
    assert shown["state"] == "rolled_back"

    class Killed(Exception):
        pass

    def killed(s, ctx, step, payload, recorder=None):
        if step.kind == "inventory_adjustment":
            raise Killed()
        return real(s, ctx, step, payload, recorder)
    monkeypatch.setattr(cutover, "_run_step", killed)
    with pytest.raises(Exception):
        run("opening-draft apply", dict(draft=draft["id"]), reason="Post it again")
    assert run("opening-draft show", dict(draft=draft["id"]))["state"] == "applying"
    stopped_at = _balances(run)  # the invoices and bills are in; the stock is not yet
    assert stopped_at["Accounts Receivable"] == 1_700_000 and "Inventory Asset" not in stopped_at
    monkeypatch.setattr(cutover, "_run_step", real)
    resumed = run("opening-draft apply", dict(draft=draft["id"]), reason="Continue")
    assert resumed["draft"]["state"] == "applied" and resumed["apply"]["already_in"] > 0
    tie = run("opening-draft tie-out", dict(draft=draft["id"]))["tie_out"]
    assert tie["tied"], tie["summary"]


def test_an_applied_opening_reverses_as_one_unit_or_names_what_blocks_it(fresh):
    run = fresh["run"]
    _grant_deletes(fresh)
    customers = _customers(run)
    draft = _draft(run)
    posted = run("opening-draft apply", dict(draft=draft["id"]), reason="Post it")
    assert any("clearing account" in note for note in posted["outside_batch"])
    assert any("next invoice number" in note for note in posted["outside_batch"])
    # A company has one opening: a second is refused while this one stands, naming it (review L3).
    second = _draft(run)
    with pytest.raises(BookflowError) as standing:
        run("opening-draft apply", dict(draft=second["id"]), reason="A second opening")
    assert standing.value.details["standing"] == draft["id"] and draft["id"] in standing.value.message
    run("opening-draft discard", dict(draft=second["id"]), reason="Not needed")
    try:
        plan = run("opening-draft reverse", dict(draft=draft["id"]), dry_run=True, reason="Undo the opening")
    except BookflowError as error:
        raise AssertionError((error.details or {}).get("blockers")) from None
    assert not plan["blockers"] and plan["remaining"] > 60
    done = run("opening-draft reverse", dict(draft=draft["id"], fingerprint=plan["fingerprint"]), reason="Undo the opening")
    assert done["draft"]["state"] == "reversed" and done["state"] == "reversed"
    assert any("clearing account" in note for note in done["stays"])
    assert _balances(run) == {} and _customers(run) == customers
    # Started again, it posts afresh (what the reversal retired is not taken as already in), and once a payment is
    # applied to an opening invoice the reversal is refused naming it, changing nothing.
    again = _draft(run)
    applied = run("opening-draft apply", dict(draft=again["id"]), reason="Post it again")
    assert applied["apply"]["already_in"] == 1  # the clearing account, at 0.00: accounts keep their history
    assert run("opening-draft tie-out", dict(draft=again["id"]))["tie_out"]["tied"]
    version = run("invoice show", {"invoice": "1012"})["version"]
    try:
        run("payment receive", dict(customer="Lakeside Cafe", date="2026-10-02", amount="75.00", deposit_to="Checking", payment_method="Check",
                                    operation_key="lakeside-1", applications={"mode": "inline", "items": [
                                        {"invoice": "1012", "expected_version": version, "amount": "75.00"}]}),
            reason="Lakeside paid")
    except BookflowError as error:
        raise AssertionError(error.details) from None
    with pytest.raises(BookflowError) as blocked:
        run("opening-draft reverse", dict(draft=again["id"]), reason="Undo the opening")
    assert blocked.value.code == "E_BATCH_BLOCKED"
    held = [b for b in blocked.value.details["blockers"] if b["code"] == "in_use"]
    assert any("invoice 1012" in b["message"] and "payment" in b["message"] for b in held), held
    # Stock it brought in, sold since: the sale holds it, named (review H1).
    run("customer create", {"name": "Walk In Buyer"})
    run("invoice post", dict(customer="Walk In Buyer", date="2026-10-05", number="2001",
                             lines=[dict(item="Part 10", quantity="6", unit_price="5.00")]), reason="A sale")
    with pytest.raises(BookflowError) as sold:
        run("opening-draft reverse", dict(draft=again["id"]), dry_run=True, reason="Undo the opening")
    held = [b for b in sold.value.details["blockers"] if b["code"] == "in_use"]
    assert any("invoice 2001" in b["message"] and "Part 10" in b["message"] for b in held), held
    assert run("opening-draft show", dict(draft=again["id"]))["state"] == "applied"
    assert _balances(run)["Accounts Receivable"] == 1_700_000  # as of the opening date; the payment came after


def test_equity_typed_must_balance_and_derived_equity_is_worked_out(fresh):
    run = fresh["run"]
    typed = BALANCES + "Owner's Capital\t10000.00\n"
    run("account create", dict(name="Owner's Capital", type="equity"))
    draft = _draft(run, balances={"content": typed})
    plan = run("opening-draft plan", dict(draft=draft["id"]))["plan"]
    blocking = [e for e in plan["exceptions"] if e["severity"] == "blocking"]
    assert [e["code"] for e in blocking] == ["trial_balance_unbalanced"]
    check = next(c for c in plan["checks"] if c["name"] == "trial_balance_total")
    assert check["source"]["minor_units"] - check["compared"]["minor_units"] == 880_872  # 18,808.72 owed, 10,000.00 typed
    # 18,808.72 is what balances them: typed as the owner's capital, the rows balance themselves and nothing is derived
    # but stock.
    exact = BALANCES + "Owner's Capital\t18808.72\n"
    version = run("opening-draft show", dict(draft=draft["id"]))["version"]
    run("opening-draft update", dict(draft=draft["id"], expected_version=version, balances={"content": exact}))
    plan = run("opening-draft plan", dict(draft=draft["id"]))["plan"]
    assert plan["ready"], plan["blocking"]
    journal = {line["account"]: (line["side"], line["amount"]["minor_units"]) for line in plan["journal"]["lines"]}
    assert journal["Owner's Capital"] == ("credit", 1_880_872) and "Opening Balance Equity" not in journal


def test_every_exception_an_entered_row_can_reach(fresh):
    """Each exception of docs/cutover.md that rows an owner types can cause, from the rows alone; the rest concern
    exports the guided path never reads (IIF lists, report titles, reconciliations, 1099s)."""
    run = fresh["run"]

    def codes(**steps):
        files = [dict(kind=kind, content=content) for kind, content in steps.items()]
        plan = run("cutover plan", dict(as_of=AS_OF, files=files))
        return {e["code"] for e in plan["exceptions"]}

    assert "unmapped_account" in codes(entered_balances="Account,Balance\nPetty Cash,50.00\n")
    assert "duplicate_account" in codes(entered_balances="Account,Balance\nChecking,1\n1000,2\n")
    assert "missing_open_invoices" in codes(entered_balances="Account,Balance\nAccounts Receivable,100\n")
    assert "missing_unpaid_bills" in codes(entered_balances="Account,Balance\nAccounts Payable,100\n")
    assert "missing_inventory_valuation" in codes(entered_balances="Account,Balance\nInventory Asset,100\n")
    assert "receivables_do_not_tie" in codes(entered_balances="Account,Balance\nAccounts Receivable,1\n",
                                             entered_open_invoices=invoices_text())
    assert "payables_do_not_tie" in codes(entered_balances="Account,Balance\nAccounts Payable,1\n",
                                          entered_unpaid_bills=bills_text())
    assert "inventory_does_not_tie" in codes(entered_balances="Account,Balance\nInventory Asset,1\n",
                                             entered_stock="Item,Quantity,Total value\nPart 01,1,2.50\n")
    assert "document_after_cutover" in codes(entered_open_invoices=invoices_text([("Acme", "9", "10/15/2026", "", "", "5")]))
    assert "document_without_date" in codes(entered_open_invoices=invoices_text([("Acme", "9", "", "", "", "5")]))
    assert "unreadable_amount" in codes(entered_open_invoices=invoices_text([("Acme", "9", "09/05/2026", "", "", "$599.♎56")]))
    assert "ambiguous_date" in codes(entered_open_invoices=invoices_text([("Acme", "9", "03/04/2026", "", "", "5")]))
    assert "missing_column" in codes(entered_stock="Item\nPart 01\n")
    assert "unknown_item" in codes(entered_stock="Item,Quantity,Total value\nWidget,1,2\n")
    income = run("account show", {"account": "Service Income"})["id"]
    run("item create", dict(name="Labor", type="service", income_account_id=income, price="50.00"))
    assert "type_conflict" in codes(entered_stock="Item,Quantity,Total value\nLabor,1,2\n")
    assert "negative_stock" in codes(entered_stock="Item,Quantity,Total value\nPart 01,-1,-2.50\n")
    assert "stock_value_mismatch" in codes(entered_stock="Item,Quantity,Total value,Unit cost\nPart 01,2,9.00,2.50\n")
    assert "possible_duplicate_customer" in codes(entered_open_invoices=invoices_text([
        ("Midland Electric Supply", "1", "09/01/2026", "", "", "5"), ("Midland Elec. Supply", "2", "09/01/2026", "", "", "5")]))
    assert "unknown_terms" in codes(entered_open_invoices=invoices_text([("Acme", "9", "09/05/2026", "", "Whenever", "5")]))
    assert "several_trial_balances" in codes(entered_balances="Account,Balance\nChecking,1\n",
                                             trial_balance="Debit,Credit\n")
    trial = "Account,Balance,Side\nChecking,10,debit\nOpening Balance Equity,9,credit\n"
    assert "trial_balance_unbalanced" in codes(entered_balances=trial)
    # The books must be empty up to the date, and the period open.
    run("journal post", dict(date="2026-09-01", lines=[dict(account="Checking", side="debit", amount="1.00"),
                                                       dict(account="Sales Income", side="credit", amount="1.00")]),
        reason="An entry before the opening")
    assert "entries_before_cutover" in codes(entered_balances="Account,Balance\nChecking,1\n")
    run("company update", dict(closing_date="2026-09-15"), reason="Close August")
    assert "period_closed" in codes(entered_open_invoices=invoices_text([("Acme", "9", "09/13/2026", "", "", "5")]))


# ------------------------------------------------------------------ the same opening from anchor-style exports

def _due(date, due, terms):
    from datetime import date as day, timedelta
    if due:
        return due
    month, dd, year = (int(x) for x in date.split("/"))
    days = {"Net 30": 30, "Net 15": 15}[terms]
    return (day(year, month, dd) + timedelta(days=days)).strftime("%m/%d/%Y")


def _q(*cells):
    return ",".join(f'"{c}"' if c != "" else "" for c in cells) + "\n"


def _grouped(title, header, rows, side):
    """An Open Invoices or Unpaid Bills Detail export: grouped by name, a Total row each, then TOTAL."""
    text = _q("Fresh Start LLC") + _q(title) + _q("As of September 30, 2026") + _q("", "", *header)
    total = 0
    by_party = {}
    for row in rows:
        by_party.setdefault(row[0], []).append(row)
    for party, docs in by_party.items():
        text += _q(party, *[""] * (len(header) + 1))
        subtotal = 0
        for name, number, date, due, terms, amount in docs:
            cents = round(float(amount.replace(",", "")) * 100)
            subtotal += cents
            cells = {"Type": "Invoice" if side == "receivable" else "Bill", "Date": date, "Num": number,
                     "Terms": terms, "Due Date": _due(date, due, terms), "Open Balance": amount.replace(",", "")}
            text += _q("", "", *[cells.get(h, "") for h in header])
        total += subtotal
        text += _q(f"Total {party}", *[""] * len(header), f"{subtotal / 100:.2f}")
    text += _q("TOTAL", *[""] * len(header), f"{total / 100:.2f}")
    return text


def _anchor_files():
    tb = (_q("Fresh Start LLC") + _q("Trial Balance") + _q("Accrual Basis") + _q("As of September 30, 2026")
          + _q("", "", "Sep 30, 26") + _q("", "", "Debit", "Credit"))
    rows = [("1000 · Checking", "12430.55", ""), ("1010 · Savings", "5000.00", ""),
            ("1100 · Accounts Receivable", "17000.00", ""), ("1300 · Inventory Asset", "1162.50", ""),
            ("2000 · Accounts Payable", "", "7500.00"), ("2050 · Visa", "", "1284.33"),
            ("2700 · Truck Loan", "", "8000.00"), ("3000 · Opening Balance Equity", "", "18808.72")]
    for label, debit, credit in rows:
        tb += _q(label, "", debit, credit)
    tb += _q("TOTAL", "", "35593.05", "35593.05")
    invoices = _grouped("Open Invoices", ("Type", "Date", "Num", "Terms", "Due Date", "Open Balance"), INVOICES, "receivable")
    bills = _grouped("Unpaid Bills Detail", ("Type", "Date", "Num", "Terms", "Due Date", "Open Balance"), BILLS, "payable")
    stock = (_q("Fresh Start LLC") + _q("Inventory Valuation Summary") + _q("As of September 30, 2026")
             + _q("", "On Hand", "Avg Cost", "Asset Value"))
    for n, name in enumerate(ITEMS, 1):
        stock += _q(name, str(n), "2.50", f"{n * 2.5:.2f}")
    stock += _q("TOTAL", "465", "", "1162.50")
    items = "!INVITEM\tNAME\tINVITEMTYPE\n" + "".join(f"INVITEM\t{name}\tINVENTORY\n" for name in ITEMS)
    return [dict(content=items, name="items.iif", kind="iif"), dict(content=tb, name="trial_balance.csv"),
            dict(content=invoices, name="open_invoices.csv"), dict(content=bills, name="unpaid_bills.csv"),
            dict(content=stock, name="inventory_valuation.csv")]


def test_the_same_data_through_cutover_from_anchor_style_exports_yields_the_same_books(fresh):
    run, client = fresh["run"], fresh["client"]
    draft = _draft(run)
    run("opening-draft apply", dict(draft=draft["id"]), reason="Post it")
    entered = _books(run)
    entered_aging, entered_due = _aging(run), _due_dates(run)

    client.run("company new", dict(organization="Demo Holdings LLC", legal_name="Fresh Exports LLC",
                                   display_name="Fresh Exports", home_currency="USD", timezone="America/Chicago"))
    other = lambda name, data=None, **kw: client.run(name, data or {}, company="Fresh Exports", **kw)
    other("account create", dict(name="Savings", type="bank", number="1010"))
    other("account create", dict(name="Visa", type="credit_card", number="2050"))
    other("account create", dict(name="Truck Loan", type="long_term_liability", number="2700"))
    ids = {n: other("account show", {"account": n})["id"] for n in ("Inventory Asset", "Cost of Goods Sold", "Sales Income")}
    for name in ITEMS:
        other("item create", dict(name=name, type="inventory_part", price="5.00", cost="2.50", description=name,
                                  purchase_description=name, asset_account_id=ids["Inventory Asset"],
                                  cogs_account_id=ids["Cost of Goods Sold"], income_account_id=ids["Sales Income"]))
    files = _anchor_files()
    plan = other("cutover plan", dict(as_of=AS_OF, files=files))
    assert plan["ready"], plan["blocking"]
    assert {c["basis"] for c in plan["checks"]} == {"given"}  # every figure of an export is the old books' own
    other("cutover apply", dict(as_of=AS_OF, files=files), reason="Move in from the exports")
    assert other("cutover tie-out", dict(as_of=AS_OF, files=files))["tied"]
    assert _books(other) == entered
    assert _aging(other) == entered_aging  # every bucket, not only the totals
    assert _due_dates(other) == entered_due


def _aging(run):
    money = lambda row: {k: v["minor_units"] for k, v in row.items() if isinstance(v, dict) and "minor_units" in v}
    return ({r["current_customer_label"]: money(r) for r in run("report ar-aging", dict(as_of=AS_OF, limit=200))["rows"]},
            {r["current_vendor_name"]: money(r) for r in run("report ap-aging", dict(as_of=AS_OF, limit=200))["rows"]})


def _due_dates(run):
    return {n: (shown["date"], shown["due_date"]) for n in (str(x) for x in range(1001, 1013))
            for shown in [run("invoice show", {"invoice": n})]}


def test_an_undo_stopped_part_way_is_finished_before_anything_posts_again(fresh, monkeypatch):
    """Review H2 and L5, with credits on both sides: a killed apply, an undo that stops part way (the draft is
    `failed` and apply is refused), the undo finished (back to `rolled_back`, nothing pasted anew), and the same
    draft applied again ties, the credit memo and vendor credit with it."""
    from bookflow.company import batches, cutover
    run = fresh["run"]
    _grant_deletes(fresh)
    invoices = INVOICES + [("Adams, Rachel", "CM-1", "09/25/2026", "", "", "-112.50")]
    bills = BILLS + [("Office Depot", "VC-1", "09/20/2026", "", "", "-200.00")]
    draft = _draft(run, open_invoices={"content": invoices_text(invoices)}, unpaid_bills={"content": bills_text(bills)},
                   given={})
    real = cutover._run_step

    class Killed(Exception):
        pass

    def killed(s, ctx, step, payload, recorder=None):
        if step.kind == "inventory_adjustment":
            raise Killed()
        return real(s, ctx, step, payload, recorder)
    monkeypatch.setattr(cutover, "_run_step", killed)
    with pytest.raises(Killed):
        run("opening-draft apply", dict(draft=draft["id"]), reason="Post it")
    monkeypatch.setattr(cutover, "_run_step", real)
    real_reverse = batches.reverse
    calls = {"n": 0}

    def one_slice(*a, **k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise BookflowError("E_BATCH_BLOCKED", message="stopped for the test", details={"blockers": []})
        return real_reverse(*a, **{**k, "limit": 5})
    monkeypatch.setattr(batches, "reverse", one_slice)
    with pytest.raises(BookflowError) as stopped:
        run("opening-draft reverse", dict(draft=draft["id"]), reason="Undo")
    assert calls["n"] == 2, stopped.value.details  # one slice ran, the next was stopped
    monkeypatch.setattr(batches, "reverse", real_reverse)
    shown = run("opening-draft show", dict(draft=draft["id"]))
    assert shown["state"] == "failed" and shown["next"] == f"opening-draft reverse {draft['id']}"
    with pytest.raises(BookflowError) as refused:
        run("opening-draft apply", dict(draft=draft["id"]), reason="Continue")
    assert refused.value.code == "E_VALIDATION"
    done = run("opening-draft reverse", dict(draft=draft["id"]), reason="Finish the undo")
    assert done["state"] == "reversed" and done["draft"]["state"] == "rolled_back"
    assert _balances(run) == {}
    again = run("opening-draft apply", dict(draft=draft["id"]), reason="Post it again")
    assert again["draft"]["state"] == "applied" and again["apply"]["already_in"] == 1  # the clearing account
    tie = run("opening-draft tie-out", dict(draft=draft["id"]))["tie_out"]
    assert tie["tied"], tie["summary"]
    books = _balances(run)
    assert books["Accounts Receivable"] == 1_700_000 - 11_250 and books["Accounts Payable"] == -(750_000 - 20_000)
    # The credits come back out with the rest: the credit memo deleted, the vendor credit voided.
    plan = run("opening-draft reverse", dict(draft=draft["id"]), dry_run=True, reason="Undo")
    steps = {s["record_type"]: s["steps"] for s in plan["steps"]}
    assert steps["credit_memo"][-1] == "credit-memo delete" and steps["vendor_credit"][-1] == "vendor-credit void"


def test_an_agent_accepts_differences_only_when_its_principal_granted_it(hosted):
    """Review L6: `accept_differences` for an agent: refused ungranted, refused when its principal lacks it, admitted
    once granted (a dry run, so nothing posts); a person is never asked."""
    from tests.conftest import hosted_call, make_agent
    from tests.test_people_only import REASON, _bearer, _grant
    cid = hosted.company_id
    hosted.ok("user.add", {"username": "principal-p", "company": cid, "role": "owner", "password": "pw-principal-p-12345"},
              headers=REASON)
    q = hosted.ok("user.add", {"username": "principal-q", "company": cid, "role": "admin",
                               "password": "pw-principal-q-12345"}, headers=REASON)["user_id"]
    _grant(hosted, q, "admin", denies=["opening.accept-differences"])
    agent = make_agent(hosted_call(hosted), "opening-agent", principals="principal-p", company=cid, role="admin")
    other = make_agent(hosted_call(hosted), "opening-agent-q", principals="principal-q", company=cid, role="admin")
    as_agent, as_other = _bearer(hosted, agent, "principal-p"), _bearer(hosted, other, "principal-q")
    draft = hosted.ok("opening-draft.create", {"as_of": "2024-12-31", "given": {"receivables": "10.00"},
                                               "open_invoices": {"content": "Customer,Number,Date,Open amount\n"
                                                                            "Gate Cafe,G-1,12/14/2024,5.00\n"}},
                      company=cid, headers=REASON)["id"]
    body = {"draft": draft, "accept_differences": True}
    path = f"/companies/{cid}/commands/opening-draft.apply?dry_run=true"

    def call(headers):
        return hosted.api.post(path, json=body, headers={**REASON, **headers})
    r = call(as_agent)
    assert r.status_code == 403 and r.json()["details"]["reason"] == "capability_not_granted", r.text
    assert "opening.accept-differences" in r.json()["message"]
    _grant(hosted, agent, "admin", grants=["opening.accept-differences"])
    _grant(hosted, other, "admin", grants=["opening.accept-differences"])
    r = call(as_other)
    assert r.status_code == 403 and r.json()["details"]["reason"] == "principal_lacks_capability", r.text
    r = call(as_agent)
    assert r.status_code == 200 and '"accepted: ' in r.text, r.text
    r = call(_bearer(hosted, "principal-p"))
    assert r.status_code == 200, r.text


def _kill_at_stock(monkeypatch):
    from bookflow.company import cutover
    real = cutover._run_step

    class Killed(Exception):
        pass

    def killed(s, ctx, step, payload, recorder=None):
        if step.kind == "inventory_adjustment":
            raise Killed()
        return real(s, ctx, step, payload, recorder)
    monkeypatch.setattr(cutover, "_run_step", killed)
    return real, Killed


def test_a_resumed_apply_refused_part_way_needs_the_callers_own_grants_to_undo(fresh, root, monkeypatch):
    """Verification M1: the owner's apply stops after the invoices and bills; a clerk with no delete grant resumes it and
    a later step is refused. The automatic undo is the command's own promise only for its own call's writes, so here
    it is refused for want of the clerk's grants, naming them, and nothing of the owner's is deleted in the clerk's
    name; the owner, who holds the grants, finishes the undo."""
    from bookflow.company import cutover
    from tests.conftest import as_user, make_actor
    run = fresh["run"]
    company_id = run("company show")["company_id"]
    draft = _draft(run)
    real, Killed = _kill_at_stock(monkeypatch)
    with pytest.raises(Killed):
        run("opening-draft apply", dict(draft=draft["id"]), reason="Post it")
    make_actor(root, "clerk", company_role=(company_id, "standard"))
    clerk = as_user(root, "clerk")
    calls = {"n": 0}

    def refusing(s, ctx, step, payload, recorder=None):
        if step.kind == "inventory_adjustment":
            calls["n"] += 1
            if calls["n"] == 3:
                raise BookflowError("E_VALIDATION", message="refused for the test.")
        return real(s, ctx, step, payload, recorder)
    monkeypatch.setattr(cutover, "_run_step", refusing)
    with pytest.raises(BookflowError) as stopped:
        clerk.run("opening-draft apply", dict(draft=draft["id"]), company=COMPANY, reason="Continue the opening")
    details = stopped.value.details
    assert details["rolled_back"] is False and details["draft_state"] == "failed", details
    assert "transaction." in json_text(details["undo_refused"]) and ".delete" in json_text(details["undo_refused"])
    assert run("invoice show", {"invoice": "1001"})["status"] == "posted"  # nothing deleted in the clerk's name
    monkeypatch.setattr(cutover, "_run_step", real)
    _grant_deletes(fresh)
    done = run("opening-draft reverse", dict(draft=draft["id"]), reason="Finish the undo")
    assert done["draft"]["state"] == "rolled_back" and _balances(run) == {}


def json_text(value):
    import json
    return json.dumps(value, default=str)


def test_an_agent_resuming_a_persons_stopped_opening_cannot_undo_it_without_grants(hosted, monkeypatch):
    """Verification M1 for an agent: a person's apply stops (not a refusal); the agent resumes it and a later invoice is
    refused. The undo needs the agent's own delete grants, so nothing the person posted is deleted."""
    from bookflow.company import cutover
    from tests.conftest import hosted_call, make_agent
    from tests.test_people_only import REASON, _bearer
    cid = hosted.company_id
    hosted.ok("user.add", {"username": "principal-p", "company": cid, "role": "owner",
                           "password": "pw-principal-p-12345"}, headers=REASON)
    agent = make_agent(hosted_call(hosted), "resume-agent", principals="principal-p", company=cid, role="standard")
    as_agent, as_person = _bearer(hosted, agent, "principal-p"), _bearer(hosted, "principal-p")

    def call(name, body, who):
        return hosted.api.post(f"/companies/{cid}/commands/{name}", json=body, headers={**REASON, **who})
    content = ("Customer,Number,Date,Open amount\nGate Cafe,G-1,12/14/2024,5.00\nGate Cafe,G-2,12/15/2024,6.00\n"
               "Gate Cafe,G-3,12/16/2024,7.00\n")
    draft = call("opening-draft.create", {"as_of": "2024-12-31", "open_invoices": {"content": content}}, as_person).json()["id"]
    real_step, real_stopped = cutover._run_step, cutover._stopped

    def stop_at(number):
        def step(s, ctx, st, payload, recorder=None):
            if st.kind == "invoice" and st.name.startswith(f"Invoice {number}"):
                raise BookflowError("E_VALIDATION", message="refused for the test.")
            return real_step(s, ctx, st, payload, recorder)
        return step
    monkeypatch.setattr(cutover, "_run_step", stop_at("G-2"))
    monkeypatch.setattr(cutover, "_stopped", lambda step, error, created: BookflowError(
        "E_VERSION_CONFLICT", message="stopped for the test (not a refusal)."))
    assert call("opening-draft.apply", {"draft": draft}, as_person).status_code != 200
    monkeypatch.setattr(cutover, "_stopped", real_stopped)
    monkeypatch.setattr(cutover, "_run_step", stop_at("G-3"))
    r = call("opening-draft.apply", {"draft": draft}, as_agent)
    details = r.json().get("details") or {}
    assert details.get("rolled_back") is False and details.get("draft_state") == "failed", r.text
    shown = hosted.ok("invoice.show", {"invoice": "G-1"}, company=cid)
    assert shown["status"] == "posted"


def test_a_stopped_opening_whose_documents_were_voided_by_hand_rolls_back_cleanly(fresh, monkeypatch):
    """Verification N1: a stopped apply; invoice 1003 deleted and 1004 voided by hand; apply again. Whatever that apply
    does, the undo passes over the documents already gone and finishes, so the draft is `rolled_back` (nothing
    stands) and discard is reachable: no state is a dead end."""
    from bookflow.company import cutover
    run = fresh["run"]
    _grant_deletes(fresh)
    draft = _draft(run)
    real, Killed = _kill_at_stock(monkeypatch)
    with pytest.raises(Killed):
        run("opening-draft apply", dict(draft=draft["id"]), reason="Post it")
    monkeypatch.setattr(cutover, "_run_step", real)
    v = run("invoice show", {"invoice": "1003"})["version"]
    run("invoice delete", {"invoice": "1003", "expected_version": v}, reason="by hand")
    v = run("invoice show", {"invoice": "1004"})["version"]
    run("invoice void", {"invoice": "1004", "expected_version": v}, reason="by hand")
    try:
        run("opening-draft apply", dict(draft=draft["id"]), reason="Continue")
        run("opening-draft reverse", dict(draft=draft["id"]), reason="Undo it")
    except BookflowError as error:
        assert error.details.get("rolled_back") is True, (error.code, error.message, error.details)
    shown = run("opening-draft show", dict(draft=draft["id"]))
    assert shown["state"] == "rolled_back" and _balances(run) == {}, shown["state"]
    assert run("opening-draft discard", dict(draft=draft["id"]), reason="Start again")["state"] == "discarded"


def test_an_applied_opening_undone_in_two_runs_ends_reversed(fresh, monkeypatch):
    """Verification low: applied, the undo stops after one slice (failed), then finishes: `reversed`, not
    `rolled_back`."""
    from bookflow.company import batches
    run = fresh["run"]
    _grant_deletes(fresh)
    draft = _draft(run)
    run("opening-draft apply", dict(draft=draft["id"]), reason="Post it")
    real_reverse = batches.reverse
    calls = {"n": 0}

    def one_slice(*a, **k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise BookflowError("E_BATCH_BLOCKED", message="stopped for the test", details={"blockers": []})
        return real_reverse(*a, **{**k, "limit": 5})
    monkeypatch.setattr(batches, "reverse", one_slice)
    with pytest.raises(BookflowError):
        run("opening-draft reverse", dict(draft=draft["id"]), reason="Undo")
    monkeypatch.setattr(batches, "reverse", real_reverse)
    assert run("opening-draft show", dict(draft=draft["id"]))["state"] == "failed"
    done = run("opening-draft reverse", dict(draft=draft["id"]), reason="Finish the undo")
    assert done["draft"]["state"] == "reversed"
