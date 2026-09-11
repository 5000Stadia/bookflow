"""Accrual reports over all immutable effects; signed values are debit minus credit.

Trial balance omits zero ending balances unless include_zero=True (including
inactive and never-posted accounts). GL paginates opening/posting/closing rows;
summary rows consume the same limit as details. Totals cover the whole filter.
No current document state or posting-source allocation participates in sums.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import date
import hashlib
import hmac
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from bookflow.core.errors import BookflowError
from bookflow.core.money import Money
from bookflow.core.session import now_iso
from bookflow.company.query import permission_fingerprint

I64_MIN, I64_MAX = -(2**63), 2**63 - 1
# Bumped when the shape or the row order of a report page changes, so a
# continuation minted by an earlier version restarts instead of paging into a
# different order.  "2": rows read in account-number order and trial-balance
# rows carry the account number.
REPORT_VERSION = "2"


def account_order(prefix: str = "") -> str:
    """Presentation order for account rows: account number, then name.

    Accountants read a chart by number, so every report row set is ordered that
    way before anything else about the account.  Numbers are one to seven ASCII
    digits (`ck_accounts_number`), so CAST puts 999 before 1010 where the stored
    text would not.  Unnumbered accounts follow the numbered ones in name order,
    and the stable id breaks the remaining ties so OFFSET paging stays
    deterministic.  Ordering only: no total, subtotal, grouping or section
    boundary is computed from it.
    """
    return (f"{prefix}number IS NULL, CAST({prefix}number AS INTEGER), "
            f"{prefix}full_name_key, {prefix}id")


def _account_display(full_name, name, number, use_numbers, lowest_only) -> str:
    """The one rule for showing an account on a report row.

    The company decides whether numbers appear at all and whether a subaccount
    is shown by its leaf name; `current_account_number` and the other current
    fields on the row carry the unabbreviated facts either way.
    """
    label = str(name) if lowest_only else str(full_name)
    return f"{number} · {label}" if use_numbers and number else label


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def iso_date(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("expected YYYY-MM-DD")
    date.fromisoformat(value)
    return value


class TrialBalanceInput(StrictModel):
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive accounting as-of date, YYYY-MM-DD.")
    basis: Literal["accrual"] = "accrual"
    include_zero: bool = Field(default=False, description="Include zero ending balances, including inactive and never-posted accounts.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _date_to = field_validator("date_to")(iso_date)


class GeneralLedgerInput(StrictModel):
    date_from: str = Field(min_length=10, max_length=10, description="Inclusive first accounting date, YYYY-MM-DD.")
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last accounting date, YYYY-MM-DD.")
    basis: Literal["accrual"] = "accrual"
    account: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional account ID or canonical full name; includes inactive accounts.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _dates = field_validator("date_from", "date_to")(iso_date)

    @model_validator(mode="after")
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class MoneyOutput(StrictModel):
    amount: str
    currency: str
    minor_units: int = Field(ge=I64_MIN, le=I64_MAX)


def money(value: int, currency: str) -> MoneyOutput:
    if type(value) is not int or not I64_MIN <= value <= I64_MAX:
        raise BookflowError("E_VALUE_RANGE", details={"field": "report amount", "minimum": I64_MIN, "maximum": I64_MAX})
    return MoneyOutput(**Money(value, currency).to_dict())


class ReportPeriod(StrictModel):
    date_from: str | None
    date_to: str


class ReportMetadata(StrictModel):
    company_id: str
    period: ReportPeriod
    basis: Literal["accrual"] = "accrual"
    report_version: str
    schema_revision: str
    generation_time: str
    audit_watermark: int = Field(ge=0)
    currency: str


class TrialBalanceRow(StrictModel):
    account_id: str
    current_account_label: str
    current_account_name: str
    current_account_number: str | None
    display_account_label: str
    active: bool
    signed_net: MoneyOutput
    debit: MoneyOutput
    credit: MoneyOutput


class TrialBalanceTotals(StrictModel):
    debit: MoneyOutput
    credit: MoneyOutput
    signed_net: MoneyOutput


class GeneralLedgerTotals(StrictModel):
    opening: MoneyOutput
    period_debits: MoneyOutput
    period_credits: MoneyOutput
    closing: MoneyOutput


class GeneralLedgerRow(StrictModel):
    kind: Literal["opening", "posting", "closing"]
    account_id: str
    current_account_label: str
    current_account_name: str
    current_account_number: str | None
    display_account_label: str
    signed_balance: MoneyOutput
    debit: MoneyOutput
    credit: MoneyOutput
    effective_date: str | None = None
    posting_line_id: str | None = None
    line_no: int | None = None
    batch_id: str | None = None
    batch_kind: Literal["original", "reversal", "replacement"] | None = None
    transaction_id: str | None = None
    transaction_type: Literal["journal_entry", "invoice", "sales_receipt", "payment", "deposit", "bill", "bill_payment", "credit_memo", "sales_tax_payment", "customer_refund", "vendor_credit"] | None = None
    transaction_number: str | None = None
    revision_id: str | None = None
    reverses_batch_id: str | None = None
    replaces_batch_id: str | None = None
    recorded_at: str | None = None
    account_snapshot: dict | None = None
    party_name: str | None = None
    class_name: str | None = None
    description: str | None = None


class Page(StrictModel):
    metadata: ReportMetadata
    count: int = Field(ge=0, le=200, description="Rows on this page only; summary rows also consume the limit.")
    next_cursor: str | None

    @model_validator(mode="after")
    def count_matches(self):
        if self.count != len(self.rows):
            raise ValueError("count must match page rows")
        return self


class TrialBalanceOutput(Page):
    totals: TrialBalanceTotals
    rows: list[TrialBalanceRow]


class GeneralLedgerOutput(Page):
    totals: GeneralLedgerTotals
    rows: list[GeneralLedgerRow]


class IntegerSum:
    """SQLite aggregate/window with arbitrary precision and lossless text results."""
    def __init__(self):
        self.total = 0

    @staticmethod
    def integer(value):
        if value is None:
            return 0
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
            return int(value)
        raise ValueError("bookflow_sum_int accepts only integers or lossless integer text")

    def step(self, value):
        self.total += self.integer(value)

    def inverse(self, value):
        self.total -= self.integer(value)

    def value(self):
        return str(self.total)

    def finalize(self):
        return self.value()


def register_ledger_functions(db) -> None:
    """Install on this Database's raw connection (also accepts sqlite3.Connection)."""
    raw = getattr(db, "raw", db)
    raw.create_window_function("bookflow_sum_int", 1, IntegerSum)


