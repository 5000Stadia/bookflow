"""Income and expense activity grouped by the tax line recorded on each account.

`accounts.tax_line` had no reader before this report, so these cases are the
whole contract: which accounts are grouped, what an unassigned account does,
that a group total is the sum of the accounts printed under it whatever the
page boundary is, and that the report's own net income is the figure the
profit and loss reports for the same two dates.
"""
import pytest

from bookflow.company import financial_statements as fs
from bookflow.company import income_tax_reports as tax
from bookflow.company import schema
from tests.test_row8_reports import ledger, insert, all_pages  # noqa: F401


# id, account type, tax line
ADDED = (("service", "income", "Gross receipts"),
         ("supplies", "expense", None),
         ("cogs", "cost_of_goods_sold", "Cost of goods sold"),
         ("interest", "other_income", None),
         ("penalty", "other_expense", "Other deductions"),
         ("unused", "expense", "Other deductions"))

# day, minor units, debit account, credit account
POSTINGS = (("2026-01-15", 9999, "a", "b"),
            ("2026-02-02", 5000, "a", "b"),
            ("2026-02-03", 3000, "a", "service"),
            ("2026-02-04", 1200, "c", "a"),
            ("2026-02-05", 400, "supplies", "a"),
            ("2026-02-06", 2500, "cogs", "a"),
            ("2026-02-07", 150, "a", "interest"),
            ("2026-02-08", 90, "penalty", "a"))


@pytest.fixture
def tax_ledger(ledger):
    s, batch, _ = ledger
    s.company.raw.execute("UPDATE accounts SET type='income', tax_line='Gross receipts' WHERE id='b'")
    s.company.raw.execute("UPDATE accounts SET type='expense', tax_line='Other deductions' WHERE id='c'")
    s.company.raw.execute("UPDATE accounts SET type='non_posting' WHERE id='z'")
    for account, kind, line in ADDED:
        insert(s.company, schema.accounts, id=account, name=account, name_key=account,
               full_name=account, full_name_key=account, path=account, depth=1,
               type=kind, currency="USD", active=True, tax_line=line)
    for day, units, debit, credit in POSTINGS:
        batch(day, units, debit=debit, credit=credit)
    return s


def summary(s, **kwargs):
    return tax.income_tax_summary(
        tax.IncomeTaxSummaryInput(**{"date_from": "2026-02-01", "date_to": "2026-02-28", **kwargs}), s)


def shape(rows):
    return [(row.kind, row.display_tax_line, row.account_id, row.amount.minor_units) for row in rows]


EXPECTED = [("tax_line", "Cost of goods sold", None, 2500),
            ("account", "Cost of goods sold", "cogs", 2500),
            ("tax_line", "Gross receipts", None, 8000),
            ("account", "Gross receipts", "b", 5000),
            ("account", "Gross receipts", "service", 3000),
            ("tax_line", "Other deductions", None, 1290),
            ("account", "Other deductions", "c", 1200),
            ("account", "Other deductions", "penalty", 90),
            ("tax_line", "Unassigned", None, 550),
            ("account", "Unassigned", "interest", 150),
            ("account", "Unassigned", "supplies", 400)]


def test_every_line_carries_its_own_accounts_its_own_total_and_the_period_it_was_asked_for(tax_ledger):
    s = tax_ledger
    report = summary(s, limit=200)
    assert shape(report.rows) == EXPECTED
    # A group total is exactly the accounts printed under it.
    groups = {}
    for row in report.rows:
        if row.kind == "account":
            groups.setdefault(row.display_tax_line, []).append(row.amount.minor_units)
    for row in report.rows:
        if row.kind == "tax_line":
            assert row.amount.minor_units == sum(groups[row.display_tax_line])
            assert row.account_count == len(groups[row.display_tax_line])
            assert row.account_id is None and row.display_account_label is None
    # An unassigned account says so in the typed output, not only in the label.
    unassigned = {row.account_id for row in report.rows if row.tax_line is None and row.kind == "account"}
    assert unassigned == {"interest", "supplies"}
    # The January posting is outside the period and no balance-sheet or
    # non-posting account is on an income tax summary.
    assert {row.account_id for row in report.rows} & {"a", "z"} == set()
    assert dict(s.company.raw.execute("SELECT id, type FROM accounts").fetchall())["a"] == "bank"


