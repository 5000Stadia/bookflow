"""Weighted-average replay, and the dated corrections a backdated change owes.

Nothing here touches a database. It takes one item's movement rows in their stable order and
answers two questions: what the books *should* say, and what has to be posted to make them say
it. Keeping that arithmetic in a function with no session is what lets the hard cases be
tested directly instead of through four commands.

## What replay is

Walk the item's *input* movements -- receipts, issues and value adjustments that no reversal
has retired -- in ``(effective_date, sequence, id)`` order, carrying on-hand quantity ``Q`` and
asset value ``V``. A bought receipt adds both, including active receipt-targeted purchase-price
corrections at that original acquisition position. A value adjustment moves value alone. An issue takes
quantity out and consumes value at the running weighted average:

    consumed = V                                   when the issue empties the stock
    consumed = round_half_even(V * |q| / Q)        otherwise

A **return** -- a receipt naming an issue in ``returns_movement_id`` -- is that issue read
backwards, so its value is an output of this walk and not a number anybody states. The returns
of one issue take contiguous spans of its issued quantity in the order they are walked and
divide its consumed value by ``core.exact.endpoint_share``, the same rule the credit memo's own
money goes through:

    target(R) = endpoint_share(consumed(M), |q(M)|, low, low + q(R))

They therefore telescope to exactly ``consumed(M)``, whatever order they were taken in and
however many pieces the line came back in. That is the point of computing them here rather than
beside the ledger: when a backdated purchase moves ``consumed(M)``, every return against ``M``
moves with it and is owed a correction like the issue itself, so nothing is left standing at a
cost that no longer exists. A return whose issue is retired, or dated ahead of it, has no cost
to take a share of; its stated value stands and no correction is computed for it.

The first line is the whole of the zero-residual rule. Computing the last issue from a rounded
average would leave a few minor units of value sitting behind no quantity for ever; taking the
remainder outright cannot.

Neither line ever divides before multiplying and neither ever sees a float: both operands are
exact integers and ``round_half_even`` resolves the single division once.

## Why a correction is not an input

``recost`` rows are what replay *produced* last time -- against an issue, and now against a
return too -- and ``reversal`` rows retire what a void took back. Replaying either as if it were a new purchase or a new sale would fold a correction
into the average that the correction was computed from -- the mistake the costing decision
names explicitly. So the walk reads ``INPUT_KINDS`` only, and the two derived kinds are read
to work out what is already posted. A receipt-targeted recost is instead an explicit
purchase-price input attached to the original receipt; it is never walked as a later
value adjustment. Issue-targeted recosts, and the recosts against the returns that mirror
them, remain replay outputs.

## What "already posted" means, and why nothing is corrected twice

For one issue ``M``, and for one return ``R`` alike -- they are the two kinds of movement
whose value this walk decides rather than reads:

    posted_effective(X) = X.value + sum(active recosts of X) + sum(reversals of X)
    target(X)           = 0 when X is retired, else replay's computed value
    delta               = target - posted_effective

A delta of zero writes nothing, which is what makes running the recalculation again a no-op
and a retry harmless. A retired issue's target is zero, so the same subtraction also backs out
the corrections that were posted against it while it stood -- there is no second rule for
voids, because the first one already covers them.

Summing the same identity over every row shows why the stock reports and the balance sheet
cannot drift apart: total posted value is the bought receipts and value adjustments plus each
issue's and each return's ``posted_effective``, which after correction is its target, which is
exactly the ``V`` this replay computed. Because the returns of an issue telescope to what that
issue consumed, a line that was wholly sold and wholly returned leaves nothing behind in the
asset account -- which is the property that a stated return cost silently broke.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from bookflow.company.inventory_schema import INPUT_KINDS
from bookflow.core.exact import QUANTITY_SCALE, endpoint_share, round_ratio_half_even

MICRO = 10 ** QUANTITY_SCALE


class StockRefusal(Exception):
    """A replay that cannot stand: negative stock, negative value, or unvalued stock.

    Carried as an exception rather than returned because every caller refuses the whole
    change on it; the command layer turns it into the error code a person reads.
    """

    def __init__(self, reason: str, movement: Mapping | None, **details):
        self.reason = reason
        self.movement = movement
        self.details = details
        super().__init__(reason)


@dataclass(frozen=True)
class Correction:
    """One dated value delta a caller owes against one issue, or one return of an issue."""

    target_movement: Mapping
    delta_minor_units: int
    effective_date: str


@dataclass(frozen=True)
class Replay:
    quantity_microunits: int
    value_minor_units: int
    targets: dict[str, int]
    corrections: tuple[Correction, ...] = ()

    @property
    def average_cost_minor_units(self) -> int:
        """Value per one whole unit, rounded once for display only."""
        if self.quantity_microunits <= 0:
            return 0
        return round_ratio_half_even(self.value_minor_units * MICRO, self.quantity_microunits)


def ordered(rows: Iterable[Mapping]) -> list[Mapping]:
    """One item's movements in the order replay and every report must read them."""
    return sorted(rows, key=lambda row: (row['effective_date'], int(row['sequence']), row['id']))


