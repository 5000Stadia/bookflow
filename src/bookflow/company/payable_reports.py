"""Accrual payables reports over the same immutable effects the ledger reads.

These are the receivables reports read from the other side of the books. Both
start from Accounts Payable posting effects dated on or before the as-of date,
signed so that what is owed to a vendor is positive -- credit minus debit,
because a payable is credit-normal -- and then move each active settlement
application from the source that supplied it -- a bill payment or a vendor
credit, read the same way -- onto the bill it settles.
That movement transfers an amount between two rows and never creates or
destroys one, so the aging total is exactly the Accounts Payable balance the
balance sheet reports for the same date, whatever the settlement history looks
like. A bill credits Accounts Payable, a correction reverses the old credit and
posts a new one, a void reverses at the original date, and a payment debits it
and is attributed to the bill it names, so a document's net is what is still
owed on it whatever its history looks like.

Aging is by due date for a bill and by accounting date for everything else that
reaches Accounts Payable, which is what gives a payable journal entry and an
unapplied bill payment a column of their own. Rows whose columns are all zero --
a paid bill, a voided one, a fully applied payment -- are omitted, which cannot
move a total.

**A paid bill is not on a report called unpaid bills.** ``report unpaid-bills``
lists a bill at what is still open on it, so a bill settled to nothing leaves
the report exactly as a paid invoice leaves ``report open-invoices``: because it
is worth zero, not because a status was filtered. That is also what keeps the
two payables reports agreeing about what is outstanding -- the aging drops the
same zero and neither total moves. ``settlement_status`` therefore has only the
two answers a listed bill can truthfully give, ``unpaid`` and ``partly_paid``.

The settlement edge is ``ap_applications`` and the vendor it belongs to is
``ap_obligation_keys.vendor_id``, which ``bill_validation`` requires to equal the
vendor of every revision of the bill and which a storage trigger requires to
equal the paying source's vendor. So both halves of the transfer land on the
same party as the posting lines they move between, and a settlement can never
shift a balance from one vendor to another the way a parent and a job can on the
receivables side.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.aging import (
    BUCKET_SQL, BUCKETS, COLUMNS, bucket_edges, days_past_due,
)
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.core.errors import BookflowError


class AsOfInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive accounting as-of date, YYYY-MM-DD; payables are aged against it.")
    basis: Literal["accrual"] = "accrual"
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        """The report period's single inclusive bound, named as every report names it."""
        return self.as_of


class ApAgingInput(AsOfInput):
    pass


class UnpaidBillsInput(AsOfInput):
    vendor: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional vendor ID or name; omit for every vendor with an open bill.")
    past_due_only: bool = Field(default=False, description="Omit bills that are not yet due on the as-of date.")


class ApAgingTotals(StrictModel):
    current: MoneyOutput
    days_1_30: MoneyOutput
    days_31_60: MoneyOutput
    days_61_90: MoneyOutput
    over_90: MoneyOutput
    total: MoneyOutput


class ApAgingRow(StrictModel):
    vendor_id: str | None
    current_vendor_name: str | None
    display_vendor_label: str
    active: bool | None
    current: MoneyOutput
    days_1_30: MoneyOutput
    days_31_60: MoneyOutput
    days_61_90: MoneyOutput
    over_90: MoneyOutput
    total: MoneyOutput


class UnpaidBillsTotals(StrictModel):
    amount: MoneyOutput
    applied: MoneyOutput
    balance: MoneyOutput


class UnpaidBillRow(StrictModel):
    transaction_id: str
    number: str
    date: str
    due_date: str
    days_past_due: int
    aging_bucket: Literal["current", "days_1_30", "days_31_60", "days_61_90", "over_90"]
    settlement_status: Literal["unpaid", "partly_paid"]
    vendor_id: str | None
    current_vendor_name: str | None
    display_vendor_label: str
    supplier_reference: str | None
    amount: MoneyOutput
    applied: MoneyOutput
    balance: MoneyOutput


class ApAgingOutput(ledger.Page):
    totals: ApAgingTotals
    rows: list[ApAgingRow]


class UnpaidBillsOutput(ledger.Page):
    totals: UnpaidBillsTotals
    rows: list[UnpaidBillRow]


NO_VENDOR = "No name"

