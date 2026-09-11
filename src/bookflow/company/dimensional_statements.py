"""Profit and loss read across one posting dimension: a column per job, or per class.

These are the profit-and-loss statement, split sideways. The row set, the account
order, the section arithmetic and the whole-statement totals are the ones
``financial_statements`` already computes, so the Total column of a by-job or
by-class report is the profit-and-loss for the same dates, account for account and
total for total, rather than a second arithmetic that happens to agree today.

What splits them is one column on the posting line. Every posting line carries the
party it names and the class it was entered under, so a column is a value of that
one column and a cell is one account's net within it. A line that names no customer
or job, and a line whose party is a vendor, an employee or an other name -- which is
not a job -- belong to the explicit Unassigned column; a line entered under no class
belongs to Unclassified. Neither is ever dropped, because dropping either would make
the columns stop adding up to the statement.

Columns are the dimension values with posting activity in the period, read in
hierarchy order so a job sits under the customer it is named beneath. A wide report
is still bounded: past ``columns`` named columns the remainder is folded into one
Other column that says how many values it holds, so a company with three hundred
jobs still gets a report that adds up rather than an error, and narrowing the
period or reading one customer's jobs brings the individual columns back.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from bookflow.company import financial_statements as statements
from bookflow.company import ledger_reports as ledger
from bookflow.core.errors import BookflowError

# What each report splits on, in one place: the posting-line expression that names
# the column, the list the column is labelled from, and what an absent value is called.
DIMENSIONS = {
    "job": dict(
        report="profit-and-loss-by-job",
        # A party is only a customer or job when the line says so. A vendor or an
        # employee named on an expense line is not a job and is not made one here.
        expression="CASE WHEN l.name_type='customer' THEN l.name_id END",
        table="customers",
        unassigned="Unassigned",
    ),
    "class": dict(
        report="profit-and-loss-by-class",
        expression="l.class_id",
        table="classes",
        unassigned="Unclassified",
    ),
}

MAX_COLUMNS = 200


class DimensionalProfitAndLossInput(statements.ProfitAndLossInput):
    columns: int = Field(default=50, ge=1, le=MAX_COLUMNS, description="How many named columns are shown before the remainder is folded into one Other column.")


class DimensionColumn(ledger.StrictModel):
    kind: Literal["value", "unassigned", "other"]
    id: str | None
    label: str
    current_label: str | None
    parent_id: str | None
    active: bool | None
    folded_count: int | None
    totals: statements.ProfitAndLossTotals


class DimensionalStatementRow(ledger.StrictModel):
    account_id: str
    current_account_label: str
    current_account_name: str
    current_account_number: str | None
    display_account_label: str
    account_type: str
    parent_id: str | None
    active: bool
    section: Literal["income", "cost_of_goods_sold", "expense", "other_income", "other_expense"]
    amounts: list[ledger.MoneyOutput]
    total: ledger.MoneyOutput


class DimensionalProfitAndLossOutput(ledger.Page):
    dimension: Literal["job", "class"]
    columns: list[DimensionColumn]
    rows: list[DimensionalStatementRow]
    totals: statements.ProfitAndLossTotals


# Ordering for a column heading read off a hierarchical list: the hierarchy name, so
# a job follows the customer it is named under, and a value whose record has been
# removed from the list follows them all by its stable id.
_COLUMN_ORDER = "full_name_key IS NULL, full_name_key, dimension"


def _cells_query(dimension):
    """One row per account and dimension value, over the requested period only."""
    return f"""
