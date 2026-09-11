"""What each tax agency is owed, read from the postings the books actually made.

This is the payables report for money the company collected on somebody else's behalf. It
starts from every posting effect on an account whose system role is ``sales_tax_payable``,
dated on or before the as-of date, signed so that what is owed is positive -- credit minus
debit, because a tax liability is credit-normal -- and then asks each effect which agency it
belongs to.

**Three attributions, one rule.** A sale's tax leg names its ``sales_tax_components`` row
through ``posting_line_sources.tax_component_id``; a credit memo's tax leg is named by
``credit_tax_components.posting_source_id``; a remittance's liability leg is named by
``sales_tax_payment_profiles.liability_posting_source_id``. Each of those names an agency, and
a reversal is resolved by following ``posting_line_sources.reversed_source_id`` back to the
attribution it inverts -- which is why voiding an invoice, a credit memo or a remittance moves
this report by exactly what the document moved and by nothing else.

**Nothing is dropped.** An effect on a sales-tax-payable account that no attribution claims --
a journal entry posted straight at the liability, which is how a sales tax adjustment would be
entered today -- is reported on its own row with no agency rather than left out. That is what
makes the report's total the account's own balance for the same date: every posting is in
exactly one row, and the columns of a row add up to its balance. Both identities are checked
in Python on every row, where an integer is exact and unbounded.

**Accrual only, and deliberately.** ``company_info.sales_tax_liability_basis`` has two
settings. On ``invoice_date`` the liability is recorded when the invoice is, which is what
every posting above already says. On ``payment_receipt`` the liability would fall due when the
customer pays -- and the books hold nothing that could say so, because ``sales_defaults``
refuses to post a taxable sale at all unless the basis is ``invoice_date``, so a company on
the cash basis has never recorded a tax component to report. There is no deferred-tax account
and no posting that moves tax from unearned to payable. Reporting an accrual figure under a
cash-basis policy would be a number nothing supports, so this refuses with
``E_TAX_BASIS_UNSUPPORTED`` instead.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.core.errors import BookflowError

NO_AGENCY = "Not attributed to an agency"

COLUMNS = ("tax_charged", "tax_credited", "remitted", "unattributed", "balance")


class SalesTaxLiabilityInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive accounting as-of date, YYYY-MM-DD; tax effects on or before it are counted.")
    basis: Literal["accrual"] = "accrual"
    agency: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional tax agency vendor ID or name; omit for every agency with a balance.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        """The report period's single inclusive bound, named as every report names it."""
        return self.as_of


class SalesTaxLiabilityTotals(StrictModel):
    tax_charged: MoneyOutput
    tax_credited: MoneyOutput
    remitted: MoneyOutput
    unattributed: MoneyOutput
    balance: MoneyOutput


class SalesTaxLiabilityRow(StrictModel):
    agency_id: str | None
    current_agency_name: str | None
    display_agency_label: str
    active: bool | None
    is_tax_agency: bool | None
    tax_charged: MoneyOutput
    tax_credited: MoneyOutput
    remitted: MoneyOutput
    unattributed: MoneyOutput
    balance: MoneyOutput


class SalesTaxLiabilityOutput(ledger.Page):
    totals: SalesTaxLiabilityTotals
    rows: list[SalesTaxLiabilityRow]


# Never let SQLite do arithmetic on an aggregate: bookflow_sum_int returns lossless text and
# SQLite would coerce text arithmetic to REAL. Every expression aggregated here is built from
# stored INTEGER columns only.
#
# `attributed` maps a posting line to the agency whose liability it moved. A reversal line
# carries its own attribution row pointing at the one it inverts, so following
# `reversed_source_id` is what makes a void land on the same agency as the document it undoes.
# A sale's reversal keeps `tax_component_id` verbatim, so that branch needs no such follow.
_EFFECTS = """
WITH liability AS (
 SELECT l.id AS line, l.debit_minor_units AS debit, l.credit_minor_units AS credit
 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
 JOIN accounts a ON a.id=l.account_id
 WHERE a.system_role='sales_tax_payable' AND b.effective_date<=:as_of
), attributed AS (
 SELECT ps.posting_line_id AS line, tc.agency_id AS agency, 'charged' AS origin
 FROM posting_line_sources ps JOIN sales_tax_components tc ON tc.id=ps.tax_component_id
 UNION ALL
 SELECT ps.posting_line_id, cc.agency_id, 'credited'
 FROM posting_line_sources ps JOIN credit_tax_components cc
      ON cc.posting_source_id=coalesce(ps.reversed_source_id, ps.id)
 UNION ALL
 SELECT ps.posting_line_id, pp.agency_id, 'remitted'
 FROM posting_line_sources ps JOIN sales_tax_payment_profiles pp
      ON pp.liability_posting_source_id=coalesce(ps.reversed_source_id, ps.id)
), cells AS (
 SELECT n.agency AS party,
        CASE WHEN n.origin='charged' THEN l.credit-l.debit ELSE 0 END AS tax_charged,
        CASE WHEN n.origin='credited' THEN l.debit-l.credit ELSE 0 END AS tax_credited,
        CASE WHEN n.origin='remitted' THEN l.debit-l.credit ELSE 0 END AS remitted,
        0 AS unattributed,
        l.credit-l.debit AS balance
 FROM liability l JOIN attributed n ON n.line=l.line
 UNION ALL
 SELECT NULL, 0, 0, 0, l.credit-l.debit, l.credit-l.debit
 FROM liability l WHERE NOT EXISTS (SELECT 1 FROM attributed n WHERE n.line=l.line)
), agency_columns AS (
 SELECT party, bookflow_sum_int(tax_charged) AS tax_charged,
        bookflow_sum_int(tax_credited) AS tax_credited,
        bookflow_sum_int(remitted) AS remitted,
        bookflow_sum_int(unattributed) AS unattributed,
        bookflow_sum_int(balance) AS balance
 FROM cells GROUP BY party
), selected AS (
 SELECT c.*, v.id AS agency_id, v.name, v.name_key, v.active, v.is_tax_agency
 FROM agency_columns c LEFT JOIN vendors v ON v.id=c.party
 WHERE (%s)
   AND (:agency IS NULL OR c.party=:agency)
) """ % " OR ".join(f"c.{name}!='0'" for name in COLUMNS)