def net_balances(db, as_of: str | None = None) -> dict[str, int]:
    """Own-account net, no descendants or normal-side conversion; no i64 clamp.

    The caller owns the snapshot. Accounts without effects are absent.
    """
    if as_of is not None:
        iso_date(as_of)
    register_ledger_functions(db)
    raw = getattr(db, "raw", db)
    return {row[0]: int(row[1]) for row in raw.execute("""
        SELECT l.account_id, bookflow_sum_int(l.debit_minor_units-l.credit_minor_units)
        FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
        WHERE (:as_of IS NULL OR b.effective_date<=:as_of) GROUP BY l.account_id
    """, {"as_of": as_of})}


@contextmanager
def _snapshot(db):
    # Read-only Database handles already pin a transaction. Direct callers and
    # pooled writable handles get a transaction owned only for this report.
    owned = not db.raw.in_transaction
    if owned:
        db.raw.execute("BEGIN")
    try:
        yield
    finally:
        if owned:
            db.raw.rollback()


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ReportCursor(StrictModel):
    version: Literal[1] = 1
    company: str
    account_id: str | None = None
    query: str
    permissions: str
    watermark: str
    offset: int = Field(ge=1, le=I64_MAX)
    metadata: ReportMetadata


def _invalid_cursor():
    return BookflowError("E_VALIDATION", details={"fields": [{"field": "cursor", "problem": "invalid or mismatched continuation; restart without cursor"}]})


