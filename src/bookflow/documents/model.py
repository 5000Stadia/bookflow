"""What a customer-facing document says, described once, independent of how it is drawn.

This module reads through command output only. It never recomputes an amount: every
figure on a page is a string a command already returned, and every name is the name the
document captured when it was written. Nothing here knows about HTTP, PDF or a browser,
so the same description can be drawn to paper, attached to a record, or asserted in a
test.

The four documents a customer can receive are `invoice`, `sales-receipt`, `estimate` and
`statement`. Their headers differ, their line tables differ and their totals differ; the
shape below is what they share.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from bookflow.core.errors import BookflowError

# One page of statement rows the report command will return; the reader follows its
# cursor so a long account is complete on the page rather than silently truncated.
STATEMENT_PAGE = 200
STATEMENT_PAGES = 50

ADDRESS_FIELDS = ("line1", "line2", "city", "state", "postal_code", "country")

# Statement rows name themselves in the words a customer reads, never the wire value.
ENTRIES = {"balance_forward": "Balance forward", "balance_due": "Balance due",
           "invoice": "Invoice", "sales_receipt": "Sales receipt", "payment": "Payment",
           "deposit": "Deposit", "journal_entry": "Adjustment",
           "credit_memo": "Credit memo", "customer_refund": "Refund",
           "applied_credit": "Credit applied"}

AGING = (("current", "Current"), ("days_1_30", "1-30"), ("days_31_60", "31-60"),
         ("days_61_90", "61-90"), ("over_90", "Over 90"), ("total", "Total"))


def address_lines(address: Mapping[str, Any] | None) -> list[str]:
    """The recorded parts of one address, in reading order, with nothing invented."""
    return [str(address[key]) for key in ADDRESS_FIELDS
            if address and address.get(key) not in (None, "")]


def address_block(address: Mapping[str, Any] | None) -> list[str]:
    """The same address written the way it goes on an envelope: town, region and postal
    code share a line, because a printed block that stacks them reads as a list."""
    if not address:
        return []
    part = lambda key: str(address[key]) if address.get(key) not in (None, "") else ""
    locality = ", ".join(bit for bit in (part("city"), part("state")) if bit)
    locality = " ".join(bit for bit in (locality, part("postal_code")) if bit)
    return [line for line in (part("line1"), part("line2"), locality, part("country")) if line]


def _issuer_address(issuer: Mapping[str, Any]) -> list[str]:
    return address_block({key.removeprefix("address_"): value
                          for key, value in issuer.items() if key.startswith("address_")})


def _label(value: Any) -> str | None:
    """A reference's own display label, or the plain value when it is not a reference."""
    if isinstance(value, Mapping):
        for key in ("label", "full_name", "name"):
            if value.get(key):
                return str(value[key])
        return None
    return None if value in (None, "") else str(value)


def _facts(pairs) -> tuple[tuple[str, str], ...]:
    return tuple((label, str(value)) for label, value in pairs if value not in (None, ""))


@dataclass(frozen=True)
class Party:
    """One addressed block: who is sending, or who is receiving."""

    heading: str
    name: str | None = None
    lines: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.name and not self.lines


@dataclass(frozen=True)
class Column:
    label: str
    weight: float
    align: str = "left"
    wrap: bool = False


@dataclass(frozen=True)
class Total:
    label: str
    value: str
    emphasis: bool = False


@dataclass(frozen=True)
class Note:
    heading: str | None
    body: str


@dataclass(frozen=True)
class Grid:
    """A small labelled block of figures, such as the aging box under a statement."""

    heading: str
    cells: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PrintedDocument:
    kind: str
    title: str
    number: str | None
    issuer: Party
    parties: tuple[Party, ...]
    facts: tuple[tuple[str, str], ...]
    columns: tuple[Column, ...]
    rows: tuple[tuple[str, ...], ...]
    totals: tuple[Total, ...] = ()
    grids: tuple[Grid, ...] = ()
    notes: tuple[Note, ...] = ()
    alerts: tuple[str, ...] = ()
    footer: str = ""
    # `name` is what this document is called in a tab, a filename or an attachment list.
    # `subject` is the one line printed under the letterhead, and stays empty where the
    # title block already says everything -- an invoice repeats nothing about itself.
    name: str = ""
    subject: str = ""
    contact: tuple[str, ...] = field(default_factory=tuple)


Read = Callable[[str, dict[str, Any], str | None], dict[str, Any]]


