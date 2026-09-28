"""Customer and vendor balances: what each party owes or is owed, and how it got there.

Four reports, read off the same effects the aging reports and the customer statement
already read, so none of them is a second arithmetic that has to be kept in step.

**A balance summary is the aging's Total column.** ``report customer-balance-summary`` is
one row per customer or job with the balance ``report ar-aging`` totals for it on the same
date, and ``report vendor-balance-summary`` is the same for vendors against ``report
ap-aging``. A party whose balance is zero is omitted, which cannot move a total, so each
summary's total is Accounts Receivable or Accounts Payable on the accrual balance sheet for
the same date.

**A balance detail is every effect that makes up that balance**, oldest first, with the
running balance after each one and a total row closing each party. The anchor's balance
detail reads all dates by default, and so does this: every effect on or before the as-of
date, for every party whose balance on that date is not zero (or for the one party named).
A party's total row is its summary row.

Receivables carry settlement between a parent and its job exactly as the customer statement
does -- new cash is owned by the party whose invoice it settles -- so the rows are the
statement's own rows read from the beginning of the books. A settlement between a customer
and itself moves nothing that customer owes and has no row. Payables settle only within one
vendor (a storage trigger requires it), so a vendor's rows are its payable posting effects
and nothing else.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company import payable_reports as payables
from bookflow.company import receivable_reports as receivables
from bookflow.company.aging import bucket_edges
from bookflow.company.ledger_reports import (
    MoneyOutKind, MoneyOutput, StrictModel, TransactionType, iso_date, money,
)
from bookflow.core.errors import BookflowError

# The beginning of the books: a balance detail reads every date, as the anchor's does.
ALL_DATES = "0001-01-01"


class _AsOf(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive accounting as-of date, YYYY-MM-DD; balances are what is owed at the end of this day.")
    basis: Literal["accrual"] = "accrual"
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        """The report period's single inclusive bound, named as every report names it."""
        return self.as_of


class CustomerBalanceSummaryInput(_AsOf):
    pass


class VendorBalanceSummaryInput(_AsOf):
    pass


class CustomerBalanceDetailInput(_AsOf):
    customer: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional customer or job ID or canonical full name; a job is its own customer and is not included with its parent. Omit for every customer or job with a balance on the as-of date.")


class VendorBalanceDetailInput(_AsOf):
    vendor: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional vendor ID or name; omit for every vendor with a balance on the as-of date.")


class BalanceTotals(StrictModel):
    balance: MoneyOutput


class CustomerBalanceRow(StrictModel):
    customer_id: str | None
    current_customer_label: str | None
    current_customer_name: str | None
    display_customer_label: str
    parent_id: str | None
    active: bool | None
    balance: MoneyOutput


class VendorBalanceRow(StrictModel):
    vendor_id: str | None
    current_vendor_name: str | None
    display_vendor_label: str
    active: bool | None
    balance: MoneyOutput


class CustomerBalanceDetailRow(StrictModel):
    """One effect on one customer's receivable, or that customer's closing total row."""
    kind: Literal["activity", "total"]
    customer_id: str | None
    current_customer_label: str | None
    current_customer_name: str | None
    display_customer_label: str
    parent_id: str | None
    active: bool | None
    date: str | None
    # A settlement between a parent and its job moves a balance without being a document
    # of either's; the row still names the invoice or receipt it sits on.
    entry: Literal["document", "applied_credit", "total"]
    transaction_id: str | None
    transaction_type: TransactionType | None
    number: str | None
    memo: str | None
    due_date: str | None
    amount: MoneyOutput
    balance: MoneyOutput


class VendorBalanceDetailRow(StrictModel):
    """One effect on one vendor's payable, or that vendor's closing total row."""
    kind: Literal["activity", "total"]
    vendor_id: str | None
    current_vendor_name: str | None
    display_vendor_label: str
    active: bool | None
    date: str | None
    transaction_id: str | None
    transaction_type: TransactionType | None
    money_out_kind: MoneyOutKind | None
    number: str | None
    memo: str | None
    due_date: str | None
    amount: MoneyOutput
    balance: MoneyOutput


class CustomerBalanceSummaryOutput(ledger.Page):
    totals: BalanceTotals
    rows: list[CustomerBalanceRow]


class VendorBalanceSummaryOutput(ledger.Page):
    totals: BalanceTotals
    rows: list[VendorBalanceRow]


class CustomerBalanceDetailOutput(ledger.Page):
    totals: BalanceTotals
    rows: list[CustomerBalanceDetailRow]


class VendorBalanceDetailOutput(ledger.Page):
    totals: BalanceTotals
    rows: list[VendorBalanceDetailRow]


# The aging's own per-party rows, kept where the balance is not zero. The aging keeps a
# party whose columns cancel (an overdue invoice beside an unapplied credit); a balance
# summary does not, because its one column is the net.
_CUSTOMER_SUMMARY = receivables._AGING + ", balances AS (SELECT * FROM selected WHERE total!='0') "
_VENDOR_SUMMARY = payables._AGING + ", balances AS (SELECT * FROM selected WHERE total!='0') "