def _cursor_key(db):
    row = db.raw.execute("SELECT key_material FROM report_cursor_keys WHERE key_id=1").fetchone()
    if row is None or not isinstance(row[0], bytes) or len(row[0]) != 32:
        raise BookflowError("E_INTERNAL", message="Report continuation key is unavailable")
    return row[0]


def _cursor_mac(db, payload):
    return hmac.digest(_cursor_key(db), b"bookflow.report.cursor.v1\x00" + payload, "sha256")


def _decode_cursor(encoded_cursor, db):
    try:
        if not 1 <= len(encoded_cursor) <= 4096:
            raise ValueError
        payload_text, signature_text = encoded_cursor.encode("ascii").split(b".")
        decode = lambda value: base64.b64decode(value + b"=" * (-len(value) % 4), altchars=b"-_", validate=True)
        payload, signature = decode(payload_text), decode(signature_text)
        if not hmac.compare_digest(signature, _cursor_mac(db, payload)):
            raise ValueError
        return ReportCursor.model_validate_json(payload)
    except (ValueError, UnicodeError, ValidationError):
        raise _invalid_cursor() from None


def _state(s, inp, report, principal_id, account_id, *, account_scoped=True):
    """Continuation state; account_id is the stable ID this cursor carries.

    A report that pages one account narrows its own effect and label scans to
    it. A report that carries a different stable ID in the same cursor slot --
    the customer a receivables report was filtered to -- passes
    account_scoped=False, so the ID identifies the continuation without
    pretending to be a posting account.
    """
    raw = s.company.raw
    scope = account_id if account_scoped else None
    # Immutable rows can only append. Count plus maximal identities detect even
    # backdated additions whose accounting date precedes the previous page.
    effect = raw.execute("""SELECT count(*), max(l.id), max(b.id)
        FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
        WHERE b.effective_date<=:date_to AND (:account IS NULL OR l.account_id=:account)
        """, {"date_to": inp.date_to, "account": scope}).fetchone()
    labels = hashlib.sha256()
    # `financial` is the statements whose rows are accounts and whose row labels
    # come from the company's account-display preferences: the profit and loss,
    # the balance sheet, the statement of cash flows and the income tax summary.
    # The customer statement is a receivables report and takes the receivable
    # branch, like its two neighbours.
    financial = report in {"profit-and-loss", "balance-sheet", "cash-flows", "income-tax-summary"}
    receivable = report in {"ar-aging", "open-invoices", "statement"}
    # The sales tax liability is a payables report whose rows are agencies, which are
    # vendors, so it labels and orders its rows exactly as the other two do.
    payable = report in {"ap-aging", "unpaid-bills", "sales-tax-liability"}
    if financial:
        label_query = "SELECT id, full_name, full_name_key, name, number, type, parent_id, active FROM accounts ORDER BY id"
    elif receivable:
        # Receivables rows are customers, not accounts, and the hierarchy name
        # is both the row label and the row order.
        label_query = "SELECT id, full_name, full_name_key, name, parent_id, active FROM customers ORDER BY id"
    elif payable:
        # Payables rows are vendors, which are a flat list, so the one name is
        # both the row label and the row order.
        label_query = "SELECT id, name, name_key, active FROM vendors ORDER BY id"
    elif report == "trial-balance":
        label_query = _EFFECTS + """SELECT a.id, a.full_name, a.name, a.number, a.active FROM accounts a
            LEFT JOIN balances b ON b.account_id=a.id
            WHERE :include_zero OR coalesce(b.closing,'0')!='0' ORDER BY a.id"""
    else:
        label_query = _EFFECTS + """SELECT a.id, a.full_name, a.number FROM accounts a
            JOIN balances b ON b.account_id=a.id
            WHERE b.opening!='0' OR b.activity>0 ORDER BY a.id"""
    for row in raw.execute(label_query, {"date_to": inp.date_to,
            "date_from": getattr(inp, "date_from", "0001-01-01"), "account": scope,
            "include_zero": getattr(inp, "include_zero", False)}):
        labels.update(json.dumps(tuple(row), separators=(",", ":")).encode())
    currency = raw.execute("SELECT home_currency FROM company_info").fetchone()[0]
    revision = raw.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    extra_state = None
    # Trial balance, general ledger and the statements label rows through the company's
    # account-number and lowest-subaccount preferences, so a change to either
    # one has to stale a continuation the same way a renamed account does.
    if financial or report in {"trial-balance", "general-ledger"}:
        extra_state = [tuple(raw.execute("""SELECT fiscal_year_start_month,
            use_account_numbers, show_lowest_subaccount_only FROM company_info""").fetchone())]
        if financial:
            extra_state.append(raw.execute("SELECT coalesce(max(seq),0) FROM audit_events").fetchone()[0])
    elif receivable:
        # A receivables row moves with settlement history, which posts nothing,
        # so the posting effect alone cannot see an apply or an unapply.
        extra_state = [tuple(raw.execute(
            "SELECT count(*), max(id) FROM applications WHERE effective_date<=:date_to",
            {"date_to": inp.date_to}).fetchone()),
            raw.execute("SELECT coalesce(max(seq),0) FROM audit_events").fetchone()[0]]
    elif payable:
        # A payable row moves with settlement history, which posts nothing,
        # so the posting effect alone cannot see an apply or an unapply. The
        # audit sequence covers both: every application is written under an
        # audit event, so any settlement stales a continuation minted before it
        # instead of letting it page into a different set of rows.
        extra_state = [raw.execute("SELECT coalesce(max(seq),0) FROM audit_events").fetchone()[0]]
    watermark = _hash([tuple(effect), labels.hexdigest(), currency, revision, extra_state]) if extra_state is not None else _hash([tuple(effect), labels.hexdigest(), currency, revision])
    permissions = _hash([permission_fingerprint(s, principal_id), s.memberships])
    query = _hash([report, inp.model_dump(exclude={"cursor"})])
    company = str(s.company_row["id"])
    if inp.cursor is not None:
        previous = _decode_cursor(inp.cursor, s.company)
        if (previous.company, previous.query, previous.permissions) != (company, query, permissions):
            raise _invalid_cursor()
        if previous.watermark != watermark or previous.metadata.report_version != REPORT_VERSION:
            raise BookflowError("E_QUERY_STALE", details={"restart": "Repeat the report without cursor; discard previous pages."})
        if (previous.metadata.company_id != company or previous.metadata.currency != currency
                or previous.metadata.schema_revision != revision
                or previous.metadata.period.model_dump() != {"date_from": getattr(inp, "date_from", None), "date_to": inp.date_to}):
            raise _invalid_cursor()
        return previous, previous.offset
    audit = raw.execute("SELECT coalesce(max(seq),0) FROM audit_events").fetchone()[0]
    metadata = ReportMetadata(company_id=company, period=ReportPeriod(date_from=getattr(inp, "date_from", None), date_to=inp.date_to),
        report_version=REPORT_VERSION, schema_revision=revision, generation_time=now_iso(), audit_watermark=audit, currency=currency)
    return ReportCursor(company=company, account_id=account_id, query=query, permissions=permissions, watermark=watermark, offset=1, metadata=metadata), 0


