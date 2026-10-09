"""Keep Harbor Electric's July in Bookflow through the Python client, as a person would, and read the books back.

`Replay` moves the old books in with the cutover commands, posts every July event from
tests/fixtures/fakeco/answer/events.json through public commands, reconciles checking to the July OFX
statement with `reconcile import`, and sets the closing date as the person. Every kind of transaction
it meets is recorded in `fit` with the commands that kept it and how well Bookflow fits it: `does` (an
ordinary command does what the anchor does), `workaround` (it can be kept, with the steps named) or
`missing` (what the anchor would do that Bookflow cannot). `books()` reads the trial balance, agings,
sales tax owed and stock back for comparison with the key.

Run it alone to keep the month in a fresh data root and print the fit table:
`PYTHONPATH=src:. python -m tests.fakeco_replay DATA_ROOT`.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import bookflow
from bookflow import BookflowError
from bookflow.core import registry
from bookflow.core.ids import new_id

from tests import fakeco, fakeco_data as D

FIXTURE = fakeco.OUT
OLD = FIXTURE / "handed-over" / "old-books"
JULY = FIXTURE / "handed-over" / "2026-07"
ORGANIZATION = "Harbor Holdings"
COMPANY = "Harbor Electric"
AS_OF = "2026-06-30"
CORE_FILES = ("lists.iif", "trial_balance.csv", "open_invoices.csv", "unpaid_bills.csv", "inventory_valuation.csv")
AGING_FILES = ("ar_aging.csv", "ap_aging.csv")
# The general chart already numbers two accounts the old books number differently: Bank Fees is the old books'
# Bank Service Charges, and Other Expense moves off 9100 so Interest Expense keeps its number.
MAPPINGS = {"accounts": {"Bank Service Charges": "Bank Fees"}}
NAMES = {"Bank Service Charges": "Bank Fees"}  # old-books account name -> the Bookflow account it became
STATEMENT_ONLY = ("bank_charge", "bank_interest")


@dataclass
class Fit:
    kind: str
    status: str  # does, workaround, missing
    commands: list[str]
    how: str
    anchor: str = ""
    events: list[str] = field(default_factory=list)


def money(cents: int) -> str:
    return fakeco.money(cents)


def cents(text) -> int:
    return fakeco.cents(text)


class Replay:
    def __init__(self, root: Path, client=None):
        self.root = Path(root)
        self.client = client or bookflow.connect(data_root=str(self.root))
        self.fit: dict[str, Fit] = {}
        self.made: dict[str, dict] = {}  # event id -> what Bookflow made for it
        self.invoices: dict[str, str] = {}  # invoice, credit memo or sales receipt number -> id
        self.bills: dict[tuple[str, str], str] = {}  # (vendor, reference) -> bill or vendor credit id
        self.redirect: dict[str, str] = {}  # an invoice number the key uses -> the one Bookflow holds instead
        self.events = json.loads((FIXTURE / "answer" / "events.json").read_text())
        self.by_id = {ev["id"]: ev for ev in self.events}

    # ---------------------------------------------------------------- plumbing
    def run(self, name: str, data: dict | None = None, **context):
        if registry.get(name).is_write:
            context.setdefault("reason", "R170 fit check: Harbor Electric")
        return self.client.run(name, data or {}, company=COMPANY, **context)

    def note(self, kind: str, status: str, commands, how: str, anchor: str = "", event: str | None = None):
        row = self.fit.get(kind)
        if row is None:
            row = self.fit[kind] = Fit(kind, status, list(commands), how, anchor)
        if event and event not in row.events:
            row.events.append(event)

    def invoice_id(self, number: str) -> str:
        number = self.redirect.get(number, number)
        if number not in self.invoices:
            found = [row for row in self.run("invoice query", dict(number=number, limit=10))["items"]
                     if row["number"] == number]
            assert len(found) == 1, (number, found)
            self.invoices[number] = found[0]["id"]
        return self.invoices[number]

    def credit_id(self, number: str) -> str:
        if number not in self.invoices:
            found = [row for row in self.run("credit-memo query", dict(number=number, limit=10))["items"]
                     if row["number"] == number]
            assert len(found) == 1, (number, found)
            self.invoices[number] = found[0]["id"]
        return self.invoices[number]

    def bill_id(self, vendor: str, reference: str) -> str:
        key = (vendor, reference)
        if key not in self.bills:
            found = [row for row in self.run("bill query", dict(vendor=vendor, limit=200))["items"]
                     if row.get("supplier_reference") == reference]
            assert len(found) == 1, (key, found)
            self.bills[key] = found[0]["id"]
        return self.bills[key]

    def party(self, name: str, kind: str = "vendor") -> dict:
        noun = {"vendor": "vendor", "customer": "customer", "other_name": "other-name"}[kind]
        record = self.run(f"{noun} show", {noun.replace("-", "_"): name})
        return {"name_type": kind, "name_id": record["id"]}

    # ---------------------------------------------------------------- the company and the move-in
    def create_company(self):
        self.client.run("organization new", dict(name=ORGANIZATION))
        self.client.run("company new", dict(organization=ORGANIZATION, legal_name=D.COMPANY["legal_name"],
                                             display_name=COMPANY, home_currency="USD", timezone="America/Chicago"))
        self.run("company update", dict(sales_tax_enabled=True))
        self.run("account update", dict(account="Other Expense", number="9900"))

    def attach(self, names) -> list[dict]:
        company_id = self.run("company show")["company_id"]
        files = []
        for name in names:
            with open(OLD / name, "rb") as body:
                added = self.run("attachment add", dict(record_type="company_info", record_id=company_id,
                                                        original_filename=name, media_type="text/plain",
                                                        caption="Old books export"), input_stream=body)
            files.append({"attachment": added["attachment"]["id"]})
        return files

    def move_in(self) -> dict:
        core = self.attach(CORE_FILES)
        agings = self.attach(AGING_FILES)
        plan = self.run("cutover plan", dict(as_of=AS_OF, files=core, mappings=MAPPINGS))
        assert plan["ready"], plan["blocking"]
        applied = self.run("cutover apply", dict(as_of=AS_OF, files=core, mappings=MAPPINGS),
                           reason="Move in from the old books")
        tie = self.run("cutover tie-out", dict(as_of=AS_OF, files=core + agings, mappings=MAPPINGS))
        self.cutover = dict(plan=plan, applied=applied, tie=tie)
        return self.cutover

    # ---------------------------------------------------------------- after the move-in
    def settle_in(self):
        """What a person sets up once the lists are in, before the first new entry."""
        tax_item = self.run("item show", dict(item=D.COMPANY["tax_item"]))["id"]
        self.run("company update", dict(default_sales_tax_item_id=tax_item))
        self.note("Customer sales tax item (IIF TAXITEM)", "workaround", ["company update"],
                  "the move-in leaves every customer's sales tax item empty, so the first invoice is refused until "
                  "the company default sales tax item is set (`company update default_sales_tax_item_id`)",
                  "each customer keeps the tax item the list gives it and its invoices take it")
        owner, = D.OTHER_NAMES.items()
        name, info = owner
        self.run("other-name create", dict(name=name, first_name="Mike", last_name="Harbor", phone=info["phone"]))
        self.note("Other names list (owner)", "workaround", ["other-name create"],
                  "the move-in ignores the IIF's OTHERNAME list; the owner is added by hand for the draws",
                  "the other names list comes over with the rest")
        self.account_ids = {row["full_name"]: row["id"] for row in self.run("account list")["items"]}
        self.non_taxable = next(row["id"] for row in self.run("sales-tax-code list")["items"] if row["code"] == "Non")

    def opening_detail(self):
        """The June statements did not show every entry the old books held. The move-in brings checking and
        the card in as one amount each, so the items the statements did not show yet are put in one by one,
        against Cutover Clearing, beside one entry that moves the rest of the opening amount the other way."""
        clearing = "Cutover Clearing"
        for account in ("Checking", "Visa Business Card"):
            items = [u for u in D.UNCLEARED if u[0] == account]
            card = account == "Visa Business Card"
            net = sum(cents(u[6]) for u in items)  # checking: money in; card: what is owed
            # checking (money in) goes up by the checks still to clear and down by the deposit; the card (owed)
            # goes down by the charges still to post: the amount the uncleared items then move back
            back = -net if not card else net
            lines = [dict(account=account, side="debit" if back > 0 else "credit", amount=money(abs(back))),
                     dict(account=clearing, side="credit" if back > 0 else "debit", amount=money(abs(back)))]
            made = self.run("journal post", dict(date=AS_OF, memo=f"{account}: the June statement's balance, before "
                                                 "the items it did not show yet", lines=lines))
            self.made[f"opening:{account}"] = made
            for _, kind, when, number, name, memo, amount, _ in items:
                value = cents(amount)
                if card:
                    self.run("card-charge post", dict(account=account, date=when, amount=money(value), memo=memo,
                                                      **({"pay_to": self.party(name)} if name else {}),
                                                      expenses=[dict(account=clearing, amount=money(value), memo=memo)]))
                elif value < 0:  # no payee: the payment is already in the old books' vendor and 1099 totals
                    self.run("check post", dict(account=account, number=number, date=when, amount=money(-value),
                                                memo=f"{name}: {memo} (written before the move-in)",
                                                expenses=[dict(account=clearing, amount=money(-value), memo=memo)]))
                else:
                    self.run("register post", dict(account=account, date=when, direction="increase",
                                                   amount=money(value), memo=memo, category=clearing))
        self.note("Outstanding checks, deposit in transit and unposted card charges at the cutover", "workaround",
                  ["journal post", "check post", "register post", "card-charge post"],
                  "the move-in brings checking and the card in as one opening amount each, so a first "
                  "reconciliation cannot clear June's outstanding items one by one; each is entered again against "
                  "Cutover Clearing, beside a journal moving the same total back (checks without a payee, so the "
                  "old books' vendor and 1099 totals are not counted twice), and the opening reconciliation covers "
                  "the June statement balance and leaves them outstanding",
                  "the new company is set up from the last statement balance with each outstanding item entered "
                  "on its own, so the first reconciliation clears them")

    # ---------------------------------------------------------------- July, event by event
    def july(self, until: str = "2026-07-31"):
        for ev in self.events:
            if ev["date"] > until or not ev["date"].startswith("2026-07") or ev["kind"] in STATEMENT_ONLY:
                continue
            handler = getattr(self, "_" + ev["kind"])
            try:
                handler(ev)
            except BookflowError as error:
                raise AssertionError(f"{ev['id']} {ev['kind']}: {error.to_dict()}") from error

    def _lines(self, ev: dict) -> list[dict]:
        lines = []
        for line in ev["lines"]:
            row = dict(item=line["item"])
            if line["quantity"] is not None:
                row["quantity"] = line["quantity"]
                row["unit_price"] = line["rate"]
            if line["desc"] != fakeco.item(line["item"])["desc"]:
                row["description"] = line["desc"]
            lines.append(row)
        return lines

    def _new_customer(self, name: str):
        info = D.NEW_CUSTOMERS[name]
        city, rest = info["city"].split(", ")
        state, postal = rest.split(" ")
        terms = {row["name"]: row["id"] for row in self.run("term list")["items"]}
        self.run("customer create", dict(name=name, first_name=info["first"], last_name=info["last"],
                                         phone=info["phone"], terms_id=terms[info["terms"]],
                                         billing_address=dict(line1=info["street"][0], city=city, state=state,
                                                              postal_code=postal)))

    def _invoice(self, ev: dict):
        if ev.get("new_customer"):
            self._new_customer(ev["customer"])
        data = dict(customer=ev["customer"], date=ev["date"], number=ev["number"], lines=self._lines(ev))
        if ev.get("po"):
            data["customer_purchase_order"] = ev["po"]
        if ev.get("terms") and ev["terms"] != fakeco.customer(ev["customer"])["terms"]:
            data["terms"] = ev["terms"]
        made = self.run("invoice post", data)
        assert made["total"]["amount"] == ev["total"], (ev["id"], made["total"], ev["total"])
        self.invoices[ev["number"]] = made["id"]
        self.made[ev["id"]] = made
        kinds = {fakeco.item(line["item"])["kind"] for line in ev["lines"]}
        if "DISC" in kinds:
            self.note("Invoice with a percentage discount item", "does", ["invoice post"],
                      "the 10% senior discount item takes its percent of the labor line above it, as the anchor does",
                      event=ev["id"])
        elif ":" in ev["customer"]:
            self.note("Invoice to a customer's job", "does", ["invoice post"],
                      "customer:job names the job; terms, tax and P.O. come out as the old books'", event=ev["id"])
        else:
            self.note("Invoice (service, stocked parts, job materials, other charges, sales tax)", "does",
                      ["invoice post"], "items, quantities and rates as written; stocked parts take their average "
                      "cost; tax is figured on the taxable lines to the cent", event=ev["id"])
        if ev.get("new_customer"):
            self.note("New customer from the paperwork", "does", ["customer create", "invoice post"],
                      "made with its address and terms before its first invoice", event=ev["id"])

    def _sales_receipt(self, ev: dict):
        made = self.run("sales-receipt post", dict(customer=ev["customer"], date=ev["date"], number=ev["number"],
                                                   lines=self._lines(ev), payment_method=ev["method"],
                                                   deposit_to="Undeposited Funds"))
        assert made["total"]["amount"] == ev["total"], (ev["id"], made["total"], ev["total"])
        self.made[ev["id"]] = made
        self.note("Cash sale", "does", ["sales-receipt post"], "a sales receipt into Undeposited Funds, deposited later",
                  event=ev["id"])

    def _credit_memo(self, ev: dict):
        source = self.invoice_id(ev["source_invoice"])
        shown = self.run("invoice show", dict(invoice=source))
        lines = []
        for line in ev["lines"]:
            original = next(row for row in shown["revision"]["lines"]
                            if row["item_snapshot"]["item"]["label"] == line["item"])
            lines.append(dict(source_invoice=source, source_line=original["line_id"], quantity=line["quantity"]))
        made = self.run("credit-memo post", dict(customer=ev["customer"], date=ev["date"], number=ev["number"],
                                                 lines=lines, memo=ev["note"][:200]))
        assert made["total"]["amount"] == ev["total"], (ev["id"], made["total"], ev["total"])
        self.invoices[ev["number"]] = made["id"]
        self.made[ev["id"]] = made
        self.note("Customer return of a stocked item", "does", ["credit-memo post"],
                  "a credit memo against the invoice line: the charger goes back on the shelf at what its sale took, "
                  "and its sales tax comes back", event=ev["id"])

    def _customer_refund(self, ev: dict):
        credit = self.invoices[ev["credit"]]
        made = self.run("customer-refund post", dict(date=ev["date"], customer=ev["customer"],
                                                     sources=[dict(credit_memo=credit, amount=ev["amount"])],
                                                     funding_account="Checking", method="Check",
                                                     check_number=ev["number"]))
        self.made[ev["id"]] = made
        self.note("Refund check to a customer", "does", ["customer-refund post"],
                  "a refund check from checking that uses up the credit memo", event=ev["id"])

    def _credit_apply(self, ev: dict):
        credit = self.credit_id(ev["credit"])
        invoice = self.invoice_id(ev["invoice"])
        self.run("customer-credit apply", dict(
            credit_memo=credit, expected_version=self.run("credit-memo show", dict(credit_memo=credit))["version"],
            date=ev["date"], applications=[dict(invoice=invoice, amount=ev["amount"],
                                                expected_version=self.run("invoice show", dict(invoice=invoice))["version"])]))
        self.note("Customer credit memo applied to a new invoice", "does", ["customer-credit apply"],
                  "the May credit memo brought in by the move-in pays part of a July invoice", event=ev["id"])

    def _payment(self, ev: dict):
        applications, discounts = defaultdict(int), {}
        for row in ev["applied"]:
            number = self.redirect.get(row["invoice"], row["invoice"])
            applications[number] += cents(row["amount"])
            if cents(row["discount"]):
                discounts[number] = row["discount"]
        items = []
        for number, amount in applications.items():
            invoice = self.invoice_id(number)
            items.append(dict(invoice=invoice, amount=money(amount),
                              expected_version=self.run("invoice show", dict(invoice=invoice))["version"]))
        data = dict(customer=ev["customer"], date=ev["date"], amount=ev["amount"], operation_key=new_id(),
                    payment_method=ev["method"], reference=ev["ref"],
                    applications=dict(mode="inline", items=items))
        if discounts:
            data["discounts"] = [dict(invoice=self.invoice_id(n), amount=a) for n, a in discounts.items()]
            data["discount_account"] = "Sales Discounts"
        made = self.run("payment receive", data)
        self.made[ev["id"]] = made
        if discounts:
            self.note("Customer takes an early-payment discount", "does", ["payment receive"],
                      "the 1% is listed under `discounts` and goes to Sales Discounts", event=ev["id"])
        if len(ev["applied"]) > 1:
            self.note("One check paying several invoices (across jobs)", "does", ["payment receive"],
                      "the parent customer's check is applied to each job's invoice", event=ev["id"])
        if any(amount is not None for _, amount in ev["apply"]):  # the paperwork names a part payment
            self.note("Short payment left open", "does", ["payment receive"],
                      "applied for what was paid; the rest stays due on the invoice", event=ev["id"])
        self.note("Customer payment into Undeposited Funds", "does", ["payment receive"],
                  "applied to the invoices it pays; waits in Undeposited Funds for the deposit", event=ev["id"])

    def _deposit(self, ev: dict):
        if all(row["source"].startswith("uf:") for row in ev["items"]):
            # Undeposited Funds came in as one opening amount: a deposit cannot draw on it (an `additional` line
            # from Undeposited Funds is refused with a bare E_VALIDATION), so the cutover's own advice is followed.
            checks = ", ".join(f"{row['source'][3:]} {row['amount']}" for row in ev["items"])
            made = self.run("journal post", dict(date=ev["date"], memo=f"Deposit of checks received before the move-in: "
                                                 f"{checks}", lines=[
                dict(account="Checking", side="debit", amount=ev["total"]),
                dict(account="Undeposited Funds", side="credit", amount=ev["total"])]))
            self.made[ev["id"]] = made
            self.note("Deposit of checks received before the cutover", "workaround", ["journal post"],
                      "the move-in brings Undeposited Funds in as one amount that Make Deposits cannot pick, and a "
                      "deposit line drawn from Undeposited Funds is refused with E_VALIDATION and no details; the "
                      "cutover's advice is a journal from Undeposited Funds to Checking",
                      "the two receipts sit in Undeposited Funds and Make Deposits picks them", event=ev["id"])
            return
        sources, additional = [], []
        for row in ev["items"]:
            source = row["source"]
            made = self.made[source]
            kind = "sales_receipt" if self.by_id[source]["kind"] == "sales_receipt" else "payment"
            noun = "sales-receipt" if kind == "sales_receipt" else "payment"
            record_id = made.get("payment_id") or made.get("id") or made["payment"]["id"]
            version = self.run(f"{noun} show", {noun.replace("-", "_"): record_id})["version"]
            sources.append(dict(source_type=kind, source=record_id, expected_version=version))
        document = dict(mode="inline", deposit_to="Checking", date=ev["date"], sources=sources, additional=additional)
        made = self.run("deposit post", dict(operation_key=new_id(), document=document))
        self.made[ev["id"]] = made
        self.note("Bank deposit of several checks and cash", "does", ["deposit post"],
                  "the payments and the cash sale picked from Undeposited Funds", event=ev["id"])

    def _bill(self, ev: dict):
        data = dict(vendor=ev["vendor"], date=ev["date"], supplier_reference=ev["number"], terms=ev["terms"])
        items = [dict(item=row["item"], quantity=row["quantity"], unit_cost=row["cost"]) for row in ev["lines"]
                 if "item" in row]
        expenses = [dict(account=row["account"], amount=row["amount"], memo=row["memo"],
                         **({"customer": row["job"]} if row.get("job") else {})) for row in ev["lines"] if "account" in row]
        if items:
            data["items"] = items
        if expenses:
            data["expenses"] = expenses
        made = self.run("bill post", data)
        assert made["total"]["amount"] == ev["total"], (ev["id"], made["total"], ev["total"])
        self.bills[(ev["vendor"], ev["number"])] = made["id"]
        self.made[ev["id"]] = made
        if items:
            self.note("Supplier bill receiving stocked items", "does", ["bill post"],
                      "item rows add quantity and cost to stock", event=ev["id"])
        if any(row.get("job") for row in ev["lines"]):
            self.note("Bill lines costed to a customer's job", "does", ["bill post"],
                      "expense rows name the customer:job", event=ev["id"])
        self.note("Vendor bill (expenses)", "does", ["bill post"], "terms and due date from the vendor or the bill",
                  event=ev["id"])

    def _vendor_credit_apply(self, ev: dict):
        credit = self.bill_credit(ev["vendor"], ev["credit"])
        bill = self.bill_id(ev["vendor"], ev["bill"])
        self.run("vendor-credit apply", dict(credit=credit, date=ev["date"],
                                             bills=[dict(bill=bill, amount=ev["amount"])]))
        self.note("Vendor credit used against a bill", "does", ["vendor-credit apply"],
                  "the June credit brought in by the move-in reduces the bill before it is paid", event=ev["id"])

    def bill_credit(self, vendor: str, reference: str) -> str:
        key = (vendor, reference)
        if key not in self.bills:
            found = [row for row in self.run("vendor-credit query", dict(vendor=vendor, limit=50))["items"]
                     if row.get("supplier_reference") == reference]
            assert len(found) == 1, (key, found)
            self.bills[key] = found[0]["id"]
        return self.bills[key]

    def _bill_payment(self, ev: dict):
        bills = []
        for row in ev["bills"]:
            entry = dict(bill=self.bill_id(ev["vendor"], row["bill"]), amount=row["amount"])
            if cents(row["discount"]):
                entry["discount"] = row["discount"]
            bills.append(entry)
        data = dict(date=ev["date"], bills=bills, funding_account="Checking",
                    method="Check" if ev["method"] == "Check" else "Bank Transfer")
        if ev["method"] == "Check":
            data["check_number"] = ev["number"]
        if any(cents(row["discount"]) for row in ev["bills"]):
            data["discount_account"] = "Purchase Discounts"
        made = self.run("bill pay", data)
        self.made[ev["id"]] = made
        if "discount" in json.dumps(bills):
            self.note("Paying a supplier inside its 2% discount", "does", ["bill pay"],
                      "the discount is given per bill and goes to Purchase Discounts", event=ev["id"])
        if ev["method"] == "Check":
            self.note("Bill payment by check", "does", ["bill pay"], "check number and the bills it pays",
                      event=ev["id"])
        else:
            self.note("Bill paid online or by autopay", "does", ["bill pay"], "funded from checking with no check number",
                      event=ev["id"])

    def _check(self, ev: dict):
        kind = ev.get("payee_kind", "vendor")
        payee = self.party(ev["payee"], kind)
        lines = [dict(account=NAMES.get(account, account), amount=amount, memo=memo) for account, amount, memo in ev["lines"]]
        if ev["number"] != "ACH":
            made = self.run("check post", dict(account="Checking", number=ev["number"], date=ev["date"],
                                               amount=ev["total"], pay_to=payee, expenses=lines))
            label = "Check written for an expense (rent, permit)"
            how = "a check with its number, payee and expense lines"
        else:
            made = self.run("register post", dict(account="Checking", date=ev["date"], direction="decrease",
                                                  amount=ev["total"], payee=payee, memo=ev.get("desc"),
                                                  allocations=[dict(account=row["account"], amount=row["amount"],
                                                                    memo=row["memo"]) for row in lines]))
            accounts = {row["account"] for row in lines}
            if "Owner's Draw" in accounts:
                label, how = "Owner draw (online transfer to the owner)", "a register entry to Owner's Draw, payee the owner"
            elif "Van Loan" in accounts:
                label, how = ("Loan payment split into principal and interest",
                              "one register entry with two allocations: Van Loan and Interest Expense")
            else:
                label, how = "ACH debit for an expense (insurance, payroll service fee)", \
                    "a register entry with the expense allocations and the payee"
        self.made[ev["id"]] = made
        self.note(label, "does", ["check post" if ev["number"] != "ACH" else "register post"], how, event=ev["id"])

    def _card_charge(self, ev: dict, noun: str = "card-charge"):
        payee = ev.get("payee")
        data = dict(account="Visa Business Card", date=ev["date"], amount=ev["total"],
                    expenses=[dict(account=account, amount=amount, memo=memo) for account, amount, memo in ev["lines"]])
        if payee == "Midland Elec. Supply":
            payee = "Midland Electric Supply"
            self.note("Duplicate vendor name (Midland Elec. Supply)", "missing", ["vendor deactivate"],
                      "there is no merge: the receipt is entered under Midland Electric Supply and the duplicate "
                      "deactivated, its history left under its own name (R117 Merge duplicates is in Later)",
                      "Merge: rename the duplicate to the right name and the anchor folds its history in",
                      event=ev["id"])
            self.run("vendor deactivate", dict(vendor="Midland Elec. Supply"))
        if payee and payee in D.NEW_VENDORS:
            info = D.NEW_VENDORS[payee]
            city, rest = info["city"].split(", ")
            state, postal = rest.split(" ")
            self.run("vendor create", dict(name=payee, phone=info["phone"],
                                           address=dict(line1=info["street"][0], city=city, state=state,
                                                        postal_code=postal)))
            self.note("New vendor from the paperwork", "does", ["vendor create"], "made before its first receipt",
                      event=ev["id"])
        if payee:
            data["pay_to"] = self.party(payee)
        made = self.run(f"{noun} post", data)
        self.made[ev["id"]] = made
        if noun == "card-credit":
            self.note("Card refund (returned purchase)", "does", ["card-credit post"],
                      "a card credit against the expense it came from", event=ev["id"])
        else:
            self.note("Credit card purchase", "does", ["card-charge post"],
                      "payee optional, expense lines", event=ev["id"])

    def _card_credit(self, ev: dict):
        self._card_charge(ev, "card-credit")

    def _transfer(self, ev: dict):
        made = self.run("transfer post", dict(from_account=ev["from_account"], to_account=ev["to_account"],
                                              date=ev["date"], amount=ev["amount"], memo=ev["note"][:200]))
        self.made[ev["id"]] = made
        if ev["to_account"] == "Visa Business Card":
            self.note("Paying the credit card from checking", "does", ["transfer post"],
                      "a transfer from checking to the card account", event=ev["id"])
        else:
            self.note("Transfer to savings", "does", ["transfer post"], "checking to savings", event=ev["id"])

    def _sales_tax_payment(self, ev: dict):
        made = self.run("sales-tax pay", dict(agency=ev["agency"], date=ev["date"], through_date=ev["through"],
                                              amount=ev["amount"], funding_account="Checking", method="Bank Transfer"))
        self.made[ev["id"]] = made
        self.note("Sales tax payment to the state", "does", ["sales-tax pay"],
                  "pays the agency's June liability, which the move-in brought in as a sales tax adjustment",
                  event=ev["id"])

    def _payroll(self, ev: dict):
        lines = [dict(account="Payroll Expenses:Wages", side="debit", amount=ev["gross"], description="Gross wages"),
                 dict(account="Payroll Expenses:Payroll Taxes", side="debit", amount=ev["employer_taxes"],
                      description="Employer taxes"),
                 dict(account="Checking", side="credit", amount=ev["net"], description="Net pay",
                      name_type="vendor", name_id=self.party("Paywell Payroll Services")["name_id"]),
                 dict(account="Checking", side="credit", amount=ev["tax_debit"], description="Payroll taxes",
                      name_type="vendor", name_id=self.party("Paywell Payroll Services")["name_id"])]
        made = self.run("journal post", dict(date=ev["date"], memo=f"Paywell payroll, pay date {ev['pay_date']}",
                                             lines=lines))
        self.made[ev["id"]] = made
        self.note("Payroll from an outside service (net pay and tax debits)", "does", ["journal post"],
                  "one journal per run from the service's report: gross wages and employer taxes against the two "
                  "checking debits (Bookflow's own payroll, R116, is in Later; the anchor's needs its paid "
                  "subscription too)", event=ev["id"])

    def _bounced_check(self, ev: dict):
        payment = self.by_id[ev["payment"]]
        clearing = "Returned Checks Clearing"
        if "Returned Check Charges" not in self.account_ids:
            income = self.run("account create", dict(name="Returned Check Charges", number="4800", type="income"))
            self.account_ids["Returned Check Charges"] = income["id"]
            holding = self.run("account create", dict(name=clearing, type="other_current_asset",
                                                      description="A customer's returned check, until it is invoiced"))
            self.account_ids[clearing] = holding["id"]
            self.run("item create", dict(name="Returned Check Fee", type="other_charge", sales_tax_code_id=self.non_taxable,
                                         description="Returned check fee", price="35.00", income_account_id=income["id"]))
            # An item cannot post to a bank account (income_account_id refuses type bank), so the returned
            # check is invoiced through a clearing account and taken out of checking by a register entry.
            self.run("item create", dict(name="Returned Check", type="other_charge", sales_tax_code_id=self.non_taxable,
                                         description="Customer check returned unpaid", price="0.00",
                                         income_account_id=holding["id"]))
        lines = [dict(item="Returned Check", quantity="1", unit_price=ev["amount"],
                      description=f"Check #{payment['ref']} returned unpaid (it paid invoice "
                                  f"{', '.join(row['invoice'] for row in payment['applied'])})"),
                 dict(item="Returned Check Fee", quantity="1", unit_price=ev["customer_fee"])]
        invoice = self.run("invoice post", dict(customer=ev["customer"], date=ev["date"], number=ev["fee_number"],
                                                lines=lines, terms="Due on receipt"))
        assert invoice["total"]["minor_units"] == cents(ev["amount"]) + cents(ev["customer_fee"]), invoice["total"]
        self.invoices[ev["fee_number"]] = invoice["id"]
        for applied in payment["applied"]:
            self.redirect[applied["invoice"]] = ev["fee_number"]
        returned = self.run("register post", dict(account="Checking", date=ev["date"], direction="decrease",
                                                  amount=ev["amount"], payee=self.party(ev["customer"], "customer"),
                                                  memo=f"Returned check #{payment['ref']}", category=clearing))
        fee = self.run("register post", dict(account="Checking", date=ev["date"], direction="decrease",
                                             amount=ev["bank_fee"], payee=self.party("Cedar Prairie Bank"),
                                             memo="Return item fee", category="Bank Fees"))
        self.made[ev["id"]] = dict(invoice=invoice, returned=returned, fee=fee)
        self.note("Bounced customer check (returned item, bank fee, fee charged to the customer)", "workaround",
                  ["account create", "item create", "invoice post", "register post"],
                  "the customer is invoiced the returned check (an other-charge item on a clearing account, since an "
                  "item cannot post to a bank account) and the returned-check fee; a register entry takes the "
                  "check out of checking against the clearing account and another enters the bank's fee; the "
                  "customer's cash is applied to that invoice (R148 bounced payments is in Later)",
                  "Receive Payments > Record Bounced Check reopens the paid invoice, enters the bank fee and invoices "
                  "the customer's fee in one step", event=ev["id"])

    def _inventory_adjust(self, ev: dict):
        made = self.run("inventory adjust", dict(item=ev["item"], date=ev["date"], quantity_change=ev["quantity"],
                                                 adjustment_account=ev["account"], memo=ev["note"][:200]))
        self.made[ev["id"]] = made
        self.note("Stock written off (damaged)", "does", ["inventory adjust"],
                  "quantity down, value at the running average, to Inventory Adjustments", event=ev["id"])

    def stock(self, as_of: str) -> dict:
        rows = self.run("report inventory-valuation", dict(as_of=as_of, limit=200))["rows"]
        return {row["item_name"]: (row["quantity_on_hand"], row["asset_value"]["minor_units"]) for row in rows}

    def _vendor_credit(self, ev: dict):
        clearing = "Vendor Returns Clearing"
        if clearing not in self.account_ids:
            made = self.run("account create", dict(name=clearing, type="other_current_asset",
                                                   description="Stock sent back to a vendor, until its credit is entered"))
            self.account_ids[clearing] = made["id"]
        for row in ev["lines"]:
            before = self.stock(ev["date"])[row["item"]][1]
            self.run("inventory adjust", dict(item=row["item"], date=ev["date"], quantity_change=f"-{row['quantity']}",
                                              adjustment_account=clearing,
                                              memo=f"Returned to {ev['vendor']}, {ev['number']}"))
            taken = before - self.stock(ev["date"])[row["item"]][1]
            rest = cents(row["amount"]) - taken  # the credit is worth more (or less) than the average took off
            if rest:
                self.run("inventory adjust", dict(item=row["item"], date=ev["date"], value_change=money(abs(rest)),
                                                  negative_value=rest > 0, adjustment_account=clearing,
                                                  memo=f"Credited value of the units returned, {ev['number']}"))
        made = self.run("vendor-credit post", dict(vendor=ev["vendor"], date=ev["date"], supplier_reference=ev["number"],
                                                   expenses=[dict(account=clearing, amount=ev["total"],
                                                                  memo="Returned stock")]))
        self.bills[(ev["vendor"], ev["number"])] = made["id"]
        self.made[ev["id"]] = made
        self.note("Stock returned to the supplier for credit", "workaround", ["inventory adjust", "vendor-credit post"],
                  "vendor credits have no item rows, and a quantity decrease cannot carry the credited value, so the "
                  "stock comes off at its average into a clearing account, a value adjustment takes off the rest of "
                  "the credited value, and the vendor credit takes the clearing account back",
                  "a vendor credit with item rows takes the quantity and the credited value off stock", event=ev["id"])

    def _write_off(self, ev: dict):
        """A write-off. A receipt must carry cash ('cash received must be positive'), so the anchor's zero receipt
        with the balance as a discount to Bad Debt is refused, and no item can post to an expense account; the
        balance is credited through a clearing account instead."""
        clearing = "Write-off Clearing"
        target = NAMES.get(ev["account"], ev["account"])
        if clearing not in self.account_ids:
            made = self.run("account create", dict(name=clearing, type="other_current_asset",
                                                   description="Customer balances written off, until moved to their account"))
            self.account_ids[clearing] = made["id"]
            self.run("item create", dict(name="Write-off", type="other_charge", price="0.00", sales_tax_code_id=self.non_taxable,
                                         description="Balance written off", income_account_id=made["id"]))
        invoice = self.invoice_id(ev["invoice"])
        inactive = not self.run("customer show", dict(customer=ev["customer"]))["active"]
        if inactive:  # a new document must name an active customer
            self.run("customer activate", dict(customer=ev["customer"]))
        credit = self.run("credit-memo post", dict(customer=ev["customer"], date=ev["date"], memo=ev["note"][:200],
                                                   lines=[dict(item="Write-off", quantity="1", unit_price=ev["amount"],
                                                               description=f"Invoice {ev['invoice']} written off")]))
        self.run("customer-credit apply", dict(
            credit_memo=credit["id"], expected_version=credit["version"], date=ev["date"],
            applications=[dict(invoice=invoice, amount=ev["amount"],
                               expected_version=self.run("invoice show", dict(invoice=invoice))["version"])]))
        journal = self.run("journal post", dict(date=ev["date"], memo=f"Invoice {ev['invoice']} written off", lines=[
            dict(account=target, side="debit", amount=ev["amount"]),
            dict(account=clearing, side="credit", amount=ev["amount"])]))
        if inactive:
            self.run("customer deactivate", dict(customer=ev["customer"]))
        self.made[ev["id"]] = dict(credit=credit, journal=journal)
        if ev["account"] == "Bad Debt":
            self.note("Bad debt written off (inactive customer)", "workaround",
                      ["account create", "item create", "customer activate", "credit-memo post", "customer-credit apply",
                       "journal post", "customer deactivate"],
                      "a receipt must carry cash, so the anchor's zero receipt with a discount to Bad Debt is refused, "
                      "and an item cannot post to an expense account; a 'Write-off' item on a clearing account "
                      "credits the invoice through a credit memo and a journal moves the amount to Bad Debt; the "
                      "inactive customer is activated for it and deactivated again (R148 is in Later)",
                      "Receive Payments with no cash and the balance as a discount to Bad Debt (or a credit memo with a "
                      "Bad Debt item), 'use it once' for the inactive name", event=ev["id"])
        else:
            self.note("Small balance waived (trip charge the customer disputed)", "workaround",
                      ["credit-memo post", "customer-credit apply", "journal post"],
                      "as the bad debt: credit memo through the write-off clearing account, journal to the discount "
                      "account", "a zero receipt with the balance as a discount", event=ev["id"])

    # ---------------------------------------------------------------- what only the statement knows
    def statement_only(self, ev: dict):
        if ev["kind"] == "bank_charge":
            made = self.run("register post", dict(account=ev["account"], date=ev["date"], direction="decrease",
                                                  amount=ev["amount"], payee=self.party("Cedar Prairie Bank"),
                                                  memo=ev["desc"].title(), category=NAMES.get(ev["expense"], ev["expense"])))
            self.note("Bank service fee seen only on the statement", "does", ["register post"],
                      "entered from the unmatched statement line, then ticked by the next import", event=ev["id"])
        else:
            made = self.run("register post", dict(account=ev["account"], date=ev["date"], direction="increase",
                                                  amount=ev["amount"], payee=self.party("Cedar Prairie Bank"),
                                                  memo=ev["desc"].title(), category=ev["income"]))
            self.note("Interest seen only on the statement", "does", ["register post"],
                      "entered from the unmatched statement line", event=ev["id"])
        self.made[ev["id"]] = made
        return made

    # ---------------------------------------------------------------- reconciliation
    def candidates(self, draft: str) -> list[dict]:
        rows, cursor = [], None
        while True:
            page = self.run("reconcile candidates", dict(draft=draft, limit=200, filters=dict(hide_after_date=False),
                                                         **({"cursor": cursor} if cursor else {})))
            rows += page["items"]
            cursor = page.get("next_cursor")
            if not cursor:
                return rows

    def mark(self, draft: dict, entries: list[dict]) -> dict:
        return self.run("reconcile mark", dict(draft=draft["id"], operation_key=new_id(),
                                               expected_version=draft["version"], entries=entries))["draft"]

    def open_reconciliation(self, account: str, statement: str, ending: str, june: str) -> dict:
        """The first reconciliation of `account`: an opening at the June statement that covers what that statement
        showed and leaves the old books' uncleared items outstanding, then the July statement's draft."""
        opening = self.run("reconcile opening start", dict(
            operation_key=new_id(), account=account, opening_date=AS_OF, entered_balance=june,
            evidence=dict(format=1, statement_reference=f"June 2026 {account} statement",
                          entered_text="Ending balance of the last statement the old books reconciled"),
            references=[]))["draft"]
        card = account == "Visa Business Card"
        uncleared = {(when, cents(amount)) for acct, _, when, _, _, _, amount, _ in D.UNCLEARED if acct == account}
        entries = []
        for row in self.candidates(opening["id"]):
            if row["date"] > AS_OF:
                continue
            amount = -row["amount"] if card else row["amount"]
            outstanding = (row["date"], amount) in uncleared or (row["date"], -amount) in uncleared
            entries.append(dict(movement=row["movement"], group_fingerprint=row["group_fingerprint"],
                                action="outstanding" if outstanding else "covered"))
        opening = self.mark(opening, entries)
        self.covered = {entry["group_fingerprint"] for entry in entries if entry["action"] == "covered"}
        draft = self.run("reconcile start", dict(operation_key=new_id(), account=account, statement_date=statement,
                                                 ending_balance=ending, opening_draft_id=opening["id"]))["draft"]
        return draft

    def reconcile(self, account: str, content: str, statement: str, ending: str, june: str,
                  csv_mapping: dict | None = None) -> dict:
        """Reconcile `account` to one statement file: import, tick by hand what the import cannot pair, enter what
        only the statement knows, import again, and finish once the difference is zero."""
        draft = self.open_reconciliation(account, statement, ending, june)
        extra = {"csv_mapping": csv_mapping} if csv_mapping else {}
        first = self.run("reconcile import", dict(account=account, content=content, draft=draft["id"], **extra))
        self.imports = [first]
        statement_only = [ev for ev in self.events if ev["kind"] in STATEMENT_ONLY and ev["account"] == account
                          and ev["date"] <= statement]
        entered = []
        for line in first["lines"]:
            if line["status"] != "unmatched":
                continue
            ev = next((ev for ev in statement_only if cents(ev["amount"]) == abs(line["amount"])
                       and ev["cleared"] == line["date"] and ev not in entered), None)
            if ev is not None:
                self.statement_only(ev)
                entered.append(ev)
        again = self.run("reconcile import", dict(account=account, content=content, draft=draft["id"], **extra))
        self.imports.append(again)
        draft = again["draft"]
        # what the import could not pair or only suggested: the person finds each in the candidate list and ticks it
        open_rows = [row for row in self.candidates(draft["id"]) if not row["selected"] and row["eligible"]]
        hand = []
        for line in again["lines"]:
            if line["status"] in ("matched", "reconciled", "duplicate") or line.get("marked"):
                continue
            same = [row for row in open_rows if row["amount"] == line["amount"] and row["date"] <= line["date"]
                    and row not in hand]
            assert len(same) == 1, ("cannot tick by hand", line, same)
            hand.append(same[0])
        if hand:
            draft = self.mark(draft, [dict(movement=row["movement"], group_fingerprint=row["group_fingerprint"],
                                           action="mark") for row in hand])
        outstanding = [row for row in self.candidates(draft["id"]) if not row["selected"] and row["date"] <= statement
                       and row["group_fingerprint"] not in self.covered]
        guards = self.run("reconcile preview", dict(draft=draft["id"], expected_version=draft["version"]))
        assert guards["balanced"] and guards["totals"]["difference"] == 0, guards["totals"]
        done = self.run("reconcile finish", dict(operation_key=new_id(), draft=draft["id"],
                                                 expected_version=draft["version"],
                                                 expected_facts_fingerprint=guards["expected_facts_fingerprint"],
                                                 dependency_guard=guards["dependency_guard"]))
        return dict(first=first, again=again, by_hand=hand, preview=guards, finish=done, entered=entered,
                    outstanding=outstanding)

    def close(self, date: str):
        return self.run("company update", dict(closing_date=date), reason="July is reconciled and reviewed")

    def keep_july(self) -> dict:
        """The whole fit check: move in, keep July, reconcile every account to its statement, close the month."""
        self.create_company()
        self.move_in()
        self.cutover_notes()
        self.settle_in()
        self.opening_detail()
        self.july()
        k = key("2026-07-31")["reconciliations"]
        results = {}
        for account, name, mapping in (("Checking", "checking-2026-07.ofx", None),
                                       ("Savings", "savings-2026-07.csv", None),
                                       ("Visa Business Card", "visa-2026-07.csv", None)):
            rec = k[account]
            results[account] = self.reconcile(account, (JULY / name).read_text(), rec["statement_date"],
                                              rec["ending_balance"], fakeco_data_june(account), mapping)
        self.reconciliation_notes(results)
        ten99 = self.run("report vendor-1099-summary", dict(date_from="2026-01-01", date_to="2026-12-31"))
        paid = "; ".join(f"{row['display_vendor_label']} {row['payments']['amount']}" for row in ten99["rows"])
        self.note("1099 subcontractor paid by bill and check", "does", ["bill post", "bill pay",
                                                                         "report vendor-1099-summary"],
                  f"the move-in keeps the vendor's 1099 flag; the summary shows his July payments ({paid})")
        self.note("1099 totals for the year at a mid-year move-in", "missing", ["report vendor-1099-summary"],
                  "the move-in brings no payment history and there is no place for a vendor's 1099 amount paid "
                  "before the cutover, so the 2026 summary lacks January to June; the old books' report has to be "
                  "added by hand at filing", "the whole year is in one file, so the 1099 summary is the year's")
        closed = self.close("2026-07-31")
        self.note("Closing date set by the person after the month is reconciled", "does", ["company update"],
                  "closing_date 2026-07-31 through the person's own client; an entry dated in July is refused "
                  "afterwards with E_PERIOD_CLOSED")
        return dict(reconciliations=results, closed=closed)

    def cutover_notes(self):
        plan, tie = self.cutover["plan"], self.cutover["tie"]
        codes = {(e["severity"], e["code"]) for e in plan["exceptions"]}
        self.note("Lists from the old books (accounts, customers and jobs, vendors, items, terms)", "does",
                  ["cutover plan", "cutover apply", "cutover tie-out"],
                  f"one Lists IIF with every list: {tie['lists']['compared']} list records compared, "
                  f"{tie['lists']['differences']} differences; Net 21 made as a new term; the inactive customer "
                  "comes in and is deactivated")
        self.note("Opening balances, open invoices and credit memo, unpaid bills and vendor credit, stock", "does",
                  ["cutover apply", "cutover tie-out"],
                  "trial balance, A/R and A/P agings and stock tie to the cent; Cutover Clearing 0.00")
        self.note("June sales tax owed to the state at the cutover", "does", ["cutover apply", "sales-tax pay"],
                  "comes in as a sales tax adjustment under the Illinois Department of Revenue and is paid with "
                  "`sales-tax pay` on July 20")
        self.note("Account numbers the new chart already uses (6100, 9100)", "workaround",
                  ["account update", "cutover plan mappings"],
                  "the plan blocks with number_taken; Bank Service Charges maps to Bank Fees and Bookflow's Other "
                  "Expense is renumbered so Interest Expense keeps 9100. Mapping Interest Expense to `create` "
                  "instead brings it in without its number and `cutover tie-out` then never ties (it reports the "
                  "account as missing and its 612.88 as two trial balance differences): a defect",
                  "the old books are the new books' chart; no collision")
        if ("warning", "item_skipped") in codes:
            self.note("Group item (Smoke Alarm Package)", "workaround", ["item create"],
                      "the IIF item list does not carry a group's members, so the move-in skips it with a warning; "
                      "it is made again by hand with its members when first used (not used in July)",
                      "the item list export carries the group and the new books keep it")
        if ("warning", "non_posting_accounts") in codes:
            self.note("Estimates and Purchase Orders accounts", "does", ["cutover plan"],
                      "non-posting accounts are left out with a warning; estimates and orders are documents here")
        self.note("Customer types, employees, payment methods in the Lists IIF", "missing", ["cutover plan"],
                  "the move-in reads accounts, customers, vendors, items and terms and ignores the other lists: "
                  "customers come in without their customer type, and employees are not made (no July entry "
                  "needed them)", "every exported list is imported")

    def reconciliation_notes(self, results: dict):
        checking = results["Checking"]
        self.note("Bank statement imported for reconciliation (OFX)", "does", ["reconcile import"],
                  f"{checking['first']['counts']['matched']} of {checking['first']['counts']['lines']} July lines "
                  "paired and ticked on the first import, including deposits, ACH debits, transfers, payroll's two "
                  "debits from one journal and the returned check")
        if checking["by_hand"]:
            self.note("Checks clearing the bank (check numbers on the statement)", "missing", ["reconcile mark"],
                      f"no check pairs: the import compares the statement's check number with the movement's "
                      f"internal document reference ('11', '19', '1', ...), never the check number, so all "
                      f"{len(checking['by_hand'])} cleared checks (written, bill payments and the refund) come back "
                      "'unmatched: no entry in the books has this amount near this date; enter it', advice that "
                      "would enter them twice. They are ticked by hand. A defect in R167",
                      "the cleared check is matched by its number and amount")
        else:
            self.note("Checks clearing the bank (check numbers on the statement)", "does", ["reconcile import"],
                      "each cleared check pairs by its number")
        self.note("Savings statement (CSV, newest first, running balance)", "does", ["reconcile import"],
                  "columns read from the headings; ending balance taken from the Balance column")
        self.note("Credit card statement (CSV download) and reconciliation", "does", ["reconcile import"],
                  "purchases, the return and the payment pair by amount and date; the CSV has no balance, so the "
                  "statement's new balance is typed from its summary")
        self.note("Statement reconciled to a zero difference and certified", "does",
                  ["reconcile opening start", "reconcile start", "reconcile preview", "reconcile finish"],
                  "checking, savings and the Visa each certified at the July statement balance")

    # ---------------------------------------------------------------- reading the books back
    def books(self, as_of: str) -> dict:
        """The trial balance, agings, sales tax owed and stock as Bookflow reports them, in the key's terms."""
        names = {row["id"]: row["full_name"] for row in self.run("account list")["items"]}
        back = {new: old for old, new in NAMES.items()}

        def pages(command: str, data: dict) -> list[dict]:
            rows, cursor = [], None
            while True:
                page = self.run(command, {**data, "limit": 200, **({"cursor": cursor} if cursor else {})})
                rows += page["rows"]
                cursor = page.get("next_cursor")
                if not cursor:
                    return rows

        tb = {}
        for row in pages("report trial-balance", dict(date_to=as_of)):
            if row["signed_net"]["minor_units"]:
                name = names[row["account_id"]]
                tb[back.get(name, name)] = row["signed_net"]["minor_units"]
        buckets = ("current", "days_1_30", "days_31_60", "days_61_90", "over_90", "total")

        def aging(command: str, label: str) -> dict:
            out = {}
            for row in pages(command, dict(as_of=as_of)):
                values = tuple(row[b]["minor_units"] for b in buckets)
                if any(values):
                    out[row[label]] = values
            return out
        tax = {row["display_agency_label"]: row["balance"]["minor_units"]
               for row in self.run("sales-tax liability", dict(as_of=as_of))["rows"]}
        stock = {row["item_name"]: (row["quantity_on_hand"], row["asset_value"]["minor_units"])
                 for row in pages("report inventory-valuation", dict(as_of=as_of))}
        return dict(trial_balance=tb, receivables=aging("report ar-aging", "display_customer_label"),
                    payables=aging("report ap-aging", "display_vendor_label"), sales_tax=tax, stock=stock)