# Never let SQLite do arithmetic on an aggregate: bookflow_sum_int returns
# lossless text and SQLite would coerce text arithmetic to REAL. Only the
# stored one-sided posting line and the stored application amount are
# subtracted or negated here, and both are INTEGER columns. Second-level
# aggregation over the text is exact because bookflow_sum_int itself accepts
# lossless integer text.
#
# The sign is the payable's own: credit minus debit, so a bill is positive and
# a debit to Accounts Payable that no bill owns -- an unapplied vendor credit,
# or a bill payment with nothing applied to it -- is negative, exactly as
# unapplied customer credit is negative on the receivables side.
#
# `settled` joins the obligation side only. The paying document is taken
# straight from `ap_applications.source_transaction_id`, so a second kind of
# paying source moves through here with no branch and no arm to forget: joining
# a source table to learn the kind would be an inner join that silently dropped
# every row of a kind the join did not cover.
#
# `settled` is the same edge `ap_settlement.applied_totals` nets, read along the
# date axis: an apply on or before the as-of date that no unapply on or before
# it has taken back. An unapply is a whole-edge inverse carrying the same amount
# as the apply it reverses, enforced by `ap_applications_exact_inverse`, so
# excluding the reversed apply outright is the whole of the netting and there is
# no partial inverse to half-count.
#
# That same trigger also requires an unapply to carry its apply's own
# `effective_date`, so an unapply can never fall on a later date than what it
# takes back and the `u.effective_date<=:as_of` clause cannot decide anything
# today. It is written anyway, and `receivable_reports` writes it for the same
# reason: the day an inverse is allowed its own date, a report that reads every
# unapply regardless of date would silently restore a balance before the unapply
# happened, and that is a defect nothing else here would catch.
_EFFECTS = """
WITH ap AS (
 SELECT l.transaction_id AS tx, l.name_id AS party,
        l.credit_minor_units-l.debit_minor_units AS amount, b.effective_date AS effect_date
 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
 JOIN accounts a ON a.id=l.account_id
 WHERE a.type='accounts_payable' AND b.effective_date<=:as_of
), settled AS (
 SELECT s.obligation_transaction_id AS bill, s.source_transaction_id AS payment,
        k.vendor_id AS party, s.amount_minor_units AS amount
 FROM ap_applications s
 JOIN ap_obligation_keys k ON k.transaction_id=s.obligation_transaction_id
                          AND k.id=s.obligation_key_id
 WHERE s.kind='apply' AND s.effective_date<=:as_of
   AND NOT EXISTS (SELECT 1 FROM ap_applications u
                   WHERE u.reverses_application_id=s.id AND u.effective_date<=:as_of)
), signed AS (
 SELECT tx, party, amount FROM ap
 UNION ALL SELECT bill, party, -amount FROM settled
 UNION ALL SELECT payment, party, amount FROM settled
), document AS (
 SELECT tx, party, bookflow_sum_int(amount) AS net FROM signed GROUP BY tx, party
), dated AS (
 SELECT d.tx, d.party, d.net, t.type AS document_type, r.number AS document_number,
        r.date AS document_date, p.supplier_reference AS supplier_reference,
        CASE WHEN t.type='bill' THEN p.due_date ELSE r.date END AS aging_date
 FROM document d JOIN transactions t ON t.id=d.tx
 JOIN transaction_revisions r ON r.id=t.current_revision_id
 LEFT JOIN purchase_profiles p ON p.revision_id=t.current_revision_id
)
"""

_AGING = _EFFECTS + f""", vendor_columns AS (
 SELECT party,
   {', '.join(f"bookflow_sum_int(CASE WHEN {BUCKET_SQL}={index} THEN net ELSE '0' END) AS bucket_{index}" for index in range(len(BUCKETS)))},
   bookflow_sum_int(net) AS total
 FROM dated GROUP BY party
), selected AS (
 SELECT c.*, v.id AS vendor_id, v.name, v.name_key, v.active
 FROM vendor_columns c LEFT JOIN vendors v ON v.id=c.party
 WHERE {' OR '.join(f"c.{name}!='0'" for name in COLUMNS)}
) """

# Bills only, so this report's total is payables before any vendor credit, the
# way open invoices is receivables before unapplied customer credit.
#
# Three separate sums, never one arithmetic expression: `gross` is the bill's
# own payable posting before any settlement, `applied` is what the active
# applications took off it, and `net` -- already carrying both, because `dated`
# reads the transferred `signed` rows -- is what is left. A bill can never also
# be a paying source, so for every row here `net` is `gross` less `applied`
# exactly; `unpaid_bills` re-checks that in Python on every row rather than
# trusting two expressions to stay equal.
#
# `d.net!='0'` is what keeps a paid bill off a report called unpaid bills: it
# leaves because it is worth nothing, the same way a voided bill does and the
# same way a paid invoice leaves open invoices.
_UNPAID = _EFFECTS + f""", ledger_gross AS (
 SELECT tx, party, bookflow_sum_int(amount) AS gross FROM ap GROUP BY tx, party
), bill_applied AS (
 SELECT bill, party, bookflow_sum_int(amount) AS applied FROM settled GROUP BY bill, party
), selected AS (
 SELECT d.tx, d.party, d.net, d.document_number, d.document_date, d.aging_date,
   d.supplier_reference, coalesce(g.gross,'0') AS gross, coalesce(a.applied,'0') AS applied,
   {BUCKET_SQL} AS bucket,
   v.id AS vendor_id, v.name, v.name_key
 FROM dated d
 LEFT JOIN ledger_gross g ON g.tx=d.tx AND g.party IS d.party
 LEFT JOIN bill_applied a ON a.bill=d.tx AND a.party IS d.party
 LEFT JOIN vendors v ON v.id=d.party
 WHERE d.document_type='bill' AND d.net!='0'
   AND (:vendor IS NULL OR d.party=:vendor)
   AND (:past_due_only=0 OR {BUCKET_SQL}>0)
) """