# Agencies are vendors, which are a flat list, so the one name is both the label and the
# order; a party with no vendor record follows them all, by stable id.
_AGENCY_ORDER = "name_key IS NULL, name_key, coalesce(party,'')"


def _label(name):
    return NO_AGENCY if name is None else str(name)


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def require_accrual_basis(db):
    """Refuse rather than answer when the company's own policy is one nothing recorded."""
    basis = db.raw.execute("SELECT sales_tax_liability_basis FROM company_info").fetchone()[0]
    if basis != "invoice_date":
        raise BookflowError("E_TAX_BASIS_UNSUPPORTED", details={
            "sales_tax_liability_basis": basis, "supported": ["invoice_date"],
            "next": "Sales tax liability is derived from the tax recorded on posted sales, which "
                    "is only recorded on the invoice_date basis; set the company's sales tax "
                    "liability basis to invoice_date, or compute the cash-basis figure outside "
                    "Bookflow."})


def agency_balances(db, as_of: str, *, agency_id: str | None = None) -> dict[str | None, int]:
    """What each agency is owed on ``as_of``; the key is None for unattributed effects.

    This is the same derivation the report pages, read without paging so that a write can be
    checked against it. The caller owns the transaction: ``sales-tax pay`` calls this inside
    the writer so that what it refuses is what storage says at that instant.
    """
    iso_date(as_of)
    ledger.register_ledger_functions(db)
    return {row["party"]: int(row["balance"]) for row in _rows(db.raw.execute(
        _EFFECTS + "SELECT party, balance FROM selected", {"as_of": as_of, "agency": agency_id}))}


def sales_tax_liability(inp: SalesTaxLiabilityInput, s, *, principal_id=None) -> SalesTaxLiabilityOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        require_accrual_basis(s.company)
        agency_id = None
        if inp.cursor is not None:
            # Keep the stable ID resolved for the first page: renaming the selected agency
            # must stale the continuation rather than turn it into a record-not-found on
            # page two.
            agency_id = ledger._decode_cursor(inp.cursor, s.company).account_id
        elif inp.agency is not None:
            from bookflow.company.parties import resolve_party
            agency_id = resolve_party(s.company, "vendor", inp.agency)["id"]
        state, offset = ledger._state(s, inp, "sales-tax-liability", principal_id, agency_id,
                                      account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, "agency": agency_id}
        totals = dict.fromkeys(COLUMNS, 0)
        # Stream every agency's column set, including rows past this page, so a page boundary
        # can never hide an amount from a total. The balance is a fifth independent sum rather
        # than the other four added up, so the relation between them is checked on every row
        # instead of assumed: if the attribution branches and the raw posting sum could ever
        # disagree, one of them is wrong and the report says so rather than printing a figure
        # nothing supports.
        for row in raw.execute(_EFFECTS + f"SELECT {', '.join(COLUMNS)} FROM selected", params):
            values = {name: money(int(value), currency).minor_units
                      for name, value in zip(COLUMNS, row)}
            _check(values)
            for name in COLUMNS:
                totals[name] += values[name]
        _check(totals)
        page = _rows(raw.execute(_EFFECTS + f"""SELECT * FROM selected
            ORDER BY {_AGENCY_ORDER} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = [SalesTaxLiabilityRow(agency_id=row["agency_id"], current_agency_name=row["name"],
            display_agency_label=_label(row["name"]),
            active=None if row["active"] is None else bool(row["active"]),
            is_tax_agency=None if row["is_tax_agency"] is None else bool(row["is_tax_agency"]),
            **{name: money(int(row[name]), currency) for name in COLUMNS})
            for row in page[:inp.limit]]
        return SalesTaxLiabilityOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=SalesTaxLiabilityTotals(**{name: money(totals[name], currency) for name in COLUMNS}),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def _check(values):
    if (values["tax_charged"] - values["tax_credited"] - values["remitted"]
            + values["unattributed"] != values["balance"]):
        raise BookflowError("E_INTERNAL",
                            message="A sales tax balance is not its charges less its credits and remittances")
