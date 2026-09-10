"""Accrual receivables reports over the same immutable effects the statements read.

Both reports start from Accounts Receivable posting effects dated on or before
the as-of date and then move each active settlement application from the
receipt that supplied the credit onto the invoice it settles. That movement
transfers an amount between two rows and never creates or destroys one, so the
aging total is exactly the Accounts Receivable balance the balance sheet
reports for the same date, whatever the settlement history looks like.

Aging is by due date for an invoice and by accounting date for everything else
that reaches Accounts Receivable, which is what gives an unapplied customer
credit and a receivable journal entry a column of their own. Rows whose columns
are all zero -- a paid invoice, a voided one, a fully applied receipt -- are
omitted, which cannot move a total.

The statement is the same arithmetic read along the date axis instead of the
age axis. A customer's closing balance is every Accounts Receivable effect
dated on or before the period end, which is the identical expression the aging
sums for that customer, so the two always agree and their totals are both the
Accounts Receivable balance on the balance sheet. Between the opening balance
and the closing balance the statement lists one row per document per date at
what that document did to the receivable on that date, so a document worth
nothing -- a voided invoice, whose reversal carries the original date -- has no
row because it has no amount, not because a status was filtered. Applying a
receipt to an invoice of the same customer posts nothing and moves nothing that
customer owes, so it produces no row; a settlement whose paying party and whose
invoice customer are different customers does move a balance between them, and
each side gets its own row, because otherwise one of the two statements would
not add up. New cash is owned by the party whose invoice it settles, so a
parent's receipt that pays a job's invoice is already the job's for the settled
part and the parent's for the rest, and no command available today produces the
second case at all. Carrying it anyway is what makes a customer's closing
balance and that customer's aging row the same expression, rather than the same
expression only for as long as that stays true.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.core.errors import BookflowError

# Column order is the order a bookkeeper reads an aging, and the JSON field
# names are the columns. Edges are exact: an invoice due exactly 30 days before
# the as-of date is 1-30, and one due exactly 31 days before is 31-60.
BUCKETS = ("current", "days_1_30", "days_31_60", "days_61_90", "over_90")
BUCKET_EDGES = (0, 30, 60, 90)
# SQL never names a bucket, so no column alias can collide with a keyword.
_COLUMNS = (*(f"bucket_{index}" for index in range(len(BUCKETS))), "total")


def bucket_edges(as_of: str) -> dict[str, str]:
    """The as-of date and the three past-due boundary dates, as ISO date text.

    Calendar arithmetic happens once, here, in exact whole days; SQL only ever
    compares ISO date text, whose lexicographic order is its calendar order.
    Dates before year 1 do not exist, so an early as-of date clamps rather than
    underflowing, which collapses the older columns instead of failing.
    """
    ordinal = date.fromisoformat(as_of).toordinal()
    return {f"edge{index}": date.fromordinal(max(1, ordinal - days)).isoformat()
            for index, days in enumerate(BUCKET_EDGES)}


def days_past_due(as_of: str, aging_date: str) -> int:
    """Whole days this row is past due; zero or negative while it is current."""
    return date.fromisoformat(as_of).toordinal() - date.fromisoformat(aging_date).toordinal()


def bucket_of(as_of: str, aging_date: str) -> str:
    """The Python twin of _BUCKET; one rule, two evaluators, same boundaries."""
    days = days_past_due(as_of, aging_date)
    for index, edge in enumerate(BUCKET_EDGES):
        if days <= edge:
            return BUCKETS[index]
    return BUCKETS[-1]


_BUCKET = ("CASE WHEN aging_date>=:edge0 THEN 0 WHEN aging_date>=:edge1 THEN 1 "
           "WHEN aging_date>=:edge2 THEN 2 WHEN aging_date>=:edge3 THEN 3 ELSE 4 END")


class AsOfInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive accounting as-of date, YYYY-MM-DD; receivables are aged against it.")
    basis: Literal["accrual"] = "accrual"
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        """The report period's single inclusive bound, named as every report names it."""
        return self.as_of


