"""Exact schedule arithmetic for fixed-asset depreciation and loan amortization (R119).

Everything is integer minor units. One rounding rule throughout, the codebase's: an exact ratio
rounded once to the minor unit, half to even.

**Depreciation.** Periods start with the in-service month (a full month is taken for it, the
anchor's convention) and are a month or twelve months long; the last yearly period is shorter
when the life is not whole years. Each period takes its share of what is still to depreciate:

- straight line: ``(book value - salvage) x months in period / months of life remaining``;
- declining balance: ``book value x rate x months in period / (100 x life months)``, where rate is
  150 or 200 (percent of the straight-line rate), or the straight-line share when that is larger --
  the switch to straight line, which once made is kept, since the straight-line share then stays
  the larger;
- never below salvage, and the last period takes exactly what remains above salvage.

So the schedule always sums exactly to cost less salvage. A period already posted keeps the
amount it was posted at, and later periods are computed from the book value it left, so changing
salvage or life after posting spreads the difference over what remains.

**Loans.** The periodic rate is ``annual rate / payments per year``. The regular payment is the
level payment that brings the balance to the balloon (zero when none) after the last payment,
rounded once. Each payment's interest is ``balance x periodic rate``, rounded once; the rest of the
payment is principal; the last scheduled payment (or the one that would overshoot) pays exactly
the balance left, so it absorbs the rounding and carries any balloon.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from fractions import Fraction

from bookflow.core.exact import round_ratio_half_even

PER_YEAR = {'weekly': 52, 'biweekly': 26, 'monthly': 12, 'quarterly': 4, 'semiannual': 2, 'annual': 1}
_MONTHS = {'monthly': 1, 'quarterly': 3, 'semiannual': 6, 'annual': 12}
_DAYS = {'weekly': 7, 'biweekly': 14}
_RATE_SCALE = 100 * 1_000_000  # percent held in millionths


def add_months(day: date, months: int, *, anchor_day: int | None = None) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(anchor_day or day.day, last))


def month_end(day: date) -> date:
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


# ------------------------------------------------------------------------------------ depreciation

def periods(in_service: str, life_months: int, period: str) -> list[dict]:
    """The schedule's periods: number, start, end, months."""
    start = date.fromisoformat(in_service).replace(day=1)
    step = 1 if period == 'month' else 12
    out, used, number = [], 0, 1
    while used < life_months:
        months = min(step, life_months - used)
        first = add_months(start, used)
        last = month_end(add_months(start, used + months - 1))
        out.append(dict(number=number, start=first.isoformat(), end=last.isoformat(), months=months))
        used += months
        number += 1
    return out


def depreciation(cost: int, salvage: int, method: str, declining_percent: int | None, life_months: int,
                 rows: list[dict], posted: dict[int, int]) -> list[dict]:
    """Amount per period, each row gaining ``amount``, ``basis`` (the method that gave the amount:
    'straight_line' or 'declining_balance'), ``posted``, ``accumulated`` and ``book_value``.
    ``posted`` maps period number to the amount it stands posted at."""
    book, remaining, switched, out = cost, life_months, method == 'straight_line', []
    for index, row in enumerate(rows):
        months = row['months']
        room = max(book - salvage, 0)
        line = room if index == len(rows) - 1 else round_ratio_half_even(room * months, remaining)
        if not switched:
            declining = round_ratio_half_even(book * declining_percent * months, 100 * life_months)
            switched = line >= declining or index == len(rows) - 1
        basis = 'straight_line' if switched else 'declining_balance'
        amount = line if switched else min(declining, room)
        if row['number'] in posted:
            amount = posted[row['number']]
        book -= amount
        remaining -= months
        out.append(dict(row, amount=amount, basis=basis, posted=row['number'] in posted,
                        accumulated=cost - book, book_value=book))
    return out


# ------------------------------------------------------------------------------------------- loans

def due_date(first: str, frequency: str, number: int) -> str:
    day = date.fromisoformat(first)
    if frequency in _DAYS:
        return (day + timedelta(days=_DAYS[frequency] * (number - 1))).isoformat()
    return add_months(day, _MONTHS[frequency] * (number - 1), anchor_day=day.day).isoformat()


def periodic_rate(rate_millionths: int, frequency: str) -> Fraction:
    return Fraction(rate_millionths, _RATE_SCALE * PER_YEAR[frequency])


def interest(balance: int, rate_millionths: int, frequency: str) -> int:
    return round_ratio_half_even(balance * rate_millionths, _RATE_SCALE * PER_YEAR[frequency])


def level_payment(principal: int, rate_millionths: int, frequency: str, count: int, balloon: int = 0) -> int:
    """The level payment bringing ``principal`` to ``balloon`` after ``count`` payments, rounded once."""
    r = periodic_rate(rate_millionths, frequency)
    if r == 0:
        exact = Fraction(principal - balloon, count)
    else:
        growth = (1 + r) ** count
        exact = (principal * growth - balloon) * r / (growth - 1)
    return round_ratio_half_even(exact.numerator, exact.denominator)


def remaining_rows(balance: int, *, payment: int, rate_millionths: int, frequency: str, first_payment_date: str,
                   term: int, start_number: int) -> list[dict]:
    """Scheduled payments from ``start_number`` on, from ``balance``, until it is paid."""
    out, number = [], start_number
    while balance > 0:
        owed = interest(balance, rate_millionths, frequency)
        principal = payment - owed
        if number >= term or principal >= balance:
            principal = balance
        elif principal <= 0:
            raise ValueError('the payment does not cover the interest')
        balance -= principal
        out.append(dict(number=number, date=due_date(first_payment_date, frequency, number), principal=principal,
                        interest=owed, balance=balance))
        number += 1
        if number > term + 12 * 400:  # a guard, never reached by a payment that covers interest
            raise ValueError('schedule does not end')
    return out