def test_net_income_here_is_the_figure_the_profit_and_loss_reports(tax_ledger):
    s = tax_ledger
    report = summary(s, limit=200)
    assert report.totals.income.minor_units == 8150
    assert report.totals.expense.minor_units == 4190
    assert report.totals.net_income.minor_units == 3960
    profit = fs.profit_and_loss(fs.ProfitAndLossInput(date_from="2026-02-01", date_to="2026-02-28"), s)
    assert report.totals.net_income == profit.totals.net_income
    # Whole-year: the January sale joins gross receipts and nothing else moves.
    year = summary(s, date_from="2026-01-01", date_to="2026-12-31", limit=200)
    assert next(row for row in year.rows if row.kind == "tax_line"
                and row.display_tax_line == "Gross receipts").amount.minor_units == 8000 + 9999
    whole = fs.profit_and_loss(fs.ProfitAndLossInput(date_from="2026-01-01", date_to="2026-12-31"), s)
    assert year.totals.net_income == whole.totals.net_income


def test_totals_and_group_totals_cover_the_whole_filter_while_rows_page(tax_ledger):
    s = tax_ledger
    whole = summary(s, limit=200)
    for limit in (1, 2, 4):
        page = summary(s, limit=limit)
        assert page.totals == whole.totals
        assert page.count == len(page.rows) == limit
        assert page.next_cursor is not None
        # A group row on a page whose accounts have not arrived still totals the group.
        for row in page.rows:
            if row.kind == "tax_line":
                assert row.amount == next(other.amount for other in whole.rows
                                          if other.kind == "tax_line"
                                          and other.display_tax_line == row.display_tax_line)
    paged, rows = all_pages(lambda **kw: summary(s, **kw), limit=1)
    assert shape(rows) == EXPECTED
    assert paged.totals == whole.totals


def test_an_account_with_no_activity_is_asked_for_rather_than_assumed(tax_ledger):
    s = tax_ledger
    quiet = summary(s, limit=200)
    assert "unused" not in {row.account_id for row in quiet.rows}
    assert next(row for row in quiet.rows if row.kind == "tax_line"
                and row.display_tax_line == "Other deductions").account_count == 2
    shown = summary(s, include_zero=True, limit=200)
    assert shown.totals == quiet.totals
    assert next(row for row in shown.rows if row.account_id == "unused").amount.minor_units == 0
    assert next(row for row in shown.rows if row.kind == "tax_line"
                and row.display_tax_line == "Other deductions").account_count == 3


def test_a_tax_line_spelled_unassigned_is_still_its_own_line(tax_ledger):
    """The sentinel label is presentation; the typed value keeps the two apart."""
    s = tax_ledger
    s.company.raw.execute("UPDATE accounts SET tax_line='Unassigned' WHERE id='supplies'")
    report = summary(s, limit=200)
    lines = [(row.tax_line, row.display_tax_line, row.amount.minor_units)
             for row in report.rows if row.kind == "tax_line"]
    assert ("Unassigned", "Unassigned", 400) in lines
    assert (None, "Unassigned", 150) in lines
    assert report.totals == summary(s, limit=200).totals


def test_the_period_must_be_ordered_and_the_dates_real():
    with pytest.raises(ValueError):
        tax.IncomeTaxSummaryInput(date_from="2026-03-01", date_to="2026-02-28")
    with pytest.raises(ValueError):
        tax.IncomeTaxSummaryInput(date_from="2026-02-30", date_to="2026-03-01")