def _continuation(state, offset, count, more, db):
    if not more:
        return None
    payload = state.model_copy(update={"offset": offset + count}).model_dump_json().encode()
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return encode(payload) + "." + encode(_cursor_mac(db, payload))


# Never use SQL arithmetic on aggregate decimal text: SQLite would coerce it to
# REAL. Only each one-sided stored line's subtraction occurs in SQLite.
_EFFECTS = """
WITH effects AS (
 SELECT l.*, b.effective_date, b.kind AS batch_kind, b.revision_id,
        b.reverses_batch_id, b.replaces_batch_id, b.created_at AS recorded_at
 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
 WHERE b.effective_date<=:date_to AND (:account IS NULL OR l.account_id=:account)
), balances AS (
 SELECT account_id,
   bookflow_sum_int(debit_minor_units-credit_minor_units) AS closing,
   bookflow_sum_int(CASE WHEN effective_date<:date_from THEN debit_minor_units-credit_minor_units ELSE 0 END) AS opening,
   bookflow_sum_int(CASE WHEN effective_date>=:date_from THEN debit_minor_units ELSE 0 END) AS debits,
   bookflow_sum_int(CASE WHEN effective_date>=:date_from THEN credit_minor_units ELSE 0 END) AS credits,
   count(CASE WHEN effective_date>=:date_from THEN 1 END) AS activity
 FROM effects GROUP BY account_id
)
"""