def _company(read: Read, company_id: str) -> dict[str, Any]:
    """Company identity and contact details. `company show` keeps the name a user sees on
    the registration summary and everything else under `info`, so both are merged here."""
    shown = read("company show", {}, company_id)
    return {**shown["info"], "display_name": shown["display_name"]}


def _contact(info: Mapping[str, Any]) -> tuple[str, ...]:
    """How a customer reaches the company. Contact details are not captured on a
    document revision, so these are the company's current ones and nothing else is."""
    return tuple(str(info[key]) for key in ("phone", "email", "website")
                 if info.get(key) not in (None, ""))


def _issuer_party(issuer: Mapping[str, Any], info: Mapping[str, Any]) -> Party:
    name = issuer.get("display_name") or issuer.get("legal_name") or info.get("display_name")
    lines = _issuer_address(issuer) or _issuer_address(info)
    return Party("From", str(name) if name else None, tuple(lines))


def _customer_party(profile: Mapping[str, Any], heading: str = "Bill to") -> Party:
    customer = profile.get("customer") or {}
    name = _label(customer)
    company_name = customer.get("company_name") if isinstance(customer, Mapping) else None
    lines = list(address_block(profile.get("billing_address")))
    if company_name and company_name != name:
        lines.insert(0, str(company_name))
    return Party(heading, name, tuple(lines))


def _ship_party(profile: Mapping[str, Any]) -> Party:
    return Party("Ship to", None, tuple(address_block(profile.get("shipping_address"))))


def _sales_columns(revision: Mapping[str, Any]) -> tuple[Column, ...]:
    taxed = any(line["tax"]["minor_units"] for line in revision["lines"])
    columns = [Column("Item", 1.5, wrap=True), Column("Description", 3.4, wrap=True),
               Column("Qty", 0.8, align="right"), Column("Rate", 1.0, align="right")]
    if taxed:
        columns.append(Column("Tax", 0.9, align="right"))
    columns.append(Column("Amount", 1.1, align="right"))
    return tuple(columns)


def _sales_rows(revision: Mapping[str, Any], columns) -> tuple[tuple[str, ...], ...]:
    taxed = any(column.label == "Tax" for column in columns)
    rows = []
    for line in revision["lines"]:
        snapshot = line["item_snapshot"]
        rate = (line["unit_price"]["amount"] if line.get("unit_price")
                else "Quoted" if line.get("pricing_basis") == "allocated" else "")
        unit = _label(snapshot.get("unit"))
        quantity = str(line["quantity"]) + (f" {unit}" if unit else "")
        row = [_label(snapshot["item"]) or "", line.get("description") or "", quantity, rate]
        if taxed:
            row.append(line["tax"]["amount"])
        row.append(line["net"]["amount"])
        rows.append(tuple(row))
    return tuple(rows)


def _notes(profile: Mapping[str, Any], revision: Mapping[str, Any]) -> tuple[Note, ...]:
    notes = []
    message = profile.get("customer_message")
    if isinstance(message, Mapping):
        message = _label(message)
    if message:
        notes.append(Note(None, str(message)))
    if revision.get("memo"):
        notes.append(Note("Memo", str(revision["memo"])))
    return tuple(notes)