# The customer statement's own rows over every date. `party_balance.closing` is the same
# expression the aging sums for that customer, so a customer's total row here is its
# summary row. `open_party` is who is printed: every customer with a balance, or the one
# named even when it owes nothing.
_CUSTOMER_DETAIL = receivables._PARTIES + """, open_party AS (
 SELECT party, closing FROM party_balance
 WHERE :customer IS NOT NULL OR coalesce(closing,'0')!='0'
), activity AS (
 SELECT e.party, 1 AS phase, e.effect_date, e.movement_rank, e.tx, e.net
 FROM entry e JOIN open_party o ON o.party IS e.party WHERE e.net!='0'
), running AS (
 SELECT a.*, bookflow_sum_int(net) OVER (
   PARTITION BY party ORDER BY effect_date, movement_rank, tx
   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS balance
 FROM activity a
), flat AS (
 SELECT party, phase, effect_date, movement_rank, tx, net, balance FROM running
 UNION ALL
 SELECT party, 2, '', 0, '', coalesce(closing,'0'), coalesce(closing,'0') FROM open_party
), selected AS (
 SELECT f.*, t.type AS document_type, r.number AS document_number, r.memo AS document_memo,
   p.due_date, c.id AS customer_id, c.full_name, c.name, c.full_name_key, c.parent_id, c.active
 FROM flat f
 LEFT JOIN transactions t ON t.id=f.tx
 LEFT JOIN transaction_revisions r ON r.id=t.current_revision_id
 LEFT JOIN sales_profiles p ON p.revision_id=t.current_revision_id
 LEFT JOIN customers c ON c.id=f.party
) """

# A vendor's payable effects, one row per document per date. Settlement never crosses
# vendors, so applying a payment to a bill moves nothing a vendor owes and needs no row.
_VENDOR_DETAIL = payables._EFFECTS + """, entry AS (
 SELECT tx, party, effect_date, bookflow_sum_int(amount) AS net FROM ap
 GROUP BY tx, party, effect_date
), party_balance AS (
 SELECT party, bookflow_sum_int(net) AS closing FROM entry GROUP BY party
), open_party AS (
 SELECT party, closing FROM party_balance WHERE :vendor IS NULL AND closing!='0'
 UNION ALL
 SELECT :vendor, coalesce((SELECT closing FROM party_balance WHERE party=:vendor),'0')
 WHERE :vendor IS NOT NULL
), activity AS (
 SELECT e.party, 1 AS phase, e.effect_date, e.tx, e.net
 FROM entry e JOIN open_party o ON o.party IS e.party WHERE e.net!='0'
), running AS (
 SELECT a.*, bookflow_sum_int(net) OVER (
   PARTITION BY party ORDER BY effect_date, tx
   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS balance
 FROM activity a
), flat AS (
 SELECT party, phase, effect_date, tx, net, balance FROM running
 UNION ALL
 SELECT party, 2, '', '', closing, closing FROM open_party
), selected AS (
 SELECT f.*, t.type AS document_type, r.number AS document_number, r.memo AS document_memo,
   m.kind AS money_out_kind, p.due_date, v.id AS vendor_id, v.name, v.name_key, v.active
 FROM flat f
 LEFT JOIN transactions t ON t.id=f.tx
 LEFT JOIN transaction_revisions r ON r.id=t.current_revision_id
 LEFT JOIN money_out_documents m ON m.transaction_id=t.id AND m.type=t.type
 LEFT JOIN purchase_profiles p ON p.revision_id=t.current_revision_id
 LEFT JOIN vendors v ON v.id=f.party
) """


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _active(value):
    return None if value is None else bool(value)


def _selected_party(inp, s, noun):
    """The one party a detail report is filtered to, resolved once on the first page."""
    if inp.cursor is not None:
        # Keep the stable ID resolved for the first page: renaming the selected party
        # must stale the continuation rather than turn page two into a record-not-found.
        return ledger._decode_cursor(inp.cursor, s.company).account_id
    selector = getattr(inp, noun)
    if selector is None:
        return None
    from bookflow.company.parties import resolve_party
    return resolve_party(s.company, noun, selector)["id"]


