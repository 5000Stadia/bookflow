"""The indirect statement of cash flows, reconciled to the other two statements.

The report is one identity, not a collection of rules. At every date the books
balance, so the signed net (debit minus credit) of every account together is
zero. Split the chart into cash accounts, the remaining balance-sheet accounts
and the income accounts, and take the change each one made over an inclusive
period::

    change(cash) + change(other balance sheet) + change(income accounts) = 0

``report profit-and-loss`` reports net income for that same period, and net
income is exactly the negated signed change of the income accounts, because
income is credit-normal and an expense debit-normal. Substituting gives the
statement this module renders::

    change(cash) = net income - change(other balance sheet)

So each balance-sheet account contributes its own negated signed change: an
increase in an asset is a use of cash and a decrease is a source, while an
increase in a liability or in equity is a source and a decrease a use. That is
one rule, applied to a credit-normal account through the sign its stored lines
already carry, rather than two rules that could disagree.

Because the identity holds over the whole chart, the three section subtotals
plus net income plus the opening cash balance are the closing cash balance
whatever the classification is; sectioning only decides which subtotal an
account lands in. ``totals.difference`` is that reconciliation, published on
the report the way the balance sheet publishes its own, and it is zero for any
company whose books balance.

**One answer to which section an account is in.**
``accounts.cash_flow_section`` is that answer: a section the account itself
declares, falling back to the section its type gives. Both halves live in
``bookflow.company.accounts`` beside the rest of the chart vocabulary --
``CASH_FLOW_SECTION_BY_TYPE`` is the type rule and ``cash_flow_section()``
resolves an account against it -- and ``_section_sql`` below is that same
resolution written once for SQLite, over the same declared mapping. The type
rule is checked against ``STATEMENT_FAMILY`` at import, so an account type added
to the chart vocabulary without a section fails loudly instead of silently
falling out of a statement that would still look balanced.

**What the account-type vocabulary cannot say, and what the column says
instead.** A depreciation or amortisation add-back belongs in the operating
section on the anchor product's own statement, and no account type and no
``system_role`` distinguishes accumulated depreciation from any other fixed
asset. The add-back itself needs no help -- an increase in accumulated
depreciation is a credit against a fixed-asset account, which is a negative
asset change and therefore a source of cash -- but the type rule reports it
under investing. Declaring ``cash_flow_section = 'operating'`` on the
accumulated-depreciation account moves that row, and only that row, to
operating. Nothing declares itself by default, so a company that has said
nothing gets exactly the statement it got before the column existed; and
because sectioning only chooses which subtotal a change lands in, net change in
cash and closing cash are the same figures either way.

A profit-and-loss account may declare ``operating`` and nothing else, because
the statement reports every income and expense effect inside net income, which
it reports under operating. That declaration records what is already true of
depreciation expense rather than moving anything.
"""
from __future__ import annotations

from typing import get_args

from pydantic import Field, field_validator, model_validator

from bookflow.company import accounts as chart
from bookflow.company import ledger_reports as ledger


# The chart vocabulary this statement runs on, named here and declared once in
# `bookflow.company.accounts`, which also checks the type rule against
# STATEMENT_FAMILY at import.
SECTIONS = get_args(chart.CashFlowSection)
CASH_TYPES = chart.CASH_FLOW_CASH_TYPES
SECTION_BY_TYPE = chart.CASH_FLOW_SECTION_BY_TYPE
SECTION_COLUMN = "cash_flow_section"

INCOME_TYPES = frozenset(
    account_type for account_type, family in chart.STATEMENT_FAMILY.items()
    if family == "profit_and_loss")


def _in(types) -> str:
    """A SQL type set written from the declared vocabulary, never retyped."""
    return "(" + ", ".join("'" + account_type + "'" for account_type in sorted(types)) + ")"


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    for row in cursor:
        yield dict(zip(columns, row))


def _section_sql() -> str:
    """``accounts.cash_flow_section`` in SQL: the account's own answer, else its type's.

    The arms are written from the same mapping the Python resolver uses, so the
    two cannot drift apart into two classifications that disagree.
    """
    by_section: dict[str, list[str]] = {}
    for account_type, section in SECTION_BY_TYPE.items():
        by_section.setdefault(section, []).append(account_type)
    arms = " ".join(f"WHEN a.type IN {_in(by_section[section])} THEN '{section}'"
                    for section in SECTIONS if section in by_section)
    return f"coalesce(a.{SECTION_COLUMN}, CASE " + arms + " END)"


SECTION_ORDER = "CASE section " + " ".join(
    f"WHEN '{section}' THEN {index}" for index, section in enumerate(SECTIONS)) + " END"


class CashFlowsInput(ledger.TrialBalanceInput):
    date_from: str = Field(min_length=10, max_length=10, description="Inclusive first accounting date, YYYY-MM-DD.")
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last accounting date, YYYY-MM-DD.")
    include_zero: bool = Field(default=False, description="Include accounts whose balance did not change over the period, including inactive and never-posted balance-sheet accounts.")

    _date_from = field_validator("date_from")(ledger.iso_date)

    @model_validator(mode="after")
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class CashFlowRow(ledger.StrictModel):
    """One balance-sheet account's period change.

    ``opening_balance`` and ``closing_balance`` are on the account's own normal
    side, the way every other statement here prints a balance, so a liability of
    a thousand reads as a thousand rather than as its stored credit sign.
    ``amount`` is the cash effect and keeps the statement's own convention: an
    asset that grew reads negative and a liability or equity that grew reads
    positive.
    """
    account_id: str
    current_account_label: str
    current_account_name: str
    current_account_number: str | None
    display_account_label: str
    account_type: str
    parent_id: str | None
    active: bool
    section: chart.CashFlowSection
    opening_balance: ledger.MoneyOutput
    closing_balance: ledger.MoneyOutput
    amount: ledger.MoneyOutput