def _sale(read: Read, company_id: str, kind: str, document_id: str) -> PrintedDocument:
    """An invoice or a sales receipt: what was sold, what it came to, and what is owed."""
    selector = "invoice" if kind == "invoice" else "sales_receipt"
    record = read(f"{kind} show", {selector: document_id}, company_id)
    info = _company(read, company_id)
    revision, profile = record["revision"], record["revision"]["profile"]
    columns = _sales_columns(revision)
    invoice = kind == "invoice"
    facts = [("Date", revision["date"])]
    if invoice:
        facts += [("Terms", _label(profile.get("terms"))), ("Due date", profile.get("due_date"))]
    else:
        facts += [("Payment method", _label(profile.get("payment_method"))),
                  ("Reference", profile.get("payment_reference"))]
    facts += [("P.O. number", profile.get("customer_purchase_order")),
              ("Rep", _label(profile.get("sales_rep"))),
              ("Ship date", profile.get("ship_date")),
              ("Ship via", _label(profile.get("ship_method")))]
    totals = [Total("Subtotal", revision["subtotal"]["amount"]),
              Total("Sales tax", revision["tax"]["amount"]),
              Total("Total", revision["total"]["amount"], emphasis=not invoice)]
    settlement = record.get("settlement_current")
    if invoice and settlement:
        from bookflow.core.money import Money
        applied = Money(settlement["applied_minor_units"], settlement["currency"]).to_dict()["amount"]
        due = Money(settlement["due_minor_units"], settlement["currency"]).to_dict()["amount"]
        totals += [Total("Payments and credits", applied),
                   Total("Balance due", due, emphasis=True)]
    elif invoice:
        totals[-1] = Total("Total", revision["total"]["amount"], emphasis=True)
    alerts = []
    if record.get("status") == "voided":
        alerts.append("VOIDED" + (f" — {record['void_reason']}" if record.get("void_reason") else ""))
    title = "Invoice" if invoice else "Sales Receipt"
    return PrintedDocument(
        kind=kind, title=title, number=revision["number"],
        issuer=_issuer_party(revision.get("issuer_snapshot") or {}, info),
        parties=(_customer_party(profile), _ship_party(profile)),
        facts=_facts(facts), columns=columns, rows=_sales_rows(revision, columns),
        totals=tuple(totals), notes=_notes(profile, revision), alerts=tuple(alerts),
        footer=f"{title} {revision['number']} · {revision['currency']}",
        name=f"{title} {revision['number']}", contact=_contact(info))


def _estimate(read: Read, company_id: str, document_id: str) -> PrintedDocument:
    """A quote: the same scope and pricing the customer was shown, with no amount owed."""
    record = read("estimate show", {"estimate": document_id}, company_id)
    info = _company(read, company_id)
    revision = record["revision"]
    facts_block, profile = revision["facts"], revision["facts"]["profile"]
    taxed = any(line["tax"]["minor_units"] for line in revision["lines"])
    columns = [Column("Item", 1.5, wrap=True), Column("Description", 3.4, wrap=True),
               Column("Qty", 0.8, align="right"), Column("Rate", 1.0, align="right")]
    if taxed:
        columns.append(Column("Tax", 0.9, align="right"))
    columns.append(Column("Amount", 1.1, align="right"))
    rows = []
    for line in revision["lines"]:
        line_facts = line["facts"]
        unit = _label(line_facts["profile"].get("unit"))
        row = [_label(line_facts["profile"]["item"]) or "", line_facts.get("description") or "",
               str(line["quantity"]) + (f" {unit}" if unit else ""),
               line["unit_price"]["amount"] if line.get("unit_price") else ""]
        if taxed:
            row.append(line["tax"]["amount"])
        row.append(line["net"]["amount"])
        rows.append(tuple(row))
    facts = [("Date", revision["date"]), ("Status", str(revision["status"]).replace("_", " ")),
             ("Expires on", facts_block.get("expires_on")),
             ("Terms", _label(profile.get("terms"))),
             ("P.O. number", profile.get("customer_purchase_order")),
             ("Rep", _label(profile.get("sales_rep")))]
    notes = []
    for key, heading in (("scope", "Scope"), ("inclusions", "Included"),
                         ("exclusions", "Not included"), ("timing", "Timing"),
                         ("commercial_terms", "Commercial terms")):
        if facts_block.get(key):
            notes.append(Note(heading, str(facts_block[key])))
    message = _label(profile.get("customer_message"))
    if message:
        notes.append(Note(None, message))
    if facts_block.get("memo"):
        notes.append(Note("Memo", str(facts_block["memo"])))
    alerts = []
    if record.get("expired"):
        alerts.append("This estimate has passed its expiry date.")
    if not record.get("active", True):
        alerts.append("This estimate is closed.")
    name = f"Estimate {revision['number']}"
    return PrintedDocument(
        kind="estimate", title="Estimate", number=revision["number"],
        issuer=_issuer_party(facts_block.get("issuer_snapshot") or {}, info),
        parties=(_customer_party(profile), _ship_party(profile)),
        facts=_facts(facts), columns=tuple(columns), rows=tuple(rows),
        totals=(Total("Subtotal", revision["net"]["amount"]),
                Total("Sales tax", revision["tax"]["amount"]),
                Total("Total", revision["total"]["amount"], emphasis=True)),
        notes=tuple(notes), alerts=tuple(alerts),
        footer=f"Estimate {revision['number']} · {revision['currency']}",
        name=name + (f" · {revision['title']}" if revision.get("title") else ""),
        subject=str(revision.get("title") or ""), contact=_contact(info))