class ArAgingInput(AsOfInput):
    pass


class OpenInvoicesInput(AsOfInput):
    customer: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional customer or job ID or canonical full name; a job is its own customer and is not included with its parent.")
    past_due_only: bool = Field(default=False, description="Omit invoices that are not yet due on the as-of date.")


class StatementInput(StrictModel):
    date_from: str = Field(min_length=10, max_length=10, description="Inclusive first accounting date of the statement period, YYYY-MM-DD; everything before it is the opening balance.")
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last accounting date, YYYY-MM-DD; the closing balance and the aging columns are as of this date.")
    basis: Literal["accrual"] = "accrual"
    customer: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional customer or job ID or canonical full name; a job is its own customer and is not included with its parent. Omit for every customer with a balance or with activity in the period.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _dates = field_validator("date_from", "date_to")(iso_date)

    @model_validator(mode="after")
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class ArAgingTotals(StrictModel):
    current: MoneyOutput
    days_1_30: MoneyOutput
    days_31_60: MoneyOutput
    days_61_90: MoneyOutput
    over_90: MoneyOutput
    total: MoneyOutput


class ArAgingRow(StrictModel):
    customer_id: str | None
    current_customer_label: str | None
    current_customer_name: str | None
    display_customer_label: str
    parent_id: str | None
    active: bool | None
    current: MoneyOutput
    days_1_30: MoneyOutput
    days_31_60: MoneyOutput
    days_61_90: MoneyOutput
    over_90: MoneyOutput
    total: MoneyOutput


class OpenInvoicesTotals(StrictModel):
    amount: MoneyOutput
    applied: MoneyOutput
    balance: MoneyOutput


class OpenInvoiceRow(StrictModel):
    transaction_id: str
    number: str
    date: str
    due_date: str
    days_past_due: int
    aging_bucket: Literal["current", "days_1_30", "days_31_60", "days_61_90", "over_90"]
    settlement_status: Literal["unpaid", "partly_paid"]
    customer_id: str | None
    current_customer_label: str | None
    current_customer_name: str | None
    display_customer_label: str
    parent_id: str | None
    amount: MoneyOutput
    applied: MoneyOutput
    balance: MoneyOutput


class StatementTotals(StrictModel):
    opening: MoneyOutput
    charges: MoneyOutput
    credits: MoneyOutput
    closing: MoneyOutput


class StatementRow(StrictModel):
    kind: Literal["opening", "activity", "closing"]
    entry: Literal["balance_forward", "invoice", "sales_receipt", "payment", "deposit",
                   "journal_entry", "applied_credit", "balance_due"]
    customer_id: str | None
    current_customer_label: str | None
    current_customer_name: str | None
    display_customer_label: str
    parent_id: str | None
    active: bool | None
    date: str | None
    transaction_id: str | None
    number: str | None
    document_date: str | None
    due_date: str | None
    memo: str | None
    amount: MoneyOutput
    balance: MoneyOutput


class ArAgingOutput(ledger.Page):
    totals: ArAgingTotals
    rows: list[ArAgingRow]


class StatementOutput(ledger.Page):
    totals: StatementTotals
    aging: ArAgingTotals
    rows: list[StatementRow]


class OpenInvoicesOutput(ledger.Page):
    totals: OpenInvoicesTotals
    rows: list[OpenInvoiceRow]


NO_CUSTOMER = "No name"