WITH cells AS (
 SELECT l.account_id AS account_id, {dimension['expression']} AS dimension, a.type AS account_type,
   bookflow_sum_int(l.debit_minor_units - l.credit_minor_units) AS net
 FROM posting_lines l
 JOIN posting_batches b ON b.id = l.batch_id
 JOIN accounts a ON a.id = l.account_id
 WHERE b.effective_date >= :date_from AND b.effective_date <= :date_to
   AND {statements.PL_ACCOUNT_TYPES_SQL}
 GROUP BY l.account_id, {dimension['expression']}, a.type
)"""


def _columns_query(dimension):
    """Every dimension value with activity, with the label its list carries now."""
    return _cells_query(dimension) + f""", present AS (
 SELECT DISTINCT dimension FROM cells
)
SELECT p.dimension, d.full_name, d.full_name_key, d.parent_id, d.active
 FROM present p LEFT JOIN {dimension['table']} d ON d.id = p.dimension
 ORDER BY {_COLUMN_ORDER}"""


def _statement(inp, s, *, dimension, principal_id):
    facts = DIMENSIONS[dimension]
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, facts["report"], principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        numbers, lowest = raw.execute(
            "SELECT use_account_numbers, show_lowest_subaccount_only FROM company_info").fetchone()
        params = {"date_from": inp.date_from, "date_to": inp.date_to,
                  "account": None, "zero": inp.include_zero}
        # Every account that took a posting in the period, which is the statement's own
        # account set widened by the accounts a split can still have money in.
        query, order = statements._query(True, present=statements.PL_POSTED)

        # The whole statement first, from the profit-and-loss expression itself, so
        # the Total column is that report rather than an agreement with it.
        sections = dict.fromkeys(statements.PL_SECTIONS, 0)
        account_totals = {}
        for row in statements._rows(raw.execute(query + "SELECT * FROM statement_accounts", params)):
            amount = ledger.money(statements._net(row, True), currency).minor_units
            sections[row["section"]] += amount
            account_totals[row["id"]] = amount
        totals = statements.profit_and_loss_totals(sections, currency)

        # Then the same money again, split by the dimension. Every cell is counted,
        # including the ones whose column is folded, so nothing leaves the report.
        # An income or expense account's type is its statement section, which is what
        # lets a cell be filed under a section without reading the row set again.
        cells, column_sections = {}, {}
        for account_id, value, account_type, net in raw.execute(
                _cells_query(facts) + "SELECT account_id, dimension, account_type, net FROM cells", params):
            if account_type not in sections:
                raise BookflowError("E_INTERNAL", message="A posting account has no profit-and-loss section")
            signed = int(net) if account_type in statements.DEBIT_TYPES else -int(net)
            cells[(account_id, value)] = cells.get((account_id, value), 0) + signed
            column_sections.setdefault(value, dict.fromkeys(statements.PL_SECTIONS, 0))
            column_sections[value][account_type] += signed

        present = [dict(zip(("dimension", "full_name", "full_name_key", "parent_id", "active"), row))
                   for row in raw.execute(_columns_query(facts), params)]
        named = [row for row in present if row["dimension"] is not None]
        unassigned = [row for row in present if row["dimension"] is None]
        shown, folded = named[:inp.columns], named[inp.columns:]

        columns, keys = [], []
        for row in shown:
            keys.append((row["dimension"],))
            columns.append(DimensionColumn(kind="value", id=row["dimension"],
                label=str(row["full_name"]) if row["full_name"] is not None else row["dimension"],
                current_label=row["full_name"], parent_id=row["parent_id"],
                active=None if row["active"] is None else bool(row["active"]),
                folded_count=None,
                totals=statements.profit_and_loss_totals(
                    column_sections.get(row["dimension"], dict.fromkeys(statements.PL_SECTIONS, 0)), currency)))
        if folded:
            merged = dict.fromkeys(statements.PL_SECTIONS, 0)
            for row in folded:
                for section, amount in column_sections.get(row["dimension"], {}).items():
                    merged[section] += amount
            keys.append(tuple(row["dimension"] for row in folded))
            columns.append(DimensionColumn(kind="other", id=None,
                label=f"Other ({len(folded)})", current_label=None, parent_id=None, active=None,
                folded_count=len(folded),
                totals=statements.profit_and_loss_totals(merged, currency)))
        if unassigned:
            keys.append((None,))
            columns.append(DimensionColumn(kind="unassigned", id=None, label=facts["unassigned"],
                current_label=None, parent_id=None, active=None, folded_count=None,
                totals=statements.profit_and_loss_totals(
                    column_sections.get(None, dict.fromkeys(statements.PL_SECTIONS, 0)), currency)))

        # The columns must carry the statement; a column set that does not is a defect
        # in this report, not a number to print.
        for section in statements.PL_SECTIONS:
            across = sum(getattr(column.totals, section).minor_units for column in columns)
            if across != sections[section]:
                raise BookflowError("E_INTERNAL", message="Dimensional columns do not add to the statement total")

        page = list(statements._rows(raw.execute(query + f"""SELECT * FROM statement_accounts
            ORDER BY {order}, {ledger.account_order()} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset})))
        rows = []
        for row in page[:inp.limit]:
            amounts = [sum(cells.get((row["id"], value), 0) for value in key) for key in keys]
            total = account_totals[row["id"]]
            if sum(amounts) != total:
                raise BookflowError("E_INTERNAL", message="Dimensional row cells do not add to the account total")
            label = ledger._account_display(row["full_name"], row["name"], row["number"], numbers, lowest)
            rows.append(DimensionalStatementRow(account_id=row["id"],
                current_account_label=row["full_name"], current_account_name=row["name"],
                current_account_number=row["number"], display_account_label=label,
                account_type=row["type"], parent_id=row["parent_id"], active=bool(row["active"]),
                section=row["section"],
                amounts=[ledger.money(amount, currency) for amount in amounts],
                total=ledger.money(total, currency)))
        return DimensionalProfitAndLossOutput(metadata=state.metadata, dimension=dimension,
            columns=columns, rows=rows, count=len(rows), totals=totals,
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def profit_and_loss_by_job(inp: DimensionalProfitAndLossInput, s, *, principal_id=None) -> DimensionalProfitAndLossOutput:
    return _statement(inp, s, dimension="job", principal_id=principal_id)


def profit_and_loss_by_class(inp: DimensionalProfitAndLossInput, s, *, principal_id=None) -> DimensionalProfitAndLossOutput:
    return _statement(inp, s, dimension="class", principal_id=principal_id)
