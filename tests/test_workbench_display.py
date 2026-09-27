"""The browser's money and date wording: exact digits, grouped, never through a float."""
from datetime import date, datetime, timezone

from bookflow.adapters.workbench import display as D


def test_money_groups_exact_digits_and_names_foreign_currency():
    assert D.money({"amount": "1855.95", "currency": "USD"}) == "$1,855.95"
    assert D.money("-1234567.05", "USD") == "-$1,234,567.05"
    assert D.money("0.00 USD") == "$0.00"
    assert D.money({"amount": "12000", "currency": "JPY"}, home="USD") == "¥12,000 JPY"
    assert D.money("5.125", "BHD") == "5.125 BHD"
    # Precision the float path would lose survives untouched.
    assert D.money("90071992547409.93", "USD") == "$90,071,992,547,409.93"
    assert D.money("n/a") == "n/a" and D.money(None) == ""


def test_amount_is_the_figure_alone_for_a_column_headed_by_its_currency():
    assert D.amount({"amount": "-40.00", "currency": "USD"}) == "-40.00"
    assert D.amount("1000") == "1,000"


def test_dates_carry_the_year_only_when_it_is_not_this_year():
    today = date(2026, 9, 27)
    assert D.day("2026-11-12", today) == "Nov 12"
    assert D.day("2025-11-12", today) == "Nov 12, 2025"
    assert D.longday("2026-01-05") == "Jan 5, 2026"
    assert D.day("not a date", today) == "not a date"


def test_ago_reads_recent_instants_relative_to_now():
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    assert D.ago("2026-09-27T11:59:30Z", now) == "just now"
    assert D.ago("2026-09-27T11:48:00+00:00", now) == "12 min ago"
    assert D.ago("2026-09-20T11:48:00+00:00", now) == "Sep 20, 2026"
