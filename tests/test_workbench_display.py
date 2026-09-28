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


def test_a_page_reads_this_year_from_the_company_calendar():
    """In a template the year is the company's today when the page knows it, else the server's."""
    from jinja2 import Environment
    env = Environment()
    env.filters.update(D.FILTERS)
    shown = env.from_string("{{ '2026-12-31' | day }}")
    # New Year's Eve in the company's zone while the server has already reached 2027, and back.
    assert shown.render(company_today="2026-12-31") == "Dec 31"
    assert env.from_string("{{ '2025-12-31' | day }}").render(company_today="2026-01-01") == "Dec 31, 2025"
    # Without a company the server's today decides, as a direct call does.
    assert shown.render() == D.day("2026-12-31") and shown.render(company_today="") == D.day("2026-12-31")
    # A date the template passes still wins, as a report's own today does.
    assert env.from_string("{{ '2026-12-31' | day(then) }}").render(then=date(2027, 1, 1), company_today="2026-12-31") == "Dec 31, 2026"


def test_ago_reads_recent_instants_relative_to_now():
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    assert D.ago("2026-09-27T11:59:30Z", now) == "just now"
    assert D.ago("2026-09-27T11:48:00+00:00", now) == "12 min ago"
    assert D.ago("2026-09-20T11:48:00+00:00", now) == "Sep 20, 2026"


def test_negatives_read_in_parentheses_when_the_company_says_so():
    """R82: the company's negative-number style; minus stays the default."""
    assert D.money("-40.00", "USD", negatives="parentheses") == "($40.00)"
    assert D.money({"amount": "-12000", "currency": "JPY"}, home="USD", negatives="parentheses") == "(¥12,000 JPY)"
    assert D.money("-5.125", "BHD", negatives="parentheses") == "(5.125 BHD)"
    assert D.amount("-1234.50", negatives="parentheses") == "(1,234.50)"
    assert D.money("40.00", "USD", negatives="parentheses") == "$40.00"
    token = D.NEGATIVES.set("parentheses")
    try:
        assert D.money("-40.00", "USD") == "($40.00)" and D.amount("-40.00") == "(40.00)"
    finally:
        D.NEGATIVES.reset(token)
    assert D.money("-40.00", "USD") == "-$40.00" and D.amount("-40.00") == "-40.00"