# Never let SQLite do arithmetic on an aggregate: bookflow_sum_int returns
# lossless text and SQLite would coerce text arithmetic to REAL. Only the
# stored one-sided posting line and the stored application amount are
# subtracted or negated here, and both are INTEGER columns. Second-level
# aggregation over the text is exact because bookflow_sum_int itself accepts
# lossless integer text.
_EFFECTS = """
WITH ar AS (
 SELECT l.transaction_id AS tx, l.name_id AS party,
        l.debit_minor_units-l.credit_minor_units AS amount, b.effective_date AS effect_date
 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
 JOIN accounts a ON a.id=l.account_id
 WHERE a.type='accounts_receivable' AND b.effective_date<=:as_of
), settled AS (
 SELECT s.paid_transaction_id AS invoice, s.paying_transaction_id AS receipt,
        k.party_id AS credit_party, s.amount_minor_units AS amount,
        s.effective_date AS effect_date
 FROM applications s
 JOIN payment_component_keys k ON k.transaction_id=s.paying_transaction_id
                              AND k.id=s.source_component_key_id
 WHERE s.kind='apply' AND s.effective_date<=:as_of
   AND NOT EXISTS (SELECT 1 FROM applications u
                   WHERE u.reverses_application_id=s.id AND u.effective_date<=:as_of)
), settled_party AS (
 SELECT s.invoice, s.receipt, s.credit_party, s.amount, s.effect_date,
        p.customer_id AS debit_party
 FROM settled s JOIN transactions t ON t.id=s.invoice
 JOIN sales_profiles p ON p.revision_id=t.current_revision_id
), signed AS (
 SELECT tx, party, amount FROM ar
 UNION ALL SELECT invoice, debit_party, -amount FROM settled_party
 UNION ALL SELECT receipt, credit_party, amount FROM settled_party
), document AS (
 SELECT tx, party, bookflow_sum_int(amount) AS net FROM signed GROUP BY tx, party
), dated AS (
 SELECT d.tx, d.party, d.net, t.type AS document_type, t.number AS document_number,
        r.date AS document_date,
        CASE WHEN t.type='invoice' THEN p.due_date ELSE r.date END AS aging_date
 FROM document d JOIN transactions t ON t.id=d.tx
 JOIN transaction_revisions r ON r.id=t.current_revision_id
 LEFT JOIN sales_profiles p ON p.revision_id=t.current_revision_id
)
"""

_AGING = _EFFECTS + f""", party_columns AS (
 SELECT party,
   {', '.join(f"bookflow_sum_int(CASE WHEN {_BUCKET}={index} THEN net ELSE '0' END) AS bucket_{index}" for index in range(len(BUCKETS)))},
   bookflow_sum_int(net) AS total
 FROM dated GROUP BY party
), selected AS (
 SELECT p.*, c.id AS customer_id, c.full_name, c.name, c.full_name_key, c.parent_id, c.active
 FROM party_columns p LEFT JOIN customers c ON c.id=p.party
 WHERE {' OR '.join(f"p.{name}!='0'" for name in _COLUMNS)}
) """

_OPEN = _EFFECTS + f""", ledger_gross AS (
 SELECT tx, party, bookflow_sum_int(amount) AS gross FROM ar GROUP BY tx, party
), invoice_applied AS (
 SELECT invoice, debit_party, bookflow_sum_int(amount) AS applied
 FROM settled_party GROUP BY invoice, debit_party
), selected AS (
 SELECT d.tx, d.party, d.net, d.document_number, d.document_date, d.aging_date,
   coalesce(g.gross,'0') AS gross, coalesce(a.applied,'0') AS applied,
   {_BUCKET} AS bucket,
   c.id AS customer_id, c.full_name, c.name, c.full_name_key, c.parent_id
 FROM dated d
 LEFT JOIN ledger_gross g ON g.tx=d.tx AND g.party IS d.party
 LEFT JOIN invoice_applied a ON a.invoice=d.tx AND a.debit_party IS d.party
 LEFT JOIN customers c ON c.id=d.party
 WHERE d.document_type='invoice' AND d.net!='0'
   AND (:customer IS NULL OR d.party=:customer)
   AND (:past_due_only=0 OR {_BUCKET}>0)
) """