def trial_balance(inp: TrialBalanceInput, s, *, principal_id=None) -> TrialBalanceOutput:
    with _snapshot(s.company):
        register_ledger_functions(s.company)
        state, offset = _state(s, inp, "trial-balance", principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"date_to": inp.date_to, "date_from": "0001-01-01", "account": None, "zero": inp.include_zero}
        numbers, lowest = raw.execute(
            "SELECT use_account_numbers, show_lowest_subaccount_only FROM company_info").fetchone()
        query = _EFFECTS + """, selected AS (
            SELECT a.id, a.full_name, a.name, a.number, a.active, coalesce(b.closing,'0') AS net,
                a.full_name_key
            FROM accounts a LEFT JOIN balances b ON b.account_id=a.id
            WHERE :zero OR coalesce(b.closing,'0')!='0') """
        debit = credit = 0
        # Stream grouped nets with bounded Python memory. Splitting the signed
        # per-account net here is deliberate: trial balance is not gross activity.
        for (net,) in raw.execute(query + "SELECT net FROM selected", params):
            net = int(net)
            debit += max(net, 0)
            credit += max(-net, 0)
        totals = TrialBalanceTotals(debit=money(debit, currency), credit=money(credit, currency), signed_net=money(debit-credit, currency))
        page = raw.execute(query + f"SELECT * FROM selected ORDER BY {account_order()} LIMIT :limit OFFSET :offset", {**params, "limit": inp.limit+1, "offset": offset}).fetchall()
        rows = [TrialBalanceRow(account_id=r[0], current_account_label=r[1], current_account_name=r[2],
            current_account_number=r[3], display_account_label=_account_display(r[1], r[2], r[3], numbers, lowest),
            active=bool(r[4]), signed_net=money(int(r[5]), currency),
            debit=money(max(int(r[5]), 0), currency), credit=money(max(-int(r[5]), 0), currency)) for r in page[:inp.limit]]
        return TrialBalanceOutput(metadata=state.metadata, totals=totals, rows=rows, count=len(rows),
            next_cursor=_continuation(state, offset, len(rows), len(page)>inp.limit, s.company))


_GL = _EFFECTS + """, selected AS (
 SELECT * FROM balances WHERE opening!='0' OR activity>0
), running AS (
 SELECT e.*, bookflow_sum_int(debit_minor_units-credit_minor_units) OVER (
   PARTITION BY account_id ORDER BY effective_date, batch_id, line_no, id
   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_net
 FROM effects e
), flat AS (
 SELECT account_id, 0 AS phase, '' AS effective_date, '' AS batch_id, 0 AS line_no,
        '' AS posting_line_id, 'opening' AS kind, opening AS net, '0' AS debit, '0' AS credit
 FROM selected
 UNION ALL
 SELECT account_id, 1, effective_date, batch_id, line_no, id, 'posting', running_net,
        debit_minor_units, credit_minor_units FROM running WHERE effective_date>=:date_from
 UNION ALL
 SELECT account_id, 2, '', '', 0, '', 'closing', closing, debits, credits FROM selected
), ordered AS (
 SELECT f.*, a.number AS account_number, a.full_name_key AS account_full_name_key
 FROM flat f JOIN accounts a ON a.id=f.account_id
)
"""