def customer_balance_summary(inp: CustomerBalanceSummaryInput, s, *, principal_id=None) -> CustomerBalanceSummaryOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "customer-balance-summary", principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, **bucket_edges(inp.as_of)}
        total = sum(money(int(value), currency).minor_units
                    for value, in raw.execute(_CUSTOMER_SUMMARY + "SELECT total FROM balances", params))
        page = _rows(raw.execute(_CUSTOMER_SUMMARY + f"""SELECT * FROM balances
            ORDER BY {receivables._CUSTOMER_ORDER} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = [CustomerBalanceRow(customer_id=row["customer_id"], current_customer_label=row["full_name"],
            current_customer_name=row["name"], display_customer_label=receivables._label(row["full_name"]),
            parent_id=row["parent_id"], active=_active(row["active"]),
            balance=money(int(row["total"]), currency)) for row in page[:inp.limit]]
        return CustomerBalanceSummaryOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=BalanceTotals(balance=money(total, currency)),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def vendor_balance_summary(inp: VendorBalanceSummaryInput, s, *, principal_id=None) -> VendorBalanceSummaryOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "vendor-balance-summary", principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, **bucket_edges(inp.as_of)}
        total = sum(money(int(value), currency).minor_units
                    for value, in raw.execute(_VENDOR_SUMMARY + "SELECT total FROM balances", params))
        page = _rows(raw.execute(_VENDOR_SUMMARY + f"""SELECT * FROM balances
            ORDER BY {payables._VENDOR_ORDER} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = [VendorBalanceRow(vendor_id=row["vendor_id"], current_vendor_name=row["name"],
            display_vendor_label=payables._label(row["name"]), active=_active(row["active"]),
            balance=money(int(row["total"]), currency)) for row in page[:inp.limit]]
        return VendorBalanceSummaryOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=BalanceTotals(balance=money(total, currency)),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def _check_running(raw, query, params, currency, what):
    """Every party's effects carry it from nothing to its closing balance, or nothing is printed."""
    closing, moved = 0, 0
    for phase, net in raw.execute(query + "SELECT phase, net FROM flat", params):
        value = money(int(net), currency).minor_units
        if phase == 2:
            closing += value
        else:
            moved += value
    if closing != moved:
        raise BookflowError("E_INTERNAL", message=f"{what} activity does not add up to the closing balances")
    return closing


def customer_balance_detail(inp: CustomerBalanceDetailInput, s, *, principal_id=None) -> CustomerBalanceDetailOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        customer_id = _selected_party(inp, s, "customer")
        state, offset = ledger._state(s, inp, "customer-balance-detail", principal_id, customer_id,
                                      account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, "date_from": ALL_DATES, "customer": customer_id,
                  **bucket_edges(inp.as_of)}
        total = _check_running(raw, _CUSTOMER_DETAIL, params, currency, "Customer balance detail")
        page = _rows(raw.execute(_CUSTOMER_DETAIL + f"""SELECT * FROM selected
            ORDER BY {receivables._CUSTOMER_ORDER}, phase, effect_date, movement_rank, tx
            LIMIT :limit OFFSET :offset""", {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = []
        for row in page[:inp.limit]:
            closing = row["phase"] == 2
            rows.append(CustomerBalanceDetailRow(
                kind="total" if closing else "activity",
                customer_id=row["customer_id"], current_customer_label=row["full_name"],
                current_customer_name=row["name"], display_customer_label=receivables._label(row["full_name"]),
                parent_id=row["parent_id"], active=_active(row["active"]),
                date=row["effect_date"] or None,
                entry="total" if closing else ("applied_credit" if row["movement_rank"] else "document"),
                transaction_id=row["tx"] or None, transaction_type=row["document_type"],
                number=row["document_number"], memo=row["document_memo"], due_date=row["due_date"],
                amount=money(int(row["net"]), currency), balance=money(int(row["balance"]), currency)))
        return CustomerBalanceDetailOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=BalanceTotals(balance=money(total, currency)),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def vendor_balance_detail(inp: VendorBalanceDetailInput, s, *, principal_id=None) -> VendorBalanceDetailOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        vendor_id = _selected_party(inp, s, "vendor")
        state, offset = ledger._state(s, inp, "vendor-balance-detail", principal_id, vendor_id,
                                      account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, "vendor": vendor_id}
        total = _check_running(raw, _VENDOR_DETAIL, params, currency, "Vendor balance detail")
        page = _rows(raw.execute(_VENDOR_DETAIL + f"""SELECT * FROM selected
            ORDER BY {payables._VENDOR_ORDER}, phase, effect_date, tx
            LIMIT :limit OFFSET :offset""", {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = []
        for row in page[:inp.limit]:
            closing = row["phase"] == 2
            rows.append(VendorBalanceDetailRow(
                kind="total" if closing else "activity",
                vendor_id=row["vendor_id"], current_vendor_name=row["name"],
                display_vendor_label=payables._label(row["name"]), active=_active(row["active"]),
                date=row["effect_date"] or None, transaction_id=row["tx"] or None,
                transaction_type=row["document_type"], money_out_kind=row["money_out_kind"],
                number=row["document_number"], memo=row["document_memo"],
                due_date=row["due_date"] if row["document_type"] == "bill" else None,
                amount=money(int(row["net"]), currency), balance=money(int(row["balance"]), currency)))
        return VendorBalanceDetailOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=BalanceTotals(balance=money(total, currency)),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
