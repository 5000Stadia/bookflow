"""Bounded accrual statements of the immutable journal effects actually entered."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company import ledger_reports as ledger


class ProfitAndLossInput(ledger.TrialBalanceInput):
    date_from: str = Field(min_length=10, max_length=10, description="Inclusive first accounting date, YYYY-MM-DD.")
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last accounting date, YYYY-MM-DD.")
    include_zero: bool = Field(default=False, description="Include zero period nets, including inactive and never-posted income and expense accounts.")

    _date_from = field_validator("date_from")(ledger.iso_date)

    @model_validator(mode="after")
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class BalanceSheetInput(ledger.TrialBalanceInput):
    pass


class StatementRow(ledger.StrictModel):
    account_id: str
    current_account_label: str
    current_account_name: str
    current_account_number: str | None
    display_account_label: str
    account_type: str
    parent_id: str | None
    active: bool
    section: Literal["income", "cost_of_goods_sold", "expense", "other_income", "other_expense", "assets", "liabilities", "equity"]
    amount: ledger.MoneyOutput


class ProfitAndLossTotals(ledger.StrictModel):
    income: ledger.MoneyOutput
    cost_of_goods_sold: ledger.MoneyOutput
    gross_profit: ledger.MoneyOutput
    expense: ledger.MoneyOutput
    net_operating_income: ledger.MoneyOutput
    other_income: ledger.MoneyOutput
    other_expense: ledger.MoneyOutput
    net_income: ledger.MoneyOutput


class BalanceSheetTotals(ledger.StrictModel):
    assets: ledger.MoneyOutput
    liabilities: ledger.MoneyOutput
    posted_equity: ledger.MoneyOutput
    prior_earnings: ledger.MoneyOutput
    current_year_income: ledger.MoneyOutput
    total_equity: ledger.MoneyOutput
    liabilities_and_equity: ledger.MoneyOutput
    difference: ledger.MoneyOutput


class ProfitAndLossOutput(ledger.Page):
    rows: list[StatementRow]
    totals: ProfitAndLossTotals


class BalanceSheetOutput(ledger.Page):
    rows: list[StatementRow]
    totals: BalanceSheetTotals
    fiscal_year_start: str


PL_SECTIONS = ("income", "cost_of_goods_sold", "expense", "other_income", "other_expense")
BS_SECTIONS = ("assets", "liabilities", "equity")
DEBIT_TYPES = {"bank", "accounts_receivable", "other_current_asset", "fixed_asset", "other_asset", "cost_of_goods_sold", "expense", "other_expense"}
SECTION_SQL = """CASE
 WHEN a.type IN ('bank','accounts_receivable','other_current_asset','fixed_asset','other_asset') THEN 'assets'
 WHEN a.type IN ('accounts_payable','credit_card','other_current_liability','long_term_liability') THEN 'liabilities'
 ELSE a.type END"""


def _query(profit_and_loss):
    sections = PL_SECTIONS if profit_and_loss else BS_SECTIONS
    order = "CASE section " + " ".join(f"WHEN '{name}' THEN {i}" for i, name in enumerate(sections)) + " END"
    types = "a.type IN ('income','cost_of_goods_sold','expense','other_income','other_expense')"
    if not profit_and_loss:
        types = f"NOT ({types}) AND a.type!='non_posting'"
    nonzero = "coalesce(b.debits,'0')!=coalesce(b.credits,'0')" if profit_and_loss else "coalesce(b.closing,'0')!='0'"
    query = ledger._EFFECTS + f""", statement_accounts AS (
        SELECT a.id, a.full_name, a.name, a.number, a.type, a.parent_id, a.active,
            a.full_name_key, {SECTION_SQL} AS section,
            coalesce(b.closing,'0') AS closing, coalesce(b.debits,'0') AS debits,
            coalesce(b.credits,'0') AS credits
        FROM accounts a LEFT JOIN balances b ON b.account_id=a.id
        WHERE {types} AND (:zero OR {nonzero})) """
    return query, order


def _net(row, profit_and_loss):
    raw = int(row["debits"]) - int(row["credits"]) if profit_and_loss else int(row["closing"])
    return raw if row["type"] in DEBIT_TYPES else -raw


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    for row in cursor:
        yield dict(zip(columns, row))


def _statement(inp, s, *, profit_and_loss, principal_id):
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        kind = "profit-and-loss" if profit_and_loss else "balance-sheet"
        state, offset = ledger._state(s, inp, kind, principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        month, numbers, lowest = raw.execute("""SELECT fiscal_year_start_month,
            use_account_numbers, show_lowest_subaccount_only FROM company_info""").fetchone()
        year = int(inp.date_to[:4]) - (int(inp.date_to[5:7]) < month)
        fiscal_start = f"{year:04}-{month:02}-01" if year >= 1 else "0001-01-01"
        params = {"date_to": inp.date_to, "date_from": inp.date_from if profit_and_loss else fiscal_start,
                  "account": None, "zero": inp.include_zero}
        query, order = _query(profit_and_loss)
        totals = dict.fromkeys(PL_SECTIONS if profit_and_loss else BS_SECTIONS, 0)
        # Stream all own-account values, including those beyond the requested page.
        # Checking their range prevents page size from hiding an invalid amount.
        for row in _rows(raw.execute(query + "SELECT * FROM statement_accounts", params)):
            amount = ledger.money(_net(row, profit_and_loss), currency).minor_units
            totals[row["section"]] += amount
        if profit_and_loss:
            totals["gross_profit"] = totals["income"] - totals["cost_of_goods_sold"]
            totals["net_operating_income"] = totals["gross_profit"] - totals["expense"]
            totals["net_income"] = totals["net_operating_income"] + totals["other_income"] - totals["other_expense"]
            typed_totals = ProfitAndLossTotals(**{key: ledger.money(value, currency) for key, value in totals.items()})
        else:
            prior = current = 0
            for opening, closing in raw.execute(ledger._EFFECTS + """SELECT b.opening, b.closing
                    FROM balances b JOIN accounts a ON a.id=b.account_id
                    WHERE a.type IN ('income','cost_of_goods_sold','expense','other_income','other_expense')""", params):
                prior -= int(opening)
                current -= int(closing) - int(opening)
            totals["posted_equity"] = totals.pop("equity")
            totals.update(prior_earnings=prior, current_year_income=current)
            totals["total_equity"] = totals["posted_equity"] + prior + current
            totals["liabilities_and_equity"] = totals["liabilities"] + totals["total_equity"]
            totals["difference"] = totals["assets"] - totals["liabilities_and_equity"]
            typed_totals = BalanceSheetTotals(**{key: ledger.money(value, currency) for key, value in totals.items()})
        page = list(_rows(raw.execute(query + f"""SELECT * FROM statement_accounts
            ORDER BY {order}, full_name_key, id LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit+1, "offset": offset})))
        rows = []
        for row in page[:inp.limit]:
            label = row["name"] if lowest else row["full_name"]
            if numbers and row["number"]:
                label = f"{row['number']} · {label}"
            rows.append(StatementRow(account_id=row["id"], current_account_label=row["full_name"],
                current_account_name=row["name"], current_account_number=row["number"],
                display_account_label=label, account_type=row["type"], parent_id=row["parent_id"],
                active=bool(row["active"]), section=row["section"], amount=ledger.money(_net(row, profit_and_loss), currency)))
        result = dict(metadata=state.metadata, totals=typed_totals, rows=rows, count=len(rows),
                      next_cursor=ledger._continuation(state, offset, len(rows), len(page)>inp.limit, s.company))
        return ProfitAndLossOutput(**result) if profit_and_loss else BalanceSheetOutput(**result, fiscal_year_start=fiscal_start)


def profit_and_loss(inp: ProfitAndLossInput, s, *, principal_id=None) -> ProfitAndLossOutput:
    return _statement(inp, s, profit_and_loss=True, principal_id=principal_id)


def balance_sheet(inp: BalanceSheetInput, s, *, principal_id=None) -> BalanceSheetOutput:
    return _statement(inp, s, profit_and_loss=False, principal_id=principal_id)