# Vendors are a flat list, so the name is the order; parties with no vendor
# record follow them all, by stable id.
_VENDOR_ORDER = "name_key IS NULL, name_key, coalesce(party,'')"


def _label(name):
    return NO_VENDOR if name is None else str(name)


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def ap_aging(inp: ApAgingInput, s, *, principal_id=None) -> ApAgingOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "ap-aging", principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, **bucket_edges(inp.as_of)}
        totals = dict.fromkeys(COLUMNS, 0)
        # Stream every vendor's column set, including rows past this page, so a
        # page boundary can never hide an out-of-range amount from a total.
        for row in raw.execute(_AGING + f"SELECT {', '.join(COLUMNS)} FROM selected", params):
            for key, value in zip(COLUMNS, row):
                totals[key] += money(int(value), currency).minor_units
        if sum(totals[name] for name in COLUMNS[:-1]) != totals["total"]:
            raise BookflowError("E_INTERNAL", message="Aging columns do not sum to the aging total")
        page = _rows(raw.execute(_AGING + f"""SELECT * FROM selected
            ORDER BY {_VENDOR_ORDER} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = [ApAgingRow(vendor_id=row["vendor_id"], current_vendor_name=row["name"],
            display_vendor_label=_label(row["name"]),
            active=None if row["active"] is None else bool(row["active"]),
            **{name: money(int(row[column]), currency) for name, column in zip((*BUCKETS, "total"), COLUMNS)})
            for row in page[:inp.limit]]
        return ApAgingOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=ApAgingTotals(**{name: money(totals[column], currency)
                                    for name, column in zip((*BUCKETS, "total"), COLUMNS)}),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def unpaid_bills(inp: UnpaidBillsInput, s, *, principal_id=None) -> UnpaidBillsOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        vendor_id = None
        if inp.cursor is not None:
            # Keep the stable ID resolved for the first page: renaming the
            # selected vendor must stale the continuation rather than turn it
            # into a record-not-found on page two.
            vendor_id = ledger._decode_cursor(inp.cursor, s.company).account_id
        elif inp.vendor is not None:
            from bookflow.company.parties import resolve_party
            vendor_id = resolve_party(s.company, "vendor", inp.vendor)["id"]
        state, offset = ledger._state(s, inp, "unpaid-bills", principal_id, vendor_id, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, "vendor": vendor_id,
                  "past_due_only": int(inp.past_due_only), **bucket_edges(inp.as_of)}
        totals = dict.fromkeys(("amount", "applied", "balance"), 0)
        # Stream every bill, including rows past this page, so a page boundary
        # can never hide an amount from a total. The three columns are three
        # independent sums, so check the arithmetic that relates them on every
        # row, in Python, where an integer is exact and unbounded: if this SQL
        # and `ap_settlement.applied_totals` could ever disagree about what a
        # bill has been paid, one of them is wrong and the report says so rather
        # than printing a balance nothing supports.
        for gross, applied, net in raw.execute(_UNPAID + "SELECT gross, applied, net FROM selected", params):
            gross, applied, net = (money(int(value), currency).minor_units
                                   for value in (gross, applied, net))
            if gross - applied != net:
                raise BookflowError("E_INTERNAL", message="An open bill balance is not its amount less what was applied to it")
            for key, value in zip(("amount", "applied", "balance"), (gross, applied, net)):
                totals[key] += value
        page = _rows(raw.execute(_UNPAID + """SELECT * FROM selected
            ORDER BY aging_date, tx LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = [UnpaidBillRow(transaction_id=row["tx"], number=row["document_number"],
            date=row["document_date"], due_date=row["aging_date"],
            days_past_due=days_past_due(inp.as_of, row["aging_date"]),
            aging_bucket=BUCKETS[row["bucket"]],
            # Two answers, because a bill that is neither is not on this report:
            # a settled-to-nothing bill left with the voided ones above.
            settlement_status="partly_paid" if int(row["applied"]) else "unpaid",
            vendor_id=row["vendor_id"], current_vendor_name=row["name"],
            display_vendor_label=_label(row["name"]),
            supplier_reference=row["supplier_reference"],
            amount=money(int(row["gross"]), currency),
            applied=money(int(row["applied"]), currency), balance=money(int(row["net"]), currency))
            for row in page[:inp.limit]]
        return UnpaidBillsOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=UnpaidBillsTotals(**{key: money(value, currency) for key, value in totals.items()}),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
