"""The statement of cash flows, checked against an independent effect oracle.

Every figure asserted here is computed twice: once by the report, and once in
Python straight off the seeded posting lines, so the report's SQL is never its
own witness. The reconciliation -- net income plus the three section subtotals
plus opening cash equals closing cash, and closing cash equals the bank total
the balance sheet reports for the same date -- is an assertion in every case
below, not a comment.
"""
import sqlite3

import pytest

from bookflow.company import accounts as chart
from bookflow.company import cash_flow_reports as cf
from bookflow.company import financial_statements as fs
from bookflow.company import schema
from bookflow.company.charts import ACCOUNT_TYPES
from tests.test_row8_reports import ledger, insert, all_pages  # noqa: F401


# id -> account type. "a" and "bank2" are the cash the statement explains.
ADDED = (("receivable", "accounts_receivable"), ("inventory", "other_current_asset"),
         ("payable", "accounts_payable"), ("card", "credit_card"),
         ("accrued", "other_current_liability"), ("equipment", "fixed_asset"),
         ("deposit-asset", "other_asset"), ("loan", "long_term_liability"),
         ("equity", "equity"), ("bank2", "bank"))

# day, minor units, debit account, credit account
JANUARY = (("2026-01-05", 10000, "a", "equity"),
           ("2026-01-10", 3000, "receivable", "b"))
FEBRUARY = (("2026-02-02", 5000, "a", "b"),
            ("2026-02-03", 2000, "receivable", "b"),
            ("2026-02-04", 1500, "a", "receivable"),
            ("2026-02-05", 800, "c", "payable"),
            ("2026-02-06", 300, "payable", "a"),
            ("2026-02-07", 1200, "inventory", "a"),
            ("2026-02-08", 4000, "equipment", "loan"),
            ("2026-02-09", 250, "c", "card"),
            ("2026-02-10", 600, "equity", "a"),
            ("2026-02-11", 900, "a", "accrued"),
            ("2026-02-12", 700, "deposit-asset", "a"),
            # Depreciation: the add-back reaches the statement as a credit
            # against the fixed asset, because nothing on an account says which
            # fixed asset is accumulated depreciation.
            ("2026-02-13", 500, "c", "equipment"),
            ("2026-02-14", 1000, "bank2", "a"))


@pytest.fixture
def cash_ledger(ledger):
    """One account of every posting type, with "a" and "bank2" as the cash."""
    s, batch, _ = ledger
    for account, kind in (("b", "income"), ("c", "expense"), ("z", "non_posting")):
        s.company.raw.execute("UPDATE accounts SET type=? WHERE id=?", (kind, account))
    for account, kind in ADDED:
        insert(s.company, schema.accounts, id=account, name=account, name_key=account,
               full_name=account, full_name_key=account, path=account, depth=1,
               type=kind, currency="USD", active=True)
    for day, units, debit, credit in (*JANUARY, *FEBRUARY):
        batch(day, units, debit=debit, credit=credit)
    s.batch = batch
    return s


def flows(s, **kwargs):
    return cf.cash_flows(cf.CashFlowsInput(**{"date_from": "2026-02-01", "date_to": "2026-02-28", **kwargs}), s)


def oracle(s, date_from, date_to):
    """Signed opening and closing per account, read straight off the seeded lines."""
    types = dict(s.company.raw.execute("SELECT id, type FROM accounts").fetchall())
    opening = dict.fromkeys(types, 0)
    closing = dict.fromkeys(types, 0)
    for account, day, debit, credit in s.company.raw.execute("""
            SELECT l.account_id, b.effective_date, l.debit_minor_units, l.credit_minor_units
            FROM posting_lines l JOIN posting_batches b ON b.id = l.batch_id"""):
        if day > date_to:
            continue
        closing[account] += debit - credit
        if day < date_from:
            opening[account] += debit - credit
    return types, opening, closing


def declared(s):
    """What each account says its own cash-flow section is, read off the chart."""
    return dict(s.company.raw.execute("SELECT id, cash_flow_section FROM accounts").fetchall())


