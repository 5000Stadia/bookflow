"""Harbor Electric LLC: a deterministic fake small business for the R170 fit check.

`python -m tests.fakeco` rewrites tests/fixtures/fakeco/ from tests/fakeco_data.py:

- `handed-over/`: what the owner gives a bookkeeper. The old books at the 2026-06-30 cutover as
  QuickBooks Desktop exports them (one Lists IIF, trial balance, open invoices, unpaid bills, both
  aging summaries, inventory valuation, the June reconciliation summaries and the transactions
  still uncleared), then per month the paperwork in plain words and the bank, savings and card
  statements (checking as OFX and CSV).
- `answer/`: every event as data (`events.json`) and an answer key per month end: the trial
  balance, receivable and payable agings, open documents, sales tax owed, stock, and each bank and
  card account's reconciliation (cleared and outstanding items).

The key is computed here from the same events the files are written from, never from Bookflow.
Its conventions are the anchor desktop product's and are listed in the key itself (`CONVENTIONS`).
Nothing reads the clock or an unseeded random number, so a rerun writes the same bytes.
"""
from __future__ import annotations

import copy
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from pathlib import Path

from tests import fakeco_data as D

OUT = Path(__file__).resolve().parent / "fixtures" / "fakeco"
CUTOVER = date.fromisoformat(D.COMPANY["cutover"])
MONTH_ENDS = [date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30)]
AGENCY = D.COMPANY["tax_agency"]
RATE = Decimal(D.COMPANY["tax_rate"])  # percent
HOLIDAYS = {date(2026, 7, 3), date(2026, 9, 7)}  # Independence Day observed, Labor Day: no bank postings
DOT = "·"  # the desktop product's separator between an account's number and name

CONVENTIONS = [
    "Accrual basis. Trial balance amounts are signed, debit positive; accounts with a zero balance are left out.",
    "Account names are the old books' full names (Parent:Child); numbers are the old books' numbers.",
    "Sales tax: one Illinois Department of Revenue rate of 8.75%, figured once per document on the sum of its "
    "taxable lines (taxable item for a taxable customer) and rounded half up to the cent; owed from the document "
    "date; a credit memo takes back its own tax.",
    "A percentage discount item takes its percent of the line just above it, rounded half up.",
    "Inventory is weighted-average cost: a sale takes value x quantity / quantity on hand, rounded half to even, "
    "and the last unit out takes whatever value is left; a purchase adds its cost; stock returned to a vendor "
    "comes off at the credited amount; stock a customer returns comes back at what its sale took.",
    "Early-payment discounts are the terms' percent of the open balance, rounded half up, to Sales Discounts "
    "(customers) or Purchase Discounts (vendors); sales tax is not adjusted for them.",
    "A bounced customer check is recorded as the anchor's Record Bounced Check does: the invoice it paid is open "
    "again, the returned amount and the bank's fee come out of checking (fee to Bank Service Charges), and the "
    "customer is invoiced the returned-check fee (Returned Check Charges).",
    "A bad debt is written off with a zero receipt whose discount goes to Bad Debt.",
    "Agings are by due date for invoices and bills and by document date for credits, in the columns Current, "
    "1-30, 31-60, 61-90 and over 90 days past due, by customer:job and vendor.",
    "Reconciliations: an item is cleared on the day the bank or card company posted it; the statement's "
    "beginning balance is the previous statement's ending balance; outstanding items are entries dated on or "
    "before the statement date that the statement does not show.",
]


# ---------------------------------------------------------------- money and dates