def general_ledger(inp: GeneralLedgerInput, s, *, principal_id=None) -> GeneralLedgerOutput:
    with _snapshot(s.company):
        register_ledger_functions(s.company)
        account_id = None
        if inp.cursor is not None:
            # Retain the initially resolved stable ID: renaming a selected full
            # name must stale its continuation, not produce record-not-found.
            account_id = _decode_cursor(inp.cursor, s.company).account_id
        elif inp.account is not None:
            from bookflow.company.accounts import resolve_account
            account_id = resolve_account(s.company, inp.account)["id"]
        state, offset = _state(s, inp, "general-ledger", principal_id, account_id)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"date_from": inp.date_from, "date_to": inp.date_to, "account": account_id}
        numbers, lowest = raw.execute(
            "SELECT use_account_numbers, show_lowest_subaccount_only FROM company_info").fetchone()
        values = raw.execute(_GL + """SELECT bookflow_sum_int(opening), bookflow_sum_int(debits),
            bookflow_sum_int(credits), bookflow_sum_int(closing) FROM selected""", params).fetchone()
        totals = GeneralLedgerTotals(**{key: money(int(value or 0), currency) for key, value in zip(
            ("opening", "period_debits", "period_credits", "closing"), values)})
        # Window calculation is inside running, before this bounded page slice.
        result = raw.execute(_GL + f""", page AS (
          SELECT account_id, phase, effective_date, batch_id, line_no, posting_line_id, kind, net, debit, credit
          FROM ordered
          ORDER BY {account_order("account_")}, phase, effective_date, batch_id, line_no, posting_line_id
          LIMIT :limit OFFSET :offset)
          SELECT p.*, a.full_name AS current_account_label, a.name AS current_account_name,
            a.number AS current_account_number, e.batch_kind, e.transaction_id,
            t.type AS transaction_type,
            e.revision_id, r.number AS transaction_number, e.reverses_batch_id, e.replaces_batch_id,
            e.recorded_at, e.account_snapshot, e.party_name, e.class_name, e.description
          FROM page p JOIN accounts a ON a.id=p.account_id
          LEFT JOIN effects e ON e.id=p.posting_line_id
          LEFT JOIN transaction_revisions r ON r.id=e.revision_id
          LEFT JOIN transactions t ON t.id=e.transaction_id
          ORDER BY {account_order("a.")}, p.phase, p.effective_date, p.batch_id, p.line_no, p.posting_line_id
        """, {**params, "limit": inp.limit+1, "offset": offset})
        columns = [d[0] for d in result.description]
        page = [dict(zip(columns, row)) for row in result.fetchall()]
        rows = []
        for row in page[:inp.limit]:
            row.pop("phase")
            row["display_account_label"] = _account_display(
                row["current_account_label"], row["current_account_name"],
                row["current_account_number"], numbers, lowest)
            row["signed_balance"] = money(int(row.pop("net")), currency)
            row["debit"], row["credit"] = money(int(row["debit"]), currency), money(int(row["credit"]), currency)
            if row["kind"] != "posting":
                for key in ("effective_date", "batch_id", "line_no", "posting_line_id"):
                    row[key] = None
            if row["account_snapshot"] is not None:
                row["account_snapshot"] = json.loads(row["account_snapshot"])
            rows.append(GeneralLedgerRow(**row))
        return GeneralLedgerOutput(metadata=state.metadata, totals=totals, rows=rows, count=len(rows),
            next_cursor=_continuation(state, offset, len(rows), len(page)>inp.limit, s.company))