def expected(s, date_from="2026-02-01", date_to="2026-02-28"):
    """What the statement has to say, derived from the oracle and nothing else."""
    types, opening, closing = oracle(s, date_from, date_to)
    sections = dict.fromkeys(cf.SECTIONS, 0)
    declarations = declared(s)
    contributions, net_income, opening_cash, closing_cash = {}, 0, 0, 0
    for account, kind in types.items():
        if kind in cf.CASH_TYPES:
            opening_cash += opening[account]
            closing_cash += closing[account]
        elif kind in cf.SECTION_BY_TYPE:
            contribution = opening[account] - closing[account]
            contributions[account] = contribution
            sections[declarations[account] or cf.SECTION_BY_TYPE[kind]] += contribution
        elif kind in cf.INCOME_TYPES:
            net_income -= closing[account] - opening[account]
    operating = net_income + sections["operating"]
    return contributions, dict(
        net_income=net_income, operating_adjustments=sections["operating"], operating=operating,
        investing=sections["investing"], financing=sections["financing"],
        net_change_in_cash=operating + sections["investing"] + sections["financing"],
        opening_cash=opening_cash, closing_cash=closing_cash,
        difference=closing_cash - opening_cash - (operating + sections["investing"] + sections["financing"]))


def units(totals):
    return {key: value.minor_units for key, value in totals}


def test_every_posting_account_type_carries_exactly_one_cash_flow_disposition():
    """A type added to the chart vocabulary cannot fall silently out of the statement."""
    disposed = cf.CASH_TYPES | set(cf.SECTION_BY_TYPE) | cf.INCOME_TYPES | {"non_posting"}
    assert disposed == ACCOUNT_TYPES
    assert not cf.CASH_TYPES & set(cf.SECTION_BY_TYPE)
    assert cf.CASH_TYPES | set(cf.SECTION_BY_TYPE) == {
        account_type for account_type, family in chart.STATEMENT_FAMILY.items()
        if family == "balance_sheet"}
    assert set(cf.SECTION_BY_TYPE.values()) == set(cf.SECTIONS)


def test_the_sql_and_the_python_answer_which_section_identically():
    """Two readings of one rule would be two rules, and they would drift apart."""
    combinations = [(kind, section) for kind in sorted(ACCOUNT_TYPES)
                    for section in (None, *cf.SECTIONS)]
    values = ", ".join("(?, ?)" for _ in combinations)
    with sqlite3.connect(":memory:") as connection:
        answered = connection.execute(
            f"WITH a(type, {cf.SECTION_COLUMN}) AS (VALUES {values}) "
            f"SELECT type, {cf.SECTION_COLUMN}, {cf._section_sql()} FROM a",
            [field for pair in combinations for field in pair]).fetchall()
    assert answered == [(kind, section, chart.cash_flow_section(kind, section))
                        for kind, section in combinations]
    # And the rule really is the account's own answer first, the type's second.
    assert chart.cash_flow_section("fixed_asset", None) == "investing"
    assert chart.cash_flow_section("fixed_asset", "operating") == "operating"
    assert chart.cash_flow_section("expense", None) is None


def test_the_statement_reconciles_to_cash_to_the_profit_and_loss_and_to_the_balance_sheet(cash_ledger):
    s = cash_ledger
    contributions, totals = expected(s)
    report, rows = all_pages(lambda **kw: flows(s, **kw), limit=2)
    assert units(report.totals) == totals
    # The reconciliation itself, said three ways.
    assert report.totals.difference.minor_units == 0
    assert (report.totals.opening_cash.minor_units + report.totals.net_change_in_cash.minor_units
            == report.totals.closing_cash.minor_units)
    assert (report.totals.net_income.minor_units + report.totals.operating_adjustments.minor_units
            + report.totals.investing.minor_units + report.totals.financing.minor_units
            == report.totals.closing_cash.minor_units - report.totals.opening_cash.minor_units)
    # Net income is the figure the profit and loss reports for the same dates.
    profit = fs.profit_and_loss(fs.ProfitAndLossInput(date_from="2026-02-01", date_to="2026-02-28"), s)
    assert report.totals.net_income == profit.totals.net_income
    # Closing cash is the bank total the balance sheet reports for the same date.
    sheet, sheet_rows = all_pages(
        lambda **kw: fs.balance_sheet(fs.BalanceSheetInput(date_to="2026-02-28", **kw), s), limit=3)
    sheet_amounts = {row.account_id: row.amount.minor_units for row in sheet_rows}
    banks = sum(row.amount.minor_units for row in sheet_rows if row.account_type == "bank")
    assert banks == report.totals.closing_cash.minor_units
    assert sheet.totals.difference.minor_units == 0
    # And every row is the account's own change, in the section its type declares.
    assert {row.account_id: row.amount.minor_units for row in rows} == {
        account: value for account, value in contributions.items() if value}
    assert {row.account_id: row.section for row in rows} == {
        account: chart.cash_flow_section(dict(ADDED)[account], declared(s)[account])
        for account in contributions if contributions[account]}
    assert [row.section for row in rows] == sorted(
        (row.section for row in rows), key=cf.SECTIONS.index)
    # Balances read on the account's own normal side, and the cash effect is the
    # change in that balance: negated for an asset, taken as it stands for a
    # liability or for equity.
    for row in rows:
        change = row.closing_balance.minor_units - row.opening_balance.minor_units
        credit_normal = chart.NORMAL_BALANCE[row.account_type] == "credit"
        assert row.amount.minor_units == (change if credit_normal else -change)
        # And that normal-side closing balance is the very figure the balance
        # sheet prints for the same account on the same date.
        assert row.closing_balance.minor_units == sheet_amounts.get(row.account_id, 0)