def fakeco_data_june(account: str) -> str:
    return D.JUNE_STATEMENTS[account]


def key(as_of: str) -> dict:
    return json.loads((FIXTURE / "answer" / f"key-{as_of}.json").read_text())


def expected(as_of: str) -> dict:
    """The key in the terms `Replay.books` reads Bookflow in."""
    k = key(as_of)
    buckets = ("current", "1-30", "31-60", "61-90", "over_90", "total")
    return dict(
        trial_balance={row["account"]: cents(row["balance"]) for row in k["trial_balance"]["rows"]},
        receivables={row["name"]: tuple(cents(row[b]) for b in buckets) for row in k["receivables"]["rows"]},
        payables={row["name"]: tuple(cents(row[b]) for b in buckets) for row in k["payables"]["rows"]},
        sales_tax={row["agency"]: cents(row["owed"]) for row in k["sales_tax"]},
        stock={row["item"]: (row["on_hand"], cents(row["value"])) for row in k["inventory"]["rows"]})


def differences(books: dict, want: dict) -> list[str]:
    found = []
    for part, rows in want.items():
        have = books[part]
        for name in sorted(set(rows) | set(have)):
            if rows.get(name) != have.get(name):
                found.append(f"{part}: {name}: key {rows.get(name)} books {have.get(name)}")
    return found


def fit_rows(replay: Replay) -> list[Fit]:
    order = {"does": 0, "workaround": 1, "missing": 2}
    return sorted(replay.fit.values(), key=lambda row: order[row.status])


def main(argv: list[str]) -> None:
    root = Path(argv[0])
    import os
    os.environ["BOOKFLOW_DATA_ROOT"] = str(root)
    client = bookflow.connect(data_root=str(root))
    client.init()
    replay = Replay(root, client)
    month = replay.keep_july()
    for line in differences(replay.books("2026-07-31"), expected("2026-07-31")):
        print("DIFFERENCE", line)
    for account, rec in month["reconciliations"].items():
        print(account, rec["finish"]["totals"]["ending_balance"], rec["finish"]["totals"]["difference"],
              "by hand", len(rec["by_hand"]), "outstanding", [row["amount"] for row in rec["outstanding"]])
    counts = defaultdict(int)
    for row in fit_rows(replay):
        counts[row.status] += 1
        print(f"{row.status:10} | {row.kind} | {', '.join(row.commands)} | {', '.join(row.events)}")
    print(dict(counts))
    (root / "fit.json").write_text(json.dumps([row.__dict__ for row in fit_rows(replay)], indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