# Customers read in hierarchy-name order, so a job follows the parent it is
# named under; parties with no customer record follow them all, by stable id.
_CUSTOMER_ORDER = "full_name_key IS NULL, full_name_key, coalesce(party,'')"


# One statement row is one document's effect on one customer's receivable on one
# date. `movement_rank` separates a posted document effect from a settlement
# movement so the two never collapse into each other, and it also orders a
# same-day pair so a payment is read before the credit it releases.
#
# A settlement between a customer and itself is deliberately absent: its two
# halves are equal and opposite within that one customer, so a row for each
# would show a balance moving that never moved. Across a parent and its job the
# halves land on different customers and both rows are real; across the whole
# report they still cancel, which is why the closing total stays the Accounts
# Receivable balance whatever the settlement history looks like.
_PARTIES = _EFFECTS + """, movement AS (
 SELECT tx, party, effect_date, 0 AS movement_rank, amount FROM ar
 UNION ALL
 SELECT invoice, debit_party, effect_date, 1, -amount FROM settled_party
  WHERE debit_party IS NOT credit_party
 UNION ALL
 SELECT receipt, credit_party, effect_date, 1, amount FROM settled_party
  WHERE debit_party IS NOT credit_party
), entry AS (
 SELECT tx, party, effect_date, movement_rank, bookflow_sum_int(amount) AS net
 FROM movement GROUP BY tx, party, effect_date, movement_rank
), party_scope AS (
 SELECT party FROM entry WHERE :customer IS NULL OR party IS :customer
 UNION SELECT :customer WHERE :customer IS NOT NULL
), party_balance AS (
 SELECT s.party,
   bookflow_sum_int(CASE WHEN e.effect_date<:date_from THEN e.net END) AS opening,
   bookflow_sum_int(e.net) AS closing
 FROM party_scope s LEFT JOIN entry e ON e.party IS s.party
 GROUP BY s.party
), party_included AS (
 -- A customer named on the request always gets a statement, even an empty one.
 -- Otherwise nothing is printed for a customer who owed nothing, was owed
 -- nothing, and did nothing, which cannot move a total either way.
 SELECT * FROM party_balance b
 WHERE :customer IS NOT NULL OR b.opening!='0' OR b.closing!='0'
    OR EXISTS (SELECT 1 FROM entry e WHERE e.party IS b.party
               AND e.effect_date>=:date_from AND e.net!='0')
), timeline AS (
 SELECT party, 0 AS phase, '' AS effect_date, 0 AS movement_rank, '' AS tx, opening AS net
 FROM party_included
 UNION ALL
 SELECT e.party, 1, e.effect_date, e.movement_rank, e.tx, e.net
 FROM entry e JOIN party_included b ON b.party IS e.party
 WHERE e.effect_date>=:date_from AND e.net!='0'
) """

# The running balance is a window over the whole customer, computed before any
# page slice, so the balance printed on page two is the balance the customer
# would read off a single sheet.
_STATEMENT = _PARTIES + """, running AS (
 SELECT t.*, bookflow_sum_int(net) OVER (
   PARTITION BY party ORDER BY phase, effect_date, movement_rank, tx
   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS balance
 FROM timeline t
), flat AS (
 SELECT party, phase, effect_date, movement_rank, tx, net, balance FROM running
 UNION ALL
 SELECT party, 2, '', 0, '', '0', closing FROM party_included
), selected AS (
 SELECT f.*, t.type AS document_type, r.number AS document_number, r.date AS document_date,
   r.memo AS document_memo, p.due_date,
   c.id AS customer_id, c.full_name, c.name, c.full_name_key, c.parent_id, c.active
 FROM flat f
 LEFT JOIN transactions t ON t.id=f.tx
 LEFT JOIN transaction_revisions r ON r.id=t.current_revision_id
 LEFT JOIN sales_profiles p ON p.revision_id=t.current_revision_id
 LEFT JOIN customers c ON c.id=f.party
) """