def test_an_asset_rising_uses_cash_and_an_asset_falling_supplies_it(cash_ledger):
    s = cash_ledger
    rising = {row.account_id: row.amount.minor_units for row in flows(s, limit=200).rows}
    # Receivables, inventory and equipment all grew over February; equity fell by
    # the owner's draw. Liabilities grew, and every one of those is a source.
    assert rising["receivable"] == -500 and rising["inventory"] == -1200
    assert rising["equipment"] == -3500 and rising["deposit-asset"] == -700
    assert rising["payable"] == 500 and rising["card"] == 250 and rising["accrued"] == 900
    assert rising["loan"] == 4000 and rising["equity"] == -600

    # The same accounts read over the one day each moves the other way: a
    # receivable collected, and a payable settled.
    collected = flows(s, date_from="2026-02-04", date_to="2026-02-04", limit=200)
    assert units(collected.totals) == expected(s, "2026-02-04", "2026-02-04")[1]
    assert {row.account_id: row.amount.minor_units for row in collected.rows} == {"receivable": 1500}
    assert collected.totals.net_income.minor_units == 0
    assert collected.totals.operating.minor_units == 1500
    assert collected.totals.difference.minor_units == 0

    settled = flows(s, date_from="2026-02-06", date_to="2026-02-06", limit=200)
    assert units(settled.totals) == expected(s, "2026-02-06", "2026-02-06")[1]
    assert {row.account_id: row.amount.minor_units for row in settled.rows} == {"payable": -300}
    assert settled.totals.net_income.minor_units == 0
    assert settled.totals.operating.minor_units == -300
    assert settled.totals.net_change_in_cash.minor_units == -300
    assert settled.totals.difference.minor_units == 0


def test_depreciation_is_added_back_through_the_asset_it_was_credited_to(cash_ledger):
    s = cash_ledger
    equipment = next(row for row in flows(s, limit=200).rows if row.account_id == "equipment")
    # 4000 of equipment bought, 500 of it written off: the write-off is a source
    # of cash that offsets the expense inside net income, and it is reported in
    # investing because this account has declared no section of its own.
    assert declared(s)["equipment"] is None
    assert equipment.amount.minor_units == -3500 and equipment.section == "investing"
    without_depreciation = flows(s, date_from="2026-02-13", date_to="2026-02-13", limit=200)
    assert without_depreciation.totals.net_income.minor_units == -500
    assert without_depreciation.totals.investing.minor_units == 500
    assert without_depreciation.totals.net_change_in_cash.minor_units == 0
    assert without_depreciation.totals.difference.minor_units == 0


