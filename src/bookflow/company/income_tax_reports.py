"""Income and expense activity grouped by the tax line each account is assigned.

``accounts.tax_line`` is the free-text tax-form classification a bookkeeper
sets on an account -- the line of the return the account's activity ends up on.
This report is the only thing that reads it: for an inclusive accounting period
it groups every income and expense account by that value, shows each line's
constituent accounts, and puts the accounts with no tax line into their own
group so nothing is silently dropped from a return.

Amounts are on each account's own normal side, which is what makes a group
legible: revenue is positive on an income line and a cost is positive on a
deduction line, exactly as ``report profit-and-loss`` prints them. The sign is
applied to each stored posting line before it is summed, never to an aggregate,
because the lossless text ``bookflow_sum_int`` returns would become a float the
moment SQLite did arithmetic on it.

Totals cover the whole filter, and because the filter is every income and
expense account, ``totals.net_income`` is the same figure ``report
profit-and-loss`` reports for the same two dates.

Which account types are income and expense is read from
``accounts.STATEMENT_FAMILY`` and which side each is normal on from
``accounts.NORMAL_BALANCE``, so a new account type reaches this report from the
one place the chart vocabulary is declared.

A continuation stales on any audited company change, which is what covers a
tax line being reassigned between two pages: the assignment lives on the
account, an account update is an audited write, and the financial continuation
state carries the company's audit sequence.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company import accounts as chart
from bookflow.company import ledger_reports as ledger


UNASSIGNED = "Unassigned"

INCOME_TYPES = frozenset(
    account_type for account_type, family in chart.STATEMENT_FAMILY.items()
    if family == "profit_and_loss")
CREDIT_NORMAL_TYPES = frozenset(
    account_type for account_type in INCOME_TYPES
    if chart.NORMAL_BALANCE[account_type] == "credit")


def _in(types) -> str:
    """A SQL type set written from the declared vocabulary, never retyped."""
    return "(" + ", ".join("'" + account_type + "'" for account_type in sorted(types)) + ")"


class IncomeTaxSummaryInput(ledger.TrialBalanceInput):
    date_from: str = Field(min_length=10, max_length=10, description="Inclusive first accounting date, YYYY-MM-DD.")
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last accounting date, YYYY-MM-DD.")
    include_zero: bool = Field(default=False, description="Include income and expense accounts with no period activity, including inactive and never-posted ones.")

    _date_from = field_validator("date_from")(ledger.iso_date)

    @model_validator(mode="after")
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class IncomeTaxSummaryRow(ledger.StrictModel):
    kind: Literal["tax_line", "account"]
    tax_line: str | None
    display_tax_line: str
    account_count: int | None = None
    account_id: str | None = None
    current_account_label: str | None = None
    current_account_name: str | None = None
    current_account_number: str | None = None
    display_account_label: str | None = None
    account_type: str | None = None
    parent_id: str | None = None
    active: bool | None = None
    amount: ledger.MoneyOutput


class IncomeTaxSummaryTotals(ledger.StrictModel):
    income: ledger.MoneyOutput
    expense: ledger.MoneyOutput
    net_income: ledger.MoneyOutput


class IncomeTaxSummaryOutput(ledger.Page):
    rows: list[IncomeTaxSummaryRow]
    totals: IncomeTaxSummaryTotals


# Only a stored line's own two integer columns are subtracted in SQLite. The
# normal side is chosen per line from the account's declared type, so the group
# sums and the report total are second-level aggregation over lossless text --
# which bookflow_sum_int accepts exactly -- rather than arithmetic on it.
_QUERY = f"""
WITH amounts AS (
 SELECT l.account_id AS account_id,
   bookflow_sum_int(CASE WHEN a.type IN {_in(CREDIT_NORMAL_TYPES)}
     THEN l.credit_minor_units - l.debit_minor_units
     ELSE l.debit_minor_units - l.credit_minor_units END) AS amount
 FROM posting_lines l
 JOIN posting_batches b ON b.id = l.batch_id
 JOIN accounts a ON a.id = l.account_id
 WHERE b.effective_date >= :date_from AND b.effective_date <= :date_to
   AND a.type IN {_in(INCOME_TYPES)}
 GROUP BY l.account_id
), tax_accounts AS (
 SELECT a.id, a.full_name, a.name, a.number, a.type, a.parent_id, a.active, a.full_name_key,
        a.tax_line, coalesce(m.amount, '0') AS amount
 FROM accounts a LEFT JOIN amounts m ON m.account_id = a.id
 WHERE a.type IN {_in(INCOME_TYPES)}
), selected AS (
 SELECT * FROM tax_accounts WHERE :zero OR amount != '0'
), tax_lines AS (
 SELECT tax_line, bookflow_sum_int(amount) AS amount, count(*) AS account_count
 FROM selected GROUP BY tax_line
), flat AS (
 SELECT tax_line, 0 AS phase, 'tax_line' AS kind, amount, account_count,
        NULL AS id, NULL AS full_name, NULL AS name, NULL AS number, NULL AS type,
        NULL AS parent_id, NULL AS active, NULL AS full_name_key
 FROM tax_lines
 UNION ALL
 SELECT tax_line, 1, 'account', amount, NULL,
        id, full_name, name, number, type, parent_id, active, full_name_key
 FROM selected
)
"""


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    for row in cursor:
        yield dict(zip(columns, row))


def income_tax_summary(inp: IncomeTaxSummaryInput, s, *, principal_id=None) -> IncomeTaxSummaryOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "income-tax-summary", principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        numbers, lowest = raw.execute(
            "SELECT use_account_numbers, show_lowest_subaccount_only FROM company_info").fetchone()
        params = {"date_from": inp.date_from, "date_to": inp.date_to, "zero": inp.include_zero}
        income = expense = 0
        # Every income and expense account, including those this page will not
        # show and those the zero filter hides, so no page boundary and no filter
        # can move a total. Each account's amount is range-checked here.
        for account_type, amount in raw.execute(_QUERY + "SELECT type, amount FROM tax_accounts", params):
            value = ledger.money(int(amount), currency).minor_units
            if account_type in CREDIT_NORMAL_TYPES:
                income += value
            else:
                expense += value
        totals = IncomeTaxSummaryTotals(income=ledger.money(income, currency),
            expense=ledger.money(expense, currency), net_income=ledger.money(income - expense, currency))
        page = list(_rows(raw.execute(_QUERY + f"""SELECT * FROM flat
            ORDER BY tax_line IS NULL, tax_line, phase, {ledger.account_order()}
            LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset})))
        rows = []
        for row in page[:inp.limit]:
            common = dict(kind=row["kind"], tax_line=row["tax_line"],
                display_tax_line=UNASSIGNED if row["tax_line"] is None else row["tax_line"],
                amount=ledger.money(int(row["amount"]), currency))
            if row["kind"] == "tax_line":
                rows.append(IncomeTaxSummaryRow(account_count=row["account_count"], **common))
                continue
            rows.append(IncomeTaxSummaryRow(account_id=row["id"], current_account_label=row["full_name"],
                current_account_name=row["name"], current_account_number=row["number"],
                display_account_label=ledger._account_display(
                    row["full_name"], row["name"], row["number"], numbers, lowest),
                account_type=row["type"], parent_id=row["parent_id"], active=bool(row["active"]), **common))
        return IncomeTaxSummaryOutput(metadata=state.metadata, rows=rows, count=len(rows), totals=totals,
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