# The foot is the aging report's own columns over the statement's own customers,
# read from the same `dated` rows, so a statement and the aging summary never
# disagree about the same customer on the same date.
# An aggregate over no rows at all is SQL NULL rather than a sum of nothing, so
# every column is defaulted here: a company with no receivables has an aging.
_STATEMENT_AGING = _PARTIES + f"""SELECT
  {', '.join(f"coalesce(bookflow_sum_int(CASE WHEN {_BUCKET}={index} THEN d.net ELSE '0' END),'0')" for index in range(len(BUCKETS)))},
  coalesce(bookflow_sum_int(d.net),'0')
 FROM dated d JOIN party_scope s ON s.party IS d.party"""


def _label(full_name):
    return NO_CUSTOMER if full_name is None else str(full_name)


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def ar_aging(inp: ArAgingInput, s, *, principal_id=None) -> ArAgingOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "ar-aging", principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, **bucket_edges(inp.as_of)}
        totals = dict.fromkeys(_COLUMNS, 0)
        # Stream every customer's column set, including rows past this page, so
        # a page boundary can never hide an out-of-range amount from a total.
        for row in raw.execute(_AGING + f"SELECT {', '.join(_COLUMNS)} FROM selected", params):
            for key, value in zip(_COLUMNS, row):
                totals[key] += money(int(value), currency).minor_units
        if sum(totals[name] for name in _COLUMNS[:-1]) != totals["total"]:
            raise BookflowError("E_INTERNAL", message="Aging columns do not sum to the aging total")
        page = _rows(raw.execute(_AGING + f"""SELECT * FROM selected
            ORDER BY {_CUSTOMER_ORDER} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = [ArAgingRow(customer_id=row["customer_id"], current_customer_label=row["full_name"],
            current_customer_name=row["name"], display_customer_label=_label(row["full_name"]),
            parent_id=row["parent_id"], active=None if row["active"] is None else bool(row["active"]),
            **{name: money(int(row[column]), currency) for name, column in zip((*BUCKETS, "total"), _COLUMNS)})
            for row in page[:inp.limit]]
        return ArAgingOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=ArAgingTotals(**{name: money(totals[column], currency)
                                    for name, column in zip((*BUCKETS, "total"), _COLUMNS)}),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def open_invoices(inp: OpenInvoicesInput, s, *, principal_id=None) -> OpenInvoicesOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        customer_id = None
        if inp.cursor is not None:
            # Keep the stable ID resolved for the first page: renaming the
            # selected customer must stale the continuation rather than turn it
            # into a record-not-found on page two.
            customer_id = ledger._decode_cursor(inp.cursor, s.company).account_id
        elif inp.customer is not None:
            from bookflow.company.parties import resolve_party
            customer_id = resolve_party(s.company, "customer", inp.customer)["id"]
        state, offset = ledger._state(s, inp, "open-invoices", principal_id, customer_id, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, "customer": customer_id,
                  "past_due_only": int(inp.past_due_only), **bucket_edges(inp.as_of)}
        totals = dict.fromkeys(("amount", "applied", "balance"), 0)
        for gross, applied, net in raw.execute(_OPEN + "SELECT gross, applied, net FROM selected", params):
            for key, value in zip(("amount", "applied", "balance"), (gross, applied, net)):
                totals[key] += money(int(value), currency).minor_units
        page = _rows(raw.execute(_OPEN + """SELECT * FROM selected
            ORDER BY aging_date, tx LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = [OpenInvoiceRow(transaction_id=row["tx"], number=row["document_number"],
            date=row["document_date"], due_date=row["aging_date"],
            days_past_due=days_past_due(inp.as_of, row["aging_date"]),
            aging_bucket=BUCKETS[row["bucket"]],
            settlement_status="partly_paid" if int(row["applied"]) else "unpaid",
            customer_id=row["customer_id"], current_customer_label=row["full_name"],
            current_customer_name=row["name"], display_customer_label=_label(row["full_name"]),
            parent_id=row["parent_id"], amount=money(int(row["gross"]), currency),
            applied=money(int(row["applied"]), currency), balance=money(int(row["net"]), currency))
            for row in page[:inp.limit]]
        return OpenInvoicesOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=OpenInvoicesTotals(**{key: money(value, currency) for key, value in totals.items()}),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


# What a row is, in the one word a reader needs; a settlement movement is named
# for what it is rather than for the document it happens to sit on.
_ENTRY = {0: "balance_forward", 2: "balance_due"}


def statement(inp: StatementInput, s, *, principal_id=None) -> StatementOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        customer_id = None
        if inp.cursor is not None:
            # Keep the stable ID resolved for the first page: renaming the
            # selected customer must stale the continuation rather than turn it
            # into a record-not-found on page two.
            customer_id = ledger._decode_cursor(inp.cursor, s.company).account_id
        elif inp.customer is not None:
            from bookflow.company.parties import resolve_party
            customer_id = resolve_party(s.company, "customer", inp.customer)["id"]
        state, offset = ledger._state(s, inp, "statement", principal_id, customer_id, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.date_to, "date_from": inp.date_from, "customer": customer_id,
                  **bucket_edges(inp.date_to)}
        # Stream every selected customer, including those past this page, so a
        # page boundary can never hide a balance from a total.
        opening = closing = charges = credits = 0
        for row in raw.execute(_PARTIES + "SELECT opening, closing FROM party_included", params):
            opening += money(int(row[0]), currency).minor_units
            closing += money(int(row[1]), currency).minor_units
        for (net,) in raw.execute(_PARTIES + "SELECT net FROM timeline WHERE phase=1", params):
            net = money(int(net), currency).minor_units
            charges, credits = charges + max(net, 0), credits + min(net, 0)
        if opening + charges + credits != closing:
            raise BookflowError("E_INTERNAL", message="Statement activity does not carry the opening balance to the closing balance")
        aged = raw.execute(_STATEMENT_AGING, params).fetchone()
        aging = {name: money(int(value), currency).minor_units
                 for name, value in zip((*BUCKETS, "total"), aged)}
        if sum(aging[name] for name in BUCKETS) != aging["total"]:
            raise BookflowError("E_INTERNAL", message="Statement aging columns do not sum to the aging total")
        if aging["total"] != closing:
            raise BookflowError("E_INTERNAL", message="Statement aging does not equal the closing balance")
        page = _rows(raw.execute(_STATEMENT + f"""SELECT * FROM selected
            ORDER BY {_CUSTOMER_ORDER}, phase, effect_date, movement_rank, tx
            LIMIT :limit OFFSET :offset""", {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = []
        for row in page[:inp.limit]:
            phase = row["phase"]
            entry = _ENTRY.get(phase) or ("applied_credit" if row["movement_rank"] else row["document_type"])
            rows.append(StatementRow(
                kind=("opening", "activity", "closing")[phase], entry=entry,
                customer_id=row["customer_id"], current_customer_label=row["full_name"],
                current_customer_name=row["name"], display_customer_label=_label(row["full_name"]),
                parent_id=row["parent_id"],
                active=None if row["active"] is None else bool(row["active"]),
                date=row["effect_date"] or None, transaction_id=row["tx"] or None,
                number=row["document_number"], document_date=row["document_date"],
                due_date=row["due_date"], memo=row["document_memo"],
                amount=money(int(row["net"]), currency), balance=money(int(row["balance"]), currency)))
        return StatementOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=StatementTotals(**{name: money(value, currency) for name, value in
                (("opening", opening), ("charges", charges), ("credits", credits), ("closing", closing))}),
            aging=ArAgingTotals(**{name: money(aging[name], currency) for name in (*BUCKETS, "total")}),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