def consumed_value(value_minor_units: int, quantity_microunits: int, issued_microunits: int) -> int:
    """What issuing ``issued_microunits`` takes out of ``value_minor_units``.

    ``issued_microunits`` is a positive magnitude and must not exceed what is on hand; the
    caller checks that, because the refusal it raises names the item and the date.
    """
    if issued_microunits == quantity_microunits:
        return value_minor_units
    return round_ratio_half_even(value_minor_units * issued_microunits, quantity_microunits)


def _retired(rows: Sequence[Mapping]) -> set[str]:
    return {row['reverses_movement_id'] for row in rows if row['kind'] == 'reversal'}


def replay(rows: Iterable[Mapping]) -> Replay:
    """Canonical on-hand quantity and value, plus the corrections the posted rows still owe."""
    rows = ordered(rows)
    retired = _retired(rows)
    posted: dict[str, int] = {}
    for row in rows:
        if row['kind'] == 'recost' and row['id'] not in retired:
            posted[row['corrects_movement_id']] = posted.get(row['corrects_movement_id'], 0) + int(row['value_minor_units'])
        elif row['kind'] == 'reversal':
            posted[row['reverses_movement_id']] = posted.get(row['reverses_movement_id'], 0) + int(row['value_minor_units'])

    quantity = value = 0
    targets: dict[str, int] = {}
    issued_rows = {row['id']: row for row in rows if row['kind'] == 'issue'}
    given_back: dict[str, int] = {}     # issue id -> quantity its returns have taken back
    stale: set[str] = set()             # returns this walk cannot speak for
    for row in rows:
        if row['kind'] not in INPUT_KINDS or row['id'] in retired:
            continue
        change = int(row['quantity_microunits'])
        if row['kind'] == 'receipt':
            quantity += change
            issue_id = row.get('returns_movement_id')
            if issue_id is None:
                # Receipt-targeted purchase-price corrections belong at acquisition,
                # even when a bill is recorded after intervening issues.
                value += int(row['value_minor_units']) + posted.get(row['id'], 0)
            elif issue_id not in targets:
                # The issue it mirrors is retired, or is dated after it and so has no cost
                # yet at this point in the walk. Nothing here can say what that return is
                # worth, so its stated value stands and no correction is computed for it.
                stale.add(row['id'])
                value += int(row['value_minor_units']) + posted.get(row['id'], 0)
            else:
                # A return is the inverse of the issue it names, so it gives back a share of
                # what that issue is worth *now*. That is what carries a backdated purchase
                # through to the returns it displaced instead of stranding them at a stale
                # cost. The share is the next contiguous span of the issued quantity, divided
                # by the same endpoint rule the credit's own money goes through, so the
                # returns of one issue telescope to exactly what that issue consumed.
                whole = -int(issued_rows[issue_id]['quantity_microunits'])
                low = given_back.get(issue_id, 0)
                high = low + change
                if high > whole:
                    raise StockRefusal('over_returned', row, issued_microunits=whole,
                                       returned_microunits=high,
                                       problem='more would come back than that sale took out')
                given_back[issue_id] = high
                taken_back = endpoint_share(-targets[issue_id], whole, low, high)
                targets[row['id']] = taken_back
                value += taken_back
        elif row['kind'] == 'value':
            if quantity <= 0:
                raise StockRefusal('unvalued_stock', row, quantity_microunits=quantity,
                                   problem='an item with nothing on hand cannot carry inventory value')
            proposed = value + int(row['value_minor_units'])
            if proposed <= 0:
                raise StockRefusal('unvalued_stock', row, quantity_microunits=quantity,
                                   value_minor_units=proposed,
                                   problem='stock on hand must be worth more than nothing; '
                                           'issue the quantity to write it off in full')
            value = proposed
        else:
            issue = -change
            if issue > quantity:
                raise StockRefusal('negative_stock', row, quantity_microunits=quantity,
                                   requested_microunits=issue,
                                   problem='more would go out than is on hand on that date')
            taken = consumed_value(value, quantity, issue)
            targets[row['id']] = -taken
            quantity -= issue
            value -= taken
        if quantity < 0 or value < 0:
            raise StockRefusal('negative_stock', row, quantity_microunits=quantity,
                               value_minor_units=value, problem='stock or its value would go below zero')
        if quantity == 0 and value != 0:
            raise StockRefusal('residual_value', row, value_minor_units=value,
                               problem='no quantity on hand may not leave value behind')

    corrections = []
    for row in rows:
        owns_cost = row['kind'] == 'issue' or (
            row['kind'] == 'receipt' and row.get('returns_movement_id') is not None
            and row['id'] not in stale)
        if not owns_cost:
            continue
        target = targets.get(row['id'], 0)
        delta = target - (int(row['value_minor_units']) + posted.get(row['id'], 0))
        if delta:
            corrections.append(Correction(row, delta, row['effective_date']))
    corrections.sort(key=lambda correction: (correction.effective_date, int(correction.target_movement['sequence'])))
    return Replay(quantity, value, targets, tuple(corrections))


def totals(rows: Iterable[Mapping]) -> tuple[int, int]:
    """Posted on-hand quantity and value: the running sums of every row, corrections included.

    This is what a report reads. It agrees with ``replay`` only once the corrections replay
    asked for have been written, which is the whole invariant this increment exists to hold.
    """
    quantity = value = 0
    for row in rows:
        quantity += int(row['quantity_microunits'])
        value += int(row['value_minor_units'])
    return quantity, value