def _statement(read: Read, company_id: str, identity: Mapping[str, Any]) -> PrintedDocument:
    """One customer's account for a period: what they carried, what happened, what is owed."""
    missing = [key for key in ("customer", "date_from", "date_to") if not identity.get(key)]
    if missing:
        raise BookflowError("E_VALIDATION", details={"fields": [
            {"field": key, "problem": "a statement is one customer's account over one period"}
            for key in missing]})
    raw = {"customer": identity["customer"], "date_from": identity["date_from"],
           "date_to": identity["date_to"], "limit": STATEMENT_PAGE}
    first = read("report statement", raw, company_id)
    rows_out = list(first["rows"])
    cursor, pages = first.get("next_cursor"), 0
    while cursor and pages < STATEMENT_PAGES:
        page = read("report statement", {**raw, "cursor": cursor}, company_id)
        rows_out += page["rows"]
        cursor, pages = page.get("next_cursor"), pages + 1
    # A truncated account is worse than a long one, so where the cap is reached the page
    # says so rather than ending mid-account with nothing to show for it.
    alerts = () if not cursor else (
        f"Only the first {len(rows_out)} entries of this period are printed. "
        "Print a shorter period to see the rest.",)
    record = read("customer show", {"customer": identity["customer"]}, company_id)
    info = _company(read, company_id)
    period, currency = first["metadata"]["period"], first["metadata"]["currency"]
    columns = (Column("Date", 1.0), Column("Entry", 1.4, wrap=True),
               Column("Number", 1.2, wrap=True), Column("Details", 2.6, wrap=True),
               Column("Amount", 1.1, align="right"), Column("Balance", 1.1, align="right"))
    rows = []
    for row in rows_out:
        details = []
        if row.get("due_date"):
            details.append("Due " + row["due_date"])
        if row.get("memo"):
            details.append(str(row["memo"]))
        rows.append((row.get("date") or "", ENTRIES.get(row["entry"], row["entry"]),
                     row.get("number") or "", " · ".join(details),
                     row["amount"]["amount"], row["balance"]["amount"]))
    totals = first["totals"]
    address = record.get("effective_billing_address") or record.get("billing_address")
    party = Party("Statement for", record.get("full_name") or _label(record.get("name")),
                  tuple(([str(record["company_name"])] if record.get("company_name") else [])
                        + address_block(address)))
    return PrintedDocument(
        kind="statement", title="Statement", number=None,
        # A statement is a current reading of the books, not a captured revision, so it
        # carries the company's current name and address rather than a snapshot.
        issuer=Party("From", str(info.get("display_name") or info.get("legal_name")),
                     tuple(_issuer_address(info))),
        parties=(party,),
        facts=_facts((("Statement date", period["date_to"]),
                      ("Period from", period["date_from"]),
                      ("Currency", currency),
                      ("Basis", str(first["metadata"]["basis"]).capitalize()))),
        columns=columns, rows=tuple(rows),
        totals=(Total("Balance forward", totals["opening"]["amount"]),
                Total("Charges", totals["charges"]["amount"]),
                Total("Payments and credits", totals["credits"]["amount"]),
                Total("Balance due", totals["closing"]["amount"], emphasis=True)),
        grids=(Grid(f"What is owed, by age, as of {period['date_to']}",
                    tuple((heading, first["aging"][key]["amount"]) for key, heading in AGING)),),
        notes=(Note(None, "Invoices are aged on their due date. A voided document has no row "
                          "because it is worth nothing on its own date."),), alerts=alerts,
        footer=f"Statement · {period['date_from']} to {period['date_to']} · {currency}",
        name=f"Statement as of {period['date_to']}", contact=_contact(info))


BUILDERS = {
    "invoice": lambda read, company_id, identity: _sale(read, company_id, "invoice", identity["document"]),
    "sales-receipt": lambda read, company_id, identity: _sale(read, company_id, "sales-receipt", identity["document"]),
    "estimate": lambda read, company_id, identity: _estimate(read, company_id, identity["document"]),
    "statement": _statement,
}


def build(read: Read, company_id: str, kind: str, identity: Mapping[str, Any]) -> PrintedDocument:
    """Describe one document. `read(command, input, company_id)` runs a registered command."""
    if kind not in BUILDERS:
        raise BookflowError("E_USAGE", message=f"`{kind}` is not a printable document")
    return BUILDERS[kind](read, company_id, identity)