class CashFlowTotals(ledger.StrictModel):
    net_income: ledger.MoneyOutput
    operating_adjustments: ledger.MoneyOutput
    operating: ledger.MoneyOutput
    investing: ledger.MoneyOutput
    financing: ledger.MoneyOutput
    net_change_in_cash: ledger.MoneyOutput
    opening_cash: ledger.MoneyOutput
    closing_cash: ledger.MoneyOutput
    difference: ledger.MoneyOutput


class CashFlowsOutput(ledger.Page):
    rows: list[CashFlowRow]
    totals: CashFlowTotals


# Opening is every effect strictly before date_from and closing every effect
# through date_to, so closing less opening is the period's own change. Both
# arrive as lossless text from bookflow_sum_int and are subtracted in Python;
# SQLite would coerce arithmetic on that text to REAL.
_QUERY = ledger._EFFECTS + f""", cash_flow_accounts AS (
    SELECT a.id, a.full_name, a.name, a.number, a.type, a.parent_id, a.active, a.full_name_key,
        {_section_sql()} AS section,
        coalesce(b.opening, '0') AS opening, coalesce(b.closing, '0') AS closing,
        coalesce(b.debits, '0') AS debits, coalesce(b.credits, '0') AS credits
    FROM accounts a LEFT JOIN balances b ON b.account_id = a.id), selected AS (
    SELECT * FROM cash_flow_accounts
    WHERE type IN {_in(SECTION_BY_TYPE)} AND (:zero OR opening != closing)) """


def cash_flows(inp: CashFlowsInput, s, *, principal_id=None) -> CashFlowsOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "cash-flows", principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        numbers, lowest = raw.execute(
            "SELECT use_account_numbers, show_lowest_subaccount_only FROM company_info").fetchone()
        params = {"date_from": inp.date_from, "date_to": inp.date_to,
                  "account": None, "zero": inp.include_zero}
        sections = dict.fromkeys(SECTIONS, 0)
        # Every sectioned account, including the ones this page will not show and
        # the ones the zero filter hides, so a page boundary or a filter can never
        # move a subtotal. Each account's own change is range-checked here rather
        # than only where it is rendered.
        for section, opening, closing in raw.execute(
                _QUERY + f"SELECT section, opening, closing FROM cash_flow_accounts WHERE type IN {_in(SECTION_BY_TYPE)}",
                params):
            sections[section] += ledger.money(int(opening) - int(closing), currency).minor_units
        net_income = 0
        for account_type, debits, credits in raw.execute(
                _QUERY + f"SELECT type, debits, credits FROM cash_flow_accounts WHERE type IN {_in(INCOME_TYPES)}",
                params):
            signed = int(debits) - int(credits)
            credit_normal = chart.NORMAL_BALANCE[account_type] == "credit"
            # What the profit and loss prints for this account: positive for
            # revenue, positive for a cost, each read on its own normal side and
            # range-checked here exactly as that statement range-checks it.
            amount = ledger.money(-signed if credit_normal else signed, currency).minor_units
            net_income += amount if credit_normal else -amount
        opening_cash = closing_cash = 0
        for opening, closing in raw.execute(
                _QUERY + f"SELECT opening, closing FROM cash_flow_accounts WHERE type IN {_in(CASH_TYPES)}", params):
            opening_cash += ledger.money(int(opening), currency).minor_units
            closing_cash += ledger.money(int(closing), currency).minor_units
        totals = dict(net_income=net_income, operating_adjustments=sections["operating"],
                      operating=net_income + sections["operating"],
                      investing=sections["investing"], financing=sections["financing"])
        totals["net_change_in_cash"] = totals["operating"] + totals["investing"] + totals["financing"]
        totals["opening_cash"], totals["closing_cash"] = opening_cash, closing_cash
        # The reconciliation, published rather than asserted away: for books that
        # balance this is zero, and the balance sheet publishes its own the same way.
        totals["difference"] = closing_cash - opening_cash - totals["net_change_in_cash"]
        page = list(_rows(raw.execute(
            _QUERY + f"""SELECT * FROM selected ORDER BY {SECTION_ORDER}, {ledger.account_order()}
                LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset})))
        rows = []
        for row in page[:inp.limit]:
            label = ledger._account_display(row["full_name"], row["name"], row["number"], numbers, lowest)
            side = -1 if chart.NORMAL_BALANCE[row["type"]] == "credit" else 1
            opening, closing = int(row["opening"]), int(row["closing"])
            rows.append(CashFlowRow(account_id=row["id"], current_account_label=row["full_name"],
                current_account_name=row["name"], current_account_number=row["number"],
                display_account_label=label, account_type=row["type"], parent_id=row["parent_id"],
                active=bool(row["active"]), section=row["section"],
                opening_balance=ledger.money(side * opening, currency),
                closing_balance=ledger.money(side * closing, currency),
                amount=ledger.money(opening - closing, currency)))
        return CashFlowsOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=CashFlowTotals(**{key: ledger.money(value, currency) for key, value in totals.items()}),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