def cents(text) -> int:
    value = Decimal(str(text).replace(",", "").replace("$", ""))
    scaled = value * 100
    assert scaled == scaled.to_integral_value(), text
    return int(scaled)


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def round_half_even(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def money(c: int) -> str:
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}{c // 100}.{c % 100:02d}"


def qb_money(c: int) -> str:
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}{c // 100:,}.{c % 100:02d}"


def day(text: str) -> date:
    return date.fromisoformat(text)


def us(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def next_business_day(d: date, at_least: int = 0) -> date:
    d = d + timedelta(days=at_least)
    while not business_day(d):
        d += timedelta(days=1)
    return d


def tax_on(base: int) -> int:
    return round_half_up(Decimal(base) * RATE / 100)


def terms_of(name: str) -> tuple[int, Decimal, int]:
    for term, due, percent, discount_days in D.TERMS:
        if term == name:
            return due, Decimal(percent.rstrip("%") or "0"), discount_days
    raise KeyError(name)


# ---------------------------------------------------------------- lists

def customer(name: str) -> dict:
    return D.CUSTOMERS.get(name) or D.NEW_CUSTOMERS[name]


def vendor(name: str) -> dict:
    return D.VENDORS.get(name) or D.NEW_VENDORS[name]


def item(name: str) -> dict:
    return D.ITEMS.get(name) or D.NEW_ITEMS[name]


ACCOUNT_NUMBERS = {name: number for number, name, *_ in D.ACCOUNTS + D.NEW_ACCOUNTS}
ACCOUNT_TYPES = {name: kind for _, name, kind, *_ in D.ACCOUNTS + D.NEW_ACCOUNTS}
ACCOUNT_ORDER = [name for number, name, *_ in sorted(D.ACCOUNTS + D.NEW_ACCOUNTS, key=lambda a: (not a[0], a[0]))]


def qb_account(name: str) -> str:
    """`1500 · Vehicles:1510 · Cost`: the trial balance's form of an account's full name."""
    parts, path = [], []
    for part in name.split(":"):
        path.append(part)
        number = ACCOUNT_NUMBERS[":".join(path)]
        parts.append(f"{number} {DOT} {part}" if number else part)
    return ":".join(parts)


# ---------------------------------------------------------------- the books

@dataclass
class Doc:
    kind: str  # invoice, credit_memo, payment (receivables); bill, credit (payables)
    number: str
    date: date
    name: str  # customer:job or vendor
    due: date | None
    total: int
    open: int
    terms: str = ""
    po: str = ""
    tax: int = 0
    issued: dict = field(default_factory=dict)  # stocked item -> (quantity, cost) a sale took


@dataclass
class Move:
    """One entry on a bank or card account as the books hold it, and when the statement shows it."""
    account: str
    date: date
    amount: int  # bank: money in positive; card: what is owed, a charge positive
    cleared: date | None
    event: str
    kind: str  # check, deposit, ach, transfer, fee, interest, charge, credit, payment ...
    number: str = ""
    name: str = ""
    memo: str = ""
    desc: str = ""
    trntype: str = ""
    category: str = ""
    card_date: date | None = None


class Books:
    def __init__(self):
        self.gl: dict[str, int] = defaultdict(int)
        self.ar: dict[str, Doc] = {}
        self.ap: dict[tuple[str, str], Doc] = {}
        self.stock: dict[str, list] = {}
        self.tax: list[tuple[date, int, str]] = []
        self.undeposited: dict[str, int] = {}
        self.moves: list[Move] = []
        self.events: list[dict] = []
        self.payments: dict[str, dict] = {}
        self.statement_balances = {name: cents(v) for name, v in D.JUNE_STATEMENTS.items()}
        self.loan = 0
        self.last_check = 4482
        self.last_invoice = 2404

    # -- posting
    def post(self, event: str, lines: list[tuple[str, int]]):
        assert sum(amount for _, amount in lines) == 0, (event, lines)
        for account, amount in lines:
            assert account in ACCOUNT_NUMBERS, account
            self.gl[account] += amount

    def issue(self, name: str, quantity: Decimal) -> int:
        on_hand, value = self.stock[name]
        assert quantity <= on_hand, (name, quantity, on_hand)
        consumed = value if quantity == on_hand else round_half_even(Decimal(value) * quantity / on_hand)
        self.stock[name] = [on_hand - quantity, value - consumed]
        return consumed

    def receive(self, name: str, quantity: Decimal, value: int):
        on_hand, held = self.stock[name]
        self.stock[name] = [on_hand + quantity, held + value]

    def tax_owed(self, through: date | None = None) -> int:
        return sum(amount for when, amount, _ in self.tax if through is None or when <= through)

    def move(self, **fields):
        self.moves.append(Move(**fields))

    # -- the old books at the cutover
    def open_cutover(self):
        for number, when, name, terms, due, po, amount in D.OPEN_INVOICES:
            self.ar[number] = Doc("invoice", number, day(when), name, day(due), cents(amount), cents(amount), terms, po)
        for number, when, name, amount in D.OPEN_CREDITS:
            self.ar[number] = Doc("credit_memo", number, day(when), name, None, -cents(amount), -cents(amount))
        for name, number, when, terms, due, amount in D.UNPAID_BILLS:
            self.ap[(name, number)] = Doc("bill", number, day(when), name, day(due), cents(amount), cents(amount), terms)
        for name, number, when, amount in D.OPEN_VENDOR_CREDITS:
            self.ap[(name, number)] = Doc("credit", number, day(when), name, None, -cents(amount), -cents(amount))
        for name, (quantity, value) in D.STOCK.items():
            self.stock[name] = [Decimal(quantity), cents(value)]
        for name, spec in D.ITEMS.items():
            if spec["kind"] == "INVENTORY":
                self.stock.setdefault(name, [Decimal(0), 0])
        for when, name, number, amount, _ in D.UNDEPOSITED:
            self.undeposited["uf:" + name] = cents(amount)
        self.tax.append((CUTOVER, cents(D.CUTOVER_SALES_TAX), "cutover"))
        self.loan = -cents(D.CUTOVER_BALANCES["Van Loan"])

        gl = self.gl
        for name, amount in D.CUTOVER_BALANCES.items():
            gl[name] += cents(amount)
        gl["Accounts Receivable"] += sum(doc.open for doc in self.ar.values())
        gl["Accounts Payable"] -= sum(doc.open for doc in self.ap.values())
        gl["Inventory Asset"] += sum(value for _, value in self.stock.values())
        gl["Undeposited Funds"] += sum(self.undeposited.values())
        gl["Sales Tax Payable"] -= self.tax_owed()
        uncleared = defaultdict(int)
        for account, kind, when, number, name, memo, amount, cleared in D.UNCLEARED:
            uncleared[account] += cents(amount)
            card = account == "Visa Business Card"
            self.move(account=account, date=day(when), amount=cents(amount), cleared=day(cleared), event="cutover",
                      kind={"Check": "check", "Bill Pmt -Check": "check", "Deposit": "deposit"}.get(kind, "charge"),
                      number=number, name=name, memo="BRANCH DEPOSIT" if kind == "Deposit" else "",
                      desc=("CHECK " + number) if number else ("DEPOSIT" if kind == "Deposit" else _card_desc(name, memo)),
                      trntype="CHECK" if number else ("DEP" if kind == "Deposit" else ""),
                      category="Home" if card and name else ("Food & Drink" if card else ""),
                      card_date=day(when) if card else None)
        gl["Checking"] += cents(D.JUNE_STATEMENTS["Checking"]) + uncleared["Checking"]
        gl["Visa Business Card"] -= cents(D.JUNE_STATEMENTS["Visa Business Card"]) + uncleared["Visa Business Card"]
        assert gl["Savings"] == cents(D.JUNE_STATEMENTS["Savings"])
        plug = -sum(gl.values())
        assert plug < 0, plug  # retained earnings carry a credit balance
        gl["Retained Earnings"] += plug

    # -- events
    def apply(self, ev: dict):
        handler = getattr(self, "_" + ev["kind"])
        detail = handler(ev) or {}
        self.events.append({**ev, **detail})

    def _invoice_lines(self, ev: dict, taxable_customer: bool) -> tuple[list[dict], int, int]:
        lines, previous = [], None
        for name, quantity, rate, desc in ev["lines"]:
            spec = item(name)
            if spec["kind"] == "DISC":
                percent = Decimal(spec["price"].rstrip("%"))
                amount = round_half_up(Decimal(previous) * percent / 100)
                lines.append(dict(item=name, quantity=None, rate=spec["price"], amount=amount, desc=desc or spec["desc"],
                                  taxable=False, account=spec["income"]))
            else:
                q = Decimal(quantity)
                price = cents(rate if rate is not None else spec["price"])
                amount = round_half_up(q * price)
                lines.append(dict(item=name, quantity=str(q), rate=money(price), amount=amount,
                                  desc=desc or spec["desc"], taxable=spec["taxable"] and taxable_customer,
                                  account=spec["income"]))
            previous = lines[-1]["amount"]
        base = sum(line["amount"] for line in lines if line["taxable"])
        tax = tax_on(base)
        return lines, base, tax

    def _sale(self, ev: dict, debit_account: str) -> dict:
        info = customer(ev["customer"])
        lines, base, tax = self._invoice_lines(ev, info["taxable"])
        total = sum(line["amount"] for line in lines) + tax
        posting = [(debit_account, total)]
        issued = {}
        for line in lines:
            posting.append((line["account"], -line["amount"]))
            if item(line["item"])["kind"] == "INVENTORY":
                cost = self.issue(line["item"], Decimal(line["quantity"]))
                posting += [("Cost of Goods Sold", cost), ("Inventory Asset", -cost)]
                line["cost"] = cost
                quantity, held = issued.get(line["item"], (Decimal(0), 0))
                issued[line["item"]] = (quantity + Decimal(line["quantity"]), held + cost)
        if tax:
            posting.append(("Sales Tax Payable", -tax))
            self.tax.append((day(ev["date"]), tax, ev["id"]))
        self.post(ev["id"], posting)
        return dict(lines=lines, taxable=base, tax=tax, total=total, issued=issued)

    def _invoice(self, ev: dict):
        info = customer(ev["customer"])
        terms = ev.get("terms") or info["terms"]
        due_days, _, _ = terms_of(terms)
        when = day(ev["date"])
        detail = self._sale(ev, "Accounts Receivable")
        doc = Doc("invoice", ev["number"], when, ev["customer"], when + timedelta(days=due_days), detail["total"],
                  detail["total"], terms, ev.get("po", ""), detail["tax"], detail["issued"])
        self.ar[ev["number"]] = doc
        return {**_jsonable(detail), "terms": terms, "due": doc.due.isoformat()}

    def _sales_receipt(self, ev: dict):
        detail = self._sale(ev, "Undeposited Funds")
        self.undeposited[ev["id"]] = detail["total"]
        return _jsonable(detail)

    def _credit_memo(self, ev: dict):
        info = customer(ev["customer"])
        lines, base, tax = self._invoice_lines(ev, info["taxable"])
        total = sum(line["amount"] for line in lines) + tax
        source = self.ar[ev["source_invoice"]]
        posting = [("Accounts Receivable", -total)]
        for line in lines:
            posting.append((line["account"], line["amount"]))
            if item(line["item"])["kind"] == "INVENTORY":
                quantity = Decimal(line["quantity"])
                sold, cost = source.issued[line["item"]]
                value = round_half_even(Decimal(cost) * quantity / sold)
                on_hand, held = self.stock[line["item"]]
                average = round_half_even(Decimal(held) * quantity / on_hand) if on_hand else value
                assert value == average, ("a return whose sale cost and current average differ", value, average)
                self.receive(line["item"], quantity, value)
                posting += [("Inventory Asset", value), ("Cost of Goods Sold", -value)]
                line["cost"] = value
        if tax:
            posting.append(("Sales Tax Payable", tax))
            self.tax.append((day(ev["date"]), -tax, ev["id"]))
        self.post(ev["id"], posting)
        self.ar[ev["number"]] = Doc("credit_memo", ev["number"], day(ev["date"]), ev["customer"], None, -total, -total)
        return _jsonable(dict(lines=lines, taxable=base, tax=tax, total=total))

    def _discount_for(self, doc: Doc, when: date, how) -> int:
        if how is None:
            return 0
        if how != "terms":
            return cents(how)
        _, percent, days = terms_of(doc.terms)
        assert percent and (when - doc.date).days <= days, ("no discount on these terms today", doc.number, when)
        return round_half_up(Decimal(doc.open) * percent / 100)

    def _payment(self, ev: dict):
        when = day(ev["date"])
        discounts = {number: how for number, how in ev.get("discounts", [])}
        applied, total_discount = [], 0
        for number, amount in ev["apply"]:
            doc = self.ar[number]
            discount = self._discount_for(doc, when, discounts.get(number))
            paid = doc.open - discount if amount is None else cents(amount)
            assert 0 < paid + discount <= doc.open, (ev["id"], number)
            doc.open -= paid + discount
            total_discount += discount
            applied.append(dict(invoice=number, amount=money(paid), discount=money(discount)))
        amount = sum(cents(a["amount"]) for a in applied)
        if ev.get("amount") is not None:
            assert cents(ev["amount"]) == amount, ev["id"]
        posting = [("Undeposited Funds", amount), ("Accounts Receivable", -(amount + total_discount))]
        if total_discount:
            posting.append(("Sales Discounts", total_discount))
        self.post(ev["id"], posting)
        self.undeposited[ev["id"]] = amount
        self.payments[ev["id"]] = dict(customer=ev["customer"], applied=applied, amount=amount, ref=ev.get("ref"))
        return dict(amount=money(amount), applied=applied, discount=money(total_discount))

    def _credit_apply(self, ev: dict):
        amount = cents(ev["amount"])
        credit, invoice = self.ar[ev["credit"]], self.ar[ev["invoice"]]
        assert -credit.open >= amount and invoice.open >= amount
        credit.open += amount
        invoice.open -= amount
        return {}

    def _deposit(self, ev: dict):
        sources = [*ev.get("payments", []), *("uf:" + name for name in ev.get("undeposited", []))]
        amounts = [self.undeposited.pop(source) for source in sources]
        total = sum(amounts)
        self.post(ev["id"], [("Checking", total), ("Undeposited Funds", -total)])
        self.move(account="Checking", date=day(ev["date"]), amount=total, cleared=_cleared(ev), event=ev["id"],
                  kind="deposit", desc="DEPOSIT", trntype="DEP", memo="BRANCH DEPOSIT")
        return dict(total=money(total), items=[dict(source=s, amount=money(a)) for s, a in zip(sources, amounts)])

    def _bill(self, ev: dict):
        info = vendor(ev["vendor"])
        terms = ev.get("terms") or info["terms"] or "Due on receipt"
        due_days, _, _ = terms_of(terms)
        when = day(ev["date"])
        posting, lines = [], []
        for name, quantity, cost, job in ev.get("items", []):
            q = Decimal(quantity)
            amount = round_half_up(q * cents(cost))
            if item(name)["kind"] == "INVENTORY":
                self.receive(name, q, amount)
                posting.append(("Inventory Asset", amount))
            else:
                posting.append((item(name)["expense"], amount))
            lines.append(dict(item=name, quantity=str(q), cost=cost, amount=money(amount), job=job))
        for account, amount, memo, job in ev.get("expenses", []):
            posting.append((account, cents(amount)))
            lines.append(dict(account=account, amount=amount, memo=memo, job=job))
        total = sum(amount for _, amount in posting)
        posting.append(("Accounts Payable", -total))
        self.post(ev["id"], posting)
        self.ap[(ev["vendor"], ev["number"])] = Doc("bill", ev["number"], when, ev["vendor"],
                                                    when + timedelta(days=due_days), total, total, terms)
        return dict(total=money(total), terms=terms, due=(when + timedelta(days=due_days)).isoformat(), lines=lines)

    def _vendor_credit(self, ev: dict):
        posting, lines = [], []
        for name, quantity, cost, job in ev.get("items", []):
            q = Decimal(quantity)
            amount = round_half_up(q * cents(cost))
            on_hand, value = self.stock[name]
            assert q <= on_hand
            self.stock[name] = [on_hand - q, value - amount]
            posting.append(("Inventory Asset", -amount))
            lines.append(dict(item=name, quantity=str(q), cost=cost, amount=money(amount), job=job))
        for account, amount, memo, job in ev.get("expenses", []):
            posting.append((account, -cents(amount)))
            lines.append(dict(account=account, amount=amount, memo=memo, job=job))
        total = -sum(amount for _, amount in posting)
        posting.append(("Accounts Payable", total))
        self.post(ev["id"], posting)
        self.ap[(ev["vendor"], ev["number"])] = Doc("credit", ev["number"], day(ev["date"]), ev["vendor"], None,
                                                    -total, -total)
        return dict(total=money(total), lines=lines)

    def _vendor_credit_apply(self, ev: dict):
        amount = cents(ev["amount"])
        credit, bill = self.ap[(ev["vendor"], ev["credit"])], self.ap[(ev["vendor"], ev["bill"])]
        assert -credit.open >= amount and bill.open >= amount
        credit.open += amount
        bill.open -= amount
        return {}

    def _bill_payment(self, ev: dict):
        when = day(ev["date"])
        paid_lines, total, total_discount = [], 0, 0
        for number, amount, how in ev["bills"]:
            doc = self.ap[(ev["vendor"], number)]
            discount = self._discount_for(doc, when, how)
            paid = doc.open - discount if amount is None else cents(amount)
            assert 0 < paid + discount <= doc.open
            doc.open -= paid + discount
            total += paid
            total_discount += discount
            paid_lines.append(dict(bill=number, amount=money(paid), discount=money(discount)))
        posting = [("Accounts Payable", total + total_discount), ("Checking", -total)]
        if total_discount:
            posting.append(("Purchase Discounts", -total_discount))
        self.post(ev["id"], posting)
        check = ev["method"] == "Check"
        self.move(account="Checking", date=when, amount=-total, cleared=_cleared(ev), event=ev["id"],
                  kind="check" if check else "ach", number=ev.get("number") or "", name=ev["vendor"],
                  desc=("CHECK " + ev["number"]) if check else ev["desc"], trntype="CHECK" if check else "DEBIT",
                  memo="" if check else ev.get("memo", "ACH DEBIT"))
        return dict(total=money(total), discount=money(total_discount), bills=paid_lines)

    def _check(self, ev: dict):
        lines = [(account, cents(amount)) for account, amount, _ in ev["lines"]]
        total = sum(amount for _, amount in lines)
        self.post(ev["id"], lines + [("Checking", -total)])
        if "Van Loan" in dict(lines):
            self.loan -= dict(lines)["Van Loan"]
        check = ev["number"] != "ACH"
        self.move(account="Checking", date=day(ev["date"]), amount=-total, cleared=_cleared(ev), event=ev["id"],
                  kind="check" if check else "ach", number=ev["number"] if check else "", name=ev["payee"],
                  desc=("CHECK " + ev["number"]) if check else ev["desc"], trntype="CHECK" if check else ev["trntype"],
                  memo="" if check else ev.get("memo", ""))
        return dict(total=money(total))

    def _card_charge(self, ev: dict, sign: int = 1):
        lines = [(account, cents(amount)) for account, amount, _ in ev["lines"]]
        total = sum(amount for _, amount in lines)
        self.post(ev["id"], [(a, sign * v) for a, v in lines] + [("Visa Business Card", -sign * total)])
        self.move(account="Visa Business Card", date=day(ev["date"]), amount=sign * total, cleared=_cleared(ev),
                  event=ev["id"], kind="charge" if sign > 0 else "credit", name=ev.get("payee") or "",
                  desc=ev["desc"], category=ev.get("category", ""), card_date=day(ev["date"]))
        return dict(total=money(total))

    def _card_credit(self, ev: dict):
        return self._card_charge(ev, -1)

    def _transfer(self, ev: dict):
        amount = cents(ev["amount"])
        target = ev["to_account"]
        self.post(ev["id"], [(target, amount), (ev["from_account"], -amount)])
        when = day(ev["date"])
        self.move(account=ev["from_account"], date=when, amount=-amount, cleared=_cleared(ev), event=ev["id"],
                  kind="transfer", desc=ev["desc"], trntype=ev.get("trntype", "XFER"))
        if target == "Visa Business Card":
            self.move(account=target, date=when, amount=-amount, cleared=_cleared(ev), event=ev["id"],
                      kind="payment", desc=ev["card_desc"], category="", card_date=when)
        else:
            self.move(account=target, date=when, amount=amount, cleared=_cleared(ev), event=ev["id"], kind="transfer",
                      desc=ev["savings_desc"], trntype="XFER")
        return {}

    def _sales_tax_payment(self, ev: dict):
        through = day(ev["through"])
        owed = self.tax_owed(through)
        amount = cents(ev["amount"]) if ev.get("amount") else owed
        assert amount == owed, ("sales tax paid is not what was owed", amount, owed)
        self.tax.append((day(ev["date"]), -amount, ev["id"]))
        self.post(ev["id"], [("Sales Tax Payable", amount), ("Checking", -amount)])
        self.move(account="Checking", date=day(ev["date"]), amount=-amount, cleared=_cleared(ev), event=ev["id"],
                  kind="ach", name=ev["agency"], desc=ev["desc"], trntype="DEBIT",
                  memo="ST-1 " + through.strftime("%b%y").upper())
        return dict(amount=money(amount))

    def _bounced_check(self, ev: dict):
        payment = self.payments[ev["payment"]]
        amount = payment["amount"]
        when = day(ev["date"])
        for applied in payment["applied"]:
            self.ar[applied["invoice"]].open += cents(applied["amount"])
        fee, charge = cents(ev["bank_fee"]), cents(ev["customer_fee"])
        self.post(ev["id"], [("Accounts Receivable", amount), ("Checking", -amount),
                             ("Bank Service Charges", fee), ("Checking", -fee),
                             ("Accounts Receivable", charge), ("Returned Check Charges", -charge)])
        self.move(account="Checking", date=when, amount=-amount, cleared=_cleared(ev), event=ev["id"],
                  kind="returned", name=payment["customer"], desc="RETURNED DEPOSITED ITEM", trntype="DEBIT",
                  memo="CHECK " + payment["ref"])
        self.move(account="Checking", date=when, amount=-fee, cleared=_cleared(ev), event=ev["id"] + "-fee",
                  kind="fee", desc="RETURN ITEM FEE", trntype="FEE")
        self.ar[ev["fee_number"]] = Doc("invoice", ev["fee_number"], when, payment["customer"], when, charge, charge,
                                        "Due on receipt")
        return dict(amount=money(amount), customer=payment["customer"], ref=payment["ref"])

    def _customer_refund(self, ev: dict):
        credit = self.ar[ev["credit"]]
        amount = -credit.open
        credit.open = 0
        self.post(ev["id"], [("Accounts Receivable", amount), ("Checking", -amount)])
        self.move(account="Checking", date=day(ev["date"]), amount=-amount, cleared=_cleared(ev), event=ev["id"],
                  kind="check", number=ev["number"], name=ev["customer"], desc="CHECK " + ev["number"], trntype="CHECK")
        return dict(amount=money(amount))

    def _inventory_adjust(self, ev: dict):
        quantity = Decimal(ev["quantity"])
        assert quantity < 0
        value = self.issue(ev["item"], -quantity)
        self.post(ev["id"], [(ev["account"], value), ("Inventory Asset", -value)])
        return dict(value=money(-value))

    def _write_off(self, ev: dict):
        doc = self.ar[ev["invoice"]]
        amount = doc.open
        doc.open = 0
        self.post(ev["id"], [(ev["account"], amount), ("Accounts Receivable", -amount)])
        return dict(amount=money(amount))

    def _payroll(self, ev: dict):
        rows, gross_total, net_total, withheld_total, employer_total = [], 0, 0, 0, 0
        for name, (regular, overtime) in ev["hours"].items():
            rate = cents(D.EMPLOYEES[name]["rate"])
            gross = round_half_up(Decimal(regular) * rate + Decimal(overtime) * rate * Decimal("1.5"))
            federal = round_half_up((Decimal(gross) - 36538) * Decimal("0.10"))
            social, medicare = round_half_up(Decimal(gross) * Decimal("0.062")), round_half_up(Decimal(gross) * Decimal("0.0145"))
            state = round_half_up(Decimal(gross) * Decimal("0.0495"))
            withheld = federal + social + medicare + state
            employer = social + medicare
            rows.append(dict(employee=name, regular=regular, overtime=overtime, gross=money(gross), federal=money(federal),
                             social_security=money(social), medicare=money(medicare), state=money(state),
                             net=money(gross - withheld), employer_taxes=money(employer)))
            gross_total += gross
            withheld_total += withheld
            employer_total += employer
            net_total += gross - withheld
        taxes = withheld_total + employer_total
        when = day(ev["date"])
        self.post(ev["id"], [("Payroll Expenses:Wages", gross_total), ("Payroll Expenses:Payroll Taxes", employer_total),
                             ("Checking", -net_total), ("Checking", -taxes)])
        stamp = day(ev["pay_date"]).strftime("%m%d%y")
        self.move(account="Checking", date=when, amount=-net_total, cleared=_cleared(ev), event=ev["id"] + "-net",
                  kind="ach", name="Paywell Payroll Services", desc="PAYWELL PAYROLL", memo="NET PAY " + stamp,
                  trntype="DEBIT")
        self.move(account="Checking", date=when, amount=-taxes, cleared=_cleared(ev), event=ev["id"] + "-tax",
                  kind="ach", name="Paywell Payroll Services", desc="PAYWELL PAYROLL", memo="TAXES " + stamp,
                  trntype="DEBIT")
        return dict(employees=rows, gross=money(gross_total), employee_taxes=money(withheld_total),
                    employer_taxes=money(employer_total), net=money(net_total), tax_debit=money(taxes))

    def _bank_charge(self, ev: dict):
        amount = cents(ev["amount"])
        self.post(ev["id"], [(ev["expense"], amount), (ev["account"], -amount)])
        self.move(account=ev["account"], date=day(ev["date"]), amount=-amount, cleared=_cleared(ev), event=ev["id"],
                  kind="fee", desc=ev["desc"], trntype="SRVCHG")
        return {}

    def _bank_interest(self, ev: dict):
        amount = cents(ev["amount"])
        self.post(ev["id"], [(ev["account"], amount), (ev["income"], -amount)])
        self.move(account=ev["account"], date=day(ev["date"]), amount=amount, cleared=_cleared(ev), event=ev["id"],
                  kind="interest", desc=ev["desc"], trntype="INT")
        return {}


def _cleared(ev: dict) -> date | None:
    return day(ev["cleared"]) if ev.get("cleared") else None


def _card_desc(name: str, memo: str) -> str:
    return name.upper() if name else "JOLIET DINER"


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return money(value)
    return value


# ---------------------------------------------------------------- August and September

def later_months(books: Books, month: int, rng: random.Random) -> list[dict]:
    """A lighter month of routine work, planned from what is open at the start of the month.

    `books` is a copy: planning reads it and may scribble on it, and the real books then run the
    events this returns. Check and invoice numbers are given in date order at the end.
    """
    first = date(2026, month, 1)
    last = date(2026, month + 1, 1) - timedelta(days=1)
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    work = [d for d in days if business_day(d)]
    events: list[dict] = []
    prefix = {8: "A", 9: "S"}[month]

    def add(**ev):
        events.append(ev)
        return ev

    def bday(d: date, lag: int = 0) -> date:
        return next_business_day(d, lag)

    # rent, ads, software, insurance, internet, the loan, draws
    rent_day = bday(first)
    add(kind="check", date=rent_day.isoformat(), number="CHK", payee="Joliet Industrial Partners LLC",
        lines=[("Rent", "2450.00", f"{first:%B} rent")], cleared=bday(rent_day, 3).isoformat(),
        note=f"{first:%B} rent check.")
    add(kind="card_charge", date=rent_day.isoformat(), payee="SearchLocal Ads",
        lines=[("Advertising", "250.00", f"{first:%B} search ads")], cleared=bday(rent_day, 1).isoformat(),
        desc="SEARCHLOCAL ADS", category="Professional Services", note="SearchLocal ads.")
    fp = date(2026, month, 3)
    add(kind="card_charge", date=fp.isoformat(), payee="FieldPro Software",
        lines=[("Dues and Subscriptions", "89.00", "FieldPro monthly")], cleared=bday(fp, 1).isoformat(),
        desc="FIELDPRO SOFTWARE", category="Bills & Utilities", note="FieldPro subscription.")
    ins = bday(date(2026, month, 5))
    add(kind="check", date=ins.isoformat(), number="ACH", payee="Midwest Trades Insurance Agency",
        lines=[("Insurance:General Liability", "902.00", f"{first:%B} installment"),
               ("Insurance:Workers Compensation", "1055.00", f"{first:%B} installment")],
        cleared=ins.isoformat(), desc="MIDWEST TRADES INS", memo="PREMIUM " + ins.strftime("%m%d%y"), trntype="DEBIT",
        note="Insurance installment (general liability 902.00, workers comp 1,055.00).")
    vb = date(2026, month, 12)
    add(kind="card_charge", date=vb.isoformat(), payee="Valley Broadband",
        lines=[("Utilities:Telephone and Internet", "189.99", f"Internet and phones, {first:%B}")],
        cleared=bday(vb, 1).isoformat(), desc="VALLEY BROADBAND", category="Bills & Utilities",
        note="Valley Broadband autopay on the card.")
    loan_day = bday(date(2026, month, 15))
    interest = round_half_up(Decimal(books.loan) * Decimal("0.0429") / 12)
    principal = 68952 - interest
    add(kind="check", date=loan_day.isoformat(), number="ACH", payee="Cedar Prairie Bank",
        lines=[("Van Loan", money(principal), "Principal"), ("Interest Expense", money(interest), "Interest")],
        cleared=loan_day.isoformat(), desc="LOAN PAYMENT 88231", memo="AUTO DEBIT", trntype="PAYMENT",
        note=f"Van loan payment 689.52 (principal {money(principal)}, interest {money(interest)}).")
    for when in (bday(date(2026, month, 14)), last if business_day(last) else bday(last - timedelta(days=2))):
        add(kind="check", date=when.isoformat(), number="ACH", payee="Harbor, Mike", payee_kind="other_name",
            lines=[("Owner's Draw", "2000.00", "Draw")], cleared=when.isoformat(), desc="ONLINE TRANSFER TO CHK X5531",
            memo="HARBOR M", trntype="XFER", note="$2,000 to my personal account.")

    # payroll every other Thursday after July 23, and Paywell's fee
    run = date(2026, 7, 23)
    while run <= last:
        run += timedelta(days=14)
        if run.month != month:
            continue
        overtime = str(rng.choice([0, 0, 2, 4, 6]))
        kayla = str(rng.choice([80, 80, 80, 72, 76]))
        add(kind="payroll", date=run.isoformat(), period_end=(run - timedelta(days=5)).isoformat(),
            pay_date=(run + timedelta(days=1)).isoformat(),
            hours={"Ortega, Luis": ("80", overtime), "Brandt, Kayla": (kayla, "0")}, cleared=run.isoformat(),
            note=f"Payroll for Friday {run + timedelta(days=1):%m/%d}.")
    fee_day = bday(date(2026, month, 21))
    add(kind="check", date=fee_day.isoformat(), number="ACH", payee="Paywell Payroll Services",
        lines=[("Payroll Expenses:Payroll Service Fees", "98.50", f"{first:%B} service fee")],
        cleared=fee_day.isoformat(), desc="PAYWELL PAYROLL FEE", memo=f"{first:%b}".upper(), trntype="DEBIT",
        note="Paywell's monthly fee.")

    # last month's sales tax on the 20th or the next business day; the card's last statement on the 24th
    tax_day = bday(date(2026, month, 20))
    previous_end = first - timedelta(days=1)
    owed = books.tax_owed(previous_end)
    add(kind="sales_tax_payment", date=tax_day.isoformat(), agency=AGENCY, through=previous_end.isoformat(),
        amount=money(owed), cleared=tax_day.isoformat(), desc="IL DEPT OF REVENUE EFT",
        note=f"Filed and paid {previous_end:%B}'s sales tax, {money(owed)}.")
    statement = books.statement_balances["Visa Business Card"]
    pay_day = bday(date(2026, month, 24))
    add(kind="transfer", date=pay_day.isoformat(), from_account="Checking", to_account="Visa Business Card",
        amount=money(statement), cleared=pay_day.isoformat(), desc="CEDAR PRAIRIE VISA PMT", trntype="PAYMENT",
        card_desc="PAYMENT THANK YOU", note=f"Paid the {previous_end:%B} Visa statement in full, {money(statement)}.")

    # bills open at the start of the month, paid by their due dates; Midland's credits go on its first bill
    for (name, number), doc in sorted(books.ap.items(), key=lambda kv: (kv[1].due or kv[1].date, kv[0])):
        if doc.kind != "bill" or doc.open <= 0:
            continue
        due = doc.due or doc.date
        pay = bday(first, 2) if due < first else max(bday(due - timedelta(days=4)), bday(first))
        if pay > last:
            continue
        if name == "Midland Electric Supply":
            for credit in [d for (v, _), d in sorted(books.ap.items()) if v == name and d.kind == "credit" and d.open < 0]:
                take = min(-credit.open, doc.open)
                add(kind="vendor_credit_apply", date=pay.isoformat(), vendor=name, credit=credit.number, bill=number,
                    amount=money(take), note=f"Used Midland credit {credit.number} on {number}.")
                credit.open += take
                doc.open -= take
        if name == "Illinois Valley Gas & Electric":
            add(kind="bill_payment", date=bday(due).isoformat(), vendor=name, method="ACH", number=None,
                bills=[(number, None, None)], cleared=bday(due).isoformat(), desc="IL VALLEY G&E AUTOPAY",
                note=f"Gas & electric autopay took {number}.")
        elif name == "Prairie Fleet Card":
            add(kind="bill_payment", date=pay.isoformat(), vendor=name, method="ACH", number=None,
                bills=[(number, None, None)], cleared=pay.isoformat(), desc="PRAIRIE FLEET CARD PMT",
                note=f"Paid the fuel card bill {number} online.")
        else:
            add(kind="bill_payment", date=pay.isoformat(), vendor=name, method="Check", number="CHK",
                bills=[(number, None, None)], cleared=bday(pay, rng.choice([3, 4, 5])).isoformat(),
                note=f"Paid {name} {number}.")

    # receivables open at the start of the month
    for number, doc in sorted(books.ar.items()):
        if doc.kind != "invoice" or doc.open <= 0 or doc.name == "Washington, Darnell":
            continue
        if doc.name == "Route 59 Auto Care" and doc.open == 4900:
            add(kind="write_off", date=bday(first, 3).isoformat(), customer=doc.name, invoice=number,
                account="Sales Discounts",
                note="Talked to Dave at Route 59 - we'll waive the $49 trip charge on 2392. Clear it off.")
            continue
        due = doc.due or doc.date
        when = max(bday(due - timedelta(days=rng.randint(0, 6))), bday(first, rng.randint(1, 6)))
        if when <= last:
            _pay(add, doc, when, rng)

    # new work: invoices from a few templates; quick payers pay inside the month
    pool = [name for name, spec in sorted(D.CUSTOMERS.items())
            if not spec["inactive"] and spec["job_status"] != "Closed"
            and name not in ("Cash Customer", "Washington, Darnell")
            and not any(other.startswith(name + ":") for other in D.CUSTOMERS)]
    pool += sorted(D.NEW_CUSTOMERS)
    planned = {name: value[0] for name, value in books.stock.items()}
    jobs = 14 if month == 8 else 16
    used: dict[str, int] = defaultdict(int)
    for n in range(jobs):
        when = work[min(len(work) - 1, round((n + 0.5) * len(work) / jobs))]
        who = rng.choice([name for name in pool if used[name] < 2])
        used[who] += 1
        info = customer(who)
        commercial = info["ctype"] in ("Commercial", "Property Management", "General Contractor")
        lines = []
        if rng.random() < 0.55 and not commercial:
            lines.append(("Service Call", "1", None, rng.choice(["No power to garage", "Flickering lights",
                                                                 "Tripping breaker", "Outlet repair",
                                                                 "Doorbell transformer", "Bathroom fan wiring"])))
            if rng.random() < 0.5:
                lines.append(("Additional Hour", str(rng.choice([1, 1, 2])), None, None))
        else:
            hours = rng.choice([2, 3, 4, 6, 8, 12, 16])
            small = ["Add a circuit", "Fixture swap", "Exterior outlet", "Ceiling fan install", "Smoke alarm wiring"]
            large = ["Kitchen remodel circuits", "Lighting retrofit", "Subpanel install", "EV circuit",
                     "Basement finish wiring", "Unit make-ready"]
            lines.append(("Labor", str(hours), None, rng.choice(small if hours <= 4 else large)))
            if hours >= 6:
                lines.append(("Apprentice Labor", str(hours), None, None))
        for stocked in rng.sample(["GFCI Receptacle 20A", "LED Wafer Light 6in", "Smoke/CO Detector", "Breaker 20A 1P",
                                   "Breaker 20A AFCI", "Surge Protector"], rng.choice([0, 1, 1, 2])):
            want = Decimal(1) if stocked == "Surge Protector" else Decimal(rng.choice([1, 2, 2, 3, 4, 6]))
            if planned[stocked] >= want + 2:
                planned[stocked] -= want
                lines.append((stocked, str(want), None, None))
        if rng.random() < 0.6:
            lines.append(("Materials", "1", f"{rng.randint(2400, 61000) / 100:.2f}", rng.choice(
                ["Wire, boxes, fittings", "Fixtures and trim", "Conduit and fittings", "Devices and plates"])))
        ev = add(kind="invoice", date=when.isoformat(), number="INV", customer=who, lines=lines,
                 note=f"Invoiced {_spoken(who)}.")
        terms = info["terms"]
        quick = {"Due on receipt": (0, 6), "Net 15": (8, 18)}.get(terms)
        if quick:
            paid = bday(when, rng.randint(*quick))
            if paid <= last:
                _pay(add, ev, paid, rng)
        elif terms == "1% 10 Net 30" and rng.random() < 0.5:
            paid = bday(when, rng.randint(5, 9))
            if (paid - when).days <= 10 and paid <= last:
                _pay(add, ev, paid, rng, discount=True)
    if month == 8:
        add(kind="invoice", date="2026-08-26", number="INV", customer="Van Dyke Construction:Lot 14 Prairie Crossing",
            po="VDC-14-04", lines=[("Labor", "1", "4740.00", "Final, balance of $18,600 contract")],
            note="Lot 14 final billing, the rest of the contract.")
        add(kind="bill", date="2026-08-12", vendor="Delgado, Ray", number="0812",
            expenses=[("Subcontractors", "1325.00", "Pulling feeders, 205 N Chicago",
                       "Ridgeline Realty Management:205 N Chicago St")], note="Ray's invoice 0812.")
    else:
        add(kind="bill", date="2026-09-15", vendor="Lund & Ostrowski CPAs", number="2026-0815",
            expenses=[("Professional Fees", "425.00", "Q3 estimate and bookkeeping review", None)],
            note="Lund & Ostrowski bill.")
        add(kind="bill", date="2026-09-11", vendor="Delgado, Ray", number="0911",
            expenses=[("Subcontractors", "980.00", "Trenching, Hickory Creek pole lights",
                       "Lakeview Property Management:Hickory Creek Townhomes")], note="Ray's invoice 0911.")

    # Midland: a restock on the first Tuesday, job materials every Tuesday
    tuesdays = [d for d in work if d.weekday() == 1]
    restock = [(name, str(int(spec["reorder"]) * 2), spec["cost"], None) for name, spec in D.ITEMS.items()
               if spec["kind"] == "INVENTORY" and planned[name] <= Decimal(spec["reorder"]) * 2]
    jobs_open = [k for k, spec in sorted(D.CUSTOMERS.items()) if ":" in k and spec["job_status"] != "Closed"]
    for n, tuesday in enumerate(tuesdays):
        expenses = [("Job Materials", f"{rng.randint(15000, 140000) / 100:.2f}", "Job materials", rng.choice(jobs_open))]
        add(kind="bill", date=tuesday.isoformat(), vendor="Midland Electric Supply",
            number=f"S{1192000 + month * 1000 + tuesday.day * 7}.001", items=restock if n == 0 else [],
            expenses=expenses, note="Midland bill" + (" (stock order and job materials)." if n == 0 else "."))

    # small card purchases, the fuel card statement, gas & electric, uniforms
    purchases = [("Plainfield Hardware & Lumber", "Job Materials", "PLAINFIELD HARDWARE & LUMBER", "Home", (1500, 9000)),
                 ("Plainfield Hardware & Lumber", "Small Tools and Equipment", "PLAINFIELD HARDWARE & LUMBER", "Home",
                  (2500, 16000)),
                 ("Prairie Office Supply", "Office Supplies", "PRAIRIE OFFICE SUPPLY", "Shopping", (1800, 9000)),
                 (None, "Meals", "JOLIET DINER", "Food & Drink", (1800, 7500))]
    for n in range(7):
        when = work[min(len(work) - 1, 1 + n * 3)]
        payee, account, desc, category, (low, high) = rng.choice(purchases)
        add(kind="card_charge", date=when.isoformat(), payee=payee,
            lines=[(account, f"{rng.randint(low, high) / 100:.2f}", account)], cleared=bday(when, 1).isoformat(),
            desc=desc, category=category, note=f"Card: {payee or desc.title()}, {account.lower()}.")
    add(kind="bill", date=last.isoformat(), vendor="Prairie Fleet Card", number=f"{last:%m%d}-7741",
        expenses=[("Vehicle Expense:Fuel", f"{rng.randint(105000, 139000) / 100:.2f}", f"{first:%B} fuel", None)],
        note=f"Fleet card statement for {first:%B} fuel.")
    gas = bday(date(2026, month, 19))
    add(kind="bill", date=gas.isoformat(), vendor="Illinois Valley Gas & Electric",
        number=f"6619-0442 {first:%b}".upper(), terms="Net 21",
        expenses=[("Utilities:Gas and Electric", f"{rng.randint(36000, 47000) / 100:.2f}", f"Shop, {first:%B}", None)],
        note="Gas & electric bill.")
    add(kind="bill", date=bday(date(2026, month, 24)).isoformat(), vendor="Prairie Uniform Service",
        number=f"U-{89107 + (month - 7) * 711}", expenses=[("Uniforms", "112.36", f"{first:%B} service", None)],
        note="Uniform bill.")

    # in date order; deposits on Tuesdays and Fridays of what has come in; numbers in the order written
    events.sort(key=lambda ev: (ev["date"], _paper_class(ev["kind"]), _order(ev["kind"])))
    final, waiting = [], []
    for when in days:
        todays = [ev for ev in events if ev["date"] == when.isoformat()]
        final += todays
        waiting += [ev for ev in todays if ev["kind"] == "payment"]
        if waiting and business_day(when) and when.weekday() in (1, 4):
            final.append(dict(kind="deposit", date=when.isoformat(), cleared=when.isoformat(),
                              payments=[ev for ev in waiting], note="Deposited the checks."))
            waiting = []
    if waiting:
        when = last if business_day(last) else bday(last - timedelta(days=3))
        final.append(dict(kind="deposit", date=when.isoformat(), cleared=bday(last, 1).isoformat(),
                          payments=[ev for ev in waiting], note="Dropped the last checks at the bank after closing."))
    final.append(dict(kind="bank_charge", date=last.isoformat(), account="Checking", amount="15.00",
                      cleared=last.isoformat(), desc="MONTHLY MAINTENANCE FEE", expense="Bank Service Charges"))
    for account, amount in (("Checking", rng.choice(["0.79", "0.91", "1.04"])), ("Savings", rng.choice(["4.72", "4.74"]))):
        final.append(dict(kind="bank_interest", date=last.isoformat(), account=account, amount=amount,
                          cleared=last.isoformat(), desc="INTEREST PAYMENT", income="Interest Income"))
    for n, ev in enumerate(final, start=1):
        ev["id"] = f"{prefix}{n:02d}"
        if ev.get("number") == "CHK":
            books.last_check += 1
            ev["number"] = str(books.last_check)
        elif ev.get("number") == "INV":
            books.last_invoice += 1
            ev["number"] = str(books.last_invoice)
    for ev in final:  # references by event, now that every event has its id and number
        if ev["kind"] == "deposit":
            ev["payments"] = [p["id"] for p in ev["payments"]]
        if ev["kind"] == "payment" and isinstance(ev["apply"][0][0], dict):
            target = ev["apply"][0][0]
            ev["apply"] = [(target["number"], None)]
            ev["discounts"] = [(target["number"], "terms")] if ev["discounts"] else []
            ev["note"] = ev["note"].replace("{number}", target["number"])
    return final


def _pay(add, doc, when: date, rng: random.Random, discount: bool = False):
    """Plan a customer's check for one invoice: an open one (a Doc) or one planned this month (an event)."""
    if isinstance(doc, Doc):
        name, number = doc.name, doc.number
        discount = discount or (doc.terms == "1% 10 Net 30" and (when - doc.date).days <= 10)
        target = number
    else:
        name, number, target = doc["customer"], "{number}", doc
    top = name.split(":")[0]
    add(kind="payment", date=when.isoformat(), customer=top, ref=str(rng.randint(1000, 39999)), method="Check",
        apply=[(target, None)], discounts=[(target, "terms")] if discount else [],
        note=f"{_spoken(top)} paid {number}" + (", less their 1%." if discount else "."))


def _spoken(name: str) -> str:
    """How the owner says a name: `Ana Morales`, `Lakeview (Hickory Creek Townhomes)`."""
    top, _, job = name.partition(":")
    if ", " in top and not customer(top)["company"]:
        last, first = top.split(", ", 1)
        top = f"{first} {last}"
    return f"{top} ({job})" if job else top


def _paper_class(kind: str) -> int:
    """Where a kind of event falls within one day so that stock moves in the order the paperwork lists it.

    The paperwork lists a week or a month section by section: invoices, sales and credit memos first (they take stock
    out and put returns back), then bills and vendor credits (stock in and back out), then the rest, inventory
    adjustments last (render.SECTIONS). Bookflow costs same-date stock movements in entry order, so a person entering
    the paperwork as written gets the answer key's figures only if the key takes the day in that order. Everything
    else moves no stock and keeps its own order, which keeps each payment after its invoice and each credit
    application before the payment it reduces.
    """
    return 0 if kind in ("invoice", "sales_receipt", "credit_memo") else 1 if kind in ("bill", "vendor_credit") else 2


def _order(kind: str) -> int:
    return ["bill", "vendor_credit", "vendor_credit_apply", "invoice", "sales_receipt", "credit_memo", "credit_apply",
            "payment", "write_off", "bill_payment"].index(kind) if kind in (
        "bill", "vendor_credit", "vendor_credit_apply", "invoice", "sales_receipt", "credit_memo", "credit_apply",
        "payment", "write_off", "bill_payment") else 20


# ---------------------------------------------------------------- the run

@dataclass
class Snapshot:
    as_of: date
    gl: dict
    ar: dict
    ap: dict
    stock: dict
    tax: int


def build() -> tuple[Books, list[Snapshot]]:
    books = Books()
    books.open_cutover()
    snapshots = [Snapshot(CUTOVER, dict(books.gl), copy.deepcopy(books.ar), copy.deepcopy(books.ap),
                          copy.deepcopy(books.stock), books.tax_owed())]
    july = sorted(D.JULY, key=lambda ev: (ev["date"], _paper_class(ev["kind"])))  # stable: the list's own order within a class
    for ev in july:
        books.apply(ev)
    rng = random.Random(170)
    for month_end in MONTH_ENDS:
        if month_end.month > 7:
            for ev in later_months_real(books, month_end.month, rng):
                books.apply(ev)
        snapshots.append(Snapshot(month_end, dict(books.gl), copy.deepcopy(books.ar), copy.deepcopy(books.ap),
                                  copy.deepcopy(books.stock), books.tax_owed()))
        books.statement_balances = statement_balances(books, month_end)
    return books, snapshots


def later_months_real(books: Books, month: int, rng: random.Random) -> list[dict]:
    """`later_months` on a copy, so its planning never touches the books it reads."""
    planner = copy.deepcopy(books)
    events = later_months(planner, month, rng)
    books.last_check, books.last_invoice = planner.last_check, planner.last_invoice
    return events


def statement_balances(books: Books, month_end: date) -> dict:
    balances = {}
    for account in ("Checking", "Savings", "Visa Business Card"):
        opening = cents(D.JUNE_STATEMENTS[account])
        balances[account] = opening + sum(m.amount for m in books.moves if m.account == account and m.cleared
                                          and CUTOVER < m.cleared <= month_end)
    return balances


if __name__ == "__main__":
    from tests import fakeco_render
    fakeco_render.main(sys.argv[1:])
