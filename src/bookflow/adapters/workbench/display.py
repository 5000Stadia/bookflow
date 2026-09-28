"""How the browser shows money and dates to a person.

Display only. Every value arrives as the exact decimal string or ISO date the command returned
and leaves as text; no amount passes through a float, and nothing here is used for input,
exports, printing data or any interface other than the rendered page.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime, timezone
from typing import Any

from jinja2 import pass_context

# The home-currency symbols a bookkeeper expects to see. A currency not listed keeps its code.
SYMBOLS = {"USD": "$", "CAD": "$", "AUD": "$", "NZD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
# How the company shows a negative amount: "minus" (-$40.00) or "parentheses" ($40.00). Set
# for the request from the company's `negative_number_style` when its company page loads; a
# page with no company keeps the minus sign.
NEGATIVES: ContextVar[str] = ContextVar("bookflow_negative_number_style", default="minus")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _split(value: Any) -> tuple[str | None, str | None]:
    """(amount, currency) from a money mapping, a "12.34 USD" string or a bare amount."""
    if isinstance(value, dict):
        return (None if value.get("amount") is None else str(value["amount"])), value.get("currency")
    if value is None or isinstance(value, bool):
        return None, None
    text = str(value).strip()
    parts = text.split()
    if len(parts) == 2 and len(parts[1]) == 3 and parts[1].isalpha() and parts[1].isupper():
        return parts[0], parts[1]
    return text, None


def _grouped(amount: str) -> str | None:
    """("-", "1,234,567.50") from "-1234567.50"; None when the text is not a plain decimal."""
    sign = "-" if amount.startswith("-") else ""
    body = amount.lstrip("+-")
    whole, dot, frac = body.partition(".")
    if not whole.isdigit() or (dot and not frac.isdigit()):
        return None
    groups = []
    while len(whole) > 3:
        whole, tail = whole[:-3], whole[-3:]
        groups.insert(0, tail)
    groups.insert(0, whole)
    return sign, ",".join(groups) + (dot + frac if dot else "")


def _signed(sign: str, body: str, negatives: str | None) -> str:
    """A negative body with the company's sign: "-$40.00" or "($40.00)"."""
    if not sign:
        return body
    return f"({body})" if (negatives or NEGATIVES.get()) == "parentheses" else sign + body


def amount(value: Any, negatives: str | None = None) -> str:
    """A figure for a column whose header already names the currency: "1,855.95", "-40.00".

    A negative figure reads "(40.00)" instead when the company shows negatives in parentheses.
    """
    text, _ = _split(value)
    if text is None or text == "":
        return ""
    grouped = _grouped(text)
    if grouped is None:
        return text
    sign, digits = grouped
    return _signed(sign, digits, negatives)


def money(value: Any, currency: str | None = None, home: str | None = None,
          negatives: str | None = None) -> str:
    """An amount a person reads: "$1,855.95", "-$40.00", "¥12,000 JPY" when foreign.

    `currency` names the currency when `value` is a bare amount. `home` is the company's home
    currency; an amount in any other currency also shows its code so two currencies never read
    alike. A negative amount reads "($40.00)" when the company shows negatives in parentheses
    (`negatives`, else the request's company setting). Text that is not a plain decimal is
    returned unchanged.
    """
    text, code = _split(value)
    code = code or currency
    if text is None or text == "":
        return ""
    grouped = _grouped(text)
    if grouped is None:
        return text
    sign, digits = grouped
    symbol = SYMBOLS.get(code or "")
    if symbol is None:
        return _signed(sign, f"{digits} {code}" if code else digits, negatives)
    shown = f"{symbol}{digits}"
    if home and code != home:
        shown += f" {code}"
    return _signed(sign, shown, negatives)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def day(value: Any, today: date | None = None) -> str:
    """A date in a list: "Nov 12" this year, "Nov 12, 2025" in any other year."""
    when = _as_date(value)
    if when is None:
        return "" if value is None else str(value)
    current = (today or date.today()).year
    shown = f"{MONTHS[when.month - 1]} {when.day}"
    return shown if when.year == current else f"{shown}, {when.year}"


@pass_context
def _day_on_page(context, value: Any, today: Any = None) -> str:
    """`day` in a template: "this year" is the company's calendar year when the page knows the company.

    A template that already holds the date to read against passes it, as a report does.
    """
    return day(value, _as_date(today) or _as_date(context.get("company_today") or None))


def longday(value: Any) -> str:
    """A date on a detail page: "Nov 12, 2026"."""
    when = _as_date(value)
    if when is None:
        return "" if value is None else str(value)
    return f"{MONTHS[when.month - 1]} {when.day}, {when.year}"


def ago(value: Any, now: datetime | None = None) -> str:
    """A recorded instant relative to now: "just now", "12 min ago", "3 h ago", else the date."""
    try:
        then = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "" if value is None else str(value)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    seconds = ((now or datetime.now(timezone.utc)) - then).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return longday(then.date())


FILTERS = {"money": money, "amount": amount, "day": _day_on_page, "longday": longday, "ago": ago}