def test_a_declared_section_moves_the_add_back_to_operating_without_moving_cash(cash_ledger):
    """The chart's own answer wins, and only the subtotals it chooses between move."""
    s = cash_ledger
    # The shape a real chart has: accumulated depreciation is its own fixed-asset
    # account, and depreciation expense is its own expense account.
    insert(s.company, schema.accounts, id="accumulated", name="accumulated", name_key="accumulated",
           full_name="accumulated", full_name_key="accumulated", path="accumulated", depth=1,
           type="fixed_asset", currency="USD", active=True)
    insert(s.company, schema.accounts, id="wear", name="wear", name_key="wear", full_name="wear",
           full_name_key="wear", path="wear", depth=1, type="expense", currency="USD", active=True)
    s.batch("2026-02-20", 700, debit="wear", credit="accumulated")

    undeclared = flows(s, limit=200)
    assert units(undeclared.totals) == expected(s)[1]
    row = next(item for item in undeclared.rows if item.account_id == "accumulated")
    # A credit to a fixed asset is a falling asset, so a source of cash -- reported
    # in investing while the account has said nothing about itself.
    assert row.amount.minor_units == 700 and row.section == "investing"

    s.company.raw.execute(
        "UPDATE accounts SET cash_flow_section='operating' WHERE id IN ('accumulated', 'wear')")
    moved = flows(s, limit=200)
    assert units(moved.totals) == expected(s)[1]
    row = next(item for item in moved.rows if item.account_id == "accumulated")
    assert row.amount.minor_units == 700 and row.section == "operating"

    # The add-back is worth exactly 700 of operating cash it was not credited with
    # before, taken out of investing. Every other figure on the statement is the
    # one it already was, including the reconciliation.
    assert moved.totals.operating.minor_units == undeclared.totals.operating.minor_units + 700
    assert moved.totals.investing.minor_units == undeclared.totals.investing.minor_units - 700
    for figure in ("net_income", "financing", "net_change_in_cash", "opening_cash",
                   "closing_cash", "difference"):
        assert getattr(moved.totals, figure) == getattr(undeclared.totals, figure), figure
    assert moved.totals.difference.minor_units == 0
    assert (moved.totals.opening_cash.minor_units + moved.totals.net_change_in_cash.minor_units
            == moved.totals.closing_cash.minor_units)
    # Closing cash is still the bank total the balance sheet reports for the date.
    _, sheet_rows = all_pages(
        lambda **kw: fs.balance_sheet(fs.BalanceSheetInput(date_to="2026-02-28", **kw), s), limit=5)
    assert sum(item.amount.minor_units for item in sheet_rows if item.account_type == "bank") \
        == moved.totals.closing_cash.minor_units
    # The rows stay grouped in section order, with the moved account among the
    # operating ones rather than left where its type would have put it.
    assert [item.section for item in moved.rows] == sorted(
        (item.section for item in moved.rows), key=cf.SECTIONS.index)


def test_totals_cover_every_account_while_rows_page_and_zero_changes_are_asked_for(cash_ledger):
    s = cash_ledger
    whole = flows(s, limit=200)
    for limit in (1, 2, 5):
        page = flows(s, limit=limit)
        assert page.totals == whole.totals
        assert page.count == len(page.rows) == min(limit, len(whole.rows))
        assert page.next_cursor is not None
    paged, rows = all_pages(lambda **kw: flows(s, **kw), limit=1)
    assert [row.account_id for row in rows] == [row.account_id for row in whole.rows]
    assert paged.totals == whole.totals

    # Every sectioned account has a change here, so a zero one has to be made.
    s.company.raw.execute("UPDATE accounts SET type='other_asset' WHERE id='z'")
    quiet = flows(s, limit=200)
    assert "z" not in {row.account_id for row in quiet.rows}
    assert quiet.totals == whole.totals
    shown = flows(s, include_zero=True, limit=200)
    assert shown.totals == whole.totals
    quiet_row = next(row for row in shown.rows if row.account_id == "z")
    assert quiet_row.amount.minor_units == 0 and quiet_row.section == "investing"


def test_a_report_with_no_period_activity_still_reconciles(cash_ledger):
    s = cash_ledger
    quiet = flows(s, date_from="2026-03-01", date_to="2026-03-31", limit=200)
    assert quiet.rows == [] and quiet.next_cursor is None
    assert quiet.totals.net_income.minor_units == 0
    assert quiet.totals.net_change_in_cash.minor_units == 0
    assert quiet.totals.opening_cash == quiet.totals.closing_cash
    assert quiet.totals.difference.minor_units == 0


def test_the_period_must_be_ordered_and_the_dates_real():
    with pytest.raises(ValueError):
        cf.CashFlowsInput(date_from="2026-03-01", date_to="2026-02-28")
    with pytest.raises(ValueError):
        cf.CashFlowsInput(date_from="2026-02-30", date_to="2026-03-01")
